"""Authenticated draft preview and cancellation through real HTTP handlers."""
import threading
import unittest
from unittest.mock import patch

from tests import test_workbench_batch_draft_api as draft_helpers
from tests import test_workbench_webui as http_helpers
from gis_mesh_workbench.job_control import checkpoint


class DraftHTTPTests(unittest.TestCase):
    features = draft_helpers.BatchDraftAPITests.features
    payload = draft_helpers.BatchDraftAPITests.payload
    wait = draft_helpers.BatchDraftAPITests.wait
    request = http_helpers.WebUITests.request

    def setUp(self):
        draft_helpers.BatchDraftAPITests.setUp(self)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(lambda: (self.server.shutdown(), self.thread.join(timeout=3)))

    def test_preview_is_authenticated_bounded_and_serves_assets(self):
        payload = self.payload()
        self.assertEqual(self.request('/api/extraction-draft/preview', payload,
                         headers={'X-Workbench-Token': 'wrong'})[0], 403)
        with patch('gis_mesh_workbench.batch_extraction.extract_indexed',
                   side_effect=AssertionError('preview must not extract')):
            code, page, _ = self.request('/api/extraction-draft/preview', payload)
        self.assertEqual(code, 200, page)
        self.assertEqual(page['rows'][0]['values']['rain_col'], {'state': 'not_computed'})
        self.assertEqual(page['selected_cells'], 1)
        self.assertEqual(self.request('/api/extraction-draft/preview',
                         dict(payload, limit=201))[0], 400)
        for path, mime in [('/extraction_draft.js', 'text/javascript'),
                           ('/extraction_draft.css', 'text/css')]:
            code, body, headers = self.request(path)
            self.assertEqual(code, 200)
            self.assertIn(mime, headers['Content-Type'])
            self.assertTrue(body)

    def test_stop_waits_for_worker_and_retains_parent_mesh(self):
        before = self.cells_path.read_bytes()
        entered = threading.Event()
        release = threading.Event()

        def slow(*args, **kwargs):
            entered.set()
            release.wait(timeout=5)
            checkpoint()
            raise AssertionError('worker should observe cancellation')

        with patch('gis_mesh_workbench.batch_extraction.extract_indexed', side_effect=slow):
            code, started, _ = self.request('/api/batch/start', self.payload())
            self.assertEqual(code, 200, started)
            self.assertTrue(entered.wait(3))
            code, stopped, _ = self.request('/api/job/stop', {'job_id': started['job_id']})
            self.assertEqual(code, 200, stopped)
            self.assertTrue(stopped['accepted'])
            self.assertEqual(self.server.job(started['job_id'])['status'], 'stopping')
            release.set()
            self.assertEqual(self.wait(started['job_id'])['status'], 'cancelled')
        self.assertEqual(self.cells_path.read_bytes(), before)
        self.assertEqual(self.server.job('accepted-mesh')['status'], 'pass')

    def test_untouched_default_preview_confirm_computes_selected_raster(self):
        payload = dict(mesh_job_id='accepted-mesh', features=self.features())
        code, preview, _ = self.request('/api/extraction-draft/preview', payload)
        self.assertEqual(code, 200, preview)
        self.assertEqual(len(preview['draft']['columns']), 1)
        column = preview['draft']['columns'][0]
        self.assertEqual(column['source_name'], 'rain')
        code, started, _ = self.request('/api/batch/start', dict(payload,
            draft=preview['draft'], plan_hash=preview['plan_hash']))
        self.assertEqual(code, 200, started)
        self.assertEqual(self.wait(started['job_id'])['status'], 'pass')
        code, result, _ = self.request('/api/batch/preview',
                                      dict(job_id=started['job_id'], limit=1))
        self.assertEqual(code, 200, result)
        # Canonical draft column IDs survive pre- and post-extraction tables.
        value = result['rows'][0]['values'][column['id']]
        self.assertEqual(value['state'], 'computed')
        self.assertEqual(value['value'], 15)
        self.assertEqual(value['status'], 'complete')
