import json
import math
import unittest

import numpy as np

from gis_mesh_workbench.quality_assessment import evaluate_mesh_quality
from gis_mesh_workbench.quality_assessment import quality_config_hash, quality_mesh_hash, triangle_metrics
from gis_mesh_workbench.job_control import JobControl, job_scope


def mesh(points, *, cell_id="T1", kind="triangle"):
    return {"coordinate_units": "m", "cells": [{"cell_id": cell_id, "kind": kind, "xy": points}]}


def tri(result, element_id="T1"):
    return next(row for row in result["elements"] if row["element_id"] == element_id)


def codes(result):
    return {issue["code"] for issue in result["issues"]}


class QualityAssessmentTests(unittest.TestCase):
    def test_hash_helpers_match_result_and_distinguish_nonfinite_payloads(self):
        payload = mesh([[0, 0], [1, 0], [0, 1]])
        result = evaluate_mesh_quality(payload)
        self.assertEqual(result["mesh_hash"], quality_mesh_hash(payload))
        self.assertEqual(result["config_hash"], quality_config_hash())
        self.assertNotEqual(quality_mesh_hash({"x": math.nan}), quality_mesh_hash({"x": None}))
        self.assertIsNotNone(triangle_metrics([[0, 0], [1, 0], [0, 1]]))
        self.assertIsNone(triangle_metrics([[0, 0], [1, 0], [2, 0]]))

    def test_existing_job_cancellation_returns_cancelled_schema(self):
        control = JobControl()
        control.request_cancel()
        with job_scope(control):
            result = evaluate_mesh_quality(mesh([[0, 0], [1, 0], [0, 1]]))
        self.assertEqual(result["status"], "CANCELLED")
        json.dumps(result, allow_nan=False)

    def test_equilateral_triangle_known_answer(self):
        result = evaluate_mesh_quality(mesh([[0, 0], [1, 0], [.5, math.sqrt(3) / 2]]))
        row = tri(result)
        self.assertAlmostEqual(row["area"], math.sqrt(3) / 4, places=14)
        self.assertAlmostEqual(row["min_angle_deg"], 60, places=12)
        self.assertAlmostEqual(row["max_angle_deg"], 60, places=12)
        self.assertAlmostEqual(row["aspect_ratio"], 2 / math.sqrt(3), places=14)
        self.assertAlmostEqual(row["quality_q"], 1, places=14)
        self.assertEqual(result["status"], "PARTIAL")
        self.assertEqual(result["boundary"]["status"], "NOT_AVAILABLE")
        json.dumps(result, allow_nan=False)

    def test_right_isosceles_triangle_known_answer(self):
        row = tri(evaluate_mesh_quality(mesh([[0, 0], [1, 0], [0, 1]])))
        self.assertAlmostEqual(row["area"], .5, places=14)
        self.assertAlmostEqual(row["min_angle_deg"], 45, places=12)
        self.assertAlmostEqual(row["max_angle_deg"], 90, places=12)
        self.assertAlmostEqual(row["aspect_ratio"], 2, places=14)
        self.assertAlmostEqual(row["quality_q"], 0.8284271247461901, places=14)

    def test_degenerate_has_no_shape_metrics_and_fails(self):
        result = evaluate_mesh_quality(mesh([[0, 0], [1, 0], [2, 0]]))
        row = tri(result)
        self.assertEqual(result["status"], "FAIL")
        self.assertFalse(row["valid"])
        self.assertIsNone(row["quality_q"])
        self.assertIn("DEGENERATE_TRIANGLE", codes(result))
        json.dumps(result, allow_nan=False)

    def test_near_degenerate_relative_tolerance_across_scales(self):
        for scale, offset in ((1.0, 0.0), (1e3, 0.0), (1.0, 1e6)):
            with self.subTest(scale=scale, offset=offset):
                result = evaluate_mesh_quality(mesh([[offset, offset], [offset + scale, offset],
                                                     [offset + .5 * scale, offset + 1e-14 * scale]]))
                self.assertFalse(tri(result)["valid"])
                self.assertIn("DEGENERATE_TRIANGLE", codes(result))

    def test_translation_rotation_scale_and_vertex_permutation_invariance(self):
        base = np.array([[.2, -.4], [2.1, .3], [-.1, 1.7]], dtype=float)
        first = tri(evaluate_mesh_quality(mesh(base.tolist())))
        angle = .731
        rotation = np.array([[math.cos(angle), -math.sin(angle)],
                             [math.sin(angle), math.cos(angle)]])
        transformed = (base @ rotation.T) * 17 + [1e6, -2e6]
        second = tri(evaluate_mesh_quality(mesh(transformed.tolist())))
        permuted = tri(evaluate_mesh_quality(mesh(transformed[[1, 0, 2]].tolist())))
        for key in ("quality_q", "aspect_ratio", "min_angle_deg", "max_angle_deg"):
            self.assertAlmostEqual(first[key], second[key], places=9)
            self.assertAlmostEqual(second[key], permuted[key], places=9)
        self.assertAlmostEqual(second["area"] / first["area"], 17 ** 2, places=7)
        self.assertAlmostEqual(second["longest_edge"] / first["longest_edge"], 17, places=7)
        self.assertAlmostEqual(second["signed_area"], -permuted["signed_area"], places=7)

    def test_shared_edge_builds_two_element_component(self):
        payload = {"cells": [
            {"cell_id": "a", "kind": "triangle", "xy": [[0, 0], [1, 0], [0, 1]]},
            {"cell_id": "b", "kind": "triangle", "xy": [[1, 0], [1, 1], [0, 1]]},
        ]}
        result = evaluate_mesh_quality(payload)
        self.assertEqual(result["validation"]["non_overlapping"]["status"], "PASS")
        self.assertEqual(result["summary"]["interior_edge_count"], 1)
        self.assertEqual(result["summary"]["connected_component_count"], 1)

    def test_vertex_touch_is_not_adjacency_or_overlap(self):
        payload = {"cells": [
            {"cell_id": "a", "kind": "triangle", "xy": [[0, 0], [1, 0], [0, 1]]},
            {"cell_id": "b", "kind": "triangle", "xy": [[1, 1], [2, 1], [1, 2]]},
        ]}
        result = evaluate_mesh_quality(payload)
        self.assertEqual(result["validation"]["non_overlapping"]["status"], "PASS")
        self.assertEqual(result["summary"]["connected_component_count"], 2)
        self.assertNotIn("MESH_OVERLAP", codes(result))

    def test_duplicate_element_independent_of_vertex_order(self):
        payload = {"cells": [
            {"cell_id": "a", "kind": "triangle", "xy": [[0, 0], [1, 0], [0, 1]]},
            {"cell_id": "b", "kind": "triangle", "xy": [[0, 1], [0, 0], [1, 0]]},
        ]}
        result = evaluate_mesh_quality(payload)
        self.assertIn("DUPLICATE_ELEMENT", codes(result))
        self.assertEqual(result["status"], "FAIL")

    def test_positive_area_overlap_is_reported_with_ids_and_area(self):
        payload = {"cells": [
            {"cell_id": "a", "kind": "triangle", "xy": [[0, 0], [2, 0], [0, 2]]},
            {"cell_id": "b", "kind": "triangle", "xy": [[.5, .5], [2, .5], [.5, 2]]},
        ]}
        result = evaluate_mesh_quality(payload)
        issue = next(i for i in result["issues"] if i["code"] == "MESH_OVERLAP")
        self.assertEqual(issue["element_ids"], ["a", "b"])
        self.assertGreater(issue["details"]["intersection_area"], 0)
        self.assertEqual(result["validation"]["non_overlapping"]["status"], "FAIL")

    def test_nonmanifold_edge_is_reported(self):
        payload = {"cells": [
            {"cell_id": "a", "kind": "triangle", "xy": [[0, 0], [1, 0], [0, 1]]},
            {"cell_id": "b", "kind": "triangle", "xy": [[1, 0], [0, 0], [0, -1]]},
            {"cell_id": "c", "kind": "triangle", "xy": [[0, 0], [1, 0], [1, 1]]},
        ]}
        result = evaluate_mesh_quality(payload)
        self.assertIn("NONMANIFOLD_EDGE", codes(result))
        self.assertEqual(result["validation"]["topologically_conforming"]["status"], "FAIL")

    def test_hanging_vertex_t_junction_is_reported(self):
        payload = {"cells": [
            {"cell_id": "upper", "kind": "triangle", "xy": [[0, 0], [2, 0], [1, 2]]},
            {"cell_id": "lower_left", "kind": "triangle", "xy": [[0, 0], [1, -1], [1, 0]]},
            {"cell_id": "lower_right", "kind": "triangle", "xy": [[1, 0], [2, 0], [1, -1]]},
        ]}
        result = evaluate_mesh_quality(payload)
        self.assertIn("T_JUNCTION", codes(result))
        self.assertEqual(result["validation"]["topologically_conforming"]["status"], "FAIL")

    def test_explicit_connectivity_reports_missing_and_orphan_nodes(self):
        payload = {"nodes": [
            {"node_id": 1, "xy": [0, 0]}, {"node_id": 2, "xy": [1, 0]},
            {"node_id": 3, "xy": [0, 1]}, {"node_id": 4, "xy": [2, 2]},
        ], "elements": [
            {"element_id": "ok", "kind": "triangle", "node_ids": [1, 2, 3]},
            {"element_id": "dangling", "kind": "triangle", "node_ids": [1, 2, 99]},
        ]}
        result = evaluate_mesh_quality(payload)
        self.assertIn("MISSING_NODE_REFERENCE", codes(result))
        self.assertIn("ORPHAN_NODE", codes(result))
        self.assertEqual(result["summary"]["orphan_node_count"], 1)
        self.assertEqual(result["status"], "FAIL")

    def test_coordinate_payload_marks_original_node_checks_unavailable(self):
        result = evaluate_mesh_quality(mesh([[0, 0], [1, 0], [0, 1]]))
        self.assertIsNone(result["summary"]["orphan_node_count"])
        self.assertEqual(result["validation"]["structurally_valid"]["orphan_nodes"], "NOT_AVAILABLE")

    def test_quad_triangle_shape_metrics_are_not_applicable(self):
        result = evaluate_mesh_quality(mesh([[0, 0], [1, 0], [1, 1], [0, 1]], kind="rectangle"))
        row = tri(result)
        self.assertEqual(row["triangle_metric_status"], "NOT_APPLICABLE")
        self.assertIsNone(row["quality_q"])

    def test_duplicate_and_nonfinite_ids_and_coordinates_fail_cleanly(self):
        payload = {"cells": [
            {"cell_id": "same", "kind": "triangle", "xy": [[0, 0], [1, 0], [0, 1]]},
            {"cell_id": "same", "kind": "triangle", "xy": [[2, 0], [3, 0], [2, 1]]},
            {"cell_id": "bad", "kind": "triangle", "xy": [[0, 0], [math.inf, 0], [0, 1]]},
        ]}
        result = evaluate_mesh_quality(payload)
        self.assertIn("DUPLICATE_ELEMENT_ID", codes(result))
        self.assertIn("NONFINITE_COORDINATE", codes(result))
        json.dumps(result, allow_nan=False)

    def test_overlaps_use_bounded_index_candidates(self):
        payload = {"cells": [
            {"cell_id": str(i), "kind": "triangle", "xy": [[0, 0], [1, 0], [0, 1]]}
            for i in range(6)
        ]}
        result = evaluate_mesh_quality(payload, config={"max_overlap_candidates": 1})
        self.assertIn("OVERLAP_CANDIDATE_BUDGET_EXCEEDED", codes(result))
        self.assertEqual(result["validation"]["non_overlapping"]["status"], "ERROR")

    def test_issue_cap_is_bounded(self):
        payload = {"cells": [{"cell_id": i, "kind": "triangle", "xy": [[0, 0], [1, 0], [2, 0]]}
                            for i in range(10)]}
        result = evaluate_mesh_quality(payload, config={"max_issues": 2})
        self.assertEqual(len(result["issues"]), 2)
        self.assertTrue(result["summary"]["issues_truncated"])


if __name__ == "__main__":
    unittest.main()
