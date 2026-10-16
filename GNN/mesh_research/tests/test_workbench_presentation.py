"""Renderer selection must preserve complete scientific geometry and attributes."""
import copy
import unittest

from gis_mesh_workbench.presentation import artifact_view, display_policy


class MeshPresentationTests(unittest.TestCase):
    def test_renderer_thresholds(self):
        for count, mode in ((0, 'interactive'), (2000, 'interactive'),
                            (2001, 'batched'), (13158, 'batched'),
                            (100000, 'batched'), (100001, 'summary')):
            with self.subTest(count=count):
                policy = display_policy(count)
                self.assertEqual(policy['mode'], mode)
                self.assertEqual(policy['cell_count'], count)

    def test_large_views_retain_coordinates_values_and_coverage_without_mutation(self):
        rows = [dict(cell_id=str(i), kind='triangle',
                     xy=[[500000, 1000000], [500008, 1000000], [500000, 1000008]],
                     value=None if i == 0 else 17, footprint_coverage=1,
                     valid_coverage=0 if i == 0 else 1,
                     class_fractions={'1': 0.25, '2': 0.75}) for i in range(13158)]
        original = dict(cells=rows, crs_wkt='EPSG:32649')
        before = copy.deepcopy(original)
        for name in ('cells', 'attributes'):
            result = artifact_view(name, original)
            self.assertEqual(result['display']['mode'], 'batched')
            self.assertEqual(result['cells'], before['cells'])
            self.assertEqual(original, before)
        self.assertEqual(result['summary']['valid_count'], 13157)
        self.assertEqual(result['summary']['missing_count'], 1)

    def test_out_of_contract_guard_and_qa_view_leave_downloads_untouched(self):
        original = dict(cells=[{} for _ in range(100001)])
        self.assertEqual(artifact_view('cells', original)['cells'], [])
        self.assertEqual(len(original['cells']), 100001)
        qa = dict(mesh_quality=dict(centres=[dict(cell_id='a', xy=[1, 2])], count=1))
        self.assertEqual(artifact_view('qa', qa)['mesh_quality'], dict(count=1))
        self.assertEqual(len(qa['mesh_quality']['centres']), 1)
