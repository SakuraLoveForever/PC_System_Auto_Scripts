"""Dev-only visual check: capture a real Tk window on a private desktop.

Usage:
    python tools/capture_ui.py [output.png] [--page power|startup|settings]
    python tools/capture_ui.py [output.png] [--dialog setup|confirm|target]

Uses sample power plans and startup entries, so captures never include user data
or start a monitor or tray icon. The interactive Windows desktop stays put.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

import customtkinter as ctk

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", nargs="?", default=str(PROJECT / "shot" / "ui.png"))
    parser.add_argument("--dialog", choices=["setup", "confirm", "target"], default=None)
    parser.add_argument("--page", choices=["power", "startup", "settings"], default="power")
    parser.add_argument("--size", help="override the default full window size")
    args = parser.parse_args()

    from tests.isolated_desktop import enter_isolated_desktop

    enter_isolated_desktop()
    with tempfile.TemporaryDirectory(prefix="pc-auto-capture-") as temp:
        os.environ["PC_AUTO_SCRIPTS_DATA_DIR"] = temp
        try:
            return _capture(args)
        finally:
            logging.shutdown()


def _capture(args) -> int:
    import main as m
    from power_manager import PlanSnapshot, PowerPlan
    from startup_manager import StartupItem

    plans = PlanSnapshot(plans=[
        PowerPlan("381b4222-f694-41f0-9685-ff5bb260df2e", "平衡", is_active=True),
        PowerPlan("8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c", "高性能", is_acceptable=True),
        PowerPlan("a1841308-3541-4fab-bc81-f71556f20b4a", "节能"),
    ])
    items = [
        StartupItem("系统自动助手", r'"C:\Apps\SystemAssistant.exe" --startup', "registry"),
        StartupItem("同步工具", r'"C:\Program Files\Sync\Sync.exe" --background', "registry"),
        StartupItem("音频控制台", r"C:\Program Files\Audio\AudioConsole.exe", "registry_hklm"),
        StartupItem("桌面便签", r"C:\Users\Demo\Startup\桌面便签.lnk", "startup_folder"),
        StartupItem("备份提醒", r"C:\ProgramData\Startup\备份提醒.lnk", "startup_folder_common"),
    ]
    cfg = dict(m.CONFIG_DEFAULTS, monitor_enabled=False, setup_completed=True,
               start_to_tray=False)
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    with mock.patch.object(m.App, "_setup_tray", return_value=False), \
         mock.patch.object(m, "load_config", return_value=cfg), \
         mock.patch.object(m, "get_power_snapshot", return_value=plans), \
         mock.patch.object(m, "get_all_items", return_value=items):
        app = m.App()
        if args.size:
            app.geometry(f"{args.size}+60+60")
        else:
            app.geometry("+60+60")
        app.deiconify()
        captured = False

        def prepare():
            if not app.winfo_ismapped():
                app.deiconify()
                app.after(200, prepare)
                return
            if args.page != "power":
                app._show_page(args.page)
            app.after(250, step_dialog if args.dialog else step_capture)

        def step_capture():
            nonlocal captured
            if args.dialog and _modal(app) is None:
                # On a busy host the timer can fire before Tk maps the dialog.
                app.after(100, step_capture)
                return
            try:
                _grab(app, output, dialog=bool(args.dialog))
                captured = True
            finally:
                if args.dialog:
                    _close_modal(app)
                else:
                    shutdown()

        def step_dialog():
            app.after(400, step_capture)
            if args.dialog == "setup":
                app.cfg["setup_completed"] = False
                app._show_first_run_setup()
            elif args.dialog == "target":
                app._pick_target_from_setup(enable=False)
            else:
                app._ask_confirm(
                    "删除启动项？",
                    "将从系统中移除以下启动项：\n\n名称：demo\n来源：用户启动文件夹\n"
                    "命令/文件：\nC:\\Users\\me\\Startup\\demo.cmd\n\n"
                    "删除前会自动保存备份，可用「恢复」撤销。",
                    danger=True)
            # wait_window has returned: the modal is gone, safe to tear down.
            shutdown()

        def shutdown():
            app._closing = True
            app._stop_monitor(save=False)
            _close_modal(app)
            app.quit()

        app.after(1800, prepare)
        app.after(30000, shutdown)
        app.mainloop()
        app.destroy()

    if not captured:
        print("Window capture failed", file=sys.stderr)
        return 1
    print(f"saved {output}")
    return 0


def _modal(app):
    for child in app.winfo_children():
        try:
            if isinstance(child, ctk.CTkToplevel) and child.winfo_viewable():
                return child
        except Exception:
            continue
    return None


def _close_modal(app) -> None:
    window = _modal(app)
    if window is None:
        return
    try:
        app._close_modal(window, None)
    except Exception:
        pass


def _grab(app, output: Path, dialog: bool = False) -> None:
    import ctypes
    from ctypes import wintypes
    from tkinter import Entry, Text
    from PIL import ImageGrab

    window = _modal(app) if dialog else app
    if window is None or not window.winfo_viewable():
        raise RuntimeError("No mapped Tk window to capture")
    hwnd = int(window.wm_frame(), 0)
    redraw = ctypes.windll.user32.RedrawWindow
    redraw.argtypes = (wintypes.HWND, wintypes.LPVOID, wintypes.HANDLE, wintypes.UINT)
    redraw.restype = wintypes.BOOL
    # Force an inactive desktop to paint the Tk canvas and its child controls.
    redraw(hwnd, None, None, 0x0185)
    window.update_idletasks()
    # Pillow renders by HWND; a screen capture would read the user's desktop.
    result = ImageGrab.grab(window=hwnd)
    widgets = list(window.winfo_children())
    while widgets:
        widget = widgets.pop()
        if not widget.winfo_viewable() or widget.winfo_toplevel() is not window:
            continue
        if isinstance(widget, (Entry, Text)):
            handle = widget.winfo_id()
            redraw(handle, None, None, 0x0185)
            widget.update_idletasks()
            result.paste(ImageGrab.grab(window=handle),
                         (widget.winfo_rootx() - window.winfo_rootx(),
                          widget.winfo_rooty() - window.winfo_rooty()))
        widgets.extend(widget.winfo_children())
    result.save(output)


if __name__ == "__main__":
    sys.exit(main())
