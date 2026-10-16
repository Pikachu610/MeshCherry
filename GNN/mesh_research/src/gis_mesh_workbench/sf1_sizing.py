"""Bounded SF-1 relief profile, ported from the retained GNN research method.

The numerical kernels mirror mesh_research.terrain and mesh_research.size_field.
They live here because the research package is deliberately excluded from the
desktop distribution. This profile does not run the historical budget search.
"""
import json
import math
from pathlib import Path

import numpy as np
from rasterio.features import geometry_mask
from scipy import ndimage
from shapely.geometry import box, mapping

from .job_control import checkpoint, observe
from .raster_source import open_raster, snapshot, assert_unchanged, HASH_SCOPE
from .spatial import metric_crs

WINDOWS_M = (150, 450)
NATIVE_SPACING_M = 30.0
CONTROL_FACTOR = 4
GAMMA = 2.0
GRADATION_RATIO = 1.5
REFERENCE_DISTANCE_M = 250.0
H_BOUNDS_M = (250.0, 1000.0)
MAX_CONTROL_POINTS = 750000
MAX_SOURCE_PIXELS = 2000000


def policy():
    return dict(policy_version='gnn-sf1-relief-profile-v1',
        research_case='relief-g2p0-r1p5', predictor_windows_m=list(WINDOWS_M),
        predictor_window_pixels=[5, 15], percentile_positions=[25, 95],
        combination='maximum of separately normalized local-relief scores',
        gamma=GAMMA, h_bounds_m=list(H_BOUNDS_M),
        native_pixel_size_m=[30, 30], control_factor=CONTROL_FACTOR,
        elevation_units='m; explicit dem_value_units declaration and known band-unit check',
        control_spacing_m=120, gradation_ratio=GRADATION_RATIO,
        gradation_reference_distance_m=REFERENCE_DISTANCE_M,
        resampling='none; native aligned metric DEM',
        domain_coverage='selected ROI inside actual DEM affine footprint; control halo is interpolation only',
        normalization_support='valid source-pixel centres inside selected ROI',
        neighbourhood_support='all valid native DEM neighbours; excludes NoData',
        missing_target='raw h_max; gradation may propagate refinement',
        constant_relief_target='h_max before explicit scale and gradation',
        calibration='not performed; scale is an explicit input',
        max_source_pixels=MAX_SOURCE_PIXELS,
        boundary_simplification=False)


def inspect_source(src, band, analysis_crs):
    """Require the supported research grid instead of silently resampling it."""
    from .gmsh_meshing import inspect_relief_source
    inspect_relief_source(src, band)
    if src.width * src.height > MAX_SOURCE_PIXELS:
        raise ValueError('DEM SF-1 vượt giới hạn 2.000.000 pixel; chọn DEM nhỏ hơn')
    if src.crs != metric_crs(analysis_crs):
        raise ValueError('SF-1 yêu cầu DEM và miền cùng CRS mét; chuyển DEM trước khi dùng profile')
    declared_units = getattr(src, 'units', (None,) * src.count)[band - 1]
    if declared_units and declared_units.strip().lower() not in {'m', 'metre', 'metres', 'meter', 'meters'}:
        raise ValueError('SF-1 yêu cầu cao độ DEM đơn vị m; kiểm tra đơn vị band')
    t = src.transform
    if not (t.b == 0 and t.d == 0 and math.isclose(t.a, 30, abs_tol=1e-9)
            and math.isclose(t.e, -30, abs_tol=1e-9)):
        raise ValueError('Profile SF-1 yêu cầu DEM north-up, pixel vuông 30 × 30 m')
    if src.width < 5 or src.height < 5:
        raise ValueError('Profile SF-1 cần DEM ít nhất 5 × 5 pixel')
    points = (math.ceil(src.width / CONTROL_FACTOR) + 2) * (math.ceil(src.height / CONTROL_FACTOR) + 2)
    if points > MAX_CONTROL_POINTS:
        raise ValueError('Control field SF-1 vượt giới hạn 750.000 điểm; chọn DEM nhỏ hơn')


