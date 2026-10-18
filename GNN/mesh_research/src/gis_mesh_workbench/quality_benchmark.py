"""Repeatable QA timing and sampled process-tree RSS measurements.

Benchmarking never launches a generator implicitly. Supply a callback that
returns one mesh per run when generation timing is wanted; otherwise the exact
provided mesh snapshot is evaluated for every repetition.
"""
from __future__ import annotations

from datetime import datetime, timezone
import os
import platform
import json
import statistics
import threading
import time
import uuid

from .job_control import Cancelled, checkpoint
from .quality_assessment import evaluate_mesh_quality, quality_mesh_hash

SCHEMA_VERSION = "meshcherry-quality-benchmark-v1"
DEFAULT_REPETITIONS = 5
MAX_REPETITIONS = 50
MAX_WARMUP = 5
DEFAULT_MEMORY_INTERVAL_SECONDS = 0.02


def _memory_sampler(interval_seconds):
    """Create a best-effort concurrent process-tree RSS sampler, if available."""
    try:
        import psutil
        root = psutil.Process(os.getpid())
        lock = threading.Lock()
        stop = threading.Event()
        state = {"peak": None, "samples": 0}

        def sample():
            try:
                processes = [root]
                try:
                    processes.extend(root.children(recursive=True))
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
                # Sum RSS for processes visible at this same sampling instant.
                total = 0
                for process in processes:
                    try:
                        total += process.memory_info().rss
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        continue
                with lock:
                    state["peak"] = total if state["peak"] is None else max(state["peak"], total)
                    state["samples"] += 1
            except Exception:
                return

        def run():
            while not stop.is_set():
                sample()
                stop.wait(interval_seconds)

        thread = threading.Thread(target=run, name="mesh-quality-rss-sampler", daemon=True)
        return {"status": "AVAILABLE", "method": "sampled_process_tree_rss",
                "scope": "parent and currently visible descendants, summed per sample",
                "interval_seconds": float(interval_seconds), "thread": thread,
                "stop": stop, "sample": sample, "lock": lock, "state": state}
    except Exception as exc:
        return {"status": "NOT_AVAILABLE", "method": "unsupported",
                "reason": f"psutil unavailable: {type(exc).__name__}"}


def _stop_memory_sampler(sampler):
    if sampler.get("status") != "AVAILABLE":
        return {"value": None, "status": "NOT_AVAILABLE", "method": sampler["method"],
                "reason": sampler.get("reason", "process-tree RSS sampling is unavailable")}
    sampler["stop"].set()
    sampler["thread"].join(timeout=max(0.1, sampler["interval_seconds"] * 4))
    sampler["sample"]()
    with sampler["lock"]:
        value = sampler["state"]["peak"]
        samples = sampler["state"]["samples"]
    if value is None or samples == 0:
        return {"value": None, "status": "NOT_AVAILABLE", "method": sampler["method"],
                "reason": "No process-tree RSS samples were collected."}
    return {"value": int(value), "status": "MEASURED_SAMPLED",
            "method": sampler["method"], "scope": sampler["scope"],
            "sampling_interval_seconds": sampler["interval_seconds"],
            "sample_count": samples,
            "limitations": "Sampling can miss short-lived peaks; process membership is observed, not atomic."}


def _metric(statistics_tree, name, field):
    record = statistics_tree.get(name, {}) if isinstance(statistics_tree, dict) else {}
    value = record.get(field) if isinstance(record, dict) else None
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _aggregate(runs):
    fields = ("generation_time_seconds", "qa_time_seconds", "pipeline_time_seconds",
              "min_angle_deg", "q_mean", "q_p5", "ar_p95", "boundary_error", "size_p95")
    output = {}
    for field in fields:
        values = [float(row[field]) for row in runs if row.get("status") == "SUCCESS"
                  and isinstance(row.get(field), (int, float))]
        output[field] = ({"count": len(values), "median": statistics.median(values),
                          "min": min(values), "max": max(values),
                          "stdev": statistics.stdev(values) if len(values) > 1 else None}
                         if values else {"count": 0, "median": None, "min": None,
                                         "max": None, "stdev": None})
    rss = [row["peak_rss_bytes"] for row in runs if row.get("status") == "SUCCESS"
           and isinstance(row.get("peak_rss_bytes"), int)]
    output["peak_rss_bytes"] = ({"count": len(rss), "median": statistics.median(rss),
                                 "min": min(rss), "max": max(rss)} if rss else
                                {"count": 0, "median": None, "min": None, "max": None})
    return output


