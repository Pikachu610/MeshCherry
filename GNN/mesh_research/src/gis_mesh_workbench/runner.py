"""Bounded config-driven workflow shared by CLI and future UI."""
import csv
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import time
import uuid

import jsonschema
import rasterio
from .raster_source import open_raster, raster_snapshots, assert_rasters_unchanged, snapshot, assert_unchanged, HASH_SCOPE
from shapely.geometry import Polygon

from .adapters import import_mesh, structured_grid, _json
from .extraction import extract_attributes, _digest
from .spatial import metric_crs
from .gmsh_reference import inspect_preset, generate_reference
from .gmsh_meshing import inspect_domain, generate as generate_gmsh
from .indexed_extraction import extract_indexed, estimate, limits as indexed_limits, process_memory
from .build_info import identity
from .job_control import Cancelled, checkpoint, current, observe, finalize_manifest
from .failure_details import describe_failure


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def effective_config(path):
    path = Path(path).resolve()
    config = _json(path)
    # Serialization also rejects numeric overflow constants such as 1e999.
    json.dumps(config, allow_nan=False)
    if not isinstance(config, dict):
        raise ValueError('config must be an object')
    config = json.loads(json.dumps(config))
    for field in ('raster_path', 'output_root'):
        if isinstance(config.get(field), str) and config[field]:
            config[field] = str((path.parent / config[field]).resolve())
    if isinstance(config.get('resolution_reference'),dict) and config['resolution_reference'].get('path'):
        config['resolution_reference']['path']=str((path.parent/config['resolution_reference']['path']).resolve())
    if isinstance(config.get('overview_dem'),dict) and config['overview_dem'].get('path'):
        config['overview_dem']['path']=str((path.parent/config['overview_dem']['path']).resolve())
    source = config.get('cell_source')
    if isinstance(source, dict) and isinstance(source.get('path'), str):
        source['path'] = str((path.parent / source['path']).resolve())
    if isinstance(source,dict) and source.get('boundary_path'):
        source['boundary_path']=str((path.parent/source['boundary_path']).resolve())
    if isinstance(source,dict) and source.get('roi_raster_path'):
        source['roi_raster_path']=str((path.parent/source['roi_raster_path']).resolve())
    if isinstance(source,dict) and source.get('dem_raster_path'):
        source['dem_raster_path']=str((path.parent/source['dem_raster_path']).resolve())
    config.setdefault('edge_subdivisions', 8)
    if config.get('schema_version') in {'gis-workbench-run-config-v1','gis-workbench-run-config-v2'}:
        config['indexed_limits'] = indexed_limits(config.get('indexed_limits'))
    else:
        config.setdefault('max_pixels', 10000)
        config.setdefault('max_pairs', 1000000)
    if isinstance(source, dict):
        from .workload_limits import cell_cap
        source.setdefault('max_cells', cell_cap(config))
    return config


