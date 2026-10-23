"""Authenticated native controls and browser fallback contract."""
import json
import tempfile
import threading
import unittest
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from unittest.mock import Mock

from gis_mesh_workbench.webui import WorkbenchServer


class WindowControlsTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.server=WorkbenchServer(self.directory.name)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)
        self.directory.cleanup()

    def post(self, data, **headers):
        defaults={'Content-Type':'application/json','Origin':self.server.origin,
                  'X-Workbench-Token':self.server.token}
        defaults.update(headers)
        request=Request(self.server.origin+'/api/window/control',
                        data=json.dumps(data).encode(),headers=defaults)
        try:
            response=urlopen(request)
        except HTTPError as error:
            response=error
        with response:
            return response.status,json.loads(response.read())

    def test_valid_action_and_busy_rejection(self):
        callback=Mock(return_value={'maximized':True})
        self.server.native_window_control=callback
        self.assertEqual(self.post({'action':'toggle_maximize'}),(200,{'maximized':True}))
        callback.assert_called_once_with('toggle_maximize')
        callback.side_effect=ValueError('Đang xử lý, chưa thể đóng.')
        status,body=self.post({'action':'close'})
        self.assertEqual(status,400)
        self.assertIn('Đang xử lý',body['error'])

    def test_invalid_actions_never_reach_native(self):
        callback=Mock()
        self.server.native_window_control=callback
        for payload in ([],{}, {'action':[]},{'action':'execute'},
                        {'action':'close','code':'anything'}):
            with self.subTest(payload=payload):
                self.assertEqual(self.post(payload)[0],400)
        callback.assert_not_called()

    def test_security_gate_and_browser_fallback(self):
        callback=Mock(return_value={'ok':True})
        self.server.native_window_control=callback
        for headers in ({'Origin':'https://example.com'},
                        {'X-Workbench-Token':'incorrect'}):
            self.assertEqual(self.post({'action':'close'},**headers)[0],403)
        callback.assert_not_called()
        self.server.native_window_control=None
        self.assertEqual(self.post({'action':'close'})[0],400)

    def test_html_mode_and_script(self):
        for enabled in (False,True):
            self.server.native_window_control=Mock() if enabled else None
            with urlopen(self.server.origin+'/') as response:
                html=response.read().decode()
            self.assertIn('data-desktop-chrome="'+str(enabled).lower()+'"',html)
            self.assertNotIn('{{DESKTOP_CHROME}}',html)
        with urlopen(self.server.origin+'/window_chrome.js') as response:
            self.assertIn('javascript',response.headers['Content-Type'])
            self.assertIn(b'MeshCherryChrome',response.read())


if __name__=='__main__':
    unittest.main()
