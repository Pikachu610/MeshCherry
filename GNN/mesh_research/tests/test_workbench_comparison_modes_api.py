"""Authenticated comparison mode contracts share the accepted mesh lifecycle."""
import json
import unittest

from tests import test_workbench_comparison_api as comparison
from tests import test_workbench_webui as helpers


class ComparisonModesAPITests(unittest.TestCase):
    setUp=helpers.WebUITests.setUp
    stop=helpers.WebUITests.stop
    request=helpers.WebUITests.request
    wait_job=helpers.WebUITests.wait_job
    seed=comparison.ComparisonAPITests.seed

    def run_report(self,payload):
        status,started,_=self.request('/api/comparison',payload)
        self.assertEqual(status,202,started)
        job=self.wait_job(started['job_id'])
        self.assertEqual(job['status'],'pass',job)
        status,report,_=self.request('/api/artifact?id='+started['job_id']+'&name=comparison')
        self.assertEqual(status,200,report)
        return report

    def test_native_pixel_mode_without_requested_size(self):
        path=self.seed();before=path.read_bytes()
        payload={k:v for k,v in self.payload.items() if k!='cell_size_m'}
        payload['baseline_mode']='native_raster'
        report=self.run_report(payload)
        self.assertEqual(report['baseline']['source_pixel_count'],2)
        self.assertEqual(report['structured']['node_count'],2)
        self.assertEqual(report['baseline']['mode'],'native_raster')
        self.assertEqual(path.read_bytes(),before)
        self.assertEqual(self.server.job('accepted')['status'],'pass')

    def test_estimate_requires_no_raster_and_exposes_no_measured_errors(self):
        self.seed()
        report=self.run_report(dict(mesh_job_id='accepted',baseline_mode='resolution_estimate',
                                    cell_size_m=1,source_resolution_m=2))
        self.assertEqual(report['baseline']['estimated_source_pixel_count'],1)
        self.assertEqual(report['baseline']['estimated_node_count'],2)
        self.assertEqual(report['square_grid']['node_count'],2)
        self.assertFalse(report['metrics_available'])
        self.assertIsNone(report['metrics'])

    def test_invalid_mode_specific_fields_are_rejected_before_job_creation(self):
        self.seed()
        payloads=[
            dict(self.payload,baseline_mode='unknown'),
            dict(self.payload,baseline_mode='native_raster'),
            dict(mesh_job_id='accepted',baseline_mode='resolution_estimate',cell_size_m=True,source_resolution_m=30),
            dict(mesh_job_id='accepted',baseline_mode='resolution_estimate',cell_size_m=30,source_resolution_m=0),
            dict(mesh_job_id='accepted',baseline_mode='resolution_estimate',cell_size_m=30,source_resolution_m=30,raster_path=str(self.raster)),
        ]
        for payload in payloads:
            with self.subTest(payload=payload):
                status,_,_=self.request('/api/comparison',payload)
                self.assertEqual(status,400)
        self.assertEqual(set(self.server.jobs),{'accepted'})

    def test_native_request_recovery_keeps_identity(self):
        self.seed()
        payload={k:v for k,v in self.payload.items() if k!='cell_size_m'}
        payload.update(baseline_mode='native_raster',request_id='native-request-001')
        status,started,_=self.request('/api/comparison',payload)
        self.assertEqual(status,202,started)
        self.assertEqual(self.wait_job(started['job_id'])['status'],'pass')
        status,replayed,_=self.request('/api/comparison',payload)
        self.assertEqual(status,202,replayed)
        self.assertEqual(replayed['job_id'],started['job_id'])
        self.assertEqual(len(self.server.jobs),2)
