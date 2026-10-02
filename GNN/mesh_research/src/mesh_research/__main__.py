from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any

from .boundary import BoundaryError, derive_boundary, verify_component_hashes
from .gmsh_adapter import (
    GmshRunError,
    generate_polygon_domain,
    generate_synthetic_domain,
)
from .remapping import remap_raster
from .topology import MeshValidationError, build_cell_graph


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _collect_artifact_hashes(run_directory: Path) -> dict[str, str]:
    return {
        path.relative_to(run_directory).as_posix(): _sha256(path)
        for path in sorted(item for item in run_directory.rglob("*") if item.is_file())
        if path.name != "manifest.json"
    }


def _manifest_payload(
    *,
    config: dict[str, Any],
    config_path: Path,
    config_hash: str,
    run_id: str,
    created_at: datetime,
    status: str,
    artifact_hashes: dict[str, str],
    generator: dict[str, Any] | None = None,
    resources: dict[str, Any] | None = None,
    errors: dict[str, str] | list[str] | None = None,
    additional_inputs: list[dict[str, str]] | None = None,
    boundary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": "mesh-run-manifest-v0",
        "run_id": run_id,
        "run_name": config.get("run_name"),
        "created_at": created_at.isoformat(),
        "config": config,
        "config_sha256": config_hash,
        "inputs": [
            {
                "role": "run_config",
                "logical_path": str(config_path),
                "sha256": config_hash,
            }
        ],
        "crs": config.get("crs"),
        "units": {
            "coordinate": config.get("coordinate_units"),
            "state": config.get("state_units"),
        },
        "generator": generator if generator is not None else config.get("generator"),
        "domain": config.get("domain"),
        "artifact_hashes": artifact_hashes,
        "status": status,
    }
    if resources is not None:
        payload["resources"] = resources
    if additional_inputs:
        payload["inputs"].extend(additional_inputs)
    if boundary is not None:
        payload["boundary"] = boundary
    if errors is not None:
        payload["errors"] = errors
    return payload


def _validate_config(config: Any) -> dict[str, str]:
    if not isinstance(config, dict):
        return {"config": "must be a JSON object"}
    required_fields = (
        "schema_version",
        "run_name",
        "generator",
        "domain",
        "crs",
        "coordinate_units",
        "state_units",
        "output_root",
    )
    errors = {
        field: "is required" for field in required_fields if field not in config
    }
    if "generator" in config:
        generator = config["generator"]
        if not isinstance(generator, dict):
            errors["generator"] = "must be an object"
        elif generator.get("name") not in {"fixture", "gmsh"}:
            errors["generator.name"] = (
                f"unsupported generator: {generator.get('name')}"
            )
        elif generator.get("name") == "gmsh":
            if not generator.get("executable"):
                errors["generator.executable"] = "is required for Gmsh"
            if not isinstance(generator.get("parameters"), dict):
                errors["generator.parameters"] = "must be an object for Gmsh"
            domain = config.get("domain")
            supported_domains = {
                "synthetic_inclined_plane",
                "synthetic_v_valley",
                "synthetic_radial_bowl",
                "synthetic_y_drainage",
                "roi_boundary",
            }
            if not isinstance(domain, dict) or domain.get("kind") not in supported_domains:
                errors["domain.kind"] = (
                    f"unsupported Gmsh domain: {domain.get('kind') if isinstance(domain, dict) else None}"
                )
            elif domain.get("kind") == "roi_boundary":
                if not domain.get("boundary_source"):
                    errors["domain.boundary_source"] = "is required for roi_boundary"
                simplification = domain.get("simplification")
                if not isinstance(simplification, dict):
                    errors["domain.simplification"] = "must be an object for roi_boundary"
                else:
                    for field in (
                        "method",
                        "tolerances",
                        "max_relative_area_difference",
                        "max_hausdorff_distance",
                        "hausdorff_sampling_interval",
                    ):
                        if field not in simplification:
                            errors[f"domain.simplification.{field}"] = "is required"
    return errors


