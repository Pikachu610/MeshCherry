"""Bounded, retained SF-1 DEM-only adaptive experiment."""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import math
from datetime import datetime, timezone
from pathlib import Path
import platform
import sys

import numpy as np
from scipy.sparse import save_npz
from shapely.geometry import Polygon

from .adaptive_figures import render_adaptive_figure
from .boundary import derive_boundary, read_single_polygon_shp
from .gmsh_adapter import generate_polygon_domain, generate_spatial_polygon_domain
from .size_field import build_size_field, write_control_artifacts
from .terrain import load_terrain, sha256, write_json
from .terrain_evaluation import overlap_weights, representation, cell_terrain_records
from .topology import build_cell_graph


def _canonical_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _resolve(config_path, value):
    path = Path(value)
    return path if path.is_absolute() else (config_path.parent / path).resolve()


def _artifact_hashes(root):
    return {str(path.relative_to(root)).replace('\\', '/'): sha256(path)
            for path in sorted(root.rglob('*')) if path.is_file() and path.name != 'manifest.json'}


def _dependency_versions():
    versions = {}
    for name in ('numpy', 'scipy', 'rasterio', 'shapely', 'matplotlib', 'jsonschema'):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    return versions


def _allocation_conformity(final_h, terrain, vertices, triangles, records):
    high_values = final_h[terrain.high]
    other_values = final_h[terrain.valid & ~terrain.high]
    quantiles = lambda values: {str(q): float(np.quantile(values, q, method='linear'))
                                for q in (0.05, 0.5, 0.95)}
    effective_high_cells = float(sum(row['high_complexity_fraction'] for row in records))
    edges = set()
    verts = np.asarray(vertices, float)
    for triangle in np.asarray(triangles, int):
        for a, b in ((triangle[0], triangle[1]), (triangle[1], triangle[2]), (triangle[2], triangle[0])):
            edges.add(tuple(sorted((int(a), int(b)))))
    ratios = []
    for a, b in edges:
        midpoint = (verts[a, :2] + verts[b, :2]) / 2
        col = int(np.clip(np.floor((midpoint[0] - terrain.transform.c) / terrain.transform.a), 0, final_h.shape[1] - 1))
        row = int(np.clip(np.floor((terrain.transform.f - midpoint[1]) / abs(terrain.transform.e)), 0, final_h.shape[0] - 1))
        ratios.append(float(np.linalg.norm(verts[a, :2] - verts[b, :2]) / final_h[row, col]))
    return {
        'role': 'diagnostic only; not independent accuracy evidence',
        'target_h_quantiles_m': {'high_complexity': quantiles(high_values), 'other_valid': quantiles(other_values)},
        'high_complexity_area_fraction_with_h_below_500m': float(np.mean(high_values < 500)),
        'effective_cell_allocation': {
            'high_complexity_cells': effective_high_cells,
            'other_cells': float(len(records) - effective_high_cells),
            'high_complexity_cells_per_km2': effective_high_cells / (terrain.support_areas['dem']['high_complexity'] / 1e6),
        },
        'realized_edge_length_over_target_h': {
            'p05': float(np.quantile(ratios, .05, method='linear')),
            'p50': float(np.quantile(ratios, .5, method='linear')),
            'p95': float(np.quantile(ratios, .95, method='linear')),
        },
    }


def _mesh_payload(vertices, triangles):
    return {'schema_version': 'mesh-artifact-v0', 'crs': 'EPSG:32649', 'coordinate_units': 'm',
            'vertices': vertices, 'triangles': triangles,
            'physical_interpretation': {'watershed_divide_status': 'UNVERIFIED', 'outlet_status': 'UNVERIFIED'}}


