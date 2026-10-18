"""HTTP contracts for partial batch previews and streamed exports."""
import http.client
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
import zipfile
from io import BytesIO

from affine import Affine
import numpy as np
import rasterio

from gis_mesh_workbench.extraction import Cell, _digest
from gis_mesh_workbench.mesh_workflow import geometry_digest
from gis_mesh_workbench.webui import WorkbenchServer


class BatchHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.server = WorkbenchServer(self.root)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self._stop_server)

        self.crs = 'EPSG:32649'
        cells = [
            Cell('cell-a', 'rectangle', ((500000, 1000000), (500002, 1000000),
                                         (500002, 1000001), (500000, 1000001))),
            Cell('cell-b', 'rectangle', ((500002, 1000000), (500004, 1000000),
                                         (500004, 1000001), (500002, 1000001))),
        ]
        output = self.server.output_root / 'accepted-mesh'
        output.mkdir()
        cells_path = output / 'cells.json'
        cells_path.write_text(json.dumps(dict(schema_version='workbench-cells-v1',
            coordinate_units='m', crs_wkt=self.crs,
            cells=[dict(cell_id=c.cell_id, kind=c.kind, xy=c.xy) for c in cells])), encoding='utf-8')
        (output / 'manifest.json').write_text(json.dumps(dict(status='pass', operation='mesh_only',
            geometry_digest=geometry_digest(cells, self.crs),
            artifact_hashes={'cells.json': _digest(cells_path)})), encoding='utf-8')
        self.server.jobs['accepted-mesh'] = dict(status='pass', stage='done', operation='mesh_only',
            result=dict(status='pass', output=str(output)))

        self.raster = self.root / 'source.tif'
        with rasterio.open(self.raster, 'w', driver='GTiff', width=4, height=1,
                           count=1, dtype='float64', crs=self.crs,
                           transform=Affine(1, 0, 500000, 0, -1, 1000001)) as dst:
            dst.write(np.array([[10, 20, 30, 40]], dtype='float64'), 1)

    def _stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)

    def request(self, method, path, data=None, token=True):
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=5)
        headers = {'Origin': self.server.origin, 'Content-Type': 'application/json'}
        if token:
            headers['X-Workbench-Token'] = self.server.token
        body = json.dumps(data).encode('utf-8') if data is not None else None
        connection.request(method, path, body, headers)
        response = connection.getresponse()
        status, payload = response.status, response.read()
        response_headers = dict(response.getheaders())
        connection.close()
        return status, payload, response_headers

    def json_request(self, method, path, data=None, token=True):
        status, payload, headers = self.request(method, path, data, token)
        return status, json.loads(payload) if payload else None, headers

    def specs(self):
        return [
            dict(name='rain', path=str(self.raster), band=1,
                 semantics='intensive', value_units='mm'),
            dict(name='bad_band', path=str(self.raster), band=2,
                 semantics='intensive', value_units='mm'),
        ]

    def wait_job(self, job_id):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            status, job, _ = self.json_request('GET', '/api/job?id=' + job_id)
            self.assertEqual(status, 200, job)
            if job['status'] not in {'running', 'stopping'}:
                return job
            time.sleep(.01)
        self.fail('batch job did not finish in time')

    def wait_download(self, download_id):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            status, state, _ = self.json_request('GET', '/api/download/status?id=' + download_id)
            self.assertEqual(status, 200, state)
            if state['state'] in {'saved', 'cancelled', 'error'}:
                return state
            time.sleep(.01)
        self.fail('native batch download did not finish in time')

    def test_partial_batch_preview_streamed_formats_and_native_save(self):
        status, queued, _ = self.json_request('POST', '/api/batch/start',
            {'mesh_job_id': 'accepted-mesh', 'features': self.specs()})
        self.assertEqual(status, 200, queued)
        job = self.wait_job(queued['job_id'])
        self.assertEqual(job['status'], 'partial', job)

        status, preview, _ = self.json_request('POST', '/api/batch/preview',
            {'job_id': queued['job_id'], 'limit': 2})
        self.assertEqual(status, 200, preview)
        self.assertEqual(preview['total_cells'], 2)
        self.assertEqual([row['cell_id'] for row in preview['rows']], ['cell-a', 'cell-b'])
        self.assertEqual(preview['rows'][0]['values']['rain'], 15)
        self.assertIsNone(preview['rows'][0]['values']['bad_band'])
        self.assertEqual(preview['batch']['status'], 'partial')
        self.assertEqual([item['status'] for item in preview['batch']['features']],
                         ['complete', 'failed'])

        for file_format, expected_type in (
                ('wide_csv', 'application/zip'), ('long_csv', 'application/zip'),
                ('json', 'application/json')):
            status, body, headers = self.request('POST', '/api/batch/export',
                {'job_id': queued['job_id'], 'format': file_format})
            self.assertEqual(status, 200, body[:200])
            self.assertTrue(headers['Content-Type'].startswith(expected_type), headers)
            self.assertIn('attachment;', headers.get('Content-Disposition', ''))
            if file_format in {'wide_csv', 'long_csv'}:
                with zipfile.ZipFile(BytesIO(body)) as archive:
                    self.assertEqual(set(archive.namelist()), {'table.csv', 'metadata.json'})
                    table = archive.read('table.csv')
                    metadata = json.loads(archive.read('metadata.json'))
                    self.assertIn(b'cell-a', table)
                    self.assertIn(b'bad_band', table)
                    self.assertEqual(metadata['status'], 'partial')
                    self.assertEqual([feature['status'] for feature in metadata['features']],
                                     ['complete', 'failed'])
            else:
                exported = json.loads(body)
                self.assertEqual(exported['batch']['status'], 'partial')

        target = self.root / 'native-batch.zip'
        self.server.native_save = lambda _filename: (str(target),)
        status, queued_download, _ = self.json_request('POST', '/api/download/start',
            {'kind': 'batch', 'request': {'job_id': queued['job_id'], 'format': 'wide_csv'}})
        self.assertEqual(status, 200, queued_download)
        saved = self.wait_download(queued_download['download_id'])
        self.assertEqual(saved['state'], 'saved', saved)
        self.assertEqual(Path(saved['path']), target)
        self.assertTrue(zipfile.is_zipfile(target))
        with zipfile.ZipFile(target) as archive:
            self.assertIn(b'cell-a', archive.read('table.csv'))
            self.assertIn('metadata.json', archive.namelist())

    def test_auth_unknown_job_and_client_path_rejected(self):
        status, _, _ = self.request('POST', '/api/batch/start',
            {'mesh_job_id': 'accepted-mesh', 'features': self.specs()}, token=False)
        self.assertEqual(status, 403)

        status, error, _ = self.json_request('POST', '/api/batch/preview',
            {'job_id': 'not-a-job', 'limit': 1})
        self.assertEqual(status, 400, error)

        status, error, _ = self.json_request('POST', '/api/batch/export',
            {'job_id': 'not-a-job', 'format': 'wide_csv'})
        self.assertEqual(status, 400, error)

        status, error, _ = self.json_request('POST', '/api/batch/export',
            {'job_id': 'accepted-mesh', 'format': 'wide_csv',
             'path': str(self.root/'client-controlled.csv')})
        self.assertEqual(status, 400, error)
        self.assertFalse((self.root/'client-controlled.csv').exists())

    def test_batch_request_lookup_is_authenticated_and_operation_scoped(self):
        request_id = 'batch_lookup_123'
        status, queued, _ = self.json_request('POST', '/api/batch/start',
            {'mesh_job_id': 'accepted-mesh', 'features': self.specs(),
             'request_id': request_id})
        self.assertEqual(status, 200, queued)
        self.wait_job(queued['job_id'])

        # A separate main-run record can share a request ID in a recovered
        # session; batch lookup must still identify only batch extraction.
        self.server.jobs['unrelated-main'] = dict(status='pass', stage='done',
            operation='mesh_only', request_kind='run', request_id=request_id,
            result={'status':'pass'})
        status, found, _ = self.json_request('GET',
            '/api/batch/status?request_id='+request_id)
        self.assertEqual(status, 200, found)
        self.assertEqual(found, dict(job_id=queued['job_id'], operation='batch_extract',
                                     request_id=request_id, status='partial'))

        status, missing, _ = self.json_request('GET',
            '/api/batch/status?request_id=unknown_lookup_123')
        self.assertEqual(status, 200, missing)
        self.assertEqual(missing, {'job_id': None})

        status, error, _ = self.json_request('GET',
            '/api/batch/status?request_id=short')
        self.assertEqual(status, 400, error)
        status, _, _ = self.request('GET',
            '/api/batch/status?request_id='+request_id, token=False)
        self.assertEqual(status, 403)


if __name__ == '__main__':
    unittest.main()
