"""External Gmsh 4.x: bounded rectangular XY domains, no research CLI dependency."""
import json
import math
from pathlib import Path
import re
import subprocess
import tempfile
from types import MappingProxyType
from .job_control import current, checkpoint, observe, run_logged

import numpy as np
import rasterio
from .raster_source import open_raster, raster_snapshots, assert_rasters_unchanged, snapshot, assert_unchanged, HASH_SCOPE
from rasterio.transform import from_bounds
from rasterio.warp import reproject, Resampling
from rasterio.features import geometry_mask
from scipy.ndimage import maximum_filter, minimum_filter
from shapely.geometry import box, mapping
from shapely.ops import unary_union

from .adapters import CellSet
from .extraction import Cell, _cells, _digest
from .spatial import metric_crs
from .boundary import read_boundary, geometry_lines
from .meshing_parameters import (CONTROL_SHAPE, PERCENTILE_POSITIONS, RELIEF_GAMMA,
    GMSH_ALGORITHM, advanced_meshing_options, validate_advanced_meshing)
from .workload_limits import MAX_MESH_BYTES, MAX_MESH_NODES, MAX_MSH_ELEMENTS, GMSH_TIMEOUT_SECONDS


def mesh_domain(config, *, prepared=True):
    source=config['cell_source']; target=metric_crs(config['analysis_crs'])
    if source.get('boundary_path'):
        domain,evidence=read_boundary(source['boundary_path'],target)
        if prepared and 'boundary_preparation' in source:
            from .derived_boundary import prepare_boundary
            derived=prepare_boundary(domain,**source['boundary_preparation'])
            domain=derived.geometry
            evidence=dict(evidence,derived_boundary=derived.metadata,
                simplification_applied=True,area_m2=domain.area,bounds=list(domain.bounds),
                boundary_policy='opt-in validated derived meshing boundary; canonical SHP unchanged',
                warnings=['Đang dùng biên dẫn xuất đã kiểm định; SHP gốc và vùng chuẩn hóa relief được giữ nguyên.'])
        return domain,evidence
    if source.get('roi_raster_path'):
        from .indexed_extraction import footprint
        with open_raster(source['roi_raster_path']) as src:
            if src.crs is None: raise ValueError('Raster ROI thiếu CRS')
            subdivisions=config.get('edge_subdivisions',8)
            if not isinstance(subdivisions,int) or not 1<=subdivisions<=64: raise ValueError('ROI edge subdivisions must be 1..64')
            domain=footprint(src,target,0,0,src.width,src.height,subdivisions=subdivisions)
            return domain,dict(kind='ascii_grid_footprint' if Path(source['roi_raster_path']).suffix.lower()=='.asc' else 'geotiff_footprint',source_path=str(Path(source['roi_raster_path']).resolve()),
                source_sha256=_digest(Path(source['roi_raster_path'])),source_files_sha256=snapshot(source['roi_raster_path']),source_crs_wkt=src.crs.to_wkt(),
                analysis_crs_wkt=target.to_wkt(),affine=list(src.transform),width=src.width,height=src.height,
                policy='outer affine footprint; NoData/mask ignored for ROI',edge_subdivisions=subdivisions,
                bounds=list(domain.bounds),area_m2=domain.area,vertex_count=len(domain.exterior.coords)-1)
    return box(*source['extent']),None


def _decode_gmsh_output(data):
    """Decode subprocess bytes without locale-dependent surrogate escapes."""
    if isinstance(data, str):
        # Some text-mode subprocess decoders use surrogateescape on Windows.
        return ''.join('\ufffd' if 0xd800 <= ord(char) <= 0xdfff else char
                       for char in data)
    return data.decode('utf-8', errors='replace')


