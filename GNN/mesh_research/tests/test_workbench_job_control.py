import sys
import tempfile
from pathlib import Path
import threading
import time
import unittest
import json
import os
import subprocess
from unittest.mock import patch

from gis_mesh_workbench.job_control import JobControl, Cancelled, job_scope, checkpoint, run_logged


class JobControlTests(unittest.TestCase):
    def test_phase_progress_keeps_denominator_and_numerator_consistent_per_pass(self):
        control=JobControl(clock=lambda:0.)
        control.stage('values')
        control.measure(40,100,unit='windows',pass_index=0)
        control.measure(30,100,unit='windows',pass_index=0)
        control.measure(40,200,unit='windows',pass_index=0)
        progress=control.snapshot()['phase_progress']
        self.assertEqual((progress['completed'],progress['total'],progress['pass_index']),
                         (40,100,0))
        self.assertEqual(control.snapshot()['phase_percent'],40)

    def test_phase_progress_resets_only_when_pass_changes(self):
        control=JobControl(clock=lambda:0.)
        control.stage('values')
        control.measure(80,100,unit='windows',pass_index=0)
        control.measure(5,100,unit='windows',pass_index=1)
        progress=control.snapshot()['phase_progress']
        self.assertEqual((progress['completed'],progress['total'],progress['pass_index']),
                         (5,100,1))
        self.assertEqual(control.snapshot()['phase_percent'],5)

    def test_reported_gmsh_percentages_never_drive_eta_without_denominator(self):
        now=[0.];control=JobControl(clock=lambda:now[0])
        control.log('Info : Meshing 1D...')
        for seconds,percent in ((0,0),(2,30),(4,60)):
            now[0]=seconds;control.log(f'Info : [ {percent}%] Meshing curve')
        self.assertEqual(control.snapshot()['phase_percent'],60)
        self.assertIsNone(control.snapshot()['eta'])
        control.log('Info : Meshing 2D...')
        control.log('Info : [ 0%] Meshing surface 1')
        self.assertIsNone(control.snapshot()['eta'])
        for seconds,percent,surface in ((6,70,1),(8,0,2),(10,20,2)):
            now[0]=seconds;control.log(f'Info : [ {percent}%] Meshing surface {surface}')
        self.assertEqual(control.snapshot()['phase_percent'],20)
        self.assertIsNone(control.snapshot()['eta'])

    @unittest.skipUnless(os.name=='nt','Windows suspended launch')
    def test_windows_process_cannot_execute_before_job_assignment(self):
        from gis_mesh_workbench.windows_job import WindowsJob
        original=WindowsJob.assign
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);marker=root/'started';control=JobControl()
            def delayed_assignment(job,process):
                time.sleep(.15)
                self.assertFalse(marker.exists(),'Process ran before job ownership')
                original(job,process)
            code=f'from pathlib import Path; Path({str(marker)!r}).write_text("started"); print("short-lived",flush=True)'
            with job_scope(control),patch.object(WindowsJob,'assign',delayed_assignment):
                result=run_logged([sys.executable,'-u','-c',code],root/'gmsh.log',5)
            self.assertEqual(result.returncode,0)
            self.assertEqual(control.snapshot()['log_lines'],['short-lived'])
            self.assertTrue(marker.exists());self.assertIsNone(control.process)

    @unittest.skipUnless(os.name=='nt','Windows suspended launch')
    def test_windows_cancel_before_resume_never_executes_child(self):
        from gis_mesh_workbench.windows_job import WindowsJob
        original=WindowsJob.assign
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);marker=root/'started';control=JobControl();processes=[]
            def cancel_after_assignment(job,process):
                processes.append(process);original(job,process);control.request_cancel()
            code=f'from pathlib import Path; Path({str(marker)!r}).touch()'
            with job_scope(control),patch.object(WindowsJob,'assign',cancel_after_assignment),self.assertRaises(Cancelled):
                run_logged([sys.executable,'-c',code],root/'gmsh.log',5)
            self.assertFalse(marker.exists());self.assertIsNotNone(processes[0].poll())
            self.assertIsNone(control.process)

    @unittest.skipUnless(os.name=='nt','Windows suspended launch')
    def test_windows_failed_assignment_never_executes_and_reaps_child(self):
        from gis_mesh_workbench.windows_job import WindowsJob
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);marker=root/'started';control=JobControl();processes=[]
            def refuse(job,process):
                processes.append(process);time.sleep(.15);raise OSError('assignment refused')
            code=f'from pathlib import Path; Path({str(marker)!r}).touch()'
            with job_scope(control),patch.object(WindowsJob,'assign',refuse),self.assertRaises(OSError):
                run_logged([sys.executable,'-c',code],root/'gmsh.log',5)
            self.assertFalse(marker.exists());self.assertIsNotNone(processes[0].poll())
            self.assertIsNone(control.process)

    @unittest.skipUnless(os.name=='nt','Windows suspended launch')
    def test_windows_failed_resume_reaps_suspended_owned_child(self):
        from gis_mesh_workbench.windows_job import WindowsJob
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);marker=root/'started';control=JobControl();processes=[]
            def refuse(job,process):
                processes.append(process);raise OSError('resume refused')
            code=f'from pathlib import Path; Path({str(marker)!r}).touch()'
            with job_scope(control),patch.object(WindowsJob,'resume',refuse),self.assertRaises(OSError):
                run_logged([sys.executable,'-c',code],root/'gmsh.log',5)
            self.assertFalse(marker.exists());self.assertIsNotNone(processes[0].poll())
            self.assertIsNone(control.process)

    @unittest.skipUnless(os.name=='nt','Windows immediate descendant ownership')
    def test_windows_short_parent_exit_kills_immediate_descendant(self):
        import ctypes
        from ctypes import wintypes
        from gis_mesh_workbench.windows_job import WindowsJob
        original=WindowsJob.resume;api=ctypes.WinDLL('kernel32',use_last_error=True)
        api.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD];api.OpenProcess.restype=wintypes.HANDLE
        api.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD];api.WaitForSingleObject.restype=wintypes.DWORD
        api.CloseHandle.argtypes=[wintypes.HANDLE]
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);marker=root/'child.pid';control=JobControl();handles=[]
            code=('import subprocess,sys; from pathlib import Path; '
                'p=subprocess.Popen([sys.executable,"-c","import time; time.sleep(30)"]); '
                f'Path({str(marker)!r}).write_text(str(p.pid))')
            def observe_child(job,process):
                original(job,process)
                until=time.monotonic()+3
                while not marker.exists() and time.monotonic()<until:time.sleep(.01)
                self.assertTrue(marker.exists())
                handle=api.OpenProcess(0x100000,False,int(marker.read_text()))
                self.assertTrue(handle);handles.append(handle)
                process.wait(timeout=3)
            try:
                with job_scope(control),patch.object(WindowsJob,'resume',observe_child):
                    result=run_logged([sys.executable,'-c',code],root/'gmsh.log',5)
                self.assertEqual(result.returncode,0)
                self.assertEqual(api.WaitForSingleObject(handles[0],1000),0)
                self.assertIsNone(control.process)
            finally:
                for handle in handles:api.CloseHandle(handle)

    def test_reference_extraction_honors_cancel_inside_pixel_scan(self):
        import numpy as np
        import rasterio
        from affine import Affine
        from gis_mesh_workbench.extraction import Cell, extract_attributes
        from gis_mesh_workbench.job_control import observe
        control = JobControl()
        visited = []
        def stop_after_first_row(stage, completed=None, total=None):
            observe(stage, completed, total)
            if completed:
                visited.append(completed)
                control.request_cancel()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'reference.tif'
            with rasterio.open(path, 'w', driver='GTiff', width=2, height=3,
                count=1, dtype='float64', crs='EPSG:32649',
                transform=Affine(1,0,500000,0,-1,1000003)) as dst:
                dst.write(np.ones((3,2)),1)
            cell=Cell('one','rectangle',((500000,1000000),(500002,1000000),
                (500002,1000003),(500000,1000003)))
            with job_scope(control), patch('gis_mesh_workbench.job_control.observe',
                side_effect=stop_after_first_row), self.assertRaises(Cancelled):
                extract_attributes(path,[cell],cell_crs='EPSG:32649',
                    analysis_crs='EPSG:32649',band=1,semantics='intensive',value_units='m')
            self.assertEqual(visited,[2])

    def test_actual_gmsh_stream_cancellation_reaps_process(self):
        executable=Path('Test_chialuoi/gmsh-4.15.2-Windows64/gmsh.exe').resolve()
        if not executable.is_file(): self.skipTest('External Gmsh integration dependency')
        class StopOn2D(JobControl):
            def log(self,line):
                super().log(line)
                if 'Meshing 2D' in line: self.request_cancel()
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);geo=root/'domain.geo';log=root/'gmsh.log'
            geo.write_text('SetFactory("OpenCASCADE"); Rectangle(1)={0,0,0,1000,1000};\n'
                'Mesh.MeshSizeMin=2; Mesh.MeshSizeMax=2; Mesh.Algorithm=6;\n')
            control=StopOn2D()
            with job_scope(control),self.assertRaises(Cancelled):
                run_logged([str(executable),str(geo),'-2','-nt','1','-o',str(root/'mesh.msh')],log,30)
            self.assertIn('Meshing 2D',log.read_text())
            self.assertIsNone(control.process)

    def test_pre_cancel_never_launches_a_process(self):
        control=JobControl();control.request_cancel()
        with tempfile.TemporaryDirectory() as directory,job_scope(control),patch('gis_mesh_workbench.job_control.subprocess.Popen') as launch:
            with self.assertRaises(Cancelled):run_logged(['not-executed'],Path(directory)/'gmsh.log',1)
            launch.assert_not_called()

    def test_mixed_stream_and_split_utf8_are_retained_verbatim(self):
        with tempfile.TemporaryDirectory() as directory:
            control=JobControl();path=Path(directory)/'gmsh.log'
            code='import os,time; os.write(1,b"first\\n"); os.write(2,b"warning\\r"); time.sleep(.15); os.write(2,b"\\n\\xe1"); time.sleep(.15); os.write(1,b"\\xbb\\x87\\n")'
            with job_scope(control):result=run_logged([sys.executable,'-u','-c',code],path,5)
            self.assertEqual(result.returncode,0)
            self.assertEqual(control.snapshot()['log_lines'],['first','warning','ệ'])

    def test_timeout_is_failure_and_process_is_reaped(self):
        with tempfile.TemporaryDirectory() as directory:
            control=JobControl()
            with job_scope(control),self.assertRaises(subprocess.TimeoutExpired):
                run_logged([sys.executable,'-c','import time; time.sleep(20)'],Path(directory)/'gmsh.log',.2)
            self.assertIsNone(control.process)
            self.assertFalse(control.cancel.is_set())

    def test_manifest_cancel_before_commit_never_keeps_success_qa(self):
        from gis_mesh_workbench.job_control import finalize_manifest
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'qa.json').write_text('{"status":"pass"}')
            control=JobControl();control.request_cancel();manifest=dict(status='pass')
            with job_scope(control):finalize_manifest(root,manifest)
            self.assertEqual(json.loads((root/'manifest.json').read_text())['status'],'cancelled')
            self.assertEqual(json.loads((root/'qa.json').read_text())['status'],'cancelled')

    def test_eta_requires_multiple_stable_observations_and_expires(self):
        now=[0.];control=JobControl(clock=lambda:now[0]);control.stage('extraction')
        control.measure(10,100)
        now[0]=2;control.measure(30,100)
        self.assertIsNone(control.snapshot()['eta'])
        now[0]=4;control.measure(50,100)
        self.assertEqual(control.snapshot()['eta']['scope'],'extraction')
        self.assertGreater(control.snapshot()['eta']['high_seconds'],control.snapshot()['eta']['low_seconds'])
        now[0]=15;self.assertIsNone(control.snapshot()['eta'])
        control.stage('serialization');self.assertIsNone(control.snapshot()['eta'])

    def test_cursor_continuity_overflow_and_safe_payload(self):
        control=JobControl()
        for index in range(3): control.log(f'<script>原文 {index}</script>')
        data=control.log_snapshot(0)
        self.assertEqual(data['text'],'<script>原文 0</script>\n<script>原文 1</script>\n<script>原文 2</script>\n')
        self.assertEqual(control.log_snapshot(data['next_cursor'])['text'],'')
        for index in range(1000):control.log('\\\x01'+str(index)+'X'*2000)
        data=control.log_snapshot(0)
        self.assertGreater(data['dropped'],0)
        self.assertTrue(data['truncated_lines'])
        self.assertLess(len(json.dumps(data,ensure_ascii=False).encode()),32768)

    @unittest.skipUnless(os.name=='nt','Windows process-tree ownership')
    def test_windows_cancel_kills_owned_descendant(self):
        import ctypes
        from ctypes import wintypes
        api=ctypes.WinDLL('kernel32',use_last_error=True)
        api.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD];api.OpenProcess.restype=wintypes.HANDLE
        api.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD];api.WaitForSingleObject.restype=wintypes.DWORD
        api.CloseHandle.argtypes=[wintypes.HANDLE]
        with tempfile.TemporaryDirectory() as directory:
            control=JobControl();outcome=[]
            code='import subprocess,sys,time; p=subprocess.Popen([sys.executable,"-c","import time; time.sleep(30)"]); print("CHILD_PID="+str(p.pid),flush=True); time.sleep(30)'
            def worker():
                try:
                    with job_scope(control): run_logged([sys.executable,'-u','-c',code],Path(directory)/'gmsh.log',35)
                except Cancelled:outcome.append('cancelled')
            thread=threading.Thread(target=worker);thread.start();handle=None
            try:
                until=time.monotonic()+5
                while not control.snapshot()['log_lines'] and time.monotonic()<until:time.sleep(.02)
                line=next(line for line in control.snapshot()['log_lines'] if line.startswith('CHILD_PID='))
                handle=api.OpenProcess(0x100000,False,int(line.split('=')[1]))
                self.assertTrue(handle)
                control.request_cancel();thread.join(timeout=5)
                self.assertFalse(thread.is_alive());self.assertEqual(outcome,['cancelled'])
                self.assertEqual(api.WaitForSingleObject(handle,1000),0)
            finally:
                control.request_cancel();thread.join(timeout=5)
                if handle:api.CloseHandle(handle)
    def test_cancel_is_job_scoped_and_repeated_request_is_safe(self):
        first=JobControl(); second=JobControl()
        self.assertTrue(first.request_cancel())
        self.assertTrue(first.request_cancel())
        with job_scope(first):
            with self.assertRaises(Cancelled): checkpoint()
        with job_scope(second): checkpoint()
        checkpoint()

    def test_logs_are_bounded_and_percent_is_not_whole_job(self):
        control=JobControl()
        control.log('Info : Meshing 1D...')
        control.log('Info : [ 40%] Meshing curve 1')
        view=control.snapshot()
        self.assertEqual(view['phase'],'gmsh_1d')
        self.assertEqual(view['phase_percent'],40)
        self.assertNotIn('percent',view)
        control.log('Info : Done meshing 1D')
        control.log('Info : Meshing 2D...')
        self.assertIsNone(control.snapshot()['phase_percent'])
        for index in range(1000): control.log('Info : line '+str(index))
        self.assertLessEqual(len(control.snapshot()['log_lines']),200)
        self.assertIn('Info : line 999',control.snapshot()['log_lines'])

    def test_no_eta_without_data_and_stage_change_resets_estimate(self):
        control=JobControl()
        self.assertIsNone(control.snapshot()['eta'])
        control.log('Info : Meshing 2D...')
        control.log('Info : [ 20%] Meshing surface 1')
        self.assertIsNone(control.snapshot()['eta'])
        control.stage('quality')
        self.assertIsNone(control.snapshot()['phase_percent'])
        self.assertIsNone(control.snapshot()['eta'])

    def test_success_publication_wins_late_cancel(self):
        control=JobControl()
        with control.publication(): self.assertFalse(control.request_cancel())
        self.assertFalse(control.request_cancel())
        other=JobControl(); other.request_cancel()
        with self.assertRaises(Cancelled):
            with other.publication(): self.fail('published after cancel')

    def test_live_process_logs_arrive_before_exit_and_cancel_reaps(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'gmsh.log'; control=JobControl(); outcome=[]
            def work():
                try:
                    with job_scope(control):
                        run_logged([sys.executable,'-u','-c',
                            'import time; print("Info : Meshing 2D...",flush=True); time.sleep(20)'],path,30)
                except Cancelled: outcome.append('cancelled')
            thread=threading.Thread(target=work);thread.start()
            try:
                until=time.monotonic()+5
                while not control.snapshot()['log_lines'] and time.monotonic()<until: time.sleep(.03)
                self.assertIn('Info : Meshing 2D...',control.snapshot()['log_lines'])
                self.assertTrue(thread.is_alive())
                control.request_cancel();thread.join(timeout=5)
                self.assertFalse(thread.is_alive())
                self.assertEqual(outcome,['cancelled'])
                self.assertIsNone(control.process)
                self.assertIn('Info : Meshing 2D...',path.read_text())
            finally:
                control.request_cancel();thread.join(timeout=5)
