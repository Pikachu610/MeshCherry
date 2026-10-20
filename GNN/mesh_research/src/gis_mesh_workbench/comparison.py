"""Measured, fixed-support square/triangle representation comparison.

Squares are clipped to the frozen triangle union, including holes. Disconnected
pieces of a clipped square become separate connected control volumes. This
module never edits accepted geometry or rasters.
"""
import math
import random
from pathlib import Path

import numpy as np
import shapely
from shapely.geometry import box
from shapely.ops import unary_union
from shapely.strtree import STRtree

from .extraction import _cells, _digest, _ExactFloatSum
from .indexed_extraction import (
    MAX_CLASS_ENTRIES, MAX_WINDOWS, footprint, limits, metadata,
    window_candidates, windows,
)
from .comparison_kernel import (
    GeometryWeightCache, GeometryWeights, geometry_batch,
    geometry_chunk_pixels, automatic_kernel,
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


def _polygon_component_count(geometry):
    """Count positive-area polygon pieces without retaining them beyond a pixel."""
    pending = [geometry]
    count = visited = 0
    while pending:
        if visited % 128 == 0:
            checkpoint()
        visited += 1
        part = pending.pop()
        if part.geom_type == 'Polygon':
            if part.area > 0:
                count += 1
        elif hasattr(part, 'geoms'):
            pending.extend(part.geoms)
    return count


def _zone_layout(domain):
    """Build nine modest ROI clips; row 0 is south, column 0 is west."""
    xmin, ymin, xmax, ymax = domain.bounds
    dx, dy = (xmax-xmin)/3, (ymax-ymin)/3
    zones = []
    for row in range(3):
        checkpoint()
        for column in range(3):
            clip = box(xmin+column*dx, ymin+row*dy,
                       xmax if column == 2 else xmin+(column+1)*dx,
                       ymax if row == 2 else ymin+(row+1)*dy).intersection(domain)
            zones.append(dict(row=row, column=column, roi_area_m2=float(clip.area)))
    if abs(math.fsum(zone['roi_area_m2'] for zone in zones)-domain.area) > max(1e-8, domain.area*1e-9):
        raise ValueError('comparison spatial zones do not cover the ROI area')
    return dict(bounds=(xmin, ymin, xmax, ymax), dx=dx, dy=dy, zones=zones)


def _zone_index(layout, point):
    xmin, ymin, xmax, ymax = layout['bounds']
    dx, dy = layout['dx'], layout['dy']
    column = min(2, max(0, int(math.floor((point.x-xmin)/dx))))
    row = min(2, max(0, int(math.floor((point.y-ymin)/dy))))
    return row*3+column


def _zone_node_counts(polygons, layout):
    counts = [0]*9
    for index, polygon in enumerate(polygons):
        if index % 128 == 0:
            checkpoint()
        counts[_zone_index(layout, polygon.representative_point())] += 1
    return counts


def compare_mesh(cells_path, raster_path, *, analysis_crs, cell_size_m=None,
                 baseline_mode='target_grid', band=1, semantics='intensive',
                 value_units='', edge_subdivisions=8, _kernel='auto',
                 _geometry_cache_bytes=16*1024*1024):
    """Compare unchanged frozen triangles with a newly clipped square partition.

    Intensive errors integrate squared/absolute differences on pixel-cell
    intersections. They are NOT errors of a pixel-averaged backprojection,
    which can hide subpixel differences. Extensive values are source-pixel
    totals; categorical output checks conservation and dominant-class spatial loss.
    Existing indexed read/pair/class ceilings and cooperative cancellation apply.
    """
    observe('comparison_prepare')
    target = metric_crs(analysis_crs)
    if baseline_mode not in {'target_grid', 'native_raster'}:
        raise ValueError('comparison baseline mode must be target_grid or native_raster')
    if baseline_mode == 'target_grid':
        if (isinstance(cell_size_m, bool) or not isinstance(cell_size_m, (int, float))
                or not math.isfinite(cell_size_m) or cell_size_m <= 0):
            raise ValueError('comparison cell size must be finite and positive')
    elif cell_size_m is not None:
        raise ValueError('cell size is only valid for target_grid baseline')
    if semantics not in {'intensive', 'extensive', 'categorical'} or not value_units:
        raise ValueError('declared semantics and value units required')
    if type(edge_subdivisions) is not int or not 1 <= edge_subdivisions <= 64:
        raise ValueError('edge_subdivisions must be an integer from 1 to 64')
    if _kernel not in {'auto', 'scalar', 'vectorized'}:
        raise ValueError('comparison kernel must be scalar or vectorized')
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
    if baseline_mode == 'target_grid':
        squares, origin = _square_partition(domain, float(cell_size_m))
        parts = {'structured': squares, 'unstructured': triangles}
    else:
        squares, origin = None, None
        parts = {'unstructured': triangles}
    zones = _zone_layout(domain)
    mesh_zone_counts = _zone_node_counts(triangles, zones)
    structured_zone_counts = (_zone_node_counts(squares, zones)
                              if baseline_mode == 'target_grid' else [0]*9)
    zone_counts = dict(unstructured=mesh_zone_counts,
                       structured=structured_zone_counts)
    trees = {name: STRtree(polys) for name, polys in parts.items()}
    geometry_parts = dict(parts, __domain__=domain)
    geometry_arrays = {name: tree.geometries for name, tree in trees.items()}
    chunk_pixels = geometry_chunk_pixels(geometry_parts)
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
    native_counts = dict(source_pixel_count=0, valid_source_pixel_count=0,
                         clipped_component_count=0)
    # The geometric native-pixel sample is display-only and strictly bounded.
    native_preview_polygons = []
    native_preview_seen = 0
    native_preview_rng = random.Random(0)
    geometry_cache = GeometryWeightCache(_geometry_cache_bytes)

    # One shared source traversal per pass guarantees identical masks and band.
    with open_raster(raster_path) as src:
        selected_kernel = (automatic_kernel(src,target,semantics,baseline_mode,parts)
                           if _kernel == 'auto' else _kernel)
        metadata(src, band, cap)
        source_crs = src.crs.to_wkt()
        affine, shape = list(src.transform), [src.height, src.width]
        native_source_resolution = dict(
            x=math.hypot(src.transform.a, src.transform.d),
            y=math.hypot(src.transform.b, src.transform.e),
            units='degrees' if src.crs.is_geographic else src.crs.linear_units,
            crs=src.crs.to_string())
        aggregate = dict(windows_scanned=0, windows_read=0, pixels_read=0,
                         candidate_pairs=0, peak_window_pixels=0)
        total_windows = sum(1 for _ in windows(src, band, cap))
        cache_counts = dict(hits=0, misses=0, bytes=0,
                            status=('enabled' if _geometry_cache_bytes else 'disabled_by_budget')
                            if semantics == 'intensive' else 'not_applicable')
        for pass_index in range(2 if semantics == 'intensive' else 1):
            phase = ('values_pass1' if pass_index == 0 else 'error_pass2')
            if semantics != 'intensive':
                phase = 'measure_pass1'
            observe(phase, 0, total_windows, unit='windows', pass_index=pass_index)
            if pass_index:
                if not math.isfinite(support) or support <= 0:
                    raise ValueError('comparison has no finite valid common raster support')
                for partition_states in states.values():
                    for state in partition_states:
                        # Keep zero-support geometry in the inventory, but it
                        # has no representative value to compare. Do not invent
                        # a zero mean or add synthetic support to the metrics.
                        if state['area'] <= 0:
                            continue
                        state['mean'] = state['mean_scale'] * state['mean_normalized']
                        if not math.isfinite(state['mean']):
                            raise ValueError('comparison intensive mean exceeds finite numeric range')
            counters = dict(windows_scanned=0, windows_read=0, pixels_read=0,
                            candidate_pairs=0, peak_window_pixels=0)
            for window_index, window in enumerate(windows(src, band, cap)):
                counters['windows_scanned'] += 1
                aggregate['windows_scanned'] += 1
                if aggregate['windows_scanned'] > MAX_WINDOWS:
                    raise ValueError('comparison window scan limit exceeded')
                if not len(window_candidates(src, target, trees['unstructured'], window, len(triangles))):
                    observe(phase, counters['windows_scanned'], total_windows,
                            unit='windows', pass_index=pass_index)
                    continue
                count = int(window.width*window.height)
                counters['pixels_read'] += count
                aggregate['pixels_read'] += count
                if aggregate['pixels_read'] > cap['max_pixels_read']:
                    raise ValueError('comparison read pixel limit exceeded')
                counters['windows_read'] += 1
                aggregate['windows_read'] += 1
                counters['peak_window_pixels'] = max(counters['peak_window_pixels'], count)
                aggregate['peak_window_pixels'] = max(aggregate['peak_window_pixels'], count)
                data = src.read(band, window=window, masked=True)
                # Categorical IDs may be uint64/int64 values beyond float64's
                # exact-integer range. Preserve their dtype and dictionary keys.
                values = np.asarray(data.data) if semantics == 'categorical' else np.asarray(data.data, float)
                valid = ~np.ma.getmaskarray(data) & np.isfinite(values)
                width = int(window.width)
                total = int(window.width*window.height)
                flat_values = values.ravel()
                flat_valid = valid.ravel()
                for chunk_start in range(0, total, chunk_pixels):
                    checkpoint()
                    chunk_stop = min(total, chunk_start+chunk_pixels)
                    offsets = np.arange(chunk_start, chunk_stop, dtype=np.int64)
                    rows = offsets // width
                    cols = offsets % width
                    absolute_cols = cols + int(window.col_off)
                    absolute_rows = rows + int(window.row_off)
                    cache_key = (window_index, chunk_start)
                    cached = geometry_cache.get(cache_key) if semantics == 'intensive' else None
                    if cached is None:
                        if semantics == 'intensive':
                            cache_counts['misses'] += 1
                        pixels, pixel_areas, domain_areas, intersection_data, candidate_data = geometry_batch(
                            src, target, absolute_cols, absolute_rows, edge_subdivisions,
                            geometry_parts, trees, valid_mask=flat_valid[offsets],
                            kernel=selected_kernel, polygon_arrays=geometry_arrays)
                        weights = GeometryWeights(pixel_areas, domain_areas,
                                                  intersection_data, candidate_data)
                        if pass_index == 0 and semantics == 'intensive':
                            geometry_cache.put(cache_key, weights)
                            cache_counts['bytes'] = geometry_cache.bytes_used
                    else:
                        cache_counts['hits'] += 1
                        weights = cached
                        pixels = None
                        pixel_areas, domain_areas = weights.pixel_areas, weights.domain_areas
                        intersection_data, candidate_data = weights.intersections, weights.candidate_counts

                    batch_candidates = sum(candidate_data.values())
                    counters['candidate_pairs'] += batch_candidates
                    aggregate['candidate_pairs'] += batch_candidates
                    if aggregate['candidate_pairs'] > cap['max_candidate_pairs']:
                        raise ValueError('comparison candidate pair limit exceeded')

                    if baseline_mode == 'native_raster' and pass_index == 0:
                        clipped = shapely.intersection(pixels, domain)
                        for local_index, geom in enumerate(clipped):
                            if local_index % 128 == 0:
                                checkpoint()
                            component_count = _polygon_component_count(geom)
                            if not component_count:
                                continue
                            native_counts['source_pixel_count'] += 1
                            native_counts['clipped_component_count'] += component_count
                            native_preview_seen += 1
                            if len(native_preview_polygons) < 1200:
                                native_preview_polygons.append(geom)
                            else:
                                replacement = native_preview_rng.randrange(native_preview_seen)
                                if replacement < 1200:
                                    native_preview_polygons[replacement] = geom
                            zone_counts['structured'][_zone_index(
                                zones, geom.representative_point())] += 1
                            if flat_valid[int(offsets[local_index])]:
                                native_counts['valid_source_pixel_count'] += 1

                    for local_index, offset in enumerate(offsets):
                        if local_index % 128 == 0:
                            checkpoint()
                        offset = int(offset)
                        if not flat_valid[offset]:
                            continue
                        value = flat_values[offset].item() if semantics == 'categorical' else float(flat_values[offset])
                        domain_area = float(domain_areas[local_index]) if pass_index == 0 else 0.
                        if pass_index == 0:
                            support += domain_area
                            if semantics == 'extensive':
                                source_total_sum.add(value*(domain_area/float(pixel_areas[local_index])))
                            if semantics == 'categorical' and domain_area > 0:
                                if value not in source_classes:
                                    class_entries += 1
                                if class_entries > MAX_CLASS_ENTRIES:
                                    raise ValueError('comparison categorical accumulator limit exceeded')
                                source_classes[value] = source_classes.get(value, 0.)+domain_area

                        for name, (pair_pixels, pair_cells, pair_areas) in intersection_data.items():
                            left = int(np.searchsorted(pair_pixels, local_index, side='left'))
                            right = int(np.searchsorted(pair_pixels, local_index, side='right'))
                            for pair_index in range(left, right):
                                area = float(pair_areas[pair_index])
                                state = states[name][int(pair_cells[pair_index])]
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
                                    state['total_sum'].add(value*(area/float(pixel_areas[local_index])))
                                if semantics == 'categorical':
                                    if value not in state['classes']:
                                        class_entries += 1
                                    if class_entries > MAX_CLASS_ENTRIES:
                                        raise ValueError('comparison categorical accumulator limit exceeded')
                                    state['classes'][value] = state['classes'].get(value, 0.)+area
                observe(phase, counters['windows_scanned'], total_windows,
                        unit='windows', pass_index=pass_index)
            if pass_index == 0 and semantics == 'intensive' and not geometry_cache.enabled:
                cache_counts['status'] = 'recompute_fallback_budget'
            elif pass_index == 0 and semantics == 'intensive':
                cache_counts['status'] = 'cached' if geometry_cache.entries else 'empty_geometry'
            passes.append(counters)
    observe('comparison_publish')
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
    if baseline_mode == 'native_raster':
        # The raster compared with itself is an exact identity reference. Its
        # zero error does not establish physical accuracy or added detail.
        native_metric = (
            dict(rmse=0.0, mae=0.0, bias=0.0, units=value_units,
                 interpretation='native raster identity reference; zero error is tautological')
            if semantics == 'intensive' else
            dict(source_total=source_total, remapped_total=source_total,
                 conservation_residual=0.0, tolerance=max(1e-10, abs(source_total)*1e-9),
                 units=value_units)
            if semantics == 'extensive' else
            dict(source_class_fractions={str(float(k)) if isinstance(k, int) and abs(k) <= 2**53 else str(k): v/support
                    for k, v in sorted(source_classes.items())},
                 remapped_class_fractions={str(float(k)) if isinstance(k, int) and abs(k) <= 2**53 else str(k): v/support
                    for k, v in sorted(source_classes.items())},
                 max_fraction_error=0.0, area_weighted_modal_agreement=1.0,
                 area_weighted_modal_disagreement=0.0,
                 interpretation='native raster identity reference; zero spatial loss is tautological'))
        summaries['structured'] = dict(cell_count=native_counts['source_pixel_count'],
            node_count=native_counts['source_pixel_count'],
            valid_source_pixel_count=native_counts['valid_source_pixel_count'],
            clipped_component_count=native_counts['clipped_component_count'],
            valid_support_area_m2=support)
        metrics['structured'] = native_metric
    assert_unchanged(raster_path, raster_before)
    if (_digest(raster_path) != raster_hash or _digest(cells_path) != mesh_hash
            or _digest(manifest_path) != manifest_hash):
        raise ValueError('comparison input changed during computation')
    structured_node_count = (len(squares) if baseline_mode == 'target_grid'
                             else native_counts['source_pixel_count'])
    graph_node_policy = (
        'one native source pixel is counted once as one graph-node candidate; clipped connected pieces are geometric diagnostics, not extra nodes'
        if baseline_mode == 'native_raster' else
        'one target or mesh control-volume cell = one graph node; mesh vertices are not graph nodes')
    report = dict(schema_version='mesh-comparison-v1', **summaries,
        node_reduction_percent=100*(1-len(triangles)/structured_node_count),
        graph_node_delta=len(triangles)-structured_node_count,
        graph_node_policy=graph_node_policy,
        domain_area_m2=float(domain.area), common_support_area_m2=float(support), semantics=semantics,
        metrics=metrics, method='fixed-valid-support pixel-cell intersection-area piecewise-constant reconstruction',
        error_definition='integrate cell representative minus source pixel value over their intersection; not pixel-averaged backprojection or GNN prediction accuracy',
        square_grid=dict(cell_size_m=float(cell_size_m) if cell_size_m is not None else None, origin_xy=origin,
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
                      geometry_kernel=selected_kernel,
                      kernel_selection='bounded benchmark envelope; scalar fallback outside it' if _kernel=='auto' else 'explicit diagnostic override',
                      max_class_entries=MAX_CLASS_ENTRIES, passes=passes,
                      geometry_weight_cache=dict(**cache_counts,
                          max_bytes=_geometry_cache_bytes,
                          semantics='ephemeral pixel-cell intersection areas; overflow triggers scalar recomputation'),
                      aggregate=dict(**aggregate,
                          scope='whole comparison job; intensive mode includes both passes')))
    if baseline_mode == 'native_raster':
        report.update(baseline=dict(mode='native_raster', node_count=structured_node_count,
            source_pixel_count=native_counts['source_pixel_count'],
            valid_source_pixel_count=native_counts['valid_source_pixel_count'],
            clipped_component_count=native_counts['clipped_component_count'],
            alignment='source affine pixel grid, including rotation; pixels counted once when ROI intersection area is positive',
            source_resolution=native_source_resolution,
            identity_reference='native pixel values compared with themselves; zero error is tautological'))
        report['square_grid'].update(cell_size_m=None,
            boundary_policy='native affine pixels counted when their ROI intersection has positive area',
            shape_policy='native pixel cells; clipped pieces counted separately; source grid is not regenerated')
    else:
        report['baseline'] = dict(mode='target_grid', node_count=structured_node_count,
            cell_size_m=float(cell_size_m), alignment='accepted triangle union bounding-box lower-left',
            source_resolution=native_source_resolution,
            source_vs_requested=dict(requested_cell_size_m=float(cell_size_m),
                source_resolution=native_source_resolution,
                interpretation='Target cells are evaluated against the original pixel values. Smaller target cells subdivide piecewise-constant source support and do not create new source information; source and requested sizes are directly comparable only when their CRS and units align.'))
    zone_rows = []
    for index, base in enumerate(zones['zones']):
        area = base['roi_area_m2']
        mesh_nodes = mesh_zone_counts[index]
        structured_nodes = zone_counts['structured'][index]
        zone_rows.append(dict(row=base['row'], column=base['column'],
            roi_area_m2=area, mesh_node_count=mesh_nodes,
            structured_node_count=structured_nodes,
            mesh_nodes_per_km2=(mesh_nodes/area*1e6 if area > 0 else 0.0),
            structured_nodes_per_km2=(structured_nodes/area*1e6 if area > 0 else 0.0),
            graph_node_delta=mesh_nodes-structured_nodes))
    if (sum(z['mesh_node_count'] for z in zone_rows) != len(triangles)
            or sum(z['structured_node_count'] for z in zone_rows) != structured_node_count):
        raise ValueError('comparison spatial zone node counts differ from global totals')
    report['spatial_zones'] = dict(rows=zone_rows,
        allocation_rule='each accepted triangle or target cell is assigned once by its representative point; each native source pixel with positive-area ROI intersection is assigned once by the representative point of that clipped pixel geometry; row 0 is south and column 0 is west',
        relief_alignment='not_assessed')
    # Keep geometric mesh vertices visibly distinct from cell-centred graph
    # nodes. This is a structural count, not another graph-node count.
    unique_vertices = set()
    for cell_index, cell in enumerate(frozen.cells):
        if cell_index % 128 == 0:
            checkpoint()
        unique_vertices.update(cell.xy)
    report['geometry_counts'] = dict(
        unstructured_unique_vertex_count=len(unique_vertices),
        unstructured_vertex_definition='unique accepted mesh coordinate pairs; graph nodes remain one per triangle cell')
    from .comparison_visualization import comparison_visualization
    report['visualization'] = comparison_visualization(
        domain=domain,
        structured=squares if baseline_mode == 'target_grid' else native_preview_polygons,
        structured_total_count=(len(squares) if baseline_mode == 'target_grid'
                                else native_counts['source_pixel_count']),
        unstructured=triangles,
        source_reason='Source raster values were measured, but no spatial thumbnail is included in this report.',
    )
    return report
