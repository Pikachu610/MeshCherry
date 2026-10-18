"""Area-only resolution estimates for frozen triangle meshes.

This module estimates regular-grid cell counts from area and nominal cell size.
It does not read a raster, construct a grid, or measure representation error.
"""
import math
from pathlib import Path

from shapely.ops import unary_union

from .extraction import _cells, _digest
from .job_control import checkpoint
from .mesh_workflow import read_frozen
from .spatial import metric_crs
from .workload_limits import MAX_MESH_CELLS

MAX_SAFE_JSON_INTEGER = 2**53 - 1


def _positive_finite_size(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f'{name} must be a finite positive number')
    try:
        result = float(value)
    except (OverflowError, ValueError):
        raise ValueError(f'{name} must be a finite positive number') from None
    if not math.isfinite(result) or result <= 0:
        raise ValueError(f'{name} must be a finite positive number')
    return result


def _ceil_area_over_square(area, resolution):
    """Compute ceil(area / resolution**2) without float overflow/underflow."""
    if not math.isfinite(area) or area <= 0:
        raise ValueError('comparison union area must be finite and positive')
    area_num, area_den = area.as_integer_ratio()
    res_num, res_den = resolution.as_integer_ratio()
    numerator = area_num * res_den * res_den
    denominator = area_den * res_num * res_num
    return (numerator + denominator - 1) // denominator


def _require_json_safe_count(count, name):
    if count > MAX_SAFE_JSON_INTEGER:
        raise ValueError(
            f'{name} exceeds the maximum exactly representable JSON/browser integer '
            f'({MAX_SAFE_JSON_INTEGER})')
    return count


def compare_resolution_estimate(cells_path, *, analysis_crs, cell_size_m,
                                source_resolution_m):
    """Estimate node counts from mesh area and nominal source/target resolution.

    The returned native and target-grid counts are area-only estimates. They do
    not account for grid alignment, raster NoData, or ROI boundary geometry and
    do not provide raster values or an error/RMSE measurement.
    """
    checkpoint()
    target = metric_crs(analysis_crs)
    target_size = _positive_finite_size(cell_size_m, 'cell_size_m')
    source_size = _positive_finite_size(source_resolution_m, 'source_resolution_m')
    path = Path(cells_path)
    manifest_path = path.parent / 'manifest.json'
    mesh_hash_before = _digest(path)
    manifest_hash_before = _digest(manifest_path)
    frozen = read_frozen(path, target, maximum=MAX_MESH_CELLS)
    if any(cell.kind != 'triangle' for cell in frozen.cells):
        raise ValueError('resolution estimate requires an accepted triangular mesh')

    checkpoint()
    polygons = _cells(frozen.cells)
    try:
        union = unary_union(polygons)
        area = float(union.area)
        summed_area = math.fsum(float(polygon.area) for polygon in polygons)
    except (ArithmeticError, OverflowError, ValueError) as error:
        raise ValueError('could not compute finite frozen mesh union area') from error
    if union.is_empty or not union.is_valid or not math.isfinite(area) or area <= 0:
        raise ValueError('frozen mesh union area must be finite and positive')
    if not math.isfinite(summed_area):
        raise ValueError('frozen mesh cell area sum is not finite')
    overlap_area = summed_area - area
    if overlap_area > max(1e-8, summed_area * 1e-9):
        raise ValueError('frozen triangular mesh cells overlap by positive area')

    mesh_nodes = len(frozen.cells)
    estimated_source_pixels = _require_json_safe_count(
        _ceil_area_over_square(area, source_size), 'estimated source pixel count')
    estimated_target_cells = _require_json_safe_count(
        _ceil_area_over_square(area, target_size), 'estimated target cell count')
    checkpoint()
    if (_digest(path) != mesh_hash_before
            or _digest(manifest_path) != manifest_hash_before):
        raise ValueError('frozen mesh input changed during resolution estimate')

    reduction = 100.0 * (1.0 - mesh_nodes / estimated_target_cells)
    return dict(
        schema_version='mesh-resolution-estimate-v1',
        mesh=dict(cell_count=mesh_nodes, node_count=mesh_nodes,
                  union_area_m2=area,
                  node_policy='one accepted triangle cell = one graph node'),
        square_grid=dict(cell_count=estimated_target_cells,
                         node_count=estimated_target_cells,
                         cell_size_m=target_size,
                         count_kind='estimated',
                         estimate='ceil(mesh union area / cell_size_m squared)',
                         limitations=['area-only estimate',
                                      'grid alignment and ROI boundary geometry ignored']),
        baseline=dict(mode='resolution_estimate',
                      count_kind='estimated',
                      estimated_node_count=estimated_target_cells,
                      estimated_source_pixel_count=estimated_source_pixels,
                      source_resolution_m=source_size,
                      limitations=['area-only estimate',
                                   'grid alignment and ROI boundary geometry ignored',
                                   'raster NoData and valid-pixel coverage unavailable']),
        node_reduction_percent=reduction,
        graph_node_delta=mesh_nodes - estimated_target_cells,
        graph_node_delta_definition='accepted mesh nodes minus estimated target-grid nodes',
        metrics_available=False,
        metrics=None,
        method='area-only nominal-resolution estimate; no raster grid constructed or read',
        error_definition='not computed; resolution estimates do not measure representation error or terrain accuracy',
        provenance=dict(mesh_path=str(path.resolve()),
                        mesh_sha256=mesh_hash_before,
                        mesh_manifest_sha256=manifest_hash_before,
                        geometry_digest=frozen.provenance['geometry_digest'],
                        analysis_crs_wkt=target.to_wkt(),
                        area_model='projected planar in metres'),
    )
