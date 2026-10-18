"""Run an explicit, repeatable QA benchmark from a saved offline `.mcy` project."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from gis_mesh_workbench.project_io import load_project
from gis_mesh_workbench.quality_benchmark import (
    DEFAULT_REPETITIONS, MAX_REPETITIONS, MAX_WARMUP, run_quality_benchmark,
)

MAX_CONFIG_BYTES = 1024 * 1024


def _config(path):
    if path is None:
        return None
    path = Path(path)
    if not path.is_file() or path.stat().st_size > MAX_CONFIG_BYTES:
        raise ValueError("Quality config must be a JSON file no larger than 1 MiB")
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError("Quality config JSON must contain an object")
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", type=Path, help="saved `.mcy` project; no remeshing is run")
    parser.add_argument("--repetitions", type=int, default=DEFAULT_REPETITIONS,
                        choices=range(1, MAX_REPETITIONS + 1))
    parser.add_argument("--warmup", type=int, default=0, choices=range(0, MAX_WARMUP + 1))
    parser.add_argument("--config", type=Path, help="optional quality threshold JSON")
    parser.add_argument("--output", type=Path, required=True,
                        help="new JSON output path (existing files are never overwritten)")
    args = parser.parse_args(argv)
    project = load_project(args.project)
    result = run_quality_benchmark(project["cells"], repetitions=args.repetitions,
                                   warmup=args.warmup, quality_config=_config(args.config))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("xb") as target:
        target.write((json.dumps(result, ensure_ascii=False, allow_nan=False,
                                 indent=2, sort_keys=True) + "\n").encode("utf-8"))
    print(json.dumps({"status": result["status"], "successful_runs": result["successful_runs"],
                      "failed_runs": result["failed_runs"], "output": str(args.output)},
                     ensure_ascii=False))
    return 0 if result["status"] == "COMPLETED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
