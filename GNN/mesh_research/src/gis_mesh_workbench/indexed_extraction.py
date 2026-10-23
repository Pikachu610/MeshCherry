"""Bounded window reads + cell STRtree, with a retained reference oracle.

Cross-CRS window culling is deliberately disabled: a densified window edge
alone is not a guaranteed enclosure of every transformed interior pixel.
Pixel geometry still follows the reference's exact densification policy.
"""
import ctypes
import math
from pathlib import Path
import time

import numpy as np
import rasterio
from .raster_source import open_raster, raster_snapshots, assert_rasters_unchanged, snapshot, assert_unchanged, HASH_SCOPE
from rasterio.crs import CRS
from rasterio.warp import transform
from rasterio.windows import Window
import shapely
from shapely.geometry import Polygon
from shapely.ops import unary_union
from shapely.strtree import STRtree

from .extraction import _cells, _digest
from .spatial import metric_crs

CEILINGS = dict(max_source_pixels=10000000,max_pixels_read=2000000,
                max_candidate_pairs=5000000,max_window_pixels=65536)
MAX_WINDOWS = 100000
MAX_CLASS_ENTRIES = 100000


def _require_finite_json_numbers(value):
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError('extraction produced a nonfinite JSON number')
    if isinstance(value, dict):
        for item in value.values(): _require_finite_json_numbers(item)
    elif isinstance(value, (list, tuple)):
        for item in value: _require_finite_json_numbers(item)


def _category_key(value):
    # Preserve the existing key spelling for ordinary numeric class codes.
    # Large integer IDs need their original decimal spelling, without float64.
    if isinstance(value, int) and abs(value) <= 2**53:
        return str(float(value))
    return str(value)


def _category_value(value):
    # JSON numbers become doubles in the UI. Keep otherwise lossy IDs as text.
    if isinstance(value, int) and abs(value) > 2**53:
        return str(value)
    return float(value)


def limits(values=None):
    result = dict(CEILINGS)
    if values is not None:
        if not isinstance(values,dict) or set(values)-set(CEILINGS):
            raise ValueError('unknown indexed limits')
        result.update(values)
    if any(type(v) is not int or not 1 <= v <= CEILINGS[k] for k,v in result.items()):
        raise ValueError('indexed hard ceilings cannot be raised')
    return result


def process_memory():
    """Process lifetime peak RSS, not isolated extraction allocation or GDAL cap."""
    try:
        if hasattr(ctypes,'windll'):
            from ctypes import wintypes
            class Counters(ctypes.Structure):
                _fields_=[('cb',wintypes.DWORD),('faults',wintypes.DWORD)]+[
                    (name,ctypes.c_size_t) for name in ('peak','working','page_peak','page',
                    'nonpage_peak','nonpage','pagefile','pagefile_peak')]
            counter=Counters(); counter.cb=ctypes.sizeof(counter)
            kernel=ctypes.windll.kernel32
            kernel.GetCurrentProcess.restype=wintypes.HANDLE
            ctypes.windll.psapi.GetProcessMemoryInfo.argtypes=[wintypes.HANDLE,ctypes.c_void_p,wintypes.DWORD]
            if not ctypes.windll.psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(),ctypes.byref(counter),counter.cb):
                raise OSError('GetProcessMemoryInfo failed')
            value=int(counter.peak)
        else:
            import resource, sys
            value=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            value=int(value if sys.platform=='darwin' else value*1024)
        return dict(name='process_lifetime_peak_rss_bytes',value=value,status='measured',
                    scope='entire current process lifetime; includes other work; not isolated extraction')
    except Exception as error:
        return dict(name='process_lifetime_peak_rss_bytes',value=None,status='unavailable',reason=str(error))


def footprint(src, target, col, row, width=1, height=1, subdivisions=8):
    corners=[(col,row),(col+width,row),(col+width,row+height),(col,row+height)]
    n=1 if src.crs==target else subdivisions
    points=[src.transform @ (a[0]+(b[0]-a[0])*k/n,a[1]+(b[1]-a[1])*k/n)
            for a,b in zip(corners,corners[1:]+corners[:1]) for k in range(n)]
    if src.crs!=target:
        x,y=transform(src.crs,target,*zip(*points)); points=list(zip(x,y))
    if not np.isfinite(points).all(): raise ValueError('nonfinite reprojected footprint')
    polygon=Polygon(points)
    if not polygon.is_valid or polygon.area<=0: raise ValueError('invalid pixel/window footprint')
    return polygon