def _friendly_schema_error(error):
    """Render schema failures without echoing the rejected instance/config."""
    labels={'config':'Cấu hình','cell_source':'Nguồn lưới','roi_raster_path':'raster ROI',
            'boundary_path':'bộ SHP','extent':'miền chữ nhật (extent)',
            'dem_raster_path':'DEM','dem_band':'band DEM','executable':'Gmsh',
            'h_min_m':'h_min_m','h_max_m':'h_max_m','max_cells':'max_cells',
            'output_root':'thư mục kết quả','analysis_crs':'CRS phân tích',
            'run_name':'tên lần chạy','raster_path':'raster dữ liệu',
            'band':'band dữ liệu','sizing':'cách tính kích thước'}
    parts=list(error.path)
    safe_parts=[]
    for part in parts:
        if isinstance(part,int):safe_parts.append(f'mục {part}')
        else:safe_parts.append(labels.get(str(part),'trường'))
    path=(' › '.join(safe_parts) or 'Cấu hình')[:120]
    if error.validator in {'oneOf','anyOf','allOf'}:
        return f'{path}: cấu hình chưa khớp dạng được hỗ trợ; kiểm tra trường bắt buộc và lựa chọn mâu thuẫn.'
    if error.validator=='required' and isinstance(error.instance,dict):
        missing=sorted(set(error.validator_value)-set(error.instance))
        if missing:
            names=', '.join(labels.get(name,name) for name in missing)
            return f'{path}: còn thiếu trường bắt buộc {names}.'
    if error.validator=='additionalProperties':
        return f'{path}: có trường không hỗ trợ. Kiểm tra tên trường theo loại nguồn đã chọn.'
    if error.validator=='minLength':
        return f'{path}: không được để trống.'
    if error.validator=='type':
        expected=error.validator_value
        names={'object':'một đối tượng','array':'một danh sách','string':'văn bản',
               'number':'một số','integer':'một số nguyên','boolean':'đúng/sai'}
        if isinstance(expected,list):expected=' hoặc '.join(names.get(item,item) for item in expected)
        else:expected=names.get(expected,expected)
        return f'{path}: cần có giá trị dạng {expected}.'
    if error.validator in {'enum','const'}:
        values=error.validator_value if error.validator=='enum' else [error.validator_value]
        return f'{path}: chọn một giá trị hợp lệ ({", ".join(map(str,values))}).'
    if error.validator in {'minimum','exclusiveMinimum','maximum','exclusiveMaximum'}:
        bound=error.validator_value
        relation={'minimum':'không nhỏ hơn','exclusiveMinimum':'lớn hơn',
                  'maximum':'không lớn hơn','exclusiveMaximum':'nhỏ hơn'}[error.validator]
        return f'{path}: cần {relation} {bound}.'
    return f'{path}: chưa đáp ứng yêu cầu cấu hình.'


