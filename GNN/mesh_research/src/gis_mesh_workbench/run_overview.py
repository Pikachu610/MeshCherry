"""Bounded, factual overview figure for one completed mesh run."""
from __future__ import annotations

import json
import math
import re
import threading
import zipfile
from pathlib import Path

import matplotlib
matplotlib.use('Agg', force=True)
import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection
from matplotlib.lines import Line2D
from matplotlib.tri import Triangulation
from matplotlib.ticker import FuncFormatter, MaxNLocator
import numpy as np
import rasterio
from rasterio.vrt import WarpedVRT
from rasterio.features import geometry_mask
from rasterio.windows import from_bounds
from shapely.geometry import Polygon, mapping
from shapely.ops import unary_union

from .job_control import checkpoint


_MAX_RASTER_SIDE = 700
_MAX_CELLS = 100_000
_MAX_FIELD_FILE_BYTES = 8 * 1024 * 1024
_MAX_FIELD_ARRAY_BYTES = 64 * 1024 * 1024
_PLOT_LOCK = threading.RLock()


def _bounds(cells):
    xs = [float(x) for cell in cells for x, _ in cell.xy]
    ys = [float(y) for cell in cells for _, y in cell.xy]
    return min(xs), min(ys), max(xs), max(ys)


def _shared_edges(cells):
    """Return only edges shared by exactly two cells, in linear time."""
    owners = {}
    for index, cell in enumerate(cells):
        if index % 1024 == 0:
            checkpoint()
        points = tuple((float(x), float(y)) for x, y in cell.xy)
        for a, b in zip(points, points[1:] + points[:1]):
            if a == b:
                continue
            key = (a, b) if a < b else (b, a)
            owners.setdefault(key, []).append(index)
    return [(edge, pair) for edge, pair in owners.items() if len(pair) == 2]


def _field_path(output: Path, evidence):
    run_root = output.resolve()

    def contained_file(candidate):
        candidate = Path(candidate).resolve()
        try:
            candidate.relative_to(run_root)
        except ValueError:
            return None
        if candidate.is_file() and candidate.stat().st_size <= _MAX_FIELD_FILE_BYTES:
            return candidate
        return None

    if isinstance(evidence, dict):
        for key in ('control_field_path', 'size_field_path'):
            if evidence.get(key):
                path = Path(evidence[key])
                if not path.is_absolute():
                    path = run_root / path
                path = contained_file(path)
                if path is not None:
                    return path
    for name in ('sf1-control-field.npz', 'size.pos', 'size-field.json'):
        path = contained_file(run_root / 'meshing' / name)
        if path is not None:
            return path
    return None


def _read_target(output, config, evidence):
    path = _field_path(output, evidence)
    source = config.get('cell_source', {}) if isinstance(config, dict) else {}
    kind = 'unavailable'
    label = 'Không có trường h mục tiêu đã lưu'
    data = None
    if path and path.name == 'sf1-control-field.npz':
        with zipfile.ZipFile(path) as archive:
            if sum(item.file_size for item in archive.infolist()) > _MAX_FIELD_ARRAY_BYTES:
                return kind, label, None
        with np.load(path, allow_pickle=False) as field:
            x, y, h = (np.asarray(field[k], dtype=float) for k in ('x', 'y', 'h'))
        if x.ndim == y.ndim == 1 and h.shape == (len(y), len(x)) and np.isfinite(h).any():
            data = (x, y, np.ma.masked_invalid(h))
            kind, label = 'saved_control_field', 'Trường h điều khiển đã lưu (m)'
    elif path and path.name == 'size.pos':
        triangles = []
        pattern = re.compile(r'ST\s*\(\s*([^}]*)\)\s*\{\s*([^}]*)\}')
        for index, match in enumerate(pattern.finditer(path.read_text(encoding='utf-8'))):
            if index % 512 == 0:
                checkpoint()
            coords = [float(v) for v in re.findall(r'[-+]?(?:\d*\.\d+|\d+\.?\d*)(?:[eE][-+]?\d+)?', match.group(1))]
            values = [float(v) for v in re.findall(r'[-+]?(?:\d*\.\d+|\d+\.?\d*)(?:[eE][-+]?\d+)?', match.group(2))]
            if len(coords) >= 9 and len(values) == 3:
                triangles.append(([(coords[i], coords[i+1]) for i in (0, 3, 6)], values))
        if triangles:
            raw_points = np.asarray([point for triangle, _ in triangles for point in triangle], dtype=float)
            raw_values = np.asarray([value for _, group in triangles for value in group], dtype=float)
            points, inverse = np.unique(raw_points, axis=0, return_inverse=True)
            sums = np.bincount(inverse, weights=raw_values, minlength=len(points))
            counts = np.bincount(inverse, minlength=len(points))
            values = sums / np.maximum(counts, 1)
            data = (points, values)
            kind, label = 'saved_exploratory_field', 'Trường h thăm dò đã lưu (m)'
    elif path and path.name == 'size-field.json':
        details = json.loads(path.read_text(encoding='utf-8'))
        value = details.get('requested_target_size_m')
        if isinstance(value, (int, float)) and math.isfinite(float(value)) and float(value) > 0:
            data = float(value)
            kind, label = 'saved_uniform_field', 'Trường đều theo cấu hình đã lưu (m)'
    if data is None and source.get('sizing') == 'uniform':
        value = source.get('h_max_m')
        if isinstance(value, (int, float)) and math.isfinite(value) and value > 0:
            kind, label, data = 'configured_uniform_field', 'Kích thước đều đã chọn (m)', float(value)
    return kind, label, data


