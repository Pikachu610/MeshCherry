"""Focused regressions for LTS desktop lifecycle and preflight exclusion."""
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from gis_mesh_workbench import desktop, webui


class LifecycleTests(unittest.TestCase):
    def test_desktop_close_vetoes_each_active_operation_and_allows_idle(self):
        class Event:
            def __iadd__(self, callback):
                self.callback = callback
                return self

        class Server:
            def __init__(self, **state):
                self.lock = threading.RLock()
                self.document_active = state.get('document_active', False)
                self.picker_active = state.get('picker_active', False)
                self.preflight_inflight = state.get('preflight_inflight', set())
                self.downloads = SimpleNamespace(active=state.get('download_active', False))
                self.jobs = state.get('jobs', {})
                self.origin = 'http://127.0.0.1:1'
                self.token = 'test'

            def serve_forever(self): pass
            def shutdown(self): pass
            def server_close(self): pass

        busy_states = [
            {'document_active': True},
            {'preflight_inflight': {1}},
            {'picker_active': True},
            {'download_active': True},
            {'jobs': {'job': {'status': 'running'}}},
            {'jobs': {'job': {'status': 'stopping'}}},
        ]
        with tempfile.TemporaryDirectory() as directory, patch.object(desktop, 'migrate_legacy'):
            for state in busy_states + [{}]:
                with self.subTest(state=state):
                    server = Server(**state)
                    window = SimpleNamespace(maximized=False,events=SimpleNamespace(
                        closing=Event(),maximized=Event(),restored=Event()))
                    view = SimpleNamespace(
                        settings={},
                        FileDialog=SimpleNamespace(FOLDER=1, OPEN=2, SAVE=3),
                        create_window=lambda *_args, **_kwargs: window,
                    )
                    view.start = lambda **_kwargs: setattr(
                        view, 'close_result', window.events.closing.callback())
                    with patch.object(desktop, 'WorkbenchServer', return_value=server):
                        desktop.launch(directory, webview_module=view,
                                       preferences=Path(directory) / 'prefs.json')
                    self.assertIs(view.close_result, False if state else None)

    def _server(self, directory):
        server = webui.WorkbenchServer.__new__(webui.WorkbenchServer)
        server.lock = threading.RLock()
        server.picker_active = False
        server.document_active = False
        server.preflight_generation = 0
        server.preflight_inflight = set()
        server.current_preflight = None
        server.validated = {}
        server.session_dir = Path(directory)
        server.jobs = {}
        server.controls = {}
        server.normalize_form = lambda data: dict(data)
        server.protect_config_inputs = lambda _config: None
        server._active_job_locked = lambda: server.document_active or any(
            item['status'] in {'running', 'stopping'} for item in server.jobs.values())
        return server

    def test_overlapping_preflight_is_rejected_under_shared_lock(self):
        entered = threading.Event()
        release = threading.Event()
        calls = []
        def fake_preflight(_config):
            calls.append(1)
            entered.set()
            self.assertTrue(release.wait(3))
            return {'status': 'pass'}, SimpleNamespace(cells=[])

        with tempfile.TemporaryDirectory() as directory, \
                patch.object(webui, 'write_json', side_effect=lambda path, _data: Path(path).write_text('{}')), \
                patch.object(webui, 'effective_config', return_value={}), \
                patch.object(webui, 'preflight', side_effect=fake_preflight), \
                patch.object(webui, 'display_policy', return_value={'mode': 'summary'}):
            server = self._server(directory)
            errors = []
            thread = threading.Thread(target=lambda: self._capture(server, errors))
            thread.start()
            self.assertTrue(entered.wait(2))
            generation = server.preflight_generation
            inflight = set(server.preflight_inflight)
            with self.assertRaisesRegex(ValueError, 'chờ'):
                server.validate_form({})
            self.assertEqual(server.preflight_generation, generation)
            self.assertEqual(server.preflight_inflight, inflight)
            release.set()
            thread.join(3)
            self.assertFalse(thread.is_alive())
            self.assertEqual(len(calls), 1)
            self.assertEqual(errors, [])
            self.assertEqual(server.preflight_inflight, set())
            self.assertIsNotNone(server.current_preflight)

    def _capture(self, server, errors):
        try:
            server.validate_form({})
        except BaseException as error:
            errors.append(error)

    def test_preflight_guard_is_restored_after_failure(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(webui, 'write_json', side_effect=RuntimeError('controlled failure')):
            server = self._server(directory)
            with self.assertRaisesRegex(RuntimeError, 'controlled failure'):
                server.validate_form({})
            self.assertEqual(server.preflight_inflight, set())


if __name__ == '__main__':
    unittest.main()
