"""Optional, explicit GIS and target-field evidence for the shared QA module."""
import hashlib
import json
import math

import numpy as np
from shapely import hausdorff_distance
from shapely.geometry import Polygon, shape, mapping
from shapely.ops import unary_union
from shapely.errors import GEOSException

from .job_control import checkpoint, observe
from .spatial import metric_crs

CONTEXT_DEFAULTS = dict(boundary_relative_tolerance=1e-8,
    boundary_distance_tolerance_m=.01, boundary_densify=.25, size_tolerance=.25)


def context_config(config=None):
    values = dict(CONTEXT_DEFAULTS)
    if config:
        if not isinstance(config, dict) or set(config) - set(values):
            raise ValueError('Unsupported QA context settings')
        values.update(config)
    for key, value in values.items():
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ValueError(f'{key} must be finite and nonnegative')
    if not .05 <= values['boundary_densify'] <= 1:
        raise ValueError('Boundary densify must be 0.05..1')
    if values['boundary_relative_tolerance'] > 1 or values['size_tolerance'] > 10:
        raise ValueError('QA context tolerance exceeds supported range')
    return values


def reference_hash(roi=None, size_field=None, config=None):
    return hashlib.sha256(json.dumps(dict(roi=roi, size_field=size_field,
        config=context_config(config)), sort_keys=True, allow_nan=False,
        separators=(',', ':')).encode()).hexdigest()


def _unavailable(reason):
    return dict(status='NOT_AVAILABLE', reason=reason)


def _statistics(values):
    if not values:
        return dict(status='NOT_AVAILABLE', valid_count=0, reason='No valid samples')
    array = np.asarray(values, dtype=float)
    return dict(status='PASS', valid_count=len(values), min=float(array.min()),
        p5=float(np.percentile(array, 5)), median=float(np.median(array)),
        mean=float(np.sum(array/len(array))), p95=float(np.percentile(array, 95)),
        p99=float(np.percentile(array, 99)), max=float(array.max()))


def enrich_quality(result, mesh, *, roi=None, size_field=None, config=None):
    """Attach optional evidence; never transform/relabel or alter mesh geometry."""
    cfg = context_config(config)
    result['context_config'] = cfg
    result['references'] = dict(roi=roi, size_field=size_field)
    result['reference_hash'] = reference_hash(roi, size_field, cfg)
    records = {str(row['element_id']): row for row in result['elements']}
    cells = mesh.get('cells', [])
    metric = True
    try:
        crs = metric_crs(mesh.get('crs_wkt') or mesh.get('crs'))
    except (ValueError, TypeError):
        metric = False
    if roi is None:
        boundary = _unavailable('Original ROI was not retained for this mesh')
    elif not metric:
        boundary = _unavailable('Metre-based GIS fidelity requires an explicit projected metre CRS')
    else:
        boundary = _boundary(cells, records, roi, crs, cfg)
    result['boundary'] = boundary
    result['validation']['matches_input_domain'] = dict(status=boundary['status'],
        reason=boundary.get('reason'))
    result['size'] = _size(cells, records, size_field, crs if metric else None, cfg)
    ratios = [r['size_ratio'] for r in result['elements'] if r.get('size_ratio') is not None]
    if ratios:
        counts, edges = np.histogram(ratios, bins=10)
        result['histograms']['size_ratio'] = dict(status='AVAILABLE', edges=edges.tolist(), counts=counts.tolist(),
            valid_count=len(ratios), excluded_count=len(result['elements'])-len(ratios))
        result['statistics']['size_ratio'] = _statistics(ratios)
    if boundary['status'] == 'FAIL':
        result['issues'].append(dict(code='BOUNDARY_MISMATCH', category='BOUNDARY',
            severity='error', message='Mesh support differs from the retained original ROI',
            element_ids=[], node_ids=[], details=boundary))
    if boundary['status'] == 'FAIL':
        result['status'] = 'FAIL'
    elif any(item['status'] in {'WARNING', 'ERROR'} for item in (boundary, result['size'])):
        if result['status'] in {'PASS','PARTIAL'}:
            result['status'] = 'WARNING'
    unavailable=[name for name,assessment in (('boundary',boundary),('target_size',result['size']))
                 if assessment['status'] in {'NOT_AVAILABLE','ERROR'}]
    if result['validation'].get('structurally_valid',{}).get('orphan_nodes')=='NOT_AVAILABLE':
        unavailable.append('original_orphan_nodes')
    result['completeness']=dict(status='PARTIAL' if unavailable else 'COMPLETE',
        unavailable_checks=unavailable,interpretation='Performed checks and evidence completeness are reported separately')
    if unavailable and result['status']=='PASS':result['status']='PARTIAL'
    return result


