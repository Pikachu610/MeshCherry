"""Mesh-only artifacts and exact-cell reuse by the extraction pipeline."""
import hashlib
import json
import math
import time
import uuid
from pathlib import Path
from types import MappingProxyType

from shapely.ops import unary_union

from .adapters import CellSet, _json
from .extraction import Cell, _cells, _digest
from .spatial import metric_crs
from .workload_limits import MAX_MESH_BYTES
from .build_info import identity as application_identity
from .job_control import Cancelled, checkpoint, current, observe, finalize_manifest


def geometry_digest(cells, crs):
    payload=dict(crs_wkt=metric_crs(crs).to_wkt(),coordinate_units='m',
        cells=[dict(cell_id=c.cell_id,kind=c.kind,xy=[[float(x),float(y)] for x,y in c.xy]) for c in cells])
    return hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def read_frozen(path, analysis_crs, maximum=1000, expected_digest=None):
    path=Path(path)
    if path.stat().st_size>MAX_MESH_BYTES: raise ValueError('Frozen mesh exceeds 64 MiB')
    digest=_digest(path); data=_json(path)
    manifest_path=path.parent/'manifest.json'
    if not manifest_path.is_file(): raise ValueError('Frozen mesh requires successful mesh-only manifest')
    if manifest_path.exists():
        manifest=_json(manifest_path)
        if manifest.get('status')!='pass' or manifest.get('operation')!='mesh_only' or manifest.get('artifact_hashes',{}).get(path.name)!=digest:
            raise ValueError('Frozen mesh manifest/hash mismatch')
    if data.get('schema_version')!='workbench-cells-v1' or data.get('coordinate_units')!='m':
        raise ValueError('Unsupported frozen mesh')
    crs=metric_crs(data['crs_wkt'])
    if crs!=metric_crs(analysis_crs): raise ValueError('Frozen mesh CRS mismatch')
    rows=data['cells']
    if not isinstance(rows,list) or not 0<len(rows)<=maximum: raise ValueError('Frozen cell limit')
    cells=tuple(Cell(r['cell_id'],r['kind'],tuple(tuple(p) for p in r['xy'])) for r in rows)
    _cells(cells)
    identity=geometry_digest(cells,crs)
    if identity!=manifest.get('geometry_digest') or (expected_digest is not None and identity!=expected_digest):
        raise ValueError('Frozen geometry digest mismatch')
    if _digest(path)!=digest: raise ValueError('Frozen mesh changed during read')
    return CellSet(cells,crs.to_wkt(),'m','frozen_cells',MappingProxyType(dict(source_sha256=digest,source_path=str(path),geometry_digest=identity)))


def quality(cells):
    polygons=_cells(cells); edges=[]; angles=[]; aspect=[]
    for c,p in zip(cells,polygons):
        checkpoint()
        lengths=[math.dist(a,b) for a,b in zip(c.xy,c.xy[1:]+c.xy[:1])]
        edges.extend(lengths)
        if c.kind=='triangle':
            for i,a in enumerate(lengths):
                b,d=lengths[(i+1)%3],lengths[(i+2)%3]
                angles.append(math.degrees(math.acos(max(-1,min(1,(b*b+d*d-a*a)/(2*b*d))))))
            aspect.append(max(lengths)**2/(2*p.area))
    union=unary_union(polygons); areas=[p.area for p in polygons]
    return dict(cell_count=len(cells),vertex_count=len({p for c in cells for p in c.xy}),
        total_area_m2=sum(areas),union_area_m2=union.area,overlap_area_m2=max(0,sum(areas)-union.area),
        area_min_m2=min(areas),area_max_m2=max(areas),edge_min_m=min(edges),edge_max_m=max(edges),
        triangle_min_angle_deg=min(angles) if angles else None,
        triangle_max_aspect=max(aspect) if aspect else None,
        aspect_definition='longest edge / altitude to longest edge',
        centres=[dict(cell_id=c.cell_id,x=p.centroid.x,y=p.centroid.y) for c,p in zip(cells,polygons)])


