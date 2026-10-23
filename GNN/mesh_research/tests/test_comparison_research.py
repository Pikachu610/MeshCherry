"""Research comparisons remove total-work ceilings, not numerical safeguards."""
import json
import unittest
from unittest.mock import patch

import numpy as np
import rasterio
from affine import Affine

import test_workbench_comparison as fixtures
from gis_mesh_workbench.comparison import compare_mesh, preflight_comparison
from gis_mesh_workbench.indexed_extraction import CEILINGS, limits
from gis_mesh_workbench.job_control import Cancelled, JobControl, job_scope
from gis_mesh_workbench.extraction import Cell


class ResearchComparisonTests(unittest.TestCase):
    setUp = fixtures.ComparisonTests.setUp
    freeze = fixtures.ComparisonTests.freeze
    write = fixtures.ComparisonTests.write

    def run_comparison(self, mode, semantics='intensive', **options):
        return compare_mesh(self.mesh, self.raster, analysis_crs=self.crs,
            baseline_mode=mode, cell_size_m=1 if mode == 'target_grid' else None,
            semantics=semantics, value_units='m', **options)

    def test_research_removes_source_read_pair_and_scan_ceilings_only(self):
        tiny = dict(CEILINGS, max_source_pixels=1, max_pixels_read=1,
                    max_candidate_pairs=1)
        with patch('gis_mesh_workbench.comparison.limits', return_value=tiny), \
                patch('gis_mesh_workbench.comparison.MAX_WINDOWS', 0), \
                patch('gis_mesh_workbench.comparison_admission.MAX_WINDOWS', 0):
            report = self.run_comparison('native_raster', workload_mode='research')
        self.assertEqual(report['structured']['node_count'], 4)
        workload = report['workload']
        self.assertEqual(workload['workload_mode'], 'research')
        for name in ('max_source_pixels', 'max_pixels_read', 'max_candidate_pairs'):
            self.assertIsNone(workload['hard_limits'][name])
        self.assertIsNone(workload['max_windows_scanned'])
        self.assertEqual(workload['hard_limits']['max_window_pixels'], 65536)
        self.assertLessEqual(workload['aggregate']['peak_window_pixels'], 65536)
        json.dumps(report, allow_nan=False)

    def test_research_and_bounded_math_identical_all_semantics_and_baselines(self):
        self.write([[10, 30], [10, 17]], [[255, 0], [255, 255]])
        for baseline in ('target_grid', 'native_raster'):
            for semantics in ('intensive', 'extensive', 'categorical'):
                with self.subTest(baseline=baseline, semantics=semantics):
                    bounded = self.run_comparison(baseline, semantics)
                    research = self.run_comparison(baseline, semantics, workload_mode='research')
                    for key in ('metrics', 'structured', 'unstructured',
                                'graph_node_delta', 'node_reduction_percent',
                                'common_support_area_m2', 'spatial_zones'):
                        self.assertEqual(bounded[key], research[key], key)

    def test_research_preflight_is_metadata_only_without_geometry_or_window_scan(self):
        with patch('gis_mesh_workbench.comparison.STRtree', side_effect=AssertionError('index')), \
                patch('gis_mesh_workbench.comparison.Polygon', side_effect=AssertionError('polygon')), \
                patch('gis_mesh_workbench.comparison_admission.windows', side_effect=AssertionError('windows')), \
                patch.object(rasterio.io.DatasetReader, 'read', side_effect=AssertionError('band read')):
            result = preflight_comparison(self.mesh, self.raster,
                analysis_crs=self.crs, workload_mode='research')
        self.assertEqual(result['estimate_kind'], 'metadata_upper_bound')
        self.assertFalse(result['complete'])
        self.assertIsNone(result['aggregate_pixels_read'])
        self.assertEqual(result['aggregate_pixels_read_upper_bound'], 8)

    def test_advisory_window_denominator_matches_tiled_and_striped_runtime(self):
        from gis_mesh_workbench.comparison_admission import metadata_workload_advisory
        from gis_mesh_workbench.indexed_extraction import windows
        from rasterio.crs import CRS
        for tiled in (False, True):
            options = dict(tiled=True, blockxsize=16, blockysize=16) if tiled else {}
            with rasterio.open(self.raster, 'w', driver='GTiff', width=35, height=33,
                    count=1, dtype='uint8', crs=self.crs,
                    transform=Affine(1, 0, 500000, 0, -1, 1000002), **options) as dst:
                dst.write(np.ones((33, 35), dtype='uint8'), 1)
            for window_pixels in (4, 65536):
                cap = dict(CEILINGS, max_window_pixels=window_pixels, max_source_pixels=None)
                with rasterio.open(self.raster) as src:
                    estimate = metadata_workload_advisory(src, CRS.from_string(self.crs),
                        band=1, semantics='intensive', cap=cap)
                    actual = len(list(windows(src, 1, cap)))
                self.assertEqual(estimate['windows_scanned_per_pass'], actual)

    def test_research_cross_crs_preserves_conservative_support(self):
        from rasterio.warp import transform
        x, y = transform('EPSG:4326', 'EPSG:3857', [111, 111.00002], [9, 9.00002])
        self.crs = 'EPSG:3857'
        a, b, c, d = (x[0], y[0]), (x[1], y[0]), (x[1], y[1]), (x[0], y[1])
        self.cells = (Cell('cross-a', 'triangle', (a, b, c)),
                      Cell('cross-b', 'triangle', (a, c, d)))
        self.freeze()
        with rasterio.open(self.raster, 'w', driver='GTiff', width=2, height=2,
                count=1, dtype='float64', crs='EPSG:4326',
                transform=Affine(.00001, 0, 111, 0, -.00001, 9.00002)) as dst:
            dst.write(np.full((2, 2), 17.), 1)
        for baseline in ('target_grid', 'native_raster'):
            bounded = self.run_comparison(baseline)
            research = self.run_comparison(baseline, workload_mode='research')
            self.assertEqual(bounded['metrics'], research['metrics'])
            self.assertEqual(bounded['common_support_area_m2'], research['common_support_area_m2'])
            self.assertEqual(research['workload']['aggregate']['pixels_read'], 8)
            self.assertEqual(research['workload']['admission']['window_culling'],
                             'disabled_cross_crs_conservative')

    def test_research_runtime_stop_and_memory_guards_remain(self):
        import gis_mesh_workbench.comparison as comparison
        original = comparison.geometry_batch
        control = JobControl()
        def stop_inside_geometry(*args, **kwargs):
            control.request_cancel()
            return original(*args, **kwargs)
        with job_scope(control), patch('gis_mesh_workbench.comparison.geometry_batch', stop_inside_geometry):
            with self.assertRaises(Cancelled):
                self.run_comparison('native_raster', workload_mode='research')
        with patch('gis_mesh_workbench.comparison.MAX_CLASS_ENTRIES', 1):
            with self.assertRaisesRegex(ValueError, 'categorical accumulator limit'):
                self.run_comparison('native_raster', 'categorical', workload_mode='research')

    def test_research_keeps_cancellation_and_mode_validation(self):
        for mode in (None, True, 1, 'unlimited', {}, []):
            with self.subTest(mode=mode), self.assertRaisesRegex(ValueError, 'workload mode'):
                self.run_comparison('native_raster', workload_mode=mode)
        control = JobControl()
        control.request_cancel()
        with job_scope(control), self.assertRaises(Cancelled):
            preflight_comparison(self.mesh, self.raster,
                analysis_crs=self.crs, workload_mode='research')

    def test_research_does_not_relax_shared_extraction_limits(self):
        with self.assertRaisesRegex(ValueError, 'cannot be raised'):
            limits({'max_pixels_read': None})

    def test_research_keeps_large_integer_categories(self):
        values = np.array([[2**63+17, 2**63+29], [2**63+17, 2**63+29]], dtype='uint64')
        with rasterio.open(self.raster, 'w', driver='GTiff', width=2, height=2,
                count=1, dtype='uint64', crs=self.crs,
                transform=Affine(1, 0, 500000, 0, -1, 1000002)) as dst:
            dst.write(values, 1)
        bounded = self.run_comparison('native_raster', 'categorical')
        research = self.run_comparison('native_raster', 'categorical', workload_mode='research')
        self.assertEqual(bounded['metrics'], research['metrics'])
        self.assertIn(str(2**63+17), research['metrics']['unstructured']['source_class_fractions'])


if __name__ == '__main__':
    unittest.main()
