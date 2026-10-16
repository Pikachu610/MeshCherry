"""Measured, fixed-support square/triangle representation comparison.

Squares are clipped to the frozen triangle union, including holes. Disconnected
pieces of a clipped square become separate connected control volumes. This
module never edits accepted geometry or rasters.
"""
import math
from pathlib import Path

import numpy as np
from shapely.geometry import box
from shapely.ops import unary_union
from shapely.strtree import STRtree

from .extraction import _cells, _digest, _ExactFloatSum
from .indexed_extraction import (
    MAX_CLASS_ENTRIES, MAX_WINDOWS, footprint, limits, metadata,
    window_candidates, windows,
)
from .job_control import checkpoint, observe
from .mesh_workflow import read_frozen
from .raster_source import assert_unchanged, open_raster, snapshot
from .spatial import metric_crs
from .workload_limits import MAX_MESH_CELLS


def _square_partition(domain, size):
    xmin, ymin, xmax, ymax = domain.bounds
    counts = ((xmax-xmin)/size, (ymax-ymin)/size)
    if any(not math.isfinite(n) or n > MAX_MESH_CELLS for n in counts):
        raise ValueError('comparison square bounding-grid cell limit exceeded')
    nx, ny = map(math.ceil, counts)
    if nx * ny > MAX_MESH_CELLS:
        raise ValueError('comparison square bounding-grid cell limit exceeded')
    result = []
    for row in range(ny):
        checkpoint()
        for col in range(nx):
            if col % 128 == 0:
                checkpoint()
            square = box(xmin+col*size, ymin+row*size,
                         min(xmax, xmin+(col+1)*size), min(ymax, ymin+(row+1)*size))
            clipped = square.intersection(domain)
            if clipped.area > 0:
                pending = [clipped]
                components = []
                visited = 0
                while pending:
                    if visited % 128 == 0:
                        checkpoint()
                    visited += 1
                    part = pending.pop()
                    if part.geom_type == 'Polygon' and part.area > 0:
                        components.append(part)
                    elif hasattr(part, 'geoms'):
                        for child_index, child in enumerate(part.geoms):
                            if child_index % 128 == 0:
                                checkpoint()
                            pending.append(child)
                # Stable row/column order, then connected-component order.
                result.extend(sorted(components, key=lambda p: (p.bounds, p.wkb)))
                if len(result) > MAX_MESH_CELLS:
                    raise ValueError('comparison connected square cell limit exceeded')
    if not result:
        raise ValueError('comparison square partition is empty')
    checkpoint()
    rebuilt = unary_union(result)
    checkpoint()
    if rebuilt.symmetric_difference(domain).area > max(1e-8, domain.area*1e-9):
        raise ValueError('comparison partitions do not cover the same ROI')
    return result, [xmin, ymin]


def _finite_area_sum(values):
    """Accurate positive-area summation with bounded cancellation latency."""
    def checked():
        for index, value in enumerate(values):
            if index % 128 == 0:
                checkpoint()
            if not math.isfinite(value) or value < 0:
                raise ValueError('comparison categorical areas must be finite and nonnegative')
            yield value
        checkpoint()
    try:
        total = math.fsum(checked())
    except OverflowError as exc:
        raise ValueError('comparison categorical arithmetic exceeds finite numeric range') from exc
    if not math.isfinite(total):
        raise ValueError('comparison categorical arithmetic exceeds finite numeric range')
    return total


