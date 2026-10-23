"""Validated opt-in derived-boundary simplification tests."""

import json
import unittest

from shapely.geometry import MultiPolygon, Point, Polygon

from gis_mesh_workbench.derived_boundary import (
    _densified_ring,
    _densified_symmetric_distance,
    prepare_boundary,
    simplify_derived_boundary,
)


class DerivedBoundaryTests(unittest.TestCase):
    def source(self):
        detailed = Polygon(
            [(0, 0), (2, 0.08), (4, 0), (6, -0.08), (8, 0), (8, 8), (0, 8), (0, 0)],
            holes=[[(1, 2), (2, 2), (2, 3), (1.5, 3.08), (1, 3), (1, 2)]],
        )
        second = Polygon([(10, 0), (12, 0), (14, 0.08), (16, 0), (16, 4), (10, 4), (10, 0)])
        return MultiPolygon([detailed, second])

    def derive(self, source, **overrides):
        options = dict(
            tolerance_m=0.2,
            max_area_change_fraction=0.1,
            max_boundary_distance_m=0.25,
            sampling_interval_m=0.01,
            units="m",
        )
        options.update(overrides)
        return simplify_derived_boundary(source, **options)

    def test_simplifies_without_mutating_or_changing_components_and_holes(self):
        source = self.source()
        original_wkb = source.wkb
        first = self.derive(source)
        second = self.derive(source)

        self.assertEqual(source.wkb, original_wkb)
        self.assertEqual(first.geometry.geom_type, "MultiPolygon")
        self.assertEqual(len(first.geometry.geoms), 2)
        self.assertEqual(sum(len(part.interiors) for part in first.geometry.geoms), 1)
        self.assertLess(first.metadata["derived"]["vertex_count"], first.metadata["source"]["vertex_count"])
        self.assertEqual(first.metadata["source"]["geometry_sha256"], second.metadata["source"]["geometry_sha256"])
        self.assertEqual(first.metadata["derived"]["geometry_sha256"], second.metadata["derived"]["geometry_sha256"])
        self.assertTrue(first.metadata["accepted"])
        self.assertIsInstance(first.metadata["derived"]["hole_count"], int)
        self.assertGreater(first.metadata["added_area_m2"], 0)
        self.assertGreater(first.metadata["removed_area_m2"], 0)
        json.dumps(first.metadata, allow_nan=False)
        self.assertLessEqual(
            first.metadata["boundary_distance"]["upper_bound_m"],
            first.metadata["limits"]["max_boundary_distance_m"],
        )

    def test_rejects_area_gate(self):
        with self.assertRaisesRegex(ValueError, "tỷ lệ diện tích sai khác đối xứng"):
            self.derive(self.source(), max_area_change_fraction=0.0)

    def test_rejects_boundary_distance_gate(self):
        with self.assertRaisesRegex(ValueError, "cận trên khoảng cách biên đối xứng"):
            self.derive(self.source(), max_boundary_distance_m=0.0, max_area_change_fraction=1.0)

    def test_enforces_area_limit_on_each_component(self):
        large = Polygon([(0, 0), (100, 0), (100, 100), (0, 100), (0, 0)])
        small_detailed = Polygon(
            [(200, 0), (202, 0), (204, 0.08), (206, 0), (208, 0),
             (208, 8), (200, 8), (200, 0)]
        )
        with self.assertRaisesRegex(ValueError, "tỷ lệ sai khác diện tích của một thành phần"):
            self.derive(
                MultiPolygon([large, small_detailed]),
                max_area_change_fraction=0.0001,
                max_boundary_distance_m=0.25,
            )

    def test_enforces_area_limit_on_each_hole(self):
        shell = Polygon(
            [(0, 0), (1000, 0), (1000, 1000), (0, 1000), (0, 0)],
            holes=[[(10, 10), (11, 10), (12, 10.8), (13, 10), (14, 10), (14, 14), (10, 14), (10, 10)]],
        )
        with self.assertRaisesRegex(ValueError, "tỷ lệ sai khác diện tích của một lỗ"):
            self.derive(
                shell,
                tolerance_m=1.0,
                max_area_change_fraction=0.0001,
                max_boundary_distance_m=1.0,
                sampling_interval_m=0.1,
            )

    def test_rejects_excessive_densified_sample_count(self):
        with self.assertRaisesRegex(ValueError, "vượt ngân sách 500,000"):
            self.derive(self.source(), sampling_interval_m=1e-9)

    def test_requires_explicit_projected_metre_unit_contract(self):
        with self.assertRaisesRegex(ValueError, "tọa độ chiếu theo mét"):
            self.derive(self.source(), units="degree")

    def test_runner_adapter_uses_its_derived_sampling_interval(self):
        result = prepare_boundary(
            self.source(),
            tolerance_m=0.2,
            max_area_change_fraction=0.1,
            max_boundary_distance_m=0.25,
        )
        self.assertEqual(result.metadata["units"], "m")
        self.assertEqual(result.metadata["boundary_distance"]["sampling_interval_m"], 0.125)

    def test_no_hole_polygon_metadata_is_json_serializable_with_integer_count(self):
        source = Polygon([(0, 0), (4, 0.08), (8, 0), (8, 8), (0, 8), (0, 0)])
        result = self.derive(source)
        self.assertEqual(result.metadata["derived"]["hole_count"], 0)
        self.assertIsInstance(result.metadata["derived"]["hole_count"], int)
        json.dumps(result.metadata, allow_nan=False)

    def test_indexed_distances_match_naive_point_to_boundary_distances(self):
        source = Polygon([(0, 0), (4, 0.3), (8, 0), (8, 8), (0, 8), (0, 0)])
        derived = source.simplify(0.5, preserve_topology=True)
        interval = 0.37
        result = _densified_symmetric_distance(source, derived, interval)
        source_samples = [point for ring in (source.exterior,) for point in _densified_ring(ring, interval)]
        derived_samples = [point for ring in (derived.exterior,) for point in _densified_ring(ring, interval)]
        expected_source_to_derived = max(derived.boundary.distance(Point(*point)) for point in source_samples)
        expected_derived_to_source = max(source.boundary.distance(Point(*point)) for point in derived_samples)
        self.assertAlmostEqual(result["source_to_derived_m"], expected_source_to_derived, places=12)
        self.assertAlmostEqual(result["derived_to_source_m"], expected_derived_to_source, places=12)

    def test_rejects_invalid_canonical_geometry(self):
        bow_tie = Polygon([(0, 0), (2, 2), (0, 2), (2, 0), (0, 0)])
        with self.assertRaisesRegex(ValueError, "Ranh giới nguồn phải hợp lệ"):
            self.derive(bow_tie)

    def test_rejects_zero_length_source_edge(self):
        duplicate_edge = Polygon([(0, 0), (2, 0), (2, 0), (2, 2), (0, 2), (0, 0)])
        with self.assertRaisesRegex(ValueError, "cạnh dài 0"):
            self.derive(duplicate_edge)

    def test_rejects_nonpolygon_geometry(self):
        with self.assertRaisesRegex(ValueError, "Ranh giới phải là Polygon"):
            self.derive(None)

    def test_rejects_string_threshold_as_clear_value_error(self):
        with self.assertRaisesRegex(ValueError, "phải là số"):
            self.derive(self.source(), tolerance_m="0.2")


if __name__ == "__main__":
    unittest.main()
