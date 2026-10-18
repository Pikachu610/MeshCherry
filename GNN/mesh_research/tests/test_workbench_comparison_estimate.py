import json
from pathlib import Path
import tempfile
import unittest

from gis_mesh_workbench.comparison_estimate import (
    MAX_SAFE_JSON_INTEGER, _ceil_area_over_square, _require_json_safe_count,
    compare_resolution_estimate,
)
from gis_mesh_workbench.extraction import Cell, _digest
from gis_mesh_workbench.mesh_workflow import geometry_digest


class ResolutionEstimateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.mesh = self.root / 'cells.json'
        self.crs = 'EPSG:32649'
        self.cells = (
            Cell('accepted-a', 'triangle', ((500000, 1000000),
                (500002, 1000000), (500002, 1000002))),
            Cell('accepted-b', 'triangle', ((500000, 1000000),
                (500002, 1000002), (500000, 1000002))),
        )
        self.freeze()

    def freeze(self):
        self.mesh.write_text(json.dumps(dict(schema_version='workbench-cells-v1',
            coordinate_units='m', crs_wkt=self.crs,
            cells=[dict(cell_id=c.cell_id, kind=c.kind, xy=c.xy) for c in self.cells])))
        (self.root / 'manifest.json').write_text(json.dumps(dict(status='pass',
            operation='mesh_only', artifact_hashes={'cells.json': _digest(self.mesh)},
            geometry_digest=geometry_digest(self.cells, self.crs))))

    def estimate(self, **kwargs):
        return compare_resolution_estimate(self.mesh, analysis_crs=self.crs,
            cell_size_m=kwargs.pop('cell_size_m', 1),
            source_resolution_m=kwargs.pop('source_resolution_m', 2), **kwargs)

    def test_area_only_report_uses_cell_nodes_and_ceil_area_counts(self):
        result = self.estimate()
        self.assertEqual(result['mesh']['cell_count'], 2)
        self.assertEqual(result['mesh']['node_count'], 2)
        self.assertEqual(result['mesh']['union_area_m2'], 4)
        self.assertEqual(result['square_grid']['cell_count'], 4)
        self.assertEqual(result['square_grid']['node_count'], 4)
        self.assertEqual(result['baseline']['estimated_node_count'], 4)
        self.assertEqual(result['baseline']['estimated_source_pixel_count'], 1)
        self.assertEqual(result['baseline']['source_resolution_m'], 2)
        self.assertEqual(result['node_reduction_percent'], 50)
        self.assertEqual(result['graph_node_delta'], -2)
        self.assertFalse(result['metrics_available'])
        self.assertIsNone(result['metrics'])
        self.assertIn('NoData', ' '.join(result['baseline']['limitations']))
        self.assertIn('not computed', result['error_definition'])

    def test_source_to_finer_target_estimates_distinct_counts(self):
        result = self.estimate(cell_size_m=1, source_resolution_m=2)
        self.assertEqual(result['baseline']['estimated_source_pixel_count'], 1)
        self.assertEqual(result['square_grid']['node_count'], 4)
        self.assertEqual(result['graph_node_delta'], -2)
        self.assertEqual(result['node_reduction_percent'], 50)
        self.assertIn('area-only', result['method'])
        self.assertNotIn('rmse', result)

    def test_source_60m_to_target_30m_uses_target_count_for_baseline_nodes(self):
        self.cells = (
            Cell('large-a', 'triangle', ((500000, 1000000),
                (500060, 1000000), (500060, 1000120))),
            Cell('large-b', 'triangle', ((500000, 1000000),
                (500060, 1000120), (500000, 1000120))),
        )
        self.freeze()
        result = self.estimate(cell_size_m=30, source_resolution_m=60)
        self.assertEqual(result['mesh']['node_count'], 2)
        self.assertEqual(result['baseline']['estimated_source_pixel_count'], 2)
        self.assertEqual(result['square_grid']['node_count'], 8)
        self.assertEqual(result['baseline']['estimated_node_count'], 8)
        self.assertEqual(result['graph_node_delta'], -6)

    def test_rounds_up_any_positive_fractional_area_ratio(self):
        self.assertEqual(self.estimate(cell_size_m=3)['square_grid']['node_count'], 1)
        with self.assertRaisesRegex(ValueError, 'estimated target cell count.*exactly representable'):
            self.estimate(cell_size_m=1e-200)
        with self.assertRaisesRegex(ValueError, 'estimated source pixel count.*exactly representable'):
            self.estimate(source_resolution_m=1e-200)

    def test_json_count_boundary_is_exact_and_out_of_range_is_rejected(self):
        # The maximum safe integer itself remains exact in browser JSON numbers.
        count = _ceil_area_over_square(float(MAX_SAFE_JSON_INTEGER), 1.0)
        self.assertEqual(count, MAX_SAFE_JSON_INTEGER)
        self.assertEqual(_require_json_safe_count(count, 'estimated count'),
                         MAX_SAFE_JSON_INTEGER)
        with self.assertRaisesRegex(ValueError, 'maximum exactly representable'):
            _require_json_safe_count(MAX_SAFE_JSON_INTEGER + 1, 'estimated count')

    def test_invalid_sizes_and_crs_are_rejected(self):
        for bad in (0, -1, float('nan'), float('inf'), True):
            with self.subTest(cell_size=bad), self.assertRaises(ValueError):
                self.estimate(cell_size_m=bad)
            with self.subTest(source_resolution=bad), self.assertRaises(ValueError):
                self.estimate(source_resolution_m=bad)
        with self.assertRaisesRegex(ValueError, 'projected'):
            compare_resolution_estimate(self.mesh, analysis_crs='EPSG:4326',
                cell_size_m=30, source_resolution_m=60)

    def test_nontriangular_cells_are_rejected(self):
        self.cells = (Cell('rectangle', 'rectangle', ((500000, 1000000),
            (500002, 1000000), (500002, 1000002), (500000, 1000002))),)
        self.freeze()
        with self.assertRaisesRegex(ValueError, 'triangular'):
            self.estimate()

    def test_overlapping_triangle_union_is_rejected(self):
        self.cells = (
            Cell('triangle-a', 'triangle', ((500000, 1000000),
                (500002, 1000000), (500002, 1000002))),
            Cell('triangle-b', 'triangle', ((500000, 1000000),
                (500002, 1000000), (500002, 1000002))),
        )
        self.freeze()
        with self.assertRaisesRegex(ValueError, 'overlap'):
            self.estimate()


if __name__ == '__main__':
    unittest.main()