def _categorical_metrics(states, source_classes, support):
    """Aggregate only stored class entries; keep conservation and spatial loss."""
    if not math.isfinite(support) or support <= 0:
        raise ValueError('comparison categorical support must be finite and positive')
    areas_by_class, modal_areas = {}, []
    entries = 0
    for position, state in enumerate(states):
        if position % 128 == 0:
            checkpoint()
        modal_area = 0.
        for value, area in state['classes'].items():
            if entries % 128 == 0:
                checkpoint()
            entries += 1
            if not math.isfinite(area) or area < 0:
                raise ValueError('comparison categorical areas must be finite and nonnegative')
            areas_by_class.setdefault(value, []).append(area)
            modal_area = max(modal_area, area)
        modal_areas.append(modal_area)
    checkpoint()
    source, rebuilt, max_error = {}, {}, 0.
    for position, (value, area) in enumerate(sorted(source_classes.items())):
        if position % 128 == 0:
            checkpoint()
        if not math.isfinite(area) or area < 0:
            raise ValueError('comparison categorical areas must be finite and nonnegative')
        # Keep existing labels for exactly representable integer categories;
        # larger integer IDs need exact decimal strings to stay distinct.
        key = str(float(value)) if isinstance(value, int) and abs(value) <= 2**53 else str(value)
        source[key] = area/support
        rebuilt[key] = _finite_area_sum(areas_by_class.pop(value, ()))/support
        if not all(math.isfinite(v) for v in (source[key], rebuilt[key])):
            raise ValueError('comparison categorical arithmetic exceeds finite numeric range')
        max_error = max(max_error, abs(source[key]-rebuilt[key]))
    if areas_by_class:
        raise ValueError('comparison remapped classes differ from the common source support')
    agreement = _finite_area_sum(modal_areas)/support
    if not math.isfinite(agreement) or not -1e-12 <= agreement <= 1+1e-12:
        raise ValueError('comparison categorical modal support differs')
    agreement = min(1., max(0., agreement))
    return dict(source_class_fractions=source, remapped_class_fractions=rebuilt,
        max_fraction_error=max_error, area_weighted_modal_agreement=agreement,
        area_weighted_modal_disagreement=1-agreement,
        interpretation='global fractional composition conservation; modal agreement is the valid-area fraction matching each cell dominant source class, not GNN prediction accuracy; no numeric RMSE')


