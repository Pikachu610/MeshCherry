"""Research policy must survive admission, worker, report and request recovery."""
import json
import threading
import time
import unittest
from unittest.mock import patch

from tests import test_workbench_webui as helpers
from tests import test_workbench_comparison_api as comparison_helpers
from gis_mesh_workbench.indexed_extraction import limits
from gis_mesh_workbench.job_control import checkpoint


class ComparisonResearchAPITests(unittest.TestCase):
    setUp = helpers.WebUITests.setUp
    stop = helpers.WebUITests.stop
    request = helpers.WebUITests.request
    wait_job = helpers.WebUITests.wait_job
    seed = comparison_helpers.ComparisonAPITests.seed

    def test_research_bypasses_total_read_budget_and_binds_report_config(self):
        mesh = self.seed()
        before = mesh.read_bytes()
        with patch('gis_mesh_workbench.comparison.limits', return_value={**limits(), 'max_pixels_read': 3}):
            status, started, _ = self.request('/api/comparison', {**self.payload, 'workload_mode': 'research'})
            self.assertEqual(status, 202, started)
            job = self.wait_job(started['job_id'])
        self.assertEqual(job['status'], 'pass', job)
        status, report, _ = self.request('/api/artifact?id=' + started['job_id'] + '&name=comparison')
        self.assertEqual(status, 200, report)
        self.assertEqual(report['workload']['workload_mode'], 'research')
        self.assertIsNone(report['workload']['hard_limits']['max_pixels_read'])
        self.assertEqual(report['workload']['aggregate']['pixels_read'], 4)
        config = json.loads((self.server.artifact(started['job_id'], 'comparison').parent / 'config.json').read_text())
        self.assertEqual(config['workload_mode'], 'research')
        self.assertEqual(mesh.read_bytes(), before)

    def test_unknown_or_nonstring_policy_rejected_without_job(self):
        self.seed()
        for mode in ('unlimited', '', None, True, 1, {}):
            with self.subTest(mode=mode):
                status, _, _ = self.request('/api/comparison', {**self.payload, 'workload_mode': mode})
                self.assertEqual(status, 400)
        self.assertEqual(set(self.server.jobs), {'accepted'})

    def test_policy_participates_in_request_identity(self):
        self.seed()
        payload = {**self.payload, 'workload_mode': 'research', 'request_id': 'research-request-0001'}
        status, started, _ = self.request('/api/comparison', payload)
        self.assertEqual(status, 202, started)
        self.assertEqual(self.wait_job(started['job_id'])['status'], 'pass')
        repeated = self.request('/api/comparison', payload)
        self.assertEqual(repeated[0], 202)
        self.assertEqual(repeated[1]['job_id'], started['job_id'])
        self.assertEqual(self.request('/api/comparison', {**payload, 'workload_mode': 'bounded'})[0], 400)

    def test_research_stop_never_publishes_completed_comparison(self):
        self.seed()
        entered = threading.Event()
        def slow(*args, **kwargs):
            self.assertEqual(kwargs['workload_mode'], 'research')
            entered.set()
            for _ in range(500):
                checkpoint()
                time.sleep(.005)
            raise AssertionError('Stop was not observed')
        with patch('gis_mesh_workbench.comparison.compare_mesh', side_effect=slow), \
                patch('gis_mesh_workbench.comparison.STRtree', side_effect=AssertionError('API built tree')), \
                patch('gis_mesh_workbench.comparison_admission.windows', side_effect=AssertionError('API scanned raster')):
            status, started, _ = self.request('/api/comparison', {**self.payload, 'workload_mode': 'research'})
            self.assertEqual(status, 202, started)
            self.assertTrue(entered.wait(2))
            self.assertEqual(self.request('/api/job/stop', {'job_id': started['job_id']})[0], 200)
            job = self.wait_job(started['job_id'])
        self.assertEqual(job['status'], 'cancelled', job)
        self.assertEqual(self.request('/api/artifact?id=' + started['job_id'] + '&name=comparison')[0], 403)

    def test_policy_not_accepted_for_geometry_only_estimate(self):
        self.seed()
        payload = dict(mesh_job_id='accepted', baseline_mode='resolution_estimate',
                       cell_size_m=30, source_resolution_m=60, workload_mode='research')
        self.assertEqual(self.request('/api/comparison', payload)[0], 400)


if __name__ == '__main__':
    unittest.main()
