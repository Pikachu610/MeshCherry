import hashlib
import http.client
import json
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

    def request(self,path,data=None,headers=None,method=None):
        connection=http.client.HTTPConnection(*self.server.server_address,timeout=5)
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

    def test_truthful_cute_ui_contract(self):
        from gis_mesh_workbench.webui import ASSETS
        html = (ASSETS/'index.html').read_text(encoding='utf-8')
        script = (ASSETS/'app.js').read_text(encoding='utf-8')
        css = (ASSETS/'wizard.css').read_text(encoding='utf-8')
        for label in ('Chọn GeoTIFF trên máy', 'Hai pixel nhân tạo', 'Gmsh reference demo'):
            self.assertIn(label, html)
        for unsupported in ('Kéo thả', 'khoảng 2 phút', 'Map vệ tinh', 'Phủ hoàn hảo'):
            self.assertNotIn(unsupported, html)
        for ident in ('welcome-start', 'welcome-skip', 'mode', 'raster', 'validate', 'run', 'csv', 'json', 'map'):
            self.assertEqual(html.count('id="'+ident+'"'), 1)
        self.assertEqual(html.count('data-mode='), 3)
        self.assertIn('Nhập CRS tính toán không sửa được lỗi này', script)
        self.assertIn('if(!source.crs){stage(1);', script)
        self.assertIn('null không có nghĩa là 0', script)
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

    def wait_job(self,ident):
        deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            _,job,_=self.request('/api/job?id='+ident)
            if job['status']!='running': return job
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
        except OSError: self.skipTest('local Windows symlink permission unavailable')
        self.assertEqual(self.request('/api/fs/list',{'path':str(link)})[0],403)

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

    def test_one_active_job_and_runner_progress(self):
        ready=threading.Event(); release=threading.Event()
        def held(path,progress=None):
            progress('extraction'); ready.set(); release.wait(timeout=3)
            return dict(status='fail',output=None,error='controlled failure')
        with patch('gis_mesh_workbench.webui.run',side_effect=held):
            first=self.validate(); status,data,_=self.request('/api/run',{'preflight_id':first})
            self.assertEqual(status,202); self.assertTrue(ready.wait(timeout=1))
            _,job,_=self.request('/api/job?id='+data['job_id']); self.assertEqual(job['stage'],'extraction')
            second=self.validate(); self.assertEqual(self.request('/api/run',{'preflight_id':second})[0],400)
            release.set(); self.assertEqual(self.wait_job(data['job_id'])['status'],'fail')


if __name__=='__main__': unittest.main()