def compare_mesh(cells_path, raster_path, *, analysis_crs, cell_size_m,
                 band=1, semantics='intensive', value_units='', edge_subdivisions=8):
    """Compare unchanged frozen triangles with a newly clipped square partition.

    Intensive errors integrate squared/absolute differences on pixel-cell
    intersections. They are NOT errors of a pixel-averaged backprojection,
    which can hide subpixel differences. Extensive values are source-pixel
    totals; categorical output checks conservation and dominant-class spatial loss.
    Existing indexed read/pair/class ceilings and cooperative cancellation apply.
    """
    checkpoint()
    target = metric_crs(analysis_crs)
    if (isinstance(cell_size_m, bool) or not isinstance(cell_size_m, (int, float))
            or not math.isfinite(cell_size_m) or cell_size_m <= 0):
        raise ValueError('comparison cell size must be finite and positive')
    if semantics not in {'intensive', 'extensive', 'categorical'} or not value_units:
        raise ValueError('declared semantics and value units required')
    if type(edge_subdivisions) is not int or not 1 <= edge_subdivisions <= 64:
        raise ValueError('edge_subdivisions must be an integer from 1 to 64')
    cells_path, raster_path = Path(cells_path), Path(raster_path)
    mesh_hash = _digest(cells_path)
    manifest_path = cells_path.parent/'manifest.json'
    manifest_hash = _digest(manifest_path)
    frozen = read_frozen(cells_path, target, maximum=MAX_MESH_CELLS)
    if any(c.kind != 'triangle' for c in frozen.cells):
        raise ValueError('comparison requires an accepted triangular mesh')
    triangles = _cells(frozen.cells)
    checkpoint()
    domain = unary_union(triangles)
    checkpoint()
    squares, origin = _square_partition(domain, float(cell_size_m))
    parts = {'structured': squares, 'unstructured': triangles}
    trees = {name: STRtree(polys) for name, polys in parts.items()}
    states = {name: [dict(area=0., mean=0., total=0., total_sum=_ExactFloatSum(), classes={}) for _ in polys]
              for name, polys in parts.items()}
    cap = limits()
    raster_before = snapshot(raster_path)
    raster_hash = _digest(raster_path)
    support = 0.
    source_total_sum = _ExactFloatSum()
    source_classes = {}
    class_entries = 0
    passes = []
    errors = {name: dict(scale=0., squared=0., absolute=0., bias=0., area=0.) for name in parts}

    # One shared source traversal per pass guarantees identical masks and band.
    with open_raster(raster_path) as src:
        metadata(src, band, cap)
        source_crs = src.crs.to_wkt()
        affine, shape = list(src.transform), [src.height, src.width]
        for pass_index in range(2 if semantics == 'intensive' else 1):
            if pass_index:
                if not math.isfinite(support) or support <= 0:
                    raise ValueError('comparison has no finite valid common raster support')
                for partition_states in states.values():
                    for state in partition_states:
                        state['mean'] = state['mean_scale'] * state['mean_normalized']
                        if not math.isfinite(state['mean']):
                            raise ValueError('comparison intensive mean exceeds finite numeric range')
            counters = dict(windows_scanned=0, windows_read=0, pixels_read=0,
                            candidate_pairs=0, peak_window_pixels=0)
            for window in windows(src, band, cap):
                observe('extraction')
                counters['windows_scanned'] += 1
                if counters['windows_scanned'] > MAX_WINDOWS:
                    raise ValueError('comparison window scan limit exceeded')
                if not len(window_candidates(src, target, trees['unstructured'], window, len(triangles))):
                    continue
                count = int(window.width*window.height)
                counters['pixels_read'] += count
                if counters['pixels_read'] > cap['max_pixels_read']:
                    raise ValueError('comparison read pixel limit exceeded')
                counters['windows_read'] += 1
                counters['peak_window_pixels'] = max(counters['peak_window_pixels'], count)
                data = src.read(band, window=window, masked=True)
                # Categorical IDs may be uint64/int64 values beyond float64's
                # exact-integer range. Preserve their dtype and dictionary keys.
                values = np.asarray(data.data) if semantics == 'categorical' else np.asarray(data.data, float)
                valid = ~np.ma.getmaskarray(data) & np.isfinite(values)
                for row in range(int(window.height)):
                    for col in range(int(window.width)):
                        if col % 128 == 0:
                            checkpoint()
                        if not valid[row, col]:
                            continue
                        pixel = footprint(src, target, int(window.col_off)+col,
                            int(window.row_off)+row, subdivisions=edge_subdivisions)
                        value = values[row, col].item() if semantics == 'categorical' else float(values[row, col])
                        # Count the support once, independently of partition.
                        if pass_index == 0:
                            area = pixel.intersection(domain).area
                            support += area
                            if semantics == 'extensive':
                                source_total_sum.add(value*(area/pixel.area))
                            if semantics == 'categorical' and area > 0:
                                if value not in source_classes:
                                    class_entries += 1
                                if class_entries > MAX_CLASS_ENTRIES:
                                    raise ValueError('comparison categorical accumulator limit exceeded')
                                source_classes[value] = source_classes.get(value, 0.)+area
                        for name, polygons in parts.items():
                            candidates = trees[name].query(pixel)
                            counters['candidate_pairs'] += len(candidates)
                            if counters['candidate_pairs'] > cap['max_candidate_pairs']:
                                raise ValueError('comparison candidate pair limit exceeded')
                            for position, index in enumerate(candidates):
                                if position % 128 == 0:
                                    checkpoint()
                                area = float(pixel.intersection(polygons[int(index)]).area)
                                if area <= 0:
                                    continue
                                state = states[name][int(index)]
                                if pass_index:
                                    error = errors[name]
                                    local_scale = max(abs(state['mean']), abs(value))
                                    normalized_delta = (state['mean']/local_scale - value/local_scale
                                                        if local_scale else 0.0)
                                    scale = max(error['scale'], local_scale)
                                    ratio = error['scale']/scale if scale else 0.0
                                    delta = normalized_delta*(local_scale/scale) if scale else 0.0
                                    error['squared'] = error['squared']*ratio*ratio + (area/support)*delta*delta
                                    error['absolute'] = error['absolute']*ratio + (area/support)*abs(delta)
                                    error['bias'] = error['bias']*ratio + (area/support)*delta
                                    error['scale'] = scale
                                    error['area'] += area
                                    continue
                                weight = state['area']+area
                                if semantics == 'intensive':
                                    value_scale = max(state.get('mean_scale', 0.0), abs(value))
                                    old_normalized = (state.get('mean_normalized', 0.0) *
                                                      (state.get('mean_scale', 0.0)/value_scale)
                                                      if value_scale else 0.0)
                                    value_normalized = value/value_scale if value_scale else 0.0
                                    state['mean_normalized'] = old_normalized + (
                                        value_normalized-old_normalized)*(area/weight)
                                    state['mean_scale'] = value_scale
                                state['area'] = weight
                                if semantics == 'extensive':
                                    state['total_sum'].add(value*(area/pixel.area))
                                if semantics == 'categorical':
                                    if value not in state['classes']:
                                        class_entries += 1
                                    if class_entries > MAX_CLASS_ENTRIES:
                                        raise ValueError('comparison categorical accumulator limit exceeded')
                                    state['classes'][value] = state['classes'].get(value, 0.)+area
            passes.append(counters)
    if support <= 0:
        raise ValueError('comparison has no finite valid common raster support')
    source_total = (source_total_sum.value('comparison source total')
                    if semantics == 'extensive' else 0.0)
    metrics, summaries = {}, {}
    tolerance = max(1e-8, support*1e-8)
    for name, polygons in parts.items():
        checkpoint()
        valid_area = math.fsum(s['area'] for s in states[name])
        if abs(valid_area-support) > tolerance:
            raise ValueError('comparison valid support differs between partitions')
        summaries[name] = dict(cell_count=len(polygons), node_count=len(polygons),
                              valid_support_area_m2=valid_area)
        if semantics == 'intensive':
            error = errors[name]
            if abs(error['area']-support) > tolerance:
                raise ValueError('comparison error pass support differs')
            metrics[name] = dict(rmse=error['scale']*math.sqrt(error['squared']),
                mae=error['scale']*error['absolute'], bias=error['scale']*error['bias'], units=value_units)
            if any(not math.isfinite(metrics[name][k]) for k in ('rmse', 'mae', 'bias')):
                raise ValueError('comparison intensive arithmetic exceeds finite numeric range')
        elif semantics == 'extensive':
            remapped_total_sum = _ExactFloatSum()
            for state_index, state in enumerate(states[name]):
                if state_index % 128 == 0:
                    checkpoint()
                remapped_total_sum.merge(state['total_sum'])
            total = remapped_total_sum.value('comparison remapped total')
            residual = total-source_total
            if not all(math.isfinite(v) for v in (total, source_total, residual)):
                raise ValueError('comparison extensive arithmetic exceeds finite numeric range')
            conservation_tolerance = max(1e-10, abs(source_total)*1e-9)
            if abs(residual) > conservation_tolerance:
                raise ValueError('comparison extensive conservation failed')
            metrics[name] = dict(source_total=source_total, remapped_total=total,
                conservation_residual=residual, tolerance=conservation_tolerance, units=value_units)
        else:
            metrics[name] = _categorical_metrics(states[name], source_classes, support)
    assert_unchanged(raster_path, raster_before)
    if (_digest(raster_path) != raster_hash or _digest(cells_path) != mesh_hash
            or _digest(manifest_path) != manifest_hash):
        raise ValueError('comparison input changed during computation')
    return dict(schema_version='mesh-comparison-v1', **summaries,
        node_reduction_percent=100*(1-len(triangles)/len(squares)),
        graph_node_delta=len(triangles)-len(squares),
        graph_node_policy='one control-volume cell = one graph node; mesh vertices are not graph nodes',
        domain_area_m2=float(domain.area), common_support_area_m2=float(support), semantics=semantics,
        metrics=metrics, method='fixed-valid-support pixel-cell intersection-area piecewise-constant reconstruction',
        error_definition='integrate cell representative minus source pixel value over their intersection; not pixel-averaged backprojection or GNN prediction accuracy',
        square_grid=dict(cell_size_m=float(cell_size_m), origin_xy=origin,
            boundary_policy='exact intersection with accepted triangle union; holes retained; each connected polygon piece is one cell',
            cell_id_policy='comparison-only row-south/column-east/component bounds-WKB order; accepted IDs unchanged',
            shape_policy='interior squares; clipped boundary cells may be irregular polygons'),
        provenance=dict(source_path=str(raster_path.resolve()), source_sha256=raster_hash,
            source_files_sha256=raster_before, mesh_path=str(cells_path.resolve()), mesh_sha256=mesh_hash,
            mesh_manifest_sha256=manifest_hash, geometry_digest=frozen.provenance['geometry_digest'],
            source_crs_wkt=source_crs, analysis_crs_wkt=target.to_wkt(), affine=affine, shape=shape,
            band=band, edge_subdivisions=edge_subdivisions, value_units=value_units,
            raster_resampling='none', area_model='projected planar; cross-CRS edges approximated'),
        workload=dict(hard_limits=cap, max_cells=MAX_MESH_CELLS,
                      max_class_entries=MAX_CLASS_ENTRIES, passes=passes))
