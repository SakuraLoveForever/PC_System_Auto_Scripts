"""Dev-only visual check: build the real window and grab a screenshot.

Usage:
    python tools/capture_ui.py [output.png] [--compact] [--dialog setup|confirm|target]

Requires a display. Runs the REAL event loop (mainloop), reads the real system
state read-only, and keeps its config in a temporary directory. This is a
development aid, not part of the shipped application.
"""

from __future__ import annotations

import argparse
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
    parser.add_argument("--compact", action="store_true")
    parser.add_argument("--dialog", choices=["setup", "confirm", "target"], default=None)
    parser.add_argument("--size", default="1000x660")
    args = parser.parse_args()

    tmp = Path(tempfile.mkdtemp())
    os.environ["PC_AUTO_SCRIPTS_DATA_DIR"] = str(tmp)

    import main as m

    m.CONFIG_FILE = tmp / "config.json"
    m.DATA_DIR = tmp

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)

    patcher = mock.patch.object(m.App, "_show_first_run_setup", return_value=None)
    with mock.patch.object(m.App, "_setup_tray", return_value=False), \
         mock.patch.object(m, "_start_ipc_server", return_value=True), \
         patcher:
        app = m.App()
        app.deiconify()
        app.geometry(f"{args.size}+60+60")

        def step_compact():
            app._set_compact_mode(True)

        def step_capture():
            _grab(app, output, dialog=bool(args.dialog))
            _close_modal(app)
            if args.dialog:
                return
            shutdown()

        def step_dialog():
            if args.dialog in ("setup", "target", "confirm"):
                app.after(900, lambda: _finish_dialog(app, output))
            if args.dialog == "setup":
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

        def _finish_dialog(app, output):
            _grab(app, output, dialog=True)
            _close_modal(app)

        def shutdown():
            if getattr(app, "_pc_shutdown", False):
                return
            app._pc_shutdown = True
            app._closing = True
            app._stop_monitor(save=False)
            try:
                app.quit()
            except Exception:
                pass

        if args.dialog:
            app.after(2600, step_dialog)
            app.after(15000, shutdown)  # safety net
        elif args.compact:
            app.after(1800, step_compact)
            app.after(2600, step_capture)
            app.after(15000, shutdown)
        else:
            app.after(2600, step_capture)
            app.after(15000, shutdown)

        app.mainloop()

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
    try:
        from PIL import ImageGrab
    except ImportError:
        print("Pillow is required for screenshots", file=sys.stderr)
        return

    windows = [app]
    if dialog:
        modal = _modal(app)
        if modal is not None:
            windows.append(modal)

    x = min(w.winfo_rootx() for w in windows)
    y = min(w.winfo_rooty() for w in windows)
    right = max(w.winfo_rootx() + w.winfo_width() for w in windows)
    bottom = max(w.winfo_rooty() + w.winfo_height() for w in windows)
    ImageGrab.grab(bbox=(x, y, right, bottom), all_screens=True).save(output)


if __name__ == "__main__":
    sys.exit(main())
