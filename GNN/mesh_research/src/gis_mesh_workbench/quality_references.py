"""Load only manifest-verified original ROI and Gmsh size references."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re

from shapely.geometry import Point, Polygon
from shapely.strtree import STRtree

from .job_control import checkpoint
from .workload_limits import MAX_MESH_CELLS, MAX_OVERLAP_CANDIDATES

_MAX_REFERENCE_BYTES = 64 * 1024 * 1024
_MAX_CONTROL_TRIANGLES = 100_000
_ST = re.compile(r"ST\(([^)]*)\)\s*\{([^}]*)\}\s*;")
_UNIFORM = re.compile(r'Field\[1\]\.F="([0-9.eE+-]+)";')


def _verified_file(root: Path, relative: str, manifest: dict) -> tuple[Path, str] | None:
    """Resolve a retained file only when its exact relative path is hashed."""
    if not isinstance(relative, str) or not relative or "\\" in relative:
        return None
    candidate_rel = Path(relative)
    if candidate_rel.is_absolute() or any(part in ("", ".", "..") for part in candidate_rel.parts):
        return None
    hashes = manifest.get("artifact_hashes")
    if not isinstance(hashes, dict):
        return None
    expected = hashes.get(candidate_rel.as_posix())
    if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", expected):
        return None
    candidate = root / candidate_rel
    path = candidate.resolve(strict=False)
    try:
        path.relative_to(root)
    except ValueError:
        return None
    if not path.is_file():
        return None
    try:
        before = path.stat()
        if before.st_size > _MAX_REFERENCE_BYTES:
            return None
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            opened = stream.fileno()
            opened_stat = os.fstat(opened)
            # Content digest is authoritative. Windows ctime can reflect delayed
            # metadata updates rather than content modification.
            if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
                    opened_stat.st_dev, opened_stat.st_ino, opened_stat.st_size,
                    opened_stat.st_mtime_ns):
                return None
            blocks = 0
            while block := stream.read(1024 * 1024):
                digest.update(block)
                blocks += 1
                if blocks % 8 == 0:
                    checkpoint()
        actual = digest.hexdigest()
        after = path.stat()
        if ((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
                or candidate.resolve(strict=True) != path):
            return None
    except OSError:
        return None
    return (path, actual) if actual.lower() == expected.lower() else None


def _read_json(path: Path):
    try:
        with path.open("r", encoding="utf-8") as stream:
            return json.load(stream)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None


def _cells_iter(cells):
    if isinstance(cells, dict):
        cells = cells.get("cells", [])
    if not isinstance(cells, (list, tuple)) or len(cells) > MAX_MESH_CELLS:
        return None
    return cells


def _centroid_samples(cells):
    rows = _cells_iter(cells)
    if rows is None:
        return None
    samples = []
    for index, cell in enumerate(rows):
        if index % 128 == 0:
            checkpoint()
        try:
            cid = str(cell.cell_id if hasattr(cell, "cell_id") else cell["cell_id"])
            xy = cell.xy if hasattr(cell, "xy") else cell["xy"]
            if len(xy) != 3:
                continue
            points = [(float(p[0]), float(p[1])) for p in xy]
            if not all(math.isfinite(v) for p in points for v in p):
                continue
            centroid = Polygon(points).centroid
            samples.append((cid, centroid.x, centroid.y))
        except (KeyError, TypeError, ValueError, IndexError, AttributeError):
            continue
    return samples


def _barycentric(point, triangle, values):
    a, b, c = triangle
    den = (b[1]-c[1])*(a[0]-c[0]) + (c[0]-b[0])*(a[1]-c[1])
    if not math.isfinite(den) or abs(den) < 1e-15:
        return None
    u = ((b[1]-c[1])*(point[0]-c[0]) + (c[0]-b[0])*(point[1]-c[1]))/den
    v = ((c[1]-a[1])*(point[0]-c[0]) + (a[0]-c[0])*(point[1]-c[1]))/den
    w = 1-u-v
    if min(u, v, w) < -1e-10:
        return None
    result = u*values[0]+v*values[1]+w*values[2]
    return float(result) if math.isfinite(result) and result > 0 else None


def _relief_triangles(path: Path):
    points, values = [], []
    try:
        with path.open("r", encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, 1):
                if line_number % 512 == 0:
                    checkpoint()
                match = _ST.search(line)
                if not match:
                    continue
                if len(points) >= _MAX_CONTROL_TRIANGLES:
                    return None
                coords = [float(v) for v in match.group(1).split(",")]
                sizes = [float(v) for v in match.group(2).split(",")]
                if len(coords) != 9 or len(sizes) != 3:
                    return None
                tri = tuple((coords[i], coords[i+1]) for i in (0, 3, 6))
                if not all(math.isfinite(v) for p in tri for v in p) or not all(math.isfinite(v) and v > 0 for v in sizes):
                    return None
                points.append(tri); values.append(tuple(sizes))
                if len(points) % 512 == 0:
                    checkpoint()
    except (OSError, UnicodeError, ValueError):
        return None
    if not points:
        return None
    return points, values


def _sample_indexed(samples, triangles, values):
    checkpoint()
    geometries = []
    for index, triangle in enumerate(triangles):
        if index % 512 == 0:
            checkpoint()
        geometries.append(Polygon(triangle))
    tree = STRtree(geometries)
    checkpoint()
    targets = {}
    candidate_count = 0
    for sample_index, (cell_id, x, y) in enumerate(samples):
        if sample_index % 128 == 0:
            checkpoint()
        point = Point(x, y)
        candidates = tree.query(point, predicate="intersects")
        candidate_count += len(candidates)
        if candidate_count > MAX_OVERLAP_CANDIDATES:
            return None
        for index in candidates:
            index = int(index)
            value = _barycentric((x, y), triangles[index], values[index])
            if value is not None:
                targets[cell_id] = value
                break
    return targets


def load_references(parent_output, cells, manifest):
    """Return `(roi, size_field)` from bounded, hash-verified retained evidence.

    Missing, unsafe, oversized, malformed, or hash-mismatched evidence is
    represented as `None`; actual mesh edges are never used to invent targets.
    """
    try:
        root = Path(parent_output).resolve(strict=True)
    except (OSError, TypeError, ValueError):
        return None, None
    if (not root.is_dir() or not isinstance(manifest, dict)
            or manifest.get("status") != "pass"):
        return None, None
    roi = None
    verified = _verified_file(root, "meshing/domain-boundary.json", manifest)
    if verified:
        payload = _read_json(verified[0])
        if (_verified_file(root, "meshing/domain-boundary.json", manifest) == verified
                and isinstance(payload, dict) and isinstance(payload.get("geometry"), dict)
                and isinstance(payload.get("crs_wkt"), str)):
            roi = {"geometry": payload["geometry"], "crs_wkt": payload["crs_wkt"]}

    size_field = None
    samples = _centroid_samples(cells)
    if samples is None:
        return roi, None
    # Gmsh-generated metadata is retained in the run manifest; the sidecar
    # must independently match its artifact hash before it can be sampled.
    generator = manifest.get("gmsh_generated")
    evidence = generator.get("size_field") if isinstance(generator, dict) else None
    if not isinstance(evidence, dict):
        return _final_roi(root, manifest, verified, roi), None
    crs_wkt = (manifest.get("effective_contract", {}).get("analysis_crs_wkt")
               if isinstance(manifest.get("effective_contract"), dict) else None)
    if not isinstance(crs_wkt, str):
        crs_wkt = manifest.get("crs")
    if not isinstance(crs_wkt, str):
        return _final_roi(root, manifest, verified, roi), None
    if evidence.get("method") == "uniform":
        geo_file = _verified_file(root, "meshing/domain.geo", manifest)
        target = None
        if geo_file:
            try:
                text = geo_file[0].read_text(encoding="utf-8")
                match = _UNIFORM.search(text)
                target = float(match.group(1)) if match else None
            except (OSError, UnicodeError, ValueError):
                target = None
        if (geo_file and _verified_file(root, "meshing/domain.geo", manifest) == geo_file
                and target is not None and math.isfinite(target) and target > 0):
            size_field = {"definition": "gmsh_characteristic_length", "crs_wkt": crs_wkt,
                          "uniform": float(target), "source_sha256": geo_file[1]}
        else:
            size_field = None
        return _final_roi(root, manifest, verified, roi), size_field

    pos_file = _verified_file(root, "meshing/size.pos", manifest)
    target_sizes = None
    source_hash = None
    if pos_file:
        parsed = _relief_triangles(pos_file[0])
        if parsed:
            target_sizes = _sample_indexed(samples, *parsed)
            source_hash = pos_file[1]
            if _verified_file(root, "meshing/size.pos", manifest) != pos_file:
                target_sizes = None
    # SF-1's retained NPZ contains grid samples, but its exact Gmsh
    # interpolation triangles are not proven here. Do not infer them.
    if target_sizes:
        size_field = {"definition": "gmsh_characteristic_length", "crs_wkt": crs_wkt,
                      "target_sizes": target_sizes, "source_sha256": source_hash}
    return _final_roi(root, manifest, verified, roi), size_field


def _final_roi(root, manifest, verified, roi):
    """Discard parsed ROI if its retained artifact changed during the load."""
    if verified is None or roi is None:
        return roi
    return roi if _verified_file(root, "meshing/domain-boundary.json", manifest) == verified else None
