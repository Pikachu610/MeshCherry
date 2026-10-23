"""Regression coverage for the original GNN closed-ring RDP boundary method."""

import unittest
from unittest.mock import patch

from shapely.geometry import MultiPolygon, Polygon

from gis_mesh_workbench.derived_boundary import (
    _simplify_closed_ring,
    prepare_boundary,
    simplify_derived_boundary,
)


class GnnClosedBoundaryTests(unittest.TestCase):
    def test_closed_ring_matches_original_anchor_and_farthest_arc_rdp(self):
        # Expected order/count comes from the original mesh_research routine.
        source = Polygon([
            (0, 0), (1, .1), (2, 0), (3, .2), (4, 0),
            (4, 3), (2, 3.1), (0, 3), (0, 0),
        ])
        self.assertEqual(
            _simplify_closed_ring(source.exterior, .25),
            [(0., 0.), (4., 0.), (4., 3.), (0., 3.)],
        )

    def test_method_is_opt_in_and_uses_existing_area_and_distance_gates(self):
        source = Polygon([
            (0, 0), (2, .08), (4, 0), (6, -.08), (8, 0),
            (8, 8), (0, 8), (0, 0),
        ])
        original_wkb = source.wkb
        defaults = prepare_boundary(
            source, tolerance_m=.2, max_area_change_fraction=.1,
            max_boundary_distance_m=.25,
        )
        derived = prepare_boundary(
            source, tolerance_m=.2, max_area_change_fraction=.1,
            max_boundary_distance_m=.25, method="gnn_closed_rdp",
        )
        self.assertEqual(source.wkb, original_wkb)
        self.assertEqual(defaults.metadata["method"], "topology_preserving")
        self.assertEqual(derived.metadata["method"], "gnn_closed_rdp")
        self.assertLess(derived.metadata["derived"]["vertex_count"], derived.metadata["source"]["vertex_count"])
        self.assertLessEqual(derived.metadata["symmetric_difference_fraction"], .1)
        self.assertLessEqual(derived.metadata["boundary_distance"]["upper_bound_m"], .25)

        with self.assertRaisesRegex(ValueError, "tỷ lệ diện tích sai khác đối xứng"):
            simplify_derived_boundary(
                source, tolerance_m=.2, max_area_change_fraction=0,
                max_boundary_distance_m=.25, sampling_interval_m=.125,
                units="m", method="gnn_closed_rdp",
            )

    def test_iterative_rdp_checks_cancellation_and_has_a_work_cap(self):
        ring = Polygon([(0, 0), (1, .1), (2, 0), (3, .2), (4, 0), (4, 3), (0, 3), (0, 0)])
        with patch("gis_mesh_workbench.derived_boundary.checkpoint", side_effect=RuntimeError("cancelled")):
            with self.assertRaisesRegex(RuntimeError, "cancelled"):
                _simplify_closed_ring(ring.exterior, .01)
        with patch("gis_mesh_workbench.derived_boundary.MAX_RDP_DISTANCE_CHECKS", 1):
            with self.assertRaisesRegex(ValueError, "RDP vượt ngân sách"):
                _simplify_closed_ring(ring.exterior, .01)

    def test_unknown_method_is_rejected(self):
        source = Polygon([(0, 0), (4, 0), (4, 4), (0, 4), (0, 0)])
        with self.assertRaisesRegex(ValueError, "method phải là"):
            prepare_boundary(
                source, tolerance_m=1, max_area_change_fraction=.1,
                max_boundary_distance_m=2, method="other",
            )

    def test_gnn_method_preserves_multipolygon_components_and_holes(self):
        first = Polygon(
            [(0, 0), (2, .08), (4, 0), (6, -.08), (8, 0), (8, 8), (0, 8), (0, 0)],
            holes=[[(1, 2), (2, 2.08), (3, 2), (3, 4), (1, 4), (1, 2)]],
        )
        second = Polygon(
            [(12, 0), (14, .08), (16, 0), (16, 6), (12, 6), (12, 0)],
            holes=[[(13, 2), (14, 2), (14, 3), (13.5, 3.08), (13, 3), (13, 2)]],
        )
        source = MultiPolygon([first, second])
        original_wkb = source.wkb
        result = prepare_boundary(
            source, tolerance_m=.2, max_area_change_fraction=.1,
            max_boundary_distance_m=.25, method="gnn_closed_rdp",
        )
        self.assertTrue(result.geometry.is_valid)
        self.assertEqual(source.wkb, original_wkb)
        self.assertEqual(len(result.geometry.geoms), 2)
        self.assertEqual([len(part.interiors) for part in result.geometry.geoms], [1, 1])
        self.assertEqual(result.metadata["derived"]["component_count"], 2)
        self.assertEqual(result.metadata["derived"]["hole_count"], 2)

    def test_non_string_method_is_rejected_as_value_error(self):
        source = Polygon([(0, 0), (4, 0), (4, 4), (0, 4), (0, 0)])
        with self.assertRaisesRegex(ValueError, "method phải là"):
            prepare_boundary(
                source, tolerance_m=1, max_area_change_fraction=.1,
                max_boundary_distance_m=2, method=[],
            )


if __name__ == "__main__":
    unittest.main()
