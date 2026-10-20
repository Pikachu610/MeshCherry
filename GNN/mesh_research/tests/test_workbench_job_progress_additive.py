"""Work counters extend monitor payloads without removing legacy progress."""
import unittest

from gis_mesh_workbench.job_control import JobControl, observe, job_scope


class AdditiveJobProgressTests(unittest.TestCase):
    def test_phase_counter_is_additive_and_keeps_legacy_phase_percent(self):
        control=JobControl();control.stage('count_target_grid')
        with job_scope(control): observe('count_target_grid',3,10,'bounding_grid_cells')
        snapshot=control.snapshot()
        self.assertEqual(snapshot['phase_percent'],30)
        self.assertEqual(snapshot['phase_progress'],dict(scope='phase',phase='count_target_grid',
            completed=3,total=10,unit='bounding_grid_cells'))
        self.assertEqual(snapshot['phase'],'count_target_grid')

    def test_backward_count_does_not_desynchronize_count_and_percent(self):
        control=JobControl();control.stage('count_native_pixels')
        control.measure(7,10,unit='source_pixels_examined')
        control.measure(4,10,unit='source_pixels_examined')
        snapshot=control.snapshot()
        self.assertEqual(snapshot['phase_progress']['completed'],7)
        self.assertEqual(snapshot['phase_percent'],70)

    def test_denominator_change_requires_new_phase_or_pass_identity(self):
        now=[0.];control=JobControl(clock=lambda:now[0]);control.stage('values_pass1')
        control.measure(6,10,unit='windows',pass_index=0)
        now[0]=1
        control.measure(1,5,unit='windows',pass_index=0)
        # An altered work plan cannot silently restart the same named pass.
        self.assertEqual(control.snapshot()['phase_progress']['completed'],6)
        self.assertEqual(control.snapshot()['phase_progress']['total'],10)
        self.assertEqual(control.snapshot()['phase_percent'],60)
        self.assertEqual(len(control.points),1)
        now[0]=2
        control.measure(2,5,unit='windows',pass_index=1)
        self.assertEqual(control.snapshot()['phase_progress']['pass_index'],1)
        self.assertEqual(control.snapshot()['phase_progress']['completed'],2)
        self.assertEqual(len(control.points),1)

    def test_legacy_fractional_gmsh_measure_does_not_fabricate_work_counts(self):
        control=JobControl();control.stage('gmsh_1d');control.measure(42.5,100)
        self.assertEqual(control.snapshot()['phase_percent'],42.5)
        self.assertNotIn('phase_progress',control.snapshot())

    def test_bad_counter_values_are_ignored(self):
        control=JobControl();control.stage('count')
        for completed,total in ((True,10),(2,False),(1,float('inf')),(11,10),(-1,10)):
            control.measure(completed,total,unit='items')
        self.assertIsNone(control.snapshot()['phase_percent'])
        self.assertNotIn('phase_progress',control.snapshot())


if __name__=='__main__':
    unittest.main()