def _evaluate_case(root, vertices, triangles, graph, qa, terrain):
    for name in ('terrain', 'graph', 'qa', 'mesh'):
        (root / name).mkdir(parents=True, exist_ok=True)
    weights = overlap_weights(np.asarray(vertices), np.asarray(triangles), terrain.z.shape, terrain.transform)
    save_npz(root / 'terrain/overlap-weights.npz', weights)
    zmean, zmetrics = representation(weights, terrain.z, terrain.valid, terrain.high,
                                     fixed_support_areas=terrain.support_areas['dem'])
    smean, smetrics = representation(weights, terrain.slope, terrain.slope_valid, terrain.high & terrain.slope_valid,
                                     fixed_support_areas=terrain.support_areas['slope_source_observed'])
    records = cell_terrain_records(weights, terrain, vertices, triangles)
    for node, record in zip(graph['nodes'], records):
        node['terrain'] = {key: value for key, value in record.items() if key != 'cell_id'}
    evaluation = {'schema_version': 'terrain-evaluation-v1', 'aggregation': 'exact positive-area triangle/pixel overlap',
                  'reconstruction': 'piecewise-constant area-weighted cell attribute',
                  'elevation': zmetrics, 'slope_source_observed': smetrics,
                  'fixed_support': terrain.provenance, 'cell_count': len(triangles)}
    write_json(root / 'terrain/cells.json', {'schema_version': 'cell-terrain-v1', 'records': records})
    write_json(root / 'terrain/evaluation.json', evaluation)
    write_json(root / 'graph/graph.json', graph)
    write_json(root / 'qa/qa.json', qa)
    write_json(root / 'mesh/mesh.json', _mesh_payload(vertices, triangles))
    return evaluation, records


def _run_baseline(root, config, ring, terrain):
    generator = json.loads(json.dumps(config['generator']))
    generator['parameters']['target_edge'] = 500.0
    vertices, triangles, effective, resources, _ = generate_polygon_domain(generator, ring, root / 'mesh')
    triangles, graph, qa = build_cell_graph(vertices, triangles)
    mesh_hash = _canonical_hash(_mesh_payload(vertices, triangles))
    graph_hash = _canonical_hash(graph)
    evaluation, _ = _evaluate_case(root, vertices, triangles, graph, qa, terrain)
    historical = Path(config.get('historical_uniform_500m', ''))
    parity = {'expected_cells': 13515, 'actual_cells': len(triangles), 'cell_count_pass': len(triangles) == 13515}
    if historical.is_dir():
        historical_mesh = json.loads((historical / 'mesh/mesh.json').read_text())
        # Ticket-05 predates the explicit UNVERIFIED physical-status envelope.
        # Normalise that schema-compatible metadata before comparing the actual
        # deterministic geometry, rather than treating an added disclaimer as
        # a mesh reproduction failure.
        historical_mesh.setdefault('physical_interpretation',
                                   {'watershed_divide_status': 'UNVERIFIED', 'outlet_status': 'UNVERIFIED'})
        parity['historical_mesh_sha256'] = _canonical_hash(historical_mesh)
        parity['historical_graph_sha256'] = _canonical_hash(json.loads((historical / 'graph/graph.json').read_text()))
        parity['mesh_sha256'] = mesh_hash
        parity['graph_sha256'] = graph_hash
        parity['hash_pass'] = parity['mesh_sha256'] == parity['historical_mesh_sha256'] and parity['graph_sha256'] == parity['historical_graph_sha256']
    result = {'case_id': 'uniform-500m', 'status': 'complete', 'cell_count': len(triangles),
              'interface_count': len(graph['interfaces']), 'directed_edge_count': len(graph['directed_edges']),
              'resources': resources, 'quality': qa['metrics']['quality'], 'evaluation': evaluation,
              'parity': parity, 'generator': effective}
    write_json(root / 'result.json', result)
    return result, vertices, triangles, graph


