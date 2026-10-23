"""Release regression: reject known-over-budget work before creating a job."""
import unittest
from unittest.mock import patch

from tests import test_workbench_webui as helpers
from tests import test_workbench_comparison_api as comparison_helpers
from gis_mesh_workbench.indexed_extraction import limits


class ComparisonReleaseAPITests(unittest.TestCase):
    setUp = helpers.WebUITests.setUp
    stop = helpers.WebUITests.stop
    request = helpers.WebUITests.request
    wait_job = helpers.WebUITests.wait_job
    seed = comparison_helpers.ComparisonAPITests.seed

    def test_intensive_two_pass_cap_is_rejected_before_job_creation(self):
        mesh = self.seed()
        before = mesh.read_bytes()
        cap = {**limits(), 'max_pixels_read': 3}
        with patch('gis_mesh_workbench.comparison.limits', return_value=cap):
            status, body, _ = self.request('/api/comparison', self.payload)
        self.assertEqual(status, 400, body)
        self.assertEqual(set(self.server.jobs), {'accepted'})
        self.assertIn('failure', body)
        self.assertIn('message', body['failure'])
        self.assertEqual(mesh.read_bytes(), before)

    def test_runtime_failure_has_guidance_but_keeps_technical_detail(self):
        self.seed()
        with patch('gis_mesh_workbench.comparison.compare_mesh',
                   side_effect=ValueError('comparison read pixel limit exceeded')):
            status, body, _ = self.request('/api/comparison', self.payload)
            self.assertEqual(status, 202, body)
            job = self.wait_job(body['job_id'])
        self.assertEqual(job['status'], 'fail', job)
        result = job['result']
        self.assertEqual(result['error'], 'comparison read pixel limit exceeded')
        self.assertIn('failure', result)
        self.assertNotIn('comparison read pixel limit exceeded', result['failure']['message'])
        self.assertTrue(result['failure']['actions'])


if __name__ == '__main__':
    unittest.main()
