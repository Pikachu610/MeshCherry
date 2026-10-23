"""Portable entry point. Self-test exercises the frozen backend, not a mock."""
import argparse
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import threading
import http.client
import traceback

os.environ.setdefault('MPLBACKEND', 'Agg')


def self_test(report, gmsh_path=None, boundary_path=None):
    from gis_mesh_workbench.adapters import structured_grid, import_mesh
    from gis_mesh_workbench.extraction import extract_attributes
    from gis_mesh_workbench.onboarding import sample_directory
    from gis_mesh_workbench.webui import WorkbenchServer, ASSETS
    import webview
    import clr
    checks = {}
    from gis_mesh_workbench.failure_details import describe_failure
    failure = describe_failure('cells overlap in positive area')
    if failure['code'] != 'cell_overlap' or not failure['message'] or not failure['actions']:
        raise RuntimeError('Frozen actionable failure descriptions unavailable')
    checks['failure_guidance'] = dict(status='pass', code=failure['code'])
    with tempfile.TemporaryDirectory(prefix='meshcherry-smoke-') as directory:
        server = WorkbenchServer(directory)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            conn = http.client.HTTPConnection(*server.server_address, timeout=10)
            for asset in ('/', '/coach.js', '/app.js', '/wizard.css', '/attribute_table.js',
                          '/downloads.js','/extraction_draft.js','/extraction_draft.css','/attribute_table.css','/workspace_layout.css',
                          '/quality_panel.js','/quality_panel.css','/quality_bridge.js',
                          '/comparison_view.js','/comparison_view.css','/fonts/SourceSans3-VF.ttf'):
                conn.request('GET', asset, headers={'Host': server.host})
                response = conn.getresponse()
                body = response.read()
                if response.status != 200 or not body:
                    raise RuntimeError('Missing packaged asset: '+asset)
            conn.close()
            checks['local_assets'] = 'pass'
            conn = http.client.HTTPConnection(*server.server_address, timeout=20)
            conn.request('POST','/api/input-preview',body=json.dumps(dict(kind='raster',
                path=str(sample_directory()/'sample_10_30.tif'),band=1)),
                headers={'Host':server.host,'Origin':server.origin,'X-Workbench-Token':server.token,
                    'Content-Type':'application/json'})
            response=conn.getresponse();preview=json.loads(response.read());conn.close()
            if response.status!=200 or not preview.get('image','').startswith('data:image/png;base64,'):
                raise RuntimeError('Frozen source preview failed: '+str(preview.get('error')))
            checks['source_preview_api']=dict(status='pass',source_shape=preview['source_shape'])
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=5)
    root = sample_directory()
    from gis_mesh_workbench.quality_assessment import evaluate_mesh_quality
    from gis_mesh_workbench.quality_context import enrich_quality
    from gis_mesh_workbench.quality_reports import export_quality
    sample_mesh=dict(schema_version='workbench-cells-v1',crs_wkt='EPSG:32649',coordinate_units='m',
        cells=[dict(cell_id='qa-right',kind='triangle',xy=[[0.,0.],[1.,0.],[0.,1.]])])
    assessed=evaluate_mesh_quality(sample_mesh)
    enrich_quality(assessed,sample_mesh)
    if not math.isclose(assessed['elements'][0]['quality_q'],0.8284271247461901,rel_tol=1e-12):
        raise RuntimeError('Frozen triangle quality known answer failed')
    for fmt in ('json','csv','html'):
        body,_,_=export_quality(assessed,fmt)
        if not body:raise RuntimeError('Frozen quality export failed')
    checks['mesh_quality_assessment']=dict(status='pass',known_answer=True,exports=['json','csv','html'])
    grid = structured_grid(extent=[500000,1000000,500002,1000001],
        cell_width_m=1,cell_height_m=1,analysis_crs='EPSG:32649')
    triangle = import_mesh(root/'two_triangles.json',analysis_crs='EPSG:32649')
    # Exercise the new count/admission/failure modules in the actual frozen
    # runtime even when an external Gmsh installation is not supplied.
    from gis_mesh_workbench.pixel_inventory import supports_hierarchy, hierarchical_count
    from gis_mesh_workbench.comparison_admission import estimate_read_workload, ComparisonAdmissionError
    from gis_mesh_workbench.comparison_failures import describe_comparison_failure
    from gis_mesh_workbench.comparison_policy import comparison_limits
    from gis_mesh_workbench.indexed_extraction import limits
    from gis_mesh_workbench.raster_source import open_raster
    from gis_mesh_workbench.spatial import metric_crs
    from shapely.geometry import Polygon, box
    from shapely.strtree import STRtree
    target=metric_crs('EPSG:32649')
    tree=STRtree([Polygon(cell.xy) for cell in triangle.cells])
    with open_raster(root/'sample_10_30.tif') as source:
        if not supports_hierarchy(source,target):
            raise RuntimeError('Frozen native pixel hierarchy unavailable')
        count=hierarchical_count(source,box(500000,1000000,500002,1000001),
            target,(0,0,2,1))
        if count!=2:raise RuntimeError('Frozen geometric pixel count differs')
        budget=estimate_read_workload(source,target,tree,len(triangle.cells),
            band=1,semantics='intensive',cap=limits())
        if budget['aggregate_pixels_read']!=4 or budget['pass_count']!=2:
            raise RuntimeError('Frozen comparison two-pass admission differs')
        try:
            estimate_read_workload(source,target,tree,len(triangle.cells),
                band=1,semantics='intensive',cap=limits({'max_pixels_read':3}))
        except ComparisonAdmissionError as error:
            guidance=describe_comparison_failure(error)
            if not guidance['actions'] or not guidance['message']:
                raise RuntimeError('Frozen comparison failure guidance unavailable')
        else:
            raise RuntimeError('Frozen comparison admitted an over-budget job')
        research_cap=comparison_limits('research',limits({'max_pixels_read':3}))
        research=estimate_read_workload(source,target,tree,len(triangle.cells),
            band=1,semantics='intensive',cap=research_cap,workload_mode='research')
        if (research_cap['max_pixels_read'] is not None
                or research['estimate_kind']!='metadata_upper_bound'
                or research['aggregate_pixels_read_upper_bound']!=4):
            raise RuntimeError('Frozen research comparison policy differs')
        checks['research_comparison_policy']=dict(status='pass',
            total_read_ceiling=None,read_upper_bound=4,max_window_pixels=research_cap['max_window_pixels'])
    checks['pixel_inventory_and_admission']=dict(status='pass',geometric_pixels=count,
        intensive_read_pixels=4,rejected_cap=3,failure_guidance=True)
    for name, cells, expected in (('grid',grid,[10,30]),('triangles',triangle,[25,15])):
        result = extract_attributes(root/'sample_10_30.tif',cells.cells,
            cell_crs='EPSG:32649',analysis_crs='EPSG:32649',band=1,
            semantics='intensive',value_units='synthetic-value')
        values = [row['value'] for row in result['cells']]
        if values != expected:
            raise RuntimeError(f'{name} result differs: {values}')
        checks[name] = values
    from gis_mesh_workbench.batch_extraction import run_batch,preview_batch,export_wide_csv,export_json
    from gis_mesh_workbench.mesh_workflow import geometry_digest
    with tempfile.TemporaryDirectory(prefix='meshcherry-batch-smoke-') as directory:
        database=Path(directory)/'batch.sqlite'
        features=[dict(name=name,path=str(root/'sample_10_30.tif'),band=1,
                       semantics='intensive',value_units='synthetic-value') for name in ('first','second')]
        batch=run_batch(database,triangle.cells,features,cell_crs='EPSG:32649',analysis_crs='EPSG:32649',
                        accepted_geometry_digest=geometry_digest(triangle.cells,'EPSG:32649'))
        page=preview_batch(database,limit=10)
        if batch['status']!='complete' or page['total_cells']!=2:
            raise RuntimeError('Frozen batch extraction failed')
        actual=sorted(row['values']['first'] for row in page['rows'])
        if len(actual)!=2 or not all(math.isclose(value,expected,rel_tol=1e-12,abs_tol=1e-12)
                                    for value,expected in zip(actual,[15,25])):
            raise RuntimeError('Frozen batch attribute values differ')
        export_wide_csv(database,Path(directory)/'batch.csv')
        export_json(database,Path(directory)/'batch.json')
        checks['batch_extraction']=dict(status='pass',features=2,cells=2,preview=True,exports=['csv','json'])
        from gis_mesh_workbench.extraction_draft import prepare_draft, canonical_wire_state, preview_draft
        digest=geometry_digest(triangle.cells,'EPSG:32649')
        draft=prepare_draft('frozen-mesh',digest,triangle.cells,features,None)
        preview=preview_draft(draft,limit=1)
        if preview['rows'][0]['values']['raster_0']!={'state':'not_computed'}:
            raise RuntimeError('Frozen draft preview fabricated raster values')
        wire=canonical_wire_state(draft)
        wire['columns'][0]['name']='Renamed'
        wire['columns'].append(dict(id='note',type='manual',name='Note',value_type='text'))
        wire['rows']=[dict(cell_id=triangle.cells[0].cell_id,values={'note':'reviewed'})]
        wire['manual_rows']=[dict(row_id='annotation',values={'note':'manual'})]
        prepared=prepare_draft('frozen-mesh',digest,triangle.cells,features,wire)
        plan=dict(parent_geometry_digest=digest,plan_hash=prepared['plan_digest'],
                  columns=prepared['columns'],overrides=prepared['overrides'],
                  manual_rows=prepared['manual_rows'],excluded_cell_ids=[])
        draft_db=Path(directory)/'draft.sqlite'
        run_batch(draft_db,triangle.cells,prepared['features'],cell_crs='EPSG:32649',
                  analysis_crs='EPSG:32649',accepted_geometry_digest=digest,draft_plan=plan)
        result=preview_batch(draft_db,limit=10)
        if result['rows'][0]['values']['raster_0']['state']!='computed':
            raise RuntimeError('Frozen draft column identity changed after extraction')
        from gis_mesh_workbench.batch_extraction import export_long_csv
        export_wide_csv(draft_db,Path(directory)/'draft-wide.csv')
        export_long_csv(draft_db,Path(directory)/'draft-long.csv')
        export_json(draft_db,Path(directory)/'draft.json')
        checks['extraction_draft']=dict(status='pass',previews=True,manual_rows=True,
                                        renamed_columns=True,exports=['wide_csv','long_csv','json'])
    if 'Bé' in (ASSETS/'index.html').read_text(encoding='utf-8'):
        raise RuntimeError('Old UI wording remains')
    checks['pythonnet_import'] = 'pass'
    checks['frozen'] = bool(getattr(sys,'frozen',False))
    # Exercise the real GDAL ASCII driver inside the frozen distribution.
    from gis_mesh_workbench.raster_source import open_raster
    from rasterio.crs import CRS
    with tempfile.TemporaryDirectory(prefix='meshcherry-asc-smoke-') as directory:
        asc=Path(directory)/'sample.asc'
        asc.write_text('ncols 2\nnrows 1\nxllcorner 500000\nyllcorner 1000000\ncellsize 1\nNODATA_value -9999\n10 30\n',encoding='ascii')
        asc.with_suffix('.prj').write_text(CRS.from_epsg(32649).to_wkt(),encoding='ascii')
        with open_raster(asc) as src:
            if src.driver!='AAIGrid' or src.crs.to_epsg()!=32649:raise RuntimeError('ASC driver/CRS failed')
        for name,cells,expected in (('grid',grid,[10,30]),('triangles',triangle,[25,15])):
            values=[r['value'] for r in extract_attributes(asc,cells.cells,cell_crs='EPSG:32649',
                analysis_crs='EPSG:32649',band=1,semantics='intensive',value_units='m')['cells']]
            if values!=expected:raise RuntimeError('ASC extraction differs: '+str(values))
            checks['ascii_'+name]=values
    # Offline project reopening and new export code must also work when frozen.
    from gis_mesh_workbench.project_io import save_project, load_project
    from gis_mesh_workbench.indexed_extraction import extract_indexed
    from gis_mesh_workbench.exports import export_cells
    from gis_mesh_workbench.mesh_workflow import quality
    import zipfile
    with tempfile.TemporaryDirectory(prefix='meshcherry-project-smoke-') as directory:
        destination=Path(directory)
        payload=dict(schema_version='workbench-cells-v1',crs_wkt=triangle.crs_wkt,
            coordinate_units='m',cells=[dict(cell_id=c.cell_id,kind=c.kind,xy=[list(p) for p in c.xy]) for c in triangle.cells])
        attributes=extract_indexed(root/'sample_10_30.tif',triangle.cells,
            cell_crs=triangle.crs_wkt,analysis_crs=triangle.crs_wkt,band=1,
            semantics='intensive',value_units='synthetic-value')
        save_project(destination/'example.mcy',cells=payload,
            qa=dict(status='pass',mesh_quality=quality(triangle.cells)),attributes=attributes,
            view_settings=dict(show_edges=True,show_nodes=True,result_tab='extract'))
        restored=load_project(destination/'example.mcy')
        startup_workspace=destination/'startup-session';startup_workspace.mkdir()
        startup_server=WorkbenchServer(startup_workspace,startup_project=destination/'example.mcy')
        startup_thread=threading.Thread(target=startup_server.serve_forever,daemon=True)
        startup_thread.start()
        try:
            conn=http.client.HTTPConnection(*startup_server.server_address,timeout=20)
            headers={'Host':startup_server.host,'Origin':startup_server.origin,
                     'X-Workbench-Token':startup_server.token,'Content-Type':'application/json'}
            conn.request('POST','/api/project/startup',headers=headers)
            response=conn.getresponse();opened=json.loads(response.read())
            if response.status!=200 or not opened.get('has_attributes'):
                raise RuntimeError('Frozen startup project import failed')
            conn.request('POST','/api/project/startup',headers=headers)
            response=conn.getresponse();response.read();conn.close()
            if response.status==200:raise RuntimeError('Startup project imported more than once')
            checks['startup_mcy']=dict(status='pass',explicit_file_only=True,consumed_once=True)
        finally:
            startup_server.shutdown();startup_server.server_close();startup_thread.join(timeout=5)
        restored_values=[r['value'] for r in restored['attributes']['cells']]
        original_values=[r['value'] for r in attributes['cells']]
        if ([c.cell_id for c in restored['cell_objects']]!=[c.cell_id for c in triangle.cells]
                or restored_values!=original_values
                or not all(math.isclose(value,expected,rel_tol=1e-12,abs_tol=1e-12)
                           for value,expected in zip(restored_values,[25,15],strict=True))):
            raise RuntimeError('Frozen project round trip changed geometry/attributes')
        for format,name in [('geojson','cells.geojson'),('geotiff','cells.tif'),('asc','cells-asc.zip')]:
            path=destination/name
            export_cells(path,format,restored['cells'],restored['attributes'],crs=triangle.crs_wkt,
                resolution=.5,max_pixels=1000)
            if not path.is_file() or not path.stat().st_size:
                raise RuntimeError('Missing frozen export: '+format)
        with zipfile.ZipFile(destination/'cells-asc.zip') as archive:
            if not {'meshcherry.asc','meshcherry.prj'}.issubset(archive.namelist()):
                raise RuntimeError('Frozen ASC export lacks projection sidecar')
        checks['offline_mcy_and_exports']=dict(status='pass',cell_ids_preserved=True,
            values=[25,15],formats=['geojson','geotiff','asc+prj'])
    if gmsh_path:
        from gis_mesh_workbench.runner import run, write_json
        with tempfile.TemporaryDirectory(prefix='meshcherry-gmsh-smoke-') as directory:
            config_path=Path(directory)/'config.json'
            config=dict(schema_version='gis-workbench-run-config-v1',extraction_engine='indexed_windowed_v1',
                run_name='frozen-gmsh-smoke',analysis_crs='EPSG:32649',raster_path=str(root/'sample_10_30.tif'),
                band=1,semantics='intensive',value_units='synthetic-value',output_root=str(Path(directory)/'runs'),
                cell_source=dict(kind='gmsh_generated',executable=str(gmsh_path.resolve()),
                    extent=[500000,1000000,500002,1000001],sizing='relief',h_min_m=.3,h_max_m=.6,
                    relief_radius_m=.2,max_cells=1000))
            write_json(config_path,config)
            from gis_mesh_workbench.job_control import JobControl, job_scope
            controller=JobControl()
            with job_scope(controller):result=run(config_path)
            if result['status']!='pass': raise RuntimeError(str(result))
            if controller.process is not None or not any('Meshing' in line for line in controller.snapshot()['log_lines']):
                raise RuntimeError('Managed frozen Gmsh lifecycle/log capture failed')
            checks['managed_gmsh_live_log']=dict(status='pass',lines=controller.line_count,process_reaped=True)
            from gis_mesh_workbench.job_control import Cancelled, run_logged
            class CancelOnMeshLog(JobControl):
                def log(self,line):
                    super().log(line)
                    if 'Meshing 2D' in line:self.request_cancel()
            cancelled=CancelOnMeshLog()
            managed_output=Path(result['output'])/'meshing'
            try:
                with job_scope(cancelled):
                    run_logged([str(gmsh_path.resolve()),str(managed_output/'domain.geo'),
                        '-2','-format','msh2','-o',str(Path(directory)/'cancelled.msh'),
                        '-nopopup','-nt','1'],Path(directory)/'gmsh.log',30,cwd=managed_output)
            except Cancelled:pass
            else:raise RuntimeError('Frozen Gmsh log-triggered cancellation was not honored')
            if cancelled.process is not None:raise RuntimeError('Frozen cancelled process was not reaped')
            checks['managed_gmsh_cancel']=dict(status='pass',log_triggered=True,process_reaped=True)
            output=Path(result['output']); attrs=json.loads((output/'attributes.json').read_text())
            if not all(c['value'] is not None and 10-1e-8<=c['value']<=30+1e-8 for c in attrs['cells']):
                raise RuntimeError('Frozen Gmsh extraction failed')
            checks['external_gmsh_relief_extraction']=dict(status='pass',cells=len(attrs['cells']),
                mesh_msh_present=(output/'meshing/mesh.msh').is_file())
            import shapefile
            from rasterio.crs import CRS
            boundary=Path(directory)/'boundary.shp'
            with shapefile.Writer(str(boundary),shapeType=shapefile.POLYGON) as writer:
                writer.field('id','N')
                writer.poly([[(500000,1000000),(500000,1000001),(500002,1000001),(500002,1000000),(500000,1000000)],
                    [(500000.2,1000000.2),(500000.4,1000000.2),(500000.4,1000000.4),(500000.2,1000000.4),(500000.2,1000000.2)]])
                writer.record(1)
            boundary.with_suffix('.prj').write_text(CRS.from_epsg(32649).to_wkt(),encoding='utf-8')
            config['cell_source']['boundary_path']=str(boundary);write_json(config_path,config)
            result=run(config_path)
            if result['status']!='pass':raise RuntimeError(str(result))
            evidence=json.loads((Path(result['output'])/'manifest.json').read_text())['gmsh_generated']
            if evidence['boundary']['holes']!=1 or evidence['domain_coverage']!='pass':raise RuntimeError('Frozen SHP hole failed')
            checks['shp_hole_gmsh']=dict(status='pass',cells=evidence['cell_count'],holes=1,
                symmetric_difference_area_m2=evidence['symmetric_difference_area_m2'])
            config['operation']='mesh_only'
            raster_path=config.pop('raster_path')
            config['schema_version']='gis-workbench-run-config-v2'
            for key in ('band','semantics','value_units'):config.pop(key)
            config['cell_source']['sizing']='uniform'
            config['cell_source']['h_min_m']=config['cell_source']['h_max_m']
            config['cell_source'].pop('extent',None)
            config['cell_source'].pop('relief_radius_m',None)
            write_json(config_path,config)
            mesh_result=run(config_path)
            if mesh_result['status']!='pass':raise RuntimeError(str(mesh_result))
            frozen_path=Path(mesh_result['output'])/'cells.json'
            frozen_bytes=frozen_path.read_bytes()
            frozen_cells=json.loads(frozen_bytes)['cells']
            from gis_mesh_workbench.quality_references import load_references
            frozen_payload=json.loads(frozen_bytes)
            frozen_manifest=json.loads((frozen_path.parent/'manifest.json').read_text())
            original_roi,original_size=load_references(frozen_path.parent,frozen_payload,frozen_manifest)
            assessed=enrich_quality(evaluate_mesh_quality(frozen_payload),frozen_payload,
                roi=original_roi,size_field=original_size)
            if assessed['boundary']['status']!='PASS' or assessed['size']['status']=='NOT_AVAILABLE':
                raise RuntimeError('Frozen retained ROI/size QA failed: '+str(assessed['boundary']))
            if frozen_path.read_bytes()!=frozen_bytes:raise RuntimeError('QA changed accepted mesh')
            checks['retained_mesh_quality']=dict(status='pass',
                boundary=assessed['boundary']['status'],size=assessed['size']['status'],geometry_unchanged=True)
            if (frozen_path.parent/'attributes.json').exists():raise RuntimeError('Mesh-only extracted attributes')
            config['operation']='extract';config['raster_path']=raster_path
            identity=json.loads((frozen_path.parent/'manifest.json').read_text())['geometry_digest']
            config.update(band=1,semantics='intensive',value_units='synthetic-value')
            config['cell_source']=dict(kind='frozen_cells',path=str(frozen_path),geometry_digest=identity,max_cells=1000)
            write_json(config_path,config)
            extracted=run(config_path)
            if extracted['status']!='pass':raise RuntimeError(str(extracted))
            if json.loads((Path(extracted['output'])/'cells.json').read_text())['cells']!=frozen_cells:
                raise RuntimeError('Extraction changed mesh geometry or IDs')
            if frozen_path.read_bytes()!=frozen_bytes:raise RuntimeError('Saved mesh mutated')
            checks['mesh_first_shp_then_extract']=dict(status='pass',cells=len(frozen_cells),
                mesh_without_raster=True,geometry_and_ids_unchanged=True,geometry_digest=identity)
            from gis_mesh_workbench.comparison import compare_mesh
            compared=compare_mesh(frozen_path,raster_path,analysis_crs='EPSG:32649',
                cell_size_m=.5,band=1,semantics='intensive',value_units='synthetic-value')
            if compared['unstructured']['node_count']!=len(frozen_cells) or frozen_path.read_bytes()!=frozen_bytes:
                raise RuntimeError('Frozen comparison changed mesh or counts')
            checks['square_triangle_comparison']=dict(status='pass',
                square_nodes=compared['structured']['node_count'],triangle_nodes=len(frozen_cells),
                support_area_m2=compared['common_support_area_m2'],metrics=compared['metrics'])
            import numpy as np
            import rasterio
            from affine import Affine
            dem=Path(directory)/'sf1-dem.tif'
            yy,xx=np.mgrid[:60,:60]
            with rasterio.open(dem,'w',driver='GTiff',height=60,width=60,count=1,dtype='float64',
                    crs='EPSG:32649',transform=Affine(30,0,500000,0,-30,1001800)) as dataset:
                dataset.write((xx+50*np.sin(yy/4)*(xx>30)).astype('float64'),1)
            config=dict(schema_version='gis-workbench-run-config-v2',operation='mesh_only',
                extraction_engine='indexed_windowed_v1',run_name='sf1-profile-frozen',
                analysis_crs='EPSG:32649',output_root=str(Path(directory)/'runs'),
                cell_source=dict(kind='gmsh_generated',executable=str(gmsh_path.resolve()),
                    extent=[500000,1000000,501800,1001800],sizing='sf1_relief',
                    h_min_m=250,h_max_m=1000,dem_raster_path=str(dem),dem_band=1,
                    dem_value_units='m',sf1_scale=1,max_cells=1000))
            write_json(config_path,config);sf1_result=run(config_path)
            if sf1_result['status']!='pass':raise RuntimeError(str(sf1_result))
            field=json.loads((Path(sf1_result['output'])/'meshing/size-field.json').read_text())
            if (field['method']!='gnn_sf1_two_window_relief' or not field['native_gradation']['pass']
                    or not field['control_gradation']['pass']):
                raise RuntimeError('Frozen SF1 profile field QA failed')
            checks['gnn_sf1_profile']=dict(status='pass',
                native_gradation=field['native_gradation'],control_gradation=field['control_gradation'])
    if boundary_path:
        if not gmsh_path:raise ValueError('boundary smoke requires external Gmsh')
        from gis_mesh_workbench.runner import run, write_json
        from gis_mesh_workbench.boundary import read_boundary, bundle_paths
        from gis_mesh_workbench.extraction import _digest
        original={str(p):_digest(p) for p in bundle_paths(boundary_path)}
        with tempfile.TemporaryDirectory(prefix='meshcherry-dense-smoke-') as directory:
            path=Path(directory)/'config.json';root=Path(directory)/'runs';root.mkdir()
            config=dict(schema_version='gis-workbench-run-config-v2',operation='mesh_only',
                extraction_engine='indexed_windowed_v1',run_name='original-boundary-smoke',
                analysis_crs='EPSG:32649',output_root=str(root),cell_source=dict(kind='gmsh_generated',
                executable=str(gmsh_path.resolve()),boundary_path=str(boundary_path.resolve()),
                sizing='uniform',h_min_m=400,h_max_m=400))
            write_json(path,config);result=run(path)
            if result['status']!='pass':raise RuntimeError(str(result))
            qa=json.loads((Path(result['output'])/'qa.json').read_text())['mesh_quality']
            _,boundary=read_boundary(boundary_path,'EPSG:32649')
            if boundary['source_vertices']<=2000 or qa['cell_count']<=1000:
                raise RuntimeError('Smoke fixture did not exercise a high-detail boundary')
            if boundary['simplification_applied'] or qa['domain_coverage']!='pass':
                raise RuntimeError('Original boundary not preserved')
            if {str(p):_digest(p) for p in bundle_paths(boundary_path)}!=original:
                raise RuntimeError('Boundary input changed')
            checks['original_dense_boundary']=dict(status='pass',source_points=boundary['source_vertices'],
                transformed_points=boundary['transformed_vertices'],cells=qa['cell_count'],holes=qa['hole_count'],
                simplification_applied=False,unchanged_input_hashes=True,
                symmetric_difference_m2=qa['domain_symmetric_difference_m2'])
    checks['executable'] = sys.executable
    from gis_mesh_workbench.build_info import identity
    checks['application_build'] = identity()
    report.write_text(json.dumps({'status':'pass','checks':checks},indent=2),encoding='utf-8')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--workspace', type=Path)
    parser.add_argument('--self-test', type=Path, metavar='REPORT_JSON')
    parser.add_argument('--gmsh-test', type=Path, metavar='GMSH_EXE')
    parser.add_argument('--boundary-test', type=Path, metavar='SHP')
    parser.add_argument('--open-project', type=Path, metavar='PROJECT_MCY')
    parser.add_argument('--register-mcy', action='store_true')
    parser.add_argument('--unregister-mcy', action='store_true')
    args = parser.parse_args()
    try:
        if sum(bool(value) for value in (args.self_test,args.open_project,args.register_mcy,args.unregister_mcy))>1:
            raise ValueError('Chọn một thao tác mở dự án, đăng ký tệp hoặc kiểm thử.')
        if args.self_test:
            self_test(args.self_test,args.gmsh_test,args.boundary_test)
        elif args.register_mcy or args.unregister_mcy:
            if not getattr(sys,'frozen',False):
                raise ValueError('Chạy thao tác liên kết tệp từ MeshCherry.exe đã đóng gói.')
            from gis_mesh_workbench.file_association import install_per_user, uninstall_per_user
            if args.register_mcy:
                install_per_user(Path(sys.executable))
                message='Đã đăng ký tệp .mcy với MeshCherry. Nhấp đúp tệp dự án để mở. Giữ nguyên vị trí thư mục ứng dụng.'
            else:
                result=uninstall_per_user()
                message=('Không có liên kết tệp do MeshCherry quản lý để gỡ.'
                         if result['status'] in {'not_owned','not_installed'} else
                         'Đã gỡ liên kết tệp .mcy của MeshCherry cho tài khoản hiện tại.')
            import ctypes
            ctypes.windll.user32.MessageBoxW(None,message,'MeshCherry',0x40)
        else:
            from gis_mesh_workbench.desktop import launch
            from gis_mesh_workbench.webui import WORKSPACE
            workspace = args.workspace or WORKSPACE
            workspace.mkdir(parents=True, exist_ok=True)
            launch(workspace, startup_project=args.open_project)
        return 0
    except Exception:
        if args.self_test:
            args.self_test.write_text(json.dumps({'status':'fail','error':traceback.format_exc()}),encoding='utf-8')
        else:
            import ctypes
            log = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'MeshCherry' / 'startup-error.log'
            log.parent.mkdir(parents=True, exist_ok=True)
            log.write_text(traceback.format_exc(), encoding='utf-8')
            detail=(('Không cập nhật được liên kết tệp .mcy.\n\n'+str(sys.exc_info()[1]))
                    if args.register_mcy or args.unregister_mcy else
                    ('Không mở được dự án MeshCherry.\n\n'+str(sys.exc_info()[1]))
                    if args.open_project else
                    'Không mở được MeshCherry. Kiểm tra WebView2 Runtime và quyền ghi thư mục workspace.\n\n'+traceback.format_exc())
            ctypes.windll.user32.MessageBoxW(None,
                detail,
                'MeshCherry · lỗi khởi động', 0x10)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