def metadata(src, band, cap):
    if src.crs is None: raise ValueError('source raster CRS missing')
    if type(band) is not int or not 1<=band<=src.count: raise ValueError('band out of range')
    if src.scales[band-1]!=1 or src.offsets[band-1]!=0:
        raise ValueError('non-identity band scale/offset unsupported')
    if not np.isfinite(tuple(src.transform)).all() or src.transform.determinant==0:
        raise ValueError('finite nonsingular raster affine required')
    if np.issubdtype(np.dtype(src.dtypes[band-1]),np.complexfloating):
        raise ValueError('complex rasters unsupported')
    if cap['max_source_pixels'] is not None and src.width*src.height>cap['max_source_pixels']:
        raise ValueError('source pixel limit exceeded')


def windows(src, band, cap):
    side=max(1,math.isqrt(cap['max_window_pixels']))
    for _,block in src.block_windows(band):
        for row in range(int(block.row_off),int(block.row_off+block.height),side):
            for col in range(int(block.col_off),int(block.col_off+block.width),side):
                yield Window(col,row,min(side,int(block.col_off+block.width)-col),
                             min(side,int(block.row_off+block.height)-row))


def window_candidates(src,target,tree,window,cell_count):
    if src.crs!=target:
        # Conservative: do not guess that transformed window boundary encloses
        # every transformed pixel. No silent reference fallback.
        return range(cell_count)
    return tree.query(footprint(src,target,window.col_off,window.row_off,window.width,window.height))


def estimate(path,cells,*,analysis_crs,band,hard_limits=None):
    from .job_control import checkpoint
    target=metric_crs(analysis_crs); cap=limits(hard_limits)
    from .workload_limits import MAX_MESH_CELLS
    if not 0<len(cells)<=MAX_MESH_CELLS: raise ValueError('indexed cell limit exceeded')
    polygons=_cells(cells); tree=STRtree(polygons)
    read_pixels=scanned=read_windows=0
    with open_raster(path) as src:
        metadata(src,band,cap)
        for window in windows(src,band,cap):
            checkpoint()
            scanned+=1
            if scanned>MAX_WINDOWS: raise ValueError('window scan limit exceeded')
            if len(window_candidates(src,target,tree,window,len(cells))):
                read_windows+=1; read_pixels+=int(window.width*window.height)
                if read_pixels>cap['max_pixels_read']: raise ValueError('estimated read pixel limit exceeded')
        # Inverse-affine enclosure only for same CRS; cross CRS reads all windows.
        bound=unary_union(polygons).bounds
        source_window=None
        if src.crs==target:
            coords=[~src.transform @ p for p in ((bound[0],bound[1]),(bound[0],bound[3]),
                                                (bound[2],bound[1]),(bound[2],bound[3]))]
            source_window=dict(col_min=min(src.width,max(0,math.floor(min(p[0] for p in coords)))),
                row_min=min(src.height,max(0,math.floor(min(p[1] for p in coords)))),
                col_max=max(0,min(src.width,math.ceil(max(p[0] for p in coords)))),
                row_max=max(0,min(src.height,math.ceil(max(p[1] for p in coords)))))
        return dict(engine='indexed_windowed_v1',source_pixels=src.width*src.height,
            cell_count=len(cells),cell_bounds=list(bound),conservative_source_window=source_window,
            estimated_pixels_read=read_pixels,estimated_windows_read=read_windows,
            windows_scanned=scanned,hard_limits=cap,
            window_culling='affine_cell_index' if src.crs==target else 'disabled_cross_crs_conservative')


