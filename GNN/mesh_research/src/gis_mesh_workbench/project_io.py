"""Portable, offline MeshCherry project snapshots.

The archive is a small, versioned interchange format, not a run directory.
It contains only accepted cells and the evidence/configuration supplied by the
caller. It never resolves an input path, invokes Gmsh, or re-runs extraction.
"""
from __future__ import annotations

import hashlib
import io
import json
import math
from pathlib import Path
import re
import stat
import zipfile

from .extraction import Cell, _cells
from .spatial import metric_crs
from .workload_limits import MAX_MESH_BYTES, MAX_MESH_CELLS

PROJECT_SCHEMA = "meshcherry-project-v1"
MAX_ARCHIVE_BYTES = MAX_MESH_BYTES
MAX_UNCOMPRESSED_BYTES = 128 * 1024 * 1024
MAX_JSON_BYTES = MAX_UNCOMPRESSED_BYTES
MAX_MEMBERS = 6
MAX_COMPRESSION_RATIO = 200
_VIEW_KEYS = {"show_edges", "show_nodes", "result_tab", "selected_cell_id", "view_box"}
_SECRET_KEY = re.compile(r"(?:token|secret|password|credential|api.?key|access.?key|private.?key)", re.I)
_PATH_KEY = re.compile(r"(?:^|_)(?:path|root|executable|source_file)$", re.I)
_ABSOLUTE_PATH = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\|/)")
_MEMBERS = {"project.json", "cells.json", "qa.json", "config.json", "attributes.json"}


def _json_bytes(value):
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False,
                          sort_keys=True, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("Project data must be finite JSON") from exc


def _json_load(data):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON object key")
            result[key] = value
        return result
    return json.loads(data.decode("utf-8-sig"), object_pairs_hook=pairs,
                      parse_constant=lambda value: (_ for _ in ()).throw(
                          ValueError("Nonfinite JSON constant")))


def _finite_tree(value, label="data"):
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"{label} contains a nonfinite number")
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise ValueError(f"{label} object keys must be strings")
        for child in value.values():
            _finite_tree(child, label)
    elif isinstance(value, list):
        for child in value:
            _finite_tree(child, label)


def _safe_config(value):
    """Drop secrets and host paths while retaining reproducibility settings."""
    unavailable = []
    def clean(node, parent=""):
        if isinstance(node, dict):
            result = {}
            for key, child in node.items():
                if not isinstance(key, str) or _SECRET_KEY.search(key):
                    continue
                if _ABSOLUTE_PATH.match(key.strip()):
                    label = Path(key.replace("\\", "/")).name[:160]
                    unavailable.append({"field": f"{parent}.<path-key>".strip("."),
                                        "label": label or "local input"})
                    continue
                path_value = (isinstance(child, str) and
                              _ABSOLUTE_PATH.match(child.strip()) is not None)
                if _PATH_KEY.search(key) or path_value:
                    if isinstance(child, str) and child.strip():
                        label = Path(child.replace("\\", "/")).name[:160]
                        unavailable.append({"field": f"{parent}.{key}".strip("."),
                                            "label": label or "local input"})
                    result[key] = None
                else:
                    result[key] = clean(child, f"{parent}.{key}".strip("."))
            return result
        if isinstance(node, list):
            return [clean(child, parent) for child in node]
        return node
    return clean(value), unavailable


def _cells_payload(payload):
    if not isinstance(payload, dict) or set(payload) != {
            "schema_version", "crs_wkt", "coordinate_units", "cells"}:
        raise ValueError("Cells must use the complete workbench-cells-v1 schema")
    if payload["schema_version"] != "workbench-cells-v1" or payload["coordinate_units"] != "m":
        raise ValueError("Unsupported project cell schema or coordinate units")
    crs = metric_crs(payload["crs_wkt"])
    rows = payload["cells"]
    if not isinstance(rows, list) or not 0 < len(rows) <= MAX_MESH_CELLS:
        raise ValueError("Project cell count is outside supported limits")
    cells = []
    canonical = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"cell_id", "kind", "xy"}:
            raise ValueError("Malformed cell record")
        if not isinstance(row["cell_id"], str) or not row["cell_id"]:
            raise ValueError("Cell ID must be a nonempty string")
        if not isinstance(row["xy"], list):
            raise ValueError("Cell coordinates must be arrays")
        points = []
        for point in row["xy"]:
            if not isinstance(point, list) or len(point) != 2 or any(
                    type(value) not in (int, float) or not math.isfinite(value)
                    for value in point):
                raise ValueError("Cell XY coordinates must be finite numbers")
            points.append((float(point[0]), float(point[1])))
        cells.append(Cell(row["cell_id"], row["kind"], tuple(points)))
        canonical.append({"cell_id": row["cell_id"], "kind": row["kind"],
                          "xy": [[x, y] for x, y in points]})
    _cells(tuple(cells))
    normalized = {"schema_version": "workbench-cells-v1", "crs_wkt": crs.to_wkt(),
                  "coordinate_units": "m", "cells": canonical}
    return normalized, tuple(cells), crs


