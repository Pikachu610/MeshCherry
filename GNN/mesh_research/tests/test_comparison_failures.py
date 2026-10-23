import unittest

from gis_mesh_workbench.comparison_failures import describe_comparison_failure


class ComparisonFailureTests(unittest.TestCase):
    def test_limits_explain_what_to_change_without_false_resolution_promise(self):
        failure = describe_comparison_failure(ValueError('comparison read pixel limit exceeded'))
        self.assertEqual(failure['code'], 'comparison_read_limit')
        self.assertIn('đọc', failure['message'])
        self.assertIn('miền nhỏ hơn', ' '.join(failure['actions']))
        self.assertNotIn('ô nhỏ hơn', ' '.join(failure['actions']))

    def test_no_valid_support_explains_nodata_not_zero(self):
        failure = describe_comparison_failure('comparison has no finite valid common raster support')
        self.assertEqual(failure['code'], 'comparison_empty_support')
        self.assertIn('NoData', failure['message'])

    def test_unknown_errors_do_not_echo_arbitrary_exception(self):
        secret = 'bad source C:/private/data.tif?token=secret'
        failure = describe_comparison_failure(secret)
        self.assertNotIn(secret, str(failure))
        self.assertEqual(failure['code'], 'unknown_failure')

    def test_existing_known_geometry_failure_is_reused(self):
        self.assertEqual(describe_comparison_failure('cells overlap in positive area')['code'], 'cell_overlap')

    def test_source_change_has_specific_recovery(self):
        self.assertEqual(describe_comparison_failure('comparison input changed during computation')['code'], 'comparison_input_changed')


if __name__ == '__main__':
    unittest.main()