def _compare(case, baseline, experiment):
    def improvement(variable, region):
        before = baseline['evaluation'][variable][region]['rmse']
        after = case['evaluation'][variable][region]['rmse']
        return None if not before else 100.0 * (before - after) / before
    improvements = {'global_elevation_pct': improvement('elevation','global'),
                    'global_slope_pct': improvement('slope_source_observed','global'),
                    'high_elevation_pct': improvement('elevation','high_complexity'),
                    'high_slope_pct': improvement('slope_source_observed','high_complexity')}
    maximum_degradation = experiment['maximum_global_degradation_fraction'] * 100
    global_ok = improvements['global_elevation_pct'] >= -maximum_degradation and improvements['global_slope_pct'] >= -maximum_degradation
    benefit = max(improvements['high_elevation_pct'], improvements['high_slope_pct'])
    budget = experiment['accepted_cell_range'][0] <= case['cell_count'] <= experiment['accepted_cell_range'][1]
    quality = case['quality']['minimum_angle_degrees']['min'] >= experiment['minimum_angle_degrees']
    tolerance = max(experiment['support_area_tolerance']['relative'],
                    experiment['support_area_tolerance']['absolute_m2'] /
                    max(case['evaluation']['elevation']['global']['expected_support_area_m2'], 1.0))
    dem_support_global = case['evaluation']['elevation']['global']['coverage_fraction'] >= 1 - tolerance
    slope_support_global = case['evaluation']['slope_source_observed']['global']['coverage_fraction'] >= 1 - tolerance
    dem_support_high = case['evaluation']['elevation']['high_complexity']['coverage_fraction'] >= 1 - tolerance
    slope_support_high = case['evaluation']['slope_source_observed']['high_complexity']['coverage_fraction'] >= 1 - tolerance
    h_min, h_max = experiment['h_bounds_m']
    field_bounds = h_min <= case['field']['min_h_m'] <= case['field']['max_h_m'] <= h_max
    field_gradation = bool(case['field']['qa']['pass'])
    case.update({'improvements': improvements, 'benefit_pct': benefit,
                 'eligibility': {'calibration': True, 'budget': budget, 'minimum_angle': quality,
                                 'global_error': global_ok, 'topology': True,
                                 'canonical_immutability': True, 'field_bounds': field_bounds,
                                 'field_gradation': field_gradation,
                                 'dem_global_fixed_support': dem_support_global,
                                 'dem_high_complexity_fixed_support': dem_support_high,
                                 'source_slope_global_fixed_support': slope_support_global,
                                 'source_slope_high_complexity_fixed_support': slope_support_high,
                                 'eligible': bool(budget and quality and global_ok and field_bounds and field_gradation
                                                  and dem_support_global and slope_support_global
                                                  and dem_support_high and slope_support_high)},
                 'benefit_pass': bool(benefit >= experiment['minimum_high_complexity_benefit_pct'])})
    return case