def _validate_qa(qa, cell_count):
    if not isinstance(qa, dict):
        raise ValueError("Project QA must be an object")
    _finite_tree(qa, "QA")
    # The source archive's pass label is retained as history, never used as proof.
    if qa.get("status") not in ("pass", None):
        raise ValueError("Only successful accepted results can be saved")
    checks = qa.get("checks")
    if isinstance(checks, dict) and checks.get("cell_count") not in (None, cell_count):
        raise ValueError("QA cell count does not match geometry")


def _validate_attributes(attributes, cell_ids):
    if attributes is None:
        return None
    supported_schemas = {"cell-attributes-reference-v0", "cell-attributes-indexed-v1"}
    if (not isinstance(attributes, dict)
            or attributes.get("schema_version") not in supported_schemas
            or not isinstance(attributes.get("cells"), list)):
        raise ValueError("Unsupported cell attributes schema")
    rows = attributes["cells"]
    ids = [row.get("cell_id") for row in rows if isinstance(row, dict)]
    if (len(ids) != len(rows) or any(not isinstance(value, str) for value in ids)
            or len(ids) != len(set(ids)) or set(ids) != cell_ids):
        raise ValueError("Attribute IDs must exactly match the project cells")
    for row in rows:
        _finite_tree(row, "Attributes")
        if "value" not in row:
            raise ValueError("Attribute record requires a value field")
        value = row["value"]
        if value is not None and type(value) not in (str, int, float):
            raise ValueError("Attribute value must be a scalar number, string, or null")
        for field in ("footprint_coverage", "valid_coverage"):
            value = row.get(field)
            if field not in row or type(value) not in (float, int) or not 0 <= value <= 1 + 1e-8:
                raise ValueError(f"Attribute {field} must be finite and in [0, 1]")
        if row["valid_coverage"] > row["footprint_coverage"] + 1e-8:
            raise ValueError("Attribute valid_coverage cannot exceed footprint_coverage")
    _finite_tree(attributes.get("provenance", {}), "Attribute provenance")
    _finite_tree(attributes.get("qa", {}), "Attribute QA")
    return attributes


def _clean_view(settings):
    if settings is None:
        return {}
    if not isinstance(settings, dict):
        raise ValueError("View settings must be an object")
    result = {key: value for key, value in settings.items() if key in _VIEW_KEYS}
    if len(result) > 32:
        raise ValueError("Project view settings exceed the supported key limit")
    for key in ("show_edges", "show_nodes"):
        if key in result and type(result[key]) is not bool:
            raise ValueError("View toggles must be boolean")
    if ("selected_cell_id" in result and result["selected_cell_id"] is not None
            and not isinstance(result["selected_cell_id"], str)):
        raise ValueError("Selected cell ID must be a string or null")
    if "result_tab" in result and result["result_tab"] not in {"mesh", "extract"}:
        raise ValueError("Unsupported result tab")
    if "view_box" in result and result["view_box"] is not None:
        parts = result["view_box"].split() if isinstance(result["view_box"], str) else []
        try:
            numbers = [float(part) for part in parts]
        except ValueError as exc:
            raise ValueError("View box must contain four finite numbers") from exc
        if len(numbers) != 4 or not all(math.isfinite(value) for value in numbers) or numbers[2] <= 0 or numbers[3] <= 0:
            raise ValueError("View box must contain finite xmin ymin width height")
        if any(abs(value) > 1e15 for value in numbers) or any(value > 1e12 for value in numbers[2:]):
            raise ValueError("View box exceeds supported coordinate bounds")
        if not math.isfinite(numbers[0] + numbers[2]) or not math.isfinite(numbers[1] + numbers[3]):
            raise ValueError("View box extent must remain finite")
    return result


