import csv
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from affine import Affine
import numpy as np
import rasterio

from gis_mesh_workbench.extraction import Cell
from gis_mesh_workbench.mesh_workflow import geometry_digest
from gis_mesh_workbench.job_control import Cancelled, JobControl, job_scope
from gis_mesh_workbench.batch_extraction import (
    export_json, export_long_csv, export_wide_csv, inspect_batch, preview_batch, run_batch,
)
import gis_mesh_workbench.batch_extraction as batch_module


class BatchExtractionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.crs = 'EPSG:32649'
        self.cells = [
            Cell('cell-1', 'rectangle', ((500000, 1000000), (500002, 1000000),
                                         (500002, 1000001), (500000, 1000001))),
            Cell('cell-2', 'rectangle', ((500002, 1000000), (500004, 1000000),
                                         (500004, 1000001), (500002, 1000001))),
        ]
        self.raster = self.root / 'values.tif'
        with rasterio.open(self.raster, 'w', driver='GTiff', width=4, height=1,
                           count=1, dtype='float64', crs=self.crs,
                           transform=Affine(1, 0, 500000, 0, -1, 1000001)) as dst:
            dst.write(np.array([[10, 20, 30, 40]], dtype='float64'), 1)
        self.db = self.root / 'joined.sqlite'
        self.digest = geometry_digest(self.cells, self.crs)

    def spec(self, name='rain', path=None, semantics='intensive'):
        return dict(name=name, path=str(path or self.raster), band=1,
                    semantics=semantics, value_units='mm')

    def run_batch_call(self, specs):
        return run_batch(self.db, self.cells, specs, cell_crs=self.crs,
                         analysis_crs=self.crs,
                         accepted_geometry_digest=self.digest)

    def test_sequential_features_join_by_stable_cell_id_and_page(self):
        result = self.run_batch_call([self.spec('rain'), self.spec('rain_total', semantics='extensive')])
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(result['geometry_digest'], self.digest)
        page = preview_batch(self.db, offset=1, limit=1)
        self.assertEqual(page['total_cells'], 2)
        self.assertEqual([row['cell_id'] for row in page['rows']], ['cell-2'])
        self.assertEqual(page['rows'][0]['values']['rain'], 35)
        self.assertEqual(page['rows'][0]['values']['rain_total'], 70)

    def test_bad_file_is_explicit_partial_and_later_files_still_run(self):
        result = self.run_batch_call([self.spec('missing', self.root / 'absent.tif'), self.spec('good')])
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['features'][0]['status'], 'failed')
        self.assertEqual(result['features'][1]['status'], 'complete')
        page = preview_batch(self.db, limit=10)
        self.assertEqual(page['rows'][0]['values']['missing'], None)
        self.assertEqual(page['rows'][0]['values']['good'], 15)

    def test_bad_band_is_per_feature_and_later_file_runs(self):
        bad = self.spec('bad_band')
        bad['band'] = 2
        result = self.run_batch_call([bad, self.spec('good')])
        self.assertEqual([f['status'] for f in result['features']], ['failed', 'complete'])
        self.assertIn('band', result['features'][0]['error'])

    def test_all_nodata_is_explicit_per_feature(self):
        empty = self.root / 'nodata.tif'
        with rasterio.open(empty, 'w', driver='GTiff', width=4, height=1,
                           count=1, dtype='float64', crs=self.crs,
                           transform=Affine(1, 0, 500000, 0, -1, 1000001),
                           nodata=-9999) as dst:
            dst.write(np.full((1, 4), -9999, dtype='float64'), 1)
        result = self.run_batch_call([self.spec('empty', empty)])
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['features'][0]['status'], 'no_data')
        self.assertEqual(result['features'][0]['quality']['no_data_cells'], 2)

    def test_three_hundred_features_and_bounded_preview(self):
        specs = [self.spec(f'feature_{i:03d}') for i in range(300)]
        result = self.run_batch_call(specs)
        self.assertEqual(result['feature_count'], 300)
        selected = [f'feature_{i:03d}' for i in range(20)]
        page = preview_batch(self.db, limit=200, feature_names=selected)
        self.assertEqual(len(page['rows']), 2)
        self.assertEqual(len(page['rows'][0]['values']), 20)
        self.assertEqual(page['rows'][1]['cell_id'], 'cell-2')
        self.assertEqual(page['rows'][1]['values']['feature_019'], 35)
        default_page = preview_batch(self.db, limit=1)
        self.assertEqual(len(default_page['feature_names']), 20)
        self.assertEqual(len(default_page['rows'][0]['values']), 20)

    def test_duplicate_cell_ids_are_rejected(self):
        dup = [self.cells[0], Cell(self.cells[0].cell_id, 'rectangle', self.cells[1].xy)]
        with self.assertRaisesRegex(ValueError, 'unique IDs'):
            run_batch(self.db, dup, [self.spec()], cell_crs=self.crs, analysis_crs=self.crs,
                      accepted_geometry_digest=geometry_digest(dup, self.crs))

    def test_candidate_pair_budget_is_applied_per_remaining_batch_budget(self):
        result = run_batch(self.db, self.cells, [self.spec('first'), self.spec('second')],
                           cell_crs=self.crs, analysis_crs=self.crs,
                           accepted_geometry_digest=self.digest,
                           max_aggregate_candidate_pairs=3)
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['aggregate_candidate_pairs'], 0)
        self.assertEqual([f['status'] for f in result['features']], ['failed', 'failed'])

    def test_small_database_cap_keeps_prior_completed_feature_and_finishes_partial(self):
        def large_result(path, cells, **kwargs):
            records = [dict(cell_id=cell.cell_id, value=1.0, valid_area_m2=1.0,
                            detail='x' * 20_000) for cell in cells]
            return dict(cells=records, provenance={},
                        qa=dict(engine_evidence=dict(candidate_pairs=1)))
        with patch.object(batch_module, 'MAX_DATABASE_BYTES', 96 * 1024), \
                patch.object(batch_module, 'extract_indexed', side_effect=large_result):
            result = run_batch(self.db, self.cells,
                [self.spec('first'), self.spec('second'), self.spec('third')],
                cell_crs=self.crs, analysis_crs=self.crs,
                accepted_geometry_digest=self.digest)
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['features'][0]['status'], 'complete')
        self.assertTrue(all(f['status'] in {'complete', 'failed'} for f in result['features']))
        self.assertLessEqual(self.db.stat().st_size, 96 * 1024)

    def test_source_digest_and_names_are_validated(self):
        with self.assertRaisesRegex(ValueError, 'geometry digest'):
            run_batch(self.db, self.cells, [self.spec()], cell_crs=self.crs,
                      analysis_crs=self.crs, accepted_geometry_digest='wrong')
        with self.assertRaisesRegex(ValueError, 'unique'):
            self.run_batch_call([self.spec('same'), self.spec('same')])
        with self.assertRaisesRegex(ValueError, 'reserved'):
            self.run_batch_call([self.spec('CELL_ID')])
        with self.assertRaisesRegex(ValueError, 'unique'):
            self.run_batch_call([self.spec('Rain'), self.spec('rain')])

    def test_streaming_exports_and_formula_safe_csv(self):
        self.run_batch_call([self.spec('=SUM(A1:A2)')])
        wide = self.root / 'wide.csv'
        long = self.root / 'long.csv'
        payload = self.root / 'joined.json'
        export_wide_csv(self.db, wide)
        export_long_csv(self.db, long)
        export_json(self.db, payload)
        with wide.open(encoding='utf-8-sig', newline='') as stream:
            headers = next(csv.reader(stream))
        self.assertTrue(headers[-1].startswith("'=SUM"))
        self.assertEqual(json.loads(payload.read_text(encoding='utf-8'))['batch']['geometry_digest'], self.digest)
        self.assertIn('cell-1', long.read_text(encoding='utf-8-sig'))

    def test_wide_and_json_exports_use_one_join_for_many_features_and_cells(self):
        cells = [Cell(f'cell-{i:03d}', 'rectangle',
                      ((500000 + i, 1000000), (500001 + i, 1000000),
                       (500001 + i, 1000001), (500000 + i, 1000001)))
                 for i in range(25)]
        specs = [self.spec(f'feature_{i:03d}') for i in range(100)]

        def indexed(_path, input_cells, **_kwargs):
            return dict(cells=[dict(cell_id=cell.cell_id, value=7.0, valid_area_m2=1.0)
                               for cell in input_cells], provenance={},
                        qa=dict(engine_evidence=dict(candidate_pairs=1)))

        digest = geometry_digest(cells, self.crs)
        with patch.object(batch_module, 'extract_indexed', side_effect=indexed):
            result = run_batch(self.db, cells, specs, cell_crs=self.crs,
                               analysis_crs=self.crs, accepted_geometry_digest=digest)
        self.assertEqual(result['status'], 'complete')

        original_load = batch_module._load
        traces = []

        def traced_load(path):
            db = original_load(path)
            db.set_trace_callback(traces.append)
            return db

        wide = self.root / 'many.csv'
        with patch.object(batch_module, '_load', side_effect=traced_load):
            export_wide_csv(self.db, wide)
        wide_queries = [sql for sql in traces if 'feature_cells' in sql.lower()]
        self.assertEqual(len(wide_queries), 1)
        with wide.open(encoding='utf-8-sig', newline='') as stream:
            rows = list(csv.reader(stream))
        self.assertEqual(len(rows), 26)
        self.assertEqual(len(rows[0]), 105)
        self.assertEqual(rows[1][5:8], ['7.0', '7.0', '7.0'])

        traces.clear()
        payload = self.root / 'many.json'
        with patch.object(batch_module, '_load', side_effect=traced_load):
            export_json(self.db, payload)
        json_queries = [sql for sql in traces if 'feature_cells' in sql.lower()]
        self.assertEqual(len(json_queries), 1)
        exported = json.loads(payload.read_text(encoding='utf-8'))
        self.assertEqual(len(exported['cells']), 25)
        self.assertEqual(exported['cells'][0]['values']['feature_099']['value'], 7.0)

    def test_cancel_keeps_completed_feature_and_marks_current_status(self):
        control = JobControl()
        def progress(event):
            if event['stage'] == 'feature_complete':
                control.cancel.set()
        with job_scope(control):
            with self.assertRaises(Cancelled):
                run_batch(self.db, self.cells, [self.spec('first'), self.spec('second')],
                          cell_crs=self.crs, analysis_crs=self.crs,
                          accepted_geometry_digest=self.digest, progress=progress)
        state = inspect_batch(self.db)
        self.assertEqual(state['status'], 'cancelled')
        self.assertEqual([item['status'] for item in state['features']], ['complete', 'cancelled'])


if __name__ == '__main__':
    unittest.main()
