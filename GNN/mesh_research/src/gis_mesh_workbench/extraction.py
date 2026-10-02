"""Small read-only reference kernel; no meshing, raster resampling or UI.

Coordinates are GIS X,Y. Cells must already use the analysis CRS. Cross-CRS
pixel footprints are approximated using declared edge subdivisions. All
areas are projected planar areas, not geodesic/vertical-datum validation.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
from pathlib import Path

import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio.warp import transform
import shapely
from shapely.geometry import Polygon
from shapely.ops import unary_union
from .spatial import metric_crs as _metric_crs


@dataclass(frozen=True)
class Cell:
    cell_id: str
    kind: str
    xy: tuple[tuple[float, float], ...]


def _digest(path):
    h = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def _cells(cells):
    if not cells or len({c.cell_id for c in cells}) != len(cells):
        raise ValueError('nonempty cells with unique IDs required')
    polygons = []
    for cell in cells:
        expected = {'triangle': 3, 'rectangle': 4}.get(cell.kind)
        if not cell.cell_id or expected is None or len(cell.xy) != expected:
            raise ValueError('cell ID/kind/vertex count invalid')
        xy = np.asarray(cell.xy, float)
        if xy.shape != (expected, 2) or not np.isfinite(xy).all():
            raise ValueError('finite XY coordinates required')
        poly = Polygon(xy)
        if not poly.is_valid or poly.area <= 0:
            raise ValueError('valid positive-area cell required')
        if cell.kind == 'rectangle':
            edges = np.roll(xy, -1, axis=0) - xy
            lengths = np.linalg.norm(edges, axis=1)
            if (lengths == 0).any() or np.any(np.abs(
                np.sum((edges / lengths[:, None]) *
                       np.roll(edges / lengths[:, None], -1, axis=0), axis=1)
            ) > 1e-10):
                raise ValueError('rectangle must have perpendicular edges')
        polygons.append(poly)
    # Small reference kernel only; production spatial indexing is ticket 05.
    for i, first in enumerate(polygons):
        for second in polygons[i + 1:]:
            if first.intersection(second).area > 1e-10 * min(first.area, second.area):
                raise ValueError('cells overlap in positive area')
    return polygons


def extract_attributes(path, cells, *, cell_crs, analysis_crs, band,
                       semantics, value_units, edge_subdivisions=8,
                       max_pixels=10000, max_pairs=1000000):
    """Return serializable attributes/provenance. Extensive = source-pixel totals.

    No inputs are modified. Work is intentionally bounded, including overlap
    validation. Missing coverage is never upscaled. Caller supplies physical
    units; their scientific meaning and vertical datum are not inferred.
    """
    target = _metric_crs(analysis_crs)
    if cell_crs is None or CRS.from_user_input(cell_crs) != target:
        raise ValueError('cells must explicitly use analysis CRS; relabeling forbidden')
    if semantics not in {'intensive', 'extensive', 'categorical'} or not value_units:
        raise ValueError('declared semantics and value units required')
    if (not isinstance(edge_subdivisions, int) or isinstance(edge_subdivisions, bool)
            or not 1 <= edge_subdivisions <= 64):
        raise ValueError('edge_subdivisions must be an integer from 1 to 64')
    if len(cells)**2 > max_pairs:
        raise ValueError('cell validation workload exceeds reference-kernel limit')
    polygons = _cells(cells)
    union = unary_union(polygons)
    path = Path(path)
    before = _digest(path)
    records = [dict(cell_id=c.cell_id, kind=c.kind, area_m2=float(p.area),
                    footprint_area_m2=0.0, valid_area_m2=0.0)
               for c, p in zip(cells, polygons)]
    samples = [[] for _ in cells]
    union_total = 0.0
    with rasterio.open(path, 'r') as src:
        if src.crs is None:
            raise ValueError('source raster CRS missing')
        if not isinstance(band, int) or isinstance(band, bool) or not 1 <= band <= src.count:
            raise ValueError('band out of range')
        scale, offset = src.scales[band - 1], src.offsets[band - 1]
        if scale != 1.0 or offset != 0.0:
            raise ValueError('non-identity band scale/offset unsupported; stored values are not physical values')
        if src.width * src.height > max_pixels or src.width * src.height * len(cells) > max_pairs:
            raise ValueError('raster workload exceeds reference-kernel limit')
        if not np.isfinite(tuple(src.transform)).all() or abs(src.transform.determinant) == 0:
            raise ValueError('finite nonsingular raster affine required')
        raster = src.read(band, masked=True)
        if np.iscomplexobj(raster):
            raise ValueError('complex rasters are unsupported')
        values = np.asarray(raster.data, float)
        valid = ~np.ma.getmaskarray(raster) & np.isfinite(values)
        for row in range(src.height):
            for col in range(src.width):
                corners = [(col, row), (col+1, row), (col+1, row+1), (col, row+1)]
                n = 1 if src.crs == target else edge_subdivisions
                points = [src.transform @ (a[0]+(b[0]-a[0])*k/n,
                                           a[1]+(b[1]-a[1])*k/n)
                          for a, b in zip(corners, corners[1:] + corners[:1])
                          for k in range(n)]
                if src.crs != target:
                    x, y = transform(src.crs, target, *zip(*points))
                    points = list(zip(x, y))
                if not np.isfinite(points).all():
                    raise ValueError('nonfinite reprojected footprint')
                pixel = Polygon(points)
                if not pixel.is_valid or pixel.area <= 0:
                    raise ValueError('invalid reprojected pixel footprint')
                if semantics == 'extensive' and valid[row, col]:
                    union_total += values[row, col] * pixel.intersection(union).area / pixel.area
                for i, poly in enumerate(polygons):
                    area = float(pixel.intersection(poly).area)
                    if area <= 0:
                        continue
                    records[i]['footprint_area_m2'] += area
                    if valid[row, col]:
                        records[i]['valid_area_m2'] += area
                        samples[i].append((float(values[row, col]), area, float(pixel.area)))
        provenance = dict(source_path=str(path.resolve()), source_sha256=before,
                          hash_scope='main_raster_file_only',
                          band_dtype=src.dtypes[band - 1], band_scale=scale, band_offset=offset,
                          band_nodata=(src.nodatavals[band - 1] if src.nodatavals[band - 1] is None
                                       or math.isfinite(src.nodatavals[band - 1])
                                       else str(src.nodatavals[band - 1])),
                          band_mask_flags=[flag.name for flag in src.mask_flag_enums[band - 1]],
                          source_crs_wkt=src.crs.to_wkt(), analysis_crs_wkt=target.to_wkt(),
                          affine=list(src.transform), shape=[src.height, src.width], band=band,
                          xy_policy='GIS X,Y; geographic longitude,latitude',
                          edge_subdivisions=edge_subdivisions, raster_resampling='none',
                          area_model='projected planar; cross-CRS edges approximated',
                          transformation_accuracy='not independently verified',
                          extensive_basis='total_per_source_pixel' if semantics == 'extensive' else None,
                          value_units=value_units, semantics=semantics,
                          rasterio_version=rasterio.__version__, gdal_version=rasterio.__gdal_version__,
                          shapely_version=shapely.__version__,
                          limits=dict(max_pixels=max_pixels, max_pairs=max_pairs))
    if _digest(path) != before:
        raise ValueError('source changed during extraction')
    for record, sample in zip(records, samples):
        record['footprint_coverage'] = record['footprint_area_m2'] / record['area_m2']
        record['valid_coverage'] = record['valid_area_m2'] / record['area_m2']
        if record['footprint_coverage'] > 1 + 1e-8:
            raise ValueError('source footprints overlap or invalid coverage')
        record['value'] = None
        if not sample:
            continue
        v, w, pa = np.array(sample).T
        if semantics == 'intensive':
            mean = float(np.dot(v, w)/w.sum())
            record.update(value=mean, minimum=float(v.min()), maximum=float(v.max()),
                          std=float(np.sqrt(np.dot(w, (v-mean)**2)/w.sum())))
        elif semantics == 'extensive':
            record['value'] = float(np.sum(v*w/pa))
        else:
            classes = sorted(set(v.tolist()))
            fractions = {str(code): float(w[v == code].sum()/w.sum()) for code in classes}
            record.update(class_fractions=fractions,
                          value=float(max(classes, key=lambda code: fractions[str(code)])))
    qa = dict(main_raster_unchanged=True)
    if semantics == 'extensive':
        total = sum(r['value'] for r in records if r['value'] is not None)
        tolerance = max(1e-10, abs(union_total)*1e-9)
        if abs(total-union_total) > tolerance:
            raise ValueError('extensive conservation failed')
        qa.update(source_total_on_cell_union=float(union_total), cell_total=float(total),
                  residual=float(total-union_total), tolerance=tolerance)
    return dict(schema_version='cell-attributes-reference-v0', cells=records,
                provenance=provenance, qa=qa)
