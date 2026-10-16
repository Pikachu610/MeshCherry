import hashlib
import http.client
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import numpy as np
import rasterio
from affine import Affine
from gis_mesh_workbench.webui import WorkbenchServer


class WebUITests(unittest.TestCase):
    def test_stalled_log_response_does_not_block_stop(self):
        from gis_mesh_workbench.webui import Handler
        from gis_mesh_workbench.job_control import JobControl
        control=JobControl(lock=self.server.lock);control.log('actual log')
        self.server.controls['slow-reader']=control
        self.server.jobs['slow-reader']=dict(status='running',stage='meshing',result=None)
        writing=threading.Event();release=threading.Event();stopped=threading.Event();outcomes=[]
        original=Handler.respond
        def stalled_response(handler,value,*args,**kwargs):
            if isinstance(value,dict) and 'next_cursor' in value:
                writing.set();release.wait(4)
            return original(handler,value,*args,**kwargs)
        def read_log():
            outcomes.append(self.request('/api/job-log?id=slow-reader'))
        def stop_job():
            outcomes.append(self.request('/api/job/stop',dict(job_id='slow-reader')))
            stopped.set()
        with patch.object(Handler,'respond',stalled_response):
            reader=threading.Thread(target=read_log);reader.start();stopper=None
            try:
                self.assertTrue(writing.wait(2))
                stopper=threading.Thread(target=stop_job);stopper.start()
                self.assertTrue(stopped.wait(1),'A stalled log socket held the shared job lock')
                self.assertTrue(control.cancel.is_set())
            finally:
                release.set();reader.join(5)
                if stopper:stopper.join(5)
        self.assertTrue(all(result[0]==200 for result in outcomes))

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name); self.root=self.base/'workspace'; self.root.mkdir()
        self.raster=self.root/'sample.tif'
        with rasterio.open(self.raster,'w',driver='GTiff',width=2,height=1,count=1,
            dtype='float64',crs='EPSG:32649',transform=Affine(1,0,500000,0,-1,1000001)) as dst:
            dst.write(np.array([[10,30]],float),1)
        self.server=WorkbenchServer(self.root)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True); self.thread.start()
        self.addCleanup(self.stop)
        self.config=dict(schema_version='gis-workbench-run-config-v1',extraction_engine='indexed_windowed_v1',
            run_name='ui-test',analysis_crs='EPSG:32649',raster_path=str(self.raster),band=1,
            semantics='extensive',value_units='test',output_root=str(self.server.output_root),
            cell_source=dict(kind='structured_grid',extent=[500000,1000000,500002,1000001],
                cell_width_m=1,cell_height_m=1,boundary_policy='strict'))

    def stop(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=3)

    def request(self,path,data=None,headers=None,method=None,timeout=5):
        connection=http.client.HTTPConnection(*self.server.server_address,timeout=timeout)
        self.addCleanup(connection.close)
        base={'X-Workbench-Token':self.server.token,'Origin':self.server.origin}
        if data is not None: base['Content-Type']='application/json'
        if headers: base.update(headers)
        connection.request(method or ('POST' if data is not None else 'GET'),path,
                           body=json.dumps(data) if data is not None else None,headers=base)
        response=connection.getresponse(); raw=response.read();
        try: value=json.loads(raw)
        except (ValueError,UnicodeDecodeError): value=raw
        return response.status,value,dict(response.getheaders())

    def validate(self):
        status,data,_=self.request('/api/preflight',self.config)
        self.assertEqual(status,200,data); self.assertEqual(data['report']['status'],'pass',data)
        return data['preflight_id']

    def test_source_preview_api_and_outside_root_denied(self):
        status,report,_=self.request('/api/input-preview',dict(path=str(self.raster),kind='raster',band=1))
        self.assertEqual(status,200,report);self.assertEqual(report['source_shape'],[1,2])
        self.assertTrue(report['image'].startswith('data:image/png;base64,'))
        outside=self.base/'outside.tif';outside.write_bytes(self.raster.read_bytes())
        status,report,_=self.request('/api/input-preview',dict(path=str(outside),kind='raster',band=1))
        self.assertNotEqual(status,200)

    def test_source_preview_bad_band_and_stage_layout(self):
        status,report,_=self.request('/api/input-preview',dict(path=str(self.raster),kind='raster',band=2))
        self.assertNotEqual(status,200)
        html=self.request('/')[1].decode('utf-8')
        for name in ('input-preview-image','result-mesh-tab','result-extract-tab','result-view'):
            self.assertIn('id="'+name+'"',html)
        style=self.request('/wizard.css')[1].decode('utf-8')
        self.assertIn('body[data-stage="2"] .viewer,body[data-stage="3"] .viewer{display:none}',style)

    def test_v2_mesh_only_then_frozen_extract_api(self):
        config=dict(self.config,schema_version='gis-workbench-run-config-v2',operation='mesh_only')
        for key in ('raster_path','band','semantics','value_units'):config.pop(key)
        status,ready,_=self.request('/api/preflight',config)
        self.assertEqual(status,200,ready);self.assertEqual(ready['report']['status'],'pass',ready)
        status,started,_=self.request('/api/run',{'preflight_id':ready['preflight_id']})
        self.assertEqual(status,202,started);mesh=self.wait_job(started['job_id'])
        self.assertEqual(mesh['status'],'pass',mesh)
        manifest=self.request(f"/api/artifact?id={started['job_id']}&name=manifest")[1]
        config.update(operation='extract',raster_path=str(self.raster),band=1,semantics='intensive',value_units='m',
            cell_source=dict(kind='frozen_cells',path=str(Path(mesh['result']['output'])/'cells.json'),geometry_digest=manifest['geometry_digest'],max_cells=1000))
        status,ready,_=self.request('/api/preflight',config)
        self.assertEqual(status,200,ready);self.assertEqual(ready['report']['geometry_digest'],manifest['geometry_digest'])
        status,started,_=self.request('/api/run',{'preflight_id':ready['preflight_id']})
        self.assertEqual(status,202,started);self.assertEqual(self.wait_job(started['job_id'])['status'],'pass')

    def test_resolution_source_bootstrap_and_advisory_permissions(self):
        status,data,_=self.request('/api/source',dict(path=str(self.raster),band=1,analysis_crs='EPSG:32649'))
        self.assertEqual(status,200,data);self.assertEqual(data['resolution']['representative_pixel_size_m'],[1,1])
        policy=self.request('/api/bootstrap')[1]['meshing_parameters']
        self.assertEqual(policy['gamma'],2);self.assertEqual(policy['control_shape'],[33,33])
        config=dict(self.config,schema_version='gis-workbench-run-config-v2',operation='mesh_only',
            resolution_reference=dict(path=str(self.raster),band=1))
        for key in ('raster_path','band','semantics','value_units'):config.pop(key)
        status,data,_=self.request('/api/preflight',config)
        self.assertEqual(status,200,data);self.assertEqual(data['report']['meshing_request']['resolution_reference']['role'],'planned_extraction')
        config['resolution_reference']['path']=str(self.base/'outside.tif')
        self.assertEqual(self.request('/api/preflight',config)[0],403)

    def test_original_dense_boundary_http_reports_counts_and_notice(self):
        import math
        import shapefile
        path=self.root/'dense.shp'
        ring=[(500000+100*math.cos(-2*math.pi*i/2100),1000000+100*math.sin(-2*math.pi*i/2100)) for i in range(2100)]
        ring.append(ring[0])
        with shapefile.Writer(str(path),shapeType=shapefile.POLYGON) as writer:
            writer.field('id','N');writer.poly([ring]);writer.record(1)
        path.with_suffix('.prj').write_text(rasterio.crs.CRS.from_epsg(32649).to_wkt(),encoding='utf-8')
        status,report,_=self.request('/api/boundary',dict(path=str(path),analysis_crs='EPSG:32649'))
        self.assertEqual(status,200,report);self.assertEqual(report['source_vertices'],2101)
        self.assertEqual(report['transformed_vertices'],2101);self.assertFalse(report['simplification_applied'])
        self.assertIn('chạy lâu',report['warnings'][0])
        self.assertEqual(self.request('/api/bootstrap')[1]['mesh_limits']['max_cells'],100000)

    def test_active_job_recovery_metadata_and_auth(self):
        self.assertEqual(self.request('/api/active-job')[1],{'job_id':None})
        self.server.jobs['finished']={'status':'pass','source_path':'private'}
        self.assertEqual(self.request('/api/active-job')[1],{'job_id':None})
        self.server.jobs['active']={'status':'running','config':self.config}
        self.assertEqual(self.request('/api/active-job')[1],{'job_id':'active'})
        self.assertEqual(self.request('/api/active-job',headers={'X-Workbench-Token':'wrong'})[0],403)
        self.assertEqual(self.request('/api/active-job',headers={'Origin':'https://evil.example'})[0],403)

    def test_recent_http_safe_metadata_and_auth(self):
        self.assertEqual(self.request('/api/source',{'path':str(self.raster)})[0],200)
        status,data,_=self.request('/api/recent')
        self.assertEqual(status,200);self.assertEqual(len(data['items']),1)
        item=data['items'][0];self.assertEqual(set(item),{'id','kind','name','opened'})
        self.assertEqual(item['name'],self.raster.name)
        self.assertNotIn(str(self.raster.parent),json.dumps(data))
        self.assertEqual(self.server.selected_files,set())
        self.assertEqual(self.request('/api/recent',headers={'X-Workbench-Token':'bad'})[0],403)
        self.assertEqual(self.request('/api/recent',{'id':item['id']},headers={'Origin':'https://evil.example'})[0],403)
        self.assertEqual(self.request('/api/recent',{'id':item['id'],'path':str(self.raster)})[0],400)
        self.assertEqual(self.request('/api/recent',{'id':item['id']})[0],200)

    def test_native_picker_security_cancel_and_exact_file_grant(self):
        external=self.base/'outside'; external.mkdir()
        selected=external/'chosen.tif'; selected.write_bytes(self.raster.read_bytes())
        sibling=external/'other.tif'; sibling.write_bytes(self.raster.read_bytes())
        self.server.native_picker=lambda kind:[str(selected)]
        self.assertTrue(self.request('/api/bootstrap')[1]['native_picker'])
        self.assertEqual(self.request('/api/picker',{'kind':'raster'},headers={'X-Workbench-Token':'bad'})[0],403)
        self.assertEqual(self.request('/api/picker',{'kind':'raster'},headers={'Origin':'https://wrong.example'})[0],403)
        self.assertEqual(self.request('/api/picker',{'kind':'raster','path':str(sibling)})[0],400)
        ident=self.validate()
        status,data,_=self.request('/api/picker',{'kind':'raster'})
        self.assertEqual(status,200,data); self.assertEqual(data['path'],str(selected.resolve()))
        self.assertIsNone(self.server.current_preflight)
        self.assertEqual(self.request('/api/source',{'path':str(selected)})[0],200)
        self.assertEqual(self.request('/api/source',{'path':str(sibling)})[0],403)
        self.assertEqual(self.request('/api/fs/list',{'path':str(external)})[0],403)
        self.assertEqual(self.request('/api/run',{'preflight_id':ident})[0],400)
        ident=self.validate(); self.server.native_picker=lambda kind:None
        self.assertEqual(self.request('/api/picker',{'kind':'raster'})[1],{'cancelled':True})
        self.assertEqual(self.server.current_preflight,ident)

    def test_native_output_sample_protection_and_busy_guards(self):
        destination=self.base/'results'; destination.mkdir()
        self.server.native_picker=lambda kind:[str(destination)]
        self.assertEqual(self.request('/api/picker',{'kind':'output'})[0],200)
        self.assertEqual(self.server.permitted(destination,write=True),destination.resolve())
        self.server.native_picker=lambda kind:[str(self.server.sample_root)]
        self.assertEqual(self.request('/api/picker',{'kind':'output'})[0],403)
        self.server.native_picker=lambda kind:[str(destination)]
        self.server.jobs['busy']={'status':'running'}
        self.assertEqual(self.request('/api/picker',{'kind':'output'})[0],400)
        self.server.jobs.clear(); self.server.picker_active=True
        self.assertEqual(self.request('/api/picker',{'kind':'output'})[0],400)
        self.assertEqual(self.request('/api/preflight',self.config)[0],400)
        self.server.picker_active=False
        self.server.native_picker=None
        self.assertEqual(self.request('/api/picker',{'kind':'output'})[0],400)

    def test_native_picker_exception_releases_guard(self):
        def failed(kind): raise RuntimeError('dialog failed')
        self.server.native_picker=failed
        self.assertEqual(self.request('/api/picker',{'kind':'raster'})[0],400)
        self.assertFalse(self.server.picker_active)

    def test_native_mesh_and_gmsh_narrow_grants(self):
        outside=self.base/'inputs'; outside.mkdir()
        mesh=outside/'mesh.json'; mesh.write_text('{}')
        sibling=outside/'other.json'; sibling.write_text('{}')
        self.server.native_picker=lambda kind:[str(mesh)]
        self.assertEqual(self.request('/api/picker',{'kind':'mesh'})[0],200)
        self.assertEqual(self.server.permitted(mesh),mesh.resolve())
        from gis_mesh_workbench.webui import AccessError
        with self.assertRaises(AccessError): self.server.permitted(sibling)
        for name in ('other.exe','gmsh.txt'):
            wrong=outside/name; wrong.write_bytes(b'not executable')
            self.server.native_picker=lambda kind:[str(wrong)]
            self.assertEqual(self.request('/api/picker',{'kind':'gmsh'})[0],400)
        exe=outside/'gmsh.exe'; exe.write_bytes(b'not runnable')
        self.server.native_picker=lambda kind:[str(exe)]
        self.assertEqual(self.request('/api/picker',{'kind':'gmsh'})[0],200)
        self.assertFalse((self.root/'.scratch/gis-mesh-workbench/gmsh.json').exists())
        with self.assertRaises(AccessError): self.server.permitted(exe)

    def test_native_output_root_and_sample_ancestors_denied(self):
        for path in (Path(self.root.anchor),self.server.sample_root,self.server.sample_root.parent):
            self.server.native_picker=lambda kind:[str(path)]
            self.assertNotEqual(self.request('/api/picker',{'kind':'output'})[0],200)

    def test_saved_gmsh_bootstrap_is_metadata_not_readiness(self):
        from gis_mesh_workbench.runner import write_json
        saved=dict(executable='D:/tools/gmsh.exe',version='4.15.2',executable_sha256='a'*64)
        write_json(self.root/'.scratch/gis-mesh-workbench/gmsh.json',saved)
        status,data,_=self.request('/api/bootstrap')
        self.assertEqual(status,200); self.assertEqual(data['gmsh_saved'],saved)
        self.assertNotIn('ready',data['gmsh_saved'])
        from gis_mesh_workbench.webui import ASSETS
        js=(ASSETS/'app.js').read_text(encoding='utf-8')
        self.assertIn('chưa kiểm tra lại trong phiên này',js)
        self.assertIn('gmshVerified=null',js)

    def test_local_font_routes_are_allowlisted(self):
        from gis_mesh_workbench.webui import ASSETS
        for weight in ('Regular', 'Medium', 'SemiBold', 'Bold'):
            name = f'BeVietnamPro-{weight}.ttf'
            status, body, headers = self.request('/fonts/'+name)
            self.assertEqual(status, 200)
            self.assertEqual(headers['Content-Type'], 'font/ttf')
            self.assertEqual(body, (ASSETS/'fonts'/name).read_bytes())
            self.assertEqual(body[:4], b'\x00\x01\x00\x00')
        self.assertEqual(self.request('/fonts/unknown.ttf')[0], 404)
        self.assertEqual(self.request('/fonts/../app.js')[0], 404)
        self.assertIn('SIL OPEN FONT LICENSE', (ASSETS/'fonts'/'OFL.txt').read_text())

    def test_coach_asset_and_focus_contract(self):
        from gis_mesh_workbench.webui import ASSETS
        status, body, headers = self.request('/coach.js')
        self.assertEqual(status, 200)
        self.assertIn('text/javascript', headers['Content-Type'])
        html = (ASSETS/'index.html').read_text(encoding='utf-8')
        self.assertLess(html.index('/coach.js'), html.index('/app.js'))
        css = (ASSETS/'wizard.css').read_text(encoding='utf-8')
        self.assertIn('.cell-node:focus,.cell-poly:focus{outline:none}', css)
        self.assertIn('.cell-node{vector-effect:non-scaling-stroke}', css)
        script = (ASSETS/'coach.js').read_text(encoding='utf-8')
        self.assertIn("addEventListener('mouseenter'", script)
        self.assertIn("addEventListener('focus'", script)
        self.assertIn("e.key==='Escape'", script)
        self.assertNotIn('fetch(', script)

    def test_concise_copy_and_boundary_in_input_stage(self):
        from gis_mesh_workbench.webui import ASSETS
        html=(ASSETS/'index.html').read_text(encoding='utf-8')
        self.assertNotIn('Không tải dữ liệu lên mạng',html)
        self.assertNotIn('Dữ liệu không được tải lên mạng',html)
        self.assertNotIn('DỮ LIỆU CỤC BỘ',html)
        for ident in ('gmsh-domain','boundary-path','boundary-options'):
            self.assertEqual(html.count('id="'+ident+'"'),1)
            self.assertLess(html.index('id="'+ident+'"'),html.index('data-panel="2"'))
        self.assertIn('.shp + .shx + .dbf + .prj',html)

    def test_result_workspace_accessibility_and_responsive_contract(self):
        from gis_mesh_workbench.webui import ASSETS
        html=(ASSETS/'index.html').read_text(encoding='utf-8')
        js=(ASSETS/'app.js').read_text(encoding='utf-8')
        css=(ASSETS/'wizard.css').read_text(encoding='utf-8')
        self.assertIn('<svg id="map" role="group"',html)
        self.assertIn('aria-live="polite"',html)
        for ident in ('cell-search','cell-find','result-metrics','metric-cells','metric-valid','metric-missing'):
            self.assertEqual(html.count('id="'+ident+'"'),1)
        self.assertIn('record.valid_coverage<record.footprint_coverage',js)
        self.assertIn('start-another\').onclick=startAnother',js)
        self.assertIn('gmshRequest++',js)
        self.assertIn('ArrowRight',js)
        self.assertIn("'aria-hidden':'true'",js)
        self.assertIn('max-height:calc(100vh - 24px);overflow:auto',css)
        self.assertIn('.welcome-actions{position:static',css)
        self.assertIn('prefers-reduced-motion:reduce',css)

    def test_display_footprint_analysis_crs(self):
        status, data, _ = self.request('/api/source', {'path': str(self.raster), 'analysis_crs': 'EPSG:32649'})
        self.assertEqual(status, 200, data)
        self.assertEqual(data['display_footprint']['bounds'], [500000,1000000,500002,1000001])
        self.assertFalse(data['display_footprint']['approximate'])
        self.assertEqual(len(data['display_footprint']['xy']), 4)
        status, data, _ = self.request('/api/source', {'path': str(self.raster), 'analysis_crs': 'EPSG:32648'})
        self.assertEqual(status, 200, data)
        self.assertTrue(data['display_footprint']['approximate'])
        self.assertEqual(len(data['display_footprint']['xy']), 32)
        self.assertNotEqual(data['display_footprint']['bounds'][0], 500000)

    def test_coverage_ui_contract(self):
        from gis_mesh_workbench.webui import ASSETS
        html=(ASSETS/'index.html').read_text(encoding='utf-8')
        js=(ASSETS/'app.js').read_text(encoding='utf-8')
        for ident in ('coverage-summary','raster-extent'):
            self.assertEqual(html.count('id="'+ident+'"'),1)
        self.assertIn('Ngoài raster',html)
        self.assertIn('Ô nằm ngoài phạm vi raster',js)
        self.assertIn('footprint_coverage===0',js)
        self.assertIn('metadata.display_footprint',js)
        self.assertIn("$('coverage-pending').hidden=!!records.length",js)
        for ident in ('gmsh-setup-open','gmsh-setup','gmsh-setup-path','gmsh-setup-save'):
            self.assertEqual(html.count('id="'+ident+'"'),1)
        self.assertIn('Thiết lập Gmsh trước',js)
        self.assertIn('async function checkGmsh',js)

    def test_gmsh_preferences_checked_and_persisted(self):
        path=str(self.root/'gmsh.exe')
        report=dict(executable=path,version='4.15.2',executable_sha256='checked-fixture')
        with patch('gis_mesh_workbench.webui.inspect_executable',return_value=report) as inspect:
            status, data, _=self.request('/api/gmsh',{'path':path})
            self.assertEqual(status,200,data)
            inspect.assert_called_once_with(path)
        status, data, _=self.request('/api/bootstrap')
        self.assertEqual(data['gmsh_path'],path)
        self.assertEqual(self.request('/api/gmsh',{'path':'gmsh.exe'})[0],400)
        for headers in ({'X-Workbench-Token':'wrong'},{'Origin':'https://example.invalid'}):
            self.assertEqual(self.request('/api/gmsh',{'path':path},headers=headers)[0],403)
        with patch('gis_mesh_workbench.webui.inspect_executable',side_effect=ValueError('invalid executable')):
            self.assertEqual(self.request('/api/gmsh',{'path':str(self.base/'gmsh.exe')})[0],400)
        self.assertEqual(self.request('/api/bootstrap')[1]['gmsh_path'],path)
        self.assertEqual(self.request('/api/fs/list',{'path':str(self.base)})[0],403)
        restored=WorkbenchServer(self.root)
        try:
            preferences=json.loads((restored.workspace/'.scratch/gis-mesh-workbench/gmsh.json').read_text())
            self.assertEqual(preferences['executable'],path)
        finally: restored.server_close()

    def test_gmsh_preferences_survive_a_different_release_workspace(self):
        preferences=self.base/'user-profile'/'MeshCherry'/'gmsh.json'
        self.server.gmsh_preferences=preferences
        path=str(self.base/'tools'/'gmsh.exe')
        report=dict(executable=path,version='4.15.2',executable_sha256='a'*64)
        with patch('gis_mesh_workbench.webui.inspect_executable',return_value=report):
            status,data,_=self.request('/api/gmsh',{'path':path})
        self.assertEqual(status,200,data)
        self.assertTrue(preferences.is_file())
        new_workspace=self.base/'next-release'; new_workspace.mkdir()
        restored=WorkbenchServer(new_workspace,gmsh_preferences=preferences)
        try:
            self.assertEqual(restored.saved_gmsh(),report)
            self.assertFalse((new_workspace/'.scratch/gis-mesh-workbench/gmsh.json').exists())
        finally: restored.server_close()
        with patch('gis_mesh_workbench.webui.inspect_executable',side_effect=ValueError('missing exe')):
            self.assertEqual(self.request('/api/gmsh',{'path':str(self.base/'missing.exe')})[0],400)
        self.assertEqual(self.request('/api/bootstrap')[1]['gmsh_saved'],report)
        self.assertEqual(json.loads(preferences.read_text()),report)

    def test_user_400_cell_outside_raster_case(self):
        self.config['semantics']='intensive'
        self.config['cell_source'].update(extent=[499998,1000000,500002,1000001],
                                         cell_width_m=.1,cell_height_m=.1)
        status, ready, _=self.request('/api/preflight',self.config,timeout=30)
        self.assertEqual(status,200,ready)
        self.assertEqual(ready['report']['status'],'pass')
        ident=ready['preflight_id']
        status, data, _=self.request('/api/run',{'preflight_id':ident})
        self.assertEqual(status,202,data)
        job_id=data['job_id']
        self.assertEqual(self.wait_job(job_id,timeout=60)['status'],'pass')
        status, data, _=self.request(f'/api/artifact?id={job_id}&name=attributes')
        self.assertEqual(status,200,data)
        records={r['cell_id']:r for r in data['cells']}
        self.assertEqual(len(records),400)
        self.assertEqual(sum(r['footprint_coverage']==0 for r in records.values()),200)
        self.assertIsNone(records['grid-r002-c019']['value'])
        self.assertEqual(records['grid-r002-c020']['value'],10)
        self.assertAlmostEqual(records['grid-r002-c030']['value'],30)

    def test_saved_gmsh_is_rechecked_before_preflight_authority(self):
        path=self.root/'gmsh.exe'; path.write_bytes(b'not-executed-fixture')
        report=dict(executable=str(path),version='4.15.2',executable_sha256='old')
        with patch('gis_mesh_workbench.webui.inspect_executable',return_value=report):
            self.assertEqual(self.request('/api/gmsh',{'path':str(path)})[0],200)
        self.config['semantics']='intensive'
        self.config['cell_source']=dict(kind='gmsh_generated',executable=str(path),
            extent=[500000,1000000,500002,1000001],sizing='uniform',h_min_m=.6,h_max_m=.6,
            relief_radius_m=.2,max_cells=1000)
        with patch('gis_mesh_workbench.gmsh_meshing.inspect_executable',side_effect=ValueError('replaced executable')) as inspect, \
             patch('gis_mesh_workbench.runner.generate_gmsh') as generate:
            status,data,_=self.request('/api/preflight',self.config)
            self.assertEqual(status,200); self.assertEqual(data['report']['status'],'fail')
            self.assertIsNone(data['preflight_id']); inspect.assert_called_once()
            self.assertEqual(self.request('/api/run',{'preflight_id':'no-authority'})[0],400)
            generate.assert_not_called()

    def test_actual_gmsh_exports_match_retained_bytes(self):
        exe=Path(__file__).resolve().parents[3]/'Test_chialuoi/gmsh-4.15.2-Windows64/gmsh.exe'
        if not exe.is_file(): self.skipTest('External Gmsh is not installed')
        self.config['semantics']='intensive'
        self.config['cell_source']=dict(kind='gmsh_generated',executable=str(exe),
            extent=[500000,1000000,500002,1000001],sizing='uniform',h_min_m=.6,h_max_m=.6,
            relief_radius_m=.2,max_cells=1000)
        ident=self.validate(); status,data,_=self.request('/api/run',{'preflight_id':ident})
        self.assertEqual(status,202); ident=data['job_id']; job=self.wait_job(ident)
        self.assertEqual(job['status'],'pass',job)
        from gis_mesh_workbench.webui import ARTIFACTS
        output=Path(job['result']['output'])
        for name in ('csv','attributes','msh','mesh'):
            conn=http.client.HTTPConnection(*self.server.server_address,timeout=10)
            try:
                conn.request('GET',f'/api/artifact?id={ident}&name={name}&download=1',
                    headers={'X-Workbench-Token':self.server.token,'Origin':self.server.origin})
                response=conn.getresponse(); content=response.read()
                self.assertEqual(response.status,200)
                self.assertEqual(content,(output/ARTIFACTS[name]).read_bytes())
                self.assertIn(Path(ARTIFACTS[name]).name,response.getheader('Content-Disposition'))
            finally: conn.close()

    def test_truthful_cute_ui_contract(self):
        from gis_mesh_workbench.webui import ASSETS
        html = (ASSETS/'index.html').read_text(encoding='utf-8')
        script = (ASSETS/'app.js').read_text(encoding='utf-8')
        coach = (ASSETS/'coach.js').read_text(encoding='utf-8')
        css = (ASSETS/'wizard.css').read_text(encoding='utf-8')
        self.assertIn("'validate':'Kiểm tra cấu hình trước khi tạo lưới.'", coach)
        for content in (html, script, coach):
            for contrast in ('không phải', 'chưa phải', 'không có nghĩa là'):
                self.assertNotIn(contrast, content)
        for label in ('Chọn GeoTIFF', 'Hai pixel nhân tạo', 'Tạo mesh bằng Gmsh'):
            self.assertIn(label, html)
        for unsupported in ('Kéo thả', 'khoảng 2 phút', 'Map vệ tinh', 'Phủ hoàn hảo'):
            self.assertNotIn(unsupported, html)
        for ident in ('welcome-start', 'welcome-skip', 'mode', 'raster', 'validate', 'run', 'csv', 'json', 'map'):
            self.assertEqual(html.count('id="'+ident+'"'), 1)
        self.assertEqual(html.count('data-mode='), 3)
        self.assertIn('Nhập CRS tính toán không sửa được lỗi này', script)
        self.assertIn("if(!source.crs)throw new Error", script)
        self.assertIn('null: chưa có dữ liệu hợp lệ', script)
        self.assertIn('prefers-reduced-motion', css)
        self.assertIn('direction:ltr', css)
        self.assertNotIn('transition:all', css)
        self.assertNotIn('Bé', html)

    def test_samples_readable_but_never_output_destinations(self):
        sample = self.server.sample_root
        self.assertEqual(self.request('/api/fs/list', {'path': str(sample)})[0], 200)
        self.assertEqual(self.request('/api/source', {'path': str(sample/'sample_10_30.tif')})[0], 200)
        self.assertEqual(self.server.permitted(sample/'two_triangles.json'), sample/'two_triangles.json')
        for target in (sample, sample/'new-output'):
            config = dict(self.config, output_root=str(target))
            self.assertEqual(self.request('/api/preflight', config)[0], 403)
        self.assertEqual(self.server.normalize_form(self.config)['output_root'], str(self.server.output_root))
        status, data, _ = self.request('/api/bootstrap')
        self.assertEqual(status, 200)
        self.assertNotIn(str(sample), data['output_roots'])

    def test_explicit_output_root_still_allowed_and_nested_samples_protected(self):
        extra = self.base/'extra'; extra.mkdir()
        server = WorkbenchServer(self.root, allow_roots=[extra])
        try:
            self.assertEqual(server.permitted(extra, write=True), extra)
            # Simulate packaged samples nested within a writable workspace.
            nested = self.root/'packaged-samples'; nested.mkdir()
            server.sample_root = nested
            for target in (nested, nested/'output'):
                with self.assertRaisesRegex(ValueError, 'chỉ được đọc'):
                    server.permitted(target, exists=False, write=True)
        finally:
            server.server_close()

    def wait_job(self,ident,timeout=10):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            _,job,_=self.request('/api/job?id='+ident)
            if job['status'] not in {'running','stopping'}: return job
            time.sleep(.02)
        self.fail('job timed out')

    def test_loopback_headers_token_host_origin_and_methods(self):
        self.assertEqual(self.server.server_address[0],'127.0.0.1')
        for headers in ({'X-Workbench-Token':''},{'X-Workbench-Token':'wrong'},{'Host':'evil.example'}, {'Origin':'https://evil.example'}):
            status,_,_=self.request('/api/bootstrap',headers=headers); self.assertEqual(status,403)
        status,_,headers=self.request('/api/bootstrap'); self.assertEqual(status,200)
        self.assertNotIn('Access-Control-Allow-Origin',headers)
        self.assertIn("frame-ancestors 'none'",headers['Content-Security-Policy'])
        self.assertEqual(headers['X-Content-Type-Options'],'nosniff')
        self.assertEqual(self.request('/api/source',{'path':str(self.raster)},headers={'Origin':''})[0],403)
        self.assertEqual(self.request('/api/source',{'path':str(self.raster)},headers={'Content-Type':'text/plain'})[0],415)
        self.assertEqual(self.request('/api/run',method='OPTIONS')[0],405)
        self.assertEqual(self.request('/api/run',{'x':'y'*70000})[0],413)
        self.assertEqual(self.server.jobs,{})
        # An absurd declared length must be rejected without reading that body.
        self.assertEqual(self.request('/api/run',method='POST',headers={
            'Content-Type':'application/json','Content-Length':'1000000000'})[0],413)

    def test_metadata_path_escape_and_readonly_source(self):
        before=hashlib.sha256(self.raster.read_bytes()).hexdigest()
        status,data,_=self.request('/api/fs/list',{'path':str(self.root)})
        self.assertEqual(status,200); self.assertTrue(data['entries'])
        self.assertEqual(set(data['entries'][0]),{'name','path','kind','size','extension'})
        for path in (self.base,self.root/'../outside'):
            self.assertEqual(self.request('/api/fs/list',{'path':str(path)})[0],403)
        self.assertEqual(self.request('/api/fs/list',{'path':str(self.base)})[0],403)
        status,meta,_=self.request('/api/source',{'path':str(self.raster)})
        self.assertEqual(status,200); self.assertEqual(meta['crs'],'EPSG:32649')
        self.assertTrue(meta['projected_metre']); self.assertNotIn('data',meta)
        self.assertEqual(hashlib.sha256(self.raster.read_bytes()).hexdigest(),before)

    def test_packaged_brand_icon_routes(self):
        from PIL import Image
        from gis_mesh_workbench.webui import ASSETS
        for route, name, mime, signature in (
            ('/icon.png', 'meshcherry-icon-v1.png', 'image/png', b'\x89PNG\r\n\x1a\n'),
            ('/favicon.ico', 'meshcherry-icon-v1.ico', 'image/vnd.microsoft.icon', b'\x00\x00\x01\x00'),
        ):
            status, body, headers = self.request(route)
            self.assertEqual(status, 200)
            self.assertEqual(headers['Content-Type'], mime)
            self.assertTrue(body.startswith(signature))
            self.assertEqual(body, (ASSETS / name).read_bytes())
        with Image.open(ASSETS / 'meshcherry-icon-v1.png') as image:
            self.assertEqual(image.size, (512, 512))
            self.assertEqual(image.mode, 'RGBA')
            self.assertEqual(image.getchannel('A').getextrema(), (0, 255))
        with Image.open(ASSETS / 'meshcherry-icon-v1.ico') as image:
            self.assertEqual(image.ico.sizes(), {(n, n) for n in (16,24,32,48,64,128,256)})
        status, html, _ = self.request('/')
        self.assertEqual(status, 200)
        self.assertIn(b'href="/favicon.ico"', html)
        self.assertIn(b'src="/icon.png"', html)
        self.assertEqual(self.request('/branding/meshcherry-icon-master-v1.png')[0], 404)

    def test_tutorial_state_is_version_only_and_authenticated(self):
        self.server.tutorial_preferences=self.root/'tutorial-preferences.json'
        status,boot,_=self.request('/api/bootstrap')
        self.assertEqual(status,200); self.assertFalse(boot['tutorial_seen'])
        self.assertTrue(Path(boot['sample_raster']).is_file())
        self.assertEqual(self.request('/api/tutorial',{'action':'acknowledge'},headers={'X-Workbench-Token':'wrong'})[0],403)
        self.assertEqual(self.request('/api/tutorial',{'action':'acknowledge','path':'outside'})[0],400)
        self.assertEqual(self.request('/api/tutorial',{'action':'acknowledge'})[0],200)
        self.assertTrue(self.request('/api/bootstrap')[1]['tutorial_seen'])
        self.assertEqual(json.loads(self.server.tutorial_preferences.read_text()),{'tutorial_version_seen':1})
        self.assertEqual(self.request('/wizard.css')[0],200)

    def test_symlink_escape_where_supported(self):
        link=self.root/'escape'
        try: link.symlink_to(self.base,target_is_directory=True)
        except OSError as error:
            # Only unavailable platform capabilities may skip. Path/I/O bugs fail.
            if os.name == 'nt' and getattr(error, 'winerror', None) == 1314:
                self.skipTest(f'symlink creation unavailable: Windows privilege 1314: {error}')
            raise
        self.assertEqual(self.request('/api/fs/list',{'path':str(link)})[0],403)

    @unittest.skipUnless(os.name == 'nt', 'Windows junction regression')
    def test_windows_junction_rejects_read_write_and_artifact_escape(self):
        import subprocess
        from gis_mesh_workbench.webui import AccessError
        outside=self.base/'outside';outside.mkdir()
        (outside/'qa.json').write_text('{"outside":true}')
        link=self.root/'junction'
        # Paths are absolute and resolved within this test-owned temporary tree.
        self.assertTrue(link.parent.resolve().is_relative_to(self.base.resolve()))
        created=subprocess.run(['powershell','-NoProfile','-NonInteractive','-Command',
            'New-Item -ItemType Junction -Path $env:MESH_TEST_JUNCTION -Target $env:MESH_TEST_TARGET | Out-Null'],
            env={**os.environ,'MESH_TEST_JUNCTION':str(link),'MESH_TEST_TARGET':str(outside)},
            capture_output=True,text=True)
        self.assertEqual(created.returncode,0,created.stderr)
        try:
            with self.assertRaises(AccessError):self.server.permitted(link/'qa.json')
            with self.assertRaises(AccessError):self.server.permitted(link/'new.json',exists=False,write=True)
            self.assertEqual(self.request('/api/fs/list',{'path':str(link)})[0],403)
            self.server.jobs['junction-test']=dict(status='pass',result=dict(output=str(link)))
            with self.assertRaises(AccessError):self.server.artifact('junction-test','qa')
            self.assertEqual((outside/'qa.json').read_text(),'{"outside":true}')
        finally:
            # Remove the junction itself, never recurse into its target.
            link.rmdir()

    def test_native_raster_selection_grants_exact_tiff_companions(self):
        outside=self.base/'chosen';outside.mkdir()
        raster=outside/'choice.tif';raster.write_bytes(self.raster.read_bytes())
        aux=Path(str(raster)+'.aux.xml');aux.write_text('<PAMDataset/>')
        secret=outside/'unrelated.txt';secret.write_text('not selected')
        self.server.native_picker=lambda kind:[str(raster)]
        self.server.choose_native({'kind':'raster'})
        self.assertEqual(self.server.permitted(aux),aux)
        from gis_mesh_workbench.webui import AccessError
        with self.assertRaises(AccessError):self.server.permitted(secret)
        late=Path(str(raster)+'.msk');late.write_bytes(b'late companion')
        cfg=dict(self.config,raster_path=str(raster))
        with self.assertRaises(AccessError):self.server.normalize_form(cfg)

    def test_large_preflight_and_view_are_bounded_but_download_is_complete(self):
        from types import SimpleNamespace
        rows=[dict(cell_id=str(i),kind='triangle',xy=[[0,0],[1,0],[0,1]]) for i in range(10000)]
        cells=SimpleNamespace(cells=[SimpleNamespace(**row) for row in rows])
        with patch('gis_mesh_workbench.webui.preflight',return_value=({'status':'pass','cell_count':len(rows)},cells)):
            status,data,_=self.request('/api/preflight',self.config)
        self.assertEqual(status,200);self.assertEqual(data['cells'],[])
        self.assertEqual(data['display']['cell_count'],10000)
        output=self.server.output_root/'large';output.mkdir()
        (output/'manifest.json').write_text(json.dumps({'status':'pass'}))
        (output/'cells.json').write_text(json.dumps({'cells':rows,'crs_wkt':'EPSG:32649'}))
        self.server.jobs['large']=dict(status='pass',result=dict(output=str(output)))
        status,data,_=self.request('/api/artifact?id=large&name=cells&view=1')
        self.assertEqual(status,200);self.assertEqual(len(data['cells']),10000)
        self.assertEqual(data['display']['mode'],'batched')
        status,data,headers=self.request('/api/artifact?id=large&name=cells&download=1')
        self.assertEqual(status,200);self.assertEqual(len(data['cells']),10000)
        self.assertIn('attachment',headers['Content-Disposition'])

    def test_companion_added_after_preflight_is_not_implicitly_authorized(self):
        outside=self.base/'external';outside.mkdir()
        raster=outside/'selected.tif';raster.write_bytes(self.raster.read_bytes())
        self.server.native_picker=lambda kind:[str(raster)]
        self.server.choose_native({'kind':'raster'})
        self.config['raster_path']=str(raster)
        ident=self.validate()
        aux=Path(str(raster)+'.aux.xml');aux.write_text('<PAMDataset/>')
        self.assertNotIn(aux,self.server.selected_files)
        status,data,_=self.request('/api/run',{'preflight_id':ident})
        self.assertEqual(status,202,data)
        job=self.wait_job(data['job_id'])
        self.assertEqual(job['status'],'fail',job)
        self.assertIn('cho phép',job['result']['error'])

    def test_preflight_stale_invalid_crs_and_run_authority(self):
        old=self.validate(); current=self.validate()
        self.assertNotEqual(old,current)
        self.assertEqual(self.request('/api/run',{'preflight_id':old})[0],400)
        status,data,_=self.request('/api/run',{'preflight_id':current})
        self.assertEqual(status,202); ident=data['job_id']; job=self.wait_job(ident)
        self.assertEqual(job['status'],'pass',job)
        status,manifest,_=self.request(f'/api/artifact?id={ident}&name=manifest')
        self.assertEqual(status,200); self.assertEqual(manifest['effective_contract']['semantics'],'extensive')
        self.assertNotIn('small reference kernel',manifest['limitations'])
        status,attrs,_=self.request(f'/api/artifact?id={ident}&name=attributes')
        self.assertEqual(status,200); self.assertAlmostEqual(attrs['qa']['cell_total'],40)
        self.assertEqual(self.request(f'/api/artifact?id={ident}&name=../../sample.tif')[0],403)
        status,csv,headers=self.request(f'/api/artifact?id={ident}&name=csv&download=1')
        self.assertEqual(status,200); self.assertIn('attributes.csv',headers['Content-Disposition'])
        self.config['analysis_crs']='EPSG:4326'
        status,bad,_=self.request('/api/preflight',self.config)
        self.assertEqual(status,200); self.assertIsNone(bad['preflight_id'])
        self.assertEqual(bad['report']['status'],'fail')

    def test_cancelled_extract_truthful_diagnostics_and_artifact_gate(self):
        from gis_mesh_workbench.job_control import checkpoint
        ready=threading.Event();release=threading.Event()
        def held(*args,**kwargs):
            ready.set();release.wait(timeout=4);checkpoint()
            self.fail('Extraction continued after accepted stop')
        with patch('gis_mesh_workbench.runner.extract_indexed',side_effect=held):
            ident=self.validate();status,data,_=self.request('/api/run',{'preflight_id':ident})
            self.assertEqual(status,202);job_id=data['job_id'];self.assertTrue(ready.wait(timeout=2))
            try:
                self.assertEqual(self.request('/api/job/stop',{'job_id':job_id},headers={'X-Workbench-Token':'wrong'})[0],403)
                self.assertEqual(self.request('/api/job/stop',{'job_id':'not-session-job'})[0],400)
                for _ in range(2):
                    status,response,_=self.request('/api/job/stop',{'job_id':job_id})
                    self.assertEqual(status,200);self.assertTrue(response['accepted'])
                self.assertEqual(self.request('/api/job?id='+job_id)[1]['status'],'stopping')
                self.assertEqual(self.request('/api/active-job')[1]['job_id'],job_id)
            finally:release.set()
            job=self.wait_job(job_id);self.assertEqual(job['status'],'cancelled',job)
            output=Path(job['result']['output'])
            self.assertEqual(json.loads((output/'manifest.json').read_text())['status'],'cancelled')
            self.assertEqual(json.loads((output/'qa.json').read_text())['status'],'cancelled')
            for name in ('cells','attributes','csv','msh','mesh'):
                self.assertEqual(self.request(f'/api/artifact?id={job_id}&name={name}')[0],403)

    def test_log_cursor_route_authorization_and_terminal_cancel(self):
        from gis_mesh_workbench.job_control import JobControl
        control=JobControl(lock=self.server.lock);self.server.controls['log-test']=control
        self.server.jobs['log-test']=dict(status='running',stage='meshing',result=None)
        control.log('Info : Meshing 2D...')
        route='/api/job-log?id=log-test&cursor=0'
        self.assertEqual(self.request(route,headers={'Origin':'https://evil.example'})[0],403)
        status,data,headers=self.request(route)
        self.assertEqual(status,200);self.assertEqual(data['text'],'Info : Meshing 2D...\n')
        self.assertLess(int(headers['Content-Length']),32768)
        self.assertEqual(self.request('/api/job-log?id=log-test&cursor='+str(data['next_cursor']))[1]['text'],'')
        self.assertEqual(self.request('/api/job-log?id=other-session')[0],400)
        self.assertEqual(self.request('/api/job-log?id=log-test&cursor=-1')[0],400)

    def test_stop_after_pass_does_not_change_result(self):
        ident=self.validate();status,data,_=self.request('/api/run',{'preflight_id':ident})
        self.assertEqual(status,202);job_id=data['job_id']
        self.assertEqual(self.wait_job(job_id)['status'],'pass')
        status,response,_=self.request('/api/job/stop',{'job_id':job_id})
        self.assertEqual(status,200);self.assertFalse(response['accepted'])
        self.assertEqual(self.request('/api/job?id='+job_id)[1]['status'],'pass')
        self.assertEqual(self.request(f'/api/artifact?id={job_id}&name=cells')[0],200)

    def test_one_active_job_and_runner_progress(self):
        ready=threading.Event(); release=threading.Event()
        def held(path,progress=None):
            progress('extraction'); ready.set(); release.wait(timeout=3)
            return dict(status='fail',output=None,error='controlled failure')
        with patch('gis_mesh_workbench.webui.run',side_effect=held):
            first=self.validate(); status,data,_=self.request('/api/run',{'preflight_id':first})
            self.assertEqual(status,202); self.assertTrue(ready.wait(timeout=1))
            _,job,_=self.request('/api/job?id='+data['job_id']); self.assertEqual(job['stage'],'extraction')
            # An active job now rejects new preflight work before it can retain
            # the Stop/progress lock; a second start remains forbidden too.
            self.assertEqual(self.request('/api/preflight',self.config)[0],400)
            self.assertEqual(self.request('/api/run',{'preflight_id':first})[0],400)
            self.assertEqual(len(self.server.jobs),1)
            release.set(); self.assertEqual(self.wait_job(data['job_id'])['status'],'fail')


if __name__=='__main__': unittest.main()
