import http.client
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest

from gis_mesh_workbench.webui import WorkbenchServer


class DownloadFlowTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.server = WorkbenchServer(self.root)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.output = self.server.output_root / 'download-fixture'
        self.output.mkdir()
        (self.output / 'manifest.json').write_text('{"status":"pass"}', encoding='utf-8')
        (self.output / 'attributes.csv').write_bytes(b'cell_id,value\nc0,10\n')
        self.server.jobs['accepted'] = dict(status='pass', stage='done', operation='extract',
            result=dict(status='pass', output=str(self.output)))

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(3)
        self.temporary.cleanup()

    def request(self, method, path, data=None, token=True):
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=4)
        headers = {'Origin': self.server.origin, 'Content-Type': 'application/json'}
        if token: headers['X-Workbench-Token'] = self.server.token
        connection.request(method, path, json.dumps(data) if data is not None else None, headers)
        response = connection.getresponse()
        status, body = response.status, json.loads(response.read())
        connection.close()
        return status, body

    def terminal(self, ident):
        for _ in range(100):
            status, body = self.request('GET', '/api/download/status?id=' + ident)
            self.assertEqual(status, 200, body)
            if body['state'] in {'saved', 'cancelled', 'error'}: return body
            time.sleep(.01)
        self.fail('download did not terminate')

    def test_save_as_reports_exact_destination_and_progress(self):
        target = self.root / 'chosen.csv'
        self.server.native_save = lambda filename: (str(target),)
        status, body = self.request('POST', '/api/download/start',
            {'kind': 'artifact', 'job_id': 'accepted', 'name': 'csv'})
        self.assertEqual(status, 200, body)
        done = self.terminal(body['download_id'])
        self.assertEqual(done['state'], 'saved', done)
        self.assertEqual(Path(done['path']), target)
        self.assertEqual(done['bytes_written'], done['total_bytes'])
        self.assertEqual(target.read_bytes(), (self.output / 'attributes.csv').read_bytes())

    def test_cancel_save_dialog_has_no_saved_claim_or_file(self):
        self.server.native_save = lambda filename: None
        status, body = self.request('POST', '/api/download/start',
            {'kind': 'artifact', 'job_id': 'accepted', 'name': 'csv'})
        self.assertEqual(status, 200, body)
        done = self.terminal(body['download_id'])
        self.assertEqual(done['state'], 'cancelled')
        self.assertIsNone(done.get('path'))

    def test_no_arbitrary_client_destination_or_unauthenticated_start(self):
        selected = []
        self.server.native_save = lambda filename: selected.append(filename)
        data = {'kind': 'artifact', 'job_id': 'accepted', 'name': 'csv'}
        self.assertEqual(self.request('POST', '/api/download/start', data, token=False)[0], 403)
        self.assertEqual(self.request('POST', '/api/download/start',
            dict(data, path=str(self.root / 'client-chosen.csv')))[0], 400)
        self.assertFalse(selected)

    def test_multiple_raster_picker_grants_only_selected_files_and_companions(self):
        # A normal file selection grants no access to its containing directory.
        with tempfile.TemporaryDirectory() as external:
            first=Path(external)/'dem.asc';first.write_text('fixture',encoding='utf-8')
            second=Path(external)/'rain.tif';second.write_bytes(b'fixture')
            sibling=Path(external)/'private.txt';sibling.write_text('private',encoding='utf-8')
            self.server.native_picker=lambda kind:(str(first),str(second))
            status,body=self.request('POST','/api/picker',{'kind':'raster_many'})
            self.assertEqual(status,200,body)
            self.assertEqual(body['paths'],[str(first),str(second)])
            self.assertEqual(self.server.permitted(first),first)
            with self.assertRaises(ValueError):self.server.permitted(sibling)

    def test_invalid_multiselection_grants_nothing(self):
        with tempfile.TemporaryDirectory() as external:
            first=Path(external)/'dem.asc';first.write_text('fixture',encoding='utf-8')
            invalid=Path(external)/'other.exe';invalid.write_bytes(b'fixture')
            self.server.native_picker=lambda kind:(str(first),str(invalid))
            status,_=self.request('POST','/api/picker',{'kind':'raster_many'})
            self.assertEqual(status,400)
            with self.assertRaises(ValueError):self.server.permitted(first)

    def test_cancel_during_copy_keeps_existing_destination_and_removes_partial(self):
        target = self.root/'already-there.csv'
        target.write_bytes(b'original destination')
        self.server.native_save = lambda filename: (str(target),)
        manager = self.server.downloads
        manager.CHUNK_BYTES = 1
        copied = threading.Event(); continue_copy = threading.Event()
        original_cancelled = manager._cancelled
        def pause_after_first_chunk(ident):
            if manager.status(ident)['bytes_written'] > 0 and not copied.is_set():
                copied.set()
                continue_copy.wait(3)
            return original_cancelled(ident)
        manager._cancelled = pause_after_first_chunk
        status, body = self.request('POST', '/api/download/start',
            {'kind':'artifact','job_id':'accepted','name':'csv'})
        self.assertEqual(status, 200, body)
        ident = body['download_id']
        self.assertTrue(copied.wait(3), 'copy worker did not reach the first chunk')
        status, cancelled = self.request('POST', '/api/download/cancel', {'id':ident})
        self.assertEqual(status, 200, cancelled)
        continue_copy.set()
        done = self.terminal(ident)
        self.assertEqual(done['state'], 'cancelled')
        self.assertEqual(target.read_bytes(), b'original destination')
        self.assertFalse(list(self.root.glob('.already-there.csv.*.partial')))

    def test_save_rejects_selected_input_and_symlink_destination(self):
        selected = self.root/'input.tif'
        selected.write_bytes(b'preserve input')
        self.server.selected_files.add(selected)
        self.server.native_save = lambda filename: (str(selected),)
        status, body = self.request('POST', '/api/download/start',
            {'kind':'artifact','job_id':'accepted','name':'csv'})
        self.assertEqual(status, 200, body)
        done = self.terminal(body['download_id'])
        self.assertEqual(done['state'], 'error')
        self.assertEqual(selected.read_bytes(), b'preserve input')

        link = self.root/'linked.csv'; target = self.root/'link-target.csv'
        target.write_bytes(b'preserve target')
        try:
            link.symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest('Symbolic links are unavailable in this environment')
        self.server.selected_files.clear()
        self.server.native_save = lambda filename: (str(link),)
        status, body = self.request('POST', '/api/download/start',
            {'kind':'artifact','job_id':'accepted','name':'csv'})
        self.assertEqual(status, 200, body)
        done = self.terminal(body['download_id'])
        self.assertEqual(done['state'], 'error')
        self.assertTrue(link.is_symlink())
        self.assertEqual(target.read_bytes(), b'preserve target')


if __name__ == '__main__': unittest.main()
