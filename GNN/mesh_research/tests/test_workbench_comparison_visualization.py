import unittest

from shapely.geometry import MultiPolygon, box, mapping

from gis_mesh_workbench.comparison_visualization import (
    MAX_PREVIEW_FEATURES, comparison_visualization,
)


class ComparisonVisualizationTests(unittest.TestCase):
    def test_preview_is_real_geometry_bounded_and_separate_from_node_totals(self):
        structured = [box(0, 0, 1, 1) for _ in range(MAX_PREVIEW_FEATURES + 17)]
        triangles = [box(0, 0, 1, 1), box(1, 0, 2, 1)]
        domain = box(0, 0, 2, 1)

        view = comparison_visualization(domain=domain, structured=structured,
                                        unstructured=triangles)

        self.assertEqual(view['structured']['total_cell_count'], MAX_PREVIEW_FEATURES + 17)
        self.assertEqual(view['structured']['feature_count'], MAX_PREVIEW_FEATURES)
        self.assertTrue(view['structured']['sampled'])
        self.assertEqual(len(view['structured']['geometries']), MAX_PREVIEW_FEATURES)
        self.assertEqual(view['unstructured']['total_cell_count'], 2)
        self.assertEqual(view['unstructured']['feature_count'], 2)
        self.assertEqual(view['roi'], mapping(domain))
        self.assertFalse(view['source']['available'])
        self.assertTrue(view['shared_zoom'])
        self.assertIn('not graph-node counts', view['metrics_source'])

    def test_oversized_multipart_roi_outline_is_omitted_with_reason(self):
        pieces = [box(column * 4, row * 4, column * 4 + 1, row * 4 + 1)
                  for row in range(155) for column in range(155)]
        domain = MultiPolygon(pieces)

        view = comparison_visualization(domain=domain, structured=None, unstructured=[])

        self.assertFalse(view['roi_available'])
        self.assertIsNone(view['roi'])
        self.assertIn('120,000-coordinate preview limit', view['roi_reason'])
        self.assertEqual(view['bounds'], list(domain.bounds))
        self.assertTrue(view['unstructured']['available'])


if __name__ == '__main__':
    unittest.main()
