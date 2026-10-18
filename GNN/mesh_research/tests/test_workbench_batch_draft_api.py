import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from affine import Affine
import numpy as np
import rasterio

from gis_mesh_workbench.batch_api import batch_preview, draft_preview, start_batch
from gis_mesh_workbench.extraction import Cell, _digest
from gis_mesh_workbench.job_control import checkpoint
from gis_mesh_workbench.mesh_workflow import geometry_digest
from gis_mesh_workbench.webui import WorkbenchServer


class BatchDraftAPITests(unittest.TestCase):
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
        self.manifest = output / 'manifest.json'
        self.manifest.write_text(json.dumps(dict(status='pass', operation='mesh_only',
            geometry_digest=geometry_digest(self.cells, self.crs),
            artifact_hashes={'cells.json': _digest(self.cells_path)})))
        self.server.jobs['accepted-mesh'] = dict(status='pass', stage='done', operation='mesh_only',
            result=dict(status='pass', output=str(output)))
        self.raster = self.root / 'values.tif'
        with rasterio.open(self.raster, 'w', driver='GTiff', width=4, height=1,
                           count=1, dtype='float64', crs=self.crs,
                           transform=Affine(1, 0, 500000, 0, -1, 1000001)) as dst:
            dst.write(np.array([[10, 20, 30, 40]], dtype='float64'), 1)

    def features(self):
        return [dict(name='rain', path=str(self.raster), band=1,
                     semantics='intensive', value_units='mm')]

    def payload(self):
        return dict(mesh_job_id='accepted-mesh', features=self.features(), draft=dict(
            mesh_job_id='accepted-mesh', geometry_digest=geometry_digest(self.cells, self.crs),
            columns=[dict(id='rain_col', type='raster', source_name='rain', name='Rainfall'),
                     dict(id='comment', type='manual', name='Comment', value_type='text')],
            rows=[dict(cell_id='cell-b', values={'comment': 'reviewed'})],
            excluded_cell_ids=['cell-a'],
            manual_rows=[dict(row_id='station-x', values={'comment': 'standalone'})]))

    def wait(self, job_id):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            job = self.server.job(job_id)
            if job['status'] not in {'running', 'stopping'}:
                return job
            time.sleep(.01)
        self.fail('batch draft job did not finish')

    def test_preview_does_not_extract_then_confirm_freezes_subset_and_annotations(self):
        payload = self.payload()
        with patch('gis_mesh_workbench.batch_extraction.extract_indexed',
                   side_effect=AssertionError('preview must not read raster values')):
            preview = draft_preview(self.server, payload)
        self.assertEqual(preview['selected_cells'], 1)
        self.assertEqual(preview['rows'][0]['values']['rain_col'], {'state': 'not_computed'})
        self.assertFalse(preview['rows'][0]['included'])
        self.assertEqual(preview['rows'][0]['area_m2'], 2.0)
        self.assertEqual(preview['rows'][0]['centroid_x'], 500001.0)
        self.assertEqual(preview['rows'][1]['values']['comment'], 'reviewed')
        self.assertEqual(preview['rows'][2]['row_kind'], 'manual')
        started = start_batch(self.server, {**payload, 'draft': preview['draft'],
                                            'plan_hash': preview['plan_hash']})
        job = self.wait(started['job_id'])
        self.assertEqual(job['status'], 'pass', job)
        batch = job['result']['batch']
        self.assertEqual(batch['cell_count'], 1)
        self.assertEqual(batch['geometry_digest'], geometry_digest([self.cells[1]], self.crs))
        self.assertEqual(batch['draft']['parent_geometry_digest'], geometry_digest(self.cells, self.crs))
        self.assertEqual(batch['draft']['overrides']['cell-b']['comment'], 'reviewed')
        self.assertEqual(batch['draft']['manual_rows'][0]['row_id'], 'station-x')
        result_page = batch_preview(self.server, dict(job_id=started['job_id'], limit=1))
        self.assertEqual(result_page['rows'][0]['row_kind'], 'cell')
        manual_page = batch_preview(self.server, dict(job_id=started['job_id'], offset=1, limit=1))
        self.assertEqual(manual_page['rows'][0]['row_kind'], 'manual')
        self.assertEqual(manual_page['rows'][0]['row_id'], 'station-x')

    def test_manual_only_draft_removes_all_raster_work_and_exports_rows(self):
        payload = self.payload()
        payload['draft']['columns'] = [dict(id='comment', type='manual',
                                            name='Comment', value_type='text')]
        payload['draft']['rows'] = [dict(cell_id='cell-b', values={'comment': 'reviewed'})]
        preview = draft_preview(self.server, payload)
        self.assertEqual(preview['selected_cells'], 1)
        self.assertEqual(preview['columns'], [payload['draft']['columns'][0]])
        started = start_batch(self.server, {**payload, 'draft': preview['draft'],
                                            'plan_hash': preview['plan_hash']})
        job = self.wait(started['job_id'])
        self.assertEqual(job['status'], 'pass', job)
        self.assertEqual(job['result']['batch']['feature_count'], 0)
        from gis_mesh_workbench.batch_api import batch_export_file
        export = batch_export_file(self.server, dict(job_id=started['job_id'], format='wide_csv'))
        self.assertTrue(Path(export['path']).is_file())

    def test_claimed_plan_hash_is_checked_after_normalization(self):
        payload = self.payload()
        preview = draft_preview(self.server, payload)
        with self.assertRaisesRegex(ValueError, 'plan hash'):
            start_batch(self.server, {**payload, 'plan_hash': '0' * 64})

    def test_result_values_keep_stable_ids_and_distinguish_nodata_from_failure(self):
        empty = self.root / 'empty.tif'
        with rasterio.open(empty, 'w', driver='GTiff', width=4, height=1,
                           count=1, dtype='float64', crs=self.crs,
                           transform=Affine(1, 0, 500000, 0, -1, 1000001),
                           nodata=-9999) as dst:
            dst.write(np.full((1, 4), -9999, dtype='float64'), 1)
        broken = self.root / 'broken.tif'
        broken.write_text('not a raster')
        payload = self.payload()
        payload['features'] = [
            dict(name='empty', path=str(empty), band=1,
                 semantics='intensive', value_units='mm'),
            dict(name='broken', path=str(broken), band=1,
                 semantics='intensive', value_units='mm')]
        payload['draft']['columns'] = [
            dict(id='bad_col', type='raster', source_name='broken', name='Broken'),
            dict(id='empty_col', type='raster', source_name='empty', name='No data'),
            dict(id='comment', type='manual', name='Comment', value_type='text')]
        preview = draft_preview(self.server, payload)
        started = start_batch(self.server, {**payload, 'draft': preview['draft'],
                                            'plan_hash': preview['plan_hash']})
        self.wait(started['job_id'])
        result = batch_preview(self.server, dict(job_id=started['job_id'], limit=1,
            visible_columns=['bad_col', 'empty_col', 'comment']))
        values = result['rows'][0]['values']
        self.assertEqual(values['bad_col']['state'], 'failed')
        self.assertEqual(values['empty_col'],
                         {'state': 'no_data', 'value': None, 'status': 'no_data'})
        self.assertEqual(values['comment'], 'reviewed')

    def test_cell_id_search_excludes_unrelated_manual_rows(self):
        payload = self.payload()
        payload['draft']['excluded_cell_ids'] = []
        payload['draft']['manual_rows'].append(dict(row_id='station-y', values={'comment':'other'}))
        preview = draft_preview(self.server, payload)
        started = start_batch(self.server, {**payload, 'draft':preview['draft'],
                                            'plan_hash':preview['plan_hash']})
        self.assertEqual(self.wait(started['job_id'])['status'], 'pass')
        page = batch_preview(self.server, dict(job_id=started['job_id'], limit=10))
        self.assertEqual(page['total_rows'], 4)
        self.assertEqual(sum(row['row_kind']=='manual' for row in page['rows']), 2)
        for mode in ('exact', 'substring'):
            result = batch_preview(self.server, dict(job_id=started['job_id'], limit=10,
                cell_id='nonexistent', cell_id_mode=mode))
            self.assertEqual(result['total_rows'], 0)
            self.assertEqual(result['rows'], [])
        result = batch_preview(self.server, dict(job_id=started['job_id'], limit=10,
            cell_id='cell-b', cell_id_mode='exact'))
        self.assertEqual(result['total_rows'], 1)
        self.assertEqual(result['rows'][0]['cell_id'], 'cell-b')

    def test_stop_keeps_committed_draft_metadata_and_completed_feature(self):
        payload = self.payload()
        payload['features'] = [self.features()[0],
            dict(name='rain_later', path=str(self.raster), band=1,
                 semantics='intensive', value_units='mm')]
        payload['draft']['columns'] = [
            dict(id='rain_col', type='raster', source_name='rain', name='Rainfall'),
            dict(id='later_col', type='raster', source_name='rain_later', name='Later'),
            dict(id='comment', type='manual', name='Comment', value_type='text')]
        preview = draft_preview(self.server, payload)
        started_second = threading.Event()
        calls = 0

        def controlled_extract(*_args, **_kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                return dict(cells=[dict(cell_id='cell-b', value=15.0, valid_area_m2=2.0)],
                    provenance={}, qa=dict(engine_evidence=dict(candidate_pairs=1)))
            started_second.set()
            while True:
                checkpoint()
                time.sleep(.005)

        with patch('gis_mesh_workbench.batch_extraction.extract_indexed',
                   side_effect=controlled_extract):
            queued = start_batch(self.server, {**payload, 'draft': preview['draft'],
                                               'plan_hash': preview['plan_hash']})
            self.assertTrue(started_second.wait(3))
            self.server.stop_job({'job_id': queued['job_id']})
            job = self.wait(queued['job_id'])
        self.assertEqual(job['status'], 'cancelled')
        batch = job['result']['batch']
        self.assertEqual([feature['status'] for feature in batch['features']],
                         ['complete', 'cancelled'])
        self.assertEqual(batch['draft']['overrides']['cell-b']['comment'], 'reviewed')
        page = batch_preview(self.server, dict(job_id=queued['job_id'], limit=1,
            visible_columns=['rain_col', 'later_col', 'comment']))
        values = page['rows'][0]['values']
        self.assertEqual(values['rain_col']['state'], 'computed')
        self.assertEqual(values['later_col']['state'], 'cancelled')


if __name__ == '__main__':
    unittest.main()