def _draw_dem(ax, config, cells, bounds, domain):
    source = config.get('cell_source', {}) if isinstance(config, dict) else {}
    reference = config.get('overview_dem') or {}
    raster_path = source.get('dem_raster_path') or reference.get('path')
    if not raster_path:
        return None
    path = Path(raster_path)
    if not path.is_file():
        return None
    band = int(source.get('dem_band', 1) if source.get('dem_raster_path') else reference.get('band', 1))
    from .raster_source import open_raster
    with open_raster(path) as source_dataset:
        if source_dataset.crs is None or not config.get('analysis_crs'):
            return None
        target_crs = rasterio.crs.CRS.from_user_input(config['analysis_crs'])
        if source_dataset.crs == target_crs:
            return _draw_dem_dataset(ax, source_dataset, cells, bounds, band, domain)
        with WarpedVRT(source_dataset, crs=target_crs) as dataset:
            return _draw_dem_dataset(ax, dataset, cells, bounds, band, domain)


def _draw_dem_dataset(ax, dataset, cells, bounds, band, domain):
    window = from_bounds(*bounds, transform=dataset.transform).round_offsets().round_lengths()
    try:
        window = window.intersection(rasterio.windows.Window(0, 0, dataset.width, dataset.height))
    except rasterio.errors.WindowError:
        return None
    if window.width <= 0 or window.height <= 0:
        return None
    scale = min(1.0, _MAX_RASTER_SIDE / max(window.width, window.height))
    shape = (max(1, round(window.height * scale)), max(1, round(window.width * scale)))
    raster = dataset.read(band, window=window, out_shape=shape, masked=True,
                          resampling=rasterio.enums.Resampling.average)
    transform = dataset.window_transform(window) @ rasterio.Affine.scale(
        window.width / shape[1], window.height / shape[0])
    clip_mask = geometry_mask([mapping(domain)], out_shape=shape, transform=transform,
                              invert=True, all_touched=False)
    values = np.ma.filled(raster.astype('float64'), np.nan)
    shown = np.ma.array(values, mask=np.ma.getmaskarray(raster) | ~np.isfinite(values) | ~clip_mask)
    if shown.count() == 0:
        return None
    x0, y0 = transform @ (0, shape[0])
    x1, y1 = transform @ (shape[1], 0)
    artist = ax.imshow(shown, extent=(x0, x1, y0, y1), origin='upper', cmap='terrain',
                       interpolation='nearest', rasterized=True)
    ax.set_title('DEM tham chiếu (NoData được giữ nguyên)', loc='left', fontsize=10)
    return artist


def render_overview(output: Path, cells, config, evidence=None) -> bool:
    """Render while ensuring a failed plot does not leak a Matplotlib figure."""
    with _PLOT_LOCK:
        prior_figures = set(plt.get_fignums())
        try:
            return _render_overview(output, cells, config, evidence)
        finally:
            for number in set(plt.get_fignums()) - prior_figures:
                plt.close(number)