def inspect_coverage(src, domain):
    """The interpolation halo never grants extra source-raster coverage."""
    outside = domain.difference(box(*src.bounds)).area
    if outside > max(1e-8, domain.area * 1e-10):
        raise ValueError('Miền vượt phạm vi DEM SF-1; chọn DEM phủ toàn miền')


def normalize(values, valid):
    lo, hi = np.percentile(values[valid], [25, 95], method='linear')
    score = np.zeros(values.shape, dtype='float64')
    if hi > lo:
        score[valid] = np.clip((values[valid] - lo) / (hi - lo), 0, 1)
    return score, [float(lo), float(hi)]


def relief_scores(z, valid, normalization):
    scores, thresholds = [], {}
    for width in (5, 15):
        checkpoint()
        high = ndimage.maximum_filter(np.where(valid, z, -np.inf), size=width, mode='constant', cval=-np.inf)
        low = ndimage.minimum_filter(np.where(valid, z, np.inf), size=width, mode='constant', cval=np.inf)
        with np.errstate(invalid='ignore'):
            local_range = np.where(valid, high - low, 0)
        score, limits = normalize(local_range, normalization)
        scores.append(score)
        thresholds['relief' + str(width * 30)] = limits
    return np.maximum.reduce(scores), thresholds


def gradient_bound(values, spacing, denominator=None):
    dx = np.abs(np.diff(values, axis=1)) / spacing
    dy = np.abs(np.diff(values, axis=0)) / spacing
    bound = np.hypot(np.maximum(dx[:-1], dx[1:]), np.maximum(dy[:, :-1], dy[:, 1:]))
    if denominator is not None:
        bound /= denominator
    return float(np.max(bound))


def limit_field(h, spacing, logarithmic=True):
    """Conservative 4-neighbour envelope used in the established SF-1 kernels."""
    values = np.log(np.clip(h, *H_BOUNDS_M)) if logarithmic else np.asarray(h, float).copy()
    k = np.log(GRADATION_RATIO) / REFERENCE_DISTANCE_M
    step = k * spacing / np.sqrt(2) * (1 if logarithmic else H_BOUNDS_M[0])
    footprint = np.array([[0, 1, 0], [1, 1, 1], [0, 1, 0]], bool)
    maximum = int(np.ceil(np.log(H_BOUNDS_M[1] / H_BOUNDS_M[0]) / step)) + 2 if logarithmic else 10000
    for iteration in range(maximum):
        checkpoint()
        updated = np.minimum(values, ndimage.minimum_filter(values, footprint=footprint, mode='nearest') + step)
        change = float(np.max(values - updated))
        values = updated
        if change < (1e-12 if logarithmic else 1e-10):
            break
    denominator = None if logarithmic else np.minimum.reduce([
        values[:-1, :-1], values[:-1, 1:], values[1:, :-1], values[1:, 1:]])
    measured = gradient_bound(values, spacing, denominator)
    evidence = dict(ratio=GRADATION_RATIO, reference_distance_m=REFERENCE_DISTANCE_M,
        allowed_log_gradient_per_m=float(k), measured_bound_per_m=measured,
        iterations=iteration + 1, **{'pass': bool(measured <= k + 1e-12)})
    if not evidence['pass']:
        raise ValueError('SF-1 gradation limiter did not converge')
    return np.exp(values) if logarithmic else values, evidence


