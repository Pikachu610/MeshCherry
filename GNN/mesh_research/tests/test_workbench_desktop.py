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
    def test_native_picker_filters_pass_pywebview_validation(self):
        try:
            from webview.util import parse_file_type
        except ImportError:
            self.skipTest('pywebview is an optional desktop dependency')
        with tempfile.TemporaryDirectory() as directory:
            server=WorkbenchServer(directory); view=MagicMock()
            validated=[]
            def validate_filter(dialog_type, *, allow_multiple, file_types, **kwargs):
                # This is the same validator used by Window.create_file_dialog.
                parsed=tuple(parse_file_type(file_type) for file_type in file_types)
                validated.append((dialog_type,allow_multiple,file_types,parsed))
                return None
            view.create_window.return_value.create_file_dialog.side_effect=validate_filter
            def start(**kwargs):
                for kind,expected in {
                    'raster':('Raster GeoTIFF and ASC (*.tif;*.tiff;*.asc)',),
                    'mesh':('Mesh JSON (*.json)',),
                    'boundary':('Shapefile boundary (*.shp)',),
                    'gmsh':('Gmsh executable (*.exe)',),
                }.items():
                    self.assertEqual(server.choose_native({'kind':kind}),{'cancelled':True})
                    self.assertEqual(validated[-1][0],view.FileDialog.OPEN)
                    self.assertFalse(validated[-1][1])
                    self.assertEqual(validated[-1][2],expected)
                self.assertEqual([entry[3][0][1] for entry in validated],
                    ['*.tif;*.tiff;*.asc','*.json','*.shp','*.exe'])
                server.choose_native({'kind':'raster_many'})
                self.assertTrue(validated[-1][1])
                self.assertEqual(validated[-1][3][0][1],'*.tif;*.tiff;*.asc')
                self.assertIsNone(server.native_save('MeshCherry.mcy'))
                self.assertEqual(validated[-1][0],view.FileDialog.SAVE)
                self.assertEqual(validated[-1][3][0][1],'*.mcy')
                server.choose_native({'kind':'output'})
                self.assertEqual(view.create_window.return_value.create_file_dialog.call_args.args[0],view.FileDialog.FOLDER)
            view.start.side_effect=start
            with patch('gis_mesh_workbench.desktop.WorkbenchServer',return_value=server), patch('gis_mesh_workbench.desktop._invoke_dialog',side_effect=lambda callback:callback()):
                launch(directory,webview_module=view)

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
                server.picker_active=True
                self.assertIs(event.callback(),False)
                server.picker_active=False
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
            self.assertEqual(servers[0].gmsh_preferences, Path(directory)/'gmsh.json')

    def test_launch_passes_only_explicit_startup_project(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace=Path(directory);startup=workspace/'incoming.mcy';startup.write_bytes(b'archive')
            server=WorkbenchServer(workspace)
            view=MagicMock()
            with patch('gis_mesh_workbench.desktop.WorkbenchServer',return_value=server) as factory:
                launch(workspace,webview_module=view,preferences=workspace/'preferences.json',
                       startup_project=startup)
            self.assertEqual(factory.call_args.kwargs['startup_project'],startup)

    def test_failure_still_shuts_down(self):
        with tempfile.TemporaryDirectory() as directory:
            server = WorkbenchServer(directory)
            view = MagicMock(); view.start.side_effect = RuntimeError('renderer failed')
            with patch('gis_mesh_workbench.desktop.WorkbenchServer', return_value=server):
                with self.assertRaisesRegex(RuntimeError, 'renderer failed'):
                    launch(directory, webview_module=view)
            self.assertEqual(server.socket.fileno(), -1)

    def test_launch_migrates_legacy_into_per_user_preferences(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); workspace=root/'release'; workspace.mkdir()
            legacy=workspace/'.scratch/gis-mesh-workbench/gmsh.json'
            legacy.parent.mkdir(parents=True)
            saved=dict(executable=str(root/'tools/gmsh.exe'),version='4.15.2',executable_sha256='a'*64)
            legacy.write_text(json.dumps(saved),encoding='utf-8')
            def factory(**kwargs):
                server=WorkbenchServer(**kwargs)
                self.assertEqual(server.saved_gmsh(),saved)
                return server
            with patch('gis_mesh_workbench.desktop.WorkbenchServer',side_effect=factory), \
                 patch('gis_mesh_workbench.gmsh_meshing.inspect_executable') as inspect:
                launch(workspace,webview_module=MagicMock(),preferences=root/'profile/tutorial.json')
                inspect.assert_not_called()
            self.assertTrue(legacy.is_file())
            self.assertEqual(json.loads((root/'profile/gmsh.json').read_text()),saved)

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
