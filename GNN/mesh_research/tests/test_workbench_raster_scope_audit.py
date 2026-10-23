"""Sentinel regressions for ROI support versus source-raster validity."""
import tempfile
import unittest
from pathlib import Path

import numpy as np
import rasterio
from affine import Affine
from rasterio.crs import CRS
from rasterio.transform import from_origin
from shapely.geometry import box

from gis_mesh_workbench import sf1_sizing as sf1
from gis_mesh_workbench.extraction import Cell, extract_attributes
from gis_mesh_workbench.indexed_extraction import extract_indexed


class RasterScopeAuditTests(unittest.TestCase):
    def test_relief_honors_validity_mask_and_uses_valid_neighbours_as_support(self):
        z = np.zeros((12, 12), dtype=float)
        z[5, 5] = 90.0
        valid = np.ones(z.shape, dtype=bool)
        roi = np.zeros(z.shape, dtype=bool)
        roi[4:8, 4:8] = True

        baseline = sf1.relief_scores(z, valid, roi)[0]
        # Outside-ROI values are genuine neighbouring source samples. They can
        # affect local relief but are not part of percentile normalization.
        changed_valid_neighbour = z.copy()
        changed_valid_neighbour[4, 4] = 180.0
        neighbour_score = sf1.relief_scores(changed_valid_neighbour, valid, roi)[0]
        self.assertNotEqual(float(baseline[5, 5]), float(neighbour_score[5, 5]))

        # Invalid backing storage must not affect any score, even if it carries
        # a very large sentinel. This models NoData/internal-mask exclusion.
        invalid = valid.copy()
        invalid[4, 4] = False
        nodata_backing_a, nodata_backing_b = z.copy(), z.copy()
        nodata_backing_a[4, 4] = -9999.0
        nodata_backing_b[4, 4] = 123456.0
        scores_a = sf1.relief_scores(nodata_backing_a, invalid, roi)[0]
        scores_b = sf1.relief_scores(nodata_backing_b, invalid, roi)[0]
        np.testing.assert_array_equal(scores_a, scores_b)

    def test_zero_is_valid_and_not_nodata_in_sf1_relief(self):
        z = np.zeros((12, 12), dtype=float)
        valid = np.ones(z.shape, dtype=bool)
        roi = np.zeros(z.shape, dtype=bool)
        roi[3:9, 3:9] = True
        score, limits = sf1.relief_scores(z, valid, roi)
        self.assertTrue(np.isfinite(score).all())
        self.assertEqual(limits['relief150'], [0.0, 0.0])
        self.assertEqual(limits['relief450'], [0.0, 0.0])

    def test_extraction_excludes_nodata_and_nan_with_fractional_overlap(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'nodata-nan.tif'
            transform = Affine(1, 0, 500000, 0, -1, 1000002)
            values = np.array([[0.0, -9999.0], [np.nan, 10.0]], dtype='float64')
            with rasterio.open(path, 'w', driver='GTiff', width=2, height=2,
                               count=1, dtype='float64', crs='EPSG:32649',
                               transform=transform, nodata=-9999.0) as dst:
                dst.write(values, 1)

            # The ROI partly overlaps the NoData pixel. NoData is excluded,
            # while the zero remains a real valid value.
            cell = Cell('roi-cell', 'rectangle', (
                (500000.0, 1000000.0), (500001.5, 1000000.0),
                (500001.5, 1000002.0), (500000.0, 1000002.0)))
            top_row_cell = Cell('top-row-roi', 'rectangle', (
                (500000.0, 1000001.0), (500001.5, 1000001.0),
                (500001.5, 1000002.0), (500000.0, 1000002.0)))
            result = extract_attributes(path, [top_row_cell], cell_crs='EPSG:32649',
                analysis_crs='EPSG:32649', band=1, semantics='intensive',
                value_units='m')
            record = result['cells'][0]
            self.assertAlmostEqual(record['footprint_coverage'], 1.0)
            self.assertAlmostEqual(record['valid_coverage'], 2.0 / 3.0)
            self.assertEqual(record['value'], 0.0)
            self.assertAlmostEqual(record['valid_area_m2'], 1.0)
            self.assertAlmostEqual(record['area_m2'], 1.5)

            # A separate file exercises an internal validity mask without a
            # nodata sentinel. Its finite masked value must also be excluded.
            masked_path = Path(tmp) / 'internal-mask.tif'
            with rasterio.open(masked_path, 'w', driver='GTiff', width=2, height=2,
                               count=1, dtype='float64', crs='EPSG:32649',
                               transform=transform) as dst:
                dst.write(np.array([[0.0, 8.0], [6.0, 10.0]]), 1)
                dst.write_mask(np.array([[255, 0], [0, 0]], dtype='uint8'))
            masked_record = extract_attributes(masked_path, [cell], cell_crs='EPSG:32649',
                analysis_crs='EPSG:32649', band=1, semantics='intensive',
                value_units='m')['cells'][0]
            self.assertEqual(masked_record['value'], 0.0)
            self.assertAlmostEqual(masked_record['valid_area_m2'], 1.0)
            self.assertAlmostEqual(masked_record['valid_coverage'], 1.0 / 3.0)

    def test_ascii_grid_nodata_partial_overlap_matches_indexed_and_reference(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'sentinel.asc'
            path.write_text(
                'ncols 2\nnrows 1\nxllcorner 500000\nyllcorner 1000001\n'
                'cellsize 1\nNODATA_value -9999\n0 -9999\n', encoding='ascii')
            path.with_suffix('.prj').write_text(CRS.from_epsg(32649).to_wkt(), encoding='ascii')
            cell = Cell('partial', 'rectangle', (
                (500000.0, 1000001.0), (500001.5, 1000001.0),
                (500001.5, 1000002.0), (500000.0, 1000002.0)))
            options = dict(cell_crs='EPSG:32649', analysis_crs='EPSG:32649',
                           band=1, semantics='intensive', value_units='m')
            reference = extract_attributes(path, [cell], **options)['cells'][0]
            indexed = extract_indexed(path, [cell], **options)['cells'][0]
            self.assertAlmostEqual(reference['valid_area_m2'], 1.0)
            self.assertAlmostEqual(reference['area_m2'], 1.5)
            self.assertEqual(reference['value'], 0.0)
            self.assertAlmostEqual(reference['value'], indexed['value'], places=12)
            self.assertEqual(reference['valid_area_m2'], indexed['valid_area_m2'])
            self.assertAlmostEqual(reference['valid_coverage'], 2.0 / 3.0)

    def test_valid_source_outside_canonical_roi_can_support_accepted_cell_geometry(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'valid-outside.tif'
            with rasterio.open(path, 'w', driver='GTiff', width=2, height=1,
                               count=1, dtype='float64', crs='EPSG:32649',
                               transform=from_origin(500000, 1000001, 1, 1)) as dst:
                dst.write(np.array([[10.0, 30.0]]), 1)
            # The canonical ROI is x=[500000,500001], while an accepted
            # derived cell extends to x=500002 and therefore overlaps a valid
            # source pixel whose centre lies outside the canonical ROI.
            accepted_derived_cell = Cell('derived-edge', 'rectangle', (
                (500000.0, 1000000.0), (500002.0, 1000000.0),
                (500002.0, 1000001.0), (500000.0, 1000001.0)))
            options = dict(cell_crs='EPSG:32649', analysis_crs='EPSG:32649',
                           band=1, semantics='intensive', value_units='m')
            reference = extract_attributes(path, [accepted_derived_cell], **options)['cells'][0]
            indexed = extract_indexed(path, [accepted_derived_cell], **options)['cells'][0]
            self.assertEqual(reference['value'], 20.0)
            self.assertAlmostEqual(reference['value'], indexed['value'], places=12)
            self.assertEqual(reference['valid_coverage'], 1.0)


if __name__ == '__main__':
    unittest.main()
