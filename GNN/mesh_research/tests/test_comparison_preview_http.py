"""Actual HTTP comparison display assets/thumbnail keep measured-source guards."""
import unittest
from unittest.mock import patch

from tests import test_workbench_comparison_api as helpers


class ComparisonPreviewHTTPTests(unittest.TestCase):
    setUp = helpers.ComparisonAPITests.setUp
    stop = helpers.ComparisonAPITests.stop
    request = helpers.ComparisonAPITests.request
    wait_job = helpers.ComparisonAPITests.wait_job
    seed = helpers.ComparisonAPITests.seed

    def test_display_assets_and_actual_thumbnail_keep_full_counts(self):
        self.seed()
        for asset, mime in [('comparison_view.js', 'javascript'), ('comparison_view.css', 'css')]:
            status, _, headers = self.request('/' + asset)
            self.assertEqual(status, 200)
            self.assertIn(mime, headers['Content-Type'])
        status, started, _ = self.request('/api/comparison', self.payload)
        self.assertEqual(status, 202)
        self.assertEqual(self.wait_job(started['job_id'])['status'], 'pass')
        _, report, _ = self.request('/api/artifact?id=' + started['job_id'] + '&name=comparison')
        source = report['visualization']['source']
        self.assertTrue(source['available'])
        self.assertFalse(source['shared_zoom'])
        self.assertTrue(source['image'].startswith('data:image/png;base64,'))
        self.assertEqual(source['crs'], 'EPSG:32649')
        self.assertEqual(report['structured']['node_count'], 2)
        self.assertEqual(report['unstructured']['node_count'], 2)

    def test_optional_thumbnail_failure_keeps_measured_comparison(self):
        self.seed()
        with patch('gis_mesh_workbench.input_preview.raster_preview', side_effect=ValueError('bounded preview unavailable')):
            status, started, _ = self.request('/api/comparison', self.payload)
            self.assertEqual(status, 202)
            self.assertEqual(self.wait_job(started['job_id'])['status'], 'pass')
        _, report, _ = self.request('/api/artifact?id=' + started['job_id'] + '&name=comparison')
        self.assertIn('bounded preview unavailable', report['visualization']['source']['reason'])
        self.assertEqual(report['structured']['node_count'], 2)

    def test_source_mutation_during_optional_preview_cannot_publish(self):
        self.seed()
        def changed(*args, **kwargs):
            with self.raster.open('ab') as stream:
                stream.write(b'changed-during-display-read')
            raise ValueError('preview failed')
        with patch('gis_mesh_workbench.input_preview.raster_preview', side_effect=changed):
            _, started, _ = self.request('/api/comparison', self.payload)
            result = self.wait_job(started['job_id'])
        self.assertEqual(result['status'], 'fail')
        self.assertEqual(self.request('/api/artifact?id=' + started['job_id'] + '&name=comparison')[0], 403)


if __name__ == '__main__':
    unittest.main()
