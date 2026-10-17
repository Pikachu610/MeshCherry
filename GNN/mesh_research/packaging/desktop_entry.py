"""Portable entry point. Self-test exercises the frozen backend, not a mock."""
import argparse
import json
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
    with tempfile.TemporaryDirectory(prefix='meshcherry-smoke-') as directory:
        server = WorkbenchServer(directory)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            conn = http.client.HTTPConnection(*server.server_address, timeout=10)
            for asset in ('/', '/coach.js', '/app.js', '/wizard.css', '/fonts/BeVietnamPro-Regular.ttf'):
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
    grid = structured_grid(extent=[500000,1000000,500002,1000001],
        cell_width_m=1,cell_height_m=1,analysis_crs='EPSG:32649')
    triangle = import_mesh(root/'two_triangles.json',analysis_crs='EPSG:32649')
    for name, cells, expected in (('grid',grid,[10,30]),('triangles',triangle,[25,15])):
        result = extract_attributes(root/'sample_10_30.tif',cells.cells,
            cell_crs='EPSG:32649',analysis_crs='EPSG:32649',band=1,
            semantics='intensive',value_units='synthetic-value')
        values = [row['value'] for row in result['cells']]
        if values != expected:
            raise RuntimeError(f'{name} result differs: {values}')
        checks[name] = values
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
        import math
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
