"""Real HTTP integration for single-flight validation and verified QA pages."""
import http.client
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from gis_mesh_workbench.document_api import open_project
from gis_mesh_workbench.project_io import save_project
from gis_mesh_workbench.webui import WorkbenchServer


class LTSHTTPTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.server = WorkbenchServer(self.directory.name)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close)

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)

    def post(self, path, payload):
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=8)
        try:
            connection.request('POST', path, body=json.dumps(payload), headers={
                'Host': self.server.host, 'Origin': self.server.origin,
                'X-Workbench-Token': self.server.token, 'Content-Type': 'application/json'})
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def test_concurrent_http_preflight_rejects_without_superseding_first(self):
        entered, release = threading.Event(), threading.Event()
        results = []
        def blocked(_config):
            entered.set()
            if not release.wait(5):
                raise TimeoutError('controlled preflight was not released')
            return {'status': 'pass'}, None
        with patch.object(self.server, 'normalize_form', return_value={}), \
                patch.object(self.server, 'protect_config_inputs'), \
                patch('gis_mesh_workbench.webui.effective_config', return_value={}), \
                patch('gis_mesh_workbench.webui.preflight', side_effect=blocked):
            first = threading.Thread(target=lambda: results.append(self.post('/api/preflight', {})))
            first.start()
            try:
                self.assertTrue(entered.wait(2))
                generation = self.server.preflight_generation
                status, body = self.post('/api/preflight', {})
                self.assertEqual(status, 400)
                self.assertIn('chờ', body['error'])
                self.assertEqual(self.server.preflight_generation, generation)
            finally:
                release.set()
                first.join(6)
        self.assertFalse(first.is_alive())
        self.assertEqual(results[0][0], 200)
        self.assertFalse(results[0][1]['superseded'])
        self.assertEqual(self.server.current_preflight, results[0][1]['preflight_id'])

    def wait_quality(self, job_id):
        deadline = time.monotonic() + 15
        while self.server.job(job_id)['status'] in {'running', 'stopping'}:
            if time.monotonic() > deadline:
                self.fail('Quality job did not terminate')
            time.sleep(.01)
        self.assertEqual(self.server.job(job_id)['status'], 'pass')

    def test_two_real_quality_configs_remain_isolated_and_tamper_invalidates_both(self):
        archive = Path(self.directory.name) / 'input.mcy'
        save_project(archive, cells=dict(schema_version='workbench-cells-v1',
            crs_wkt='EPSG:32649', coordinate_units='m', cells=[dict(
                cell_id='tri-1', kind='triangle', xy=[[0, 0], [1, 0], [0, 1]])]),
            qa={'status': 'pass'})
        parent = open_project(self.server, archive.read_bytes())['job_id']
        jobs, hashes = [], []
        for threshold in (.1, .9):
            status, started = self.post('/api/quality/start', dict(mesh_job_id=parent,
                config={'warning_quality_q': threshold}))
            self.assertEqual(status, 202, started)
            jobs.append(started['job_id'])
            self.wait_quality(jobs[-1])
            status, report = self.post('/api/quality/result', {'job_id': jobs[-1]})
            self.assertEqual(status, 200, report)
            hashes.append(report['config_hash'])
            status, page = self.post('/api/quality/page', {'job_id': jobs[-1], 'limit': 1})
            self.assertEqual(status, 200, page)
            self.assertEqual(page['items'][0]['element_id'], 'tri-1')
        self.assertNotEqual(hashes[0], hashes[1])
        self.assertTrue(set(jobs).issubset({key[0] for key in self.server.quality_order_cache}))
        for job, expected in zip(jobs, hashes):
            status, report = self.post('/api/quality/result', {'job_id': job})
            self.assertEqual(status, 200)
            self.assertEqual(report['config_hash'], expected)
        cells = self.server.artifact(parent, 'cells')
        before = cells.read_bytes()
        stat = cells.stat()
        changed = before.replace(b'tri-1', b'tri-2', 1)
        self.assertNotEqual(before, changed)
        self.assertEqual(len(before), len(changed))
        cells.write_bytes(changed)
        os.utime(cells, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        for job in jobs:
            status, body = self.post('/api/quality/page', {'job_id': job, 'limit': 1})
            self.assertEqual(status, 400)
            self.assertIn('stale', body['error'])


if __name__ == '__main__':
    unittest.main()
