from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import struct
from typing import Any


class BoundaryError(ValueError):
    pass


_CANONICAL_SUFFIXES = (".shp", ".shx", ".dbf", ".prj", ".cpg", ".sbn", ".sbx")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def inventory_components(shp_path: Path) -> list[dict[str, str]]:
    if shp_path.suffix.lower() != ".shp" or not shp_path.is_file():
        raise BoundaryError(f"canonical Polygon SHP not found: {shp_path}")
    components = []
    for suffix in _CANONICAL_SUFFIXES:
        path = shp_path.with_suffix(suffix)
        if path.is_file():
            components.append(
                {
                    "suffix": suffix,
                    "logical_path": str(path),
                    "sha256": _sha256(path),
                }
            )
    required = {".shp", ".shx", ".dbf", ".prj"}
    present = {item["suffix"] for item in components}
    if not required.issubset(present):
        missing = ", ".join(sorted(required - present))
        raise BoundaryError(f"canonical shapefile is missing required components: {missing}")
    return components


def component_set_sha256(components: list[dict[str, str]]) -> str:
    identity = "\n".join(
        f"{item['suffix']}:{item['sha256']}" for item in sorted(components, key=lambda item: item["suffix"])
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def verify_component_hashes(components: list[dict[str, str]]) -> tuple[bool, list[str]]:
    changed = []
    for item in components:
        path = Path(item["logical_path"])
        if not path.is_file() or _sha256(path) != item["sha256"]:
            changed.append(item["logical_path"])
    return not changed, changed


def read_single_polygon_shp(path: Path) -> list[tuple[float, float]]:
    data = path.read_bytes()
    if len(data) < 100 or struct.unpack_from(">i", data, 0)[0] != 9994:
        raise BoundaryError("invalid SHP header")
    if struct.unpack_from("<i", data, 32)[0] != 5:
        raise BoundaryError("Ticket 05 requires a Polygon (shape type 5) SHP")

    records: list[list[tuple[float, float]]] = []
    offset = 100
    while offset < len(data):
        if offset + 8 > len(data):
            raise BoundaryError("truncated SHP record header")
        content_words = struct.unpack_from(">i", data, offset + 4)[0]
        start = offset + 8
        end = start + 2 * content_words
        if end > len(data):
            raise BoundaryError("truncated SHP record")
        shape_type = struct.unpack_from("<i", data, start)[0]
        if shape_type == 0:
            offset = end
            continue
        if shape_type != 5:
            raise BoundaryError(f"unsupported record shape type: {shape_type}")
        num_parts, num_points = struct.unpack_from("<ii", data, start + 36)
        if num_parts != 1:
            raise BoundaryError(
                f"Ticket 05 requires one exterior ring; found {num_parts} parts"
            )
        parts_offset = start + 44
        points_offset = parts_offset + 4 * num_parts
        if points_offset + 16 * num_points > end:
            raise BoundaryError("truncated Polygon coordinate array")
        if struct.unpack_from("<i", data, parts_offset)[0] != 0:
            raise BoundaryError("unexpected first Polygon part index")
        points = [
            struct.unpack_from("<dd", data, points_offset + 16 * index)
            for index in range(num_points)
        ]
        records.append(points)
        offset = end

    if len(records) != 1:
        raise BoundaryError(f"Ticket 05 requires exactly one Polygon feature; found {len(records)}")
    ring = records[0]
    if len(ring) < 4 or ring[0] != ring[-1]:
        raise BoundaryError("Polygon exterior ring is not closed")
    open_ring = ring[:-1]
    if len(set(open_ring)) < 3 or abs(ring_area(open_ring)) <= 1e-9:
        raise BoundaryError("canonical Polygon ring has insufficient geometry")
    return open_ring


def _point_segment_distance(
    point: tuple[float, float], start: tuple[float, float], end: tuple[float, float]
) -> float:
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    denominator = dx * dx + dy * dy
    if denominator == 0:
        return math.hypot(point[0] - start[0], point[1] - start[1])
    fraction = max(
        0.0,
        min(1.0, ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / denominator),
    )
    return math.hypot(point[0] - (start[0] + fraction * dx), point[1] - (start[1] + fraction * dy))


def _rdp(points: list[tuple[float, float]], tolerance: float) -> list[tuple[float, float]]:
    if len(points) <= 2:
        return points[:]
    distances = [
        _point_segment_distance(point, points[0], points[-1])
        for point in points[1:-1]
    ]
    maximum = max(distances, default=-1.0)
    if maximum <= tolerance:
        return [points[0], points[-1]]
    split = distances.index(maximum) + 1
    return _rdp(points[: split + 1], tolerance)[:-1] + _rdp(points[split:], tolerance)


def simplify_closed_ring(
    ring: list[tuple[float, float]], tolerance: float
) -> list[tuple[float, float]]:
    if tolerance < 0:
        raise BoundaryError("simplification tolerance must be non-negative")
    anchor = min(range(len(ring)), key=lambda index: ring[index])
    opposite = max(
        range(len(ring)),
        key=lambda index: (
            (ring[index][0] - ring[anchor][0]) ** 2
            + (ring[index][1] - ring[anchor][1]) ** 2,
            -index,
        ),
    )
    ordered = ring[anchor:] + ring[:anchor]
    split = (opposite - anchor) % len(ring)
    first_arc = ordered[: split + 1]
    second_arc = ordered[split:] + [ordered[0]]
    simplified = _rdp(first_arc, tolerance)[:-1] + _rdp(second_arc, tolerance)[:-1]
    result = []
    for point in simplified:
        if not result or point != result[-1]:
            result.append(point)
    return result


def ring_area(ring: list[tuple[float, float]]) -> float:
    return 0.5 * sum(
        ring[index][0] * ring[(index + 1) % len(ring)][1]
        - ring[(index + 1) % len(ring)][0] * ring[index][1]
        for index in range(len(ring))
    )


def _orientation(a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _segments_intersect(
    a: tuple[float, float], b: tuple[float, float], c: tuple[float, float], d: tuple[float, float]
) -> bool:
    scale = max(1.0, *(abs(value) for point in (a, b, c, d) for value in point))
    epsilon = 1e-12 * scale * scale
    def on_segment(start: tuple[float, float], end: tuple[float, float], point: tuple[float, float]) -> bool:
        return (
            min(start[0], end[0]) - epsilon <= point[0] <= max(start[0], end[0]) + epsilon
            and min(start[1], end[1]) - epsilon <= point[1] <= max(start[1], end[1]) + epsilon
        )

    o1, o2, o3, o4 = (_orientation(a, b, c), _orientation(a, b, d), _orientation(c, d, a), _orientation(c, d, b))
    if ((o1 > epsilon and o2 < -epsilon) or (o1 < -epsilon and o2 > epsilon)) and ((o3 > epsilon and o4 < -epsilon) or (o3 < -epsilon and o4 > epsilon)):
        return True
    return (
        (abs(o1) <= epsilon and on_segment(a, b, c))
        or (abs(o2) <= epsilon and on_segment(a, b, d))
        or (abs(o3) <= epsilon and on_segment(c, d, a))
        or (abs(o4) <= epsilon and on_segment(c, d, b))
    )


def ring_topology(ring: list[tuple[float, float]]) -> tuple[bool, str]:
    if len(ring) < 3 or len(set(ring)) < 3:
        return False, "fewer than three unique vertices"
    if len(set(ring)) != len(ring):
        return False, "non-adjacent repeated vertex"
    if abs(ring_area(ring)) <= 1e-9:
        return False, "zero area"
    count = len(ring)
    segments = []
    for index in range(count):
        a, b = ring[index], ring[(index + 1) % count]
        if a == b:
            return False, f"zero-length segment at {index}"
        segments.append((min(a[0], b[0]), max(a[0], b[0]), min(a[1], b[1]), max(a[1], b[1]), index, a, b))
    segments.sort(key=lambda item: (item[0], item[1], item[4]))
    for index in range(count):
        previous = ring[index - 1]
        current = ring[index]
        following = ring[(index + 1) % count]
        if abs(_orientation(previous, current, following)) <= 1e-9:
            if (
                _point_segment_distance(following, previous, current) <= 1e-9
                or _point_segment_distance(previous, current, following) <= 1e-9
            ):
                return False, f"adjacent collinear overlap at vertex {index}"
    for position, first_segment in enumerate(segments):
        _, first_max_x, first_min_y, first_max_y, first, a, b = first_segment
        for second_segment in segments[position + 1 :]:
            second_min_x, _, second_min_y, second_max_y, second, c, d = second_segment
            if second_min_x > first_max_x:
                break
            if second_max_y < first_min_y or second_min_y > first_max_y:
                continue
            if second == first or second == (first + 1) % count or first == (second + 1) % count:
                continue
            if _segments_intersect(a, b, c, d):
                return False, f"self-intersection between segments {first} and {second}"
    return True, "valid single closed ring"


class _SegmentGrid:
    def __init__(self, ring: list[tuple[float, float]], cell_size: float) -> None:
        self.cell_size = cell_size
        self.segments = [(ring[index], ring[(index + 1) % len(ring)]) for index in range(len(ring))]
        self.cells: dict[tuple[int, int], set[int]] = {}
        for segment_index, (start, end) in enumerate(self.segments):
            min_x = math.floor(min(start[0], end[0]) / cell_size)
            max_x = math.floor(max(start[0], end[0]) / cell_size)
            min_y = math.floor(min(start[1], end[1]) / cell_size)
            max_y = math.floor(max(start[1], end[1]) / cell_size)
            for x in range(min_x, max_x + 1):
                for y in range(min_y, max_y + 1):
                    self.cells.setdefault((x, y), set()).add(segment_index)
        xs = [cell[0] for cell in self.cells]
        ys = [cell[1] for cell in self.cells]
        self.extent = (min(xs), max(xs), min(ys), max(ys))

    def distance(self, point: tuple[float, float]) -> float:
        cell_x = math.floor(point[0] / self.cell_size)
        cell_y = math.floor(point[1] / self.cell_size)
        min_x, max_x, min_y, max_y = self.extent
        maximum_radius = max(abs(cell_x - min_x), abs(cell_x - max_x), abs(cell_y - min_y), abs(cell_y - max_y)) + 1
        visited: set[int] = set()
        best = math.inf
        for radius in range(maximum_radius + 1):
            for x in range(cell_x - radius, cell_x + radius + 1):
                for y in range(cell_y - radius, cell_y + radius + 1):
                    if radius and x not in {cell_x - radius, cell_x + radius} and y not in {cell_y - radius, cell_y + radius}:
                        continue
                    for segment_index in self.cells.get((x, y), ()):
                        if segment_index in visited:
                            continue
                        visited.add(segment_index)
                        start, end = self.segments[segment_index]
                        best = min(best, _point_segment_distance(point, start, end))
            searched_min_x = (cell_x - radius) * self.cell_size
            searched_max_x = (cell_x + radius + 1) * self.cell_size
            searched_min_y = (cell_y - radius) * self.cell_size
            searched_max_y = (cell_y + radius + 1) * self.cell_size
            distance_to_outside = min(
                point[0] - searched_min_x,
                searched_max_x - point[0],
                point[1] - searched_min_y,
                searched_max_y - point[1],
            )
            if best <= distance_to_outside:
                return best
        return best


def _densified_points(
    ring: list[tuple[float, float]], sampling_interval: float
) -> list[tuple[float, float]]:
    points = []
    for index, start in enumerate(ring):
        end = ring[(index + 1) % len(ring)]
        length = math.hypot(end[0] - start[0], end[1] - start[1])
        subdivisions = max(1, math.ceil(length / sampling_interval))
        points.extend(
            (
                start[0] + (end[0] - start[0]) * step / subdivisions,
                start[1] + (end[1] - start[1]) * step / subdivisions,
            )
            for step in range(subdivisions)
        )
    return points


def densified_symmetric_hausdorff(
    first: list[tuple[float, float]],
    second: list[tuple[float, float]],
    sampling_interval: float,
) -> dict[str, float | str]:
    if sampling_interval <= 0:
        raise BoundaryError("Hausdorff sampling interval must be positive")
    cell_size = max(4 * sampling_interval, 50.0)
    first_index = _SegmentGrid(first, cell_size)
    second_index = _SegmentGrid(second, cell_size)
    first_to_second = max(second_index.distance(point) for point in _densified_points(first, sampling_interval))
    second_to_first = max(first_index.distance(point) for point in _densified_points(second, sampling_interval))
    estimate = max(first_to_second, second_to_first)
    error_bound = sampling_interval / 2
    return {
        "method": "symmetric_densified_point_to_polyline",
        "sampling_interval": sampling_interval,
        "additive_error_bound": error_bound,
        "first_to_second": first_to_second,
        "second_to_first": second_to_first,
        "estimate": estimate,
        "upper_bound": estimate + error_bound,
    }


def derive_boundary(
    domain: dict[str, Any], config_path: Path, boundary_directory: Path
) -> dict[str, Any]:
    source_path = Path(domain["boundary_source"])
    if not source_path.is_absolute():
        source_path = (config_path.parent / source_path).resolve()
    components = inventory_components(source_path)
    source_set_hash = component_set_sha256(components)
    source_ring = read_single_polygon_shp(source_path)
    source_area = abs(ring_area(source_ring))
    simplification = domain["simplification"]
    method = simplification["method"]
    if method != "rdp_closed_ring":
        raise BoundaryError(f"unsupported simplification method: {method}")
    tolerances = [float(value) for value in simplification["tolerances"]]
    if not tolerances or len(tolerances) != len(set(tolerances)):
        raise BoundaryError("simplification tolerances must be a non-empty unique list")
    max_relative_area = float(simplification["max_relative_area_difference"])
    max_hausdorff = float(simplification["max_hausdorff_distance"])
    sampling_interval = float(simplification["hausdorff_sampling_interval"])

    candidates = []
    candidate_rings: dict[float, list[tuple[float, float]]] = {}
    for tolerance in tolerances:
        candidate = simplify_closed_ring(source_ring, tolerance)
        topology_valid, topology_result = ring_topology(candidate)
        candidate_area = abs(ring_area(candidate)) if len(candidate) >= 3 else 0.0
        area_difference = abs(candidate_area - source_area)
        relative_area_difference = area_difference / source_area
        hausdorff = densified_symmetric_hausdorff(source_ring, candidate, sampling_interval)
        hausdorff_distance = float(hausdorff["estimate"])
        eligible = topology_valid and relative_area_difference <= max_relative_area and float(hausdorff["upper_bound"]) <= max_hausdorff
        row = {
            "method": method,
            "tolerance": tolerance,
            "source_set_sha256": source_set_hash,
            "source_vertex_count": len(source_ring),
            "vertex_count": len(candidate),
            "topology_valid": topology_valid,
            "topology_result": topology_result,
            "source_area": source_area,
            "candidate_area": candidate_area,
            "absolute_area_difference": area_difference,
            "relative_area_difference": relative_area_difference,
            "hausdorff_distance": hausdorff_distance,
            "hausdorff_method": hausdorff["method"],
            "hausdorff_sampling_interval": hausdorff["sampling_interval"],
            "hausdorff_additive_error_bound": hausdorff["additive_error_bound"],
            "hausdorff_source_to_candidate": hausdorff["first_to_second"],
            "hausdorff_candidate_to_source": hausdorff["second_to_first"],
            "hausdorff_distance_upper_bound": hausdorff["upper_bound"],
            "eligible": eligible,
        }
        candidates.append(row)
        candidate_rings[tolerance] = candidate

    eligible_candidates = [item for item in candidates if item["eligible"]]
    selection_rule = (
        "largest configured tolerance among topology-valid candidates satisfying "
        "maximum relative-area and Hausdorff-distance gates"
    )
    source_evidence = {
        "schema_version": "canonical-boundary-source-v0",
        "source_path": str(source_path),
        "source_set_sha256": source_set_hash,
        "components": components,
        "geometry": {"shape_type": "Polygon", "feature_count": 1, "part_count": 1, "vertex_count": len(source_ring), "area": source_area},
    }
    sweep_evidence = {
        "schema_version": "boundary-simplification-sweep-v0",
        "method": method,
        "selection_rule": selection_rule,
        "eligibility_gates": {"max_relative_area_difference": max_relative_area, "max_hausdorff_distance_upper_bound": max_hausdorff},
        "candidates": candidates,
    }
    _write_json(boundary_directory / "source.json", source_evidence)
    _write_json(boundary_directory / "sweep.json", sweep_evidence)
    if not eligible_candidates:
        _write_json(boundary_directory / "failure.json", {"error": "no simplification candidate satisfies the configured gates", "selection_rule": selection_rule})
        raise BoundaryError("no simplification candidate satisfies the configured gates")
    selected = max(eligible_candidates, key=lambda item: item["tolerance"])
    selected_ring = candidate_rings[selected["tolerance"]]
    selected_evidence = {
        "schema_version": "derived-boundary-v0",
        "selection_rule": selection_rule,
        "selected_candidate": selected,
        "closed_ring": True,
        "coordinates": [[x, y] for x, y in selected_ring],
        "physical_interpretation": {"watershed_divide_status": "UNVERIFIED", "outlet_status": "UNVERIFIED"},
    }
    _write_json(boundary_directory / "selected-boundary.json", selected_evidence)
    return {"ring": selected_ring, "source": source_evidence, "sweep": sweep_evidence, "selected": selected_evidence}
