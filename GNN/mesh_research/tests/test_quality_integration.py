"""Public HTTP and archive regression for the on-demand QA workflow."""
import csv
import hashlib
import io
import json
import math
from pathlib import Path
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import zipfile
from unittest.mock import patch

from gis_mesh_workbench.project_io import save_project, load_project, quality_cache_state
from gis_mesh_workbench.quality_assessment import evaluate_mesh_quality
from gis_mesh_workbench.quality_context import enrich_quality
from gis_mesh_workbench.quality_reports import export_quality
from gis_mesh_workbench.quality_jobs import start_quality
from gis_mesh_workbench.document_api import open_project
from gis_mesh_workbench.webui import WorkbenchServer


def fixture():
    return dict(schema_version='workbench-cells-v1', crs_wkt='EPSG:32649',coordinate_units='m',
        cells=[dict(cell_id='tri-1',kind='triangle',xy=[[0,0],[1,0],[0,1]])])


class QualityIntegrationTests(unittest.TestCase):
    def test_live_sidecar_mutation_after_success_invalidates_cached_report(self):
        from gis_mesh_workbench.quality_jobs import quality_result
        with tempfile.TemporaryDirectory() as folder:
            server,thread,parent=self._opened_server(folder)
            try:
                output=Path(server.job(parent)['result']['output'])
                retained=output/'meshing'/'domain-boundary.json';retained.parent.mkdir()
                retained.write_text(json.dumps(dict(crs_wkt='EPSG:32649',geometry=dict(type='Polygon',
                    coordinates=[[[0,0],[1,0],[0,1],[0,0]]]))),encoding='utf-8')
                manifest_path=output/'manifest.json';manifest=json.loads(manifest_path.read_text())
                manifest['artifact_hashes']['meshing/domain-boundary.json']=hashlib.sha256(retained.read_bytes()).hexdigest()
                manifest_path.write_text(json.dumps(manifest),encoding='utf-8')
                started=start_quality(server,dict(mesh_job_id=parent));self._wait_quality(server,started['job_id'])
                self.assertEqual(quality_result(server,dict(job_id=started['job_id']))['job_status'],'pass')
                retained.write_text('{}',encoding='utf-8')
                with self.assertRaisesRegex(ValueError,'stale|verification'):
                    quality_result(server,dict(job_id=started['job_id']))
            finally:
                server.shutdown();server.server_close();thread.join(timeout=5)

    def _opened_server(self, folder):
        archive=Path(folder)/'input.mcy';save_project(archive,cells=fixture(),qa=dict(status='pass'))
        server=WorkbenchServer(folder)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        opened=open_project(server,archive.read_bytes())
        return server,thread,opened['job_id']

    def _wait_quality(self, server, job_id):
        deadline=time.monotonic()+15
        while server.job(job_id)['status'] in {'running','stopping'}:
            if time.monotonic()>deadline:self.fail('Quality job did not finish')
            time.sleep(.01)
        return server.job(job_id)

    def test_retained_reference_mutation_during_assessment_prevents_publication(self):
        with tempfile.TemporaryDirectory() as folder:
            server,thread,parent=self._opened_server(folder)
            try:
                output=Path(server.job(parent)['result']['output'])
                retained=output/'meshing'/'domain-boundary.json'
                retained.parent.mkdir()
                retained.write_text('{}',encoding='utf-8')
                manifest_path=output/'manifest.json'
                manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
                manifest.setdefault('artifact_hashes',{})['meshing/domain-boundary.json']=hashlib.sha256(retained.read_bytes()).hexdigest()
                manifest_path.write_text(json.dumps(manifest),encoding='utf-8')
                import gis_mesh_workbench.quality_jobs as jobs
                evaluate=jobs.evaluate_mesh_quality
                def mutate_after_read(*args,**kwargs):
                    result=evaluate(*args,**kwargs)
                    retained.write_text('{"changed":true}',encoding='utf-8')
                    return result
                with patch('gis_mesh_workbench.quality_jobs.evaluate_mesh_quality',side_effect=mutate_after_read):
                    started=start_quality(server,{'mesh_job_id':parent})
                    result=self._wait_quality(server,started['job_id'])
                self.assertEqual(result['status'],'fail')
                self.assertFalse((Path(folder)/('quality-'+started['job_id'])).exists())
                self.assertNotIn(started['job_id'],server.quality_latest)
            finally:
                server.shutdown();server.server_close();thread.join(timeout=5)

    def test_cancel_during_report_serialization_leaves_no_pass_artifact(self):
        with tempfile.TemporaryDirectory() as folder:
            server,thread,parent=self._opened_server(folder)
            try:
                import gis_mesh_workbench.quality_jobs as jobs
                serialize=jobs._json_bytes
                job_id_holder=[]
                def cancel_during_serialization(value):
                    if job_id_holder:
                        server.controls[job_id_holder[0]].cancel.set()
                    return serialize(value)
                with patch('gis_mesh_workbench.quality_jobs._json_bytes',side_effect=cancel_during_serialization):
                    started=start_quality(server,{'mesh_job_id':parent})
                    job_id_holder.append(started['job_id'])
                    result=self._wait_quality(server,started['job_id'])
                self.assertEqual(result['status'],'cancelled')
                self.assertFalse((Path(folder)/('quality-'+started['job_id'])).exists())
                self.assertNotIn(started['job_id'],server.quality_latest)
            finally:
                server.shutdown();server.server_close();thread.join(timeout=5)

    def test_quality_job_benchmark_labels_geometry_only_scope(self):
        with tempfile.TemporaryDirectory() as folder:
            server,thread,parent=self._opened_server(folder)
            try:
                started=start_quality(server,{'mesh_job_id':parent,'benchmark':True,'repetitions':1})
                job=self._wait_quality(server,started['job_id'])
                self.assertEqual(job['status'],'pass')
                report=server.quality_latest[started['job_id']]
                self.assertEqual(report['benchmark']['scope'],'geometry_only_qa')
                self.assertIn('not timed per repetition',report['benchmark']['context_note'])
            finally:
                server.shutdown();server.server_close();thread.join(timeout=5)

    def test_matching_roi_without_target_is_not_comprehensive_pass(self):
        mesh=fixture()
        roi=dict(crs_wkt=mesh['crs_wkt'],geometry=dict(type='Polygon',
            coordinates=[[[0,0],[1,0],[0,1],[0,0]]]))
        result=enrich_quality(evaluate_mesh_quality(mesh),mesh,roi=roi)
        self.assertEqual(result['boundary']['status'],'PASS')
        self.assertEqual(result['size']['status'],'NOT_AVAILABLE')
        self.assertEqual(result['status'],'PARTIAL')
        self.assertIn('target_size',result['completeness']['unavailable_checks'])
    def test_report_csv_formula_escape_and_html_worst(self):
        mesh=fixture();mesh['cells'][0]['cell_id']='=SUM(A1:A2)'
        result=evaluate_mesh_quality(mesh)
        body,_,_=export_quality(result,'csv')
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            rows=list(csv.reader(io.StringIO(archive.read('elements.csv').decode('utf-8-sig'))))
            self.assertEqual(rows[1][0],"'=SUM(A1:A2)")
        body,_,_=export_quality(result,'html')
        self.assertIn(b'=SUM(A1:A2)</td>',body)

    def test_canonical_hash_cache_roundtrip_and_reference_invalidation(self):
        mesh=fixture();result=enrich_quality(evaluate_mesh_quality(mesh),mesh)
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'quality.mcy'
            save_project(path,cells=mesh,qa=dict(status='pass',quality_assessment=result))
            restored=load_project(path)
            self.assertEqual(quality_cache_state(restored['qa']['quality_assessment'],restored['cells'])['status'],'CURRENT')
            restored['qa']['quality_assessment']['context_config']['size_tolerance']=.5
            self.assertEqual(quality_cache_state(restored['qa']['quality_assessment'],restored['cells'])['status'],'STALE')

    def test_real_http_quality_restore_page_export_and_save(self):
        with tempfile.TemporaryDirectory() as folder:
            archive=Path(folder)/'input.mcy';save_project(archive,cells=fixture(),qa=dict(status='pass'))
            server=WorkbenchServer(folder)
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            def post(path,data=None,raw=None):
                request=urllib.request.Request(server.origin+path,
                    data=raw if raw is not None else json.dumps(data).encode(),
                    headers={'Origin':server.origin,'X-Workbench-Token':server.token,
                             'Content-Type':'application/octet-stream' if raw is not None else 'application/json'})
                with urllib.request.urlopen(request,timeout=20) as response:
                    body=response.read();mime=response.headers.get_content_type()
                    return json.loads(body) if mime=='application/json' else body
            try:
                opened=post('/api/project/open',raw=archive.read_bytes())
                parent=opened['job_id']
                started=post('/api/quality/start',dict(mesh_job_id=parent))
                deadline=time.monotonic()+15
                while server.job(started['job_id'])['status'] in {'running','stopping'}:
                    if time.monotonic()>deadline:self.fail('Quality job did not finish')
                    time.sleep(.01)
                result=post('/api/quality/result',dict(job_id=started['job_id']))
                self.assertEqual(result['job_status'],'pass')
                self.assertAlmostEqual(result['elements'][0]['quality_q'],.8284271247461901)
                page=post('/api/quality/page',dict(job_id=started['job_id'],kind='elements',limit=1))
                self.assertEqual(page['total'],1)
                self.assertEqual(page['items'][0]['element_id'],'tri-1')
                body=post('/api/quality/export',dict(job_id=started['job_id'],format='csv'))
                self.assertTrue(zipfile.is_zipfile(io.BytesIO(body)))
                saved=post('/api/project/save',dict(mesh_job_id=parent))
                saved_path=Path(folder)/'saved.mcy';saved_path.write_bytes(saved)
                self.assertIn('quality_assessment',load_project(saved_path)['qa'])
                reopened=post('/api/project/open',raw=saved)
                cached=post('/api/quality/result',dict(mesh_job_id=reopened['job_id']))
                self.assertEqual(cached['mesh_hash'],result['mesh_hash'])
                original=server.artifact(parent,'cells')
                changed=json.loads(original.read_text())
                changed['cells'][0]['xy'][1][0]+=1
                original.write_text(json.dumps(changed),encoding='utf-8')
                with self.assertRaises(urllib.error.HTTPError):
                    post('/api/project/save',dict(mesh_job_id=parent))
                with self.assertRaises(urllib.error.HTTPError):
                    post('/api/quality/result',dict(job_id=started['job_id']))
            finally:
                server.shutdown();server.server_close();thread.join(timeout=5)


if __name__=='__main__':unittest.main()