def validate_config(config):
    if not isinstance(config,dict):
        raise ValueError('Cấu hình phải là một đối tượng JSON.')
    source=config.get('cell_source')
    if not isinstance(source,dict):source={}
    if 'boundary_preparation' in source:
        preparation=source['boundary_preparation']
        keys={'tolerance_m','max_area_change_fraction','max_boundary_distance_m'}
        if (source.get('kind')!='gmsh_generated' or not source.get('boundary_path')
                or not isinstance(preparation,dict) or not keys.issubset(preparation)
                or set(preparation)-keys-{'method'}):
            raise ValueError('Biên dẫn xuất chỉ dùng với miền SHP; cần đủ dung sai, giới hạn diện tích và khoảng cách.')
        if (not isinstance(preparation.get('method','topology_preserving'),str)
                or preparation.get('method','topology_preserving') not in {'topology_preserving','gnn_closed_rdp'}):
            raise ValueError('Phương pháp xử lý biên không hợp lệ; chọn giữ topology hoặc RDP vòng kín GNN.')
        if any(isinstance(preparation[k],bool) or not isinstance(preparation[k],(int,float)) or not math.isfinite(preparation[k]) or preparation[k]<=0 for k in keys):
            raise ValueError('Các giới hạn biên dẫn xuất phải là số dương hữu hạn.')
        if preparation['max_area_change_fraction']>.05:
            raise ValueError('Giới hạn diện tích khác biệt của biên dẫn xuất không được vượt 5%.')
    if source.get('kind')=='gmsh_generated' and 'advanced' in source:
        from .meshing_parameters import validate_advanced_meshing
        validate_advanced_meshing(source)
    if config.get('schema_version')=='gis-workbench-run-config-v2' and source.get('kind')=='gmsh_generated':
        if ('roi_raster_path' in source and source.get('roi_raster_path')==''
                and not source.get('extent') and not source.get('boundary_path')):
            guidance='Chọn ranh giới ROI: GeoTIFF/ASC, bộ SHP hoặc miền chữ nhật. '
            guidance+=('DEM đã chọn dùng để tính relief.' if source.get('dem_raster_path')
                       else 'Có thể chọn DEM để tính relief.')
            raise ValueError(guidance)
        domain_count=sum(bool(source.get(key)) for key in ('extent','boundary_path','roi_raster_path'))
        if domain_count==0:
            raise ValueError('Chọn ranh giới ROI: GeoTIFF/ASC, bộ SHP hoặc miền chữ nhật. '
                             'DEM dùng để tính relief.')
        if domain_count!=1:
            raise ValueError('Chỉ chọn một nguồn ROI: extent / SHP / raster')
        if source.get('sizing')=='uniform':
            if source.get('h_min_m')!=source.get('h_max_m'): raise ValueError('Uniform yêu cầu h_min = h_max: một kích thước mục tiêu h')
            if any(key in source for key in ('dem_raster_path','dem_band','relief_radius_m')):
                raise ValueError('Uniform không dùng DEM, band DEM hoặc bán kính relief')
    if config.get('schema_version')=='gis-workbench-run-config-v2' and source.get('sizing') in {'relief','sf1_relief'} and not source.get('dem_raster_path'):
        raise ValueError('DEM required for relief sizing; chọn DEM và band cao độ')
    if config.get('schema_version')=='gis-workbench-run-config-v2' and source.get('sizing')=='sf1_relief':
        if source.get('dem_value_units')!='m':
            raise ValueError('SF-1 yêu cầu khai báo giá trị cao độ DEM theo mét')
        if source.get('h_min_m')!=250 or source.get('h_max_m')!=1000:
            raise ValueError('Hồ sơ SF-1 yêu cầu h_min=250 m, h_max=1000 m')
        if 'sf1_scale' not in source:
            raise ValueError('SF-1 yêu cầu khai báo sf1_scale trong khoảng 0,25–4')
        scale=source['sf1_scale']
        if isinstance(scale,bool) or not isinstance(scale,(int,float)) or not math.isfinite(scale) or not .25<=scale<=4:
            raise ValueError('Hệ số SF-1 phải hữu hạn trong khoảng 0,25–4')
    schema_name={'gis-workbench-run-config-v1':'run-config-v1.schema.json','gis-workbench-run-config-v2':'run-config-v2.schema.json'}.get(config.get('schema_version'),'run-config.schema.json')
    schema = _json(Path(__file__).with_name(schema_name))
    if config.get('schema_version') == 'gis-workbench-run-config-v1':
        schema['properties']['operation']={'enum':['mesh_only','extract']}
        if config.get('operation')=='mesh_only':
            schema['required'].remove('raster_path')
        schema['properties']['cell_source']['oneOf'].append(dict(type='object',additionalProperties=False,
            required=['kind','path'],properties=dict(kind={'const':'frozen_cells'},
            path={'type':'string','minLength':1},max_cells={'type':'integer','minimum':1,'maximum':1000})))
        schema['properties']['cell_source']['oneOf'][0]['properties']['roi_raster_path']={'type':'string','minLength':1}
        schema['properties']['cell_source']['oneOf'][3]['properties']['roi_raster_path']={'type':'string','minLength':1}
    errors = sorted(jsonschema.Draft202012Validator(schema).iter_errors(config), key=lambda e: str(e.path))
    if errors:
        rendered=[]
        for error in errors:
            message=_friendly_schema_error(error)
            if message not in rendered:rendered.append(message)
            if len(rendered)==4:break
        raise ValueError('; '.join(rendered))


def prepare_cells(config):
    source = config['cell_source']
    if source['kind'] == 'frozen_cells':
        from .mesh_workflow import read_frozen
        return read_frozen(source['path'],config['analysis_crs'],source['max_cells'],source.get('geometry_digest'))
    if source['kind'] == 'mesh_artifact':
        return import_mesh(source['path'], analysis_crs=config['analysis_crs'], max_cells=source['max_cells'])
    if source.get('roi_raster_path'):
        from .gmsh_meshing import mesh_domain
        from shapely.geometry import box
        domain,_=mesh_domain(config)
        if domain.symmetric_difference(box(*domain.bounds)).area>max(1e-8,domain.area*1e-10):
            raise ValueError('ROI is not axis-aligned; use Gmsh to preserve footprint')
        if list(source['extent'])!=list(domain.bounds): raise ValueError('Structured extent does not match ROI footprint')
    return structured_grid(extent=source['extent'], cell_width_m=source['cell_width_m'],
        cell_height_m=source['cell_height_m'], boundary_policy=source['boundary_policy'],
        analysis_crs=config['analysis_crs'], max_cells=source['max_cells'])


