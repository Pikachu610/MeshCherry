"""Spatial size-field products and Gmsh scalar-triangle background views."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
from scipy import ndimage

from .terrain import field_gradient_bound, limit_log_size, write_json


def build_size_field(complexity, spacing, gamma, ratio, scale=1.0,
                     h_bounds=(250.0, 1000.0), reference_distance=250.0):
    h_min, h_max = map(float, h_bounds)
    raw = h_max - (h_max - h_min) * np.asarray(complexity, float) ** float(gamma)
    scaled = np.clip(float(scale) * raw, h_min, h_max)
    limited, qa = limit_log_size(scaled, spacing, ratio, reference_distance, h_bounds)
    return raw, scaled, limited, qa


def control_field(final_h, transform, factor=4, ratio=1.3,
                  reference_distance=250.0, h_bounds=(250.0, 1000.0)):
    """Conservatively transfer a 30 m field to a fixed 120 m node lattice.

    Local minima are sampled after a factor-square minimum filter, ensuring a
    requested fine patch is not erased by averaging. A one-node halo is added.
    """
    if factor < 1:
        raise ValueError('control factor must be positive')
    spacing = float(transform.a) * factor
    conservative = ndimage.minimum_filter(final_h, size=factor, mode='nearest')
    rows = list(range(0, final_h.shape[0], factor))
    cols = list(range(0, final_h.shape[1], factor))
    sampled = conservative[np.ix_(rows, cols)]
    h_min, _ = map(float, h_bounds)
    k = np.log(ratio) / reference_distance
    # POS interpolates h linearly. Bound |grad h| <= 250*k so
    # |grad log(h)| <= k everywhere because h >= 250.
    step = h_min * k * spacing / np.sqrt(2)
    footprint = np.array([[0, 1, 0], [1, 1, 1], [0, 1, 0]], bool)
    for iteration in range(10000):
        updated = np.minimum(sampled, ndimage.minimum_filter(sampled, footprint=footprint, mode='nearest') + step)
        if float(np.max(sampled - updated)) < 1e-10:
            sampled = updated
            break
        sampled = updated
    dx = np.abs(np.diff(sampled, axis=1)) / spacing
    dy = np.abs(np.diff(sampled, axis=0)) / spacing
    numerator = np.hypot(np.maximum(dx[:-1], dx[1:]), np.maximum(dy[:, :-1], dy[:, 1:]))
    denominator = np.minimum.reduce([sampled[:-1,:-1], sampled[:-1,1:], sampled[1:,:-1], sampled[1:,1:]])
    measured = float(np.max(numerator / denominator))
    qa = {'definition': 'Euclidean bilinear log-size gradient bound for linearly interpolated h',
          'ratio': ratio, 'reference_distance_m': reference_distance, 'allowed_log_gradient_per_m': float(k),
          'measured_bound_per_m': measured, 'iterations': iteration + 1,
          'pass': bool(measured <= k + 1e-12),
          'limiter': 'conservative additive 4-neighbor min-plus envelope; |grad h| <= 250*k'}
    if not qa['pass']:
        raise ValueError('control-field gradation QA failed')
    # Raster rows run north to south. Keep that orientation; POS coordinates
    # below are explicit and do not assume positive Dy.
    x = transform.c + (np.asarray(cols) + .5) * transform.a
    y = transform.f + (np.asarray(rows) + .5) * transform.e
    x = np.r_[x[0] - spacing, x, x[-1] + spacing]
    y = np.r_[y[0] - transform.e * factor, y, y[-1] + transform.e * factor]
    values = np.pad(sampled, 1, mode='edge')
    return x, y, values, {'factor': factor, 'spacing_m': spacing, **qa}


def write_background_pos(path, x, y, h):
    """Write a deterministic piecewise-linear scalar-triangle Gmsh view."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ['View "SF1 mesh size" {']
    values = np.asarray(h, float)
    for row in range(len(y) - 1):
        for col in range(len(x) - 1):
            x0, x1, y0, y1 = x[col], x[col + 1], y[row], y[row + 1]
            a, b, c, d = values[row, col], values[row, col + 1], values[row + 1, col + 1], values[row + 1, col]
            lines.append(f'ST({x0:.9g},{y0:.9g},0,{x1:.9g},{y0:.9g},0,{x1:.9g},{y1:.9g},0){{{a:.12g},{b:.12g},{c:.12g}}};')
            lines.append(f'ST({x0:.9g},{y0:.9g},0,{x1:.9g},{y1:.9g},0,{x0:.9g},{y1:.9g},0){{{a:.12g},{c:.12g},{d:.12g}}};')
    lines.append('};')
    path.write_text('\n'.join(lines) + '\n', encoding='ascii')
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_structured_field(path, x, y, h):
    """Write Gmsh's documented ASCII Structured-field format."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    x, y, values = np.asarray(x), np.asarray(y), np.asarray(h)
    if y[1] < y[0]:
        y, values = y[::-1], values[::-1, :]
    if not np.allclose(np.diff(x), x[1] - x[0]) or not np.allclose(np.diff(y), y[1] - y[0]):
        raise ValueError('structured field coordinates must be regular')
    ordered = values.T.reshape(-1)  # v(i,0,0), v(i,1,0), then next i
    lines = [f'{x[0]:.17g} {y[0]:.17g} 0', f'{x[1]-x[0]:.17g} {y[1]-y[0]:.17g} 1',
             f'{len(x)} {len(y)} 1']
    lines.extend(f'{value:.12g}' for value in ordered)
    path.write_text('\n'.join(lines) + '\n', encoding='ascii')
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_control_artifacts(directory, terrain, final_h, ratio, include_pos=True,
                            factor=4, reference_distance=250.0, h_bounds=(250.0, 1000.0)):
    directory = Path(directory)
    x, y, control, qa = control_field(final_h, terrain.transform, factor, ratio,
                                      reference_distance, h_bounds)
    pos_hash = write_background_pos(directory / 'background.pos', x, y, control) if include_pos else None
    grid_hash = write_structured_field(directory / 'background.dat', x, y, control)
    metadata = {'schema_version': 'gmsh-background-field-v1', 'representation': 'piecewise-linear h in Gmsh Structured field; equivalent POS retained for inspection',
                'x_count': len(x), 'y_count': len(y), 'triangle_count': 2 * (len(x)-1) * (len(y)-1),
                'min_h_m': float(control.min()), 'max_h_m': float(control.max()),
                'nonuniform': bool(float(control.max() - control.min()) > 1e-6),
                'pos_sha256': pos_hash, 'structured_sha256': grid_hash, 'qa': qa}
    # Compact array is retained in addition to the exact POS used by Gmsh.
    np.savez_compressed(directory / 'control-field.npz', x=x, y=y, h=control)
    write_json(directory / 'background-field.json', metadata)
    return directory / 'background.dat', metadata