def _environment(generator):
    try:
        from .build_info import identity
        app_version = identity().get("app_version")
    except Exception:
        app_version = None
    return {"os": platform.platform(), "cpu": platform.processor() or None,
            "logical_cpu_count": os.cpu_count(), "python_version": platform.python_version(),
            "application_version": app_version,
            "generator": getattr(generator, "__qualname__", None) if generator else "existing_mesh",
            "generator_version": getattr(generator, "__version__", None) if generator else None}


def run_quality_benchmark(mesh, repetitions=DEFAULT_REPETITIONS, warmup=0,
                          quality_config=None, generator=None, *,
                          memory_interval_seconds=DEFAULT_MEMORY_INTERVAL_SECONDS):
    """Evaluate a fixed or callback-generated mesh repeatedly and report evidence.

    ``generator`` must be a callable accepting the zero-based run index
    (including warmups) and returning a mesh mapping. It is never inferred from
    project config and no external process is started by this utility.
    """
    if type(repetitions) is not int or not 1 <= repetitions <= MAX_REPETITIONS:
        raise ValueError(f"repetitions must be an integer in [1, {MAX_REPETITIONS}]")
    if type(warmup) is not int or not 0 <= warmup <= MAX_WARMUP:
        raise ValueError(f"warmup must be an integer in [0, {MAX_WARMUP}]")
    if not isinstance(memory_interval_seconds, (int, float)) or not 0.001 <= memory_interval_seconds <= 1:
        raise ValueError("memory_interval_seconds must be in [0.001, 1]")
    if generator is not None and not callable(generator):
        raise ValueError("generator must be callable or null")
    try:
        quality_config = dict(quality_config or {})
        json.dumps(quality_config, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("quality_config must be a finite JSON object") from exc
    try:
        fixed_mesh_hash = quality_mesh_hash(mesh) if generator is None else None
    except Exception:
        fixed_mesh_hash = None
    runs = []
    warmup_failures = 0
    run_index = 0

    # Warmup uses the same requested setup, but does not enter measured results.
    for _ in range(warmup):
        try:
            checkpoint()
        except Cancelled:
            return {"schema_version": SCHEMA_VERSION, "run_id": uuid.uuid4().hex,
                    "status": "CANCELLED", "requested_repetitions": repetitions,
                    "warmup_runs": warmup, "runs": [],
                    "successful_runs": 0, "failed_runs": 0,
                    "metadata": _environment(generator)}
        try:
            warm_mesh = generator(run_index) if generator else mesh
            run_index += 1
            before_hash = quality_mesh_hash(warm_mesh)
            evaluate_mesh_quality(warm_mesh, config=quality_config)
            if quality_mesh_hash(warm_mesh) != before_hash:
                raise ValueError("Quality evaluator mutated the warmup mesh input")
        except Cancelled:
            return {"schema_version": SCHEMA_VERSION, "run_id": uuid.uuid4().hex,
                    "status": "CANCELLED", "requested_repetitions": repetitions,
                    "warmup_runs": warmup, "runs": runs,
                    "successful_runs": 0, "failed_runs": 0,
                    "metadata": _environment(generator)}
        except Exception:
            warmup_failures += 1

    cancelled = False
    for run_number in range(repetitions):
        try:
            checkpoint()
        except Cancelled:
            cancelled = True
            break
        sampler = _memory_sampler(float(memory_interval_seconds))
        if sampler.get("status") == "AVAILABLE":
            sampler["thread"].start()
            sampler["sample"]()
        row = {"run_number": run_number + 1, "status": "FAILED",
               "generation_time_seconds": None, "qa_time_seconds": None,
               "pipeline_time_seconds": None, "peak_rss_bytes": None,
               "validity_status": None}
        start_pipeline = time.perf_counter()
        qa_started = None
        try:
            generation_time = None
            if generator is not None:
                generation_started = time.perf_counter()
                current_mesh = generator(run_index)
                generation_time = time.perf_counter() - generation_started
                run_index += 1
            else:
                current_mesh = mesh
            row["generation_time_seconds"] = generation_time
            before_hash = quality_mesh_hash(current_mesh)
            if fixed_mesh_hash is not None and before_hash != fixed_mesh_hash:
                raise ValueError("Fixed mesh input changed between benchmark runs")
            qa_started = time.perf_counter()
            assessment = evaluate_mesh_quality(current_mesh, config=quality_config)
            qa_time = time.perf_counter() - qa_started
            if quality_mesh_hash(current_mesh) != before_hash:
                raise ValueError("Quality evaluator mutated its mesh input")
            if assessment.get("status") == "CANCELLED":
                cancelled = True
                row["status"] = "CANCELLED"
            elif not isinstance(assessment.get("mesh_hash"), str):
                raise ValueError("Quality assessment did not provide a mesh hash")
            else:
                row["status"] = "SUCCESS"
            summary = assessment.get("statistics", {})
            performance = assessment.get("performance", {})
            boundary = assessment.get("boundary", {})
            size = assessment.get("size", {})
            row.update(mesh_hash=assessment.get("mesh_hash"), config_hash=assessment.get("config_hash"),
                       element_count=(assessment.get("summary") or {}).get("element_count"),
                       node_count=(assessment.get("summary") or {}).get("node_count"),
                       min_angle_deg=_metric(summary, "min_angle_deg", "min"),
                       q_mean=_metric(summary, "quality_q", "mean"),
                       q_p5=_metric(summary, "quality_q", "p5"),
                       ar_p95=_metric(summary, "aspect_ratio", "p95"),
                       boundary_error=boundary.get("relative_area_error"),
                       size_p95=_metric(size.get("statistics", {}), "size_ratio", "p95"),
                       validity_status=assessment.get("status"),
                       qa_time_seconds=qa_time,
                       generation_time_seconds=generation_time)
            row["pipeline_time_seconds"] = time.perf_counter() - start_pipeline
        except Cancelled:
            cancelled = True
            row["status"] = "CANCELLED"
            row["pipeline_time_seconds"] = time.perf_counter() - start_pipeline
        except Exception as exc:
            row.update(error=f"{type(exc).__name__}: {exc}",
                       pipeline_time_seconds=time.perf_counter() - start_pipeline)
            if qa_started is not None:
                row["qa_time_seconds"] = time.perf_counter() - qa_started
            if generator is not None and row["generation_time_seconds"] is None:
                row["generation_time_seconds"] = time.perf_counter() - start_pipeline
        memory = _stop_memory_sampler(sampler)
        row["peak_rss_bytes"] = memory["value"]
        row["memory_status"] = memory["status"]
        runs.append(row)
        if cancelled:
            break
    success = sum(row["status"] == "SUCCESS" for row in runs)
    failed = sum(row["status"] == "FAILED" for row in runs)
    status = "CANCELLED" if cancelled else "COMPLETED" if success == repetitions else (
        "PARTIAL" if success else "FAILED")
    memory_values = [row["peak_rss_bytes"] for row in runs
                     if isinstance(row.get("peak_rss_bytes"), int)]
    memory_result = ({"value": max(memory_values), "status": "MEASURED_SAMPLED",
                      "method": "sampled_process_tree_rss",
                      "scope": "maximum per-run simultaneous process-tree sample",
                      "sampling_interval_seconds": float(memory_interval_seconds),
                      "limitations": "Sampling can miss short-lived peaks; process membership is observed, not atomic."}
                     if memory_values else {"value": None, "status": "NOT_AVAILABLE",
                                            "method": "unsupported",
                                            "reason": "psutil unavailable or no process-tree RSS sample was collected."})
    return {"schema_version": SCHEMA_VERSION, "run_id": uuid.uuid4().hex,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(), "status": status,
            "requested_repetitions": repetitions, "warmup_runs": warmup,
            "warmup_failures": warmup_failures, "successful_runs": success,
            "failed_runs": failed, "metadata": _environment(generator),
            "quality_config": quality_config, "memory": memory_result,
            "runs": runs, "aggregate": _aggregate(runs)}

