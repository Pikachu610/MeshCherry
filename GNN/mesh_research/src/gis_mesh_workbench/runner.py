"""Bounded config-driven workflow shared by CLI and future UI."""
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import time
import uuid

import jsonschema
import rasterio
from shapely.geometry import Polygon

from .adapters import import_mesh, structured_grid, _json
from .extraction import extract_attributes, _digest
from .spatial import metric_crs
from .gmsh_reference import inspect_preset, generate_reference
from .indexed_extraction import extract_indexed, estimate, limits as indexed_limits, process_memory


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
    source = config.get('cell_source')
    if isinstance(source, dict) and isinstance(source.get('path'), str):
        source['path'] = str((path.parent / source['path']).resolve())
    config.setdefault('edge_subdivisions', 8)
    if config.get('schema_version') == 'gis-workbench-run-config-v1':
        config['indexed_limits'] = indexed_limits(config.get('indexed_limits'))
    else:
        config.setdefault('max_pixels', 10000)
        config.setdefault('max_pairs', 1000000)
    if isinstance(source, dict):
        source.setdefault('max_cells', 1000)
    return config


def validate_config(config):
    schema = _json(Path(__file__).with_name('run-config-v1.schema.json'
        if config.get('schema_version') == 'gis-workbench-run-config-v1' else 'run-config.schema.json'))
    errors = sorted(jsonschema.Draft202012Validator(schema).iter_errors(config), key=lambda e: str(e.path))
    if errors:
        raise ValueError('; '.join(f'{".".join(map(str,e.path)) or "config"}: {e.message}' for e in errors))


def prepare_cells(config):
    source = config['cell_source']
    if source['kind'] == 'mesh_artifact':
        return import_mesh(source['path'], analysis_crs=config['analysis_crs'], max_cells=source['max_cells'])
    return structured_grid(extent=source['extent'], cell_width_m=source['cell_width_m'],
        cell_height_m=source['cell_height_m'], boundary_policy=source['boundary_policy'],
        analysis_crs=config['analysis_crs'], max_cells=source['max_cells'])


def preflight(config):
    """Returns visible validation object and prepared immutable cells, or None."""
    report = dict(status='fail', errors=[], cell_source_kind=config.get('cell_source',{}).get('kind'))
    cells = None
    try:
        validate_config(config)
        target = metric_crs(config['analysis_crs'])
        report.update(analysis_crs_wkt=target.to_wkt(), semantics=config['semantics'],
                      value_units=config['value_units'], raster_path=config['raster_path'],
                      limits=(config['indexed_limits'] if config.get('extraction_engine') == 'indexed_windowed_v1'
                              else {k:config[k] for k in ('max_pixels','max_pairs','edge_subdivisions')}))
        if config['cell_source']['kind'] == 'gmsh_reference':
            _, preset_report = inspect_preset(config['analysis_crs'],config['cell_source']['preset'])
            report.update(preset_report,coordinate_units='m')
        else:
            cells = prepare_cells(config)
            report.update(cell_count=len(cells.cells), cell_crs_wkt=cells.crs_wkt, coordinate_units='m')
        with rasterio.open(config['raster_path'], 'r') as src:
            band = config['band']
            if src.crs is None:
                raise ValueError('raster CRS missing')
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
        report['errors'].append(str(error))
    return report, cells


def preview(path, cells):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(9,6), facecolor='#fffafa')
    for cell in cells:
        polygon = Polygon(cell.xy)
        x,y = polygon.exterior.xy
        ax.fill(x,y,facecolor='#fff0f3',edgecolor='#dc244c',linewidth=.7)
        ax.plot(polygon.centroid.x, polygon.centroid.y, '.',color='#b91537',markersize=3)
    ax.set_aspect('equal'); ax.set_title(f'Mesh Cherry · {len(cells)} cells · centres shown')
    ax.set_xlabel('X (m)'); ax.set_ylabel('Y (m)')
    fig.tight_layout(); fig.savefig(path,dpi=140); plt.close(fig)


def run(config_path, progress=None):
    """Returns status/output/error. Parse errors raise; later errors retain run."""
    def emit(stage):
        if progress is not None:
            try:
                progress(stage)
            except Exception:
                pass  # Observational callbacks cannot change numerical behavior.
    emit('config')
    config_path = Path(config_path).resolve()
    config_hash = _digest(config_path)
    config = effective_config(config_path)
    output_root = config.get('output_root')
    if not isinstance(output_root, str) or not output_root:
        raise ValueError('no trustworthy output_root; no diagnostic run created')
    root = Path(output_root).resolve()
    # Never write output into a source directory. Reject source ancestors too.
    sources = [config_path, Path(config.get('raster_path',''))]
    if isinstance(config.get('cell_source'), dict) and config['cell_source'].get('path'):
        sources.append(Path(config['cell_source']['path']))
    if any(root == p.parent.resolve() or root in p.resolve().parents for p in sources):
        raise ValueError('output_root must be isolated from source paths')
    run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '-' + uuid.uuid4().hex[:8]
    output = root / run_id
    output.mkdir(parents=True, exist_ok=False)
    started = time.perf_counter()
    manifest = dict(schema_version='gis-workbench-run-manifest-v0', run_id=run_id,
        status='fail', config_path=str(config_path), config_sha256=config_hash,
        started_at=datetime.now(timezone.utc).isoformat(), hash_scope='main_raster_file_only',
        limitations=['raster sidecars not hashed','datum accuracy unverified','small reference kernel'])
    if config.get('extraction_engine') == 'indexed_windowed_v1':
        manifest['limitations'][-1] = 'bounded indexed/windowed engine; hard ceilings apply'
    stage = 'config'
    report = dict(status='fail', errors=['not reached'])
    try:
        write_json(output/'config.json',config)
        validate_config(config)
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
        if _digest(config_path) != config_hash:
            raise ValueError('config changed during run')
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
        write_json(output/'qa.json',qa)
        manifest['status'] = 'pass'
    except Exception as error:
        manifest.update(failed_stage=stage,error=str(error))
        write_json(output/'preflight.json',report)
        write_json(output/'qa.json',dict(status='fail',failed_stage=stage,error=str(error)))
    manifest.update(finished_at=datetime.now(timezone.utc).isoformat(),
                    runtime_seconds=time.perf_counter()-started,
                    peak_memory_status='not_measured',
                    artifact_hashes={p.relative_to(output).as_posix():_digest(p)
                                     for p in output.rglob('*') if p.is_file()})
    if config.get('extraction_engine') == 'indexed_windowed_v1':
        manifest['workbench_memory_metric'] = process_memory()
        manifest['peak_memory_status'] = manifest['workbench_memory_metric']['status']
    write_json(output/'manifest.json',manifest)
    emit('done' if manifest['status'] == 'pass' else 'failed')
    return dict(status=manifest['status'],output=str(output),error=manifest.get('error'))
