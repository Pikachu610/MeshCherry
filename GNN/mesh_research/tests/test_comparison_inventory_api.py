"""Exact count-only mesh inventories are isolated cancellable jobs."""
import json
from pathlib import Path
import threading
import time
import unittest
from unittest.mock import patch

from tests import test_workbench_webui as helpers
from gis_mesh_workbench.extraction import Cell, _digest
from gis_mesh_workbench.job_control import checkpoint
from gis_mesh_workbench.mesh_workflow import geometry_digest


class ComparisonInventoryAPITests(unittest.TestCase):
    setUp = helpers.WebUITests.setUp
    stop = helpers.WebUITests.stop
    request = helpers.WebUITests.request
    wait_job = helpers.WebUITests.wait_job

    def seed(self):
        output=self.server.output_root/'accepted-inventory'
        output.mkdir()
        cells=(Cell('cell-0','triangle',((500000,1000000),(500002,1000000),(500000,1000001))),
               Cell('cell-1','triangle',((500002,1000000),(500002,1000001),(500000,1000001))))
        path=output/'cells.json'
        path.write_text(json.dumps(dict(schema_version='workbench-cells-v1',coordinate_units='m',
            crs_wkt='EPSG:32649',cells=[dict(cell_id=c.cell_id,kind=c.kind,xy=c.xy) for c in cells])))
        (output/'manifest.json').write_text(json.dumps(dict(status='pass',operation='mesh_only',
            geometry_digest=geometry_digest(cells,'EPSG:32649'),artifact_hashes={'cells.json':_digest(path)})))
        (output/'qa.json').write_text(json.dumps(dict(status='pass',cell_count=2,
            geometry_digest=geometry_digest(cells,'EPSG:32649'))))
        self.server.jobs['accepted-inventory']=dict(status='pass',stage='done',operation='mesh_only',
            result=dict(output=str(output)))
        return path

    def target_payload(self,request_id='inventory-test-0001'):
        return dict(mesh_job_id='accepted-inventory',baseline_mode='target_grid',cell_size_m=1,
            request_id=request_id)

    def test_target_grid_exact_count_publication_recovery_and_monitor(self):
        cells=self.seed();before=cells.read_bytes()
        status,started,_=self.request('/api/comparison/count',self.target_payload())
        self.assertEqual(status,202,started)
        self.assertEqual(started['operation'],'comparison_count')
        self.assertEqual(started['mesh_count'],dict(count=2,definition='accepted_triangle_cells',unit='cells'))
        self.assertEqual(self.wait_job(started['job_id'])['status'],'pass')
        recovery=self.request('/api/comparison/count/status?request_id=inventory-test-0001')[1]
        self.assertEqual(recovery,dict(job_id=started['job_id'],operation='comparison_count',
            request_id='inventory-test-0001',status='pass',mesh_count=started['mesh_count'],
            mesh_sha256=started['mesh_sha256']))
        status,inventory,_=self.request('/api/artifact?id='+started['job_id']+'&name=comparison_counts')
        self.assertEqual(status,200,inventory)
        self.assertEqual(inventory['mesh_count']['count'],2)
        self.assertEqual(inventory['baseline']['count'],2)
        self.assertEqual(inventory['baseline']['definition'],'positive_area_clipped_connected_components')
        job=self.request('/api/job?id='+started['job_id'])[1]
        self.assertEqual(job['monitor']['phase'],'comparison_publish')
        self.assertIsNone(job['monitor']['phase_percent'])
        self.assertNotIn('phase_progress',job['monitor'])
        self.assertEqual(cells.read_bytes(),before)
        self.assertEqual(self.server.job('accepted-inventory')['status'],'pass')
        recovered=self.request('/api/comparison/count',self.target_payload())[1]
        self.assertEqual(recovered['job_id'],started['job_id'])
        self.assertEqual(self.request('/api/comparison/count',
            {**self.target_payload(),'cell_size_m':2})[0],400)

    def test_target_count_reports_completed_work_before_publication(self):
        from gis_mesh_workbench.comparison_inventory import build_inventory
        path=self.seed();observations=[]
        build_inventory(path,mesh_job_id='accepted-inventory',baseline_mode='target_grid',
            analysis_crs='EPSG:32649',cell_size_m=1,
            progress=lambda *observation:observations.append(observation))
        counts=[entry for entry in observations if entry[0]=='count_target_grid']
        self.assertTrue(counts)
        self.assertEqual(counts[-1],('count_target_grid',2,2,'bounding_grid_cells'))

    def test_target_count_does_not_require_raster_and_rejects_missing_size(self):
        self.seed()
        status,_,_=self.request('/api/comparison/count',
            dict(mesh_job_id='accepted-inventory',baseline_mode='target_grid',request_id='inventory-test-0002'))
        self.assertEqual(status,400)

    def test_malformed_count_payloads_return_validation_errors(self):
        self.seed()
        payload=self.target_payload('inventory-test-0005')
        for invalid in (
            {key:value for key,value in payload.items() if key!='mesh_job_id'},
            {key:value for key,value in payload.items() if key!='baseline_mode'},
            {**payload,'cell_size_m':True,'request_id':'inventory-test-0006'},
            {**payload,'cell_size_m':float('inf'),'request_id':'inventory-test-0007'},
            {**payload,'unknown':'field','request_id':'inventory-test-0008'},
        ):
            self.assertEqual(self.request('/api/comparison/count',invalid)[0],400)

    def test_active_job_recovery_includes_full_mesh_count_and_artifact_hash(self):
        self.seed();entered=threading.Event();release=threading.Event()
        def paused(*args,**kwargs):
            entered.set();self.assertTrue(release.wait(3))
            return 1,[500000,1000000],dict(columns=1,rows=1,cells_examined=1)
        with patch('gis_mesh_workbench.comparison_inventory._target_grid_count',side_effect=paused):
            status,started,_=self.request('/api/comparison/count',self.target_payload('inventory-test-0009'))
            self.assertEqual(status,202);self.assertTrue(entered.wait(2))
            active=self.request('/api/active-job')[1]
            self.assertEqual(active['job_id'],started['job_id'])
            self.assertEqual(active['mesh_count'],dict(count=2,definition='accepted_triangle_cells',unit='cells'))
            self.assertEqual(active['mesh_sha256'],started['mesh_sha256'])
            release.set()
            self.assertEqual(self.wait_job(started['job_id'])['status'],'pass')

    def test_native_count_separates_geometric_pixels_from_valid_data(self):
        self.seed()
        status,started,_=self.request('/api/comparison/count',dict(mesh_job_id='accepted-inventory',
            baseline_mode='native_raster',raster_path=str(self.raster),request_id='inventory-native-0001'))
        self.assertEqual(status,202,started)
        self.assertEqual(self.wait_job(started['job_id'])['status'],'pass')
        status,inventory,_=self.request('/api/artifact?id='+started['job_id']+'&name=comparison_counts')
        self.assertEqual(status,200,inventory)
        self.assertEqual(inventory['baseline']['count'],2)
        self.assertEqual(inventory['baseline']['definition'],
            'source_pixels_with_positive_area_intersection_with_mesh_union')
        self.assertEqual(inventory['provenance']['raster_grid']['source_pixel_count'],2)
        self.assertEqual(inventory['provenance']['raster_grid']['edge_subdivisions'],8)
        self.assertFalse(inventory['provenance']['raster_grid']['footprint_approximate'])
        self.assertIn('cross-CRS',inventory['provenance']['raster_grid']['area_model'])
        self.assertEqual(inventory['provenance']['valid_data_count']['status'],'not_computed')
        self.assertIsNone(inventory['provenance']['valid_data_count']['value'])

    def test_tampered_mesh_is_rejected_before_immediate_full_count(self):
        cells=self.seed();cells.write_bytes(cells.read_bytes()+b' ')
        status,_,_=self.request('/api/comparison/count',self.target_payload('inventory-test-0003'))
        self.assertEqual(status,400)

    def test_mesh_mutated_while_loading_is_rejected_before_kpi_publication(self):
        cells=self.seed()
        from gis_mesh_workbench import webui
        original=webui._json
        def mutate_after_read(path):
            value=original(path)
            if Path(path).name=='cells.json':
                Path(path).write_bytes(Path(path).read_bytes()+b' ')
            return value
        with patch('gis_mesh_workbench.webui._json',side_effect=mutate_after_read):
            status,_,_=self.request('/api/comparison/count',self.target_payload('inventory-test-0010'))
        self.assertEqual(status,400)
        self.assertFalse(any(job.get('operation')=='comparison_count'
                             for job in self.server.jobs.values()))

    def test_stop_leaves_count_artifact_unpublished_and_mesh_accepted(self):
        cells=self.seed();before=cells.read_bytes();entered=threading.Event()
        def slow(*args,**kwargs):
            entered.set()
            for _ in range(400):
                checkpoint();time.sleep(.005)
            return 1,[0,0],dict(columns=1,rows=1,cells_examined=1)
        with patch('gis_mesh_workbench.comparison_inventory._target_grid_count',side_effect=slow):
            status,started,_=self.request('/api/comparison/count',self.target_payload('inventory-test-0004'))
            self.assertEqual(status,202);self.assertTrue(entered.wait(2))
            self.assertTrue(self.request('/api/job/stop',dict(job_id=started['job_id']))[1]['accepted'])
            self.assertEqual(self.wait_job(started['job_id'])['status'],'cancelled')
        self.assertNotEqual(self.request('/api/artifact?id='+started['job_id']+
            '&name=comparison_counts')[0],200)
        self.assertEqual(cells.read_bytes(),before)


if __name__=='__main__':
    unittest.main()