def extract_indexed(path,cells,*,cell_crs,analysis_crs,band,semantics,value_units,
                    edge_subdivisions=8,hard_limits=None):
    from .job_control import checkpoint, observe
    started=time.perf_counter(); target=metric_crs(analysis_crs); cap=limits(hard_limits)
    if cell_crs is None or CRS.from_user_input(cell_crs)!=target:
        raise ValueError('cells must explicitly use analysis CRS; relabeling forbidden')
    if semantics not in {'intensive','extensive','categorical'} or not value_units:
        raise ValueError('declared semantics and value units required')
    if type(edge_subdivisions) is not int or not 1<=edge_subdivisions<=64:
        raise ValueError('edge_subdivisions must be an integer from 1 to 64')
    from .workload_limits import MAX_MESH_CELLS
    if not 0<len(cells)<=MAX_MESH_CELLS: raise ValueError('indexed cell limit exceeded')
    polygons=_cells(cells); tree=STRtree(polygons); union=unary_union(polygons)
    records=[dict(cell_id=c.cell_id,kind=c.kind,area_m2=float(p.area),footprint_area_m2=0.,valid_area_m2=0.)
             for c,p in zip(cells,polygons)]
    # Keep intensive moments normalized by the largest absolute sample. Direct
    # Welford differences can overflow for finite values such as +/-1e308,
    # which would leak NaN into the JSON report.
    acc=[dict(weight=0.,scale=0.,mean=0.,variance=0.,total=0.,
              minimum=math.inf,maximum=-math.inf,classes={}) for _ in cells]
    counters=dict(source_pixels=0,windows_scanned=0,windows_read=0,pixels_read=0,
                  candidate_pairs=0,positive_intersections=0,peak_window_pixels=0,cell_count=len(cells))
    path=Path(path); before=_digest(path); union_total=0.; class_entries=0
    raster_before=snapshot(path)
    with open_raster(path) as src:
        metadata(src,band,cap); counters['source_pixels']=src.width*src.height
        scanned_pixels=0
        for window in windows(src,band,cap):
            observe('extraction',scanned_pixels,src.width*src.height)
            scanned_pixels+=int(window.width*window.height)
            counters['windows_scanned']+=1
            if counters['windows_scanned']>MAX_WINDOWS: raise ValueError('window scan limit exceeded')
            if not len(window_candidates(src,target,tree,window,len(cells))): continue
            count=int(window.width*window.height)
            if counters['pixels_read']+count>cap['max_pixels_read']: raise ValueError('read pixel limit exceeded')
            counters['windows_read']+=1; counters['pixels_read']+=count
            counters['peak_window_pixels']=max(counters['peak_window_pixels'],count)
            data=src.read(band,window=window,masked=True)
            values=np.asarray(data.data) if semantics=='categorical' else np.asarray(data.data,float)
            valid=~np.ma.getmaskarray(data)&np.isfinite(values)
            for r in range(int(window.height)):
                observe('extraction',scanned_pixels-int(window.width*window.height)+r*int(window.width),src.width*src.height)
                for c in range(int(window.width)):
                    if c%128==0: checkpoint()
                    pixel=footprint(src,target,int(window.col_off)+c,int(window.row_off)+r,subdivisions=edge_subdivisions)
                    candidates=sorted(tree.query(pixel).tolist())
                    if counters['candidate_pairs']+len(candidates)>cap['max_candidate_pairs']:
                        raise ValueError('candidate pair limit exceeded')
                    counters['candidate_pairs']+=len(candidates)
                    is_valid=bool(valid[r,c]); value=values[r,c].item() if semantics=='categorical' else float(values[r,c])
                    if semantics=='extensive' and is_valid and candidates:
                        union_total+=value*pixel.intersection(union).area/pixel.area
                    for position,i in enumerate(candidates):
                        if position%128==0: checkpoint()
                        area=float(pixel.intersection(polygons[i]).area)
                        if area<=0: continue
                        counters['positive_intersections']+=1; rec=records[i]; state=acc[i]
                        rec['footprint_area_m2']+=area
                        if not is_valid: continue
                        rec['valid_area_m2']+=area
                        weight=state['weight']+area
                        if semantics!='categorical':
                            old_weight=state['weight']
                            scale=max(state['scale'],abs(value))
                            if scale:
                                rescale=state['scale']/scale if state['scale'] else 0.
                                old_mean=state['mean']*rescale
                                state['variance']*=rescale*rescale
                                normalized=value/scale
                                delta=normalized-old_mean
                                state['mean']=old_mean+delta*(area/weight)
                                state['variance']=(state['variance']*(old_weight/weight)
                                    +delta*delta*(old_weight/weight)*(area/weight))
                                state['scale']=scale
                            state['minimum']=min(state['minimum'],value); state['maximum']=max(state['maximum'],value)
                            state['total']+=value*area/pixel.area
                        state['weight']=weight
                        if semantics=='categorical':
                            if value not in state['classes']:
                                class_entries+=1
                                if class_entries>MAX_CLASS_ENTRIES: raise ValueError('categorical accumulator limit exceeded')
                            state['classes'][value]=state['classes'].get(value,0.)+area
        nodata=src.nodatavals[band-1]
        provenance=dict(source_path=str(path.resolve()),source_sha256=before,hash_scope=HASH_SCOPE,source_files_sha256=raster_before,
            band_dtype=src.dtypes[band-1],band_scale=src.scales[band-1],band_offset=src.offsets[band-1],
            band_nodata=nodata if nodata is None or math.isfinite(nodata) else str(nodata),
            band_mask_flags=[f.name for f in src.mask_flag_enums[band-1]],source_crs_wkt=src.crs.to_wkt(),
            analysis_crs_wkt=target.to_wkt(),affine=list(src.transform),shape=[src.height,src.width],band=band,
            xy_policy='GIS X,Y; geographic longitude,latitude',edge_subdivisions=edge_subdivisions,
            raster_resampling='none',area_model='projected planar; cross-CRS edges approximated',
            transformation_accuracy='not independently verified',
            categorical_value_encoding='integer IDs beyond abs(2**53) are exact decimal strings; other values are numbers' if semantics=='categorical' else None,
            extensive_basis='total_per_source_pixel' if semantics=='extensive' else None,
            value_units=value_units,semantics=semantics,rasterio_version=rasterio.__version__,
            gdal_version=rasterio.__gdal_version__,shapely_version=shapely.__version__,hard_limits=cap,
            window_culling='affine_cell_index' if src.crs==target else 'disabled_cross_crs_conservative')
    assert_unchanged(path,raster_before)
    if _digest(path)!=before: raise ValueError('source changed during extraction')
    for rec,state in zip(records,acc):
        checkpoint()
        rec.update(footprint_coverage=rec['footprint_area_m2']/rec['area_m2'],
                   valid_coverage=rec['valid_area_m2']/rec['area_m2'],value=None)
        if rec['footprint_coverage']>1+1e-8: raise ValueError('source footprints overlap or invalid coverage')
        if not state['weight']: continue
        if semantics=='intensive':
            rec.update(value=state['scale']*state['mean'],minimum=state['minimum'],maximum=state['maximum'],
                       std=state['scale']*math.sqrt(max(0.,state['variance'])))
        elif semantics=='extensive': rec['value']=state['total']
        else:
            classes=state['classes']; codes=sorted(classes)
            rec.update(class_fractions={_category_key(code):classes[code]/state['weight'] for code in codes},
                       value=_category_value(max(codes,key=lambda code:classes[code])))
    qa=dict(main_raster_unchanged=True)
    if semantics=='extensive':
        total=sum(rec['value'] for rec in records if rec['value'] is not None)
        tolerance=max(1e-10,abs(union_total)*1e-9)
        if abs(total-union_total)>tolerance: raise ValueError('extensive conservation failed')
        qa.update(source_total_on_cell_union=union_total,cell_total=total,residual=total-union_total,tolerance=tolerance)
    evidence=dict(extraction_engine='indexed_windowed_v1',**counters,
                  runtime_seconds=time.perf_counter()-started,memory_metric=process_memory(),
                  hard_limits=dict(**cap,max_windows_scanned=MAX_WINDOWS,max_class_entries=MAX_CLASS_ENTRIES))
    qa['engine_evidence']=evidence
    result=dict(schema_version='cell-attributes-indexed-v1',cells=records,provenance=provenance,qa=qa)
    _require_finite_json_numbers(result)
    return result