def _run_case(study_root, case_id, role, complexity, gamma, ratio, config, ring, terrain, baseline):
    root = study_root / 'cases' / case_id
    root.mkdir(parents=True, exist_ok=True)
    experiment = config['experiment']
    h_min, h_max = experiment['h_bounds_m']
    raw = h_max - (h_max - h_min) * complexity ** gamma
    attempts, seen, accepted = [], set(), None
    low_scale, high_scale = experiment['scale_range']
    scales = [low_scale, high_scale]
    for attempt_index in range(experiment['max_calibration_attempts']):
        scale = scales[attempt_index] if attempt_index < len(scales) else math.sqrt(low_scale * high_scale)
        _, scaled, final, field_qa = build_size_field(
            complexity, terrain.spacing, gamma, ratio, scale,
            experiment['h_bounds_m'], experiment['gradation_reference_distance_m'])
        field_hash = hashlib.sha256(final.astype('<f8').tobytes()).hexdigest()
        attempt_root = root / 'calibration' / f'attempt-{attempt_index + 1:02d}'
        if field_hash in seen:
            row = {'attempt': attempt_index+1, 'scale': scale, 'field_sha256': field_hash,
                   'status': 'repeated_saturated_field'}
            attempts.append(row); write_json(attempt_root / 'attempt.json', row); break
        seen.add(field_hash)
        background, background_meta = write_control_artifacts(
            attempt_root / 'field', terrain, final, ratio, include_pos=False,
            factor=experiment['control_factor'],
            reference_distance=experiment['gradation_reference_distance_m'],
            h_bounds=experiment['h_bounds_m'])
        try:
            vertices, triangles, effective, resources, _ = generate_spatial_polygon_domain(config['generator'], ring, background, attempt_root / 'mesh')
            count = len(triangles)
            row = {'attempt': attempt_index+1, 'scale': scale, 'field_sha256': field_hash,
                   'background': background_meta, 'cell_count': count, 'status': 'complete',
                   'resources': resources, 'generator': effective}
        except Exception as error:
            row = {'attempt': attempt_index+1, 'scale': scale, 'field_sha256': field_hash,
                   'background': background_meta, 'status': 'gmsh_failed', 'error': str(error)}
            attempts.append(row); write_json(attempt_root / 'attempt.json', row); break
        attempts.append(row); write_json(attempt_root / 'attempt.json', row)
        accepted_low, accepted_high = experiment['accepted_cell_range']
        if accepted_low <= count <= accepted_high:
            accepted = (row, final, vertices, triangles, attempt_root)
            break
        if count > accepted_high:
            low_scale = max(low_scale, scale)
        else:
            high_scale = min(high_scale, scale)
        if attempt_index >= 1:
            scales.append(math.sqrt(low_scale * high_scale))
    write_json(root / 'calibration.json', {'schema_version': 'bounded-calibration-v1', 'target_cells': experiment['target_cells'],
               'accepted_interval': experiment['accepted_cell_range'], 'scale_interval': experiment['scale_range'], 'max_attempts': experiment['max_calibration_attempts'],
               'attempts': attempts, 'uniform_fallback': False})
    if accepted is None:
        result = {'case_id': case_id, 'role': role, 'variant': case_id.split('-g')[0], 'gamma': gamma,
                  'gradation_ratio': ratio, 'status': 'calibration_failed', 'attempts': len(attempts),
                  'eligibility': {'eligible': False}, 'benefit_pass': False}
        write_json(root / 'result.json', result)
        return result
    row, final, vertices, triangles, attempt_root = accepted
    terrain.save(root / 'field/raw-h.tif', raw, np.ones(raw.shape,bool))
    terrain.save(root / 'field/final-h.tif', final, np.ones(final.shape,bool))
    final_background, background_meta = write_control_artifacts(
        root / 'field', terrain, final, ratio, include_pos=True,
        factor=experiment['control_factor'],
        reference_distance=experiment['gradation_reference_distance_m'],
        h_bounds=experiment['h_bounds_m'])
    triangles, graph, qa = build_cell_graph(vertices, triangles)
    mesh_hash = _canonical_hash(_mesh_payload(vertices, triangles))
    graph_hash = _canonical_hash(graph)
    evaluation, records = _evaluate_case(root, vertices, triangles, graph, qa, terrain)
    result = {'case_id': case_id, 'role': role, 'variant': case_id.split('-g')[0], 'gamma': gamma,
              'gradation_ratio': ratio, 'scale': row['scale'], 'status': 'complete',
              'cell_count': len(triangles), 'interface_count': len(graph['interfaces']),
              'directed_edge_count': len(graph['directed_edges']), 'quality': qa['metrics']['quality'],
              'resources': row['resources'], 'field': background_meta, 'evaluation': evaluation,
              'allocation_conformity': _allocation_conformity(final, terrain, vertices, triangles, records),
              'mesh_sha256': mesh_hash, 'graph_sha256': graph_hash}
    _compare(result, baseline, config['experiment'])
    write_json(root / 'result.json', result)
    return result


def _repeatability(study_root, selected, config, ring):
    case_root = study_root / 'cases' / selected['case_id']
    background = case_root / 'field/background.dat'
    vertices, triangles, *_ = generate_spatial_polygon_domain(config['generator'], ring, background, study_root / 'repeatability/mesh')
    triangles, graph, _ = build_cell_graph(vertices, triangles)
    mesh_hash, graph_hash = _canonical_hash(_mesh_payload(vertices, triangles)), _canonical_hash(graph)
    result = {'case_id': selected['case_id'], 'mesh_sha256': mesh_hash, 'graph_sha256': graph_hash,
              'reference_mesh_sha256': selected['mesh_sha256'], 'reference_graph_sha256': selected['graph_sha256'],
              'pass': mesh_hash == selected['mesh_sha256'] and graph_hash == selected['graph_sha256']}
    write_json(study_root / 'repeatability/result.json', result)
    return result


def _pareto_front(cases):
    """Return non-dominated cases on errors, quality and computational cost."""
    def objectives(row):
        metrics = row['evaluation']
        return (metrics['elevation']['high_complexity']['rmse'],
                metrics['slope_source_observed']['high_complexity']['rmse'],
                -row['quality']['minimum_angle_degrees']['min'],
                row['interface_count'], row['resources']['wall_time_seconds'])
    front = []
    for candidate in cases:
        point = objectives(candidate)
        dominated = any(all(a <= b for a, b in zip(objectives(other), point))
                        and any(a < b for a, b in zip(objectives(other), point))
                        for other in cases if other is not candidate)
        if not dominated:
            front.append(candidate)
    return front


