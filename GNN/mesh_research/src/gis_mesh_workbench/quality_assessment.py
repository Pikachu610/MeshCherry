"""Bounded, generator-independent quality assessment for 2D mesh snapshots.

The service deliberately reports unavailable GIS and size checks as such. It
does not relabel CRS, mutate connectivity, or weaken the older mesh workflow's
``_cells`` acceptance gate.
"""
from __future__ import annotations

from collections import defaultdict, deque
import hashlib
import json
import math
import time

import numpy as np
from shapely.geometry import LineString, Point, Polygon
from shapely.strtree import STRtree

from .job_control import Cancelled, checkpoint, observe
from .workload_limits import MAX_OVERLAP_CANDIDATES, MAX_MESH_CELLS, MAX_MESH_NODES

SCHEMA_VERSION = "meshcherry-quality-v1"
METRIC_DEFINITION_VERSION = "meshcherry-triangle-metrics-v1"
_DEFAULTS = {
    "relative_geometry_epsilon": 1e-12,
    "overlap_relative_tolerance": 1e-10,
    "max_overlap_candidates": MAX_OVERLAP_CANDIDATES,
    "max_issues": 10000,
    "worst_limit": 100,
    "warning_min_angle_deg": 20.0,
    "warning_quality_q": 0.2,
    "warning_aspect_ratio": 10.0,
}
_TRIANGLE_METRICS = (
    "area", "perimeter", "min_angle_deg", "max_angle_deg", "aspect_ratio",
    "quality_q", "longest_edge", "shortest_edge", "signed_area",
)


def _finite_number(value):
    return not isinstance(value, (bool, np.bool_)) and isinstance(value, (int, float, np.number)) and math.isfinite(float(value))


def _json_safe(value):
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if value is None or isinstance(value, (str, int, bool)):
        return value
    return str(value)