def save_project(path, *, cells, qa, config=None, attributes=None,
                 provenance=None, view_settings=None):
    """Write one bounded `.mcy` ZIP snapshot; caller owns the destination path."""
    cells, parsed_cells, _ = _cells_payload(cells)
    if config is not None and not isinstance(config, dict):
        raise ValueError("Historical project config must be an object")
    _validate_qa(qa, len(parsed_cells))
    cell_ids = {cell.cell_id for cell in parsed_cells}
    attributes = _validate_attributes(attributes, cell_ids)
    safe_config, unavailable = _safe_config(config or {})
    safe_qa, found = _safe_config(qa)
    unavailable.extend(found)
    if attributes is not None:
        attributes = dict(attributes)
        for name in ("provenance", "qa"):
            if isinstance(attributes.get(name), dict):
                attributes[name], found = _safe_config(attributes[name])
                unavailable.extend(found)
        attributes = _validate_attributes(attributes, cell_ids)
    # Caller-supplied provenance is historical metadata and gets the same scrub.
    if provenance is not None:
        safe_provenance, extra = _safe_config(provenance)
        unavailable.extend(extra)
    else:
        safe_provenance = {}
    view_settings = _clean_view(view_settings)
    if (view_settings.get("selected_cell_id") is not None
            and view_settings["selected_cell_id"] not in cell_ids):
        view_settings["selected_cell_id"] = None
    payloads = {"cells.json": _json_bytes(cells), "qa.json": _json_bytes(safe_qa),
                "config.json": _json_bytes(safe_config)}
    if attributes is not None:
        payloads["attributes.json"] = _json_bytes(attributes)
    if provenance is not None:
        # Kept inside config metadata so the fixed archive member set stays small.
        safe_config["project_provenance"] = safe_provenance
        payloads["config.json"] = _json_bytes(safe_config)
    if len(payloads) + 1 > MAX_MEMBERS or sum(map(len, payloads.values())) > MAX_UNCOMPRESSED_BYTES:
        raise ValueError("Project contents exceed the archive size limit")
    manifest = {"schema_version": PROJECT_SCHEMA, "files": {
        name: {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        for name, data in sorted(payloads.items())},
        "unavailable_inputs": unavailable,
        "view_settings": view_settings}
    payloads["project.json"] = _json_bytes(manifest)
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED,
                         compresslevel=6, strict_timestamps=True) as output:
        for name, data in sorted(payloads.items()):
            info = zipfile.ZipInfo(name)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (stat.S_IFREG | 0o600) << 16
            output.writestr(info, data)
    with zipfile.ZipFile(io.BytesIO(archive.getvalue()), "r") as output:
        if any(info.file_size > max(1, info.compress_size) * MAX_COMPRESSION_RATIO
               for info in output.infolist()):
            raise ValueError("Project compression ratio exceeds the safety limit")
    encoded = archive.getvalue()
    if len(encoded) > MAX_ARCHIVE_BYTES:
        raise ValueError("Project archive exceeds 64 MiB")
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(encoded)
    return destination