def build_controls(z, valid, normalization, transform, scale=1.0):
    if not normalization.any():
        raise ValueError('Miền SF-1 không có tâm pixel DEM hợp lệ')
    if not math.isfinite(scale) or not .25 <= scale <= 4:
        raise ValueError('SF-1 scale phải hữu hạn trong khoảng 0,25–4')
    complexity, thresholds = relief_scores(z, valid, normalization)
    raw = H_BOUNDS_M[1] - (H_BOUNDS_M[1] - H_BOUNDS_M[0]) * complexity ** GAMMA
    scaled = np.clip(scale * raw, *H_BOUNDS_M)
    final, native_qa = limit_field(scaled, NATIVE_SPACING_M)
    conservative = ndimage.minimum_filter(final, size=CONTROL_FACTOR, mode='nearest')
    rows, cols = np.arange(0, z.shape[0], CONTROL_FACTOR), np.arange(0, z.shape[1], CONTROL_FACTOR)
    control, control_qa = limit_field(conservative[np.ix_(rows, cols)], 120, logarithmic=False)
    x = transform.c + (cols + .5) * transform.a
    y = transform.f + (rows + .5) * transform.e
    x = np.r_[x[0] - 120, x, x[-1] + 120]
    y = np.r_[y[0] + 120, y, y[-1] - 120]
    control = np.pad(control, 1, mode='edge')
    return x, y, control, dict(normalization=thresholds, native_gradation=native_qa,
        control_gradation=control_qa, scale=scale,
        raw_target_min_m=float(raw.min()), raw_target_max_m=float(raw.max()))


def write_field(config, directory, domain):
    """Retain the Structured background actually consumed by Gmsh and its QA."""
    observe('size_field')
    source = config['cell_source']
    if 'sf1_scale' not in source:
        raise ValueError('SF-1 yêu cầu khai báo sf1_scale trong khoảng 0,25–4')
    path, band = source['dem_raster_path'], source['dem_band']
    if source.get('dem_value_units') != 'm':
        raise ValueError('Profile SF-1 yêu cầu khai báo đơn vị cao độ DEM là m')
    if (source['h_min_m'], source['h_max_m']) != H_BOUNDS_M:
        raise ValueError('Profile SF-1 dùng h_min=250 m, h_max=1000 m')
    before = snapshot(path)
    with open_raster(path) as src:
        inspect_source(src, band, config['analysis_crs'])
        inspect_coverage(src, domain)
        raster = src.read(band, masked=True).astype('float64')
        z = raster.filled(np.nan)
        valid = np.isfinite(z)
        normalization = valid & geometry_mask([mapping(domain)], out_shape=z.shape,
            transform=src.transform, invert=True)
        x, y, controls, details = build_controls(z, valid, normalization, src.transform, source['sf1_scale'])
        w, s, e, n = domain.bounds
        if w < x[0] or e > x[-1] or s < y[-1] or n > y[0]:
            raise ValueError('Miền vượt phạm vi control field SF-1; chọn DEM phủ toàn miền')
        source_shape = list(z.shape)
        affine = list(src.transform)[:6]
        source_crs = str(src.crs)
    # Gmsh Structured expects increasing Y and column-major (x then y) values.
    ordered = controls[::-1, :].T.reshape(-1)
    lines = [f'{x[0]:.17g} {y[-1]:.17g} 0', '120 120 1', f'{len(x)} {len(y)} 1']
    for offset in range(0, len(ordered), 4096):
        checkpoint()
        lines.extend(f'{value:.17g}' for value in ordered[offset:offset + 4096])
    (directory / 'size.dat').write_text('\n'.join(lines) + '\n', encoding='ascii')
    np.savez_compressed(directory / 'sf1-control-field.npz', x=x, y=y, h=controls)
    assert_unchanged(path, before)
    evidence = dict(method='gnn_sf1_two_window_relief', fixed_policy=policy(), **details,
        dem_source_path=str(Path(path).resolve()), dem_band=band, dem_value_units='m',
        source_files_sha256=before, hash_scope=HASH_SCOPE, source_shape=source_shape,
        source_affine=affine, source_crs=source_crs,
        normalization_valid_pixel_count=int(normalization.sum()),
        native_valid_pixel_count=int(valid.sum()),
        control_shape=list(controls.shape), control_spacing_m=[120, 120],
        control_bounds=[float(x[0]), float(y[-1]), float(x[-1]), float(y[0])],
        target_size_field_min_m=float(controls.min()), target_size_field_max_m=float(controls.max()),
        representation='Gmsh Structured field, piecewise-linear h',
        calibration='not performed; explicit scale only',
        source_scope='selected ROI normalization; native valid neighbours for local relief')
    (directory / 'size-field.json').write_text(json.dumps(evidence, indent=2, allow_nan=False), encoding='utf-8')
    return evidence
