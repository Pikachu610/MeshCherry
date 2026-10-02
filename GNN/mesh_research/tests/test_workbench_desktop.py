import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, MagicMock, patch

from gis_mesh_workbench.desktop import launch
from gis_mesh_workbench.onboarding import acknowledge, sample_directory, tutorial_seen
from gis_mesh_workbench.webui import WorkbenchServer
from gis_mesh_workbench.adapters import structured_grid
from gis_mesh_workbench.extraction import extract_attributes


class DesktopTests(unittest.TestCase):
    def test_frozen_gmsh_fails_explicitly(self):
        from gis_mesh_workbench.gmsh_reference import inspect_preset
        with patch('sys.frozen', True, create=True):
            with self.assertRaisesRegex(ValueError, 'Gmsh demo không có trong EXE v2'):
                inspect_preset('EPSG:32649')

    def test_closing_veto_while_running_and_allows_idle(self):
        class Event:
            def __iadd__(self, callback):
                self.callback = callback
                return self
        with tempfile.TemporaryDirectory() as directory:
            server = WorkbenchServer(directory)
            view = MagicMock()
            event = Event()
            view.create_window.return_value.events.closing = event
            def start(**kwargs):
                server.jobs['test'] = {'status': 'running'}
                self.assertIs(event.callback(), False)
                server.jobs['test']['status'] = 'pass'
                self.assertIsNone(event.callback())
            view.start.side_effect = start
            with patch('gis_mesh_workbench.desktop.WorkbenchServer', return_value=server):
                launch(directory, webview_module=view, preferences=Path(directory)/'prefs.json')
            self.assertEqual(server.socket.fileno(), -1)

    def test_window_url_no_bridge_and_shutdown(self):
        with tempfile.TemporaryDirectory() as directory:
            view = MagicMock()
            servers = []
            def factory(**kwargs):
                server = WorkbenchServer(**kwargs)
                servers.append(server)
                return server
            with patch('gis_mesh_workbench.desktop.WorkbenchServer', side_effect=factory):
                launch(directory, webview_module=view, preferences=Path(directory)/'preferences.json')
            title, url = view.create_window.call_args.args
            self.assertIn('MeshCherry', title)
            self.assertTrue(url.startswith(servers[0].origin+'/#token='))
            self.assertNotIn('js_api', view.create_window.call_args.kwargs)
            self.assertEqual(view.start.call_args.kwargs['gui'], 'edgechromium')
            self.assertTrue(view.start.call_args.kwargs['private_mode'])
            self.assertEqual(servers[0].socket.fileno(), -1)

    def test_failure_still_shuts_down(self):
        with tempfile.TemporaryDirectory() as directory:
            server = WorkbenchServer(directory)
            view = MagicMock(); view.start.side_effect = RuntimeError('renderer failed')
            with patch('gis_mesh_workbench.desktop.WorkbenchServer', return_value=server):
                with self.assertRaisesRegex(RuntimeError, 'renderer failed'):
                    launch(directory, webview_module=view)
            self.assertEqual(server.socket.fileno(), -1)

    def test_missing_dependency_opens_no_server(self):
        with patch.dict('sys.modules', {'webview': None}), patch('gis_mesh_workbench.desktop.WorkbenchServer') as factory:
            with self.assertRaisesRegex(RuntimeError, 'pip install'):
                launch()
            factory.assert_not_called()

    def test_version_only_preferences(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'tutorial.json'
            self.assertFalse(tutorial_seen(path))
            acknowledge(path)
            self.assertEqual(json.loads(path.read_text()), {'tutorial_version_seen':1})
            self.assertTrue(tutorial_seen(path))
            path.write_text('{broken', encoding='utf-8')
            self.assertFalse(tutorial_seen(path))
            self.assertFalse(tutorial_seen(None))

    def test_packaged_sample_real_extraction(self):
        root = sample_directory()
        self.assertTrue((root/'two_triangles.json').is_file())
        cells = structured_grid(extent=[500000,1000000,500002,1000001],cell_width_m=1,
            cell_height_m=1,analysis_crs='EPSG:32649')
        result = extract_attributes(root/'sample_10_30.tif',cells.cells,
            cell_crs='EPSG:32649',analysis_crs='EPSG:32649',band=1,
            semantics='intensive',value_units='synthetic-value')
        self.assertEqual([r['value'] for r in result['cells']], [10,30])
        self.assertTrue(all(r['valid_coverage']==1 for r in result['cells']))


if __name__ == '__main__':
    unittest.main()