def preflight(config):
    """Returns visible validation object and prepared immutable cells, or None."""
    source=config.get('cell_source') if isinstance(config,dict) else None
    report = dict(status='fail', errors=[],
                  cell_source_kind=source.get('kind') if isinstance(source,dict) else None)
    cells = None
    try:
        validate_config(config)
        target = metric_crs(config['analysis_crs'])
        if config.get('operation')=='mesh_only':
            if config['cell_source']['kind']=='gmsh_generated':
                report.update(inspect_domain(config))
            else:
                cells=prepare_cells(config)
                report.update(cell_count=len(cells.cells),analysis_crs_wkt=target.to_wkt())
            report.update(status='pass',pixel_count=0,operation='mesh_only')
            from .meshing_parameters import request_summary
            report['meshing_request']=request_summary(config,report.get('boundary'))
            return report,cells
        report.update(analysis_crs_wkt=target.to_wkt(), semantics=config['semantics'],
                      value_units=config['value_units'], raster_path=config['raster_path'],
                      limits=(config['indexed_limits'] if config.get('extraction_engine') == 'indexed_windowed_v1'
                              else {k:config[k] for k in ('max_pixels','max_pairs','edge_subdivisions')}))
        if config['cell_source']['kind'] == 'gmsh_reference':
            _, preset_report = inspect_preset(config['analysis_crs'],config['cell_source']['preset'])
            report.update(preset_report,coordinate_units='m')
        elif config['cell_source']['kind'] == 'gmsh_generated':
            report.update(inspect_domain(config))
        else:
            cells = prepare_cells(config)
            report.update(cell_count=len(cells.cells), cell_crs_wkt=cells.crs_wkt, coordinate_units='m',geometry_digest=cells.provenance.get('geometry_digest'))
        with open_raster(config['raster_path']) as src:
            band = config['band']
            if src.crs is None:
                raise ValueError('raster CRS missing')
            from .raster_resolution import describe
            report['raster_resolution']=describe(src,config['analysis_crs'],band)
            if not 1 <= band <= src.count:
                raise ValueError('band out of range')
            nodata = src.nodatavals[band-1]
            report.update(raster_crs_wkt=src.crs.to_wkt(), shape=[src.height,src.width],
                pixel_count=src.width*src.height, band=band, dtype=src.dtypes[band-1],
                nodata=str(nodata) if nodata is not None else None,
                mask_flags=[f.name for f in src.mask_flag_enums[band-1]],
                scale=src.scales[band-1], offset=src.offsets[band-1])
            if src.scales[band-1] != 1 or src.offsets[band-1] != 0:
                raise ValueError('non-identity band scale/offset unsupported')
            if config.get('extraction_engine') == 'indexed_windowed_v1':
                if src.width*src.height > config['indexed_limits']['max_source_pixels']:
                    raise ValueError('source pixel limit exceeded')
                report['extraction_engine'] = config['extraction_engine']
            elif (src.width*src.height > config['max_pixels']
                    or (cells is not None and (src.width*src.height*len(cells.cells) > config['max_pairs']
                    or len(cells.cells)**2 > config['max_pairs']))):
                raise ValueError('reference workload limit exceeded; large-data mode not yet available')
        if config.get('extraction_engine') == 'indexed_windowed_v1' and cells is not None:
            report['workload_estimate'] = estimate(config['raster_path'],cells.cells,
                analysis_crs=config['analysis_crs'],band=config['band'],hard_limits=config['indexed_limits'])
        report['status'] = 'pass'
    except Exception as error:
        report['status']='fail'
        report['errors'].append(str(error))
    return report, cells


