"""On-demand, cancellable quality work over immutable accepted mesh snapshots."""
from __future__ import annotations

import copy
import hashlib
import json
import re
import threading
import tempfile
import time
import uuid
from pathlib import Path

from .document_api import _accepted
from .extraction import _digest
from .job_control import Cancelled, JobControl, checkpoint, job_scope
from .quality_assessment import _config as normalize_quality_config
from .quality_assessment import evaluate_mesh_quality, quality_mesh_hash
from .quality_assessment import quality_config_hash
from .quality_benchmark import run_quality_benchmark
from .quality_context import CONTEXT_DEFAULTS, context_config, enrich_quality
from .quality_reports import _json_bytes, _validated_result, export_quality
from .runner import write_json

MAX_PAGE_SIZE = 200
MAX_QUALITY_JOBS = 50
_CORE_KEYS = {
    "relative_geometry_epsilon", "overlap_relative_tolerance",
    "max_overlap_candidates", "max_issues", "worst_limit",
    "warning_min_angle_deg", "warning_quality_q", "warning_aspect_ratio",
}


def _fingerprint(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False,
                                    allow_nan=False, separators=(",", ":")).encode()).hexdigest()


def _source_hashes(server, mesh_id):
    cells_path = server.artifact(mesh_id, "cells")
    manifest_path = server.artifact(mesh_id, "manifest")
    hashes = {str(cells_path): _digest(cells_path), str(manifest_path): _digest(manifest_path)}
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    artifacts = manifest.get("artifact_hashes", {})
    if not isinstance(artifacts, dict):
        raise ValueError("Accepted mesh manifest has invalid artifact hashes")
    output = Path(server.job(mesh_id)["result"]["output"])
    # These retained files can affect contextual QA independently of cells.json.
    for relative in ("meshing/domain-boundary.json", "meshing/size.pos", "meshing/domain.geo"):
        expected = artifacts.get(relative)
        if expected is None:
            continue
        path = server.permitted(output / relative)
        if not path.is_file():
            raise ValueError(f"Retained QA reference is missing: {relative}")
        actual = _digest(path)
        if actual.lower() != str(expected).lower():
            raise ValueError(f"Retained QA reference changed: {relative}")
        hashes[str(path)] = actual
    return hashes


def _quality_config(config):
    if config is None:
        config = {}
    if not isinstance(config, dict):
        raise ValueError("config must be an object")
    unknown = set(config) - _CORE_KEYS - set(CONTEXT_DEFAULTS)
    if unknown:
        raise ValueError("Unsupported quality config fields: " + ", ".join(sorted(unknown)))
    core = {key: value for key, value in config.items() if key in _CORE_KEYS}
    context = context_config({key: value for key, value in config.items() if key in CONTEXT_DEFAULTS})
    return normalize_quality_config(core), context