def _run_matrix(
    config: dict[str, Any],
    config_path: Path,
    config_bytes: bytes,
) -> int:
    config_hash = hashlib.sha256(config_bytes).hexdigest()
    created_at = datetime.now(timezone.utc)
    timestamp_token = created_at.strftime("%Y%m%dT%H%M%S%fZ")
    matrix_id = f"{timestamp_token}-{config_hash[:8]}"
    matrix_directory = Path(config["output_root"]) / matrix_id
    matrix_directory.mkdir(parents=True, exist_ok=False)
    _write_json(matrix_directory / "matrix-config.json", config)

    matrix_kind = config.get("matrix_kind", "synthetic")
    rows: list[dict[str, Any]] = []
    overall_status = "pass"
    for domain in config["domains"]:
        for refinement in config["refinements"]:
            case_name = f"{domain['name']}-{refinement['name']}"
            child_output_root = matrix_directory / "runs" / case_name
            child_generator = copy.deepcopy(config["generator"])
            child_generator.setdefault("parameters", {})["target_edge"] = refinement[
                "target_edge"
            ]
            child_config_path = matrix_directory / "configs" / f"{case_name}.json"
            child_domain = copy.deepcopy(domain)
            if child_domain.get("boundary_source"):
                source_path = Path(child_domain["boundary_source"])
                if not source_path.is_absolute():
                    source_path = (config_path.parent / source_path).resolve()
                try:
                    child_domain["boundary_source"] = os.path.relpath(
                        source_path, child_config_path.parent.resolve()
                    )
                except ValueError:
                    child_domain["boundary_source"] = str(source_path)
            child_config = {
                "schema_version": "mesh-run-config-v0",
                "run_name": case_name,
                "generator": child_generator,
                "domain": child_domain,
                "crs": config["crs"],
                "coordinate_units": config["coordinate_units"],
                "state_units": config["state_units"],
                "output_root": str(child_output_root),
            }
            if "remapping" in config:
                child_remapping = copy.deepcopy(config["remapping"])
                for dataset in child_remapping.get("datasets", []):
                    raster_path = Path(dataset["path"])
                    if not raster_path.is_absolute():
                        raster_path = (config_path.parent / raster_path).resolve()
                    try:
                        dataset["path"] = os.path.relpath(
                            raster_path, child_config_path.parent.resolve()
                        )
                    except ValueError:
                        dataset["path"] = str(raster_path)
                child_config["remapping"] = child_remapping
            _write_json(child_config_path, child_config)
            exit_code = _run(child_config_path)
            child_run_directories = (
                list(child_output_root.iterdir()) if child_output_root.exists() else []
            )
            if not child_run_directories:
                overall_status = "fail"
                rows.append(
                    {
                        "domain": domain["name"],
                        "refinement": refinement["name"],
                        "target_edge": refinement["target_edge"],
                        "status": "fail",
                        "error": "child run produced no auditable run directory",
                    }
                )
                continue
            child_run_directory = child_run_directories[0]
            manifest = json.loads(
                (child_run_directory / "manifest.json").read_text(encoding="utf-8")
            )
            qa_path = child_run_directory / "qa" / "qa.json"
            qa = json.loads(qa_path.read_text(encoding="utf-8")) if qa_path.exists() else {}
            status = "pass" if exit_code == 0 and manifest["status"] == "pass" else "fail"
            if status == "fail":
                overall_status = "fail"
            metrics = qa.get("metrics", {})
            resources = manifest.get("resources", {})
            row = {
                "domain": domain["name"],
                "refinement": refinement["name"],
                "target_edge": refinement["target_edge"],
                "status": status,
                "run_directory": child_run_directory.relative_to(
                    matrix_directory
                ).as_posix(),
                "cell_count": metrics.get("cell_count"),
                "interface_count": metrics.get("physical_interface_count"),
                "physical_interface_count": metrics.get("physical_interface_count"),
                "directed_edge_count": metrics.get("directed_edge_count"),
                "quality": metrics.get("quality"),
                "wall_time_seconds": resources.get("wall_time_seconds"),
                "peak_rss_bytes": resources.get("peak_rss_bytes"),
                "hard_gates": qa.get("hard_gates", []),
                "domain_checks": qa.get("domain_checks", []),
                "errors": manifest.get("errors", []),
            }
            if matrix_kind == "roi4_resolution_sweep":
                row["boundary"] = {
                    **manifest.get("boundary", {}),
                    **qa.get("boundary_assessment", {}),
                }
                semantic_status = config.get("semantic_evidence", {}).get(
                    "status", "not_available"
                )
                row["remapping"] = {
                    "status": semantic_status,
                    "checks": qa.get("remapping_checks", []),
                }
            rows.append(row)

    summary: dict[str, Any] = {
        "schema_version": (
            "roi4-resolution-sweep-v0"
            if matrix_kind == "roi4_resolution_sweep"
            else "synthetic-matrix-summary-v0"
        ),
        "matrix_id": matrix_id,
        "created_at": created_at.isoformat(),
        "source_config": str(config_path),
        "source_config_sha256": config_hash,
        "status": overall_status,
        "runs": rows,
    }
    summary_name = "synthetic-matrix-summary.json"
    if matrix_kind == "roi4_resolution_sweep":
        successful_rows = [row for row in rows if row["status"] == "pass"]
        non_dominated: list[str] = []
        objectives = (
            lambda row: (
                row["quality"]["edge_length"]["mean"],
                row["cell_count"],
                row["wall_time_seconds"],
                row["peak_rss_bytes"],
            )
        )
        for candidate in successful_rows:
            candidate_values = objectives(candidate)
            dominated = any(
                all(other_value <= candidate_value for other_value, candidate_value in zip(objectives(other), candidate_values))
                and any(other_value < candidate_value for other_value, candidate_value in zip(objectives(other), candidate_values))
                for other in successful_rows
                if other is not candidate
            )
            if not dominated:
                non_dominated.append(candidate["refinement"])
        summary.update(
            {
                "semantic_evidence": config.get(
                    "semantic_evidence",
                    {
                        "status": "not_available",
                        "audit": [],
                    },
                ),
                "pareto": {
                    "scope": "mesh_geometry_resolution_vs_cost",
                    "objectives": [
                        "mean_edge_length",
                        "cell_count",
                        "gmsh_wall_time_seconds",
                        "gmsh_peak_rss_bytes",
                    ],
                    "non_dominated_refinements": non_dominated,
                    "statement": (
                        "This comparison measures mesh geometry resolution and Gmsh "
                        "resource cost, not hydrological predictive accuracy."
                    ),
                },
                "claims": {
                    "hydrological_routing_validity": False,
                    "watershed_divide_verified": False,
                    "outlet_verified": False,
                },
                "limitations": [
                    "The same derived ROI4 boundary is used at every target size, so mesh refinement does not reduce boundary-simplification error.",
                    "Resource measurements cover the Gmsh subprocess rather than whole-pipeline memory and time.",
                ],
            }
        )
        summary_name = "roi4-resolution-sweep.json"
    _write_json(matrix_directory / summary_name, summary)
    print(matrix_directory)
    return 0 if overall_status == "pass" else 1


