"""Keep real Tk windows off the interactive Windows desktop during tests."""

import ctypes
from ctypes import wintypes
import os
import threading


_desktop = None


def enter_isolated_desktop() -> None:
    """Attach the main thread to a private, inactive desktop before creating Tk."""
    global _desktop
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError("GUI test isolation must be set up on the main thread")
    if _desktop is not None:
        return

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.CreateDesktopW.argtypes = (
        wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
    )
    user32.CreateDesktopW.restype = wintypes.HANDLE
    user32.SetThreadDesktop.argtypes = (wintypes.HANDLE,)
    user32.SetThreadDesktop.restype = wintypes.BOOL
    user32.CloseDesktop.argtypes = (wintypes.HANDLE,)
    user32.CloseDesktop.restype = wintypes.BOOL

    # DESKTOP_READOBJECTS | CREATEWINDOW | CREATEMENU | WRITEOBJECTS.
    # No SwitchDesktop call: only this thread moves; the user stays where they are.
    desktop = user32.CreateDesktopW(
        f"PCSystemAutoScriptsTest-{os.getpid()}", None, None, 0, 0x0087, None,
    )
    if not desktop:
        raise ctypes.WinError(ctypes.get_last_error())
    if not user32.SetThreadDesktop(desktop):
        error = ctypes.get_last_error()
        user32.CloseDesktop(desktop)
        raise ctypes.WinError(error)
    # The thread must stay on this desktop until process exit, which closes it.
    _desktop = desktop
