"""Live job failures must expose an explanation without reading an artifact."""
import unittest
from unittest.mock import patch

from tests import test_workbench_webui as helpers


class FailureWorkerTests(unittest.TestCase):
    setUp = helpers.WebUITests.setUp
    stop = helpers.WebUITests.stop
    request = helpers.WebUITests.request
    validate = helpers.WebUITests.validate
    wait_job = helpers.WebUITests.wait_job

    def test_returned_failure_has_actionable_details_without_manifest(self):
        preflight_id = self.validate()
        raw = 'cells overlap in positive area'
        with patch('gis_mesh_workbench.webui.run', return_value={
            'status': 'fail', 'output': None, 'error': raw,
        }):
            started = self.server.start_job(preflight_id)
            completed = self.wait_job(started['job_id'])
        self.assertEqual(completed['status'], 'fail')
        self.assertEqual(completed['result']['error'], raw)
        failure = completed['result']['failure']
        self.assertEqual(failure['code'], 'cell_overlap')
        self.assertIn('chồng', failure['message'].lower())
        self.assertTrue(failure['actions'])

    def test_unknown_failure_with_no_output_does_not_claim_manifest_exists(self):
        preflight_id = self.validate()
        with patch('gis_mesh_workbench.webui.run', return_value={
            'status': 'fail', 'output': None, 'error': r'could not read D:\private\source.tif',
        }):
            started = self.server.start_job(preflight_id)
            completed = self.wait_job(started['job_id'])
        self.assertIsNone(completed['result']['output'])
        failure = completed['result']['failure']
        self.assertEqual(failure['code'], 'unknown_failure')
        copy = ' '.join([failure['message'], *failure['actions']]).lower()
        self.assertIn('chi tiết kỹ thuật', copy)
        self.assertNotIn('manifest', copy)

    def test_unexpected_exception_has_actionable_details(self):
        preflight_id = self.validate()
        with patch('gis_mesh_workbench.webui.run', side_effect=ValueError('cells overlap in positive area')):
            started = self.server.start_job(preflight_id)
            completed = self.wait_job(started['job_id'])
        self.assertEqual(completed['result']['failure']['code'], 'cell_overlap')

    def test_cancelled_does_not_acquire_failure_diagnosis(self):
        preflight_id = self.validate()
        with patch('gis_mesh_workbench.webui.run', return_value={
            'status': 'cancelled', 'output': None, 'error': 'stopped by user',
        }):
            started = self.server.start_job(preflight_id)
            completed = self.wait_job(started['job_id'])
        self.assertEqual(completed['status'], 'cancelled')
        self.assertNotIn('failure', completed['result'])
