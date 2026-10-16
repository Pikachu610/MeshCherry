"""Focused regressions for workbench preflight, run recovery, and diagnostics."""
import threading
import time
import unittest
from unittest.mock import patch

from gis_mesh_workbench.job_control import JobControl
from tests import test_workbench_webui as webui_helpers
from tests import test_workbench_comparison_api as comparison_helpers


class IntegrationRegressionTests(unittest.TestCase):
    setUp=webui_helpers.WebUITests.setUp
    stop=webui_helpers.WebUITests.stop
    request=webui_helpers.WebUITests.request
    validate=webui_helpers.WebUITests.validate
    wait_job=webui_helpers.WebUITests.wait_job

    def test_blocked_preflight_does_not_hold_stop_lock_and_cannot_authorize_stale_run(self):
        entered=threading.Event();release=threading.Event();result=[]
        def slow_preflight(_config):
            entered.set();release.wait(3)
            return {'status':'pass','errors':[]},None
        with patch('gis_mesh_workbench.webui.preflight',side_effect=slow_preflight):
            worker=threading.Thread(target=lambda:result.append(self.server.validate_form(self.config)))
            worker.start()
            self.assertTrue(entered.wait(2))
            control=JobControl(lock=self.server.lock)
            self.server.controls['busy-job']=control
            self.server.jobs['busy-job']=dict(status='running',stage='mesh',operation='extract',result=None)
            stopped=threading.Event()
            stopper=threading.Thread(target=lambda:(self.server.stop_job({'job_id':'busy-job'}),stopped.set()))
            stopper.start()
            try:
                self.assertTrue(stopped.wait(.5),'preflight held the shared lock and blocked Stop')
                self.assertTrue(control.cancel.is_set())
            finally:
                release.set();worker.join(5);stopper.join(5)
        self.assertEqual(len(result),1)
        self.assertIsNone(result[0]['preflight_id'])
        self.assertTrue(result[0]['superseded'])
        self.assertIsNone(self.server.current_preflight)

    def test_latest_concurrent_preflight_wins_over_late_older_result(self):
        older_entered=threading.Event();release_older=threading.Event();results={}
        def ordered_preflight(config):
            if config['run_name']=='older':
                older_entered.set();release_older.wait(3)
            return {'status':'pass','errors':[]},None
        with patch('gis_mesh_workbench.webui.preflight',side_effect=ordered_preflight):
            older=dict(self.config,run_name='older');newer=dict(self.config,run_name='newer')
            first=threading.Thread(target=lambda:results.setdefault('older',self.server.validate_form(older)))
            first.start();self.assertTrue(older_entered.wait(2))
            results['newer']=self.server.validate_form(newer)
            release_older.set();first.join(5)
        self.assertFalse(first.is_alive())
        self.assertIsNone(results['older']['preflight_id'])
        self.assertTrue(results['older']['superseded'])
        self.assertEqual(self.server.current_preflight,results['newer']['preflight_id'])
        self.assertIn(results['newer']['preflight_id'],self.server.validated)

    def test_latest_validated_preflight_can_start_while_superseded_preflight_finishes(self):
        older_entered=threading.Event();release_older=threading.Event();results={}
        def ordered_preflight(config):
            if config['run_name']=='older':
                older_entered.set();release_older.wait(3)
            return {'status':'pass','errors':[]},None
        with patch('gis_mesh_workbench.webui.preflight',side_effect=ordered_preflight), \
             patch('gis_mesh_workbench.webui.run',return_value={'status':'pass','output':None}):
            older=dict(self.config,run_name='older');newer=dict(self.config,run_name='newer')
            first=threading.Thread(target=lambda:results.setdefault('older',self.server.validate_form(older)))
            first.start();self.assertTrue(older_entered.wait(2))
            results['newer']=self.server.validate_form(newer)
            started=self.server.start_job(results['newer']['preflight_id'],'latest-request-0001')
            try:
                self.assertEqual(started['status'],'running')
            finally:
                release_older.set();first.join(5)
        self.assertFalse(first.is_alive())
        self.assertTrue(results['older']['superseded'])
        self.assertEqual(self.server.jobs[started['job_id']]['request_id'],'latest-request-0001')

    def test_run_request_id_recovers_same_terminal_job_and_rejects_rebinding(self):
        preflight_id=self.validate();request_id='run-regression-unique-01'
        status,started,_=self.request('/api/run',{'preflight_id':preflight_id,'request_id':request_id})
        self.assertEqual(status,202,started)
        completed=self.wait_job(started['job_id'])
        self.assertEqual(completed['status'],'pass',completed)
        status,replay,_=self.request('/api/run',{'preflight_id':preflight_id,'request_id':request_id})
        self.assertEqual(status,202,replay)
        self.assertEqual(replay['job_id'],started['job_id'])
        recovered=self.request('/api/run/status?request_id='+request_id)[1]
        self.assertEqual(recovered,dict(job_id=started['job_id'],operation='extract',request_id=request_id,status='pass'))
        status,error,_=self.request('/api/run',{'preflight_id':'different-preflight','request_id':request_id})
        self.assertEqual(status,400)
        self.assertIn('different run',error['error'])

    def test_legacy_run_payload_without_request_id_remains_supported(self):
        preflight_id=self.validate()
        status,started,_=self.request('/api/run',{'preflight_id':preflight_id})
        self.assertEqual(status,202,started)
        self.assertNotIn('request_id',started)
        self.assertEqual(self.wait_job(started['job_id'])['status'],'pass')

    def test_cancelled_comparison_manifest_keeps_structured_provenance(self):
        comparison_helpers.ComparisonAPITests.seed(self)
        entered=threading.Event();release=threading.Event()
        from gis_mesh_workbench.job_control import finalize_manifest as original
        def pause_publication(output,manifest):
            entered.set();release.wait(3)
            return original(output,manifest)
        with patch('gis_mesh_workbench.webui.finalize_manifest',side_effect=pause_publication):
            status,started,_=self.request('/api/comparison',self.payload)
            self.assertEqual(status,202,started);self.assertTrue(entered.wait(2))
            try:
                self.assertTrue(self.request('/api/job/stop',{'job_id':started['job_id']})[1]['accepted'])
            finally:
                release.set()
            self.assertEqual(self.wait_job(started['job_id'])['status'],'cancelled')
        status,manifest,_=self.request('/api/artifact?id='+started['job_id']+'&name=manifest')
        self.assertEqual(status,200,manifest)
        self.assertEqual(manifest['schema_version'],'mesh-comparison-manifest-v1')
        self.assertEqual(manifest['operation'],'comparison')
        self.assertEqual(manifest['status'],'cancelled')
        self.assertTrue(manifest['application_build'])
        self.assertTrue(manifest['mesh_job_id'])
        self.assertTrue(manifest['input_hashes'])
        self.assertIn('cancelled_stage',manifest)


if __name__=='__main__':
    unittest.main()