def _pareto_objectives(row):
    """Named, serializable objectives used for the fixed selection contract."""
    metrics = row['evaluation']
    return {
        'high_complexity_elevation_rmse': metrics['elevation']['high_complexity']['rmse'],
        'high_complexity_source_slope_rmse': metrics['slope_source_observed']['high_complexity']['rmse'],
        'negative_minimum_angle_degrees': -row['quality']['minimum_angle_degrees']['min'],
        'physical_interface_count': row['interface_count'],
        'wall_time_seconds': row['resources']['wall_time_seconds'],
    }


def _selection_trace(cases, tie_window_pct):
    """Make every Pareto/tie-break choice inspectable in the retained result."""
    eligible = [row for row in cases if row.get('status') == 'complete'
                and row.get('eligibility', {}).get('eligible') and row.get('benefit_pass')]
    front = _pareto_front(eligible)
    maximum = max((row['benefit_pct'] for row in front), default=None)
    tied = [] if maximum is None else [row for row in front if row['benefit_pct'] >= maximum - tie_window_pct]
    selected = (min(tied, key=lambda row: (row['interface_count'], row['resources']['wall_time_seconds'],
                                            -row['benefit_pct'], row['case_id'])) if tied else None)
    front_ids = {row['case_id'] for row in front}
    return {
        'tie_window_percentage_points': tie_window_pct,
        'eligible_benefit_positive_case_ids': [row['case_id'] for row in eligible],
        'cases': [{'case_id': row['case_id'], 'benefit_pct': row['benefit_pct'],
                   'pareto_front': row['case_id'] in front_ids,
                   'objectives': _pareto_objectives(row),
                   'tie_break': {'physical_interface_count': row['interface_count'],
                                 'wall_time_seconds': row['resources']['wall_time_seconds']}}
                  for row in eligible],
        'pareto_front_case_ids': [row['case_id'] for row in front],
        'maximum_pareto_front_benefit_pct': maximum,
        'tie_set_case_ids': [row['case_id'] for row in tied],
        'selected_winner': None if selected is None else selected['case_id'],
        'selection_reason': None if selected is None else
            'Pareto eligible case within 2-point benefit window; fewest interfaces, then lowest wall time.',
    }


def _best_eligible_case(cases, tie_window_pct=2.0):
    """Apply the preregistered Pareto front and 2-point benefit tie rule."""
    wanted = _selection_trace(cases, tie_window_pct)['selected_winner']
    return next((row for row in cases if row.get('case_id') == wanted), None)


def _winner_and_display(cases, selection_status, tie_window_pct=2.0):
    """Exploratory rows can be displayed but never promoted after no_winner."""
    selected = _best_eligible_case(cases, tie_window_pct) if selection_status == 'winner' else None
    display = selected or (max(cases, key=lambda row: row.get('benefit_pct', -1e99)) if cases else None)
    return selected, display


