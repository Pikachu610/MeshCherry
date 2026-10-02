"""Optional GIS backend: immutable inputs and physically bounded sizing."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path

import numpy as np
import rasterio
from rasterio.features import geometry_mask, shapes
from scipy import ndimage
import shapely
from shapely.geometry import box, shape


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + '\n', encoding='utf-8')


def sha256(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def normalize_score(values, valid):
    if not np.any(valid):
        raise ValueError('empty normalization support')
    lo, hi = np.percentile(values[valid], [25, 95], method='linear')
    score = np.zeros(values.shape, dtype='float64')
    if hi > lo:
        score[valid] = np.clip((values[valid] - lo) / (hi - lo), 0, 1)
    return score, [float(lo), float(hi)]


def exact_mask_polygon_area(mask, polygon, transform):
    """Compute exact raster-cell support area inside a polygon."""
    mask = np.asarray(mask, dtype=bool)
    center_inside = geometry_mask([polygon], out_shape=mask.shape, transform=transform, invert=True)
    boundary_cells = geometry_mask([polygon.boundary], out_shape=mask.shape, transform=transform,
                                   invert=True, all_touched=True)
    pixel_area = abs(transform.a * transform.e)
    total = float(np.count_nonzero(mask & center_inside & ~boundary_cells) * pixel_area)
    rows, cols = np.nonzero(mask & boundary_cells)
    for row, col in zip(rows.tolist(), cols.tolist()):
        x0, x1 = transform.c + col * transform.a, transform.c + (col + 1) * transform.a
        y0, y1 = transform.f + row * transform.e, transform.f + (row + 1) * transform.e
        total += box(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)).intersection(polygon).area
    return float(total)


def relief(z, valid, window):
    highest = ndimage.maximum_filter(np.where(valid, z, -np.inf), size=window, mode='constant', cval=-np.inf)
    lowest = ndimage.minimum_filter(np.where(valid, z, np.inf), size=window, mode='constant', cval=np.inf)
    with np.errstate(invalid='ignore'):
        result = np.where(valid, highest - lowest, 0)
    support = ndimage.uniform_filter(valid.astype(float), size=window, mode='constant', cval=0)
    return result, support


def field_gradient_bound(log_h, spacing):
    """Exact conservative bound inside EVERY bilinear interpolation square.

    Each gradient component is linear in one coordinate; its extreme lies at
    an endpoint. Combining their maxima bounds the Euclidean gradient norm.
    """
    dx = np.abs(np.diff(log_h, axis=1)) / spacing
    dy = np.abs(np.diff(log_h, axis=0)) / spacing
    gx = np.maximum(dx[:-1], dx[1:])
    gy = np.maximum(dy[:, :-1], dy[:, 1:])
    return float(np.max(np.hypot(gx, gy)))


def limit_log_size(h, spacing, ratio, reference_distance=250.0, h_bounds=(250.0, 1000.0)):
    if spacing <= 0 or ratio <= 1 or reference_distance <= 0 or not np.isfinite(h).all():
        raise ValueError('invalid size field or physical gradation parameters')
    h_min, h_max = map(float, h_bounds)
    if not 0 < h_min <= h_max:
        raise ValueError('invalid size bounds')
    log_h = np.log(np.clip(h, h_min, h_max))
    k = np.log(ratio) / reference_distance
    # Conservative separable envelope guarantees ||grad(log h)||2 <= k
    # even after bilinear interpolation (not merely at raster neighbors).
    step = k * spacing / np.sqrt(2)
    footprint = np.array([[0, 1, 0], [1, 1, 1], [0, 1, 0]], bool)
    max_steps = int(np.ceil(np.log(h_max / h_min) / step)) + 2
    for iteration in range(max_steps):
        updated = np.minimum(log_h, ndimage.minimum_filter(log_h, footprint=footprint, mode='nearest') + step)
        change = float(np.max(log_h - updated))
        log_h = updated
        if change < 1e-12:
            break
    measured = field_gradient_bound(log_h, spacing)
    info = {'definition': 'Euclidean bilinear log-size gradient bound', 'ratio': ratio,
            'reference_distance_m': reference_distance, 'allowed_log_gradient_per_m': float(k),
            'measured_bound_per_m': measured, 'iterations': iteration + 1,
            'pass': bool(measured <= k + 1e-12),
            'limiter': 'conservative 4-neighbor min-plus envelope; per-axis k/sqrt(2)'}
    if not info['pass']:
        raise ValueError('gradation limiter did not converge')
    return np.exp(log_h), info


@dataclass
class Terrain:
    z: np.ndarray
    slope: np.ndarray
    valid: np.ndarray
    slope_valid: np.ndarray
    transform: object
    profile: dict
    scores: dict
    classes: np.ndarray
    high: np.ndarray
    support_geometry: object
    support_areas: dict
    provenance: dict

    @property
    def spacing(self):
        return self.transform.a

    def save(self, path, values, mask=None):
        data = np.asarray(values, dtype='float64')
        if mask is not None:
            data = np.where(mask, data, -9999)
        profile = self.profile.copy()
        profile.update(driver='GTiff', dtype='float64', count=1, nodata=-9999,
                       compress='deflate', predictor=3)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with rasterio.open(path, 'w', **profile) as target:
            target.write(data, 1)

    def extend_for_sizing(self, values):
        # This extension never modifies the source or evaluation masks.
        nearest = ndimage.distance_transform_edt(~self.valid, return_distances=False, return_indices=True)
        return values[tuple(nearest)]


def load_terrain(bundle, polygon, canonical, output):
    bundle, output = Path(bundle), Path(output)
    metadata_path = bundle / 'metadata/manifest.json'
    metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
    audit_path = bundle / 'metadata/static_audit.json'
    audit = json.loads(audit_path.read_text(encoding='utf-8'))
    if metadata.get('elevation_unit') != 'm' or metadata.get('slope_unit') != 'degrees':
        raise ValueError('terrain units must explicitly be m and degrees')
    names = ['dem_reference.tif', 'slope_complete.tif', 'slope_filled_mask.tif']
    arrays, masks, inputs = [], [], []
    profile = None
    for name in names:
        path = bundle / 'static' / name
        before = sha256(path)
        with rasterio.open(path) as ds:
            if ds.count != 1 or ds.crs.to_epsg() != 32649:
                raise ValueError('expected single-band EPSG:32649 terrain')
            t = ds.transform
            if t.b != 0 or t.d != 0 or t.a <= 0 or t.e != -t.a:
                raise ValueError('expected square north-up metric grid')
            if profile is not None and (ds.shape != arrays[0].shape or t != transform):
                raise ValueError('terrain grids are not aligned')
            if profile is None:
                profile, transform = ds.profile.copy(), t
            values = ds.read(1).astype(float)
            mask = (ds.read_masks(1) > 0) & np.isfinite(values)
            arrays.append(values)
            masks.append(mask)
            inputs.append({'path': str(path.resolve()), 'sha256': before, 'valid_pixels': int(mask.sum()),
                           'crs': str(ds.crs), 'shape': list(ds.shape), 'transform': list(t)[:6], 'nodata': ds.nodata})
        if sha256(path) != before:
            raise ValueError('source changed during read')
    z, slope, filled = arrays
    reference = metadata.get('reference_grid', {})
    audit_reference = audit.get('reference', {})
    expected_transform = reference.get('transform')
    actual_transform = list(transform)[:6]
    if (metadata.get('crs') != 'EPSG:32649' or reference != audit_reference
            or reference.get('shape') != list(z.shape)
            or expected_transform != actual_transform
            or reference.get('valid_cells') != int(masks[0].sum())
            or reference.get('cell_size_m') != [30.0, 30.0]):
        raise ValueError('terrain bundle reference-grid audit contract mismatch')
    expected_hashes = audit.get('sha256', {})
    for name, recorded in zip(names, inputs):
        if expected_hashes.get('static/' + name) != recorded['sha256']:
            raise ValueError(f'terrain source hash does not match static audit: {name}')
    if not np.array_equal(masks[0], masks[1]) or not np.array_equal(masks[0], masks[2]):
        raise ValueError('DEM, slope and fill masks differ; explicit support policy required')
    valid = masks[0]
    if not np.all(np.isin(filled[valid], [0, 1])) or np.any((slope[valid] < 0) | (slope[valid] > 90)):
        raise ValueError('invalid slope/fill-mask values')
    if int((valid & (filled == 1)).sum()) != audit.get('slope', {}).get('filled_cells'):
        raise ValueError('filled-slope pixel count does not match static audit')
    roi_mask = geometry_mask([canonical], out_shape=z.shape, transform=transform, invert=True)
    normalization_mask = valid & roi_mask
    scores, stats, other = {}, {}, {}
    scores['slope'], stats['slope'] = normalize_score(slope, normalization_mask)
    for size in (5, 15):
        r, support = relief(z, valid, size)
        key = f'relief{int(size * transform.a)}'
        other[key], other[key + '_window_support'] = r, support
        scores[key], stats[key] = normalize_score(r, normalization_mask)
    relief_keys = [key for key in scores if key.startswith('relief')]
    scores['relief'] = np.maximum.reduce([scores[k] for k in relief_keys])
    scores['combined'] = np.maximum(scores['slope'], scores['relief'])
    cutoff = float(np.percentile(scores['combined'][normalization_mask], 75, method='linear'))
    high = normalization_mask & (scores['combined'] >= cutoff)
    classes = np.where((z < 100) & (slope < 5), 1, np.where((z >= 300) | (slope >= 15), 3, 2))
    classes[~valid] = 0
    def mask_geometry(mask):
        parts = [shape(g) for g, value in shapes(mask.astype('uint8'), mask=mask, transform=transform) if value]
        return shapely.union_all(parts)

    footprint = mask_geometry(valid)
    slope_footprint = mask_geometry(valid & (filled == 0))
    support_areas = {
        'dem': {'global': float(footprint.intersection(polygon).area),
                'high_complexity': exact_mask_polygon_area(high, polygon, transform)},
        'slope_source_observed': {'global': float(slope_footprint.intersection(polygon).area),
                                  'high_complexity': exact_mask_polygon_area(high & valid & (filled == 0), polygon, transform)},
    }
    provenance = {'schema_version': 'terrain-source-v1', 'inputs': inputs,
                  'metadata': {'path': str(metadata_path.resolve()), 'sha256': sha256(metadata_path)},
                  'static_audit': {'path': str(audit_path.resolve()), 'sha256': sha256(audit_path)},
                  'normalization': stats, 'high_complexity_cutoff': cutoff,
                  'high_complexity_valid_pixel_fraction': float(high.sum() / valid.sum()),
                  'filled_slope_pixels': int((valid & (filled == 1)).sum()),
                  'derived_area_m2': polygon.area, 'canonical_area_m2': canonical.area,
                  'valid_derived_area_m2': support_areas['dem']['global'],
                  'valid_derived_fraction': support_areas['dem']['global'] / polygon.area,
                  'canonical_support_symmetric_difference_m2': footprint.symmetric_difference(canonical).area,
                  'evaluation': 'pixel-area overlap on fixed source support; no extrapolation',
                  'fixed_support_areas_m2': support_areas,
                  'sizing_extension': 'outside valid terrain support uses complexity 0 / raw h=1000 m; no terrain imputation; only gradation may propagate refinement'}
    terrain = Terrain(z, slope, valid, valid & (filled == 0), transform, profile, scores, classes, high,
                      footprint, support_areas, provenance)
    write_json(output / 'provenance.json', provenance)
    for key, values in {**other, **{k + '_score': v for k, v in scores.items()},
                        'terrain_class': classes, 'high_complexity_mask': high,
                        'slope_primary_mask': terrain.slope_valid, 'dem_valid_mask': valid}.items():
        terrain.save(output / (key + '.tif'), values, valid)
    return terrain
