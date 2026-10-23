"""Allowlisted native controls for the Windows frameless desktop window."""
from __future__ import annotations

import ctypes
import os
from typing import Callable


_RESIZE_HIT_CODES = {
    'resize-n': 12,
    'resize-ne': 14,
    'resize-e': 11,
    'resize-se': 17,
    'resize-s': 15,
    'resize-sw': 16,
    'resize-w': 10,
    'resize-nw': 13,
}
_ALLOWED_ACTIONS = frozenset({'minimize', 'toggle_maximize', 'close', *_RESIZE_HIT_CODES})
_WM_NCLBUTTONDOWN = 0x00A1


class _POINT(ctypes.Structure):
    _fields_ = [('x', ctypes.c_int32), ('y', ctypes.c_int32)]


def _pack_screen_point(x: int, y: int) -> int:
    """Pack signed screen coordinates as the POINTS value used by WM_NC* messages."""
    return ((y & 0xFFFF) << 16) | (x & 0xFFFF)


def _post_resize_message(native, action: str, post_message=None, action_factory=None,
                         cursor_position=None, release_capture=None) -> bool:
    """Ask Windows to begin resizing from an allowlisted non-client edge."""
    hit_code = _RESIZE_HIT_CODES.get(action)
    if hit_code is None or native is None:
        return False

    if post_message is None or cursor_position is None or release_capture is None:
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        if post_message is None:
            post_message = user32.PostMessageW
            post_message.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
            post_message.restype = wintypes.BOOL
        if cursor_position is None:
            get_cursor_pos = user32.GetCursorPos
            get_cursor_pos.argtypes = (ctypes.POINTER(_POINT),)
            get_cursor_pos.restype = wintypes.BOOL

            def cursor_position():
                point = _POINT()
                if not get_cursor_pos(ctypes.byref(point)):
                    raise RuntimeError('Không đọc được vị trí con trỏ để đổi kích thước cửa sổ.')
                return point.x, point.y

        if release_capture is None:
            release_capture = user32.ReleaseCapture
            release_capture.argtypes = ()
            release_capture.restype = wintypes.BOOL
    if action_factory is None:
        from System import Action
        action_factory = Action

    def post():
        handle = native.Handle.ToInt64()
        if not handle:
            raise RuntimeError('Không lấy được cửa sổ Windows để thay đổi kích thước.')
        # WebView2 may own mouse capture from the titlebar pointer event. Release
        # it so DefWindowProc can complete the native non-client resize gesture.
        release_capture()
        x, y = cursor_position()
        screen_point = _pack_screen_point(x, y)
        if not post_message(handle, _WM_NCLBUTTONDOWN, hit_code, screen_point):
            raise RuntimeError('Windows không bắt đầu được thao tác đổi kích thước.')

    if native.InvokeRequired:
        native.Invoke(action_factory(post))
    else:
        post()
    return True


def install_window_controls(window, close_guard: Callable[[], object], *,
                            windows_chrome: bool | None = None):
    """Return the server callback, or ``None`` when the OS owns its titlebar."""
    if windows_chrome is None:
        windows_chrome = os.name == 'nt'
    if not windows_chrome:
        return None

    maximized = [bool(getattr(window, 'maximized', False))]

    def remember_maximized(*_):
        maximized[0] = True

    def remember_restored(*_):
        maximized[0] = False

    window.events.maximized += remember_maximized
    window.events.restored += remember_restored

    def control(action: str):
        if action not in _ALLOWED_ACTIONS:
            raise ValueError('Thao tác cửa sổ không được hỗ trợ.')

        if action == 'minimize':
            window.minimize()
            return {'ok': True}
        if action == 'toggle_maximize':
            prior_state = maximized[0]
            maximized[0] = not prior_state
            try:
                if not maximized[0]:
                    window.restore()
                else:
                    window.maximize()
            except Exception:
                maximized[0] = prior_state
                raise
            # pywebview emits these events on the native state transition.
            # Return the expected state now so the titlebar updates immediately.
            return {'maximized': maximized[0]}
        if action == 'close':
            if close_guard() is False:
                raise ValueError('Không thể đóng cửa sổ khi tác vụ hoặc hộp thoại đang hoạt động.')
            # Do not hold the server lock while entering pywebview's UI thread.
            window.destroy()
            return {'ok': True}

        if not _post_resize_message(window.native, action):
            raise ValueError('Không thể đổi kích thước cửa sổ này.')
        return {'ok': True}

    return control

