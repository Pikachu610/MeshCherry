"""Owned, cancellable comparison jobs must not replace accepted mesh state."""
import hashlib
import json
from pathlib import Path
import sys
import threading
import time
import unittest
from unittest.mock import patch

from tests import test_workbench_webui as helpers
from gis_mesh_workbench.extraction import Cell, _digest
from gis_mesh_workbench.mesh_workflow import geometry_digest
from gis_mesh_workbench.job_control import checkpoint
from gis_mesh_workbench.research_reference import sf1_reference


class ComparisonAPITests(unittest.TestCase):
    setUp = helpers.WebUITests.setUp
    stop = helpers.WebUITests.stop
    request = helpers.WebUITests.request
    wait_job = helpers.WebUITests.wait_job

    def test_sf1_preflight_rejects_missing_explicit_scale(self):
        config = dict(schema_version='gis-workbench-run-config-v2', operation='mesh_only',
            extraction_engine='indexed_windowed_v1', run_name='sf1-missing-scale',
            analysis_crs='EPSG:32649', output_root=str(self.server.output_root),
            cell_source=dict(kind='gmsh_generated', executable=sys.executable,
                extent=[500000,1000000,501800,1001800], sizing='sf1_relief',
                h_min_m=250,h_max_m=1000,dem_raster_path=str(self.raster),dem_band=1,
                dem_value_units='m',max_cells=1000))
        status, data, _ = self.request('/api/preflight', config)
        self.assertEqual(status, 200, data)
        self.assertEqual(data['report']['status'], 'fail')
        self.assertIn('sf1_scale', str(data['report']['errors']))
        self.assertIsNone(data['preflight_id'])
        self.assertFalse(self.server.jobs)

    def seed(self):
        output = self.server.output_root/'accepted'
        output.mkdir()
        cells = (
            Cell('cell-0','triangle',((500000,1000000),(500002,1000000),(500000,1000001))),
            Cell('cell-1','triangle',((500002,1000000),(500002,1000001),(500000,1000001))),
        )
        path = output/'cells.json'
        path.write_text(json.dumps(dict(schema_version='workbench-cells-v1',coordinate_units='m',
            crs_wkt='EPSG:32649',cells=[dict(cell_id=c.cell_id,kind=c.kind,xy=c.xy) for c in cells])))
        (output/'manifest.json').write_text(json.dumps(dict(status='pass',operation='mesh_only',
            geometry_digest=geometry_digest(cells,'EPSG:32649'),artifact_hashes={'cells.json':_digest(path)})))
        self.server.jobs['accepted']=dict(status='pass',stage='done',operation='mesh_only',result=dict(output=str(output)))
        self.payload=dict(mesh_job_id='accepted',raster_path=str(self.raster),band=1,
            semantics='intensive',value_units='m',cell_size_m=1)
        return path

    def test_real_comparison_report_download_and_original_identity(self):
        path=self.seed();before=path.read_bytes()
        status,data,_=self.request('/api/comparison',self.payload)
        self.assertEqual(status,202,data)
        job=self.wait_job(data['job_id'])
        self.assertEqual(job['status'],'pass',job)
        status,report,_=self.request('/api/artifact?id='+data['job_id']+'&name=comparison')
        self.assertEqual(status,200,report)
        self.assertEqual(report['structured']['node_count'],2)
        self.assertEqual(report['unstructured']['node_count'],2)
        self.assertEqual(report['common_support_area_m2'],2)
        self.assertEqual(path.read_bytes(),before)
        self.assertEqual(self.server.job('accepted')['status'],'pass')
        self.assertEqual(self.request('/api/artifact?id='+data['job_id']+'&name=comparison&download=1')[2]['Content-Disposition'],
                         'attachment; filename="comparison.json"')

    def test_comparison_stop_and_mutual_exclusion_keep_accepted_mesh(self):
        path=self.seed();before=path.read_bytes();started=threading.Event()
        def slow(*args,**kwargs):
            started.set()
            for _ in range(500):
                checkpoint();time.sleep(.005)
            raise AssertionError('Stop was not observed')
        with patch('gis_mesh_workbench.comparison.compare_mesh',side_effect=slow):
            status,data,_=self.request('/api/comparison',self.payload)
            self.assertEqual(status,202,data);self.assertTrue(started.wait(2))
            active=self.request('/api/active-job')[1]
            self.assertEqual(active,dict(job_id=data['job_id'],operation='comparison'))
            self.assertEqual(self.request('/api/comparison',self.payload)[0],400)
            self.assertEqual(self.request('/api/job/stop',dict(job_id=data['job_id']))[0],200)
            job=self.wait_job(data['job_id'])
        self.assertEqual(job['status'],'cancelled',job)
        self.assertEqual(self.request('/api/artifact?id='+data['job_id']+'&name=comparison')[0],403)
        self.assertEqual(self.request('/api/artifact?id=accepted&name=cells')[0],200)
        self.assertEqual(path.read_bytes(),before)

    def test_comparison_authorization_unknown_job_and_bad_values(self):
        self.seed()
        self.assertEqual(self.request('/api/comparison',self.payload,headers={'X-Workbench-Token':'wrong'})[0],403)
        self.assertEqual(self.request('/api/comparison',self.payload,headers={'Origin':'https://evil.example'})[0],403)
        for change in ({'mesh_job_id':'missing'},{'cell_size_m':0},{'band':True},{'extra':1},
                       {'raster_path':str(self.base/'outside.tif')}):
            status,_,_=self.request('/api/comparison',{**self.payload,**change})
            self.assertNotEqual(status,202)

    def test_failure_does_not_publish_comparison_or_change_mesh(self):
        path=self.seed();before=path.read_bytes()
        with patch('gis_mesh_workbench.comparison.compare_mesh',side_effect=ValueError('bounded failure')):
            status,data,_=self.request('/api/comparison',self.payload)
            self.assertEqual(status,202)
            job=self.wait_job(data['job_id'])
        self.assertEqual(job['status'],'fail')
        self.assertEqual(self.request('/api/artifact?id='+data['job_id']+'&name=comparison')[0],403)
        self.assertEqual(path.read_bytes(),before)

    def test_idempotent_request_recovers_completed_comparison_and_rejects_reuse(self):
        self.seed();payload={**self.payload,'request_id':'compare-test-unique-01'}
        status,data,_=self.request('/api/comparison',payload)
        self.assertEqual(status,202)
        self.assertEqual(self.wait_job(data['job_id'])['status'],'pass')
        self.assertEqual(self.request('/api/comparison',payload)[1]['job_id'],data['job_id'])
        recovered=self.request('/api/comparison/status?request_id='+payload['request_id'])[1]
        self.assertEqual(recovered,dict(job_id=data['job_id'],operation='comparison',status='pass'))
        self.assertEqual(self.request('/api/comparison',{**payload,'cell_size_m':2})[0],400)
        self.assertEqual(self.request('/api/comparison/status?request_id=missing-key')[1],dict(job_id=None))
        stopped=self.request('/api/job/stop',dict(job_id=data['job_id']))[1]
        self.assertFalse(stopped['accepted']);self.assertEqual(stopped['status'],'pass')

    def test_stop_between_report_write_and_publication_denies_artifact(self):
        self.seed();entered=threading.Event();release=threading.Event()
        from gis_mesh_workbench.job_control import finalize_manifest as original
        def wait_publish(output,manifest):
            entered.set();release.wait(3)
            return original(output,manifest)
        with patch('gis_mesh_workbench.webui.finalize_manifest',side_effect=wait_publish):
            status,data,_=self.request('/api/comparison',self.payload)
            self.assertEqual(status,202);self.assertTrue(entered.wait(2))
            try:
                self.assertTrue(self.request('/api/job/stop',dict(job_id=data['job_id']))[1]['accepted'])
            finally:release.set()
            self.assertEqual(self.wait_job(data['job_id'])['status'],'cancelled')
        self.assertEqual(self.request('/api/artifact?id='+data['job_id']+'&name=comparison')[0],403)
        self.assertEqual(self.request('/api/artifact?id=accepted&name=cells')[0],200)

    def test_changed_frozen_mesh_fails_closed(self):
        path=self.seed()
        def change_mesh(*args,**kwargs):
            path.write_bytes(path.read_bytes()+b' ')
            return {'status':'synthetic result'}
        with patch('gis_mesh_workbench.comparison.compare_mesh',side_effect=change_mesh):
            status,data,_=self.request('/api/comparison',self.payload)
            self.assertEqual(status,202)
            self.assertEqual(self.wait_job(data['job_id'])['status'],'fail')
        self.assertEqual(self.request('/api/artifact?id='+data['job_id']+'&name=comparison')[0],403)

    def test_whitespace_units_rejected_before_start(self):
        self.seed()
        self.assertEqual(self.request('/api/comparison',{**self.payload,'value_units':'   '})[0],400)

    def test_new_sidecar_outside_exact_file_grant_is_denied_in_worker(self):
        from gis_mesh_workbench.comparison import compare_mesh
        path = self.seed(); before = path.read_bytes()
        external = self.base / 'selected.tif'
        external.write_bytes(self.raster.read_bytes())
        self.server.selected_files.add(external.resolve())
        def add_sidecar(*args, **kwargs):
            Path(str(external)+'.aux.xml').write_text('<PAMDataset/>', encoding='utf-8')
            return compare_mesh(*args, **kwargs)
        payload = {**self.payload, 'raster_path':str(external)}
        with patch('gis_mesh_workbench.comparison.compare_mesh', side_effect=add_sidecar):
            status, data, _ = self.request('/api/comparison', payload)
            self.assertEqual(status, 202, data)
            job = self.wait_job(data['job_id'])
        self.assertEqual(job['status'], 'fail', job)
        self.assertIn('Path nằm ngoài', job['result']['error'])
        self.assertEqual(self.request('/api/artifact?id='+data['job_id']+'&name=comparison')[0],403)
        self.assertEqual(path.read_bytes(), before)

    def test_research_reference_is_authenticated_historical_and_matches_retained_gnn(self):
        status,report,_=self.request('/api/research/sf1')
        self.assertEqual(status,200)
        self.assertEqual(report['selected']['node_count'],13543)
        self.assertEqual(report['baseline']['node_count'],13515)
        self.assertEqual(self.request('/api/research/sf1',headers={'X-Workbench-Token':'wrong'})[0],403)
        report['selected']['node_count']=0
        self.assertEqual(sf1_reference()['selected']['node_count'],13543)
        root=Path(__file__).resolve().parents[3]
        retained=root/sf1_reference()['source']['summary_path']
        if retained.exists():
            self.assertEqual(hashlib.sha256(retained.read_bytes()).hexdigest(),sf1_reference()['source']['summary_sha256'])
            data=json.loads(retained.read_text())
            winner=next(c for c in data['cases'] if c['case_id']==data['selected_winner'])
            self.assertEqual(winner['cell_count'],sf1_reference()['selected']['cell_count'])