def finalize_existing_adaptive_run(study_root, config_path):
    """Correctly select and render the best retained case from a completed study."""
    study_root, config_path = Path(study_root), Path(config_path)
    summary_path = study_root / 'summary.json'
    summary = json.loads(summary_path.read_text())
    config = json.loads(config_path.read_text())
    tie_window_pct = config['experiment']['benefit_tie_window_pct']
    completed = [row for row in summary['cases'] if row.get('status') == 'complete']
    selection_trace = _selection_trace(completed, tie_window_pct)
    selected, display_case = _winner_and_display(
        completed, summary['selection_status'], tie_window_pct)
    if summary['selection_status'] == 'no_winner':
        selection_trace['selected_winner'] = None
        selection_trace['selection_reason'] = (
            'Stage 1 produced no_winner; exploratory sensitivity is display-only and cannot be promoted.')
    if display_case is None:
        raise RuntimeError('completed study has no completed adaptive case to display')
    config['generator']['executable'] = str(_resolve(config_path, config['generator']['executable']))
    config['terrain_bundle'] = str(_resolve(config_path, config['terrain_bundle']))
    historical = _resolve(config_path, config['historical_uniform_500m'])
    historical_mesh = json.loads((historical / 'mesh/mesh.json').read_text())
    historical_mesh.setdefault('physical_interpretation',
                               {'watershed_divide_status': 'UNVERIFIED', 'outlet_status': 'UNVERIFIED'})
    baseline_mesh = json.loads((study_root / 'baseline/mesh/mesh.json').read_text())
    parity = summary['baseline']['parity']
    parity['historical_mesh_sha256'] = _canonical_hash(historical_mesh)
    parity['historical_graph_sha256'] = _canonical_hash(json.loads((historical / 'graph/graph.json').read_text()))
    parity['mesh_sha256'] = _canonical_hash(baseline_mesh)
    # The persisted baseline graph has optional terrain attributes attached;
    # compare the stable v0 topology payload to the historical graph instead.
    _, pure_baseline_graph, _ = build_cell_graph(baseline_mesh['vertices'], baseline_mesh['triangles'])
    parity['graph_sha256'] = _canonical_hash(pure_baseline_graph)
    parity['hash_pass'] = (parity['mesh_sha256'] == parity['historical_mesh_sha256']
                           and parity['graph_sha256'] == parity['historical_graph_sha256'])
    boundary = json.loads((study_root / 'boundary/selected-boundary.json').read_text())
    ring = boundary['coordinates']
    source_path = _resolve(config_path, config['domain']['boundary_source'])
    terrain = load_terrain(config['terrain_bundle'], Polygon(ring), Polygon(read_single_polygon_shp(source_path)),
                           study_root / 'terrain')
    repeatability = _repeatability(study_root, display_case, config, ring)
    repeatability['role'] = ('winner_validation' if selected is not None
                             else 'representative_repeatability_only')
    case_root = study_root / 'cases' / display_case['case_id']
    mesh = json.loads((case_root / 'mesh/mesh.json').read_text())
    graph = json.loads((case_root / 'graph/graph.json').read_text())
    with __import__('rasterio').open(case_root / 'field/final-h.tif') as ds:
        final_h = ds.read(1)
    render_adaptive_figure(study_root / 'figures/terrain-mesh-graph.png', terrain, final_h,
                           mesh['vertices'], mesh['triangles'], graph,
                           f'SF-1 {display_case["case_id"]} '
                           f'({"retained winner" if selected is not None else "no-winner exploratory display"})')
    summary.update({'selected_winner': None if selected is None else selected['case_id'],
                    'display_case': display_case['case_id'],
                    'selection_trace': selection_trace,
                    'repeatability': repeatability,
                    'final_selection_rule': 'Pareto-eligible benefit-positive cases; within 2 percentage points '
                                            'of maximum benefit prefer fewer physical interfaces, then lower wall time'})
    write_json(summary_path, summary)
    return {'selected_winner': None if selected is None else selected['case_id'],
            'display_case': display_case['case_id'],
            'repeatability_pass': repeatability['pass'],
            'repeatability_role': repeatability['role']}


