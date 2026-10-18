"""Failure-path tests for generated download source ownership."""
from pathlib import Path
import os
import tempfile
import time
import unittest
from unittest.mock import patch

from gis_mesh_workbench import batch_api
from gis_mesh_workbench.webui import WorkbenchServer


class BatchExportProducerCleanupTests(unittest.TestCase):
    def test_failed_export_removes_partial_session_owned_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            server = type('Server', (), {})()
            server.session_dir = root
            server.permitted = lambda path, **kwargs: Path(path)

            def write_then_fail(_database, destination):
                Path(destination).write_bytes(b'partial export')
                raise RuntimeError('simulated writer failure')

            with patch.object(batch_api, '_batch_database', return_value=({}, root/'batch.sqlite')):
                with patch.object(batch_api, '_FORMATS', {
                    'wide_csv': (write_then_fail, 'text/csv', 'batch.csv'),
                }):
                    with self.assertRaisesRegex(RuntimeError, 'simulated writer failure'):
                        batch_api.batch_export_file(server, {'job_id': 'batch-1', 'format': 'wide_csv'})

            leftovers = list((root/'batch-exports').glob('*'))
            self.assertEqual(leftovers, [], 'failed producer must not strand a partial export')

    def test_batch_download_copies_session_file_then_removes_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            server = WorkbenchServer(root)
            server.native_save = lambda _filename: (str(root/'chosen.zip'),)
            source = root/'batch-export.zip'
            source.write_bytes(b'id,value\n1,42\n')
            try:
                with patch.object(batch_api, '_batch_database', return_value=({}, root/'batch.sqlite')):
                    with patch.object(batch_api, 'batch_export_file', return_value={'path':str(source)}):
                        ident = server.downloads.start({'kind':'batch','request':{'job_id':'batch-1','format':'wide_csv'}})['download_id']
                        for _ in range(300):
                            status = server.downloads.status(ident)
                            if status['state'] in server.downloads.TERMINAL:
                                break
                            time.sleep(.01)
                        self.assertEqual(status['state'], 'saved', status)
                        self.assertEqual(Path(status['path']).read_bytes(), b'id,value\n1,42\n')
                        self.assertFalse(source.exists(), 'manager owns and cleans generated batch source')
            finally:
                server.server_close()

    def test_save_protects_selected_and_protected_inputs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            server = WorkbenchServer(root)
            output = server.output_root/'accepted'
            output.mkdir()
            (output/'manifest.json').write_text('{"status":"pass"}', encoding='utf-8')
            (output/'attributes.csv').write_bytes(b'id,value\n1,2\n')
            server.jobs['accepted'] = dict(status='pass', stage='done', operation='extract',
                result=dict(status='pass', output=str(output)))
            try:
                for destination_kind in ('selected', 'protected'):
                    target = root/(destination_kind+'.csv')
                    original = b'preserve this input'
                    target.write_bytes(original)
                    if destination_kind == 'selected':
                        server.selected_files.add(target)
                    else:
                        server.protected_inputs.add(target)
                    server.native_save = lambda _filename, path=target: (str(path),)
                    ident = server.downloads.start(
                        {'kind':'artifact','job_id':'accepted','name':'csv'})['download_id']
                    for _ in range(300):
                        state = server.downloads.status(ident)
                        if state['state'] in server.downloads.TERMINAL:
                            break
                        time.sleep(.01)
                    self.assertEqual(state['state'], 'error', state)
                    self.assertEqual(target.read_bytes(), original)
            finally:
                server.server_close()

    def test_save_rejects_hardlink_alias_to_any_retained_job_artifact(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            server = WorkbenchServer(root)
            output = server.output_root/'accepted'
            output.mkdir()
            (output/'manifest.json').write_text('{"status":"pass"}', encoding='utf-8')
            source = output/'attributes.csv'
            source.write_bytes(b'id,value\n1,2\n')
            other_output = server.output_root/'other-job'
            other_output.mkdir()
            other_artifact = other_output/'qa.json'
            other_original = b'{"status":"pass","other":true}'
            other_artifact.write_bytes(other_original)
            server.jobs['other-job'] = dict(status='pass', stage='done', operation='mesh_only',
                result=dict(status='pass', output=str(other_output)))
            server.jobs['accepted'] = dict(status='pass', stage='done', operation='extract',
                result=dict(status='pass', output=str(output)))
            alias = root/'alias.csv'
            try:
                try:
                    os.link(other_artifact, alias)
                except (OSError, NotImplementedError) as error:
                    self.skipTest('Hard links are unavailable: '+str(error))
                server.native_save = lambda _filename: (str(alias),)
                ident = server.downloads.start(
                    {'kind':'artifact','job_id':'accepted','name':'csv'})['download_id']
                for _ in range(300):
                    state = server.downloads.status(ident)
                    if state['state'] in server.downloads.TERMINAL:
                        break
                    time.sleep(.01)
                self.assertEqual(state['state'], 'error', state)
                self.assertEqual(source.read_bytes(), b'id,value\n1,2\n')
                self.assertEqual(other_artifact.read_bytes(), other_original)
                self.assertEqual(alias.read_bytes(), other_original)
            finally:
                server.server_close()

    def test_save_rejects_hardlink_alias_to_protected_workspace_input(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            server = WorkbenchServer(root)
            output = server.output_root/'accepted'
            output.mkdir()
            (output/'manifest.json').write_text('{"status":"pass"}', encoding='utf-8')
            (output/'attributes.csv').write_bytes(b'id,value\n1,2\n')
            server.jobs['accepted'] = dict(status='pass', stage='done', operation='extract',
                result=dict(status='pass', output=str(output)))
            workspace_input = root/'workspace-dem.tif'
            input_original = b'preserve workspace source'
            workspace_input.write_bytes(input_original)
            server.protected_inputs.add(workspace_input)
            alias = root/'protected-alias.csv'
            try:
                try:
                    os.link(workspace_input, alias)
                except (OSError, NotImplementedError) as error:
                    self.skipTest('Hard links are unavailable: '+str(error))
                server.native_save = lambda _filename: (str(alias),)
                ident = server.downloads.start(
                    {'kind':'artifact','job_id':'accepted','name':'csv'})['download_id']
                for _ in range(300):
                    state = server.downloads.status(ident)
                    if state['state'] in server.downloads.TERMINAL:
                        break
                    time.sleep(.01)
                self.assertEqual(state['state'], 'error', state)
                self.assertEqual(workspace_input.read_bytes(), input_original)
                self.assertEqual(alias.read_bytes(), input_original)
            finally:
                server.server_close()

    def test_failed_batch_is_rejected_before_save_dialog(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            server = WorkbenchServer(root)
            calls = []
            server.native_save = lambda name: calls.append(name)
            server.jobs['failed-batch'] = dict(status='fail', stage='done',
                operation='batch_extract', batch_output=str(root/'batch-output'),
                batch_database=str(root/'batch-output'/'batch.sqlite'), result={'status':'fail'})
            try:
                with self.assertRaisesRegex(ValueError, 'failed'):
                    server.downloads.start({'kind':'batch','request':
                        {'job_id':'failed-batch','format':'wide_csv'}})
                self.assertEqual(calls, [])
                self.assertFalse(server.downloads.active)
            finally:
                server.server_close()


if __name__ == '__main__':
    unittest.main()
