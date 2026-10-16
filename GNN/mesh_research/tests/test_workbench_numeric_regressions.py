import json
import math
from pathlib import Path
import tempfile
import unittest

from affine import Affine
import numpy as np
import rasterio

from gis_mesh_workbench.comparison import compare_mesh
from gis_mesh_workbench.extraction import Cell, _digest, extract_attributes
from gis_mesh_workbench.mesh_workflow import geometry_digest


class WorkbenchNumericRegressionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir='.')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.crs = 'EPSG:32649'

    def write_raster(self, name, values, *, dtype='float64', transform=None):
        values = np.asarray(values, dtype=dtype)
        path = self.root / name
        transform = transform or Affine(1, 0, 0, 0, -1, values.shape[0])
        with rasterio.open(path, 'w', driver='GTiff', width=values.shape[1],
                           height=values.shape[0], count=1, dtype=dtype,
                           crs=self.crs, transform=transform) as dst:
            dst.write(values, 1)
        return path

    def compare(self, values, *, cell_size_m=1, semantics='intensive', transform=None):
        mesh_path = self.root / 'cells.json'
        manifest_path = self.root / 'manifest.json'
        raster_path = self.write_raster(
            'comparison.tif', values,
            transform=transform or Affine(1, 0, 500000, 0, -1, 1000002))
        cells = (
            Cell('accepted-7', 'triangle',
                 ((500000, 1000000), (500002, 1000000), (500002, 1000002))),
            Cell('accepted-29', 'triangle',
                 ((500000, 1000000), (500002, 1000002), (500000, 1000002))),
        )
        mesh_path.write_text(json.dumps(dict(schema_version='workbench-cells-v1',
            coordinate_units='m', crs_wkt=self.crs,
            cells=[dict(cell_id=c.cell_id, kind=c.kind, xy=c.xy) for c in cells])))
        manifest_path.write_text(json.dumps(dict(status='pass', operation='mesh_only',
            artifact_hashes={'cells.json': _digest(mesh_path)},
            geometry_digest=geometry_digest(cells, self.crs))))
        return compare_mesh(mesh_path, raster_path, analysis_crs=self.crs,
                            cell_size_m=cell_size_m, semantics=semantics, value_units='u')

    def test_reference_categorical_uint64_ids_above_float_precision_stay_distinct(self):
        low, high = 2**53, 2**53 + 1
        raster_path = self.write_raster('categories.tif', [[low, high]], dtype='uint64')
        cells = [
            Cell('left', 'rectangle', ((0, 0), (1, 0), (1, 1), (0, 1))),
            Cell('right', 'rectangle', ((1, 0), (2, 0), (2, 1), (1, 1))),
        ]

        result = extract_attributes(raster_path, cells, cell_crs=self.crs,
            analysis_crs=self.crs, band=1, semantics='categorical', value_units='class-id')

        self.assertEqual([cell['value'] for cell in result['cells']], [float(low), str(high)])
        self.assertEqual([cell['class_fractions'] for cell in result['cells']],
            [{str(float(low)): 1.0}, {str(high): 1.0}])
        json.dumps(result, allow_nan=False)

    def test_reference_intensive_mean_and_std_are_stable_for_extreme_finite_values(self):
        raster_path = self.write_raster('extreme-intensive.tif', [[1e308, -1e308]])
        cell = Cell('both', 'rectangle', ((0, 0), (2, 0), (2, 1), (0, 1)))

        result = extract_attributes(raster_path, [cell], cell_crs=self.crs,
            analysis_crs=self.crs, band=1, semantics='intensive', value_units='u')

        self.assertEqual(result['cells'][0]['value'], 0.0)
        self.assertEqual(result['cells'][0]['std'], 1e308)
        json.dumps(result, allow_nan=False)

    def test_reference_intensive_constant_extremes_keep_finite_mean_and_zero_std(self):
        raster_path = self.write_raster('constant-extreme.tif', [[1e308, 1e308]])
        cell = Cell('both', 'rectangle', ((0, 0), (2, 0), (2, 1), (0, 1)))

        result = extract_attributes(raster_path, [cell], cell_crs=self.crs,
            analysis_crs=self.crs, band=1, semantics='intensive', value_units='u')

        self.assertEqual(result['cells'][0]['value'], 1e308)
        self.assertEqual(result['cells'][0]['std'], 0.0)
        json.dumps(result, allow_nan=False)

    def test_reference_extensive_total_uses_stable_sum_for_extreme_finite_values(self):
        raster_path = self.write_raster('extreme-extensive.tif', [[1e308, 1e308, -1e308]])
        cell = Cell('all', 'rectangle', ((0, 0), (3, 0), (3, 1), (0, 1)))

        result = extract_attributes(raster_path, [cell], cell_crs=self.crs,
            analysis_crs=self.crs, band=1, semantics='extensive', value_units='u')

        self.assertEqual(result['cells'][0]['value'], 1e308)
        self.assertEqual(result['qa']['source_total_on_cell_union'], 1e308)
        self.assertEqual(result['qa']['cell_total'], 1e308)
        json.dumps(result, allow_nan=False)

    def test_reference_extensive_fsum_preserves_small_residual_after_large_cancellation(self):
        raster_path = self.write_raster('small-residual.tif', [[1e308, -1e308, 1e-308]])
        cell = Cell('all', 'rectangle', ((0, 0), (3, 0), (3, 1), (0, 1)))

        result = extract_attributes(raster_path, [cell], cell_crs=self.crs,
            analysis_crs=self.crs, band=1, semantics='extensive', value_units='u')

        self.assertEqual(result['cells'][0]['value'], 1e-308)
        self.assertEqual(result['qa']['source_total_on_cell_union'], 1e-308)
        json.dumps(result, allow_nan=False)

    def test_reference_extensive_exact_fallback_preserves_residual_after_fsum_overflow(self):
        raster_path = self.write_raster(
            'fallback-residual.tif', [[1e308, 1e308, -1e308, -1e308, 1e-308]])
        cell = Cell('all', 'rectangle', ((0, 0), (5, 0), (5, 1), (0, 1)))

        result = extract_attributes(raster_path, [cell], cell_crs=self.crs,
            analysis_crs=self.crs, band=1, semantics='extensive', value_units='u')

        self.assertEqual(result['cells'][0]['value'], 1e-308)
        self.assertEqual(result['qa']['source_total_on_cell_union'], 1e-308)
        json.dumps(result, allow_nan=False)

    def test_reference_extensive_true_out_of_range_total_is_rejected(self):
        raster_path = self.write_raster('out-of-range.tif', [[1e308, 1e308]])
        cell = Cell('all', 'rectangle', ((0, 0), (2, 0), (2, 1), (0, 1)))

        with self.assertRaisesRegex(ValueError, 'finite numeric range'):
            extract_attributes(raster_path, [cell], cell_crs=self.crs,
                analysis_crs=self.crs, band=1, semantics='extensive', value_units='u')

    def test_comparison_rmse_stays_finite_when_squared_deltas_overflow(self):
        result = self.compare([[1e200, -1e200], [1e200, -1e200]])

        self.assertEqual(result['metrics']['structured']['rmse'], 0.0)
        self.assertTrue(math.isfinite(result['metrics']['unstructured']['rmse']))
        self.assertTrue(math.isclose(result['metrics']['unstructured']['rmse'],
            math.sqrt(0.75) * 1e200, rel_tol=1e-12))
        json.dumps(result, allow_nan=False)

    def test_comparison_weighted_means_and_rmse_stay_finite_at_float64_scale(self):
        result = self.compare([[1e308, -1e308], [1e308, -1e308]], cell_size_m=2)

        self.assertTrue(math.isfinite(result['metrics']['structured']['rmse']))
        self.assertEqual(result['metrics']['structured']['rmse'], 1e308)
        self.assertTrue(math.isfinite(result['metrics']['unstructured']['rmse']))
        json.dumps(result, allow_nan=False)

    def test_comparison_extensive_fraction_first_avoids_multiply_overflow(self):
        result = self.compare([[1e308]], semantics='extensive', cell_size_m=2,
            transform=Affine(2, 0, 500000, 0, -2, 1000002))

        for metric in result['metrics'].values():
            self.assertEqual(metric['source_total'], 1e308)
            self.assertEqual(metric['remapped_total'], 1e308)
            self.assertEqual(metric['conservation_residual'], 0.0)
        json.dumps(result, allow_nan=False)

    def test_comparison_extensive_accumulator_preserves_small_cancellation_residual(self):
        result = self.compare([[1e308, -1e308], [1e-308, 0.0]],
            semantics='extensive')

        self.assertEqual(result['metrics']['structured']['source_total'], 1e-308)
        self.assertEqual(result['metrics']['structured']['remapped_total'], 1e-308)
        self.assertEqual(result['metrics']['unstructured']['remapped_total'], 1e-308)
        json.dumps(result, allow_nan=False)

    def test_comparison_extensive_exact_accumulator_handles_intermediate_overflow(self):
        result = self.compare([[1e308, 1e308], [-1e308, 0.0]],
            semantics='extensive')

        for metric in result['metrics'].values():
            self.assertEqual(metric['source_total'], 1e308)
            self.assertEqual(metric['remapped_total'], 1e308)
        json.dumps(result, allow_nan=False)


if __name__ == '__main__':
    unittest.main()
