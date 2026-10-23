import unittest
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

from gis_mesh_workbench import native_chrome
from gis_mesh_workbench import desktop
from gis_mesh_workbench.webui import WorkbenchServer


class Event:
    def __iadd__(self, callback):
        self.callback = callback
        return self


class FakeWindow:
    def __init__(self, maximized=False):
        self.maximized = maximized
        self.native = Mock()
        self.events = Mock()
        self.events.closing = Event()
        self.events.maximized = Event()
        self.events.restored = Event()
        self.minimize = Mock()
        self.maximize = Mock()
        self.restore = Mock()
        self.destroy = Mock()


class NativeChromeTests(unittest.TestCase):
    def test_desktop_uses_frameless_custom_chrome_and_registers_callback(self):
        with tempfile.TemporaryDirectory() as directory:
            server = WorkbenchServer(directory)
            webview = MagicMock()
            webview.settings = {'DRAG_REGION_SELECTOR': 'old'}
            native = FakeWindow()
            webview.create_window.return_value = native

            def start(**_):
                args, kwargs = webview.create_window.call_args
                self.assertEqual(args[0], 'MeshCherry')
                self.assertTrue(kwargs['frameless'])
                self.assertFalse(kwargs['easy_drag'])
                self.assertEqual(kwargs['min_size'], (820, 620))
                self.assertEqual(webview.settings['DRAG_REGION_SELECTOR'], '.pywebview-drag-region')
                self.assertTrue(callable(server.native_window_control))

            webview.start.side_effect = start
            with patch.object(desktop, 'WorkbenchServer', return_value=server):
                desktop.launch(directory, webview_module=webview,
                               preferences=Path(directory) / 'prefs.json')
            self.assertEqual(server.socket.fileno(), -1)

    def test_non_windows_uses_os_titlebar(self):
        window = FakeWindow()
        self.assertIsNone(native_chrome.install_window_controls(
            window, lambda: None, windows_chrome=False))

    def test_minimize_maximize_toggle_and_window_events(self):
        window = FakeWindow()
        control = native_chrome.install_window_controls(
            window, lambda: None, windows_chrome=True)

        self.assertEqual(control('minimize'), {'ok': True})
        window.minimize.assert_called_once_with()
        self.assertEqual(control('toggle_maximize'), {'maximized': True})
        window.maximize.assert_called_once_with()
        self.assertEqual(control('toggle_maximize'), {'maximized': False})
        window.restore.assert_called_once_with()

        window.events.maximized.callback()
        self.assertEqual(control('toggle_maximize'), {'maximized': False})
        window.events.restored.callback()
        self.assertEqual(control('toggle_maximize'), {'maximized': True})

    def test_failed_maximize_rolls_back_expected_state(self):
        window = FakeWindow()
        window.maximize.side_effect = RuntimeError('native maximize failed')
        control = native_chrome.install_window_controls(
            window, lambda: None, windows_chrome=True)
        with self.assertRaisesRegex(RuntimeError, 'native maximize failed'):
            control('toggle_maximize')
        window.maximize.side_effect = None
        self.assertEqual(control('toggle_maximize'), {'maximized': True})

    def test_close_checks_existing_guard_before_destroy(self):
        window = FakeWindow()
        busy = Mock(return_value=False)
        control = native_chrome.install_window_controls(
            window, busy, windows_chrome=True)
        with self.assertRaisesRegex(ValueError, 'tác vụ hoặc hộp thoại'):
            control('close')
        window.destroy.assert_not_called()

        idle = Mock(return_value=None)
        control = native_chrome.install_window_controls(
            window, idle, windows_chrome=True)
        self.assertEqual(control('close'), {'ok': True})
        idle.assert_called_once_with()
        window.destroy.assert_called_once_with()

    def test_callback_rejects_arbitrary_actions(self):
        window = FakeWindow()
        control = native_chrome.install_window_controls(
            window, lambda: None, windows_chrome=True)
        for action in ('run_js', 'execute', 'resize-x', 'close;anything'):
            with self.subTest(action=action), self.assertRaisesRegex(ValueError, 'không được hỗ trợ'):
                control(action)

    def test_resize_edges_use_only_native_hit_test_codes(self):
        native = Mock()
        native.InvokeRequired = False
        native.Handle.ToInt64.return_value = 0x123456789
        sent = []
        released = Mock()
        for action, hit_code in native_chrome._RESIZE_HIT_CODES.items():
            with self.subTest(action=action):
                self.assertTrue(native_chrome._post_resize_message(
                    native, action,
                    post_message=lambda *args: sent.append(args) or 1,
                    action_factory=lambda callback: callback(),
                    cursor_position=lambda: (1700, 950), release_capture=released))
                self.assertEqual(sent[-1], (0x123456789, 0x00A1, hit_code,
                                            native_chrome._pack_screen_point(1700, 950)))
        self.assertEqual(released.call_count, len(native_chrome._RESIZE_HIT_CODES))

        self.assertFalse(native_chrome._post_resize_message(
            native, 'resize-x', post_message=Mock(), action_factory=lambda callback: callback(),
            cursor_position=lambda: (0, 0), release_capture=Mock()))
        self.assertEqual(native_chrome._pack_screen_point(-200, 300), 0x012CFF38)

    def test_resize_marshals_to_native_ui_thread(self):
        native = Mock()
        native.InvokeRequired = True
        native.Handle.ToInt64.return_value = 321
        posted = []
        released = Mock()
        native.Invoke.side_effect = lambda callback: callback()
        self.assertTrue(native_chrome._post_resize_message(
            native, 'resize-ne', post_message=lambda *args: posted.append(args) or 1,
            action_factory=lambda callback: callback,
            cursor_position=lambda: (600, 400), release_capture=released))
        native.Invoke.assert_called_once()
        released.assert_called_once_with()
        self.assertEqual(posted, [(321, 0x00A1, 14,
                                   native_chrome._pack_screen_point(600, 400))])


if __name__ == '__main__':
    unittest.main()
