import unittest
from unittest.mock import patch

from gis_mesh_workbench.comparison_allocation import allocation_summary
from gis_mesh_workbench.job_control import (
    Cancelled, JobControl, checkpoint as real_checkpoint, job_scope,
)


class AllocationSummaryTests(unittest.TestCase):
    def test_uniform_cells_have_constant_area_and_equivalent_square_size(self):
        result = allocation_summary([4, 4, 4])
        self.assertEqual(result['assessment_status'], 'cell_size_distribution_only')
        self.assertEqual(result['node_count'], 3)
        self.assertEqual(result['total_area_m2'], 12)
        self.assertEqual(result['cell_area_quantiles_m2'],
                         dict(min=4, p10=4, median=4, p90=4, max=4))
        self.assertEqual(result['equivalent_square_size_m'],
                         dict(min=2, p10=2, median=2, p90=2, max=2))
        self.assertEqual(result['relief_alignment'], 'not_assessed')
        self.assertIn('not show alignment', result['interpretation'])
        self.assertNotIn('refinement', result)

    def test_nonuniform_quantiles_and_reference_shares(self):
        result = allocation_summary([1, 4, 9, 16, 25, 36], reference_cell_area_m2=16)
        self.assertEqual(result['cell_area_quantiles_m2'],
                         dict(min=1, p10=2.5, median=12.5, p90=30.5, max=36))
        self.assertEqual(result['equivalent_square_size_m'],
                         dict(min=1, p10=1.5, median=3.5, p90=5.5, max=6))
        refinement = result['refinement']
        self.assertEqual(refinement['reference_cell_area_m2'], 16)
        self.assertEqual(refinement['reference_equivalent_square_size_m'], 4)
        self.assertEqual(refinement['threshold'], 'cell_area_m2 < reference_cell_area_m2')
        self.assertEqual(refinement['node_count_below_reference'], 3)
        self.assertEqual(refinement['node_fraction_below_reference'], .5)
        self.assertEqual(refinement['roi_area_below_reference_m2'], 14)
        self.assertAlmostEqual(refinement['roi_area_fraction_below_reference'], 14 / 91)

    def test_reference_threshold_is_strict(self):
        result = allocation_summary([1, 4, 9], reference_cell_area_m2=4)
        self.assertEqual(result['refinement']['node_count_below_reference'], 1)
        self.assertEqual(result['refinement']['roi_area_below_reference_m2'], 1)

    def test_empty_invalid_and_overflowing_areas_are_rejected(self):
        for areas in ([], (), [0], [-1], [float('nan')], [float('inf')], [True],
                      [1e308, 1e308]):
            with self.subTest(areas=areas), self.assertRaises(ValueError):
                allocation_summary(areas)
        for reference in (0, -1, float('nan'), float('inf'), True):
            with self.subTest(reference=reference), self.assertRaises(ValueError):
                allocation_summary([1], reference_cell_area_m2=reference)

    def test_cell_count_limit_is_enforced(self):
        with patch('gis_mesh_workbench.comparison_allocation.MAX_MESH_CELLS', 2):
            with self.assertRaisesRegex(ValueError, 'mesh cell limit'):
                allocation_summary([1, 1, 1])

    def test_cancellation_is_checked_while_validating_areas(self):
        control = JobControl()
        calls = 0

        def checkpoint_and_cancel():
            nonlocal calls
            calls += 1
            if calls == 3:
                control.request_cancel()
            real_checkpoint()

        with patch('gis_mesh_workbench.comparison_allocation.checkpoint', checkpoint_and_cancel):
            with job_scope(control), self.assertRaises(Cancelled):
                allocation_summary([1] * 1000)


if __name__ == '__main__':
    unittest.main()