def _render_overview(output: Path, cells, config, evidence=None) -> bool:
    """Render four-panel overview of the actual run; return False on missing data.

    ``output`` is the run directory. Artifacts are ``run-overview.png`` and
    ``run-overview.json``. Missing DEM or target field is represented explicitly.
    """
    output = Path(output)
    cells = tuple(cells)
    if not cells or len(cells) > _MAX_CELLS:
        return False
    checkpoint()
    bounds = _bounds(cells)
    domain = unary_union([Polygon(cell.xy) for cell in cells])
    centers = np.asarray([[np.mean([p[0] for p in c.xy]), np.mean([p[1] for p in c.xy])]
                          for c in cells], dtype=float)
    polygons = [np.asarray(c.xy + (c.xy[0],), dtype=float) for c in cells]
    edges = _shared_edges(cells)
    target_kind, target_label, target = _read_target(output, config, evidence)

    fig = plt.figure(figsize=(13.5, 9.5), layout='constrained')
    grid = fig.add_gridspec(2, 4, width_ratios=(1, .035, 1, .035))
    a_dem, a_h = fig.add_subplot(grid[0, 0]), fig.add_subplot(grid[0, 2])
    a_mesh, a_zoom = fig.add_subplot(grid[1, 0]), fig.add_subplot(grid[1, 2])
    dem_bar, h_bar = fig.add_subplot(grid[0, 1]), fig.add_subplot(grid[0, 3])
    # Reserve identical colorbar gutters on both rows so map frames align.
    for column in (1, 3):
        fig.add_subplot(grid[1, column]).set_visible(False)
    try:
        dem_artist = _draw_dem(a_dem, config, cells, bounds, domain)
    except (ValueError, OSError, rasterio.errors.RasterioError):
        dem_artist = None
    if dem_artist is not None:
        fig.colorbar(dem_artist, cax=dem_bar, label='Cao độ (m)')
    else:
        dem_bar.set_visible(False)
        selected_dem = config.get('cell_source', {}).get('dem_raster_path') or (config.get('overview_dem') or {}).get('path')
        message = 'Không đọc được DEM đã chọn' if selected_dem else 'Chưa chọn DEM tham chiếu\n(tùy chọn khi chia đều)'
        a_dem.text(.5, .5, message, ha='center', va='center', transform=a_dem.transAxes)
        a_dem.set_title('DEM tham chiếu', loc='left', fontsize=10)

    if isinstance(target, tuple) and len(target) == 3:
        x, y, h = target
        stride_x = max(1, math.ceil((len(x) - 1) / _MAX_RASTER_SIDE))
        stride_y = max(1, math.ceil((len(y) - 1) / _MAX_RASTER_SIDE))
        x = x[::stride_x]; y = y[::stride_y]; h = h[::stride_y, ::stride_x]
        if len(x) == 1 or len(y) == 1:
            a_h.text(.5, .5, 'Trường h có ít hơn 2 hàng/cột', ha='center', va='center', transform=a_h.transAxes)
            hm = None
        else:
            dx = float(np.median(np.diff(x)))
            dy = float(np.median(np.diff(y)))
            field_transform = rasterio.Affine(dx, 0, x[0] - dx/2, 0, dy, y[0] - dy/2)
            field_domain = geometry_mask([mapping(domain)], out_shape=h.shape,
                                         transform=field_transform, invert=True, all_touched=False)
            h = np.ma.array(np.ma.filled(h, np.nan), mask=np.ma.getmaskarray(h) | ~field_domain)
            hm = a_h.pcolormesh(x, y, h, shading='auto', cmap='viridis_r', rasterized=True)
        if hm is not None:
            fig.colorbar(hm, cax=h_bar, label='h mục tiêu (m)')
        else:
            h_bar.set_visible(False)
    elif isinstance(target, tuple) and len(target) == 2:
        points, values = target
        tri = Triangulation(points[:, 0], points[:, 1])
        hm = a_h.tripcolor(tri, values, shading='gouraud', cmap='viridis_r', rasterized=True)
        fig.colorbar(hm, cax=h_bar, label='h mục tiêu (m)')
    elif isinstance(target, float):
        west, south, east, north = bounds
        field_transform = rasterio.Affine((east-west)/256, 0, west, 0, -(north-south)/256, north)
        inside = geometry_mask([mapping(domain)], out_shape=(256, 256), transform=field_transform, invert=True)
        hm = a_h.imshow(np.ma.array(np.full((256, 256), target), mask=~inside),
                        extent=(west, east, south, north), origin='upper', cmap='viridis_r',
                        vmin=target*.95, vmax=target*1.05)
        fig.colorbar(hm, cax=h_bar, label='h mục tiêu (m)', ticks=[target])
    else:
        h_bar.set_visible(False)
        a_h.text(.5, .5, 'Trường h không khả dụng', ha='center', va='center', transform=a_h.transAxes)
    a_h.set_title(target_label, loc='left', fontsize=10)

    a_mesh.add_collection(LineCollection(polygons, colors='#6e7479', linewidths=.24,
                                         alpha=.8, rasterized=True))
    a_mesh.set_title(f'Toàn bộ mesh ({len(cells):,} ô)', loc='left', fontsize=10)
    a_zoom.set_title('Zoom: centroid và liên kết kề nhau', loc='left', fontsize=10)
    # Local zoom around the median cell; adjacency is extracted from the mesh edge dictionary.
    pivot = centers[np.argmin(np.sum(((centers - np.median(centers, axis=0)) /
                                     np.maximum(np.ptp(centers, axis=0), 1)) ** 2, axis=1))]
    width = max((bounds[2] - bounds[0]) * .12, 1e-9)
    height = max((bounds[3] - bounds[1]) * .12, 1e-9)
    visible = ((centers[:, 0] >= pivot[0] - width/2) & (centers[:, 0] <= pivot[0] + width/2) &
               (centers[:, 1] >= pivot[1] - height/2) & (centers[:, 1] <= pivot[1] + height/2))
    visible_ids = set(np.flatnonzero(visible).tolist())
    if visible_ids:
        local_cells = [polygons[index] for index in visible_ids]
        a_zoom.add_collection(LineCollection(local_cells, colors='#777d81', linewidths=.45,
                                             alpha=.7, rasterized=True))
    local = [pair for _, pair in edges if pair[0] in visible_ids and pair[1] in visible_ids]
    if local:
        links = [[centers[left], centers[right]] for left, right in local]
        a_zoom.add_collection(LineCollection(links, colors='#d93c32', linewidths=.65,
                                             alpha=.9, rasterized=True))
    a_zoom.scatter(centers[visible, 0], centers[visible, 1], s=8, color='#2585d8', alpha=.8,
                   linewidths=0, rasterized=True, zorder=3)
    a_zoom.legend(handles=[Line2D([], [], marker='o', linestyle='none', color='#2585d8', markersize=4,
                                  label='Centroid'),
                           Line2D([], [], color='#d93c32', linewidth=1, label='Liên kết kề cạnh')],
                  loc='upper right', frameon=True, fontsize=8)
    a_zoom.set_xlim(pivot[0] - width/2, pivot[0] + width/2)
    a_zoom.set_ylim(pivot[1] - height/2, pivot[1] + height/2)

    for ax in (a_dem, a_h, a_mesh, a_zoom):
        ax.set_aspect('equal', adjustable='box')
        ax.set_xlabel('X (km, projected)')
        ax.set_ylabel('Y (km, projected)')
        ax.xaxis.set_major_locator(MaxNLocator(nbins=5))
        ax.yaxis.set_major_locator(MaxNLocator(nbins=5))
        ax.xaxis.set_major_formatter(FuncFormatter(lambda value, _: f'{value / 1000:g}'))
        ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f'{value / 1000:g}'))
        ax.tick_params(axis='both', labelsize=9)
    a_mesh.set_xlim(bounds[0], bounds[2]); a_mesh.set_ylim(bounds[1], bounds[3])
    for ax in (a_dem, a_h):
        ax.set_xlim(bounds[0], bounds[2]); ax.set_ylim(bounds[1], bounds[3])
    checkpoint()
    fig.savefig(output / 'run-overview.png', dpi=150, facecolor='white')
    plt.close(fig)
    metadata = dict(schema_version='run-overview-v1', status='rendered', cell_count=len(cells),
                    dem_status='rendered' if dem_artist is not None else 'unavailable',
                    target_field_status=target_kind, shared_positive_length_edges=len(edges),
                    zoom_shared_edges=len(local), panels=['DEM reference', 'target h', 'whole mesh', 'mesh zoom'])
    (output / 'run-overview.json').write_text(json.dumps(metadata, indent=2, allow_nan=False), encoding='utf-8')
    return True