def _run(config_path: Path) -> int:
    config_bytes = config_path.read_bytes()
    config = json.loads(config_bytes)
    if config.get("schema_version") == "mesh-matrix-config-v0":
        return _run_matrix(config, config_path, config_bytes)
    if config.get("schema_version") == "dem-adaptive-experiment-v1":
        from .adaptive_experiment import run_adaptive_experiment
        return run_adaptive_experiment(config, config_path, config_bytes)
    config_hash = hashlib.sha256(config_bytes).hexdigest()
    created_at = datetime.now(timezone.utc)
    timestamp_token = created_at.strftime("%Y%m%dT%H%M%S%fZ")
    run_id = f"{timestamp_token}-{config_hash[:8]}"
    run_directory = Path(config["output_root"]) / run_id
    errors = _validate_config(config)
    if errors:
        _write_json(
            run_directory / "manifest.json",
            _manifest_payload(
                config=config,
                config_path=config_path,
                config_hash=config_hash,
                run_id=run_id,
                created_at=created_at,
                status="fail",
                artifact_hashes={},
                errors=errors,
            ),
        )
        print(
            "; ".join(f"{field}: {message}" for field, message in errors.items()),
            file=sys.stderr,
        )
        return 2

    boundary_context: dict[str, Any] | None = None
    boundary_manifest: dict[str, Any] | None = None
    boundary_inputs: list[dict[str, str]] = []
    if config["generator"]["name"] == "gmsh":
        try:
            if config["domain"]["kind"] == "roi_boundary":
                boundary_context = derive_boundary(
                    config["domain"], config_path, run_directory / "boundary"
                )
                source = boundary_context["source"]
                selected = boundary_context["selected"]["selected_candidate"]
                boundary_inputs = [
                    {
                        "role": f"canonical_boundary{item['suffix']}",
                        "logical_path": item["logical_path"],
                        "sha256": item["sha256"],
                    }
                    for item in source["components"]
                ]
                boundary_manifest = {
                    "source_set_sha256": source["source_set_sha256"],
                    "simplification_method": selected["method"],
                    "selected_tolerance": selected["tolerance"],
                    "hausdorff_method": selected["hausdorff_method"],
                    "hausdorff_sampling_interval": selected["hausdorff_sampling_interval"],
                    "hausdorff_additive_error_bound": selected["hausdorff_additive_error_bound"],
                    "hausdorff_distance_upper_bound": selected["hausdorff_distance_upper_bound"],
                    "selection_rule": boundary_context["selected"]["selection_rule"],
                    "sweep_artifact": "boundary/sweep.json",
                    "selected_boundary_artifact": "boundary/selected-boundary.json",
                    "watershed_divide_status": "UNVERIFIED",
                    "outlet_status": "UNVERIFIED",
                }
                (
                    vertices,
                    triangles,
                    effective_generator,
                    resources,
                    domain_checks,
                ) = generate_polygon_domain(
                    config["generator"], boundary_context["ring"], run_directory / "mesh"
                )
            else:
                (
                    vertices,
                    triangles,
                    effective_generator,
                    resources,
                    domain_checks,
                ) = generate_synthetic_domain(
                    config["generator"], config["domain"], run_directory / "mesh"
                )
        except (BoundaryError, GmshRunError, KeyError, TypeError, ValueError) as error:
            artifact_hashes = _collect_artifact_hashes(run_directory)
            _write_json(
                run_directory / "manifest.json",
                _manifest_payload(
                    config=config,
                    config_path=config_path,
                    config_hash=config_hash,
                    run_id=run_id,
                    created_at=created_at,
                    status="fail",
                    artifact_hashes=artifact_hashes,
                    errors={"run": str(error)},
                    additional_inputs=boundary_inputs,
                    boundary=boundary_manifest,
                ),
            )
            print(str(error), file=sys.stderr)
            return 1
    else:
        configured_mesh = config["domain"].get("mesh")
        if configured_mesh:
            vertices = configured_mesh["vertices"]
            triangles = configured_mesh["triangles"]
        else:
            vertices = [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]]
            triangles = [[0, 1, 2], [0, 2, 3]]
        effective_generator = config["generator"]
        resources = {"wall_time_seconds": 0.0, "peak_rss_bytes": 0}
        domain_checks = []

    try:
        triangles, graph, qa = build_cell_graph(vertices, triangles)
    except MeshValidationError as error:
        mesh = {
            "schema_version": "mesh-artifact-v0",
            "crs": config["crs"],
            "coordinate_units": config["coordinate_units"],
            "vertices": vertices,
            "triangles": triangles,
        }
        qa = {
            "schema_version": "mesh-qa-v0",
            "status": "fail",
            "hard_gates": [
                {
                    "name": "mesh_topology",
                    "status": "fail",
                    "errors": error.errors,
                }
            ],
        }
        _write_json(run_directory / "mesh" / "mesh.json", mesh)
        _write_json(run_directory / "qa" / "qa.json", qa)
        artifact_hashes = _collect_artifact_hashes(run_directory)
        _write_json(
            run_directory / "manifest.json",
            _manifest_payload(
                config=config,
                config_path=config_path,
                config_hash=config_hash,
                run_id=run_id,
                created_at=created_at,
                status="fail",
                artifact_hashes=artifact_hashes,
                generator=effective_generator,
                resources=resources,
                errors=error.errors,
                additional_inputs=boundary_inputs,
                boundary=boundary_manifest,
            ),
        )
        print(str(error), file=sys.stderr)
        return 1

    qa["domain_checks"] = domain_checks
    failed_domain_checks = [
        check["name"] for check in domain_checks if check["status"] != "pass"
    ]
    if failed_domain_checks:
        qa["status"] = "fail"
        qa["hard_gates"].append(
            {
                "name": "synthetic_domain_expectations",
                "status": "fail",
                "errors": failed_domain_checks,
            }
        )

    boundary_errors: list[str] = []
    if boundary_context is not None:
        immutable, changed_components = verify_component_hashes(
            boundary_context["source"]["components"]
        )
        qa["hard_gates"].append(
            {
                "name": "canonical_boundary_immutability",
                "status": "pass" if immutable else "fail",
                "changed_components": changed_components,
            }
        )
        if not immutable:
            qa["status"] = "fail"
            boundary_errors.append("canonical_boundary_immutability")
        selected = boundary_context["selected"]["selected_candidate"]
        qa["boundary_assessment"] = {
            "geometric_boundary_classification": "EXTERIOR_EDGE",
            "physical_boundary_status": "UNVERIFIED",
            "watershed_divide_status": "UNVERIFIED",
            "outlet_status": "UNVERIFIED",
            "statement": (
                "Mesh exterior geometry does not verify that ROI4 follows a "
                "watershed divide and does not identify a physical outlet."
            ),
            "selected_tolerance": selected["tolerance"],
            "relative_area_difference": selected["relative_area_difference"],
            "hausdorff_distance": selected["hausdorff_distance"],
            "hausdorff_method": selected["hausdorff_method"],
            "hausdorff_sampling_interval": selected["hausdorff_sampling_interval"],
            "hausdorff_additive_error_bound": selected["hausdorff_additive_error_bound"],
            "hausdorff_source_to_candidate": selected["hausdorff_source_to_candidate"],
            "hausdorff_candidate_to_source": selected["hausdorff_candidate_to_source"],
            "hausdorff_distance_upper_bound": selected["hausdorff_distance_upper_bound"],
        }

    mesh = {
        "schema_version": "mesh-artifact-v0",
        "crs": config["crs"],
        "coordinate_units": config["coordinate_units"],
        "vertices": vertices,
        "triangles": triangles,
    }

    remapping_inputs: list[dict[str, str]] = []
    remapping_checks: list[dict[str, Any]] = []
    conservation_tolerance = float(
        config.get("remapping", {}).get("conservation_tolerance", 1e-9)
    )
    for dataset in config.get("remapping", {}).get("datasets", []):
        raster_path = Path(dataset["path"])
        if not raster_path.is_absolute():
            raster_path = (config_path.parent / raster_path).resolve()
        remapping = remap_raster(raster_path, vertices, triangles)
        remapping_path = run_directory / "remapping" / f"{remapping['name']}.json"
        _write_json(remapping_path, remapping)
        remapping_inputs.append(
            {
                "role": "remapping_raster",
                "logical_path": str(raster_path),
                "sha256": remapping["source_sha256"],
            }
        )
        coverage = remapping["cell_coverage_fractions"]
        check: dict[str, Any] = {
            "name": remapping["name"],
            "quantity_type": remapping["quantity_type"],
            "status": "pass",
            "minimum_cell_coverage_fraction": min(coverage),
            "mean_cell_coverage_fraction": sum(coverage) / len(coverage),
        }
        if "conservation" in remapping:
            check["conservation"] = {
                **remapping["conservation"],
                "tolerance": conservation_tolerance,
            }
            if remapping["conservation"]["residual"] > conservation_tolerance:
                check["status"] = "fail"
        remapping_checks.append(check)

    qa["remapping_checks"] = remapping_checks
    failed_remapping_checks = [
        check["name"] for check in remapping_checks if check["status"] != "pass"
    ]
    if failed_remapping_checks:
        qa["status"] = "fail"
        qa["hard_gates"].append(
            {
                "name": "semantic_remapping",
                "status": "fail",
                "errors": failed_remapping_checks,
            }
        )

    artifact_paths = {
        "mesh/mesh.json": run_directory / "mesh" / "mesh.json",
        "graph/graph.json": run_directory / "graph" / "graph.json",
        "qa/qa.json": run_directory / "qa" / "qa.json",
    }
    _write_json(artifact_paths["mesh/mesh.json"], mesh)
    _write_json(artifact_paths["graph/graph.json"], graph)
    _write_json(artifact_paths["qa/qa.json"], qa)
    artifact_hashes = _collect_artifact_hashes(run_directory)
    final_status = qa["status"]
    final_errors = failed_domain_checks + failed_remapping_checks + boundary_errors
    _write_json(
        run_directory / "manifest.json",
        _manifest_payload(
            config=config,
            config_path=config_path,
            config_hash=config_hash,
            run_id=run_id,
            created_at=created_at,
            status=final_status,
            artifact_hashes=artifact_hashes,
            generator=effective_generator,
            resources=resources,
            errors=final_errors or None,
            additional_inputs=boundary_inputs + remapping_inputs,
            boundary=boundary_manifest,
        ),
    )
    if final_errors:
        print("; ".join(final_errors), file=sys.stderr)
        return 1
    print(run_directory)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="mesh-research")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("config", type=Path)
    arguments = parser.parse_args()

    if arguments.command == "run":
        return _run(arguments.config)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
