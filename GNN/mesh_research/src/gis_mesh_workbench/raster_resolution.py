"""Resolution metadata only: never changes cells, sizing or raster values."""
import math
from pathlib import Path
from statistics import median

import rasterio
from rasterio.warp import transform

from .spatial import metric_crs
from .raster_source import open_raster, check_driver, snapshot


def describe(src, analysis_crs, band=1):
    if src.crs is None:
        raise ValueError('Raster resolution reference requires CRS')
    if type(band) is not int or not 1 <= band <= src.count:
        raise ValueError('Resolution reference band out of range')
    target = metric_crs(analysis_crs)
    affine = src.transform
    native = [math.hypot(affine.a, affine.d), math.hypot(affine.b, affine.e)]
    if any(not math.isfinite(v) or v <= 0 for v in native) or affine.determinant == 0:
        raise ValueError('Degenerate pixel transform')
    units = 'degree' if src.crs.is_geographic else src.crs.linear_units
    # Fixed 3x3 metadata sampling, never all-raster extrema or ground accuracy.
    locations = [(x,y) for y in (.5,src.height/2,src.height-.5)
                 for x in (.5,src.width/2,src.width-.5)]
    columns, rows = [], []
    for x,y in locations:
        points = [affine@(x,y),affine@(x+1,y),affine@(x,y+1)]
        xs,ys = transform(src.crs,target,[p[0] for p in points],[p[1] for p in points])
        columns.append(math.hypot(xs[1]-xs[0],ys[1]-ys[0]))
        rows.append(math.hypot(xs[2]-xs[0],ys[2]-ys[0]))
    if src.crs == target: columns,rows = [native[0]],[native[1]]
    if any(not math.isfinite(v) or v <= 0 for v in columns+rows):
        raise ValueError('Pixel resolution cannot be represented in the analysis CRS')
    metric = [median(columns),median(rows)]
    sampled = [dict(minimum_m=min(v),representative_m=median(v),maximum_m=max(v),sample_count=len(v))
               for v in (columns,rows)]
    reference = max(metric)
    return dict(path=str(Path(src.name)), band=band, source_crs=str(src.crs),source_crs_wkt=src.crs.to_wkt(),
        analysis_crs_wkt=target.to_wkt(),shape=[src.height,src.width],affine=list(affine)[:6],
        analysis_crs=str(target), native_pixel_vectors=[[affine.a,affine.d],[affine.b,affine.e]],
        native_pixel_size=native, native_units=units, representative_pixel_size_m=metric,
        rotated_or_sheared=affine.b!=0 or affine.d!=0,
        projected_spacing=sampled,sample_locations_pixel=locations if src.crs!=target else [],
        spacing_method='affine norms' if src.crs==target else '3x3 sampled projected XY distances, not geodesic',
        representative_location='3x3 across raster', approximate=src.crs != target,
        spatially_variable=src.crs != target,
        sizing_reference_m=reference,
        suggestions=dict(uniform_h_m=reference,relief_h_min_m=reference,relief_h_max_m=4*reference,
            structured_width_m=metric[0],structured_height_m=metric[1]),
        suggestion_policy='pixel-reference-v1; illustrative 1x/4x, not optimized or enforced',
        disclaimer='Pixel resolution is source sampling, not accuracy; finer cells add no independent raster information')


def read(path, analysis_crs, band=1):
    with open_raster(path) as src:
        check_driver(src)
        return describe(src, analysis_crs, band)


def planning_reference(config):
    reference = config.get('resolution_reference')
    if reference:
        report = read(reference['path'], config['analysis_crs'], reference['band'])
        report['role'] = 'planned_extraction'
        from .extraction import _digest
        report['main_file_sha256']=_digest(Path(reference['path']))
        report['source_files_sha256']=snapshot(reference['path'])
        report['affects_mesh_computation']=False
        return report
    path = config['cell_source'].get('roi_raster_path')
    if path:
        report = read(path, config['analysis_crs'])
        report['role'] = 'roi_ascii_grid' if Path(path).suffix.lower()=='.asc' else 'roi_geotiff'
        return report
    return dict(role='none', status='unknown', reason='Vector/rectangle ROI has no raster pixel resolution')