def _hash_json(value):
    payload = json.dumps(_hash_safe(value), sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _hash_safe(value):
    """Canonicalize invalid floats distinctly for mesh identity fingerprints."""
    if isinstance(value, dict):
        return {str(k): _hash_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_hash_safe(v) for v in value]
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return {"__nonfinite_float__": "nan" if math.isnan(value) else "positive_infinity" if value > 0 else "negative_infinity"}
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def quality_mesh_hash(mesh):
    """Return the SHA-256 fingerprint used by :func:`evaluate_mesh_quality`."""
    # Match accepted .mcy normalization without inventing connectivity or IDs.
    if isinstance(mesh, dict) and mesh.get('schema_version') == 'workbench-cells-v1':
        try:
            from .spatial import metric_crs
            mesh = dict(mesh, crs_wkt=metric_crs(mesh['crs_wkt']).to_wkt(),
                cells=[dict(row, xy=[[float(v) if _finite_number(v) else v for v in point]
                                    for point in row['xy']]) for row in mesh['cells']])
        except (KeyError, TypeError, ValueError):
            pass  # Malformed inputs still receive an exact diagnostic fingerprint.
    return _hash_json(mesh)


def quality_config_hash(config=None):
    """Return the evaluator's normalized config and metric-version hash."""
    return _hash_json({"metric_definition_version": METRIC_DEFINITION_VERSION,
                       **_config(config)})


def _id(row, *keys):
    for key in keys:
        if key in row:
            return row[key]
    return None


def _config(config):
    if config is not None and (not isinstance(config, dict) or set(config)-set(_DEFAULTS)):
        raise ValueError('Unsupported quality settings')
    supplied = dict(config or {})
    result = dict(_DEFAULTS)
    result.update(supplied)
    for name in ("relative_geometry_epsilon", "overlap_relative_tolerance",
                 "warning_min_angle_deg", "warning_quality_q", "warning_aspect_ratio"):
        value = result[name]
        if not _finite_number(value) or float(value) < 0:
            raise ValueError(f"config.{name} must be finite and non-negative")
        result[name] = float(value)
    if result["relative_geometry_epsilon"] >= 1e-2:
        raise ValueError("config.relative_geometry_epsilon must be below 1e-2")
    if result["overlap_relative_tolerance"] >= 1:
        raise ValueError("config.overlap_relative_tolerance must be below 1")
    for name, maximum in (("max_overlap_candidates", MAX_OVERLAP_CANDIDATES),
                          ("max_issues", 100000), ("worst_limit", 10000)):
        value = result[name]
        if type(value) is not int or not 1 <= value <= maximum:
            raise ValueError(f"config.{name} must be an integer in [1, {maximum}]")
    if result["warning_min_angle_deg"] > 60:
        raise ValueError("config.warning_min_angle_deg must be <= 60")
    if result["warning_quality_q"] > 1:
        raise ValueError("config.warning_quality_q must be <= 1")
    return result


def _parse(mesh, add_issue):
    """Normalize supported cell payloads while retaining source IDs."""
    if not isinstance(mesh, dict):
        add_issue("INPUT_INVALID", "error", "Mesh must be a mapping.", category="STRUCTURE")
        return [], [], False
    explicit = isinstance(mesh.get("nodes"), list) and isinstance(mesh.get("elements"), list)
    nodes, node_lookup, elements = [], {}, []
    if explicit:
        for i, row in enumerate(mesh["nodes"]):
            checkpoint()
            if not isinstance(row, dict):
                add_issue("NODE_RECORD_INVALID", "error", "Node record must be a mapping.", node_ids=[i], category="STRUCTURE")
                continue
            nid = _id(row, "node_id", "id")
            xy = row.get("xy", row.get("coordinates"))
            if nid is None:
                add_issue("NODE_ID_MISSING", "error", "Node identifier is missing.", node_ids=[i], category="STRUCTURE")
            elif not isinstance(nid, (str, int, float)) or isinstance(nid, float) and not math.isfinite(nid):
                add_issue("NODE_ID_INVALID", "error", "Node identifiers must be finite JSON scalar values.", node_ids=[i], category="STRUCTURE")
            elif nid in node_lookup:
                add_issue("DUPLICATE_NODE_ID", "error", "Node identifier is duplicated.", node_ids=[nid], category="STRUCTURE")
            else:
                node_lookup[nid] = xy
                nodes.append((nid, xy))
        for i, row in enumerate(mesh["elements"]):
            if not isinstance(row, dict):
                add_issue("ELEMENT_RECORD_INVALID", "error", "Element record must be a mapping.", element_ids=[i], category="STRUCTURE")
                continue
            eid = _id(row, "element_id", "cell_id", "id")
            if eid is not None and (not isinstance(eid, (str, int, float)) or isinstance(eid, float) and not math.isfinite(eid)):
                add_issue("ELEMENT_ID_INVALID", "error", "Element identifiers must be finite JSON scalar values.", element_ids=[i], category="STRUCTURE")
                eid = i
            elements.append({"id": eid,
                             "kind": row.get("kind", row.get("type", "triangle")),
                             "connectivity": row.get("node_ids", row.get("connectivity")),
                             "xy": None, "source_index": i})
    else:
        rows = mesh.get("cells")
        if not isinstance(rows, list):
            add_issue("CELLS_MISSING", "error", "Mesh requires a cells list or explicit nodes/elements.", category="STRUCTURE")
            return [], [], False
        for i, row in enumerate(rows):
            if not isinstance(row, dict):
                add_issue("ELEMENT_RECORD_INVALID", "error", "Cell record must be a mapping.", element_ids=[i], category="STRUCTURE")
                continue
            eid = _id(row, "element_id", "cell_id", "id")
            if eid is not None and (not isinstance(eid, (str, int, float)) or isinstance(eid, float) and not math.isfinite(eid)):
                add_issue("ELEMENT_ID_INVALID", "error", "Element identifiers must be finite JSON scalar values.", element_ids=[i], category="STRUCTURE")
                eid = i
            elements.append({"id": eid,
                             "kind": row.get("kind", row.get("type")), "connectivity": None,
                             "xy": row.get("xy", row.get("coordinates")), "source_index": i})
    if not elements:
        add_issue("EMPTY_MESH", "error", "Mesh has no readable elements.", category="STRUCTURE")
    return elements, nodes, explicit


def _xy_points(raw, element, node_lookup, add_issue):
    eid, kind = element["id"], element["kind"]
    supported = {"triangle": 3, "tri": 3, "rectangle": 4, "quadrilateral": 4, "quad": 4}
    expected = supported.get(str(kind).lower())
    if expected is None:
        add_issue("ELEMENT_KIND_UNSUPPORTED", "error", "Unsupported element type.", element_ids=[eid], category="STRUCTURE", details={"kind": kind})
        return None, None, None
    conn = element["connectivity"]
    node_ids = None
    if conn is not None:
        if not isinstance(conn, (list, tuple)) or len(conn) != expected:
            add_issue("CONNECTIVITY_LENGTH_INVALID", "error", "Connectivity length does not match element type.", element_ids=[eid], category="STRUCTURE")
            return None, None, None
        node_ids = list(conn)
        if any(isinstance(nid, bool) or not isinstance(nid,(str,int,float)) or
               isinstance(nid,float) and not math.isfinite(nid) for nid in node_ids):
            add_issue('NODE_ID_INVALID','error','Connectivity identifiers must be finite scalar IDs.',
                      element_ids=[eid],category='STRUCTURE')
            return None,None,None
        if len(set(map(_id_key, node_ids))) != len(node_ids):
            add_issue("REPEATED_NODE", "error", "Element repeats a node identifier.", element_ids=[eid], node_ids=node_ids, category="STRUCTURE")
            return None, None, node_ids
        missing = [nid for nid in node_ids if nid not in node_lookup]
        if missing:
            add_issue("MISSING_NODE_REFERENCE", "error", "Element references missing node identifiers.", element_ids=[eid], node_ids=missing, category="STRUCTURE")
            return None, None, node_ids
        raw = [node_lookup[nid] for nid in node_ids]
    if not isinstance(raw, (list, tuple)) or len(raw) != expected:
        add_issue("CONNECTIVITY_LENGTH_INVALID", "error", "Coordinate count does not match element type.", element_ids=[eid], category="STRUCTURE")
        return None, None, node_ids
    try:
        points = np.asarray(raw, dtype=np.float64)
    except (TypeError, ValueError, OverflowError):
        points = np.empty((0, 0))
    if points.shape != (expected, 2):
        add_issue("COORDINATE_SHAPE_INVALID", "error", "Each vertex must have exactly two coordinates.", element_ids=[eid], category="STRUCTURE")
        return None, None, node_ids
    if not np.isfinite(points).all():
        add_issue("NONFINITE_COORDINATE", "error", "Coordinates contain NaN or infinity.", element_ids=[eid], node_ids=node_ids, category="STRUCTURE")
        return points, None, node_ids
    if node_ids is None:
        node_ids = [_coord_key(p) for p in points]
    if len(set(map(_id_key, node_ids))) != len(node_ids):
        add_issue("REPEATED_NODE", "error", "Element repeats a vertex.", element_ids=[eid], node_ids=node_ids, category="STRUCTURE")
        return points, None, node_ids
    return points, expected, node_ids


def _id_key(value):
    try:
        return (type(value).__name__, json.dumps(_json_safe(value), sort_keys=True, allow_nan=False))
    except (TypeError, ValueError):
        return (type(value).__name__, repr(value))


def _coord_key(point):
    x, y = float(point[0]), float(point[1])
    return (0.0 if x == 0 else x, 0.0 if y == 0 else y)


def _triangle_metrics(points, epsilon):
    # Translate to a local origin and normalize by the longest edge before
    # products, preventing large projected eastings from polluting precision.
    p = points - points[0]
    edge_vectors = np.roll(p, -1, axis=0) - p
    lengths_raw = np.hypot(edge_vectors[:, 0], edge_vectors[:, 1])
    scale = float(np.max(lengths_raw))
    if not math.isfinite(scale) or scale <= 0:
        return None, "DEGENERATE_TRIANGLE"
    q = p / scale
    u, v = q[1], q[2]
    cross = float(u[0] * v[1] - u[1] * v[0])
    if not math.isfinite(cross) or abs(cross) <= epsilon:
        return None, "DEGENERATE_TRIANGLE"
    lengths_n = np.hypot(*(np.roll(q, -1, axis=0) - q).T)
    if np.any(lengths_n <= 0):
        return None, "DEGENERATE_TRIANGLE"
    # atan2 is stable for both acute and nearly obtuse angles.
    angles = []
    for i in range(3):
        a = q[(i + 1) % 3] - q[i]
        b = q[(i + 2) % 3] - q[i]
        cr = abs(float(a[0] * b[1] - a[1] * b[0]))
        dot = float(a[0] * b[0] + a[1] * b[1])
        angles.append(math.degrees(math.atan2(cr, dot)))
    area = (0.5 * abs(cross)) * scale * scale
    lengths = lengths_n * scale
    perimeter = float(np.sum(lengths))
    ar = 1.0 / abs(cross)
    q_quality = 4.0 * cross * cross / (float(np.sum(lengths_n)) * float(np.prod(lengths_n)))
    if 1.0 < q_quality <= 1.0 + 64 * np.finfo(float).eps:
        q_quality = 1.0
    vals = (area, perimeter, min(angles), max(angles), ar, q_quality,
            float(np.max(lengths)), float(np.min(lengths)), 0.5 * cross * scale * scale)
    if not all(math.isfinite(x) for x in vals):
        return None, "METRIC_OVERFLOW"
    # Clamp only tiny roundoff beyond the mathematical upper bound of one.
    if 1.0 < q_quality <= 1.0 + 64 * np.finfo(float).eps:
        q_quality = 1.0
    return dict(zip(_TRIANGLE_METRICS, vals)), None


def triangle_metrics(points, relative_geometry_epsilon=1e-12):
    """Compute the shared triangle shape metrics, or ``None`` if undefined.

    Coordinates are translated to a local origin and scale-normalized before
    cross/dot products. ``aspect_ratio`` is Lmax squared over twice the area;
    ``quality_q`` is 16 A squared over perimeter times the product of edges.
    """
    if not _finite_number(relative_geometry_epsilon) or not 0 <= float(relative_geometry_epsilon) < 1e-2:
        raise ValueError("relative_geometry_epsilon must be finite and in [0, 1e-2)")
    try:
        array = np.asarray(points, dtype=np.float64)
    except (TypeError, ValueError, OverflowError):
        return None
    if array.shape != (3, 2) or not np.isfinite(array).all():
        return None
    result, error = _triangle_metrics(array, float(relative_geometry_epsilon))
    return result if error is None else None


def _stats(values, excluded=0):
    arr = np.asarray([float(x) for x in values if _finite_number(x)], dtype=np.float64)
    if not len(arr):
        return dict(status="NOT_AVAILABLE", reason="no valid observations", valid_count=0,
                    excluded_count=excluded)
    p = np.percentile(arr, [5, 50, 95, 99])
    return dict(status="AVAILABLE", valid_count=int(len(arr)), excluded_count=int(excluded),
                min=float(np.min(arr)), p5=float(p[0]), median=float(p[1]), mean=float(np.sum(arr/len(arr))),
                p95=float(p[2]), p99=float(p[3]), max=float(np.max(arr)))


def _histogram(values, edges, excluded):
    arr = np.asarray([float(x) for x in values if _finite_number(x)], dtype=np.float64)
    counts, _ = np.histogram(arr, bins=edges)
    return dict(status="AVAILABLE" if len(arr) else "NOT_AVAILABLE", edges=list(edges),
                counts=[int(x) for x in counts], valid_count=int(len(arr)), excluded_count=int(excluded))


def evaluate_mesh_quality(mesh, roi=None, size_field=None, config=None):
    """Evaluate a cell-coordinate mesh or explicit node/connectivity snapshot.

    Optional ``roi`` and ``size_field`` are placeholders for the workbench
    integration layer; absent inputs are explicitly NOT_AVAILABLE. ``checkpoint``
    honors the existing job-scoped cancellation token during bounded loops.
    """
    started = time.perf_counter()
    cfg = _config(config)
    if isinstance(mesh, dict):
        for key, maximum in (('cells', MAX_MESH_CELLS), ('elements', MAX_MESH_CELLS), ('nodes', MAX_MESH_NODES)):
            if isinstance(mesh.get(key), list) and len(mesh[key]) > maximum:
                raise ValueError(f'Quality {key} exceeds supported limit {maximum}')
    issues = []
    capped = False
    observed_codes = set()
    shape_error = False
    invalid_issue_ids = set()

    def add_issue(code, severity, message, *, category, element_ids=None,
                  node_ids=None, details=None, location=None):
        nonlocal capped, shape_error
        observed_codes.add(code)
        shape_error |= category == 'SHAPE' and severity == 'error'
        if severity == 'error':
            invalid_issue_ids.update(_id_key(eid) for eid in (element_ids or []))
        if len(issues) >= cfg["max_issues"]:
            capped = True
            return
        item = dict(code=code, severity=severity, category=category, message=message,
                    element_ids=element_ids or [], node_ids=node_ids or [], details=details or {})
        if location is not None:
            item["location"] = location
        issues.append(item)

    mesh_hash = quality_mesh_hash(mesh)
    config_hash = quality_config_hash(cfg)
    try:
        observe("qa_input")
        elements, nodes, explicit = _parse(mesh, add_issue)
        if not isinstance(mesh, dict):
            # _parse already emitted the useful top-level diagnostic.
            elements = []
        node_lookup = {nid: xy for nid, xy in nodes}
        id_keys = defaultdict(list)
        valid_ids = set()
        records, polygons = [], []
        used_nodes = set()
        canonical_elements = {}
        invalid_count = 0
        duplicate_element_ids = set()
        for row in elements:
            checkpoint()
            eid = row["id"]
            if eid is None:
                add_issue("ELEMENT_ID_MISSING", "error", "Element identifier is missing.", element_ids=[row["source_index"]], category="STRUCTURE")
                eid = row["source_index"]
                row["id"] = eid
            key = _id_key(eid)
            id_keys[key].append(eid)
            if len(id_keys[key]) > 1 and key not in duplicate_element_ids:
                duplicate_element_ids.add(key)
                add_issue("DUPLICATE_ELEMENT_ID", "error", "Element identifier is duplicated.", element_ids=id_keys[key], category="STRUCTURE")
            points, expected, node_ids = _xy_points(row["xy"], row, node_lookup, add_issue)
            row["node_ids"] = node_ids or []
            if node_ids:
                used_nodes.update(map(_id_key, node_ids))
            metric = None
            geometry_error = None
            kind = str(row["kind"]).lower() if row["kind"] is not None else ""
            if points is not None and expected is not None and np.isfinite(points).all():
                if expected == 3:
                    metric, geometry_error = _triangle_metrics(points, cfg["relative_geometry_epsilon"])
                    if geometry_error == "DEGENERATE_TRIANGLE":
                        add_issue(geometry_error, "error", "Triangle area is zero or below the scale-relative tolerance.", element_ids=[eid], node_ids=node_ids, category="SHAPE")
                    elif geometry_error:
                        add_issue(geometry_error, "error", "Triangle metric overflowed floating-point range.", element_ids=[eid], node_ids=node_ids, category="SHAPE")
                else:
                    try:
                        poly = Polygon(points)
                        if not poly.is_valid or not _finite_number(poly.area) or poly.area <= 0:
                            geometry_error = "INVALID_ELEMENT_GEOMETRY"
                        else:
                            metric = {"area": float(poly.area), "perimeter": float(poly.length),
                                      "longest_edge": float(max(math.dist(points[i], points[(i+1)%4]) for i in range(4))),
                                      "shortest_edge": float(min(math.dist(points[i], points[(i+1)%4]) for i in range(4)))}
                    except Exception:
                        geometry_error = "INVALID_ELEMENT_GEOMETRY"
                    if geometry_error:
                        add_issue(geometry_error, "error", "Quadrilateral is not a valid positive-area polygon.", element_ids=[eid], node_ids=node_ids, category="SHAPE")
            if points is None or expected is None or metric is None:
                invalid_count += 1
            canonical = tuple(sorted((_id_key(n) for n in node_ids))) if node_ids else None
            if points is not None and np.isfinite(points).all():
                geometric_key = tuple(sorted(_coord_key(point) for point in points))
                prior = canonical_elements.get(("geometry", geometric_key))
                if prior is not None:
                    add_issue("DUPLICATE_ELEMENT", "error", "Element duplicates another element by vertex coordinates, independent of vertex order.", element_ids=[prior, eid], node_ids=node_ids, category="STRUCTURE")
                else:
                    canonical_elements[("geometry", geometric_key)] = eid
            if canonical is not None:
                canonical_key = ("nodes", canonical)
                if canonical_key not in canonical_elements:
                    canonical_elements[canonical_key] = eid
            cell = dict(element_id=eid, kind=row["kind"], valid=metric is not None,
                        validity_status="VALID" if metric is not None else "INVALID",
                        issue_codes=[], node_ids=node_ids or [])
            for name in ("area", "perimeter", "min_angle_deg", "max_angle_deg", "aspect_ratio",
                         "quality_q", "longest_edge", "shortest_edge", "signed_area"):
                cell[name] = metric.get(name) if metric else None
            if str(row["kind"]).lower() not in ("triangle", "tri"):
                for name in ("min_angle_deg", "max_angle_deg", "aspect_ratio", "quality_q", "signed_area"):
                    cell[name] = None
                cell["triangle_metric_status"] = "NOT_APPLICABLE"
            else:
                cell["triangle_metric_status"] = "AVAILABLE" if metric else "NOT_AVAILABLE"
            records.append(cell)
            if points is not None and expected is not None and metric is not None:
                poly = Polygon(points)
                polygons.append((len(records)-1, poly, node_ids, points))
                valid_ids.add(key)
        if explicit:
            orphan = [nid for nid, _ in nodes if _id_key(nid) not in used_nodes]
            for nid in orphan:
                add_issue("ORPHAN_NODE", "warning", "Node is not referenced by any element.", node_ids=[nid], category="STRUCTURE")
            orphan_status = "WARNING" if orphan else "PASS"
        else:
            orphan = None
            orphan_status = "NOT_AVAILABLE"

        # Topology uses source node IDs where present. Coordinate payloads use
        # exact canonical coordinate keys and explicitly mark orphan checks N/A.
        incidence = defaultdict(list)
        adjacency = defaultdict(set)
        for rec_idx, poly, node_ids, points in polygons:
            checkpoint()
            if len(node_ids) != len(points):
                continue
            for a, b in zip(node_ids, node_ids[1:] + node_ids[:1]):
                edge = tuple(sorted((_id_key(a), _id_key(b))))
                incidence[edge].append(rec_idx)
        nonmanifold = []
        boundary_edges = interior_edges = 0
        for edge, owners in incidence.items():
            if len(owners) == 1:
                boundary_edges += 1
            elif len(owners) == 2:
                interior_edges += 1
                if owners[0] != owners[1]:
                    adjacency[owners[0]].add(owners[1]); adjacency[owners[1]].add(owners[0])
            else:
                nonmanifold.append((edge, owners))
                ids = [records[i]["element_id"] for i in owners]
                add_issue("NONMANIFOLD_EDGE", "error", "Edge is incident to more than two elements.", element_ids=ids, category="TOPOLOGY", details={"incidence_count": len(owners)})
        # A vertex on the interior of another element's edge is a hanging-node
        # T-junction. Use an STRtree over unique mesh vertices so this remains
        # spatially indexed; candidate work has a hard limit.
        vertex_points, vertex_owner, vertex_keys = [], [], []
        vertex_seen = set()
        for rec_idx, _, node_ids, points in polygons:
            for vertex, nid in zip(points, node_ids):
                key = _coord_key(vertex)
                if key not in vertex_seen:
                    vertex_seen.add(key)
                    vertex_points.append(Point(key))
                    vertex_owner.append((rec_idx, nid))
                    vertex_keys.append(key)
        tjunctions = set()
        if vertex_points:
            vertex_tree = STRtree(vertex_points)
            edge_candidates = 0
            for rec_idx, _, node_ids, points in polygons:
                for a, b, na, nb in zip(points, np.roll(points, -1, axis=0),
                                        node_ids, node_ids[1:] + node_ids[:1]):
                    line = LineString([a, b])
                    for point_index in vertex_tree.query(line, predicate="intersects"):
                        point_index = int(point_index)
                        point_key = vertex_keys[point_index]
                        if point_key in (_coord_key(a), _coord_key(b)):
                            continue
                        edge_candidates += 1
                        if edge_candidates > cfg["max_overlap_candidates"]:
                            add_issue("TOPOLOGY_CANDIDATE_BUDGET_EXCEEDED", "error", "Indexed T-junction candidate budget was exceeded; topology assessment is incomplete.", category="TOPOLOGY", details={"candidate_count": edge_candidates, "candidate_limit": cfg["max_overlap_candidates"]})
                            break
                        point_rec, point_node = vertex_owner[point_index]
                        if point_rec == rec_idx or line.distance(vertex_points[point_index]) != 0:
                            continue
                        pair = (rec_idx, point_rec, _coord_key(point_key))
                        if pair in tjunctions:
                            continue
                        tjunctions.add(pair)
                        add_issue("T_JUNCTION", "error", "A mesh vertex lies in the interior of another element edge.", element_ids=[records[rec_idx]["element_id"], records[point_rec]["element_id"]], node_ids=[na, nb, point_node], category="TOPOLOGY", details={"edge_node_ids": [na, nb], "hanging_node_id": point_node})
                    if edge_candidates > cfg["max_overlap_candidates"]:
                        break
                if edge_candidates > cfg["max_overlap_candidates"]:
                    break
        components, seen = [], set()
        for i in range(len(records)):
            if not records[i]["valid"] or i in seen:
                continue
            queue = [i]; seen.add(i); component = []
            while queue:
                checkpoint()
                current = queue.pop(); component.append(current)
                for neighbor in adjacency[current]:
                    if neighbor not in seen:
                        seen.add(neighbor); queue.append(neighbor)
            components.append(component)
        if len(components) > 1:
            add_issue("DISCONNECTED_COMPONENTS", "warning", "Mesh contains disconnected components; this may be valid for a multi-part domain.", element_ids=[records[i]["element_id"] for c in components for i in c], category="TOPOLOGY", details={"component_count": len(components)})

        # Ask the established validator to confirm the valid polygon subset.
        # Its failure never suppresses our richer diagnostics; its acceptance
        # gate itself remains unchanged and is reused as a consistency check.
        legacy_acceptance = "NOT_AVAILABLE"
        try:
            from .extraction import Cell, _cells
            legacy_cells = [Cell(('qa-'+str(i)) if explicit else records[i]["element_id"],
                                 "triangle" if len(points)==3 else 'rectangle',
                                 tuple(tuple(float(v) for v in p) for p in points))
                            for i, _, _, points in polygons]
            if legacy_cells:
                _cells(legacy_cells)
                legacy_acceptance = "PASS"
        except Cancelled:
            raise
        except Exception as error:
            legacy_acceptance = "FAIL"
            add_issue("LEGACY_CELL_VALIDATOR_REJECTED", "error", "Existing cell acceptance validator rejected the geometry.", category="TOPOLOGY", details={"reason": str(error)[:300]})

        # STRtree candidate pairs provide an indexed, bounded overlap check.
        overlap_pairs = 0
        overlap_status = "PASS"
        if polygons:
            geoms = [entry[1] for entry in polygons]
            tree = STRtree(geoms)
            try:
                for i, first in enumerate(geoms):
                    if i % 128 == 0:
                        observe("qa_overlap", i, len(geoms))
                    for j in tree.query(first, predicate="intersects"):
                        j = int(j)
                        if j <= i:
                            continue
                        overlap_pairs += 1
                        if overlap_pairs > cfg["max_overlap_candidates"]:
                            raise _CandidateBudgetExceeded
                        second = geoms[j]
                        area = float(first.intersection(second).area)
                        threshold = cfg["overlap_relative_tolerance"] * min(first.area, second.area)
                        if area > threshold:
                            overlap_status = "FAIL"
                            idx_a, idx_b = polygons[i][0], polygons[j][0]
                            add_issue("MESH_OVERLAP", "error", "Two elements overlap in positive area.", element_ids=[records[idx_a]["element_id"], records[idx_b]["element_id"]], category="OVERLAP", details={"intersection_area": area, "area_tolerance": threshold}, location={"type": "Point", "coordinates": [float(first.intersection(second).centroid.x), float(first.intersection(second).centroid.y)]})
            except _CandidateBudgetExceeded:
                overlap_status = "ERROR"
                add_issue("OVERLAP_CANDIDATE_BUDGET_EXCEEDED", "error", "Indexed overlap candidate budget was exceeded; overlap assessment is incomplete.", category="OVERLAP", details={"candidate_count": overlap_pairs, "candidate_limit": cfg["max_overlap_candidates"]})

        # Attach stable issue codes back to rows.
        id_to_record = defaultdict(list)
        for rec in records:
            id_to_record[_id_key(rec["element_id"])].append(rec)
        for issue in issues:
            for eid in issue.get("element_ids", []):
                for rec in id_to_record.get(_id_key(eid), []):
                    if issue["code"] not in rec["issue_codes"]:
                        rec["issue_codes"].append(issue["code"])
                    if issue["severity"] == "error":
                        rec["valid"] = False
                        rec["validity_status"] = "INVALID"

        structural_codes = {"INPUT_INVALID", "CELLS_MISSING", "EMPTY_MESH", "ELEMENT_RECORD_INVALID", "NODE_RECORD_INVALID", "NODE_ID_MISSING", "NODE_ID_INVALID", "DUPLICATE_NODE_ID", "ELEMENT_ID_MISSING", "ELEMENT_ID_INVALID", "DUPLICATE_ELEMENT_ID", "CONNECTIVITY_LENGTH_INVALID", "COORDINATE_SHAPE_INVALID", "NONFINITE_COORDINATE", "MISSING_NODE_REFERENCE", "REPEATED_NODE", "ELEMENT_KIND_UNSUPPORTED", "DUPLICATE_ELEMENT"}
        for rec in records:
            if _id_key(rec['element_id']) in invalid_issue_ids:
                rec.update(valid=False, validity_status='INVALID')
        structural = "FAIL" if observed_codes.intersection(structural_codes) or shape_error else "PASS"
        topology = "FAIL" if nonmanifold or tjunctions or 'TOPOLOGY_CANDIDATE_BUDGET_EXCEEDED' in observed_codes or legacy_acceptance == 'FAIL' else "PASS"
        if len(components) > 1 and topology == 'PASS':
            topology = "WARNING"

        triangle_rows = [r for r in records if r["kind"] in ("triangle", "tri")]
        metric_sources = {name: (records if name in {'area','perimeter','longest_edge','shortest_edge'} else triangle_rows)
                          for name in _TRIANGLE_METRICS if name!='signed_area'}
        shape_values = {name: [r[name] for r in metric_sources[name] if r["valid"] and _finite_number(r[name])]
                        for name in _TRIANGLE_METRICS if name != "signed_area"}
        excluded_triangles = sum(1 for r in triangle_rows if not r["valid"])
        statistics = {}
        warning_shape = False
        for rec in triangle_rows:
            if not rec["valid"]:
                continue
            flagged = False
            if rec["min_angle_deg"] < cfg["warning_min_angle_deg"]:
                rec["issue_codes"].append("LOW_MIN_ANGLE"); flagged = True
            if rec["quality_q"] < cfg["warning_quality_q"]:
                rec["issue_codes"].append("LOW_QUALITY_Q"); flagged = True
            if rec["aspect_ratio"] > cfg["warning_aspect_ratio"]:
                rec["issue_codes"].append("HIGH_ASPECT_RATIO"); flagged = True
            if flagged:
                warning_shape = True
                add_issue("SHAPE_WARNING", "warning", "Element exceeds one or more configurable shape warning thresholds.", element_ids=[rec["element_id"]], category="SHAPE", details={"min_angle_deg": rec["min_angle_deg"], "quality_q": rec["quality_q"], "aspect_ratio": rec["aspect_ratio"]})
        for name, values in shape_values.items():
            statistics[name] = _stats(values, len(metric_sources[name])-len(values))

        worst = {
            "lowest_quality_q": sorted((r for r in triangle_rows if r["valid"]), key=lambda x: (x["quality_q"], _id_key(x["element_id"])))[:cfg["worst_limit"]],
            "lowest_min_angle": sorted((r for r in triangle_rows if r["valid"]), key=lambda x: (x["min_angle_deg"], _id_key(x["element_id"])))[:cfg["worst_limit"]],
            "highest_aspect_ratio": sorted((r for r in triangle_rows if r["valid"]), key=lambda x: (-x["aspect_ratio"], _id_key(x["element_id"])))[:cfg["worst_limit"]],
        }
        histograms = {
            "min_angle_deg": _histogram(shape_values["min_angle_deg"], [0, 10, 20, 30, 40, 50, 60], excluded_triangles),
            "aspect_ratio": _histogram(shape_values["aspect_ratio"], [1, 1.5, 2, 3, 5, 10, 20, 50, math.inf], excluded_triangles),
            "quality_q": _histogram(shape_values["quality_q"], [0, .2, .4, .6, .8, 1.000000000000001], excluded_triangles),
            "area": _histogram([r["area"] for r in records if r["valid"]], [0, .01, .1, 1, 10, 100, math.inf], sum(not r['valid'] for r in records)),
        }
        status = "FAIL" if structural == "FAIL" or topology == "FAIL" or overlap_status in ("FAIL", "ERROR") else "WARNING" if warning_shape or topology == "WARNING" or orphan_status == "WARNING" else "PARTIAL"
        elapsed = time.perf_counter() - started
        validation = {
            "structurally_valid": {"status": structural, "invalid_element_count": sum(not r["valid"] for r in records), "orphan_node_count": len(orphan) if orphan is not None else None, "orphan_nodes": orphan_status},
            "topologically_conforming": {"status": topology, "boundary_edge_count": boundary_edges, "interior_edge_count": interior_edges, "nonmanifold_edge_count": len(nonmanifold), "connected_component_count": len(components), "component_policy": "multiple components are reported as a warning because multipart domains may be valid"},
            "non_overlapping": {"status": overlap_status, "indexed_candidate_pair_count": overlap_pairs, "candidate_limit": cfg["max_overlap_candidates"]},
            "matches_input_domain": {"status": "NOT_AVAILABLE" if roi is None else "NOT_AVAILABLE", "reason": "boundary assessment is enriched by the workbench integration layer"},
        }
        derived_vertices = len({_coord_key(point) for _,_,_,points in polygons for point in points})
        return _json_safe({
            "schema_version": SCHEMA_VERSION,
            "mesh_hash": mesh_hash,
            "config_hash": config_hash,
            "metric_definition_version": METRIC_DEFINITION_VERSION,
            "status": status,
            "validation": validation,
            "summary": {"element_count": len(elements), "valid_element_count": sum(bool(r["valid"]) for r in records), "invalid_element_count": sum(not r["valid"] for r in records), "triangle_count": len(triangle_rows), "triangle_metric_valid_count": sum(bool(r["valid"]) for r in triangle_rows), "triangle_orientation_counts": {"CCW": sum(1 for r in triangle_rows if r["valid"] and r["signed_area"] > 0), "CW": sum(1 for r in triangle_rows if r["valid"] and r["signed_area"] < 0)}, "node_count": len(nodes) if explicit else None, "orphan_node_count": len(orphan) if orphan is not None else None, "boundary_edge_count": boundary_edges, "interior_edge_count": interior_edges, "nonmanifold_edge_count": len(nonmanifold), "connected_component_count": len(components), "legacy_cell_acceptance": legacy_acceptance, "issues_truncated": capped},
            "elements": records,
            "topology_provenance": dict(identity='original_node_ids' if explicit else 'coordinate_derived',
                derived_vertex_count=derived_vertices,
                original_node_count=len(nodes) if explicit else None,
                graph_node_convention='not inferred; graph nodes may represent cells'),
            "issues": issues,
            "definitions": {
                "area": "absolute signed triangle area; coordinate_units squared",
                "perimeter": "sum of triangle edge lengths; coordinate_units",
                "min_angle_deg": "minimum of atan2(abs(cross), dot) vertex angles, degrees",
                "max_angle_deg": "maximum of atan2(abs(cross), dot) vertex angles, degrees",
                "aspect_ratio": "Lmax^2/(2*A) = Lmax/minimum altitude; unitless",
                "quality_q": "16*A^2/((a+b+c)*a*b*c) = 2r/R; unitless, equilateral=1",
                "signed_area": "0.5*cross2(B-A,C-A); positive CCW, negative CW",
                "relative_geometry_epsilon": "1e-12 default; reject if |cross|/Lmax^2 <= epsilon; scale-relative, unitless",
                "overlap_relative_tolerance": "1e-10 default times smaller element area",
                "statistical_policy": "Only valid triangle observations enter triangle statistics; excluded_count counts invalid triangle rows.",
                "coordinate_units": mesh.get("coordinate_units") if isinstance(mesh, dict) else None,
            },
            "histograms": histograms,
            "statistics": statistics,
            "threshold_fractions": {"min_angle_below_deg": {str(t): (sum(x < t for x in shape_values["min_angle_deg"]) / len(shape_values["min_angle_deg"]) if shape_values["min_angle_deg"] else None) for t in (10, 20, 30)}, "quality_q_below_warning_threshold": (sum(x < cfg["warning_quality_q"] for x in shape_values["quality_q"]) / len(shape_values["quality_q"]) if shape_values["quality_q"] else None)},
            "worst": worst,
            "boundary": {"status": "NOT_AVAILABLE", "reason": "Boundary assessment is delegated to the workbench integration layer; ROI was supplied but is not evaluated by this core." if roi is not None else "No reference ROI was supplied."},
            "size": {"status": "NOT_AVAILABLE", "reason": "Size assessment is delegated to the workbench integration layer; size_field was supplied but is not evaluated by this core." if size_field is not None else "No target size field was supplied."},
            "performance": {"status": "AVAILABLE", "qa_time_seconds": elapsed,
                            "generation_time_seconds": None, "pipeline_time_seconds": None,
                            "peak_memory_bytes": None, "peak_memory_status": "NOT_AVAILABLE",
                            "memory_method": "not measured by this core"},
        })
    except Cancelled:
        return _json_safe({"schema_version": SCHEMA_VERSION, "mesh_hash": mesh_hash,
                           "config_hash": config_hash, "metric_definition_version": METRIC_DEFINITION_VERSION,
                           "status": "CANCELLED", "validation": {}, "summary": {"issues_truncated": capped},
                           "elements": [], "issues": issues, "definitions": {}, "histograms": {},
                           "statistics": {}, "worst": {},
                           "boundary": {"status": "NOT_AVAILABLE", "reason": "cancelled"},
                           "size": {"status": "NOT_AVAILABLE", "reason": "cancelled"},
                           "performance": {"status": "CANCELLED", "qa_time_seconds": time.perf_counter()-started,
                                           "generation_time_seconds": None, "pipeline_time_seconds": None,
                                           "peak_memory_bytes": None, "peak_memory_status": "NOT_AVAILABLE"}})


class _CandidateBudgetExceeded(Exception):
    pass
