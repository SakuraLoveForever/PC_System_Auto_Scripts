"""Tray registration regressions without Tk or Explorer windows."""

import threading
import unittest
from unittest import mock

from PIL import Image
from pystray._win32 import win32

from main import App, I18n


class TrayTests(unittest.TestCase):
    def test_close_minimizes_when_tray_registration_fails(self):
        for failure in ("rejected", "backend exception", "late success", "late backend startup"):
            with self.subTest(failure=failure):
                self._check_close_fallback(failure)

    def _check_close_fallback(self, failure):
        app = App.__new__(App)
        app.cfg = {"minimize_to_tray": True}
        app._tray_icon = None
        app._tray_thread = None
        app._window_visible = True
        app._i18n = I18n()
        app._power_plans_cache = []
        app._tray_icon_image = lambda: Image.new("RGBA", (16, 16))
        app.withdraw = mock.Mock()
        app.iconify = mock.Mock()
        app._force_quit = mock.Mock()

        resume_native = threading.Event()
        stop_loop = threading.Event()

        def register_class(data):
            if failure == "late backend startup" and not resume_native.wait(timeout=3):
                raise RuntimeError("test did not release the window class registration call")
            return 1

        def shell_notify(code, data):
            if code == win32.NIM_ADD and failure == "late success":
                if not resume_native.wait(timeout=3):
                    raise RuntimeError("test did not release the native registration call")
                return 1
            return 0

        def next_message(*args):
            stop_loop.wait(timeout=5)
            return 0

        menu_window = OSError("menu window creation failed") if failure == "backend exception" else 3
        with mock.patch.multiple(
            win32, RegisterClassEx=register_class,
            CreateWindowEx=mock.Mock(side_effect=[2, menu_window]),
            ChangeWindowMessageFilterEx=mock.Mock(),
            PeekMessage=mock.Mock(), GetMessage=next_message,
            PostQuitMessage=lambda _: stop_loop.set(), PostMessage=mock.Mock(),
            DestroyWindow=mock.Mock(), UnregisterClass=mock.Mock(),
            CreatePopupMenu=mock.Mock(return_value=4),
            InsertMenuItem=mock.Mock(), DestroyMenu=mock.Mock(),
            LoadImage=mock.Mock(return_value=5), DestroyIcon=mock.Mock(),
            Shell_NotifyIcon=mock.Mock(side_effect=shell_notify),
        ):
            try:
                app._on_close()
            finally:
                resume_native.set()
            if app._tray_thread is not None:
                app._tray_thread.join(timeout=2)
                stopped = not app._tray_thread.is_alive()
                stop_loop.set()
                app._tray_thread.join(timeout=2)
                self.assertTrue(stopped)
            if app._tray_icon is not None:
                app._tray_icon.stop()
                app._tray_icon._release_icon()
                app._tray_icon = None
            win32.UnregisterClass.assert_called_once()
            commands = [call.args[0] for call in win32.Shell_NotifyIcon.call_args_list]
            if failure == "late success":
                self.assertIn(win32.NIM_ADD, commands)
                self.assertIn(win32.NIM_DELETE, commands)
            elif failure == "late backend startup":
                self.assertNotIn(win32.NIM_ADD, commands)

        app.withdraw.assert_not_called()
        app.iconify.assert_called_once_with()
        app._force_quit.assert_not_called()