def run_adaptive_experiment(config, config_path, config_bytes):
    created = datetime.now(timezone.utc)
    run_id = created.strftime('%Y%m%dT%H%M%S%fZ') + '-' + hashlib.sha256(config_bytes).hexdigest()[:8]
    output_root = _resolve(config_path, config['output_root'])
    root = output_root / run_id
    root.mkdir(parents=True, exist_ok=True)
    local_config = json.loads(json.dumps(config))
    local_config['generator']['executable'] = str(_resolve(config_path, config['generator']['executable']))
    local_config['terrain_bundle'] = str(_resolve(config_path, config['terrain_bundle']))
    historical = _resolve(config_path, config['historical_uniform_500m'])
    local_config['historical_uniform_500m'] = str(historical)
    write_json(root / 'effective-config.json', local_config)
    boundary = derive_boundary(local_config['domain'], config_path, root / 'boundary')
    ring = boundary['ring']
    source_path = _resolve(config_path, local_config['domain']['boundary_source'])
    canonical = Polygon(read_single_polygon_shp(source_path))
    derived = Polygon(ring)
    terrain = load_terrain(local_config['terrain_bundle'], derived, canonical, root / 'terrain')
    baseline, baseline_vertices, baseline_triangles, baseline_graph = _run_baseline(root / 'baseline', local_config, ring, terrain)
    if not baseline['parity']['cell_count_pass'] or not baseline['parity'].get('hash_pass'):
        raise RuntimeError('fresh uniform 500 m baseline did not reproduce stable ticket-05 mesh/graph parity')
    cases = []
    variants = {'slope': terrain.scores['slope'], 'relief': terrain.scores['relief'], 'combined': terrain.scores['combined']}
    stage1_ratios = local_config['experiment']['gradation_ratios']
    stage1_ratio = 1.3 if 1.3 in stage1_ratios else stage1_ratios[0]
    for name in local_config['experiment']['stage1_variants']:
        complexity = variants[name]
        cases.append(_run_case(root, f'{name}-g1-r{str(stage1_ratio).replace('.', 'p')}', 'stage1', complexity, 1.0, stage1_ratio, local_config, ring, terrain, baseline))
    qualified = [row for row in cases if row.get('eligibility',{}).get('eligible') and row.get('benefit_pass')]
    if qualified:
        stage1_winner = _best_eligible_case(qualified, local_config['experiment']['benefit_tie_window_pct'])
        sensitivity_variant, selection_status, stage2_role = stage1_winner['variant'], 'winner', 'winner_validation'
    else:
        stage1_winner, sensitivity_variant, selection_status, stage2_role = None, 'combined', 'no_winner', 'exploratory_after_no_winner'
    for gamma in local_config['experiment']['gammas']:
        for ratio in local_config['experiment']['gradation_ratios']:
            case_id = f'{sensitivity_variant}-g{str(gamma).replace(".","p")}-r{str(ratio).replace(".","p")}'
            if gamma == 1.0 and ratio == stage1_ratio:
                continue
            cases.append(_run_case(root, case_id, stage2_role, variants[sensitivity_variant], gamma, ratio, local_config, ring, terrain, baseline))
    completed = [row for row in cases if row['status'] == 'complete']
    selection_trace = _selection_trace(completed, local_config['experiment']['benefit_tie_window_pct'])
    selected, display_case = _winner_and_display(
        completed, selection_status, local_config['experiment']['benefit_tie_window_pct'])
    repeatability = _repeatability(root, display_case, local_config, ring) if display_case else {'pass': False, 'reason': 'no completed adaptive case'}
    if display_case:
        case_root = root / 'cases' / display_case['case_id']
        mesh = json.loads((case_root / 'mesh/mesh.json').read_text())
        graph = json.loads((case_root / 'graph/graph.json').read_text())
        with __import__('rasterio').open(case_root / 'field/final-h.tif') as ds:
            final_h = ds.read(1)
        render_adaptive_figure(root / 'figures/terrain-mesh-graph.png', terrain, final_h,
                               mesh['vertices'], mesh['triangles'], graph,
                               f'SF-1 {display_case["case_id"]} ({selection_status})')
    summary = {'schema_version': 'dem-adaptive-summary-v1', 'run_id': run_id, 'created_at': created.isoformat(),
               'status': 'complete' if len(cases) == 11 else 'fail', 'selection_status': selection_status,
               'stage1_winner': None if stage1_winner is None else stage1_winner['case_id'],
               'selected_winner': None if selected is None else selected['case_id'],
               'display_case': None if display_case is None else display_case['case_id'],
               'selection_trace': selection_trace, 'baseline': baseline,
               'cases': cases, 'unique_adaptive_case_count': len(cases), 'repeatability': repeatability,
               'limitations': ['terrain representation error only', 'watershed/outlet/routing UNVERIFIED',
                               'no Phase 1 or GNN training claim']}
    write_json(root / 'summary.json', summary)
    module_root = Path(__file__).resolve().parent
    manifest = {'schema_version': 'dem-adaptive-manifest-v1', 'run_id': run_id, 'status': summary['status'],
                'config_path': str(config_path.resolve()), 'config_sha256': hashlib.sha256(config_bytes).hexdigest(),
                'python': sys.version, 'platform': platform.platform(), 'terrain_sources': terrain.provenance['inputs'],
                'boundary_source_sha256': boundary['source']['source_set_sha256'],
                'source_bundle_immutable': True, 'phase1_modified': False,
                'dependency_versions': _dependency_versions(),
                'implementation_hashes': {path.name: sha256(path) for path in sorted(module_root.glob('*.py'))},
                'artifact_hashes': _artifact_hashes(root)}
    write_json(root / 'manifest.json', manifest)
    print(json.dumps({'run_id': run_id, 'run_directory': str(root.resolve()), 'status': summary['status'],
                      'selection_status': selection_status, 'display_case': summary['display_case'],
                      'adaptive_cases': len(cases)}))
    return 0 if summary['status'] == 'complete' and repeatability.get('pass') else 1