def inspect_executable(path):
    executable=Path(path).expanduser().resolve(strict=True)
    if not executable.is_file() or executable.name.lower() not in {'gmsh.exe','gmsh'}:
        raise ValueError('Chọn Gmsh 4.x executable: gmsh.exe / gmsh; nên tải từ gmsh.info')
    before=_digest(executable)
    if current() is None:
        result=subprocess.run([str(executable),'-version'],capture_output=True,
            timeout=8,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        version=(_decode_gmsh_output(result.stdout).strip() or
                 _decode_gmsh_output(result.stderr).strip())
    else:
        with tempfile.TemporaryDirectory(prefix='meshcherry-version-') as directory:
            log=Path(directory)/'version.log'
            result=run_logged([str(executable),'-version'],log,8)
            version=log.read_text(encoding='utf-8',errors='replace').strip()
    if result.returncode or not re.fullmatch(r'4\.\d+\.\d+',version):
        raise ValueError('Không xác nhận được Gmsh 4.x: '+version[:100])
    if _digest(executable)!=before: raise ValueError('Gmsh executable thay đổi khi kiểm tra')
    return dict(executable=str(executable),version=version,executable_sha256=before)


def inspect_domain(config, *, domain_info=None):
    source=config['cell_source']; target=metric_crs(config['analysis_crs'])
    if not source.get('boundary_path') and not source.get('roi_raster_path'):
        w,s,e,n=source['extent']
        if not all(math.isfinite(v) for v in (w,s,e,n)) or e<=w or n<=s:
            raise ValueError('Miền tăng dần và hữu hạn là bắt buộc')
    domain,boundary=mesh_domain(config) if domain_info is None else domain_info
    w,s,e,n=domain.bounds; lo,hi=source['h_min_m'],source['h_max_m']
    if not all(math.isfinite(v) for v in (w,s,e,n,lo,hi)) or e<=w or n<=s or not 0<lo<=hi:
        raise ValueError('Miền tăng dần và 0 < h_min ≤ h_max (m) là bắt buộc')
    if domain.area/lo**2>source['max_cells']*2:
        raise ValueError('Kích thước quá nhỏ cho giới hạn ô; tăng h_min hoặc giảm miền')
    if source['sizing'] in {'relief','sf1_relief'}:
        dem_path,dem_band=dem_source(config)
        with open_raster(dem_path) as src:
            if source['sizing']=='sf1_relief':
                from .sf1_sizing import inspect_source, inspect_coverage, H_BOUNDS_M
                inspect_source(src,dem_band,config['analysis_crs'])
                inspect_coverage(src,domain)
                if (lo,hi)!=H_BOUNDS_M: raise ValueError('Profile SF-1 dùng h_min=250 m, h_max=1000 m')
            else: inspect_relief_source(src,dem_band)
    advanced=validate_advanced_meshing(source)
    return dict(**inspect_executable(source['executable']),cell_count=None,coordinate_units='m',
        mesh_generation_required=True,extent=[w,s,e,n],sizing=source['sizing'],
        analysis_crs_wkt=target.to_wkt(),boundary=boundary,
        effective_meshing_controls=advanced)


def geo_meshing_controls(source):
    """Return only allowlisted overrides/actions; legacy defaults add no GEO text."""
    controls=validate_advanced_meshing(source)
    lines=[]
    if controls['algorithm_2d']!=GMSH_ALGORITHM:
        lines.append(f"Mesh.Algorithm={controls['algorithm_2d']};")
    if controls['smoothing'] or controls['optimize_2d']:
        lines.append('Mesh 2;')
        lines.extend(['OptimizeMesh "Laplace2D";']*controls['smoothing'])
        lines.extend(['OptimizeMesh "Relocate2D";']*controls['optimize_2d'])
        lines.append('Save "mesh.msh";')
    return lines


def dem_source(config):
    source=config['cell_source']
    if source.get('dem_raster_path'): return source['dem_raster_path'],source.get('dem_band',1)
    if config.get('schema_version')!='gis-workbench-run-config-v2' and config.get('raster_path'):
        if config.get('semantics')!='intensive': raise ValueError('DEM relief yêu cầu intensive')
        return config['raster_path'],config.get('band',1)
    raise ValueError('DEM required for relief sizing; chọn DEM và band cao độ')


def inspect_relief_source(src, band):
    if src.crs is None: raise ValueError('DEM thiếu CRS')
    if not 1 <= band <= src.count: raise ValueError('DEM band ngoài phạm vi')
    if np.issubdtype(np.dtype(src.dtypes[band-1]), np.complexfloating):
        raise ValueError('DEM relief không hỗ trợ raster số phức')
    if src.width*src.height>10000000: raise ValueError('DEM vượt giới hạn 10M pixel')
    if src.scales[band-1]!=1 or src.offsets[band-1]!=0:
        raise ValueError('DEM scale/offset phải là identity')


def size_field(config, directory):
    """Exploratory 33x33 control raster. Extraction still reads original pixels."""
    observe('size_field')
    dem_path,dem_band=dem_source(config);before=_digest(Path(dem_path))
    raster_before=snapshot(dem_path)
    source=config['cell_source']; domain,_=mesh_domain(config,prepared=False); w,s,e,n=map(float,domain.bounds)
    lo,hi=source['h_min_m'],source['h_max_m']; shape=CONTROL_SHAPE
    transform=from_bounds(w,s,e,n,*shape[::-1]); data=np.full(shape,np.nan)
    with open_raster(dem_path) as src:
        inspect_relief_source(src,dem_band)
        raster=src.read(dem_band,masked=True).astype('float64')
        original=raster.filled(np.nan); original[~np.isfinite(original)]=np.nan
        reproject(original,data,src_transform=src.transform,src_crs=src.crs,src_nodata=np.nan,
            dst_transform=transform,dst_crs=config['analysis_crs'],dst_nodata=np.nan,
            resampling=Resampling.nearest)
    valid=np.isfinite(data)
    support=np.ones(shape,dtype=bool)
    if source.get('boundary_path') or source.get('roi_raster_path'):
        domain,_=mesh_domain(config,prepared=False)
        support=geometry_mask([mapping(domain)],out_shape=shape,transform=transform,invert=True)
        valid &= support
    if not valid.any(): raise ValueError('Miền chia không có cao độ DEM hợp lệ')
    radius=source['relief_radius_m']
    rx=min(32,max(1,math.ceil(radius/transform.a))); ry=min(32,max(1,math.ceil(radius/abs(transform.e))))
    high=maximum_filter(np.where(valid,data,-np.inf),size=(2*ry+1,2*rx+1),mode='constant',cval=-np.inf)
    low=minimum_filter(np.where(valid,data,np.inf),size=(2*ry+1,2*rx+1),mode='constant',cval=np.inf)
    relief=np.zeros(shape); relief[valid]=high[valid]-low[valid]
    p25,p95=map(float,np.percentile(relief[valid],PERCENTILE_POSITIONS))
    complexity=np.zeros(shape)
    if p95>p25: complexity[valid]=np.clip((relief[valid]-p25)/(p95-p25),0,1)
    h=hi-(hi-lo)*complexity**RELIEF_GAMMA; h[~valid]=hi
    # Mesh field vertices at padded cell centres cover the exact rectangle.
    x=np.r_[w,np.linspace(w+transform.a/2,e-transform.a/2,33),e]
    y=np.r_[n,np.linspace(n+transform.e/2,s-transform.e/2,33),s]
    h=np.pad(h,1,mode='edge'); lines=['View "DEM local relief target size" {']
    for r in range(34):
        checkpoint()
        for c in range(34):
            for indices in (((r,c),(r,c+1),(r+1,c+1)),((r,c),(r+1,c+1),(r+1,c))):
                xyz=','.join(f'{x[j]:.17g},{y[i]:.17g},0' for i,j in indices)
                values=','.join(f'{h[i,j]:.17g}' for i,j in indices)
                lines.append(f'ST({xyz}){{{values}}};')
    lines.append('};'); (directory/'size.pos').write_text('\n'.join(lines),encoding='utf-8')
    if _digest(Path(dem_path))!=before: raise ValueError('DEM thay đổi trong lúc tạo size field')
    assert_unchanged(dem_path,raster_before)
    evidence=dict(method='exploratory_local_relief_p25_p95_gamma2',control_shape=list(shape),
        resampling='nearest for sizing only; extraction uses original raster',radius_m=radius,
        support_policy='valid controls inside domain; outside and holes excluded from relief and normalization',
        effective_window_pixels=[2*ry+1,2*rx+1],control_spacing_m=[transform.a,abs(transform.e)],
        effective_half_window_m=[rx*transform.a,ry*abs(transform.e)],
        neighbourhood='rectangular controls, not a circular radius',percentile_positions=list(PERCENTILE_POSITIONS),p25=p25,p95=p95,gamma=RELIEF_GAMMA,
        valid_control_fraction=float(valid.sum()/support.sum()),domain_control_count=int(support.sum()),
        valid_control_count=int(valid.sum()),valid_fraction_full_control_grid=float(valid.mean()),missing_size_policy='h_max',
        constant_relief_policy='h_max',gradation_limiter='none; not SF-1 calibrated field',
        dem_source_path=str(Path(dem_path).resolve()),dem_band=dem_band,control_bounds=[w,s,e,n],target_size_field_min_m=float(h.min()),target_size_field_max_m=float(h.max()),source_sha256=before)
    if config.get('schema_version')!='gis-workbench-run-config-v2':
        evidence.update(h_min_realized=float(h.min()),h_max_realized=float(h.max()))  # Legacy aliases only.
    evidence.update(source_files_sha256=raster_before,hash_scope=HASH_SCOPE)
    (directory/'size-field.json').write_text(json.dumps(evidence,indent=2),encoding='utf-8')
    return evidence


def read_triangles(path, target, maximum):
    if path.stat().st_size>MAX_MESH_BYTES: raise ValueError('MSH vượt ngân sách 64 MiB')
    lines=path.read_text(encoding='utf-8').splitlines()
    if lines[:3]!=['$MeshFormat','2.2 0 8','$EndMeshFormat']: raise ValueError('Cần MSH 2.2 ASCII')
    def section(name, limit):
        if lines.count('$'+name)!=1 or lines.count('$End'+name)!=1:
            raise ValueError('MSH thiếu/trùng section '+name)
        start=lines.index('$'+name); end=lines.index('$End'+name)
        count=int(lines[start+1])
        if not 0<count<=limit or end!=start+count+2:
            raise ValueError('MSH sai số lượng/truncated section '+name)
        return lines[start+2:end]
    node_lines=section('Nodes',MAX_MESH_NODES)
    count=len(node_lines)
    if not 0<count<=MAX_MESH_NODES: raise ValueError('Số đỉnh mesh vượt ngân sách')
    vertices={}
    for line in node_lines:
        if len(vertices)%256==0: checkpoint()
        tag,x,y,z=line.split(); xy=(float(x),float(y))
        if not np.isfinite([*xy,float(z)]).all() or abs(float(z))>1e-8: raise ValueError('MSH không phẳng XY')
        if int(tag) in vertices: raise ValueError('Trùng node tag')
        vertices[int(tag)]=xy
    element_lines=section('Elements',MAX_MSH_ELEMENTS); count=len(element_lines); cells=[]; seen=set()
    if not 0<count<=MAX_MSH_ELEMENTS: raise ValueError('Số phần tử mesh vượt ngân sách')
    for line in element_lines:
        if len(seen)%256==0: checkpoint()
        values=list(map(int,line.split()))
        if len(values)<3 or values[2]<0 or values[0] in seen:
            raise ValueError('MSH element header không hợp lệ')
        seen.add(values[0]); kind=values[1]
        if kind not in (1,2,15): raise ValueError('MSH có phần tử không hỗ trợ')
        tags=values[3+values[2]:]
        if len(tags)!={1:2,2:3,15:1}[kind] or any(t not in vertices for t in tags):
            raise ValueError('MSH node reference không hợp lệ')
        if kind==2:
            if len(tags)!=3 or len(set(tags))!=3: raise ValueError('Tam giác không hợp lệ')
            cells.append(Cell('cell-'+str(len(cells)),'triangle',tuple(vertices[t] for t in tags)))
            if len(cells)>maximum: raise ValueError('Gmsh tạo quá nhiều ô; tăng kích thước hoặc giảm miền')
    if not cells: raise ValueError('Gmsh không tạo tam giác')
    _cells(cells)
    return tuple(cells),vertices


def generate(config, output):
    from .runner import write_json
    observe('domain')
    raster_inputs=raster_snapshots(config)
    source=config['cell_source']; target=metric_crs(config['analysis_crs'])
    domain,boundary=mesh_domain(config)
    evidence=inspect_domain(config,domain_info=(domain,boundary))
    directory=output/'meshing'; directory.mkdir()
    w,s,e,n=domain.bounds; hi=source['h_max_m']; lo=source['h_min_m']
    if boundary!=evidence.get('boundary'): raise ValueError('Bộ SHP thay đổi trước khi tạo miền')
    config=dict(config,cell_source=dict(source,extent=[w,s,e,n]))
    if source['sizing']=='sf1_relief':
        from .sf1_sizing import write_field
        canonical_domain,_=mesh_domain(config,prepared=False)
        field=write_field(config,directory,canonical_domain)
        field['normalization_geometry']='canonical ROI; unchanged by optional meshing boundary preparation'
    else:
        field=size_field(config,directory) if source['sizing']=='relief' else dict(method='uniform',requested_target_size_m=hi)
    lines=['Mesh.MshFileVersion=2.2;','Mesh.Binary=0;','Mesh.ElementOrder=1;',f'Mesh.Algorithm={GMSH_ALGORITHM};',
        'Mesh.MeshSizeFactor=1;',
        'Mesh.MeshSizeFromPoints=0;','Mesh.MeshSizeFromCurvature=0;','Mesh.MeshSizeExtendFromBoundary=0;',
        f'Mesh.MeshSizeMin={lo:.17g};',f'Mesh.MeshSizeMax={hi:.17g};']
    geo_controls=geo_meshing_controls(source)
    lines+=geometry_lines(domain)
    write_json(directory/'domain-boundary.json',dict(geometry=mapping(domain),crs_wkt=target.to_wkt()))
    if source['sizing']=='sf1_relief':
        lines+=['Field[1]=Structured;','Field[1].FileName="size.dat";','Field[1].TextFormat=1;','Background Field=1;']
    else:
        lines+=['Merge "size.pos";','Field[1]=PostView;','Field[1].ViewIndex=0;','Background Field=1;'] if source['sizing']=='relief' else [
            'Field[1]=MathEval;',f'Field[1].F="{hi:.17g}";','Background Field=1;']
    lines+=geo_controls
    geo=directory/'domain.geo'; geo.write_text('\n'.join(lines),encoding='utf-8')
    observe('meshing')
    effective_controls=advanced_meshing_options(source)
    explicitly_meshed=bool(effective_controls['smoothing'] or effective_controls['optimize_2d'])
    command=[evidence['executable'],str(geo)]
    command.append('-parse_and_exit' if explicitly_meshed else '-2')
    command+=['-format','msh2','-o',str(directory/'mesh.msh'),'-nopopup','-nt','1']
    result=run_logged(command,
        directory/'gmsh.log',GMSH_TIMEOUT_SECONDS,cwd=directory)
    if result.returncode: raise ValueError('Gmsh thất bại; xem meshing/gmsh.log')
    if _digest(Path(evidence['executable']))!=evidence['executable_sha256']: raise ValueError('Executable thay đổi')
    if source.get('boundary_path'):
        from .boundary import bundle_paths
        if {p.name:_digest(p) for p in bundle_paths(source['boundary_path'])}!=boundary['files_sha256']:
            raise ValueError('Bộ SHP thay đổi trong khi meshing')
    observe('mesh_read')
    cells,vertices=read_triangles(directory/'mesh.msh',target,source['max_cells'])
    observe('quality')
    polygons=_cells(cells); union=unary_union(polygons)
    if union.symmetric_difference(domain).area>max(1e-8,domain.area*1e-8): raise ValueError('Lưới không phủ đúng miền')
    tags=list(vertices); lookup={t:i for i,t in enumerate(tags)}; xylookup={xy:lookup[t] for t,xy in vertices.items()}
    write_json(directory/'mesh.json',dict(schema_version='mesh-artifact-v0',crs=target.to_wkt(),coordinate_units='m',
        vertices=list(vertices.values()),triangles=[[xylookup[p] for p in c.xy] for c in cells]))
    components=list(domain.geoms) if domain.geom_type=='MultiPolygon' else [domain]
    evidence.update(size_field=field,effective_meshing_controls=effective_controls,
        requested_meshing_controls=source.get('advanced'),cell_count=len(cells),domain_coverage='pass',
        component_count=len(components),hole_count=sum(len(p.interiors) for p in components),
        domain_area_m2=domain.area,mesh_union_area_m2=union.area,symmetric_difference_area_m2=union.symmetric_difference(domain).area,
        coverage_reference='validated derived meshing domain' if source.get('boundary_preparation') else 'canonical selected ROI',
        boundary_status='UNVERIFIED',graph_kind='cell centres; no routing',mesh_sha256=_digest(directory/'mesh.msh'))
    assert_rasters_unchanged(raster_inputs)
    evidence['raster_inputs']=raster_inputs
    write_json(directory/'generator.json',evidence)
    return CellSet(cells,target.to_wkt(),'m','gmsh-generated',MappingProxyType(evidence)),evidence