def start_quality(server, data):
    """Start one independent quality job and return its ID immediately."""
    if not isinstance(data, dict) or set(data) - {"mesh_job_id", "config", "benchmark", "repetitions", "warmup", "request_id"}:
        raise ValueError("Unsupported quality job fields")
    mesh_id = data.get("mesh_job_id")
    if not isinstance(mesh_id, str) or not mesh_id:
        raise ValueError("Choose an accepted mesh result")
    supplied_config = data.get("config") or {}
    if isinstance(supplied_config, dict):
        supplied_config = dict(supplied_config)
        config_repetitions = supplied_config.pop("repetitions", None)
    else:
        config_repetitions = None
    core_config, ctx_config = _quality_config(supplied_config)
    benchmark = data.get("benchmark", False)
    repetitions = data.get("repetitions", config_repetitions if config_repetitions is not None else 5)
    warmup = data.get("warmup", 0)
    # Validate benchmark limits before accepting a background job.
    if type(benchmark) is not bool:
        raise ValueError("benchmark must be a boolean")
    if type(repetitions) is not int or not 1 <= repetitions <= 50:
        raise ValueError("repetitions must be an integer in [1, 50]")
    if type(warmup) is not int or not 0 <= warmup <= 5:
        raise ValueError("warmup must be an integer in [0, 5]")
    request_id = data.get("request_id")
    if request_id is not None and (not isinstance(request_id, str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{8,80}", request_id)):
        raise ValueError("Invalid quality request ID")
    quality_config_hash(core_config)  # Validate core settings before accepting the background job.
    manifest, cells = _accepted(server, mesh_id)
    source_hashes = _source_hashes(server, mesh_id)
    mesh = copy.deepcopy(cells)
    mesh_hash = quality_mesh_hash(mesh)
    fingerprint = _fingerprint(dict(mesh_job_id=mesh_id, mesh_hash=mesh_hash,
        core_config=core_config, context_config=ctx_config, benchmark=benchmark,
        repetitions=repetitions, warmup=warmup))
    with server.lock:
        if not hasattr(server, "quality_jobs"):
            server.quality_jobs = {}
        if not hasattr(server, "quality_latest"):
            server.quality_latest = {}
        if not hasattr(server, "quality_latest_meta"):
            server.quality_latest_meta = {}
        if request_id:
            existing = next(((ident, job) for ident, job in server.quality_jobs.items()
                             if job.get("request_id") == request_id), None)
            if existing:
                if existing[1].get("request_fingerprint") != fingerprint:
                    raise ValueError("Request ID already belongs to a different quality job")
                return dict(job_id=existing[0], quality_job_id=existing[0], mesh_job_id=mesh_id,
                            mesh_hash=mesh_hash, request_fingerprint=fingerprint)
        if server.picker_active or server.preflight_inflight or server.document_active or server._active_job_locked():
            raise ValueError("Hãy chờ tác vụ hiện tại hoàn tất")
        if len(getattr(server, "quality_jobs", {})) >= MAX_QUALITY_JOBS:
            raise ValueError("Quality job limit reached; restart UI")
        job_id = uuid.uuid4().hex
        control = JobControl(lock=server.lock)
        server.jobs[job_id] = dict(status="running", stage="quality", operation="quality", result=None)
        server.quality_jobs[job_id] = dict(status="running", stage="quality",
            mesh_job_id=mesh_id, mesh_hash=mesh_hash, request_fingerprint=fingerprint,
            request_id=request_id, result=None, error=None)
        server.controls[job_id] = control

    def worker():
        try:
            with job_scope(control):
                checkpoint()
                output = Path(server.job(mesh_id)["result"]["output"])
                from . import quality_references
                roi, size_field = quality_references.load_references(output, cells, manifest)
                qa_started = time.perf_counter()
                report = evaluate_mesh_quality(mesh, config=core_config)
                if report.get("status") == "CANCELLED":
                    raise Cancelled("Đã dừng theo yêu cầu")
                enrich_quality(report, mesh, roi=roi, size_field=size_field, config=ctx_config)
                report.setdefault("performance", {})["core_qa_time_seconds"] = report["performance"].get("qa_time_seconds")
                report["performance"]["qa_time_seconds"] = time.perf_counter() - qa_started
                report["performance"]["qa_time_scope"] = "core geometry, topology and overlap plus retained ROI and size context"
                report["quality_config"] = copy.deepcopy(core_config)
                report['performance']['source_generation_time_seconds']=manifest.get('generation_time_seconds')
                report['performance']['source_pipeline_time_seconds']=manifest.get('pipeline_time_seconds')
                report['performance']['generation_time_seconds_reason']='This QA job reuses an existing mesh; original measured generation time is recorded separately when available.'
                if benchmark:
                    checkpoint()
                    report["benchmark"] = run_quality_benchmark(mesh, repetitions=repetitions,
                        warmup=warmup, quality_config=core_config)
                    report["benchmark"]["scope"] = "geometry_only_qa"
                    report["benchmark"]["context_note"] = (
                        "Repeated benchmark evaluates core geometry/topology only; retained ROI and target-size "
                        "context are evaluated once in the parent report and are not timed per repetition.")
                    checkpoint()
                report["provenance"] = dict(operation='quality', mesh_job_id=mesh_id,
                    mesh_manifest_sha256=source_hashes[next(path for path in source_hashes if path.endswith("manifest.json"))],
                    request_fingerprint=fingerprint)
                if quality_mesh_hash(mesh) != mesh_hash:
                    raise ValueError("Quality evaluation mutated its frozen mesh snapshot")
                if _source_hashes(server, mesh_id) != source_hashes:
                    raise ValueError("Accepted mesh artifacts changed during quality assessment")
                checkpoint()
                status = "pass"
                report_bytes = _json_bytes(report)
                final_output = server.permitted(server.output_root / ("quality-" + job_id), exists=False, write=True)
                with tempfile.TemporaryDirectory(dir=server.output_root, prefix=".quality-stage-") as stage_dir:
                    staging = Path(stage_dir) / "result"
                    staging.mkdir()
                    (staging / "quality.json").write_bytes(report_bytes)
                    quality_digest = _digest(staging / "quality.json")
                    write_json(staging / "manifest.json", dict(schema_version="mesh-quality-job-manifest-v1",
                        status="pass", operation="quality", mesh_job_id=mesh_id, mesh_hash=mesh_hash,
                        source_hashes=source_hashes, quality_sha256=quality_digest,
                        request_fingerprint=fingerprint))
                    if _source_hashes(server, mesh_id) != source_hashes:
                        raise ValueError("Accepted mesh artifacts changed while publishing quality report")
                    checkpoint()
                    with server.lock:
                        if control.cancel.is_set():
                            raise Cancelled("Đã dừng theo yêu cầu")
                        # Publish atomically while cancellation and the job state are serialized.
                        staging.rename(final_output)
                        server.quality_latest[mesh_id] = copy.deepcopy(report)
                        server.quality_latest[job_id] = copy.deepcopy(report)
                        server.quality_latest_meta[mesh_id] = dict(output=str(final_output), sha256=quality_digest,
                            job_id=job_id)
                        server.quality_latest_meta[job_id] = dict(output=str(final_output), sha256=quality_digest,
                            job_id=job_id)
                        server.quality_jobs[job_id].update(status=status, stage="done" if status == "pass" else "fail",
                            result=dict(status=status, mesh_job_id=mesh_id, mesh_hash=mesh_hash), error=None)
                        server.jobs[job_id].update(status=status, stage="done" if status == "pass" else "fail",
                            result=dict(status=status, output=str(final_output), mesh_job_id=mesh_id, mesh_hash=mesh_hash))
        except Cancelled as exc:
            status, error = "cancelled", str(exc)
        except Exception as exc:  # Keep failure visible through the job endpoint.
            status, error = "fail", f"{type(exc).__name__}: {exc}"
        finally:
            with server.lock:
                if server.quality_jobs[job_id]["status"] == "running":
                    server.quality_jobs[job_id].update(status=status, stage=status, error=error,
                        result=dict(status=status, mesh_job_id=mesh_id, mesh_hash=mesh_hash, error=error))
                    server.jobs[job_id].update(status=status, stage=status,
                        result=dict(status=status, output=None, mesh_job_id=mesh_id,
                                    mesh_hash=mesh_hash, error=error))
                control.finished = control.clock()

    threading.Thread(target=worker, daemon=True, name="mesh-quality-" + job_id[:8]).start()
    return dict(job_id=job_id, quality_job_id=job_id, mesh_job_id=mesh_id,
                mesh_hash=mesh_hash, request_fingerprint=fingerprint)


def quality_result(server, data):
    if isinstance(data, dict):
        if set(data) - {"job_id", "mesh_job_id"}:
            raise ValueError("Unsupported quality result fields")
        ident = data.get("job_id", data.get("mesh_job_id"))
    else:
        ident = data
    with server.lock:
        job = getattr(server, "quality_jobs", {}).get(ident)
        if job is not None:
            result = copy.deepcopy(job)
            if ident in server.controls:
                result["monitor"] = server.controls[ident].snapshot()
                result["monitor"].pop("log_lines", None)
            if result.get("status") == "running":
                return result
            parent = result.get("mesh_job_id")
            report = getattr(server, "quality_latest", {}).get(ident)
            job_status = result["status"]
        else:
            parent = ident
            report = getattr(server, "quality_latest", {}).get(parent)
            job_status = "pass"
        if report is None:
            raise ValueError("No completed quality assessment is available")
        report = _current_report(server, parent, report, cache_key=ident)
        if report is None:
            raise ValueError("Saved quality assessment is stale or failed verification")
        result = copy.deepcopy(report)
        result["job_status"] = job_status
        return result


def quality_page(server, data):
    if not isinstance(data, dict) or set(data) - {"job_id", "kind", "offset", "limit", "sort_by", "sort_order", "issue_type"}:
        raise ValueError("Unsupported quality page fields")
    job_id = data.get("job_id", data.get("mesh_job_id"))
    section = data.get("kind", "elements")
    cursor = data.get("offset", 0)
    limit = data.get("limit", 100)
    sort = data.get("sort_by", "element_id")
    sort_order = data.get("sort_order", "asc")
    issue_type = data.get("issue_type")
    if section not in {"elements", "issues"}:
        raise ValueError("section must be elements or issues")
    if sort_order not in {"asc", "desc"}:
        raise ValueError("sort_order must be asc or desc")
    if type(cursor) is not int or cursor < 0 or type(limit) is not int or not 1 <= limit <= MAX_PAGE_SIZE:
        raise ValueError("Invalid quality page cursor or limit")
    with server.lock:
        job = getattr(server, "quality_jobs", {}).get(job_id)
        parent = job.get("mesh_job_id") if job else job_id
        report = getattr(server, "quality_latest", {}).get(job_id)
        cache_key = job_id if job else parent
        if report is None and not job:
            report = getattr(server, "quality_latest", {}).get(parent)
    if report is None:
        raise ValueError("No completed quality assessment is available")
    report = _current_report(server, parent, report, cache_key=cache_key)
    if report is None:
        raise ValueError("Saved quality assessment is stale or failed verification")
    rows = report.get(section, [])
    if section == "elements":
        allowed_sort = {"element_id", "quality_q", "min_angle_deg", "max_angle_deg", "aspect_ratio", "area",
                        "perimeter","longest_edge","shortest_edge","target_size","size_ratio","validity_status"}
        if sort not in allowed_sort:
            raise ValueError("Unsupported quality sort")
        key = lambda row: (str(row.get(sort, "")) if sort in {"element_id","validity_status"} else row.get(sort),
                           str(row.get("element_id", "")))
        present = [row for row in rows if row.get(sort) is not None]
        missing = [row for row in rows if row.get(sort) is None]
        rows = sorted(present, key=key, reverse=sort_order == "desc") + missing
    else:
        if issue_type and issue_type != "ALL":
            rows = [row for row in rows if row.get("code") == issue_type or row.get("category") == issue_type]
        issue_sort={'issue_type':'category'}.get(sort,sort)
        if issue_sort not in {'code','severity','category','message','element_ids','node_ids'}:issue_sort='code'
        rows = sorted(rows, key=lambda row: (str(row.get(issue_sort, "")),
                                               str(row.get("severity", "")),
                                               str(row.get("element_ids", []))), reverse=sort_order == "desc")
    page = copy.deepcopy(rows[cursor:cursor + limit])
    return dict(job_id=job_id, mesh_hash=report.get("mesh_hash"), kind=section,
        sort_by=sort, sort_order=sort_order, offset=cursor, limit=limit, total=len(rows),
        next_offset=(cursor + len(page) if cursor + len(page) < len(rows) else None), items=page)


def quality_export(server, data):
    if not isinstance(data, dict) or set(data) - {"job_id", "mesh_job_id", "format"}:
        raise ValueError("Unsupported quality export fields")
    mesh_id, fmt = data.get("job_id", data.get("mesh_job_id")), data.get("format")
    with server.lock:
        job = getattr(server, "quality_jobs", {}).get(mesh_id)
        parent = job.get("mesh_job_id") if job else mesh_id
        report = getattr(server, "quality_latest", {}).get(mesh_id)
        cache_key = mesh_id if job else parent
        if report is None and not job:
            report = getattr(server, "quality_latest", {}).get(parent)
    if report is None:
        raise ValueError("No completed quality assessment is available")
    report = _current_report(server, parent, report, cache_key=cache_key)
    if report is None:
        raise ValueError("Saved quality assessment is stale or failed verification")
    return export_quality(report, fmt)


def register_cached_quality(server, parent, result):
    """Publish a validated report from the document workflow under its job ID."""
    from .quality_reports import _validated_result
    report = _validated_result(result)
    if not isinstance(parent, str) or not parent:
        raise ValueError("A parent mesh job ID is required")
    with server.lock:
        if not hasattr(server, "quality_latest"):
            server.quality_latest = {}
        if not hasattr(server, "quality_latest_meta"):
            server.quality_latest_meta = {}
        server.quality_latest[parent] = copy.deepcopy(report)
    return copy.deepcopy(report)


def quality_latest(server, data):
    """Return the latest verified report for an accepted mesh, if available."""
    if not isinstance(data, dict) or set(data) != {"mesh_job_id"}:
        raise ValueError("Quality latest requires mesh_job_id")
    parent = data.get("mesh_job_id")
    report = cached_quality_for_mesh(server, parent)
    if report is None:
        return {"status": "not_available"}
    with server.lock:
        metadata = getattr(server, "quality_latest_meta", {}).get(parent, {})
        job_id = metadata.get("job_id", parent) if isinstance(metadata, dict) else parent
    return {"status": "pass", "job_id": job_id, "mesh_job_id": parent,
            "report": copy.deepcopy(report)}


def cached_quality_for_mesh(server, parent):
    """Return only a bounded cached assessment whose geometry/config still match."""
    if not isinstance(parent, str) or not parent or len(parent) > 200:
        return None
    with server.lock:
        report = getattr(server, "quality_latest", {}).get(parent)
    return _current_report(server, parent, report)


def _current_report(server, parent, report, *, cache_key=None):
    if not isinstance(report, dict) or not isinstance(parent, str) or not parent:
        return None
    with server.lock:
        metadata = copy.deepcopy(getattr(server, "quality_latest_meta", {}).get(cache_key or parent))
    from .project_io import quality_cache_state
    cached_config = report.get("quality_config")
    try:
        manifest, cells = _accepted(server, parent)
        state = quality_cache_state(report, cells, cached_config)
    except (ValueError, KeyError, OSError):
        return None
    if state.get("status") != "CURRENT":
        return None
    try:
        manifest_path = server.artifact(parent, "manifest")
        current_manifest_hash = _digest(manifest_path)
        if metadata is not None:
            quality_output = server.permitted(metadata.get("output", ""))
            report_path = server.permitted(quality_output / "quality.json")
            report_manifest_path = server.permitted(quality_output / "manifest.json")
            report_manifest = json.loads(report_manifest_path.read_text(encoding="utf-8"))
            serialized = _json_bytes(report)
            report_hash = hashlib.sha256(serialized).hexdigest()
            if (not isinstance(report_manifest, dict)
                    or report_manifest.get("status") != "pass"
                    or report_manifest.get("operation") != "quality"
                    or _digest(report_path) != metadata.get("sha256")
                    or report_hash != metadata.get("sha256")
                    or report_manifest.get("quality_sha256") != metadata.get("sha256")
                    or report_manifest.get("mesh_hash") != report.get("mesh_hash")):
                return None
            expected_parent = report_manifest.get("source_hashes")
            if expected_parent != _source_hashes(server, parent):
                return None
        provenance = report.get("provenance")
        # New on-demand reports are bound to the accepted manifest. Imported
        # project reports are revalidated against their restored cells/config.
        if isinstance(provenance, dict) and provenance.get("mesh_job_id") == parent:
            if provenance.get("mesh_manifest_sha256") != current_manifest_hash:
                return None
        _json_bytes(report)  # strict finite JSON and the 64 MiB report bound
    except (ValueError, TypeError, OSError, KeyError, AttributeError):
        return None
    return state["assessment"]
