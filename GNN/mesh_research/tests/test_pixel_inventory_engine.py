"""Differential checks for hierarchical native pixel inventory counting."""
import unittest
from unittest.mock import patch

from affine import Affine
from rasterio.io import MemoryFile
from shapely.geometry import box
from shapely.ops import unary_union

from gis_mesh_workbench.indexed_extraction import footprint
from gis_mesh_workbench.job_control import Cancelled
from gis_mesh_workbench.pixel_inventory import hierarchical_count, supports_hierarchy


class PixelInventoryEngineTests(unittest.TestCase):
    def setUp(self):
        self.memory = MemoryFile()
        self.addCleanup(self.memory.close)
        self.dataset = self.memory.open(driver='GTiff', width=24, height=20,
            count=1, dtype='uint8', crs='EPSG:32649',
            transform=Affine(2, 0, 500000, 0, -3, 1000000))
        self.addCleanup(self.dataset.close)

    def scalar(self, domain):
        return sum(footprint(self.dataset, self.dataset.crs, col, row)
            .intersection(domain).area > 0
            for row in range(self.dataset.height)
            for col in range(self.dataset.width))

    def test_matches_scalar_for_holes_multipart_and_grid_edge_slivers(self):
        shell_with_hole = box(500004, 999970, 500026, 999994).difference(
            box(500010, 999978, 500018, 999986))
        separated = unary_union((shell_with_hole,
            box(500030.000000001, 999976, 500036, 999988)))
        thin_sliver = box(500013.999999999, 999960, 500014.000000003, 999966)
        for domain in (shell_with_hole, separated, unary_union((separated, thin_sliver))):
            with self.subTest(parts=domain.geom_type, area=domain.area):
                expected = self.scalar(domain)
                actual = hierarchical_count(self.dataset, domain, self.dataset.crs,
                    (0, 0, self.dataset.width, self.dataset.height))
                self.assertEqual(actual, expected)

    def test_progress_accounts_each_candidate_exactly_once(self):
        domain = box(500004.25, 999960.5, 500035.75, 999987.5)
        events = []
        total = self.dataset.width*self.dataset.height
        hierarchical_count(self.dataset, domain, self.dataset.crs,
            (0, 0, self.dataset.width, self.dataset.height),
            lambda *event: events.append(event))
        self.assertTrue(events)
        self.assertEqual(events[-1], ('count_native_pixels', total, total,
                                      'source_pixels_examined'))
        completed = [event[1] for event in events]
        self.assertEqual(completed, sorted(set(completed)))
        self.assertTrue(all(event[2] == total for event in events))

    def test_fully_covered_interior_uses_tile_shortcut(self):
        domain = box(500000, 999940, 500048, 1000000)
        with patch('gis_mesh_workbench.pixel_inventory.footprint', wraps=footprint) as exact:
            actual = hierarchical_count(self.dataset, domain, self.dataset.crs,
                (0, 0, self.dataset.width, self.dataset.height))
        self.assertEqual(actual, self.dataset.width*self.dataset.height)
        self.assertEqual(exact.call_count, 0)

    def test_large_origin_and_sub_ulp_resolution_falls_back_to_scalar_error(self):
        with MemoryFile() as memory:
            src = memory.open(driver='GTiff', width=4, height=4, count=1,
                dtype='uint8', crs='EPSG:32649',
                transform=Affine(.5, 0, 1e16, 0, -.5, 1e16))
            with src:
                self.assertFalse(supports_hierarchy(src, src.crs))
                with self.assertRaisesRegex(ValueError, 'invalid pixel/window footprint'):
                    footprint(src, src.crs, 0, 0)

    def test_cancellation_is_checked_during_recursive_classification(self):
        calls = 0

        def cancel_after_bounded_work():
            nonlocal calls
            calls += 1
            if calls == 5:
                raise Cancelled('test cancellation')

        with patch('gis_mesh_workbench.pixel_inventory.checkpoint', cancel_after_bounded_work), \
                self.assertRaisesRegex(Cancelled, 'test cancellation'):
            hierarchical_count(self.dataset, box(500001, 999950, 500047, 999997),
                self.dataset.crs, (0, 0, self.dataset.width, self.dataset.height))
        self.assertEqual(calls, 5)


if __name__ == '__main__':
    unittest.main()
