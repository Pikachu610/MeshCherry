"""Adversarial equivalence tests for native pixel inventory counting.

The oracle intentionally keeps the original scalar predicate: build the
declared source-pixel footprint, intersect it with the domain, and count only
strictly positive intersection area.
"""
from contextlib import contextmanager
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from affine import Affine
import numpy as np
import rasterio
from rasterio.warp import transform as transform_coordinates
from shapely.geometry import MultiPolygon, Polygon, box

from gis_mesh_workbench.comparison_inventory import _native_pixel_count
from gis_mesh_workbench.indexed_extraction import footprint


class NativePixelInventoryFastTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.raster = Path(self.temp.name) / 'pixels.tif'

    def write(self, *, width=8, height=8, transform=None, crs='EPSG:32649',
              nodata=None, fill_value=1):
        transform = transform or Affine(1, 0, 500000, 0, -1, 1000008)
        with rasterio.open(self.raster, 'w', driver='GTiff', width=width,
                height=height, count=1, dtype='uint8', crs=crs,
                transform=transform, nodata=nodata) as dst:
            dst.write(np.full((height, width), fill_value, dtype='uint8'), 1)

    def scalar_oracle(self, domain, analysis_crs):
        """Reproduce the old all-candidate, per-pixel geometry calculation."""
        count = 0
        with rasterio.open(self.raster) as src:
            target = rasterio.crs.CRS.from_user_input(analysis_crs)
            for row in range(src.height):
                for col in range(src.width):
                    pixel = footprint(src, target, col, row, subdivisions=8)
                    if pixel.intersection(domain).area > 0:
                        count += 1
        return count

    def actual(self, domain, analysis_crs, progress=None):
        return _native_pixel_count(self.raster, domain, analysis_crs, progress)

    def assert_matches_oracle(self, domain, analysis_crs='EPSG:32649'):
        expected = self.scalar_oracle(domain, analysis_crs)
        actual, _ = self.actual(domain, analysis_crs)
        self.assertEqual(actual, expected)
        return actual

    def test_holes_and_multipart_preserve_union_geometry(self):
        self.write(width=16, height=12,
            transform=Affine(1, 0, 500000, 0, -1, 1000012))
        holed = Polygon([(500001, 1000001), (500009, 1000001),
                         (500009, 1000011), (500001, 1000011)],
                        holes=[[(500003, 1000003), (500007, 1000003),
                                (500007, 1000007), (500003, 1000007)]])
        island = box(500011, 1000002, 500015, 1000010)
        domain = MultiPolygon([holed, island])
        self.assert_matches_oracle(domain)

    def test_tiny_positive_slivers_and_exact_edge_touches_use_strict_area(self):
        self.write(width=8, height=8)
        # A positive-area sliver just across a pixel edge must count that pixel.
        sliver = box(500003 - 1e-12, 1000002.25, 500003.25, 1000002.75)
        self.assert_matches_oracle(sliver)
        # A domain whose edge lies exactly on a pixel edge only touches the
        # adjacent outside column; that column must not be counted.
        exact = box(500002, 1000002, 500004, 1000004)
        self.assert_matches_oracle(exact)
        self.assertEqual(self.scalar_oracle(exact, 'EPSG:32649'), 4)

    def test_ulp_scale_boundary_perturbations_match_original_world_predicate(self):
        self.write(width=8, height=8)
        for delta in (-1e-10, 0.0, 1e-10):
            with self.subTest(delta=delta):
                domain = box(500004 + delta, 1000002.2,
                             500006.4, 1000005.7)
                self.assert_matches_oracle(domain)

    def test_rotated_sheared_and_large_coordinate_affines_match_scalar_fallback(self):
        cases = (
            Affine(1, .25, 500000, .15, -1, 1000008),
            Affine(2, -.2, 8_000_000_000.0, .1, -1.5, -7_000_000_000.0),
        )
        for transform in cases:
            with self.subTest(transform=transform):
                self.write(width=8, height=8, transform=transform)
                corners = [transform @ p for p in ((1.4, 1.2), (6.2, 1.2),
                           (6.2, 6.4), (1.4, 6.4))]
                domain = Polygon(corners)
                from gis_mesh_workbench import comparison_inventory
                with patch('gis_mesh_workbench.comparison_inventory.hierarchical_count',
                           wraps=comparison_inventory.hierarchical_count) as optimized:
                    self.assert_matches_oracle(domain)
                optimized.assert_not_called()

    def test_cross_crs_keeps_eight_subdivision_scalar_footprint_model(self):
        source_transform = Affine(.001, 0, 108, 0, -.001, 9.01)
        self.write(width=8, height=8, transform=source_transform,
                   crs='EPSG:4326')
        xs, ys = transform_coordinates('EPSG:4326', 'EPSG:32649',
            [108.0012, 108.0068, 108.0068, 108.0012],
            [9.0022, 9.0022, 9.0078, 9.0078])
        domain = Polygon(zip(xs, ys))
        from gis_mesh_workbench import comparison_inventory
        with patch('gis_mesh_workbench.comparison_inventory.hierarchical_count',
                   wraps=comparison_inventory.hierarchical_count) as optimized:
            self.assert_matches_oracle(domain)
        optimized.assert_not_called()
        _, metadata = self.actual(domain, 'EPSG:32649')
        self.assertTrue(metadata['footprint_approximate'])
        self.assertEqual(metadata['edge_subdivisions'], 8)

    def test_metadata_only_count_never_reads_band_or_mask(self):
        self.write(width=32, height=32, nodata=1, fill_value=1)
        original_open = rasterio.open

        class MetadataOnly:
            def __init__(self, dataset):
                self._dataset = dataset

            def __getattr__(self, name):
                if name in {'read', 'read_masks', 'read_mask'}:
                    raise AssertionError(f'count accessed raster values via {name}')
                return getattr(self._dataset, name)

        @contextmanager
        def guarded_open(*args, **kwargs):
            with original_open(*args, **kwargs) as dataset:
                yield MetadataOnly(dataset)

        with patch('gis_mesh_workbench.comparison_inventory.open_raster', guarded_open):
            count, _ = self.actual(box(500000, 1000000, 500032, 1000008),
                                   'EPSG:32649')
        self.assertEqual(count, 32 * 8)

    def test_covering_interior_tiles_avoid_per_pixel_footprint_work(self):
        self.write(width=256, height=256,
            transform=Affine(1, 0, 500000, 0, -1, 1000256))
        domain = box(500000, 1000000, 500256, 1000256)
        observations = []
        with patch('gis_mesh_workbench.pixel_inventory.footprint',
                   wraps=footprint) as make_footprint:
            count, _ = self.actual(domain, 'EPSG:32649',
                lambda *item: observations.append(item))
        self.assertEqual(count, 256 * 256)
        self.assertLess(make_footprint.call_count, 256)
        self.assertTrue(observations)
        self.assertEqual(observations[-1],
            ('count_native_pixels', 256 * 256, 256 * 256, 'source_pixels_examined'))
        completed = [item[1] for item in observations]
        self.assertEqual(completed, sorted(completed))
        self.assertTrue(all(item[2] == 256 * 256 for item in observations))

    def test_cancel_from_progress_is_propagated_during_mixed_boundary_work(self):
        from gis_mesh_workbench.job_control import Cancelled, JobControl, job_scope

        self.write(width=128, height=128,
            transform=Affine(1, 0, 500000, 0, -1, 1000128))
        # A diagonal boundary prevents every source tile from being accepted
        # wholesale and gives the progress callback a point to cancel.
        domain = Polygon([(500000, 1000000), (500128, 1000128),
                          (500000, 1000128)])
        control = JobControl()
        observations = []

        def cancel_after_progress(*item):
            observations.append(item)
            if item[0] == 'count_native_pixels':
                control.request_cancel()

        with job_scope(control):
            with self.assertRaises(Cancelled):
                self.actual(domain, 'EPSG:32649', cancel_after_progress)
        self.assertTrue(observations)

    def test_final_progress_callback_cancellation_is_not_lost(self):
        from gis_mesh_workbench.job_control import Cancelled, JobControl, job_scope

        self.write(width=256, height=256,
            transform=Affine(1, 0, 500000, 0, -1, 1000256))
        domain = box(500000, 1000000, 500256, 1000256)
        control = JobControl()
        observations = []

        def cancel_on_final_progress(*item):
            observations.append(item)
            if item[0] == 'count_native_pixels' and item[1] == item[2]:
                control.request_cancel()

        with job_scope(control):
            with self.assertRaises(Cancelled):
                self.actual(domain, 'EPSG:32649', cancel_on_final_progress)
        self.assertTrue(observations)
        self.assertEqual(observations[-1][1], observations[-1][2])

    def test_collapsed_resolution_falls_back_to_scalar_footprint_error(self):
        transform = Affine(1e-7, 0, 8_000_000_000.0,
                           0, -1e-7, -7_000_000_000.0)
        self.write(width=4, height=4, transform=transform)
        from gis_mesh_workbench.pixel_inventory import supports_hierarchy
        with rasterio.open(self.raster) as src:
            self.assertFalse(supports_hierarchy(
                src, rasterio.crs.CRS.from_epsg(32649)))
        domain = box(7_999_999_999.0, -7_000_000_001.0,
                    8_000_000_001.0, -6_999_999_999.0)
        with self.assertRaisesRegex(ValueError, 'invalid pixel/window footprint'):
            self.actual(domain, 'EPSG:32649')

    def test_large_origin_above_resolution_guard_matches_scalar_predicate(self):
        transform = Affine(8e-6, 0, 8_000_000_000.0,
                           0, -8e-6, -7_000_000_000.0)
        self.write(width=8, height=8, transform=transform)
        from gis_mesh_workbench.pixel_inventory import supports_hierarchy
        with rasterio.open(self.raster) as src:
            target = rasterio.crs.CRS.from_epsg(32649)
            self.assertTrue(supports_hierarchy(src, target))
        left, top = transform @ (2, 2)
        right, bottom = transform @ (6, 6)
        self.assert_matches_oracle(box(left + 1e-5, bottom + 1e-5,
                                       right - 1e-5, top - 1e-5))

    def test_nonfinite_affine_is_rejected_before_geometric_work(self):
        from contextlib import nullcontext
        from types import SimpleNamespace

        fake = SimpleNamespace(crs=rasterio.crs.CRS.from_epsg(32649),
            transform=Affine(float('nan'), 0, 500000, 0, -1, 1000008),
            width=8, height=8)
        with patch('gis_mesh_workbench.comparison_inventory.open_raster',
                   return_value=nullcontext(fake)):
            with self.assertRaisesRegex(ValueError, 'finite nonsingular raster affine'):
                self.actual(box(500000, 1000000, 500008, 1000008),
                            'EPSG:32649')


if __name__ == '__main__':
    unittest.main()
