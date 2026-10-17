import hashlib
import http.client
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
import zipfile

from gis_mesh_workbench.webui import WorkbenchServer
from gis_mesh_workbench.document_api import open_project, save_project, export_result
from gis_mesh_workbench.runner import write_json
from gis_mesh_workbench.mesh_workflow import geometry_digest, quality, read_frozen
from gis_mesh_workbench.extraction import Cell


class DocumentAPITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.server = WorkbenchServer(self.root)
        self.addCleanup(self.server.server_close)
        self.cells = [Cell('a','rectangle',((500000,1000000),(500001,1000000),(500001,1000001),(500000,1000001))),
                      Cell('b','rectangle',((500001,1000000),(500002,1000000),(500002,1000001),(500001,1000001)))]
        from gis_mesh_workbench.spatial import metric_crs
        self.crs = metric_crs('EPSG:32649').to_wkt()
        self.payload = dict(schema_version='workbench-cells-v1',crs_wkt=self.crs,coordinate_units='m',
            cells=[dict(cell_id=c.cell_id,kind=c.kind,xy=c.xy) for c in self.cells])
        self.attributes = dict(schema_version='cell-attributes-reference-v0',
            provenance=dict(semantics='intensive',value_units='m',source_path='D:/Private/dem.tif'),
            cells=[dict(cell_id='a',value=10.0,footprint_coverage=1.0,valid_coverage=1.0),
                   dict(cell_id='b',value=30.0,footprint_coverage=1.0,valid_coverage=1.0)])
        self._job('accepted-mesh', self.payload, mesh=True)
        extraction = dict(self.payload)
        extraction.pop('schema_version')
        extraction['adapter_provenance'] = {'path': 'original/cells.json'}
        self._job('accepted-extract', extraction, mesh=False)

    def _job(self, ident, cells, mesh):
        output = self.server.output_root / ident
        output.mkdir()
        write_json(output/'cells.json', cells)
        write_json(output/'qa.json', dict(status='pass',mesh_quality=quality(self.cells)))
        if not mesh:
            write_json(output/'attributes.json',self.attributes)
        manifest = dict(status='pass',operation='mesh_only' if mesh else 'extract',
            geometry_digest=geometry_digest(self.cells,self.crs),
            artifact_hashes={'cells.json':hashlib.sha256((output/'cells.json').read_bytes()).hexdigest()})
        write_json(output/'manifest.json',manifest)
        self.server.jobs[ident] = dict(status='pass',stage='done',result=dict(status='pass',output=str(output)))

    def test_save_after_extraction_reopen_without_inputs_then_export(self):
        raw = save_project(self.server,dict(mesh_job_id='accepted-mesh',job_id='accepted-extract',
            view_settings=dict(show_edges=True,show_nodes=False,result_tab='extract',selected_cell_id='b',view_box='0 0 2 1')))
        reopened = open_project(self.server,raw)
        self.assertTrue(reopened['has_attributes'])
        self.assertEqual(reopened['view_settings']['selected_cell_id'],'b')
        output = Path(self.server.job(reopened['job_id'])['result']['output'])
        self.assertEqual(self.server.selected_files,set())
        restored = read_frozen(output/'cells.json',self.crs,maximum=1000)
        self.assertEqual([c.cell_id for c in restored.cells],['a','b'])
        self.assertEqual(restored.cells[1].xy,self.cells[1].xy)
        body,mime,name = export_result(self.server,dict(job_id=reopened['job_id'],format='asc',resolution=1.0))
        self.assertEqual(mime,'application/zip')
        with zipfile.ZipFile(io.BytesIO(body)) as zipped:
            self.assertIn('meshcherry.asc',zipped.namelist())
            self.assertIn('meshcherry.prj',zipped.namelist())
            grid = zipped.read('meshcherry.asc').decode()
            self.assertIn('10',grid);self.assertIn('30',grid)
        raw2 = save_project(self.server,dict(mesh_job_id=reopened['job_id'],job_id=reopened['job_id']))
        self.assertTrue(open_project(self.server,raw2)['has_attributes'])

    def test_historical_qa_and_attributes_csv_survive_import_artifact_and_resave(self):
        from gis_mesh_workbench.project_io import load_project
        historical = dict(status='pass', checks=dict(cell_count=2),
            sf1_sizing=dict(target_edge_m=120.0, achieved_median_edge_m=118.4),
            domain_coverage=dict(required_area_km2=6.2, covered_area_km2=6.2,
                                 coverage_fraction=1.0))
        write_json(self.server.output_root/'accepted-mesh'/'qa.json', historical)
        categories=dict(self.attributes)
        categories['cells']=[dict(self.attributes['cells'][0],value=None),
                             dict(self.attributes['cells'][1],value='class-A')]
        write_json(self.server.output_root/'accepted-extract'/'attributes.json',categories)
        raw = save_project(self.server,dict(mesh_job_id='accepted-mesh',job_id='accepted-extract'))
        reopened = open_project(self.server,raw)
        output = Path(self.server.job(reopened['job_id'])['result']['output'])
        self.assertTrue((output/'attributes.csv').is_file())
        restored_attributes=json.loads((output/'attributes.json').read_text())
        self.assertIsNone(restored_attributes['cells'][0]['value'])
        self.assertEqual(restored_attributes['cells'][1]['value'],'class-A')
        import csv
        with (output/'attributes.csv').open(encoding='utf-8-sig',newline='') as stream:
            csv_rows=list(csv.DictReader(stream))
        self.assertEqual(csv_rows[0]['value'],'')
        self.assertEqual(csv_rows[1]['value'],'class-A')
        thread = threading.Thread(target=self.server.serve_forever,daemon=True)
        thread.start()
        self.addCleanup(lambda:(self.server.shutdown(),thread.join(3)))
        conn=http.client.HTTPConnection(self.server.host,timeout=5)
        conn.request('GET',f"/api/artifact?id={reopened['job_id']}&name=qa&view=1",
            headers={'Origin':self.server.origin,'X-Workbench-Token':self.server.token})
        response=conn.getresponse();status=response.status;artifact=json.loads(response.read());conn.close()
        self.assertEqual(status,200)
        self.assertEqual(artifact['historical_qa']['sf1_sizing'],historical['sf1_sizing'])
        self.assertEqual(artifact['historical_qa']['domain_coverage'],historical['domain_coverage'])
        self.assertIn('mesh_quality',artifact)
        self.assertNotEqual(artifact['mesh_quality'],artifact['historical_qa'])
        resaved=save_project(self.server,dict(mesh_job_id=reopened['job_id'],job_id=reopened['job_id']))
        archive=Path(self.root/'resaved.mcy');archive.write_bytes(resaved)
        restored=load_project(archive)
        self.assertEqual(restored['qa']['sf1_sizing'],historical['sf1_sizing'])
        self.assertEqual(restored['qa']['domain_coverage'],historical['domain_coverage'])

    def test_export_route_accepts_nodata_override_for_value_collision(self):
        import numpy as np
        import rasterio
        from affine import Affine
        attrs=dict(self.attributes)
        attrs['cells']=[dict(self.attributes['cells'][0],value=-9999.0),self.attributes['cells'][1]]
        write_json(self.server.output_root/'accepted-extract'/'attributes.json',attrs)
        thread=threading.Thread(target=self.server.serve_forever,daemon=True);thread.start()
        self.addCleanup(lambda:(self.server.shutdown(),thread.join(3)))
        def post(payload):
            conn=http.client.HTTPConnection(self.server.host,timeout=5)
            conn.request('POST','/api/export',body=json.dumps(payload),headers={
                'Origin':self.server.origin,'X-Workbench-Token':self.server.token,
                'Content-Type':'application/json'})
            response=conn.getresponse();status=response.status;body=response.read();conn.close()
            return status,body
        base=dict(job_id='accepted-extract',format='geotiff',resolution=1.0)
        status,body=post(base)
        self.assertEqual(status,400)
        self.assertIn(b'collid',body)
        status,body=post(dict(base,nodata=-32768))
        self.assertEqual(status,200,body)
        with rasterio.MemoryFile(body) as memory:
            with memory.open() as src:
                self.assertEqual(src.nodata,-32768)
                self.assertIn(-9999,src.read(1))

    def test_active_job_and_geometry_mismatch_reject_document_mutation(self):
        self.server.jobs['running-job'] = dict(status='running',stage='meshing',result=None)
        with self.assertRaisesRegex(ValueError,'chờ'):
            open_project(self.server,b'invalid')
        self.assertFalse(self.server.document_active)
        self.server.jobs.pop('running-job')
        wrong = dict(self.payload,cells=[self.payload['cells'][0]])
        self._job('unrelated-result',wrong,mesh=True)
        with self.assertRaisesRegex(ValueError,'không thuộc'):
            save_project(self.server,dict(mesh_job_id='accepted-mesh',job_id='unrelated-result'))
        self.assertFalse(self.server.document_active)

    def test_import_write_failure_preserves_outputs_and_current_jobs(self):
        from unittest.mock import patch
        raw=save_project(self.server,dict(mesh_job_id='accepted-mesh',job_id='accepted-mesh'))
        previous=set(self.server.output_root.iterdir());jobs=set(self.server.jobs)
        with patch('gis_mesh_workbench.document_api.write_json',side_effect=OSError('disk full')):
            with self.assertRaisesRegex(OSError,'disk full'):
                open_project(self.server,raw)
        self.assertEqual(set(self.server.output_root.iterdir()),previous)
        self.assertEqual(set(self.server.jobs),jobs)
        self.assertFalse(self.server.document_active)

    def test_binary_project_route_auth_size_and_round_trip(self):
        thread = threading.Thread(target=self.server.serve_forever,daemon=True)
        thread.start()
        self.addCleanup(lambda:(self.server.shutdown(),thread.join(3)))
        raw = save_project(self.server,dict(mesh_job_id='accepted-mesh',job_id='accepted-mesh'))
        def request(headers,body):
            conn=http.client.HTTPConnection(self.server.host,timeout=5)
            conn.request('POST','/api/project/open',body=body,headers=headers)
            response=conn.getresponse();status=response.status;data=response.read();conn.close()
            return status,json.loads(data)
        headers={'Origin':self.server.origin,'Content-Type':'application/octet-stream'}
        self.assertEqual(request(headers,raw)[0],403)
        headers['X-Workbench-Token']=self.server.token
        status,data=request(headers,raw)
        self.assertEqual(status,200);self.assertFalse(data['has_attributes'])
        self.assertEqual(request(headers,b'bad archive')[0],400)
        self.assertEqual(self.server.selected_files,set())

    def test_real_indexed_extraction_can_save_reopen_and_export(self):
        import numpy as np
        import rasterio
        from affine import Affine
        from gis_mesh_workbench.runner import run
        config=dict(schema_version='gis-workbench-run-config-v1',operation='mesh_only',
            extraction_engine='indexed_windowed_v1',run_name='document-production',analysis_crs=self.crs,
            band=1,semantics='intensive',value_units='m',output_root=str(self.server.output_root),
            cell_source=dict(kind='structured_grid',extent=[500000,1000000,500002,1000001],
                cell_width_m=1,cell_height_m=1,boundary_policy='strict',max_cells=1000))
        config_path=self.root/'production-config.json';write_json(config_path,config)
        mesh=run(config_path);self.assertEqual(mesh['status'],'pass',mesh)
        self.server.jobs['production-mesh']=dict(status='pass',stage='done',result=mesh)
        source=self.root/'source.tif'
        with rasterio.open(source,'w',driver='GTiff',width=2,height=1,count=1,
                dtype='float64',crs=self.crs,transform=Affine(1,0,500000,0,-1,1000001)) as dst:
            dst.write(np.array([[10,30]],dtype='float64'),1)
        config.update(operation='extract',raster_path=str(source),cell_source=dict(kind='frozen_cells',
            path=str(Path(mesh['output'])/'cells.json'),max_cells=1000))
        write_json(config_path,config);extracted=run(config_path)
        self.assertEqual(extracted['status'],'pass',extracted)
        self.server.jobs['production-extract']=dict(status='pass',stage='done',result=extracted)
        actual=json.loads((Path(extracted['output'])/'attributes.json').read_text())
        self.assertEqual(actual['schema_version'],'cell-attributes-indexed-v1')
        raw=save_project(self.server,dict(mesh_job_id='production-mesh',job_id='production-extract'))
        reopened=open_project(self.server,raw)
        for fmt in ('geojson','geotiff','asc'):
            body,_,_=export_result(self.server,dict(job_id=reopened['job_id'],format=fmt,resolution=1.0))
            self.assertTrue(body)


if __name__=='__main__':
    unittest.main()
