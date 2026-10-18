"""Session-owned job and permission integration for multiple raster extraction."""
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
import zipfile
from unittest.mock import patch

from affine import Affine
import numpy as np
import rasterio

import gis_mesh_workbench.batch_api as batch_api_module
from gis_mesh_workbench.batch_api import batch_export_file, batch_preview, start_batch
import gis_mesh_workbench.batch_extraction as extraction_module
from gis_mesh_workbench.extraction import Cell, _digest
from gis_mesh_workbench.job_control import Cancelled, checkpoint
from gis_mesh_workbench.mesh_workflow import geometry_digest
from gis_mesh_workbench.webui import WorkbenchServer


class BatchAPITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.server = WorkbenchServer(self.root)
        self.addCleanup(self.server.server_close)
        self.crs = 'EPSG:32649'
        self.cells = [
            Cell('cell-a', 'rectangle', ((500000, 1000000), (500002, 1000000),
                                         (500002, 1000001), (500000, 1000001))),
            Cell('cell-b', 'rectangle', ((500002, 1000000), (500004, 1000000),
                                         (500004, 1000001), (500002, 1000001))),
        ]
        output = self.server.output_root / 'accepted-mesh'
        output.mkdir()
        self.cells_path = output / 'cells.json'
        self.cells_path.write_text(json.dumps(dict(schema_version='workbench-cells-v1',
            coordinate_units='m', crs_wkt=self.crs,
            cells=[dict(cell_id=c.cell_id, kind=c.kind, xy=c.xy) for c in self.cells])))
        self.mesh_manifest_path = output / 'manifest.json'
        self.mesh_manifest_path.write_text(json.dumps(dict(status='pass', operation='mesh_only',
            geometry_digest=geometry_digest(self.cells, self.crs),
            artifact_hashes={'cells.json': _digest(self.cells_path)})))
        self.server.jobs['accepted-mesh'] = dict(status='pass', stage='done', operation='mesh_only',
            result=dict(status='pass', output=str(output)))
        self.raster = self.root / 'source.tif'
        self.write_raster(self.raster)

    def write_raster(self, path):
        with rasterio.open(path, 'w', driver='GTiff', width=4, height=1,
                           count=1, dtype='float64', crs=self.crs,
                           transform=Affine(1, 0, 500000, 0, -1, 1000001)) as dst:
            dst.write(np.array([[10, 20, 30, 40]], dtype='float64'), 1)

    def specs(self):
        return [dict(name='rain', path=str(self.raster), band=1,
                     semantics='intensive', value_units='mm')]

    def wait_job(self, job_id, timeout=8):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            job = self.server.job(job_id)
            if job['status'] not in {'running', 'stopping'}:
                return job
            time.sleep(.01)
        self.fail('batch job did not finish in time')

    def test_start_joined_preview_and_streamed_export_file(self):
        started = start_batch(self.server, dict(mesh_job_id='accepted-mesh', features=self.specs()))
        self.assertEqual(started['status'], 'running')
        job = self.wait_job(started['job_id'])
        self.assertEqual(job['status'], 'pass', job)
        page = batch_preview(self.server, dict(job_id=started['job_id'], limit=1))
        self.assertEqual(page['batch']['geometry_digest'], geometry_digest(self.cells, self.crs))
        self.assertEqual(page['rows'][0]['cell_id'], 'cell-a')
        self.assertEqual(page['rows'][0]['values']['rain'], 15)
        artifact = batch_export_file(self.server, dict(job_id=started['job_id'], format='wide_csv'))
        self.assertEqual(artifact['mime'], 'application/zip')
        self.assertTrue(Path(artifact['path']).is_file())
        with zipfile.ZipFile(artifact['path']) as archive:
            self.assertEqual(set(archive.namelist()), {'table.csv', 'metadata.json'})
            self.assertIn('cell-a', archive.read('table.csv').decode('utf-8-sig'))
            metadata = json.loads(archive.read('metadata.json'))
        self.assertEqual(metadata['features'][0]['status'], 'complete')
        self.assertEqual(metadata['features'][0]['value_units'], 'mm')

    def test_request_idempotency_and_conflicting_payload(self):
        payload = dict(mesh_job_id='accepted-mesh', features=self.specs(), request_id='batch_req_123')
        first = start_batch(self.server, payload)
        second = start_batch(self.server, payload)
        self.assertEqual(first['request_id'],payload['request_id'])
        self.assertEqual(second['request_id'],payload['request_id'])
        self.assertEqual(first['job_id'], second['job_id'])
        self.wait_job(first['job_id'])
        self.raster.unlink()
        recovered = start_batch(self.server, payload)
        self.assertEqual(recovered['request_id'],payload['request_id'])
        self.assertEqual(recovered['job_id'], first['job_id'])
        different = {**payload, 'features': [dict(self.specs()[0], name='other')]}
        with self.assertRaisesRegex(ValueError, 'different batch'):
            start_batch(self.server, different)

    def test_concurrent_duplicate_start_returns_identity_from_both_paths(self):
        from concurrent.futures import ThreadPoolExecutor
        from gis_mesh_workbench import batch_api
        original=batch_api._validate_data
        barrier=threading.Barrier(2)
        payload=dict(mesh_job_id='accepted-mesh',features=self.specs(),request_id='concurrent_batch_123')
        def validate_together(server,data):
            prepared=original(server,data)
            barrier.wait(timeout=5)
            return prepared
        with patch.object(batch_api,'_validate_data',side_effect=validate_together):
            with ThreadPoolExecutor(max_workers=2) as pool:
                responses=list(pool.map(lambda _:start_batch(self.server,payload),range(2)))
        self.assertEqual(responses[0]['job_id'],responses[1]['job_id'])
        self.assertTrue(all(result['request_id']==payload['request_id'] for result in responses))
        self.assertEqual(sum(job.get('operation')=='batch_extract' for job in self.server.jobs.values()),1)
        self.wait_job(responses[0]['job_id'])

    def test_unselected_external_raster_and_tampered_mesh_are_rejected(self):
        with tempfile.TemporaryDirectory() as outside:
            path = Path(outside) / 'outside.tif'
            self.write_raster(path)
            spec = [dict(name='outside', path=str(path), band=1,
                         semantics='intensive', value_units='mm')]
            with self.assertRaisesRegex(ValueError, 'Path'):
                start_batch(self.server, dict(mesh_job_id='accepted-mesh', features=spec))
            self.server.selected_files.add(path.resolve())
            selected = start_batch(self.server, dict(mesh_job_id='accepted-mesh', features=spec))
            self.assertIn(path.resolve(), self.server.protected_inputs)
            self.assertEqual(self.wait_job(selected['job_id'])['status'], 'pass')
        self.cells_path.write_text(self.cells_path.read_text() + ' ')
        with self.assertRaisesRegex(ValueError, 'artifact hash|Tệp lưới đã thay đổi'):
            start_batch(self.server, dict(mesh_job_id='accepted-mesh', features=self.specs()))

    def test_stop_cancels_active_batch_and_retains_batch_database(self):
        started = threading.Event()
        def slow(*args, **kwargs):
            started.set()
            while True:
                checkpoint()
                time.sleep(.005)
        with patch('gis_mesh_workbench.batch_extraction.extract_indexed', side_effect=slow):
            queued = start_batch(self.server, dict(mesh_job_id='accepted-mesh', features=self.specs()))
            self.assertTrue(started.wait(3))
            stopped = self.server.stop_job({'job_id': queued['job_id']})
            self.assertTrue(stopped['accepted'])
            job = self.wait_job(queued['job_id'])
        self.assertEqual(job['status'], 'cancelled')
        page = batch_preview(self.server, dict(job_id=queued['job_id'], limit=1))
        self.assertEqual(page['batch']['status'], 'cancelled')

    def test_bad_feature_is_partial_but_job_finishes(self):
        bad = self.root / 'bad.tif'
        bad.write_text('not a raster')
        features = [dict(name='bad', path=str(bad), band=1,
                          semantics='intensive', value_units='mm'), *self.specs()]
        started = start_batch(self.server, dict(mesh_job_id='accepted-mesh', features=features))
        job = self.wait_job(started['job_id'])
        self.assertEqual(job['status'], 'partial', job)
        page = batch_preview(self.server, dict(job_id=started['job_id'], limit=1))
        self.assertEqual(page['rows'][0]['values']['bad'], None)
        self.assertEqual(page['rows'][0]['values']['rain'], 15)
        artifact = batch_export_file(self.server, dict(job_id=started['job_id'], format='long_csv'))
        with zipfile.ZipFile(artifact['path']) as archive:
            metadata = json.loads(archive.read('metadata.json'))
            self.assertEqual([item['status'] for item in metadata['features']], ['failed', 'complete'])

    def test_mesh_change_marks_batch_failed_and_blocks_preview_and_export(self):
        original = extraction_module.extract_indexed

        def extract_then_tamper(*args, **kwargs):
            result = original(*args, **kwargs)
            self.cells_path.write_text(self.cells_path.read_text() + ' ')
            return result

        with patch.object(extraction_module, 'extract_indexed', side_effect=extract_then_tamper):
            started = start_batch(self.server, dict(mesh_job_id='accepted-mesh', features=self.specs()))
            job = self.wait_job(started['job_id'])
        self.assertEqual(job['status'], 'fail')
        self.assertEqual(extraction_module.inspect_batch(job['result']['batch_database'])['status'],
                         'complete')
        with self.assertRaisesRegex(ValueError, 'failed'):
            batch_preview(self.server, dict(job_id=started['job_id'], limit=1))
        with self.assertRaisesRegex(ValueError, 'failed'):
            batch_export_file(self.server, dict(job_id=started['job_id'], format='wide_csv'))

    def test_cancellation_does_not_hide_accepted_mesh_tampering(self):
        entered = threading.Event()

        def tamper_then_wait(*_args, **_kwargs):
            self.cells_path.write_text(self.cells_path.read_text() + ' ')
            entered.set()
            while True:
                checkpoint()
                time.sleep(.005)

        with patch.object(extraction_module, 'extract_indexed', side_effect=tamper_then_wait):
            started = start_batch(self.server, dict(mesh_job_id='accepted-mesh', features=self.specs()))
            self.assertTrue(entered.wait(3))
            self.server.stop_job({'job_id': started['job_id']})
            job = self.wait_job(started['job_id'])
        self.assertEqual(job['status'], 'fail')
        with self.assertRaisesRegex(ValueError, 'failed'):
            batch_preview(self.server, dict(job_id=started['job_id'], limit=1))

    def test_failed_or_cancelled_export_removes_partial_destination(self):
        started = start_batch(self.server, dict(mesh_job_id='accepted-mesh', features=self.specs()))
        self.assertEqual(self.wait_job(started['job_id'])['status'], 'pass')
        destinations = []

        def partial_export(_database, destination):
            destination.write_text('partial output', encoding='utf-8')
            destinations.append(destination)
            raise Cancelled('cancelled during export')

        with patch.dict(batch_api_module._FORMATS,
                        {'wide_csv': (partial_export, 'text/csv', 'partial.csv')}):
            with self.assertRaises(Cancelled):
                batch_export_file(self.server, dict(job_id=started['job_id'], format='wide_csv'))
        self.assertEqual(len(destinations), 1)
        self.assertFalse(destinations[0].exists())


if __name__ == '__main__':
    unittest.main()