def run_mesh(path,config,progress=None):
    from .runner import preflight, prepare_cells, write_json, preview
    from .gmsh_meshing import generate
    started=time.perf_counter(); config_hash=_digest(path)
    root=Path(config['output_root'])
    inputs=[path]
    if config.get('resolution_reference'): inputs.append(Path(config['resolution_reference']['path']))
    for key in ('raster_path',):
        if config.get(key): inputs.append(Path(config[key]))
    source=config['cell_source']
    for key in ('path','roi_raster_path','dem_raster_path'):
        if source.get(key): inputs.append(Path(source[key]))
    if source.get('boundary_path'):
        from .boundary import bundle_paths
        inputs.extend(bundle_paths(source['boundary_path']))
    if any(root.resolve()==p.resolve().parent or root.resolve() in p.resolve().parents for p in inputs):
        raise ValueError('Output must be isolated from inputs')
    input_hashes={str(p):_digest(p) for p in inputs}
    from .raster_source import raster_snapshots, assert_rasters_unchanged, HASH_SCOPE
    raster_bundles=raster_snapshots(config)
    for files in raster_bundles.values():input_hashes.update(files)
    output=root/('mesh-'+uuid.uuid4().hex);output.mkdir(parents=True,exist_ok=False)
    if current() is not None: current().output=output
    manifest=dict(schema_version='gis-workbench-run-manifest-v0',operation='mesh_only',status='fail',
        application_build=application_identity(),
        config_sha256=config_hash,input_hashes=input_hashes,effective_contract=dict(analysis_crs_wkt=metric_crs(config['analysis_crs']).to_wkt()))
    manifest.update(raster_inputs=raster_bundles,hash_scope=HASH_SCOPE)
    try:
        observe('preflight')
        report,cells=preflight(config);write_json(output/'preflight.json',report)
        if report['status']!='pass': raise ValueError('; '.join(report['errors']))
        if progress:
            try: progress('meshing' if source['kind']=='gmsh_generated' else 'cells')
            except Exception: pass
        if source['kind']=='gmsh_generated':
            cells,evidence=generate(config,output);manifest['gmsh_generated']=evidence
        elif source['kind']=='gmsh_reference':
            raise ValueError('Reference preset is not a mesh-first ROI source')
        elif cells is None: cells=prepare_cells(config)
        observe('quality')
        write_json(output/'cells.json',dict(schema_version='workbench-cells-v1',crs_wkt=cells.crs_wkt,
            coordinate_units='m',cells=[dict(cell_id=c.cell_id,kind=c.kind,xy=c.xy) for c in cells.cells]))
        identity=geometry_digest(cells.cells,cells.crs_wkt);manifest['geometry_digest']=identity
        qa=quality(cells.cells);qa['geometry_digest']=identity
        from .meshing_parameters import request_summary
        sizing_summary=dict(report['meshing_request'])
        if manifest.get('gmsh_generated'):
            evidence=manifest['gmsh_generated']
            qa.update(domain_symmetric_difference_m2=evidence['symmetric_difference_area_m2'],
                domain_area_m2=evidence['domain_area_m2'],domain_coverage=evidence['domain_coverage'],
                component_count=evidence['component_count'],hole_count=evidence['hole_count'])
            sizing_summary['size_field']=evidence['size_field']
        qa['sizing_summary']=sizing_summary;manifest['meshing_request']=sizing_summary
        write_json(output/'qa.json',dict(status='pass',mesh_quality=qa,sizing_summary=sizing_summary))
        observe('serialization')
        preview(output/'preview.png',cells.cells)
        if _digest(path)!=config_hash: raise ValueError('Mesh config changed during run')
        if any(_digest(Path(p))!=digest for p,digest in input_hashes.items()): raise ValueError('Mesh input changed during run')
        assert_rasters_unchanged(raster_bundles)
        manifest['status']='pass'
    except Cancelled as error:
        manifest.update(status='cancelled',error=str(error),cancelled_stage=current().phase if current() else 'unknown')
        write_json(output/'qa.json',dict(status='cancelled',error=str(error)))
    except Exception as error:
        manifest['error']=str(error)
        write_json(output/'qa.json',dict(status='fail',error=str(error)))
    write_json(output/'config.json',config)
    manifest.update(runtime_seconds=time.perf_counter()-started)
    finalize_manifest(output,manifest)
    return dict(status=manifest['status'],output=str(output),error=manifest.get('error'))
