"""Exact positive-area triangle/pixel overlap and representation metrics."""
from __future__ import annotations

import math
import numpy as np
from scipy.sparse import coo_matrix, csr_matrix
import shapely
from shapely.geometry import Polygon, box


def overlap_weights(vertices, triangles, shape, transform):
    rows, cols = shape
    rr, cc, data = [], [], []
    pixel_area = abs(transform.a * transform.e)
    for cell, ids in enumerate(np.asarray(triangles, dtype=int)):
        tri = Polygon(np.asarray(vertices)[ids, :2])
        minx, miny, maxx, maxy = tri.bounds
        c0 = max(0, int(math.floor((minx - transform.c) / transform.a)))
        c1 = min(cols - 1, int(math.floor((maxx - transform.c) / transform.a)))
        r0 = max(0, int(math.floor((transform.f - maxy) / abs(transform.e))))
        r1 = min(rows - 1, int(math.floor((transform.f - miny) / abs(transform.e))))
        if c1 < c0 or r1 < r0:
            continue
        candidate_rows, candidate_cols = np.meshgrid(np.arange(r0, r1 + 1), np.arange(c0, c1 + 1), indexing='ij')
        flat_r, flat_c = candidate_rows.ravel(), candidate_cols.ravel()
        left = transform.c + flat_c * transform.a
        right = left + transform.a
        top = transform.f + flat_r * transform.e
        bottom = top + transform.e
        pixels = shapely.box(left, np.minimum(top, bottom), right, np.maximum(top, bottom))
        areas = shapely.area(shapely.intersection(pixels, tri))
        positive = areas > max(pixel_area * 1e-12, 1e-8)
        rr.extend([cell] * int(positive.sum()))
        cc.extend((flat_r[positive] * cols + flat_c[positive]).tolist())
        data.extend(areas[positive].tolist())
    return coo_matrix((data, (rr, cc)), shape=(len(triangles), rows * cols)).tocsr()


def representation(weights, values, valid, subset, fixed_support_areas=None):
    flat = np.asarray(values, float).ravel()
    valid_flat = np.asarray(valid, bool).ravel()
    subset_flat = valid_flat & np.asarray(subset, bool).ravel()
    valid_w = weights.multiply(valid_flat.astype(float))
    area_per_cell = np.asarray(valid_w.sum(axis=1)).ravel()
    sums = np.asarray(valid_w @ np.where(valid_flat, flat, 0)).ravel()
    means = np.divide(sums, area_per_cell, out=np.full(weights.shape[0], np.nan), where=area_per_cell > 0)
    def stats(mask, label):
        w = weights.multiply(mask.astype(float))
        reconstructed_sum = np.asarray(w.T @ np.nan_to_num(means)).ravel()
        covered = np.asarray(w.sum(axis=0)).ravel()
        reconstructed = np.divide(reconstructed_sum, covered, out=np.zeros_like(reconstructed_sum), where=covered > 0)
        support = mask & (covered > 0)
        errors = reconstructed[support] - flat[support]
        areas = covered[support]
        total = float(areas.sum())
        # A coverage claim must be measured against a support declared before
        # the mesh is evaluated.  The old expression inferred a denominator
        # from touched pixels and therefore hid partial-support loss.
        expected = float((fixed_support_areas or {}).get(label, total))
        if expected < total - max(1.0, expected * 1e-9):
            raise ValueError(f'covered area exceeds fixed {label} support')
        return {'area_m2': total, 'expected_support_area_m2': expected,
                'covered_area_m2': total, 'uncovered_area_m2': max(0.0, expected - total),
                'coverage_fraction': float(total / expected) if expected else 1.0,
                'mae': float(np.sum(areas * np.abs(errors)) / total) if total else None,
                'rmse': float(np.sqrt(np.sum(areas * errors**2) / total)) if total else None}
    return means, {'global': stats(valid_flat, 'global'), 'high_complexity': stats(subset_flat, 'high_complexity')}


def cell_terrain_records(weights, terrain, vertices=None, triangles=None):
    records = []
    zmean, _ = representation(weights, terrain.z, terrain.valid, terrain.high)
    smean, _ = representation(weights, terrain.slope, terrain.slope_valid, terrain.high)
    total_area = np.asarray(weights.sum(axis=1)).ravel()
    dem_area = np.asarray(weights.multiply(terrain.valid.ravel()).sum(axis=1)).ravel()
    slope_area = np.asarray(weights.multiply(terrain.slope_valid.ravel()).sum(axis=1)).ravel()
    high_area = np.asarray(weights.multiply(terrain.high.ravel()).sum(axis=1)).ravel()
    class_areas = [np.asarray(weights.multiply((terrain.classes.ravel() == code)).sum(axis=1)).ravel() for code in (1,2,3)]
    zflat = terrain.z.ravel()
    valid_flat = terrain.valid.ravel()
    for index in range(weights.shape[0]):
        start, end = weights.indptr[index], weights.indptr[index + 1]
        pixels, overlap = weights.indices[start:end], weights.data[start:end]
        keep = valid_flat[pixels]
        zv, zw = zflat[pixels[keep]], overlap[keep]
        zmin = float(np.min(zv)) if zv.size else None
        zmax = float(np.max(zv)) if zv.size else None
        zstd = (float(np.sqrt(np.sum(zw * (zv - zmean[index]) ** 2) / np.sum(zw)))
                if zv.size and np.sum(zw) else None)
        zcentroid = None
        if vertices is not None and triangles is not None:
            centroid = np.asarray(vertices)[np.asarray(triangles[index], int), :2].mean(axis=0)
            col = int(np.floor((centroid[0] - terrain.transform.c) / terrain.transform.a))
            row = int(np.floor((terrain.transform.f - centroid[1]) / abs(terrain.transform.e)))
            if 0 <= row < terrain.z.shape[0] and 0 <= col < terrain.z.shape[1] and terrain.valid[row, col]:
                zcentroid = float(terrain.z[row, col])
        records.append({'cell_id': f'cell-{index}', 'z_mean_m': None if np.isnan(zmean[index]) else float(zmean[index]),
                        'z_centroid_m': zcentroid, 'z_centroid_sampling': 'containing source pixel',
                        'z_min_m': zmin, 'z_max_m': zmax, 'z_std_m_area_weighted': zstd,
                        'dem_overlap_area_m2': float(dem_area[index]),
                        'positive_overlap_pixel_count': int(zv.size),
                        'slope_mean_degrees_source_observed': None if np.isnan(smean[index]) else float(smean[index]),
                        'dem_coverage_fraction': float(dem_area[index] / total_area[index]),
                        'source_slope_coverage_fraction': float(slope_area[index] / total_area[index]),
                        'high_complexity_fraction': float(high_area[index] / max(dem_area[index], 1e-30)),
                        'terrain_class_fractions': {'plain': float(class_areas[0][index]/max(dem_area[index],1e-30)),
                                                    'hill': float(class_areas[1][index]/max(dem_area[index],1e-30)),
                                                    'mountain': float(class_areas[2][index]/max(dem_area[index],1e-30))}})
    return records
