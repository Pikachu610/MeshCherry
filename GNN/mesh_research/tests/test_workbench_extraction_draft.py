import csv
import json
from pathlib import Path
import tempfile
import unittest

from gis_mesh_workbench.extraction import Cell
from gis_mesh_workbench.mesh_workflow import geometry_digest
from gis_mesh_workbench.extraction_draft import (
    canonical_wire_state, freeze_draft, prepare_draft, preview_draft,
)
from gis_mesh_workbench.batch_extraction import (
    export_json, export_long_csv, export_wide_csv, run_batch,
)


class ExtractionDraftTests(unittest.TestCase):
    def setUp(self):
        self.cells = [
            Cell('cell-a', 'rectangle', ((500000, 1000000), (500002, 1000000),
                                        (500002, 1000001), (500000, 1000001))),
            Cell('cell-b', 'rectangle', ((500002, 1000000), (500004, 1000000),
                                        (500004, 1000001), (500002, 1000001))),
        ]
        self.crs = 'EPSG:32649'
        self.digest = geometry_digest(self.cells, self.crs)
        self.features = [dict(name='rain', path='rain.tif', band=1,
                              semantics='intensive', value_units='mm'),
                         dict(name='elevation', path='elevation.tif', band=1,
                              semantics='intensive', value_units='m')]
        self.draft = dict(mesh_job_id='mesh-1', geometry_digest=self.digest,
            columns=[dict(id='note', type='manual', name='Review note', value_type='text'),
                     dict(id='r1', type='raster', source_name='rain', name='Rainfall'),
                     dict(id='score', type='manual', name='Score', value_type='number')],
            rows=[dict(cell_id='cell-b', values={'note': '=SUM(A1:A2)', 'score': 2.5})],
            excluded_cell_ids=['cell-a'],
            manual_rows=[dict(row_id='survey-1', values={'note': 'field only', 'score': 4})])

    def test_user_names_cannot_shadow_generated_csv_state_columns(self):
        for name in ('r1__state', 'R1__STATUS'):
            with self.subTest(name=name):
                draft = json.loads(json.dumps(self.draft))
                draft['columns'][0]['name'] = name
                with self.assertRaisesRegex(ValueError, 'generated raster'):
                    prepare_draft('mesh-1', self.digest, self.cells, self.features, draft)

    def test_preview_is_bounded_and_distinguishes_pending_from_null(self):
        prepared = prepare_draft('mesh-1', self.digest, self.cells, self.features, self.draft)
        page = preview_draft(prepared, limit=1)
        self.assertEqual(page['total_rows'], 3)
        self.assertEqual(page['total_cells'], 1)
        self.assertEqual(page['accepted_cells'], 2)
        self.assertFalse(page['rows'][0]['included'])
        self.assertEqual(page['rows'][0]['values']['r1'], {'state': 'not_computed'})
        self.assertIsNone(page['rows'][0]['values']['note'])
        selected_page = preview_draft(prepared, offset=1, limit=1)
        self.assertTrue(selected_page['rows'][0]['included'])
        self.assertEqual(selected_page['rows'][0]['values']['note'], '=SUM(A1:A2)')
        manual = preview_draft(prepared, offset=2, limit=1)['rows'][0]
        self.assertEqual(manual['row_kind'], 'manual')
        self.assertNotIn('cell_id', manual)
        self.assertEqual(len(preview_draft(prepared, visible_columns=['note'])['columns']), 1)
        with self.assertRaisesRegex(ValueError, '20'):
            preview_draft(prepared, visible_columns=['r1'] * 21)

    def test_large_preview_constructs_values_for_page_only(self):
        class TrackedCell:
            def __init__(self, index):
                self._cell_id = f'cell-{index:04d}'
                self.kind = 'rectangle'
                self.xy = ((index, 0), (index + 1, 0), (index + 1, 1), (index, 1))
                self.reads = 0

            @property
            def cell_id(self):
                self.reads += 1
                return self._cell_id

        cells = [TrackedCell(index) for index in range(5000)]
        draft = dict(mesh_job_id='mesh-1', geometry_digest=self.digest,
            columns=[dict(id='note', type='manual', name='Note', value_type='text')],
            rows=[], excluded_cell_ids=[], manual_rows=[])
        prepared = prepare_draft('mesh-1', self.digest, cells, [], draft)
        for cell in cells:
            cell.reads = 0
        page = preview_draft(prepared, offset=4999, limit=1)
        self.assertEqual(page['rows'][0]['cell_id'], 'cell-4999')
        self.assertLessEqual(sum(cell.reads for cell in cells), 3)

    def test_freeze_preserves_only_accepted_cells_and_geometry(self):
        prepared = prepare_draft('mesh-1', self.digest, self.cells, self.features, self.draft)
        frozen = freeze_draft(prepared, mesh_job_id='mesh-1',
                              geometry_digest=self.digest, cells=self.cells)
        self.assertEqual(frozen['cell_ids'], ('cell-b',))
        self.assertEqual(frozen['features'][0]['name'], 'Rainfall')
        self.assertEqual(len(frozen['features']), 1)
        self.assertEqual(frozen['parent_geometry_digest'], self.digest)
        changed = [self.cells[0], Cell('cell-b', 'rectangle', self.cells[0].xy)]
        with self.assertRaisesRegex(ValueError, 'changed'):
            freeze_draft(prepared, mesh_job_id='mesh-1', geometry_digest=self.digest,
                         cells=changed)
        prepared['overrides']['cell-b']['score'] = 9
        with self.assertRaisesRegex(ValueError, 'plan changed'):
            freeze_draft(prepared, mesh_job_id='mesh-1', geometry_digest=self.digest,
                         cells=self.cells)

    def test_rejects_unknown_ids_bad_values_and_column_collisions(self):
        for change, message in [
            ({'rows': [dict(cell_id='invented', values={'note': 'x'})]}, 'Unknown'),
            ({'rows': [dict(cell_id='cell-a', values={'r1': 8})]}, 'unknown or raster'),
            ({'rows': [dict(cell_id='cell-a', values={'score': float('nan')})]}, 'finite'),
            ({'columns': [dict(id='a', type='manual', name='cell_id', value_type='text')]}, 'reserved'),
            ({'columns': [dict(id='a', type='manual', name='same', value_type='text'),
                          dict(id='b', type='manual', name='SAME', value_type='text')]}, 'unique'),
        ]:
            payload = {**self.draft, **change}
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                prepare_draft('mesh-1', self.digest, self.cells, self.features, payload)

    def test_persisted_annotations_export_without_geometry_for_manual_rows(self):
        prepared = prepare_draft('mesh-1', self.digest, self.cells, self.features, self.draft)
        frozen = freeze_draft(prepared, mesh_job_id='mesh-1',
                              geometry_digest=self.digest, cells=self.cells)
        subset_digest = geometry_digest(frozen['cells'], self.crs)
        metadata = dict(parent_geometry_digest=self.digest, plan_hash=frozen['plan_digest'],
            columns=frozen['columns'], overrides=frozen['overrides'],
            manual_rows=frozen['manual_rows'], excluded_cell_ids=frozen['excluded_cell_ids'])
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            database = root / 'batch.sqlite'
            # A no-op extractor keeps the test independent from raster I/O.
            from unittest.mock import patch
            with patch('gis_mesh_workbench.batch_extraction.extract_indexed',
                       return_value=dict(cells=[dict(cell_id='cell-b', value=12,
                           valid_area_m2=2)], provenance={},
                           qa=dict(engine_evidence=dict(candidate_pairs=1)))):
                run_batch(database, frozen['cells'], frozen['features'], cell_crs=self.crs,
                    analysis_crs=self.crs, accepted_geometry_digest=subset_digest,
                    draft_plan=metadata)
            wide, long, payload = root / 'wide.csv', root / 'long.csv', root / 'data.json'
            export_wide_csv(database, wide); export_long_csv(database, long); export_json(database, payload)
            with wide.open(encoding='utf-8-sig', newline='') as stream:
                rows = list(csv.reader(stream))
            self.assertEqual(rows[0][0:2], ['row_kind', 'row_id'])
            self.assertEqual(rows[0][7:12], ['Review note', 'Rainfall', 'r1__state',
                                             'r1__status', 'Score'])
            manual = next(row for row in rows if row[0] == 'manual')
            self.assertEqual(manual[1], 'survey-1')
            self.assertEqual(manual[2:7], ['', '', '', '', ''])
            self.assertEqual(manual[7], 'field only')
            self.assertEqual(manual[9], 'not_applicable')
            cell_row = next(row for row in rows if row[0] == 'cell')
            self.assertEqual(cell_row[7], "'=SUM(A1:A2)")
            self.assertEqual(cell_row[8:11], ['12', 'computed', 'complete'])
            exported = json.loads(payload.read_text(encoding='utf-8'))
            self.assertEqual(exported['batch']['draft']['parent_geometry_digest'], self.digest)
            self.assertEqual(exported['manual_rows'][0]['row_kind'], 'manual')
            self.assertNotIn('geometry', exported['manual_rows'][0])
            self.assertEqual(exported['cells'][0]['values']['note'], '=SUM(A1:A2)')
            self.assertEqual(exported['cells'][0]['values']['r1'],
                             {'state': 'computed', 'value': 12, 'status': 'complete'})
            with (root / 'long.csv').open(encoding='utf-8-sig', newline='') as stream:
                long_rows = list(csv.reader(stream))
            self.assertIn('column_id', long_rows[0])
            self.assertTrue(any(row[0] == 'manual' and row[7] == 'note' for row in long_rows[1:]))

    def test_wire_state_round_trips_without_raster_values(self):
        prepared = prepare_draft('mesh-1', self.digest, self.cells, self.features, self.draft)
        wire = canonical_wire_state(prepared)
        rebuilt = prepare_draft('mesh-1', self.digest, self.cells, self.features, wire)
        self.assertEqual(preview_draft(prepared), preview_draft(rebuilt))


if __name__ == '__main__':
    unittest.main()