def preview(path, cells):
    checkpoint()
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(9,6), facecolor='#fffafa')
    from matplotlib.collections import PolyCollection
    polygons = [Polygon(cell.xy) for cell in cells]
    ax.add_collection(PolyCollection([cell.xy for cell in cells],facecolors='#fff0f3',
                                    edgecolors='#dc244c',linewidths=.7))
    ax.scatter([p.centroid.x for p in polygons],[p.centroid.y for p in polygons],
               s=3,c='#b91537')
    ax.autoscale_view()
    ax.set_aspect('equal'); ax.set_title(f'Mesh Cherry · {len(cells)} cells · centres shown')
    ax.set_xlabel('X (m)'); ax.set_ylabel('Y (m)')
    try:
        fig.tight_layout(); fig.savefig(path,dpi=140)
    finally: plt.close(fig)
    checkpoint()


def run(config_path, progress=None):
    """Returns status/output/error. Parse errors raise; later errors retain run."""
    def emit(stage):
        observe(stage)
        if progress is not None:
            try:
                progress(stage)
            except Exception:
                pass  # Observational callbacks cannot change numerical behavior.
    emit('config')
    config_path = Path(config_path).resolve()
    config_hash = _digest(config_path)
    config = effective_config(config_path)
    if config.get('operation')=='mesh_only':
        from .mesh_workflow import run_mesh
        return run_mesh(config_path,config,progress)
    output_root = config.get('output_root')
    if not isinstance(output_root, str) or not output_root:
        raise ValueError('no trustworthy output_root; no diagnostic run created')
    root = Path(output_root).resolve()
    # Never write output into a source directory. Reject source ancestors too.
    sources = [config_path, Path(config.get('raster_path',''))]
    if isinstance(config.get('cell_source'), dict) and config['cell_source'].get('path'):
        cell_path=Path(config['cell_source']['path'])
        if config['cell_source']['kind']=='frozen_cells':
            if root==cell_path.parent.resolve(): raise ValueError('output_root must not be frozen mesh directory')
        else: sources.append(cell_path)
    if config.get('cell_source',{}).get('boundary_path'):
        from .boundary import bundle_paths
        sources.extend(bundle_paths(config['cell_source']['boundary_path']))
    if any(root == p.parent.resolve() or root in p.resolve().parents for p in sources):
        raise ValueError('output_root must be isolated from source paths')
    run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '-' + uuid.uuid4().hex[:8]
    output = root / run_id
    output.mkdir(parents=True, exist_ok=False)
    if current() is not None: current().output=output
    started = time.perf_counter()
    manifest = dict(schema_version='gis-workbench-run-manifest-v0', run_id=run_id,
        application_build=identity(),
        status='fail', config_path=str(config_path), config_sha256=config_hash,
        started_at=datetime.now(timezone.utc).isoformat(), hash_scope=HASH_SCOPE,
        limitations=['recognized same-dataset companions only','datum accuracy unverified','small reference kernel'])
    if config.get('extraction_engine') == 'indexed_windowed_v1':
        manifest['limitations'][-1] = 'bounded indexed/windowed engine; hard ceilings apply'
    stage = 'config'
    report = dict(status='fail', errors=['not reached'])
    try:
        write_json(output/'config.json',config)
        validate_config(config)
        raster_inputs=raster_snapshots(config)
        manifest['raster_inputs']=raster_inputs
        contract = dict(run_name=config['run_name'], cell_source_kind=config['cell_source']['kind'],
                        semantics=config['semantics'], value_units=config['value_units'],
                        effective_limits=(dict(**config['indexed_limits'],max_cells=config['cell_source']['max_cells'],
                            edge_subdivisions=config['edge_subdivisions'])
                            if config.get('extraction_engine') == 'indexed_windowed_v1' else dict(max_pixels=config['max_pixels'],
                            max_pairs=config['max_pairs'],max_cells=config['cell_source']['max_cells'],
                            edge_subdivisions=config['edge_subdivisions'])))
        contract['extraction_engine'] = config.get('extraction_engine','reference_v0')
        manifest['effective_contract'] = contract
        target = metric_crs(config['analysis_crs'])
        contract.update(analysis_crs_wkt=target.to_wkt(),coordinate_units='m')
        stage = 'preflight'
        emit(stage)
        report,cells = preflight(config)
        write_json(output/'preflight.json',report)
        if report['status'] != 'pass':
            raise ValueError('; '.join(report['errors']))
        if config['cell_source']['kind'] == 'gmsh_reference':
            stage = 'meshing'
            emit(stage)
            cells, generator_provenance = generate_reference(output,
                analysis_crs=config['analysis_crs'],preset=config['cell_source']['preset'],
                max_cells=config['cell_source']['max_cells'])
            manifest['gmsh_reference'] = generator_provenance
            stage = 'cells'
            if config.get('extraction_engine') == 'indexed_windowed_v1':
                report['workload_estimate'] = estimate(config['raster_path'],cells.cells,
                    analysis_crs=config['analysis_crs'],band=config['band'],hard_limits=config['indexed_limits'])
                write_json(output/'preflight.json',report)
            elif (len(cells.cells)**2 > config['max_pairs']
                    or report['pixel_count']*len(cells.cells) > config['max_pairs']):
                raise ValueError('generated cells exceed reference workload limits')
        elif config['cell_source']['kind'] == 'gmsh_generated':
            stage='meshing'; emit(stage)
            cells, generator_provenance=generate_gmsh(config,output)
            manifest['gmsh_generated']=generator_provenance
            report['workload_estimate']=estimate(config['raster_path'],cells.cells,
                analysis_crs=config['analysis_crs'],band=config['band'],hard_limits=config['indexed_limits'])
            write_json(output/'preflight.json',report)
        stage = 'cells'
        emit(stage)
        geometry = [dict(cell_id=c.cell_id,kind=c.kind,xy=c.xy) for c in cells.cells]
        write_json(output/'cells.json',dict(crs_wkt=cells.crs_wkt,coordinate_units='m',
                   cells=geometry,adapter_provenance=dict(cells.provenance)))
        stage = 'extraction'
        emit(stage)
        extractor = extract_indexed if config.get('extraction_engine') == 'indexed_windowed_v1' else extract_attributes
        extractor_options = (dict(hard_limits=config['indexed_limits'])
            if extractor is extract_indexed else dict(max_pixels=config['max_pixels'],max_pairs=config['max_pairs']))
        result = extractor(config['raster_path'],cells.cells,cell_crs=cells.crs_wkt,
            analysis_crs=config['analysis_crs'],band=config['band'],semantics=config['semantics'],
            value_units=config['value_units'],edge_subdivisions=config['edge_subdivisions'],
            **extractor_options)
        if config['cell_source']['kind']=='frozen_cells':
            from .mesh_workflow import geometry_digest, read_frozen
            accepted=cells.provenance['geometry_digest']
            read_frozen(config['cell_source']['path'],config['analysis_crs'],config['cell_source']['max_cells'],accepted)
            if geometry_digest(cells.cells,cells.crs_wkt)!=accepted: raise ValueError('Frozen geometry changed during extraction')
            if _digest(Path(config['cell_source']['path']))!=cells.provenance['source_sha256']: raise ValueError('Frozen mesh changed during extraction')
            manifest['geometry_digest']=accepted
            emitted=_json(output/'cells.json')
            from .extraction import Cell
            emitted_cells=tuple(Cell(r['cell_id'],r['kind'],tuple(tuple(p) for p in r['xy'])) for r in emitted['cells'])
            if geometry_digest(emitted_cells,emitted['crs_wkt'])!=accepted: raise ValueError('Serialized mesh geometry digest mismatch')
        assert_rasters_unchanged(raster_inputs)
        if _digest(config_path) != config_hash:
            raise ValueError('config changed during run')
        if config['cell_source']['kind']=='gmsh_generated':
            # ROI, DEM and extraction may deliberately be different datasets.
            # Each dataset was independently snapshotted and rechecked above.
            sizing=manifest['gmsh_generated']['size_field']
            if (sizing.get('dem_source_path')==str(Path(config['raster_path']).resolve())
                    and sizing.get('source_sha256')!=result['provenance']['source_sha256']):
                raise ValueError('DEM changed between sizing and extraction')
            boundary=manifest['gmsh_generated'].get('boundary')
            if config['cell_source'].get('boundary_path'):
                from .boundary import bundle_paths
                if {p.name:_digest(p) for p in bundle_paths(config['cell_source']['boundary_path'])}!=boundary['files_sha256']:
                    raise ValueError('SHP bundle changed between meshing and extraction')
        manifest.update(adapter_provenance=dict(cells.provenance), extraction_provenance=result['provenance'])
        if 'engine_evidence' in result['qa']:
            manifest['engine_evidence'] = result['qa']['engine_evidence']
        stage = 'serialization'
        emit(stage)
        write_json(output/'attributes.json',result)
        fields = ['cell_id','kind','area_m2','footprint_area_m2','valid_area_m2',
                  'footprint_coverage','valid_coverage','value','minimum','maximum','std','class_fractions']
        with (output/'attributes.csv').open('w',newline='',encoding='utf-8-sig') as stream:
            writer = csv.DictWriter(stream,fieldnames=fields,extrasaction='ignore')
            writer.writeheader()
            for record in result['cells']:
                checkpoint()
                row = dict(record)
                if 'class_fractions' in row:
                    row['class_fractions'] = json.dumps(row['class_fractions'],sort_keys=True,allow_nan=False)
                writer.writerow(row)
        preview(output/'preview.png',cells.cells)
        records = result['cells']
        qa = dict(status='pass',checks=dict(preflight='pass',cell_count=len(records),
            main_raster_unchanged=result['qa']['main_raster_unchanged'],serialization='pass',
            null_value_count=sum(r['value'] is None for r in records)),
            coverage={k:dict(minimum=min(r[k] for r in records),
                             mean=sum(r[k] for r in records)/len(records))
                      for k in ('footprint_coverage','valid_coverage')},
            bounds=[min(p[0] for c in cells.cells for p in c.xy),
                    min(p[1] for c in cells.cells for p in c.xy),
                    max(p[0] for c in cells.cells for p in c.xy),
                    max(p[1] for c in cells.cells for p in c.xy)],
            extraction_qa=result['qa'])
        if manifest.get('geometry_digest'): qa['geometry_digest']=manifest['geometry_digest']
        write_json(output/'qa.json',qa)
        assert_rasters_unchanged(raster_inputs)
        if _digest(config_path)!=config_hash:raise ValueError('config changed during serialization')
        manifest['status'] = 'pass'
    except Cancelled as error:
        manifest.update(status='cancelled',cancelled_stage=stage,error=str(error))
        write_json(output/'qa.json',dict(status='cancelled',cancelled_stage=stage,error=str(error)))
    except Exception as error:
        failure = describe_failure(error)
        manifest.update(failed_stage=stage,error=str(error),failure=failure)
        write_json(output/'preflight.json',report)
        write_json(output/'qa.json',dict(status='fail',failed_stage=stage,error=str(error),failure=failure))
    manifest.update(finished_at=datetime.now(timezone.utc).isoformat(),
                    runtime_seconds=time.perf_counter()-started,
                    peak_memory_status='not_measured')
    if config.get('extraction_engine') == 'indexed_windowed_v1':
        manifest['workbench_memory_metric'] = process_memory()
        manifest['peak_memory_status'] = manifest['workbench_memory_metric']['status']
    finalize_manifest(output,manifest)
    if progress is not None:
        try: progress('done' if manifest['status']=='pass' else manifest['status'])
        except Exception: pass
    result = dict(status=manifest['status'],output=str(output),error=manifest.get('error'))
    if manifest['status'] == 'fail':
        result['failure'] = manifest['failure']
    return result
