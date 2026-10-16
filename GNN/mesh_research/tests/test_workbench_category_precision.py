"""Categorical identity must survive native raster reads and JSON consumers."""
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
import rasterio
from affine import Affine

from gis_mesh_workbench.extraction import Cell
from gis_mesh_workbench.indexed_extraction import extract_indexed


class CategoryPrecisionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'classes.tif'
        self.cell = Cell('region', 'rectangle', ((500000, 1000000),
            (500002, 1000000), (500002, 1000002), (500000, 1000002)))

    def extract(self, values, dtype, mask=None):
        values = np.asarray(values, dtype=dtype)
        with rasterio.open(self.path, 'w', driver='GTiff', width=2, height=2,
                count=1, dtype=dtype, crs='EPSG:32649',
                transform=Affine(1, 0, 500000, 0, -1, 1000002)) as dst:
            dst.write(values, 1)
            if mask is not None:
                dst.write_mask(np.asarray(mask, dtype='uint8'))
        before = self.path.read_bytes()
        report = extract_indexed(self.path, [self.cell], cell_crs='EPSG:32649',
            analysis_crs='EPSG:32649', band=1, semantics='categorical', value_units='class ID',
            hard_limits=dict(max_window_pixels=1))
        self.assertEqual(before, self.path.read_bytes())
        return json.loads(json.dumps(report, allow_nan=False))

    def test_adjacent_uint64_classes_keep_identity_and_dominant_id(self):
        low, high = 2**53, 2**53 + 1
        report = self.extract([[low, high], [high, high]], 'uint64')
        cell = report['cells'][0]
        self.assertEqual(cell['class_fractions'], {str(float(low)): .25, str(high): .75})
        self.assertEqual(cell['value'], str(high))
        self.assertEqual(cell['valid_coverage'], 1)
        self.assertIn('decimal strings', report['provenance']['categorical_value_encoding'])

    def test_signed_extreme_integer_classes_and_mask(self):
        low, high = -(2**63), -(2**63) + 1
        report = self.extract([[low, high], [high, low]], 'int64', [[255, 255], [0, 255]])
        cell = report['cells'][0]
        self.assertEqual(cell['class_fractions'], {str(low): 2/3, str(high): 1/3})
        self.assertEqual(cell['value'], str(low))
        self.assertEqual(cell['valid_coverage'], .75)

    def test_safe_integer_labels_and_fractional_classes_remain_compatible(self):
        cell = self.extract([[10, 20], [10, 20]], 'uint16')['cells'][0]
        self.assertEqual(cell['class_fractions'], {'10.0': .5, '20.0': .5})
        self.assertEqual(cell['value'], 10.0)
        cell = self.extract([[.25, .75], [.25, .75]], 'float64')['cells'][0]
        self.assertEqual(cell['class_fractions'], {'0.25': .5, '0.75': .5})
        self.assertEqual(cell['value'], .25)

    def test_finite_float_codes_do_not_enter_intensive_arithmetic(self):
        cell = self.extract([[1e308, -1e308], [np.nan, 1e308]], 'float64')['cells'][0]
        self.assertEqual(cell['class_fractions'], {'-1e+308': 1/3, '1e+308': 2/3})
        self.assertEqual(cell['value'], 1e308)
        self.assertEqual(cell['valid_coverage'], .75)

    def test_extreme_finite_intensive_values_remain_json_safe(self):
        values = np.asarray([[-1e308, 1e308], [-1e308, 1e308]], dtype='float64')
        with rasterio.open(self.path, 'w', driver='GTiff', width=2, height=2,
                count=1, dtype='float64', crs='EPSG:32649',
                transform=Affine(1, 0, 500000, 0, -1, 1000002)) as dst:
            dst.write(values, 1)
        report = extract_indexed(self.path, [self.cell], cell_crs='EPSG:32649',
            analysis_crs='EPSG:32649', band=1, semantics='intensive', value_units='test')
        json.dumps(report, allow_nan=False)
        cell = report['cells'][0]
        self.assertEqual(cell['value'], 0)
        self.assertAlmostEqual(cell['std'], 1e308)

    def test_overflowing_extensive_result_is_rejected_before_json_output(self):
        values = np.full((2, 2), 1e308, dtype='float64')
        with rasterio.open(self.path, 'w', driver='GTiff', width=2, height=2,
                count=1, dtype='float64', crs='EPSG:32649',
                transform=Affine(1, 0, 500000, 0, -1, 1000002)) as dst:
            dst.write(values, 1)
        with self.assertRaisesRegex(ValueError, 'nonfinite JSON number'):
            extract_indexed(self.path, [self.cell], cell_crs='EPSG:32649',
                analysis_crs='EPSG:32649', band=1, semantics='extensive', value_units='test')


if __name__ == '__main__':
    unittest.main()