def load_project(path):
    """Validate and reconstruct saved cells offline; never open listed inputs."""
    path = Path(path)
    if not path.is_file() or path.stat().st_size > MAX_ARCHIVE_BYTES:
        raise ValueError("Project archive is missing or exceeds 64 MiB")
    try:
        with zipfile.ZipFile(path, "r") as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if len(infos) > MAX_MEMBERS or len(names) != len(set(names)):
                raise ValueError("Project archive has duplicate or excess members")
            if any(name not in _MEMBERS for name in names) or "project.json" not in names:
                raise ValueError("Project archive contains unexpected paths or lacks manifest")
            if not {"cells.json", "qa.json", "config.json"}.issubset(names):
                raise ValueError("Project archive is missing required result files")
            if any(info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
                   for info in infos):
                raise ValueError("Unsupported project compression method")
            if any(info.is_dir() or (info.external_attr >> 16) & 0o170000 == stat.S_IFLNK
                   for info in infos):
                raise ValueError("Project archive members must be regular files")
            total = sum(info.file_size for info in infos)
            if total > MAX_UNCOMPRESSED_BYTES or any(info.file_size > MAX_JSON_BYTES for info in infos):
                raise ValueError("Project uncompressed size exceeds 128 MiB")
            if any(info.compress_size == 0 and info.file_size > 0 for info in infos):
                raise ValueError("Invalid compressed project member")
            if any(info.file_size > max(1, info.compress_size) * MAX_COMPRESSION_RATIO
                   for info in infos):
                raise ValueError("Project compression ratio exceeds the safety limit")
            raw = {info.filename: archive.read(info) for info in infos}
    except (zipfile.BadZipFile, OSError, RuntimeError) as exc:
        raise ValueError("Invalid or corrupt project archive") from exc
    manifest = _json_load(raw["project.json"])
    if (not isinstance(manifest, dict) or manifest.get("schema_version") != PROJECT_SCHEMA
            or set(manifest) != {"schema_version", "files", "unavailable_inputs", "view_settings"}):
        raise ValueError("Unsupported project manifest")
    expected_names = set(raw) - {"project.json"}
    if not isinstance(manifest["files"], dict) or set(manifest["files"]) != expected_names:
        raise ValueError("Project file inventory mismatch")
    for name, data in raw.items():
        if name == "project.json":
            continue
        record = manifest["files"][name]
        if (not isinstance(record, dict) or set(record) != {"size", "sha256"}
                or type(record["size"]) is not int or record["size"] != len(data)
                or not isinstance(record["sha256"], str)
                or record["sha256"] != hashlib.sha256(data).hexdigest()):
            raise ValueError(f"Project digest mismatch for {name}")
    cells, parsed_cells, crs = _cells_payload(_json_load(raw["cells.json"]))
    qa = _json_load(raw["qa.json"])
    _validate_qa(qa, len(parsed_cells))
    qa, qa_unavailable = _safe_config(qa)
    attributes = (_validate_attributes(_json_load(raw["attributes.json"]),
                                      {cell.cell_id for cell in parsed_cells})
                  if "attributes.json" in raw else None)
    attribute_unavailable = []
    if attributes is not None:
        attributes = dict(attributes)
        for section in ("provenance", "qa"):
            if isinstance(attributes.get(section), dict):
                attributes[section], found = _safe_config(attributes[section])
                attribute_unavailable.extend(found)
        attributes = _validate_attributes(attributes, {cell.cell_id for cell in parsed_cells})
    config, discovered_unavailable = _safe_config(_json_load(raw["config.json"]))
    if not isinstance(config, dict):
        raise ValueError("Historical project config must be an object")
    unavailable = manifest["unavailable_inputs"]
    if (not isinstance(unavailable, list) or len(unavailable) > 256 or any(
            not isinstance(item, dict) or set(item) != {"field", "label"}
            or not all(isinstance(item[key], str) for key in item)
            or len(item["field"]) > 256 or len(item["label"]) > 160
            or any(separator in item["field"] for separator in ("/", "\\", ":"))
            or any(separator in item["label"] for separator in ("/", "\\", ":"))
            or item["label"] in {".", ".."}
            for item in unavailable)):
        raise ValueError("Invalid unavailable input list")
    if discovered_unavailable:
        unavailable.extend(discovered_unavailable)
    unavailable.extend(qa_unavailable)
    unavailable.extend(attribute_unavailable)
    if len(unavailable) > 256:
        raise ValueError("Unavailable input list exceeds the safety limit")
    # Never present the original run's pass marker as current project validation.
    historic_qa = dict(qa)
    historic_qa["project_source"] = "saved historical QA; geometry and archive validated on open"
    if "status" in historic_qa:
        historic_qa["historical_status"] = historic_qa.pop("status")
    config.pop("project_provenance", None)
    view_settings = _clean_view(manifest["view_settings"])
    if (view_settings.get("selected_cell_id") is not None
            and view_settings["selected_cell_id"] not in {cell.cell_id for cell in parsed_cells}):
        view_settings["selected_cell_id"] = None
    return {"schema_version": PROJECT_SCHEMA,
            "cells": cells, "cell_objects": parsed_cells, "crs_wkt": crs.to_wkt(),
            "qa": historic_qa, "attributes": attributes, "config": config,
            "provenance": {"source": "project", "historical": True},
            "view_settings": view_settings,
            "unavailable_inputs": unavailable,
            "execution_mode": "offline_saved_result_no_implicit_rerun"}
