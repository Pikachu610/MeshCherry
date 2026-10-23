"""Admission must reject predictable aggregate read failures before geometry work."""
import unittest
from unittest.mock import patch

import test_workbench_comparison as fixtures
from gis_mesh_workbench.comparison import compare_mesh, preflight_comparison
from gis_mesh_workbench.comparison_admission import ComparisonAdmissionError, estimate_read_workload
from gis_mesh_workbench.indexed_extraction import CEILINGS
from gis_mesh_workbench.job_control import Cancelled, JobControl, job_scope
from rasterio.crs import CRS
from shapely.geometry import box
from shapely.strtree import STRtree
import rasterio


class ComparisonAdmissionTests(unittest.TestCase):
    setUp = fixtures.ComparisonTests.setUp
    freeze = fixtures.ComparisonTests.freeze
    write = fixtures.ComparisonTests.write

    def test_two_pass_budget_rejected_before_partition_or_pixel_work(self):
        cap = dict(CEILINGS, max_pixels_read=5)
        from gis_mesh_workbench.comparison import _square_partition, geometry_batch
        with patch('gis_mesh_workbench.comparison.limits', return_value=cap), \
                patch('gis_mesh_workbench.comparison._square_partition', wraps=_square_partition) as partition, \
                patch('gis_mesh_workbench.comparison.geometry_batch', wraps=geometry_batch) as geometry:
            with self.assertRaisesRegex(ValueError, 'read pixel limit'):
                compare_mesh(self.mesh, self.raster, analysis_crs=self.crs,
                             cell_size_m=1, value_units='m')
        partition.assert_not_called()
        geometry.assert_not_called()

    def test_exact_estimate_matches_both_runtime_passes(self):
        for mode in ('target_grid', 'native_raster'):
            for semantics in ('intensive', 'extensive', 'categorical'):
                with self.subTest(mode=mode, semantics=semantics):
                    estimate = preflight_comparison(self.mesh, self.raster,
                        analysis_crs=self.crs, semantics=semantics)
                    report = compare_mesh(self.mesh, self.raster, analysis_crs=self.crs,
                        baseline_mode=mode, cell_size_m=1 if mode == 'target_grid' else None,
                        value_units='m', semantics=semantics)
                    actual = report['workload']['aggregate']
                    self.assertTrue(estimate['complete'])
                    self.assertEqual(estimate['aggregate_pixels_read'], actual['pixels_read'])
                    self.assertEqual(estimate['aggregate_windows_scanned'], actual['windows_scanned'])
                    self.assertEqual(estimate['aggregate_windows_read'], actual['windows_read'])

    def test_metadata_only_and_budget_equality_allowed(self):
        with rasterio.open(self.raster) as src:
            with patch.object(type(src), 'read', side_effect=AssertionError('band read')):
                result = estimate_read_workload(src, CRS.from_string(self.crs),
                    STRtree([box(500000, 1000000, 500002, 1000002)]), 1,
                    band=1, semantics='intensive', cap=dict(CEILINGS, max_pixels_read=8))
        self.assertEqual(result['aggregate_pixels_read'], 8)

    def test_mask_does_not_reduce_read_budget(self):
        self.write([[1, 1], [1, 1]], [[0, 0], [0, 0]])
        with patch('gis_mesh_workbench.comparison.limits', return_value=dict(CEILINGS, max_pixels_read=7)):
            with self.assertRaises(ComparisonAdmissionError) as failure:
                preflight_comparison(self.mesh, self.raster, analysis_crs=self.crs)
        self.assertEqual(failure.exception.estimate['aggregate_pixels_read'], 8)
        self.assertFalse(failure.exception.estimate['complete'])

    def test_cross_crs_disables_culling_even_disjoint_index(self):
        with rasterio.open(self.raster) as src:
            result = estimate_read_workload(src, CRS.from_epsg(32648),
                STRtree([box(0, 0, 1, 1)]), 1, band=1, semantics='intensive', cap=CEILINGS)
        self.assertEqual(result['aggregate_pixels_read'], 8)
        self.assertEqual(result['window_culling'], 'disabled_cross_crs_conservative')

    def test_same_crs_disjoint_windows_not_read(self):
        with rasterio.open(self.raster) as src:
            result = estimate_read_workload(src, CRS.from_string(self.crs),
                STRtree([box(0, 0, 1, 1)]), 1, band=1, semantics='intensive', cap=CEILINGS)
        self.assertEqual(result['aggregate_pixels_read'], 0)
        self.assertEqual(result['aggregate_windows_scanned'], 2)

    def test_scan_cap_and_cancellation_before_any_values(self):
        with rasterio.open(self.raster) as src:
            tree = STRtree([box(500000, 1000000, 500002, 1000002)])
            with patch('gis_mesh_workbench.comparison_admission.MAX_WINDOWS', 1):
                with self.assertRaisesRegex(ComparisonAdmissionError, 'window scan limit'):
                    estimate_read_workload(src, CRS.from_string(self.crs), tree, 1,
                        band=1, semantics='intensive', cap=CEILINGS)
            control = JobControl()
            control.request_cancel()
            with job_scope(control), self.assertRaises(Cancelled):
                estimate_read_workload(src, CRS.from_string(self.crs), tree, 1,
                    band=1, semantics='intensive', cap=CEILINGS)


if __name__ == '__main__':
    unittest.main()
