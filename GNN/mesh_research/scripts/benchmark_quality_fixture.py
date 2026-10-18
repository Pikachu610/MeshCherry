"""Reproducible synthetic benchmark for the 2D mesh quality evaluator."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from gis_mesh_workbench.quality_assessment import quality_mesh_hash
from gis_mesh_workbench.quality_benchmark import MAX_REPETITIONS, run_quality_benchmark
from gis_mesh_workbench.workload_limits import MAX_MESH_CELLS

CRS_WKT = "EPSG:32649"


def rectangular_triangle_grid(subdivisions: int) -> dict:
    """Build a deterministic n-by-n unit-square grid split into CCW triangles."""
    cells = []
    for row in range(subdivisions):
        y0, y1 = float(row), float(row + 1)
        for col in range(subdivisions):
            x0, x1 = float(col), float(col + 1)
            a, b, c, d = (x0, y0), (x1, y0), (x1, y1), (x0, y1)
            index = 2 * (row * subdivisions + col)
            cells.append({"cell_id": f"cell-{index}", "kind": "triangle", "xy": [a, b, c]})
            cells.append({"cell_id": f"cell-{index + 1}", "kind": "triangle", "xy": [a, c, d]})
    return {"schema_version": "workbench-cells-v1", "crs_wkt": CRS_WKT,
            "coordinate_units": "m", "cells": cells}


def run_benchmark(subdivisions: int, repetitions: int) -> dict:
    if type(subdivisions) is not int or subdivisions < 1:
        raise ValueError("subdivisions must be a positive integer")
    cell_count = 2 * subdivisions * subdivisions
    if cell_count > MAX_MESH_CELLS:
        raise ValueError(f"fixture has {cell_count} cells; maximum is {MAX_MESH_CELLS}")
    if type(repetitions) is not int or not 1 <= repetitions <= MAX_REPETITIONS:
        raise ValueError(f"repetitions must be an integer in 1..{MAX_REPETITIONS}")

    fixture = rectangular_triangle_grid(subdivisions)
    geometry_hash = quality_mesh_hash(fixture)
    benchmark = run_quality_benchmark(fixture, repetitions=repetitions, warmup=0)
    if benchmark.get("successful_runs") != repetitions or any(
            row.get("element_count") != cell_count for row in benchmark.get("runs", [])):
        raise RuntimeError("quality evaluator returned an unexpected element count")
    return {
        "schema_version": "synthetic-quality-benchmark-v1",
        "fixture": "rectangular-unit-grid-split-into-ccw-triangles",
        "fixture_parameters": {"crs_wkt": CRS_WKT, "subdivisions_per_side": subdivisions,
                               "triangles_per_square": 2, "cell_count": cell_count,
                               "cell_width_m": 1, "cell_height_m": 1},
        "input_geometry_sha256": geometry_hash,
        "benchmark": benchmark,
        "performance_scope": "synthetic fixture only; not a real-basin or 100000-cell performance claim",
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--subdivisions", type=int, default=25,
                        help="grid subdivisions per side (default: 25)")
    parser.add_argument("--repetitions", type=int, default=5,
                        help="evaluation repetitions, 1..100 (default: 5)")
    parser.add_argument("--output", required=True, type=Path,
                        help="new JSON output path; existing files are never overwritten")
    args = parser.parse_args(argv)
    try:
        result = run_benchmark(args.subdivisions, args.repetitions)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
    except (FileExistsError, OSError, ValueError, RuntimeError) as error:
        parser.error(str(error))
    benchmark = result["benchmark"]
    aggregate = benchmark.get("aggregate", {})
    print(json.dumps({"output": str(args.output),
                      "fixture": result["fixture_parameters"],
                      "input_geometry_sha256": result["input_geometry_sha256"],
                      "benchmark_status": benchmark.get("status"),
                      "qa_time_seconds": aggregate.get("qa_time_seconds"),
                      "memory": benchmark.get("memory")},
                     ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
