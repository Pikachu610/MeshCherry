import unittest
from unittest.mock import patch

from gis_mesh_workbench.quality_assessment import evaluate_mesh_quality


class QualityAssessmentHardeningTests(unittest.TestCase):
    def test_rectangular_area_statistics_and_invalid_histogram_counts(self):
        result=evaluate_mesh_quality(dict(cells=[dict(cell_id='grid',kind='rectangle',
            xy=[[0,0],[2,0],[2,1],[0,1]])]))
        self.assertEqual(result['statistics']['area']['mean'],2)
        self.assertEqual(result['statistics']['quality_q']['valid_count'],0)
        overlap=evaluate_mesh_quality(dict(cells=[dict(cell_id=str(i),kind='triangle',
            xy=[[0,0],[1,0],[0,1]]) for i in range(2)]))
        self.assertEqual(overlap['histograms']['area']['excluded_count'],2)

    def test_unknown_boolean_configuration_and_unhashable_connectivity_rejected(self):
        for config in ({'invented':1},{'warning_quality_q':True}):
            with self.assertRaises(ValueError):evaluate_mesh_quality({},config=config)
        result=evaluate_mesh_quality(dict(nodes=[dict(node_id='a',xy=[0,0])],
            elements=[dict(element_id='bad',kind='triangle',node_ids=[[],{},'a'])]))
        self.assertEqual(result['status'],'FAIL')

    def test_truncation_never_hides_hard_structural_failure(self):
        mesh=dict(nodes=[dict(node_id='orphan',xy=[0,0])],elements=[
            dict(element_id='bad',kind='triangle',node_ids=['missing','x','y'])])
        result=evaluate_mesh_quality(mesh,config={'max_issues':1})
        self.assertEqual(result['validation']['structurally_valid']['status'],'FAIL')

    def test_mesh_input_count_is_capped_before_evaluation(self):
        payload = {"cells": [
            {"cell_id": "a", "kind": "triangle", "xy": [[0, 0], [1, 0], [0, 1]]},
            {"cell_id": "b", "kind": "triangle", "xy": [[2, 0], [3, 0], [2, 1]]},
        ]}
        with patch("gis_mesh_workbench.quality_assessment.MAX_MESH_CELLS", 1):
            with self.assertRaisesRegex(ValueError, "cells exceeds supported limit"):
                evaluate_mesh_quality(payload)

    def test_nonmanifold_mesh_remains_fail_even_with_disconnected_component(self):
        payload = {"cells": [
            {"cell_id": "up", "kind": "triangle", "xy": [[0, 0], [1, 0], [0, 1]]},
            {"cell_id": "down", "kind": "triangle", "xy": [[1, 0], [0, 0], [0, -1]]},
            {"cell_id": "third", "kind": "triangle", "xy": [[0, 0], [1, 0], [1, 1]]},
            {"cell_id": "island", "kind": "triangle", "xy": [[4, 0], [5, 0], [4, 1]]},
        ]}
        result = evaluate_mesh_quality(payload)
        self.assertEqual(result["validation"]["topologically_conforming"]["status"], "FAIL")
        self.assertGreater(result["summary"]["connected_component_count"], 1)
        self.assertEqual(result["status"], "FAIL")


if __name__ == "__main__":
    unittest.main()