def _boundary(cells, records, reference, crs, cfg):
    observe('quality_boundary')
    if not isinstance(reference,dict):
        return dict(status='ERROR',reason='ROI reference must be a mapping')
    try:
        if metric_crs(reference.get('crs_wkt') or reference.get('crs')) != crs:
            return _unavailable('ROI and mesh CRS differ; no implicit reprojection performed')
        domain = shape(reference['geometry'])
        if domain.geom_type not in {'Polygon', 'MultiPolygon'} or not domain.is_valid or domain.area <= 0:
            return dict(status='ERROR', reason='Original ROI must be a valid nonempty polygon')
        pieces, batches, excluded = [], [], 0
        for index, cell in enumerate(cells):
            if index % 128 == 0:
                checkpoint()
            record = records.get(str(cell['cell_id']))
            if not record or not record.get('valid'):
                excluded += 1
                continue
            pieces.append(Polygon(cell['xy']))
            if len(pieces) == 512:
                batches.append(unary_union(pieces)); pieces = []
        if pieces:
            batches.append(unary_union(pieces))
        checkpoint()
        union = unary_union(batches)
        if union.is_empty:
            return dict(status='FAIL', reason='No valid polygon support remains', excluded_count=excluded)
        missing, extra = domain.difference(union).area, union.difference(domain).area
        relative = (missing + extra) / domain.area
        # GEOS computes a discrete/densified approximation, not continuous Hausdorff.
        vertex_counts = [sum(len(p.exterior.coords) + sum(len(r.coords) for r in p.interiors)
                       for p in (item.geoms if item.geom_type == 'MultiPolygon' else [item]))
                       for item in (union, domain)]
        vertices=sum(vertex_counts)
        estimated_pairs=vertex_counts[0]*vertex_counts[1]/cfg['boundary_densify']**2
        distance = None
        method = 'GEOS symmetric discrete boundary Hausdorff with densify fraction'
        distance_status, reason = 'PASS', None
        if vertices / cfg['boundary_densify'] > 2_000_000 or estimated_pairs>20_000_000:
            distance_status, reason = 'NOT_AVAILABLE', 'Densified boundary distance work exceeds supported sample/pair budget'
        else:
            checkpoint()
            distance = float(hausdorff_distance(domain.boundary, union.boundary,
                                               densify=cfg['boundary_densify']))
            if not math.isfinite(distance):
                distance, distance_status, reason = None, 'ERROR', 'Boundary distance is nonfinite'
        failed = relative > cfg['boundary_relative_tolerance']
        if distance is not None:
            failed |= distance > cfg['boundary_distance_tolerance_m']
        return dict(status='FAIL' if failed else ('WARNING' if distance_status != 'PASS' else 'PASS'),
            missing_area=missing, extra_area=extra, symmetric_difference_area=missing+extra,
            relative_area_error=relative, coverage_ratio=domain.intersection(union).area/domain.area,
            roi_area=domain.area, mesh_union_area=union.area, excluded_count=excluded,
            hausdorff_distance=distance, hausdorff_status=distance_status,
            coordinate_units='m', area_units='m²', method=method,
            densify=cfg['boundary_densify'], reason=reason,
            limitation='ROI fidelity measures agreement with the supplied geometry, not ground-truth positional accuracy')
    except (ValueError, KeyError, TypeError, GEOSException) as error:
        return dict(status='ERROR', reason=str(error))


def _size(cells, records, field, crs, cfg):
    if field is None:
        return _unavailable('Original target-size field is unavailable; no target inferred from actual edges')
    if crs is None:
        return _unavailable('Target-size measurement requires a projected metre CRS')
    if not isinstance(field,dict) or not isinstance(field.get('target_sizes',{}),dict):
        return dict(status='ERROR',reason='Target size reference must be a mapping')
    try:
        if metric_crs(field.get('crs_wkt') or field.get('crs')) != crs:
            return _unavailable('Target field CRS differs from mesh CRS')
        definition = field.get('definition')
        if definition not in {'gmsh_characteristic_length', 'target_maximum_edge_length'}:
            return _unavailable('Target field size convention is not supported')
        uniform, targets = field.get('uniform'), field.get('target_sizes', {})
        ratios, missing, actual, edges, adjacent = [], 0, {}, {}, []
        for index, cell in enumerate(cells):
            if index % 128 == 0:
                checkpoint()
            record = records.get(str(cell['cell_id']))
            if not record or not record.get('valid') or record.get('longest_edge') is None:
                continue
            h = targets.get(str(cell['cell_id']), uniform)
            if type(h) not in (int, float) or not math.isfinite(h) or h <= 0:
                missing += 1
                continue
            ratio = record['longest_edge'] / h
            if not math.isfinite(ratio):
                missing += 1
                continue
            record.update(target_size=h, size_ratio=ratio)
            ratios.append(ratio)
            actual[str(cell['cell_id'])] = math.sqrt(record['area'])
            points = [tuple(point) for point in cell['xy']]
            for a, b in zip(points, points[1:]+points[:1]):
                edge = tuple(sorted((a, b)))
                other = edges.get(edge)
                if other is not None and other in actual:
                    x, y = actual[other], actual[str(cell['cell_id'])]
                    adjacent.append(max(x,y)/min(x,y))
                else:
                    edges[edge] = str(cell['cell_id'])
        if not ratios:
            return _unavailable('No valid target samples cover the evaluated elements')
        exceed = sum(ratio > 1 + cfg['size_tolerance'] for ratio in ratios)
        return dict(status='WARNING' if exceed or missing else 'PASS',
            definition='Lmax / target h sampled at element centroid',
            target_convention=definition, coordinate_units='m',
            interpretation=('diagnostic only; Gmsh characteristic length is not a guaranteed maximum edge'
                            if definition=='gmsh_characteristic_length' else 'requested maximum-edge conformity'),
            statistics=_statistics(ratios), exceed_fraction=exceed/len(ratios),
            exceed_count=exceed, missing_target_count=missing, tolerance=cfg['size_tolerance'],
            gradation=dict(definition='max(sqrt(area_i),sqrt(area_j))/min(sqrt(area_i),sqrt(area_j)) on shared edges',
                           statistics=_statistics(adjacent)),
            source_sha256=field.get('source_sha256'))
    except (ValueError, KeyError, TypeError) as error:
        return dict(status='ERROR', reason=str(error))
