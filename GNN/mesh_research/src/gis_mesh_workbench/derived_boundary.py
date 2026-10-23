"""Opt-in, validated simplification for derived meshing boundaries.

The input geometry is never modified. Callers remain responsible for providing
coordinates in a projected metre CRS; the explicit ``units`` argument records
that contract in the returned evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math

import numpy as np
from shapely import linestrings, normalize, points, to_wkb
from shapely.geometry import MultiPolygon, Polygon
from shapely.strtree import STRtree

from .job_control import checkpoint


MAX_DENSIFIED_SAMPLES = 500_000
DISTANCE_BATCH_SIZE = 8_192
MAX_RDP_DISTANCE_CHECKS = 50_000_000


@dataclass(frozen=True)
class DerivedBoundary:
    """A newly derived geometry and its reproducible validation evidence."""

    geometry: Polygon | MultiPolygon
    metadata: dict[str, object]


def _polygons(geometry: Polygon | MultiPolygon) -> list[Polygon]:
    if isinstance(geometry, Polygon):
        return [geometry]
    if isinstance(geometry, MultiPolygon):
        return list(geometry.geoms)
    raise ValueError("boundary must be a Polygon or MultiPolygon")


def _geometry_hash(geometry: Polygon | MultiPolygon) -> str:
    canonical = normalize(geometry)
    return hashlib.sha256(to_wkb(canonical, byte_order=1, output_dimension=2)).hexdigest()


def _vertex_count(geometry: Polygon | MultiPolygon) -> int:
    return sum(
        len(polygon.exterior.coords) - 1
        + sum(len(ring.coords) - 1 for ring in polygon.interiors)
        for polygon in _polygons(geometry)
    )


def _point_segment_distance(point: tuple[float, float], start: tuple[float, float], end: tuple[float, float]) -> float:
    dx, dy = end[0] - start[0], end[1] - start[1]
    denominator = dx * dx + dy * dy
    if denominator == 0:
        return math.hypot(point[0] - start[0], point[1] - start[1])
    fraction = max(0.0, min(1.0, ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / denominator))
    return math.hypot(point[0] - start[0] - fraction * dx, point[1] - start[1] - fraction * dy)


def _rdp(points_: list[tuple[float, float]], tolerance: float, work_budget: list[int]) -> list[tuple[float, float]]:
    """Iterative Douglas-Peucker with cooperative cancellation and work cap."""
    if len(points_) <= 2:
        return points_[:]
    keep = {0, len(points_) - 1}
    pending = [(0, len(points_) - 1)]
    while pending:
        checkpoint()
        start_index, end_index = pending.pop()
        start, end = points_[start_index], points_[end_index]
        maximum, split = -1.0, -1
        for index in range(start_index + 1, end_index):
            work_budget[0] -= 1
            if work_budget[0] < 0:
                raise ValueError(
                    "Ranh giới dẫn xuất bị từ chối: RDP vượt ngân sách tính toán "
                    f"{MAX_RDP_DISTANCE_CHECKS:,} phép đo khoảng cách."
                )
            if work_budget[0] % DISTANCE_BATCH_SIZE == 0:
                checkpoint()
            distance = _point_segment_distance(points_[index], start, end)
            if distance > maximum:
                maximum, split = distance, index
        if maximum > tolerance:
            keep.add(split)
            pending.append((split, end_index))
            pending.append((start_index, split))
    return [points_[index] for index in sorted(keep)]


def _simplify_closed_ring(ring, tolerance: float, work_budget: list[int] | None = None) -> list[tuple[float, float]]:
    """Port of mesh_research.boundary.simplify_closed_ring (anchor/farthest split)."""
    points_ = [(float(coordinate[0]), float(coordinate[1])) for coordinate in list(ring.coords)[:-1]]
    anchor = min(range(len(points_)), key=lambda index: points_[index])
    opposite = max(
        range(len(points_)),
        key=lambda index: ((points_[index][0] - points_[anchor][0]) ** 2 + (points_[index][1] - points_[anchor][1]) ** 2, -index),
    )
    ordered = points_[anchor:] + points_[:anchor]
    split = (opposite - anchor) % len(points_)
    if work_budget is None:
        work_budget = [MAX_RDP_DISTANCE_CHECKS]
    first_arc = ordered[: split + 1]
    second_arc = ordered[split:] + [ordered[0]]
    simplified = _rdp(first_arc, tolerance, work_budget)[:-1] + _rdp(second_arc, tolerance, work_budget)[:-1]
    result: list[tuple[float, float]] = []
    for point in simplified:
        if not result or point != result[-1]:
            result.append(point)
    return result


def _simplify_ring_polygon(polygon: Polygon, tolerance: float, work_budget: list[int]) -> Polygon:
    shell = _simplify_closed_ring(polygon.exterior, tolerance, work_budget)
    holes = [_simplify_closed_ring(ring, tolerance, work_budget) for ring in polygon.interiors]
    return Polygon(shell, holes)


def _densified_ring(ring, interval: float):
    coordinates = list(ring.coords)
    for start, end in zip(coordinates, coordinates[1:]):
        length = math.dist(start, end)
        steps = max(1, math.ceil(length / interval))
        for step in range(steps):
            fraction = step / steps
            yield (start[0] + (end[0] - start[0]) * fraction,
                   start[1] + (end[1] - start[1]) * fraction)


def _sample_count(geometry: Polygon | MultiPolygon, interval: float) -> int:
    count = 0
    for polygon in _polygons(geometry):
        for ring in (polygon.exterior, *polygon.interiors):
            coordinates = list(ring.coords)
            for start, end in zip(coordinates, coordinates[1:]):
                ratio = math.dist(start, end) / interval
                if not math.isfinite(ratio) or ratio > MAX_DENSIFIED_SAMPLES:
                    raise ValueError(
                        "Ranh giới dẫn xuất bị từ chối: mật độ lấy mẫu yêu cầu vượt "
                        f"ngân sách {MAX_DENSIFIED_SAMPLES:,} điểm."
                    )
                count += max(1, math.ceil(ratio))
                if count > MAX_DENSIFIED_SAMPLES:
                    raise ValueError(
                        "Ranh giới dẫn xuất bị từ chối: mật độ lấy mẫu yêu cầu vượt "
                        f"ngân sách {MAX_DENSIFIED_SAMPLES:,} điểm."
                    )
    return count


def _boundary_segment_tree(geometry: Polygon | MultiPolygon) -> STRtree:
    coordinate_pairs = []
    for polygon in _polygons(geometry):
        for ring in (polygon.exterior, *polygon.interiors):
            coordinates = list(ring.coords)
            coordinate_pairs.extend(zip(coordinates, coordinates[1:]))
    if not coordinate_pairs:
        raise ValueError("Ranh giới dẫn xuất không có đoạn biên để đối chiếu khoảng cách.")
    segments = linestrings(np.asarray(coordinate_pairs, dtype=np.float64))
    return STRtree(segments)


def _sampled_max_distance(geometry, target_tree: STRtree, interval: float, total: int) -> float:
    chunk: list[tuple[float, float]] = []
    maximum = 0.0
    completed = 0
    for polygon in _polygons(geometry):
        for ring in (polygon.exterior, *polygon.interiors):
            for coordinate in _densified_ring(ring, interval):
                chunk.append(coordinate)
                if len(chunk) == DISTANCE_BATCH_SIZE:
                    checkpoint()
                    coordinates = np.asarray(chunk, dtype=np.float64)
                    _, distances = target_tree.query_nearest(
                        points(coordinates[:, 0], coordinates[:, 1]),
                        all_matches=False,
                        return_distance=True,
                    )
                    maximum = max(maximum, float(np.max(distances)))
                    completed += len(chunk)
                    chunk.clear()
    if chunk:
        checkpoint()
        coordinates = np.asarray(chunk, dtype=np.float64)
        _, distances = target_tree.query_nearest(
            points(coordinates[:, 0], coordinates[:, 1]),
            all_matches=False,
            return_distance=True,
        )
        maximum = max(maximum, float(np.max(distances)))
        completed += len(chunk)
    checkpoint()
    if completed != total:
        raise ValueError("Ranh giới dẫn xuất: số mẫu hình học không nhất quán")
    return maximum


def _densified_symmetric_distance(source, candidate, interval: float) -> dict[str, float | str]:
    """Sample both polygon boundaries and return a conservative distance bound.

    Distance to a fixed closed set is 1-Lipschitz. Every point on a segment is
    at most ``interval / 2`` from a sample, so adding that amount to the maximum
    sampled distance bounds the continuous symmetric boundary distance.
    """
    source_tree = _boundary_segment_tree(source)
    candidate_tree = _boundary_segment_tree(candidate)
    source_count = _sample_count(source, interval)
    candidate_count = _sample_count(candidate, interval)
    total = source_count + candidate_count
    if total > MAX_DENSIFIED_SAMPLES:
        raise ValueError(
            "Ranh giới dẫn xuất bị từ chối: kiểm tra khoảng cách cần "
            f"{total:,} mẫu, vượt giới hạn {MAX_DENSIFIED_SAMPLES:,}; "
            "hãy dùng khoảng cách lấy mẫu lớn hơn hoặc ngưỡng sai lệch phù hợp."
        )
    source_to_candidate = _sampled_max_distance(source, candidate_tree, interval, source_count)
    candidate_to_source = _sampled_max_distance(candidate, source_tree, interval, candidate_count)
    estimate = max(source_to_candidate, candidate_to_source)
    error_bound = interval / 2
    return {
        "method": "symmetric_densified_point_to_indexed_segments",
        "sampling_interval_m": interval,
        "additive_error_bound_m": error_bound,
        "source_to_derived_m": source_to_candidate,
        "derived_to_source_m": candidate_to_source,
        "estimate_m": estimate,
        "upper_bound_m": estimate + error_bound,
    }


def simplify_derived_boundary(
    source: Polygon | MultiPolygon,
    *,
    tolerance_m: float,
    max_area_change_fraction: float,
    max_boundary_distance_m: float,
    sampling_interval_m: float,
    units: str,
    method: str = "topology_preserving",
) -> DerivedBoundary:
    """Create a topology-checked meshing boundary without changing ``source``.

    Simplification uses Douglas-Peucker with topology preservation. Acceptance
    also requires the same component and hole counts, valid nonempty polygonal
    topology, the configured relative-area limit, and a conservative symmetric
    densified-boundary distance bound. No default tolerance is supplied.
    """
    if units != "m":
        raise ValueError("Ngưỡng ranh giới dẫn xuất yêu cầu tọa độ chiếu theo mét (units='m').")
    values = (tolerance_m, max_area_change_fraction, max_boundary_distance_m, sampling_interval_m)
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in values):
        raise ValueError("Các ngưỡng ranh giới phải là số, không được là chuỗi hoặc boolean.")
    if any(not math.isfinite(value) for value in values):
        raise ValueError("Các ngưỡng diện tích và khoảng cách phải là số hữu hạn.")
    if tolerance_m <= 0:
        raise ValueError("tolerance_m phải lớn hơn 0.")
    if max_area_change_fraction < 0 or max_boundary_distance_m < 0:
        raise ValueError("Ngưỡng diện tích và khoảng cách biên phải không âm.")
    if sampling_interval_m <= 0:
        raise ValueError("sampling_interval_m phải lớn hơn 0.")
    if not isinstance(source, (Polygon, MultiPolygon)) or source.is_empty:
        raise ValueError("Ranh giới phải là Polygon hoặc MultiPolygon không rỗng.")
    if not source.is_valid or source.area <= 0:
        raise ValueError("Ranh giới nguồn phải hợp lệ và có diện tích dương.")
    for polygon in _polygons(source):
        for ring in (polygon.exterior, *polygon.interiors):
            coordinates = list(ring.coords)
            if any(not math.isfinite(value) for coordinate in coordinates for value in coordinate[:2]):
                raise ValueError("Ranh giới nguồn chứa tọa độ không hữu hạn.")
            if any(first[:2] == second[:2] for first, second in zip(coordinates, coordinates[1:])):
                raise ValueError("Ranh giới nguồn có cạnh dài 0; hãy giữ nguyên nguồn và xử lý dữ liệu lỗi.")

    source_parts = _polygons(source)
    source_holes = sum(len(polygon.interiors) for polygon in source_parts)
    source_hole_profile = sorted(len(polygon.interiors) for polygon in source_parts)
    source_hash = _geometry_hash(source)
    source_vertices = _vertex_count(source)
    if not isinstance(method, str) or method not in {"topology_preserving", "gnn_closed_rdp"}:
        raise ValueError("method phải là 'topology_preserving' hoặc 'gnn_closed_rdp'.")
    if method == "topology_preserving":
        candidate_parts = [part.simplify(tolerance_m, preserve_topology=True) for part in source_parts]
    else:
        work_budget = [MAX_RDP_DISTANCE_CHECKS]
        candidate_parts = [_simplify_ring_polygon(part, tolerance_m, work_budget) for part in source_parts]
    candidate = candidate_parts[0] if isinstance(source, Polygon) else MultiPolygon(candidate_parts)
    if not isinstance(candidate, (Polygon, MultiPolygon)) or candidate.is_empty or not candidate.is_valid:
        raise ValueError("Ranh giới dẫn xuất bị từ chối: hình học rỗng, sai kiểu hoặc không hợp lệ.")
    derived_parts = _polygons(candidate)
    derived_holes = sum(len(polygon.interiors) for polygon in derived_parts)
    if len(derived_parts) != len(source_parts):
        raise ValueError(f"Ranh giới dẫn xuất bị từ chối: số thành phần đổi từ {len(source_parts)} thành {len(derived_parts)}.")
    if sorted(len(polygon.interiors) for polygon in derived_parts) != source_hole_profile:
        raise ValueError(f"Ranh giới dẫn xuất bị từ chối: cấu trúc lỗ đổi từ {source_hole_profile} thành {sorted(len(polygon.interiors) for polygon in derived_parts)}.")

    absolute_area_difference = abs(candidate.area - source.area)
    added_area = candidate.difference(source).area
    removed_area = source.difference(candidate).area
    symmetric_difference_area = sum(
        original.symmetric_difference(derived).area
        for original, derived in zip(source_parts, derived_parts)
    )
    symmetric_difference_fraction = symmetric_difference_area / source.area
    component_symmetric_difference_fractions = [
        original.symmetric_difference(derived).area / original.area
        for original, derived in zip(source_parts, derived_parts)
    ]
    hole_symmetric_difference_fractions = []
    for component_index, (original, derived) in enumerate(zip(source_parts, derived_parts), 1):
        original_holes = [Polygon(ring) for ring in original.interiors]
        derived_hole_polygons = [Polygon(ring) for ring in derived.interiors]
        # GEOS preserves ring order for preserve_topology simplification; verify
        # this mapping and fail closed if a later implementation changes it.
        for hole_index, (original_hole, derived_hole) in enumerate(zip(original_holes, derived_hole_polygons), 1):
            checkpoint()
            hole_fraction = original_hole.symmetric_difference(derived_hole).area / original_hole.area
            hole_symmetric_difference_fractions.append({
                "component_index": component_index,
                "hole_index": hole_index,
                "fraction": hole_fraction,
            })
    distance = _densified_symmetric_distance(source, candidate, sampling_interval_m)
    if symmetric_difference_fraction > max_area_change_fraction:
        raise ValueError(
            "Ranh giới dẫn xuất bị từ chối: tỷ lệ diện tích sai khác đối xứng "
            f"{symmetric_difference_fraction:.12g} vượt ngưỡng {max_area_change_fraction:.12g}."
        )
    if any(value > max_area_change_fraction for value in component_symmetric_difference_fractions):
        raise ValueError(
            "Ranh giới dẫn xuất bị từ chối: tỷ lệ sai khác diện tích của một thành phần "
            f"vượt ngưỡng {max_area_change_fraction:.12g}."
        )
    if any(item["fraction"] > max_area_change_fraction for item in hole_symmetric_difference_fractions):
        raise ValueError(
            "Ranh giới dẫn xuất bị từ chối: tỷ lệ sai khác diện tích của một lỗ "
            f"vượt ngưỡng {max_area_change_fraction:.12g}."
        )
    if distance["upper_bound_m"] > max_boundary_distance_m:
        raise ValueError(
            "Ranh giới dẫn xuất bị từ chối: cận trên khoảng cách biên đối xứng "
            f"{distance['upper_bound_m']:.12g} m vượt ngưỡng {max_boundary_distance_m:.12g} m."
        )

    metadata: dict[str, object] = {
        "schema_version": "derived-meshing-boundary-v1",
        "method": method,
        "units": units,
        "tolerance_m": tolerance_m,
        "limits": {
            "max_area_change_fraction": max_area_change_fraction,
            "max_boundary_distance_m": max_boundary_distance_m,
            "sampling_interval_m": sampling_interval_m,
        },
        "source": {
            "geometry_sha256": source_hash,
            "component_count": len(source_parts),
            "hole_count": source_holes,
            "per_component_hole_counts": source_hole_profile,
            "vertex_count": source_vertices,
            "segment_count": source_vertices,
            "area_m2": source.area,
        },
        "derived": {
            "geometry_sha256": _geometry_hash(candidate),
            "component_count": len(derived_parts),
            "hole_count": derived_holes,
            "per_component_hole_counts": sorted(len(polygon.interiors) for polygon in derived_parts),
            "vertex_count": _vertex_count(candidate),
            "segment_count": _vertex_count(candidate),
            "area_m2": candidate.area,
        },
        "net_relative_area_difference": absolute_area_difference / source.area,
        "absolute_area_difference_m2": absolute_area_difference,
        "added_area_m2": added_area,
        "removed_area_m2": removed_area,
        "symmetric_difference_area_m2": symmetric_difference_area,
        "symmetric_difference_fraction": symmetric_difference_fraction,
        "component_symmetric_difference_fractions": component_symmetric_difference_fractions,
        "hole_symmetric_difference_fractions": hole_symmetric_difference_fractions,
        "boundary_distance": distance,
        "topology_valid": True,
        "accepted": True,
    }
    return DerivedBoundary(candidate, metadata)


def prepare_boundary(
    domain: Polygon | MultiPolygon,
    *,
    tolerance_m: float,
    max_area_change_fraction: float,
    max_boundary_distance_m: float,
    method: str = "topology_preserving",
) -> DerivedBoundary:
    """Runner adapter for ``mesh_domain``'s already validated metre geometry."""
    if (isinstance(max_boundary_distance_m, bool)
            or not isinstance(max_boundary_distance_m, (int, float))
            or not math.isfinite(max_boundary_distance_m)):
        raise ValueError("max_boundary_distance_m phải là số hữu hạn.")
    if max_boundary_distance_m <= 0:
        raise ValueError("max_boundary_distance_m phải lớn hơn 0.")
    return simplify_derived_boundary(
        domain,
        tolerance_m=tolerance_m,
        max_area_change_fraction=max_area_change_fraction,
        max_boundary_distance_m=max_boundary_distance_m,
        sampling_interval_m=min(1.0, max_boundary_distance_m / 2),
        units="m",
        method=method,
    )

