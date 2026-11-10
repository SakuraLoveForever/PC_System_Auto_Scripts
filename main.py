"""
PC System Auto Scripts — Power Plan Monitor & Startup Manager
Chinese desktop control center for power plans and startup items.
"""

from __future__ import annotations

import ctypes
import io
import json
import logging
import logging.handlers
import os
import queue
import re
import socket
import sys
import threading
import time
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional

import customtkinter as ctk
from PIL import Image, ImageDraw
from tkinter import Frame as TkFrame, Menu, StringVar, filedialog, ttk, font as tkfont

from pypinyin import lazy_pinyin, Style
from styles import STYLES, STYLE_ALIASES, DEFAULT_STYLE, DesignStyle
import rem_theme
from power_manager import (
    PowerMonitor,
    create_missing_builtin_schemes,
    find_auto_target,
    find_plan_by_guid,
    get_power_snapshot,
    is_plan_matching_target,
    set_unsupported_targets,
    unsupported_targets,
)
from startup_manager import (
    StartupItem,
    StartupReadError,
    add_registry_startup,
    build_self_startup_parts,
    get_all_items,
    get_registry_startup_command,
    is_known_source,
    list_backups,
    open_startup_location,
    normalize_key,
    remove_with_backup,
    restore_backup,
)
from i18n import I18n, DEFAULT_LANG

log = logging.getLogger("pc_auto_scripts")

# ---------------------------------------------------------------------------
# Paths & config
# ---------------------------------------------------------------------------
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILENAME = "config.json"
ICON_FILE = BASE_DIR / "app_icon.ico"

APP_DIR_NAME = "PC_System_Auto_Scripts"

CONFIG_DEFAULTS = {
    "style": DEFAULT_STYLE,
    "language": DEFAULT_LANG,
    "monitor_enabled": False,
    "check_interval": 60,
    "target_guid": "",
    "minimize_to_tray": True,
    "start_to_tray": True,
    "col_widths": [0.24, 0.43, 0.12, 0.18],
    "setup_completed": False,
    # Guids Windows refused to activate (usually shadowed built-in templates).
    "unsupported_targets": [],
}

INT_MIN, INT_MAX = 10, 3600
COL_MIN_VALUE, COL_MAX_VALUE = 0.02, 0.9
COL_LEN = 4

CONFIG_FILE_ERROR = ""

_GUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def _dir_is_writable(path: Path) -> bool:
    probe = path / ".pc_auto_scripts_write_test"
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def _resolve_data_dir() -> Path:
    """Data directory: explicit override, then the portable folder, then the profile.

    The override (``PC_AUTO_SCRIPTS_DATA_DIR``) keeps automated checks and shared
    installs from ever writing into the application folder.
    """
    override = os.environ.get("PC_AUTO_SCRIPTS_DATA_DIR", "").strip()
    if override:
        try:
            path = Path(override).expanduser()
            path.mkdir(parents=True, exist_ok=True)
            return path
        except OSError:
            log.warning("PC_AUTO_SCRIPTS_DATA_DIR=%s is not usable; falling back", override)
    if _dir_is_writable(BASE_DIR):
        return BASE_DIR
    local = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or ""
    fallback = Path(local) / APP_DIR_NAME if local else Path.home() / f".{APP_DIR_NAME}"
    try:
        fallback.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return fallback


DATA_DIR = _resolve_data_dir()


def _resolve_config_file() -> Path:
    """config.json next to the exe, or the user-profile copy when read-only."""
    portable = BASE_DIR / CONFIG_FILENAME
    if DATA_DIR == BASE_DIR:
        return portable
    migrated = DATA_DIR / CONFIG_FILENAME
    override = os.environ.get("PC_AUTO_SCRIPTS_DATA_DIR", "").strip()
    if override and DATA_DIR == Path(override).expanduser():
        return migrated
    if portable.exists() and not migrated.exists():
        try:
            migrated.write_text(portable.read_text(encoding="utf-8"), encoding="utf-8")
        except OSError:
            pass
    return migrated


CONFIG_FILE = _resolve_config_file()


def _enable_dpi_awareness() -> str:
    """Make the process DPI-aware before Tk starts.

    Without this, Windows virtualizes coordinates for an unaware process while
    CustomTkinter still applies its own DPI scaling, so every widget is drawn at
    the wrong size, the window is inflated past the screen and large blank areas
    appear. Must run before the Tk root window is created.
    """
    try:
        # PER_MONITOR_AWARE_V2 = -4
        if ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return "per-monitor-v2"
    except (AttributeError, OSError):
        pass
    try:
        if ctypes.windll.shcore.SetProcessDpiAwareness(2) == 0:   # PROCESS_PER_MONITOR_DPI_AWARE
            return "per-monitor"
    except (AttributeError, OSError):
        pass
    try:
        if ctypes.windll.user32.SetProcessDPIAware():
            return "system"
    except (AttributeError, OSError):
        pass
    return "none"


def setup_logging() -> None:
    """Rolling log file in the data directory; never fatal if unavailable."""
    if log.handlers:
        return
    log.setLevel(logging.INFO)
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            DATA_DIR / "pc_auto_scripts.log", maxBytes=512 * 1024,
            backupCount=3, encoding="utf-8")
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s: %(message)s"))
        log.addHandler(handler)
    except OSError:
        pass


def _coerce_bool(value, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("true", "1", "yes", "on"):
            return True
        if lowered in ("false", "0", "no", "off"):
            return False
    return default


def _coerce_interval(value, default: int) -> int:
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    if number < INT_MIN or number > INT_MAX:
        return default
    return number


def _coerce_guid(value) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return text.lower() if _GUID_RE.match(text) else ""


def _coerce_choice(value, allowed, default: str) -> str:
    text = str(value or "").strip()
    return text if text in allowed else default


def _coerce_col_widths(value) -> List[float]:
    if not isinstance(value, (list, tuple)) or len(value) != COL_LEN:
        return list(CONFIG_DEFAULTS["col_widths"])
    widths: List[float] = []
    for entry in value:
        try:
            number = float(entry)
        except (TypeError, ValueError):
            return list(CONFIG_DEFAULTS["col_widths"])
        if number != number or number < COL_MIN_VALUE or number > COL_MAX_VALUE:
            return list(CONFIG_DEFAULTS["col_widths"])
        widths.append(number)
    return widths


def _should_start_to_tray(cfg: dict, args) -> bool:
    return "--startup" in args and bool(cfg.get("start_to_tray", True))


def _normalize_config(raw) -> dict:
    """Validate an arbitrary JSON object into a complete, usable config."""
    cfg = dict(CONFIG_DEFAULTS)
    if not isinstance(raw, dict):
        return cfg

    cfg["style"] = _coerce_choice(raw.get("ui_theme"), STYLES, DEFAULT_STYLE)
    if "ui_theme" in raw:
        cfg["ui_theme"] = cfg["style"]
    cfg["language"] = DEFAULT_LANG
    cfg["monitor_enabled"] = _coerce_bool(raw.get("monitor_enabled"), CONFIG_DEFAULTS["monitor_enabled"])
    cfg["check_interval"] = _coerce_interval(raw.get("check_interval"), CONFIG_DEFAULTS["check_interval"])
    cfg["target_guid"] = _coerce_guid(raw.get("target_guid"))
    cfg["minimize_to_tray"] = _coerce_bool(raw.get("minimize_to_tray"), True)
    cfg["start_to_tray"] = _coerce_bool(raw.get("start_to_tray"), True)
    cfg["col_widths"] = _coerce_col_widths(raw.get("col_widths"))
    cfg["setup_completed"] = _coerce_bool(raw.get("setup_completed"), False)
    cfg["unsupported_targets"] = _coerce_guid_list(raw.get("unsupported_targets"))
    return cfg


def _coerce_guid_list(value) -> List[str]:
    if not isinstance(value, (list, tuple)):
        return []
    guids = []
    for entry in value:
        guid = _coerce_guid(entry)
        if guid and guid not in guids:
            guids.append(guid)
    return guids


def load_config() -> dict:
    """Load and validate config.json.

    New configurations leave automatic monitoring disabled until setup opts in.
    """
    global CONFIG_FILE_ERROR
    CONFIG_FILE_ERROR = ""
    first_run = not CONFIG_FILE.exists()
    raw = None
    if not first_run:
        try:
            raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            CONFIG_FILE_ERROR = f"config.json 解析失败，已回退默认值 ({exc})"
            _quarantine_config()
        except OSError as exc:
            CONFIG_FILE_ERROR = f"无法读取 config.json：{exc}"
    cfg = _normalize_config(raw)
    set_unsupported_targets(cfg.get("unsupported_targets"))
    if first_run or CONFIG_FILE_ERROR:
        ok, error = save_config(cfg)
        if not ok:
            CONFIG_FILE_ERROR = CONFIG_FILE_ERROR or error
    return cfg


def _quarantine_config(keep: bool = True) -> None:
    """Keep a copy of a damaged config so the user can inspect it."""
    if not keep or not CONFIG_FILE.exists():
        return
    try:
        CONFIG_FILE.replace(CONFIG_FILE.with_suffix(".json.corrupt"))
    except OSError:
        pass


def save_config(cfg: dict) -> tuple:
    """Write config atomically. Returns (ok, error_message)."""
    temp = CONFIG_FILE.with_suffix(".json.tmp")
    try:
        payload = json.dumps(dict(cfg), indent=2, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        return False, f"配置内容无法序列化：{exc}"
    try:
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(temp, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, CONFIG_FILE)
        return True, ""
    except OSError as exc:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
        log.warning("config save failed: %s", exc)
        return False, f"无法保存配置到 {CONFIG_FILE}：{exc}"


# ---------------------------------------------------------------------------
# Single-instance enforcement
#
# Ownership is an atomic per-session Windows named mutex taken before the GUI
# exists; the TCP socket is only the wake-up channel, so an unrelated service
# holding the port can never terminate this app or swallow a launch.
# ---------------------------------------------------------------------------
_SINGLE_INSTANCE_PORT = 53942
_INSTANCE_MUTEX_NAME = "Local\\PC_System_Auto_Scripts_SingleInstance"
_IPC_PROTOCOL = b"PCAS/1 show\n"
_ERROR_ALREADY_EXISTS = 183

_instance_mutex_handle = None
_ipc_server_socket = None


def _acquire_instance_mutex() -> bool:
    """True when this process owns the single-instance lock."""
    global _instance_mutex_handle
    if _instance_mutex_handle is not None:
        return True
    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
        handle = kernel32.CreateMutexW(None, True, _INSTANCE_MUTEX_NAME)
        if not handle:
            return False
        if kernel32.GetLastError() == _ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(ctypes.c_void_p(handle))
            return False
        _instance_mutex_handle = handle
        return True
    except (AttributeError, OSError):
        # Non-Windows or API unavailable: fall back to the socket probe.
        return not _is_already_running()


def _release_instance_mutex() -> None:
    global _instance_mutex_handle
    handle = _instance_mutex_handle
    if handle is None:
        return
    _instance_mutex_handle = None
    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.ReleaseMutex(ctypes.c_void_p(handle))
        kernel32.CloseHandle(ctypes.c_void_p(handle))
    except (AttributeError, OSError):
        pass


def _wake_existing_instance() -> bool:
    """Ask a running instance to show its window. True when it answered."""
    try:
        with socket.create_connection(("127.0.0.1", _SINGLE_INSTANCE_PORT), timeout=1.0) as sock:
            sock.settimeout(2.0)
            sock.sendall(_IPC_PROTOCOL)
            try:
                ack = sock.recv(64)
            except (socket.timeout, OSError):
                ack = b""
            return ack.startswith(b"PCAS/1 ok")
    except (OSError, TimeoutError):
        return False


def _is_already_running() -> bool:
    """Probe the IPC port. Only used when the named mutex is unavailable."""
    return _wake_existing_instance()


def _start_ipc_server(app) -> bool:
    """Listen for wake-up messages from other instance attempts."""
    global _ipc_server_socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind(("127.0.0.1", _SINGLE_INSTANCE_PORT))
        s.listen(2)
        s.settimeout(1.0)
    except OSError as exc:
        log.warning("IPC listen failed on port %s: %s", _SINGLE_INSTANCE_PORT, exc)
        s.close()
        return False
    _ipc_server_socket = s

    def _serve():
        while getattr(app, "_running", True):
            try:
                conn, _ = s.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                conn.settimeout(2.0)  # a silent client must not block wake-ups
                try:
                    data = conn.recv(128)
                except (socket.timeout, OSError):
                    data = b""
                if data.startswith(_IPC_PROTOCOL) or data.strip() == b"show":
                    try:
                        conn.sendall(b"PCAS/1 ok\n")
                    except OSError:
                        pass
                    app.after(0, app._restore_from_ipc)
            finally:
                try:
                    conn.close()
                except OSError:
                    pass
        try:
            s.close()
        except OSError:
            pass

    threading.Thread(target=_serve, name="IPC", daemon=True).start()
    return True


def _stop_ipc_server() -> None:
    global _ipc_server_socket
    s = _ipc_server_socket
    _ipc_server_socket = None
    if s is None:
        return
    try:
        s.close()
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Tray icon
# ---------------------------------------------------------------------------

def _create_icon_image(size: int = 64, color: str = "#5e6ad2") -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    margin = 4
    draw.rounded_rectangle([margin, margin, size - margin, size - margin], radius=12, fill=color)
    cx, cy = size // 2, size // 2
    pts = [(cx + 8, cy - 16), (cx - 4, cy - 2), (cx + 2, cy - 2),
           (cx - 8, cy + 16), (cx + 4, cy + 2), (cx - 2, cy + 2)]
    draw.polygon(pts, fill="white")
    return img


TRAY_ICON_SIZES = [(16, 16), (32, 32), (48, 48), (256, 256)]


_UI_FONT = ""


def _ui_font_family() -> str:
    """Pick one installed family that covers both CJK and Latin.

    Windows ships Microsoft YaHei UI with Segoe UI metrics; Segoe UI alone has
    no CJK glyphs, which is why every Chinese label fell back to a second font.
    """
    global _UI_FONT
    if _UI_FONT:
        return _UI_FONT
    try:
        available = {name.lower(): name for name in tkfont.families()}
    except Exception:
        available = {}
    for candidate in ("Microsoft YaHei UI", "Microsoft YaHei", "微软雅黑",
                      "Noto Sans CJK SC", "Source Han Sans SC"):
        hit = available.get(candidate.lower())
        if hit:
            _UI_FONT = hit
            return hit
    _UI_FONT = "TkDefaultFont"
    return _UI_FONT


def _create_tray_icon(color_hex: str = "#5e6ad2", art: Image.Image | None = None):
    """Multi-resolution tray icon, built in memory.

    pystray serialises icon.icon to a temp file on every change, so the source
    must stay a PIL image; sharing app_icon.ico made a stale read invisible.
    """
    img = art.resize((256, 256), Image.LANCZOS) if art else _create_icon_image(256, color_hex)
    buf = io.BytesIO()
    img.save(buf, format="ICO", sizes=TRAY_ICON_SIZES)
    buf.seek(0)
    out = Image.open(buf)
    out.load()
    return out


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
FULL_SIZE = "580x290"
FULL_MIN_SIZE = (510, 270)
COL_KEYS = ("name", "path", "source")
COL_MIN_WIDTHS = (75, 115, 66)
AUTO_TARGET_LABEL = "自动选择"
SELF_STARTUP_NAME = "PC_System_Auto_Scripts"


# ======================================================================
# Style helper — apply a DesignStyle to a widget
# ======================================================================

def apply_btn_style(btn: ctk.CTkButton, s: DesignStyle):
    """Style a primary button."""
    btn.configure(fg_color=s.accent, hover_color=s.accent_hover,
                  text_color=s.accent_text,
                  border_color=s.accent_hover,
                  border_width=1,
                  corner_radius=min(s.button_radius, 18))


def apply_btn_secondary(btn: ctk.CTkButton, s: DesignStyle):
    """Style a CTkButton as a secondary/surface button."""
    btn.configure(fg_color=s.card_elevated, hover_color=s.border_strong,
                  text_color=s.text_primary,
                  border_color=s.border,
                  border_width=1,
                  corner_radius=min(s.button_radius, 18))


def _compact_popup(menu: Menu, labels=()):
    menu.configure(borderwidth=1, activeborderwidth=0)
    for index, label in enumerate(labels):
        if menu.entrycget(index, "label") != label:
            menu.entryconfigure(index, label=label)


def apply_dropdown(dd: ctk.CTkOptionMenu, s: DesignStyle):
    """Style a CTkOptionMenu."""
    dd.configure(fg_color=s.card, text_color=s.text_primary,
                 button_color=s.accent, button_hover_color=s.accent_hover,
                 corner_radius=8,
                 dropdown_fg_color=s.card, dropdown_text_color=s.text_primary,
                 dropdown_hover_color=s.card_elevated)
    # CTk rebuilds padded labels when values change and resets borders on DPI changes.
    menu = dd._dropdown_menu
    if not menu.cget("postcommand"):
        menu.configure(font=ctk.CTkFont(family=_ui_font_family(), size=12),
                       postcommand=lambda: _compact_popup(menu, dd.cget("values")))


def apply_entry(entry: ctk.CTkEntry, s: DesignStyle):
    """Style a CTkEntry."""
    entry.configure(fg_color=s.card, text_color=s.text_primary,
                    placeholder_text_color=s.text_muted,
                    border_color=s.border,
                    border_width=1,
                    corner_radius=min(s.input_radius, 8))


def apply_switch(sw: ctk.CTkSwitch, s: DesignStyle):
    """Style a CTkSwitch."""
    # The transparent default border shrinks the track but leaves the thumb full height.
    sw.configure(switch_height=16, border_width=0,
                  progress_color=s.accent, button_color=s.text_primary,
                  text_color=s.text_primary)


def apply_card(card: ctk.CTkFrame, s: DesignStyle):
    """Style a card frame."""
    card.configure(fg_color=s.card, corner_radius=s.card_radius,
                   border_width=1, border_color=s.border)


def apply_surface(sf: ctk.CTkFrame, s: DesignStyle):
    """Style a surface frame."""
    sf.configure(fg_color=s.surface)


def _ellipsize(widget, message, width):
    text = message.replace("\n", " ")
    font = widget.cget("font")
    if isinstance(font, ctk.CTkFont):
        font = font.create_scaled_tuple(ctk.ScalingTracker.get_widget_scaling(widget))
    measure = tkfont.Font(root=widget, font=font).measure
    if measure(text) <= width:
        return text
    low, high = 0, len(text)
    while low < high:
        mid = (low + high + 1) // 2
        if measure(text[:mid] + "…") <= width:
            low = mid
        else:
            high = mid - 1
    return text[:low] + "…"


# ======================================================================
# Main Application
# ======================================================================

class App(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.cfg = load_config()
        self._style: DesignStyle = STYLES[self.cfg["style"]]
        self._i18n = I18n()
        self._monitor_running = False
        self._window_visible = True
        self._ipc_restore_pending = False
        self._tray_icon = None
        self._tray_thread = None
        self._closing = False
        self._running = True  # for IPC server loop

        # Worker threads never touch Tk directly: they post closures here and the
        # main thread drains the queue. Calling Tk from another thread raises
        # "main thread is not in main loop" and silently loses results.
        self._ui_queue: "queue.Queue" = queue.Queue()
        self._ui_drain_id = None

        # Sort state for startup list
        self._sort_key: str = "name"
        self._sort_ascending: bool = True
        self._startup_source_filter = "all"
        self._source_filter_label_to_key: Dict[str, str] = {}

        self._col_widths = list(self.cfg["col_widths"])
        self._stylables: List[tuple] = []
        self._page = None
        self._startup_ready = False
        self._startup_rows: Dict[str, StartupItem] = {}
        self._startup_query = StringVar(master=self, value="")
        self._startup_search_job = None
        self._check_running = False
        self._switch_running = False
        self._status_clear_job = None
        self._active_plan_name = "读取中…"
        self._status_message = ""
        self._status_kind = "info"
        self._startup_busy = False
        self._startup_backups: list[dict] = []
        self._startup_items_cache: List[StartupItem] = []
        self._startup_refresh_running = False
        self._startup_refresh_pending = False
        self._power_refresh_running = False
        self._power_refresh_pending = False
        self._power_plans_cache: List = []
        # GUID ↔ dropdown/channel labels
        self._target_guid_map: Dict[str, Optional[str]] = {AUTO_TARGET_LABEL: None}
        self._label_by_guid: Dict[str, str] = {}
        self._creating_schemes = False

        self.power_monitor = PowerMonitor(
            on_status_change=self._on_power_status_change,
            interval_seconds=self.cfg["check_interval"],
            on_monitor_state_change=self._on_monitor_state_change,
        )
        if self.cfg.get("target_guid"):
            self.power_monitor.target_guid = self.cfg["target_guid"]

        self.title(self._i18n.t("app.title"))
        self.geometry(FULL_SIZE)
        self.minsize(*FULL_MIN_SIZE)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # Do NOT call set_default_color_theme — we manage colors manually
        ctk.set_appearance_mode("dark")
        self.configure(fg_color=self._style.canvas)

        self._root_frame = ctk.CTkFrame(self, fg_color=self._style.canvas, corner_radius=0)
        self._root_frame.pack(fill="both", expand=True)
        self._build_shell()
        self._build_power_page(self._pages["power"])
        self._build_startup_page(self._pages["startup"])
        self._build_settings_page(self._pages["settings"])
        self._sync_startup_controls()
        self._paint_page_art()
        self._show_page("power")
        self.after_idle(self._finish_startup)

    def _finish_startup(self) -> None:
        if self._closing:
            return
        self._refresh_ui_queue()
        first_run = not self.cfg["setup_completed"]
        if not first_run and self.cfg["monitor_enabled"]:
            self._start_monitor(save=False)
        self._sync_monitor_switch()
        self._refresh_power_status()
        self._refresh_startup_list()
        if first_run:
            self.after(400, self._show_first_run_setup)
        elif _should_start_to_tray(self.cfg, sys.argv[1:]) and self._setup_tray():
            self._window_visible = False
            self.withdraw()
        if not first_run and CONFIG_FILE_ERROR:
            self._show_check_result(CONFIG_FILE_ERROR, "warning")

    # ==================================================================
    # Reporting helpers
    # ==================================================================

    def _post_ui(self, func) -> None:
        """Queue ``func`` for the Tk main thread. Safe from any thread."""
        try:
            self._ui_queue.put(func)
        except Exception:
            log.debug("UI queue unavailable; dropping callback")

    def _refresh_ui_queue(self) -> None:
        """Main-thread drain loop for background results."""
        self._ui_drain_id = None
        while True:
            try:
                func = self._ui_queue.get_nowait()
            except queue.Empty:
                break
            try:
                func()
            except Exception:
                log.exception("UI callback failed")
        if self._closing:
            return
        try:
            self._ui_drain_id = self.after(40, self._refresh_ui_queue)
        except Exception:
            self._ui_drain_id = None

    def _report(self, msg: str, kind: str = "info", persistent: bool = False):
        """Show a message without ever raising from a background callback."""
        try:
            self._show_check_result(msg, kind, persistent=persistent)
        except Exception:
            log.exception("could not show message: %s", msg)

    def _persist_config(self) -> bool:
        # Remember which targets Windows refuses, so a dead target is never
        # retried on every check.
        self.cfg["unsupported_targets"] = unsupported_targets()
        ok, error = save_config(self.cfg)
        if not ok:
            log.warning("config not saved: %s", error)
            self._report(error, "error", persistent=True)
        return ok

    def _art_active(self) -> bool:
        return bool(self._style.char_palette)

    def _theme_palette(self) -> Dict[str, str]:
        return rem_theme.palette_from_theme(self._style)

    def _paint_page_art(self):
        if not self._art_active():
            self._brand_icon.grid_remove()
            self._brand_icon.configure(image=None)
            return
        palette = self._theme_palette()
        ring = self._style.char_ring or self._style.accent
        self._brand_icon.configure(text="", image=ctk.CTkImage(
            light_image=rem_theme.rem_avatar(64, palette, ring), size=(22, 22)))
        self._brand_icon.grid(row=0, column=4, padx=(0, 8))

    def _reg(self, widget, styler):
        styler(widget, self._style)
        self._stylables.append((widget, styler))
        return widget

    def _switch_style(self, label: str):
        style = STYLES.get(STYLE_ALIASES.get(label, label))
        if style is None or style is self._style:
            return
        self._style = style
        self.cfg["style"] = self.cfg["ui_theme"] = style.name
        self._persist_config()
        for widget, styler in self._stylables:
            styler(widget, style)
        self.configure(fg_color=style.canvas)
        self._root_frame.configure(fg_color=style.canvas)
        self._paint_page_art()
        self._update_nav_style()
        self._style_startup_table()
        self._fit_status_text()
        self._update_tray_icon_color()

    def _label(self, parent, text="", *, size=13, role="text_primary", bold=False, **kwargs):
        return self._reg(ctk.CTkLabel(parent, text=text, anchor="w",
                        font=ctk.CTkFont(family=_ui_font_family(), size=size,
                                         weight="bold" if bold else "normal"), **kwargs),
                         lambda widget, style: widget.configure(text_color=getattr(style, role)))

    def _button(self, parent, text, command, *, width=96, height=28, primary=False, **kwargs):
        button = ctk.CTkButton(parent, text=text, command=command, width=width, height=height,
                               font=ctk.CTkFont(family=_ui_font_family(), size=12,
                                                weight="bold" if primary else "normal"), **kwargs)
        self._reg(button, apply_btn_style if primary else apply_btn_secondary)
        TkFrame.configure(button, takefocus=True)

        def activate(_event):
            if button.cget("state") != "disabled":
                button.invoke()
            return "break"

        for key in ("<Return>", "<space>"):
            TkFrame.bind(button, key, activate)
        TkFrame.bind(button, "<FocusIn>", lambda _: button.configure(border_color=self._style.accent))
        TkFrame.bind(button, "<FocusOut>", lambda _: button.configure(
            border_color=self._style.accent_hover if primary else self._style.border))
        return button

    def _entry(self, parent, width=120, **kwargs):
        return self._reg(ctk.CTkEntry(parent, width=width, height=28,
                         font=ctk.CTkFont(family=_ui_font_family(), size=12), **kwargs), apply_entry)

    def _dropdown(self, parent, values, width, command):
        return self._reg(ctk.CTkOptionMenu(parent, values=values, width=width, height=28,
                         dynamic_resizing=False, command=command,
                         font=ctk.CTkFont(family=_ui_font_family(), size=12)), apply_dropdown)

    def _card(self, parent):
        return self._reg(ctk.CTkFrame(parent), apply_card)

    PAGE_TITLES = {"power": "电源", "startup": "启动项", "settings": "设置"}

    def _build_shell(self):
        shell = self._root_frame
        shell.grid_columnconfigure(0, weight=1)
        shell.grid_rowconfigure(1, weight=1)
        tabs = self._reg(ctk.CTkFrame(shell, corner_radius=0), apply_surface)
        tabs.grid(row=0, column=0, sticky="ew")
        tabs.grid_columnconfigure(3, weight=1)
        self._nav_buttons = {}
        for column, (key, title) in enumerate(self.PAGE_TITLES.items()):
            button = self._button(tabs, title, lambda page=key: self._show_page(page),
                                  width=72, height=30)
            button.grid(row=0, column=column, padx=(5, 0), pady=4)
            self._nav_buttons[key] = button
        self._brand_icon = self._label(tabs, width=24, size=14, role="accent")

        host = ctk.CTkFrame(shell, fg_color="transparent", corner_radius=0)
        host.grid(row=1, column=0, padx=7, pady=6, sticky="nsew")
        host.grid_rowconfigure(0, weight=1)
        host.grid_columnconfigure(0, weight=1)
        self._pages = {
            key: ctk.CTkFrame(host, fg_color="transparent", corner_radius=0)
            for key in self.PAGE_TITLES
        }

        feedback = ctk.CTkFrame(tabs, fg_color="transparent", height=24, corner_radius=0)
        feedback.grid(row=0, column=3, padx=(9, 7), sticky="ew")
        feedback.pack_propagate(False)
        self._status_details_btn = self._button(
            feedback, "详情", self._show_status_details, width=46, height=22)
        self._check_result_label = self._label(feedback, size=12, role="text_secondary")
        self._check_result_label.pack(side="left", fill="both", expand=True)
        feedback.bind("<Configure>", lambda _: self._fit_status_text())

    def _show_page(self, key: str):
        if self._closing or key == self._page:
            return
        if self._page is not None:
            self._pages[self._page].grid_remove()
        self._page = key
        self._pages[key].grid(row=0, column=0, sticky="nsew")
        self._update_nav_style()

    def _update_nav_style(self):
        s = self._style
        for key, button in self._nav_buttons.items():
            selected = key == self._page
            button.configure(fg_color=s.card_elevated if selected else s.surface,
                             text_color=s.accent if selected else s.text_secondary,
                             border_color=s.border_strong if selected else s.surface,
                             hover_color=s.card_elevated)

    def _build_power_page(self, page):
        card = self._power_card = self._card(page)
        card.pack(fill="x")
        summary = ctk.CTkFrame(card, fg_color="transparent")
        summary.pack(fill="x", padx=9, pady=(5, 2))
        summary.grid_columnconfigure(1, weight=1)
        self._active_plan_caption = self._label(summary, "当前方案", role="text_secondary")
        self._active_plan_caption.grid(row=0, column=0, sticky="w")
        self._power_active_value = self._label(summary, self._active_plan_name, size=17, bold=True)
        self._power_active_value.grid(row=0, column=1, sticky="ew", padx=8)
        self._power_status_badge = self._label(summary, "正在读取系统状态", role="text_secondary")
        self._power_status_badge.grid(row=0, column=2, sticky="e")
        summary.bind("<Configure>", lambda _: self._fit_active_plan_name())

        actions = ctk.CTkFrame(card, fg_color="transparent")
        actions.pack(fill="x", padx=9, pady=(0, 8))
        self._power_refresh_btn = self._button(actions, "刷新状态", self._refresh_power_status,
                                               width=70)
        self._power_refresh_btn.configure(command=lambda: self._refresh_power_status()
                                          if not self._power_refresh_running else None)
        self._power_refresh_btn.pack(side="left", padx=(0, 5))
        self._power_switch_btn = self._button(actions, "切换方案",
                                              lambda: self._show_plan_menu(self._power_switch_btn),
                                              width=76)
        self._power_switch_btn.pack(side="left", padx=(0, 5))
        self._check_btn = self._button(actions, "立即纠正", self._check_now,
                                       width=78, primary=True)
        self._check_btn.pack(side="left")
        self._create_schemes_btn = self._button(actions, "创建性能方案",
                                                self._create_missing_schemes, width=98)
        self._create_schemes_btn.pack(side="right")

        policy = self._card(page)
        policy.pack(fill="x", pady=(6, 0))
        row = ctk.CTkFrame(policy, fg_color="transparent")
        row.pack(fill="x", padx=9, pady=(5, 2))
        self._monitor_switch = self._reg(ctk.CTkSwitch(
            row, text=self._i18n.t("power.auto_monitor"), command=self._toggle_monitor,
            font=ctk.CTkFont(family=_ui_font_family(), size=12)), apply_switch)
        self._monitor_switch.pack(side="left")

        options = ctk.CTkFrame(policy, fg_color="transparent")
        options.pack(fill="x", padx=9, pady=(0, 8))
        self._label(options, "目标", role="text_secondary").grid(row=0, column=0, padx=(0, 6))
        self._target_dropdown = self._dropdown(options, [AUTO_TARGET_LABEL], 200,
                                               self._on_target_changed)
        self._target_dropdown.grid(row=0, column=1, sticky="ew")
        self._set_target_selection()
        self._label(options, "间隔（秒）", role="text_secondary").grid(row=0, column=2, padx=(9, 5))
        self._interval_entry = self._entry(options, width=52)
        self._interval_entry.grid(row=0, column=3)
        self._interval_entry.insert(0, str(self.cfg["check_interval"]))
        self._interval_entry.bind("<Return>", lambda _: self._apply_interval())
        self._apply_interval_btn = self._button(options, "应用", self._apply_interval, width=50)
        self._apply_interval_btn.grid(row=0, column=4, padx=(5, 0))

    def _build_settings_page(self, page):
        card = self._card(page)
        card.pack(fill="x")
        card.grid_columnconfigure(0, weight=1)

        self._label(card, "界面主题", role="text_secondary").grid(
            row=0, column=0, sticky="w", padx=9, pady=5)
        captions = {"System": "系统深色", "Rem": "雷姆蓝调"}
        choices = {captions.get(key, key): key for key in STYLES}
        self._style_dropdown = self._dropdown(card, list(choices), 138,
                                              lambda choice: self._switch_style(choices[choice]))
        self._style_dropdown.set(captions.get(self._style.name, self._style.name))
        self._style_dropdown.grid(row=0, column=1, sticky="e", padx=9, pady=5)

        for index, (key, caption) in enumerate((
            ("minimize_to_tray", "关闭窗口时保留在托盘"),
            ("start_to_tray", "随 Windows 启动时进入托盘"),
        ), start=1):
            self._label(card, caption, role="text_secondary").grid(
                row=index, column=0, sticky="w", padx=9, pady=4)
            switch = self._reg(ctk.CTkSwitch(card, text="", width=40), apply_switch)
            switch.configure(command=lambda k=key, w=switch: self._set_setting(k, bool(w.get())))
            if self.cfg[key]:
                switch.select()
            switch.grid(row=index, column=1, sticky="e", padx=9, pady=4)

        self._label(card, "程序随 Windows 启动", role="text_secondary").grid(
            row=3, column=0, sticky="w", padx=9, pady=5)
        self._startup_self_btn = self._button(card, "启用自启", self._toggle_self_startup, width=82)
        self._startup_self_btn.grid(row=3, column=1, sticky="e", padx=9, pady=5)

    def _set_setting(self, key, value):
        self.cfg[key] = value
        self._persist_config()

    # ==================================================================
    # Target plan selection (GUID-bound)
    # ==================================================================

    def _build_target_labels(self, plans) -> List[str]:
        """Labels that carry the GUID, with a short GUID suffix on name clashes.

        Plans Windows refuses to activate (built-in templates shadowed by a real
        duplicate) are not offered at all, so picking a target always works.
        """
        name_counts: Dict[str, int] = {}
        for plan in plans:
            key = plan.name.strip().lower()
            name_counts[key] = name_counts.get(key, 0) + 1

        auto_target = find_auto_target(plans)

        self._target_guid_map = {AUTO_TARGET_LABEL: None}
        self._label_by_guid = {}
        values: List[str] = [AUTO_TARGET_LABEL]
        for plan in plans:
            if not plan.is_usable:
                continue
            if auto_target is not None and plan.guid == auto_target.guid:
                marker = "★ "          # the plan the Auto policy will maintain
            elif plan.is_acceptable:
                marker = "✓ "          # usable, but not the Auto preference
            else:
                marker = ""
            label = f"{marker}{plan.name}"
            if name_counts.get(plan.name.strip().lower(), 0) > 1:
                label = f"{label} ({plan.guid[:8].upper()})"
            if label in self._target_guid_map:
                label = f"{label} ({plan.guid[:8].upper()})"
            values.append(label)
            self._target_guid_map[label] = plan.guid
            self._label_by_guid[plan.guid] = label
        return values

    def _missing_target_label(self, target_guid: str) -> str:
        return self._i18n.t("power.target_missing_option", guid=target_guid[:8].upper())

    def _set_target_selection(self) -> None:
        """Reflect the monitor's target GUID in the dropdown, without callbacks."""
        target_guid = self.power_monitor.target_guid or ""
        if not target_guid:
            self._target_dropdown.set(AUTO_TARGET_LABEL)
            return
        label = self._label_by_guid.get(target_guid)
        if label is not None:
            self._target_dropdown.set(label)
            return
        # The configured target no longer exists: never silently fall back.
        placeholder = self._missing_target_label(target_guid)
        if placeholder in self._target_dropdown.cget("values"):
            self._target_dropdown.set(placeholder)
        else:
            self._target_dropdown.set(AUTO_TARGET_LABEL)

    def _on_target_changed(self, choice: str):
        if getattr(self, "_syncing_target_dropdown", False):
            return
        if choice == AUTO_TARGET_LABEL:
            guid = ""
        elif choice not in self._target_guid_map:
            self._show_check_result(self._i18n.t("power.target_missing_option",
                                                 guid=choice[:8].upper()), "error",
                                    persistent=True)
            self._set_target_selection()
            return
        else:
            guid = self._target_guid_map[choice] or ""
        self.power_monitor.target_guid = guid or None
        self.cfg["target_guid"] = guid
        if self._power_plans_cache:
            self._update_power_ui(
                next((plan for plan in self._power_plans_cache if plan.is_active), None),
                self._power_plans_cache)
        if self._persist_config():
            self._show_check_result("已保存监控目标", "ok")

    # ==================================================================
    # Startup items card
    # ==================================================================

    def _build_startup_page(self, page):
        card = self._startup_card = self._card(page)
        card.pack(fill="both", expand=True)
        card.grid_columnconfigure(0, weight=1)
        card.grid_rowconfigure(1, weight=1)
        toolbar = ctk.CTkFrame(card, fg_color="transparent")
        toolbar.grid(row=0, column=0, sticky="ew", padx=7, pady=(6, 4))
        toolbar.grid_columnconfigure(1, weight=1)
        self._label(toolbar, "搜索", role="text_secondary").grid(
            row=0, column=0, padx=(0, 7))
        self._startup_search_entry = self._entry(
            toolbar, width=82, textvariable=self._startup_query)
        self._startup_search_entry.grid(row=0, column=1, sticky="ew", padx=(0, 6))
        self._startup_query.trace_add("write", self._schedule_startup_search)
        self._source_filter_dropdown = self._dropdown(
            toolbar, [self._i18n.t("startup.source_all")], 108, self._on_source_filter_changed)
        self._source_filter_dropdown.grid(row=0, column=2, padx=(0, 6))
        self._startup_refresh_btn = self._button(
            toolbar, "刷新", self._request_startup_refresh, width=54)
        self._startup_refresh_btn.grid(row=0, column=3, padx=(0, 6))
        self._startup_restore_btn = self._button(
            toolbar, "恢复", self._restore_last_removed, width=62)
        self._startup_restore_btn.grid(row=0, column=4)
        self._startup_add_toggle = self._button(
            toolbar, "添加", self._toggle_startup_add_form, width=52, primary=True)
        self._startup_add_toggle.grid(row=0, column=5, padx=(6, 0))

        table_box = ctk.CTkFrame(card, fg_color="transparent")
        table_box.grid(row=1, column=0, sticky="nsew", padx=7)
        table_box.grid_columnconfigure(0, weight=1)
        table_box.grid_rowconfigure(0, weight=1)
        self._startup_table = table = ttk.Treeview(
            table_box, columns=COL_KEYS, show="headings", selectmode="browse",
            style="Startup.Treeview", height=2)
        table.grid(row=0, column=0, sticky="nsew")
        scrollbar = ctk.CTkScrollbar(table_box, width=13, command=table.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        table.configure(yscrollcommand=scrollbar.set)
        for key in COL_KEYS:
            table.column(key, stretch=False)
            table.heading(key, anchor="w", command=lambda column=key: self._toggle_sort(column))
        table.bind("<Configure>", self._size_startup_columns)
        table.bind("<Button-1>", self._start_startup_column_drag, add="+")
        table.bind("<ButtonRelease-1>", self._save_startup_columns, add="+")
        table.bind("<<TreeviewSelect>>", self._update_selected_startup)
        table.bind("<Return>", lambda _: self._on_selected_startup(self._open_startup_location))
        table.bind("<Delete>", lambda _: self._on_selected_startup(self._remove_startup_item))
        self._style_startup_table()
        self._update_sort_indicator()

        row = ctk.CTkFrame(card, fg_color="transparent")
        row.grid(row=2, column=0, sticky="ew", padx=7, pady=4)
        row.grid_columnconfigure(0, weight=1)
        self._startup_count_label = self._label(row, "读取启动项…", role="text_secondary")
        self._startup_count_label.grid(row=0, column=0, sticky="w")
        self._startup_open_btn = self._button(
            row, "打开位置", lambda: self._on_selected_startup(self._open_startup_location),
            width=74, height=28)
        self._startup_open_btn.grid(row=0, column=1, padx=(6, 0))
        self._startup_delete_btn = self._button(
            row, "删除", lambda: self._on_selected_startup(self._remove_startup_item),
            width=54, height=28)
        self._startup_delete_btn.grid(row=0, column=2, padx=(6, 0))

        form = self._startup_add_form = ctk.CTkFrame(card, fg_color="transparent")
        form.grid_columnconfigure(3, weight=1)
        self._label(form, "名称", role="text_secondary").grid(row=0, column=0, padx=(0, 5))
        self._startup_name_entry = self._entry(form, width=86)
        self._startup_name_entry.grid(row=0, column=1, padx=(0, 7))
        self._label(form, "路径", role="text_secondary").grid(row=0, column=2, padx=(0, 5))
        self._startup_path_entry = self._entry(form, width=86)
        self._startup_path_entry.grid(row=0, column=3, sticky="ew", padx=(0, 6))
        self._startup_path_entry.bind("<Return>", lambda _: self._add_startup_item())
        self._startup_browse_btn = self._button(form, "浏览", self._browse_startup_path, width=52)
        self._startup_browse_btn.grid(row=0, column=4, padx=(0, 6))
        self._startup_add_btn = self._button(form, "保存", self._add_startup_item,
                                             width=54, primary=True)
        self._startup_add_btn.grid(row=0, column=5)

    def _toggle_startup_add_form(self):
        if self._startup_add_form.winfo_manager():
            self._hide_startup_add_form()
        else:
            self._startup_add_form.grid(row=3, column=0, sticky="ew", padx=7, pady=(0, 6))
            self._startup_add_toggle.configure(text="收起")
            self._startup_name_entry.focus_set()

    def _hide_startup_add_form(self):
        focused = self.focus_get()
        while focused is not None and focused is not self._startup_add_form:
            focused = focused.master
        self._startup_add_form.grid_remove()
        self._startup_add_toggle.configure(text="添加")
        if self._page == "startup" and focused is self._startup_add_form:
            self._startup_add_toggle.focus_set()

    def _style_startup_table(self):
        s = self._style
        style = ttk.Style(self._startup_table)
        style.theme_use("clam")
        scale = self._tree_scale = ctk.ScalingTracker.get_widget_scaling(self._startup_card)
        font_size = -round(12 * scale)
        for column, minimum in zip(COL_KEYS, COL_MIN_WIDTHS):
            self._startup_table.column(column, minwidth=round(minimum * scale))
        style.configure("Startup.Treeview", background=s.card, fieldbackground=s.card,
                        foreground=s.text_primary, bordercolor=s.border, borderwidth=1,
                        lightcolor=s.border, darkcolor=s.border,
                        rowheight=round(24 * scale), font=(_ui_font_family(), font_size))
        style.configure("Startup.Treeview.Heading", background=s.surface, foreground=s.text_secondary,
                        bordercolor=s.border, lightcolor=s.border, darkcolor=s.border,
                        relief="flat", padding=(round(6 * scale), round(4 * scale)),
                        font=(_ui_font_family(), font_size, "bold"))
        style.map("Startup.Treeview", background=[("selected", s.border_strong)],
                  foreground=[("selected", s.text_primary)])
        style.map("Startup.Treeview.Heading", background=[("active", s.card_elevated)])
        self._startup_table.tag_configure("alternate", background=s.surface)

    def _size_startup_columns(self, event):
        if ctk.ScalingTracker.get_widget_scaling(self._startup_card) != self._tree_scale:
            self._style_startup_table()
        self._layout_startup_columns(event.width)

    def _layout_startup_columns(self, viewport):
        width = viewport - 2
        min_name, min_path, min_source = (round(n * self._tree_scale) for n in COL_MIN_WIDTHS)
        if width < min_name + min_path + min_source:
            return
        weights = self._col_widths[:3]
        total = sum(weights)
        name = min(width - min_path - min_source, max(min_name, round(width * weights[0] / total)))
        source = min(width - name - min_path, max(min_source, round(width * weights[2] / total)))
        for column, size in zip(COL_KEYS, (name, width - name - source, source)):
            self._startup_table.column(column, width=size)

    def _start_startup_column_drag(self, event):
        table = self._startup_table
        self._dragged_startup_column = table.identify_region(event.x, event.y) == "separator"

    def _save_startup_columns(self, _event):
        if not getattr(self, "_dragged_startup_column", False):
            return
        self._dragged_startup_column = False
        table = self._startup_table
        widths = [table.column(key, "width") for key in COL_KEYS]
        total = sum(widths)
        if total:
            # Preserve the retired fourth (action) slot for older configs.
            retained = self._col_widths[3]
            self._col_widths = [max(COL_MIN_VALUE, (1 - retained) * w / total)
                                for w in widths] + [retained]
            self.cfg["col_widths"] = self._col_widths
            self._layout_startup_columns(table.winfo_width())
            self._persist_config()

    def _update_selected_startup(self, _event=None):
        selected = bool(self._startup_table.selection())
        state = "normal" if selected and not self._startup_busy and not self._startup_refresh_running else "disabled"
        self._startup_open_btn.configure(state=state)
        self._startup_delete_btn.configure(state=state)

    def _on_selected_startup(self, action):
        selected = self._startup_table.selection()
        if selected and selected[0] in self._startup_rows:
            action(self._startup_rows[selected[0]])

    def _schedule_startup_search(self, *_):
        if self._startup_search_job is not None:
            self.after_cancel(self._startup_search_job)
        self._startup_search_job = self.after(45, self._filter_startup_items)

    def _filter_startup_items(self):
        self._startup_search_job = None
        self._render_startup_items(self._sorted_startup_items())

    def _show_plan_menu(self, button):
        plans = self._power_plans_cache
        if self._switch_running or self._check_running:
            return
        if not plans:
            self._refresh_power_status()
            return
        counts: Dict[str, int] = {}
        for plan in plans:
            key = plan.name.strip().casefold()
            counts[key] = counts.get(key, 0) + 1
        old_menu = getattr(self, "_plan_menu", None)
        if old_menu is not None:
            old_menu.destroy()
        s = self._style
        scale = ctk.ScalingTracker.get_widget_scaling(button)
        menu = self._plan_menu = Menu(
            self, tearoff=False, relief="flat",
            font=(_ui_font_family(), -round(12 * scale)),
            background=s.card, foreground=s.text_primary,
            activebackground=s.card_elevated, activeforeground=s.text_primary)
        _compact_popup(menu)
        for plan in plans:
            label = plan.name
            if counts[plan.name.strip().casefold()] > 1:
                label += f" ({plan.guid[:8].upper()})"
            if plan.is_active:
                label += "  ✓"
            menu.add_command(label=label, command=lambda guid=plan.guid: self._do_switch_plan(guid))
        try:
            menu.tk_popup(button.winfo_rootx(), button.winfo_rooty() + button.winfo_height())
        finally:
            menu.grab_release()

    # ==================================================================
    # Startup list data and actions
    # ==================================================================

    def _startup_source_labels(self) -> Dict[str, str]:
        return {
            "registry": self._i18n.t("startup.source_registry"),
            "registry_hklm": self._i18n.t("startup.source_registry_hklm"),
            "registry_hklm_wow6432": self._i18n.t("startup.source_registry_hklm_wow6432"),
            "startup_folder": self._i18n.t("startup.source_startup_folder"),
            "startup_folder_common": self._i18n.t("startup.source_startup_folder_common"),
        }

    def _format_startup_source(self, source: str) -> str:
        return self._startup_source_labels().get(source, source)

    def _update_source_filter_dropdown(self, sources=None):
        if not hasattr(self, "_source_filter_dropdown") or not self._source_filter_dropdown.winfo_exists():
            return
        source_labels = self._startup_source_labels()
        source_keys = list(sources or source_labels.keys())
        if self._startup_source_filter not in source_keys and self._startup_source_filter != "all":
            source_keys.append(self._startup_source_filter)
        source_keys = sorted(set(source_keys), key=lambda key: source_labels.get(key, key))
        labels = [self._i18n.t("startup.source_all")] + [source_labels.get(key, key) for key in source_keys]
        self._source_filter_label_to_key = {labels[0]: "all"}
        self._source_filter_label_to_key.update({
            source_labels.get(key, key): key for key in source_keys
        })
        self._source_filter_dropdown.configure(values=labels)
        selected = labels[0] if self._startup_source_filter == "all" else source_labels.get(
            self._startup_source_filter, self._startup_source_filter)
        self._source_filter_dropdown.set(selected if selected in labels else labels[0])

    def _on_source_filter_changed(self, label: str):
        self._startup_source_filter = self._source_filter_label_to_key.get(label, "all")
        self._filter_startup_items()

    def _source_tag(self, source: str) -> str:
        """Short source label for the narrow table column."""
        tag = f"startup.tag_{source}"
        if self._i18n.has(tag):
            return self._i18n.t(tag)
        return self._format_startup_source(source)

    def _sorted_startup_items(self, items: Optional[List[StartupItem]] = None) -> List[StartupItem]:
        source_labels = self._startup_source_labels()
        source_filter = self._startup_source_filter
        sort_key = self._sort_key
        reverse = not self._sort_ascending
        base_items = self._startup_items_cache if items is None else items
        search = self._startup_query.get().strip().casefold()
        filtered = [
            item for item in base_items
            if (source_filter == "all" or item.source == source_filter)
            and (not search or search in item.name.casefold() or search in item.path.casefold())
        ]

        def _sort_value(item: StartupItem) -> str:
            if sort_key == "source":
                return source_labels.get(item.source, item.source)
            return getattr(item, sort_key, "")

        return sorted(
            filtered,
            key=lambda item: self._startup_sort_token(_sort_value(item)),
            reverse=reverse,
        )

    @staticmethod
    @lru_cache(maxsize=2048)
    def _startup_sort_token(text):
        return "".join(lazy_pinyin(text, style=Style.TONE3)).casefold()

    def _toggle_sort(self, key: str):
        if self._sort_key == key:
            self._sort_ascending = not self._sort_ascending
        else:
            self._sort_key = key
            self._sort_ascending = True
        self._update_sort_indicator()
        self._filter_startup_items()

    def _update_sort_indicator(self):
        for key in COL_KEYS:
            title = self._i18n.t(f"startup.col_{key}")
            if key == self._sort_key:
                title += "  ↑" if self._sort_ascending else "  ↓"
            self._startup_table.heading(key, text=title)

    def _request_startup_refresh(self):
        if not self._startup_refresh_running and not self._startup_busy:
            self._refresh_startup_list(force=True)

    def _refresh_startup_list(self, force=False):
        if self._closing:
            return
        if self._startup_refresh_running or self._startup_busy:
            self._startup_refresh_pending |= force
            return
        if not force and self._startup_ready:
            self._filter_startup_items()
            return
        self._startup_refresh_running = True
        self._startup_count_label.configure(text="读取启动项…")
        self._sync_startup_controls()

        def read():
            error = ""
            backups = []
            try:
                backups = list_backups(DATA_DIR)
            except (ValueError, OSError) as exc:
                log.warning("startup backup ledger unavailable: %s", exc)
                error = f"备份台账无法读取：{exc}"
            try:
                items = get_all_items()
            except StartupReadError as exc:
                items = exc.items
                error = " · ".join(filter(None, (error, str(exc))))
            except Exception as exc:
                log.exception("startup item query failed")
                items = None
                error = " · ".join(filter(None, (error, str(exc))))
            self._post_ui(lambda: self._finish_startup_refresh(items, backups, error))

        threading.Thread(target=read, name="StartupQuery", daemon=True).start()

    def _finish_startup_refresh(self, items, backups, error=""):
        self._startup_refresh_running = False
        if self._closing:
            return
        if error:
            self._report(self._i18n.t("startup.read_failed", reason=error), "error", persistent=True)
        if items is not None:
            self._startup_items_cache = list(items)
            self._startup_ready = True
            self._update_source_filter_dropdown(sorted({item.source for item in items}))
        if backups is not None:
            self._startup_backups = list(backups)
        self._filter_startup_items()
        self._sync_startup_controls()
        if self._startup_refresh_pending:
            self._startup_refresh_pending = False
            self._refresh_startup_list(force=True)

    def _render_startup_items(self, items: List[StartupItem]):
        table = self._startup_table
        selected = table.selection()
        position = table.yview()[0]
        rows = table.get_children()
        if rows:
            table.delete(*rows)
        self._startup_rows = {item.identity(): item for item in items}
        for index, (key, item) in enumerate(self._startup_rows.items()):
            table.insert("", "end", iid=key, values=(item.name, item.path, self._source_tag(item.source)),
                         tags=("alternate",) if index % 2 else ())
        if selected and selected[0] in self._startup_rows:
            table.selection_set(selected[0])
            table.focus(selected[0])
        table.yview_moveto(position)
        if self._startup_refresh_running:
            count = "读取启动项…"
        elif not self._startup_ready:
            count = "尚未读取启动项"
        elif not items:
            count = "没有匹配的启动项"
        else:
            count = f"{len(items)} 个条目"
        self._startup_count_label.configure(text=count)
        self._update_selected_startup()

    # ==================================================================
    # Startup CRUD
    # ==================================================================

    def _sync_startup_controls(self):
        working = self._startup_busy or self._startup_refresh_running
        ready = "disabled" if working else "normal"
        self._startup_refresh_btn.configure(
            state=ready, text="读取中…" if self._startup_refresh_running else "刷新")
        self._startup_restore_btn.configure(
            state=ready if self._startup_backups else "disabled",
            text=f"恢复 {len(self._startup_backups)}" if self._startup_backups else "恢复")
        self._startup_add_toggle.configure(state=ready)
        self._startup_add_btn.configure(state=ready)
        self._startup_self_btn.configure(
            state=ready, text="关闭自启" if self._self_item() else "启用自启")
        self._update_selected_startup()

    def _begin_startup_action(self, label):
        if self._closing or self._startup_busy or self._startup_refresh_running:
            return False
        self._startup_busy = True
        self._sync_startup_controls()
        self._show_check_result(label, "info", persistent=True)
        return True

    def _run_startup_step(self, work, done):
        def run():
            try:
                result = work()
            except Exception as exc:
                log.exception("startup action failed")
                self._post_ui(lambda reason=str(exc): self._finish_startup_action(
                    f"启动项操作失败：{reason}", "error"))
            else:
                self._post_ui(lambda: None if self._closing else done(result))

        threading.Thread(target=run, name="StartupAction", daemon=True).start()

    def _finish_startup_action(self, message, kind="ok", *, refresh=False):
        if self._closing:
            return
        self._startup_busy = False
        self._show_check_result(message, kind)
        if refresh or self._startup_refresh_pending:
            self._startup_refresh_pending = False
            self._refresh_startup_list(force=True)
        else:
            self._sync_startup_controls()

    def _browse_startup_path(self):
        path = filedialog.askopenfilename(title="选择要随 Windows 启动的程序")
        if path:
            self._startup_path_entry.delete(0, "end")
            self._startup_path_entry.insert(0, path)

    def _add_startup_item(self):
        name = self._startup_name_entry.get().strip()
        path = self._startup_path_entry.get().strip()
        if not name or not path:
            self._show_check_result(self._i18n.t("startup.fill_both"), "warning")
            return
        self._add_registry_item(name, path, clear_form=True)

    def _add_registry_item(self, name, path, arguments="", *, clear_form=False):
        if not self._begin_startup_action("正在添加启动项…"):
            return

        def complete(status):
            if status in ("created", "overwritten"):
                if clear_form:
                    # The user may have begun typing the next entry.
                    if self._startup_name_entry.get().strip() == name:
                        self._startup_name_entry.delete(0, "end")
                    if self._startup_path_entry.get().strip() == path:
                        self._startup_path_entry.delete(0, "end")
                    if not (self._startup_name_entry.get().strip()
                            or self._startup_path_entry.get().strip()):
                        self._hide_startup_add_form()
                key = "startup.added" if status == "created" else "startup.overwritten"
                self._finish_startup_action(self._i18n.t(key, name=name), refresh=True)
            elif status in ("stale", "exists"):
                self._finish_startup_action("条目已被更改。列表刷新后请重试。", "warning", refresh=True)
            else:
                self._finish_startup_action(self._i18n.t("startup.add_failed"), "error")

        def confirm(existing):
            if existing is None:
                complete("stale")
                return
            preview = f'"{path}" {arguments}' if arguments else path
            if not self._ask_confirm(
                    self._i18n.t("dialog.overwrite_title"),
                    self._i18n.t("dialog.overwrite_body", name=name, old=existing, new=preview)):
                self._finish_startup_action(
                    self._i18n.t("startup.overwrite_cancelled", name=name), "info")
                return
            if not self._closing:
                self._run_startup_step(lambda: add_registry_startup(
                    name, path, arguments=arguments, overwrite=True, expected_command=existing), complete)

        def after_add(status):
            if status == "exists":
                self._run_startup_step(lambda: get_registry_startup_command(name), confirm)
            else:
                complete(status)

        self._run_startup_step(lambda: add_registry_startup(name, path, arguments=arguments), after_add)

    def _self_item(self):
        key = normalize_key("registry", SELF_STARTUP_NAME)
        return next((item for item in self._startup_items_cache if item.identity() == key), None)

    def _toggle_self_startup(self):
        item = self._self_item()
        if item is not None:
            self._remove_startup_item(item)
            return
        try:
            path, arguments = build_self_startup_parts()
        except ValueError:
            self._show_check_result(self._i18n.t("startup.add_failed"), "error")
        else:
            self._add_registry_item(SELF_STARTUP_NAME, path, arguments)

    def _open_startup_location(self, item: StartupItem):
        if not self._begin_startup_action("正在打开位置…"):
            return

        def done(ok):
            key = "startup.opened_location" if ok else "startup.open_location_failed"
            self._finish_startup_action(self._i18n.t(key, name=item.name), "ok" if ok else "error")

        self._run_startup_step(lambda: open_startup_location(item), done)

    def _remove_startup_item(self, item: StartupItem):
        if not is_known_source(item.source):
            self._show_check_result(self._i18n.t("startup.unknown_source", source=item.source), "error")
            return
        if not self._begin_startup_action("等待确认删除…"):
            return
        if not self._ask_confirm(
                self._i18n.t("dialog.remove_title"),
                self._i18n.t("dialog.remove_body", name=item.name,
                             source=self._format_startup_source(item.source), target=item.path),
                danger=True):
            self._finish_startup_action("已取消删除", "info")
            return
        if self._closing:
            return
        self._show_check_result("正在备份并删除启动项…", "info", persistent=True)

        def done(status):
            if status == "removed":
                self._finish_startup_action(
                    self._i18n.t("startup.removed", name=item.name) + " "
                    + self._i18n.t("startup.backup_ready"), refresh=True)
            elif status == "backup_failed":
                self._finish_startup_action("备份失败，已取消删除。", "error")
            elif status == "stale":
                self._finish_startup_action("条目已被更改。列表刷新后请重试。", "warning", refresh=True)
            else:
                self._finish_startup_action(
                    self._i18n.t("startup.remove_failed", name=item.name), "error", refresh=True)

        self._run_startup_step(lambda: remove_with_backup(item, DATA_DIR), done)

    def _restore_last_removed(self):
        if not self._begin_startup_action("正在读取启动项备份…"):
            return

        def confirm(pending):
            if not pending:
                self._startup_backups = []
                self._finish_startup_action(self._i18n.t("startup.nothing_to_restore"), "info")
                return
            entry = pending[0]
            label = entry.get("name", "")
            if not self._ask_confirm(
                    self._i18n.t("dialog.restore_title"),
                    self._i18n.t("dialog.restore_body", name=label, target=entry.get("path", ""))):
                self._finish_startup_action("已取消恢复", "info")
                return
            if self._closing:
                return
            self._show_check_result("正在恢复启动项…", "info", persistent=True)

            def done(ok):
                key = "startup.restored" if ok else "startup.restore_failed"
                self._finish_startup_action(
                    self._i18n.t(key, name=label), "ok" if ok else "error", refresh=True)

            self._run_startup_step(lambda: restore_backup(entry, DATA_DIR), done)

        self._run_startup_step(lambda: list_backups(DATA_DIR), confirm)

    # ==================================================================
    # Dialogs
    # ==================================================================

    def _modal(self, title: str, width: int = 460, height: int = 250):
        """Create a themed modal Toplevel centred on the main window."""
        s = self._style
        window = ctk.CTkToplevel(self)
        window.title(title)
        window.configure(fg_color=s.canvas)
        window.resizable(False, False)
        window.transient(self)
        window.protocol("WM_DELETE_WINDOW", lambda: self._close_modal(window, False))
        self.update_idletasks()
        x = self.winfo_rootx() + max(0, (self.winfo_width() - width) // 2)
        y = self.winfo_rooty() + max(0, (self.winfo_height() - height) // 3)
        window.geometry(f"{width}x{height}+{x}+{y}")
        return window

    def _close_modal(self, window, result):
        window._result = result
        try:
            window.grab_release()
        except Exception:
            pass
        try:
            window.destroy()
        except Exception:
            pass

    def _ask_confirm(self, title: str, body: str, danger: bool = False) -> bool:
        """Modal confirmation. Returns True only on explicit confirmation."""
        s = self._style
        window = self._modal(title, width=470, height=260)
        window._result = False

        text = ctk.CTkTextbox(window, wrap="word", font=ctk.CTkFont(size=12),
                              fg_color=s.surface, text_color=s.text_primary,
                              border_width=0)
        text.pack(fill="both", expand=True, padx=14, pady=(14, 8))
        text.insert("1.0", body)
        text.configure(state="disabled")

        buttons = ctk.CTkFrame(window, fg_color="transparent", corner_radius=0)
        buttons.pack(fill="x", padx=14, pady=(0, 12))

        confirm_btn = ctk.CTkButton(
            buttons, text=self._i18n.t("general.ok"), width=90, height=28,
            font=ctk.CTkFont(size=12),
            command=lambda: self._close_modal(window, True))
        if danger:
            apply_btn_style(confirm_btn, s)
        else:
            apply_btn_secondary(confirm_btn, s)
        confirm_btn.pack(side="right", padx=(6, 0))

        cancel_btn = ctk.CTkButton(
            buttons, text=self._i18n.t("general.cancel"), width=90, height=28,
            font=ctk.CTkFont(size=12),
            command=lambda: self._close_modal(window, False))
        apply_btn_secondary(cancel_btn, s)
        cancel_btn.pack(side="right")

        window.bind("<Escape>", lambda e: self._close_modal(window, False))
        window.bind("<Return>", lambda e: self._close_modal(window, True))
        window.grab_set()
        cancel_btn.focus_set()
        self.wait_window(window)
        self._flush_pending_wake()
        return bool(getattr(window, "_result", False))

    def _show_first_run_setup(self):
        """First run: show the current plan, the target policy and let the user choose.

        Uses the snapshot the UI already has; if there is none yet, the dialog is
        filled in asynchronously so a slow ``powercfg`` never freezes the window.
        """
        if self._closing or self.cfg["setup_completed"] or not self.winfo_exists():
            return
        s = self._style
        window = self._modal(self._i18n.t("setup.title"), width=520, height=380)
        window._result = None

        header = ctk.CTkLabel(window, text=self._i18n.t("setup.title"),
                              font=ctk.CTkFont(size=15, weight="bold"),
                              text_color=s.text_primary, anchor="w")
        header.pack(fill="x", padx=16, pady=(14, 2))

        body = ctk.CTkTextbox(window, wrap="word", font=ctk.CTkFont(size=12), height=120,
                              fg_color=s.surface, text_color=s.text_primary, border_width=0)
        body.pack(fill="x", padx=16, pady=(4, 8))

        state = {"plans": list(self._power_plans_cache), "error": ""}

        def _fill_body():
            active_name = self._i18n.t("power.unable_detect")
            for plan in state["plans"]:
                if plan.is_active:
                    active_name = plan.name
                    break
            lines = [self._i18n.t("setup.current", name=active_name),
                     self._i18n.t("setup.explain")]
            if state["error"]:
                lines.append(self._i18n.t("setup.read_error", reason=state["error"]))
            body.configure(state="normal")
            body.delete("1.0", "end")
            body.insert("1.0", "\n\n".join(lines))
            body.configure(state="disabled")

        def _apply_snapshot(snapshot):
            state["plans"] = list(snapshot.plans)
            state["error"] = self._power_error_text(snapshot) if snapshot.error else ""
            try:
                _fill_body()
            except Exception:
                log.debug("setup dialog closed before its plan read finished")

        if state["plans"]:
            _fill_body()
        else:
            body.insert("1.0", self._i18n.t("power.status_checking"))
            body.configure(state="disabled")

            def _query():
                try:
                    snapshot = get_power_snapshot()
                except Exception:
                    log.exception("first-run plan query failed")
                    return
                self._post_ui(lambda: _apply_snapshot(snapshot))

            threading.Thread(target=_query, name="SetupPlanQuery", daemon=True).start()

        enable_var = ctk.BooleanVar(value=False)

        def _finish(enable: bool, guid: str):
            self.cfg["monitor_enabled"] = enable
            self.cfg["target_guid"] = guid
            self.power_monitor.target_guid = guid or None
            self.cfg["setup_completed"] = True
            if enable:
                self._start_monitor(save=False)
            else:
                self.power_monitor.stop()
                self._monitor_running = False
            self._sync_monitor_switch()
            self._persist_config()
            self._refresh_power_status()

        choices = ctk.CTkFrame(window, fg_color="transparent", corner_radius=0)
        choices.pack(fill="x", padx=16, pady=(0, 4))

        enable_switch = ctk.CTkSwitch(
            choices, text=self._i18n.t("setup.enable_monitor"),
            variable=enable_var, font=ctk.CTkFont(size=12))
        apply_switch(enable_switch, s)
        enable_switch.pack(side="left")

        def _choose_auto():
            self._close_modal(window, "auto")

        def _choose_plan():
            if not state["plans"]:
                self._report(self._i18n.t("setup.no_performance_plan"), "warning")
                return
            self._close_modal(window, "pick")

        buttons = ctk.CTkFrame(window, fg_color="transparent", corner_radius=0)
        buttons.pack(fill="x", padx=16, pady=(4, 14))

        auto_btn = ctk.CTkButton(buttons, text=self._i18n.t("setup.use_auto"), width=110, height=28,
                                 font=ctk.CTkFont(size=12), command=_choose_auto)
        apply_btn_secondary(auto_btn, s)
        auto_btn.pack(side="left")

        pick_btn = ctk.CTkButton(buttons, text=self._i18n.t("setup.pick_plan"), width=110, height=28,
                                 font=ctk.CTkFont(size=12), command=_choose_plan)
        apply_btn_secondary(pick_btn, s)
        pick_btn.pack(side="left", padx=6)

        skip_btn = ctk.CTkButton(buttons, text=self._i18n.t("setup.skip"), width=110, height=28,
                                 font=ctk.CTkFont(size=12),
                                 command=lambda: self._close_modal(window, "skip"))
        apply_btn_secondary(skip_btn, s)
        skip_btn.pack(side="right")

        window.bind("<Escape>", lambda e: self._close_modal(window, "skip"))
        window.grab_set()
        self.wait_window(window)
        self._flush_pending_wake()

        result = getattr(window, "_result", None)
        if result == "auto":
            _finish(bool(enable_var.get()), "")
            self._show_check_result(self._i18n.t("setup.done"), "ok")
        elif result == "pick":
            self._pick_target_from_setup(enable_var.get())
        else:
            self.cfg["monitor_enabled"] = False
            self.cfg["setup_completed"] = True
            self._persist_config()

    def _pick_target_from_setup(self, enable: bool):
        """Second step of first-run setup: choose one explicit plan by GUID."""
        plans = list(self._power_plans_cache)
        if not plans:
            self._show_check_result(self._i18n.t("power.status_checking"), "info", persistent=True)
            return
        labels = self._build_target_labels(plans)
        window = self._modal(self._i18n.t("setup.pick_title"), width=420, height=180)
        window._result = None

        label = ctk.CTkLabel(window, text=self._i18n.t("setup.pick_title"),
                             font=ctk.CTkFont(size=14, weight="bold"),
                             text_color=self._style.text_primary)
        label.pack(padx=16, pady=(16, 6))

        dropdown = ctk.CTkOptionMenu(window, values=labels, font=ctk.CTkFont(size=12))
        apply_dropdown(dropdown, self._style)
        dropdown.set(labels[0])
        dropdown.pack(padx=16, pady=(0, 10))

        def _confirm():
            self._close_modal(window, dropdown.get())

        buttons = ctk.CTkFrame(window, fg_color="transparent", corner_radius=0)
        buttons.pack(pady=(0, 12))
        ok_btn = ctk.CTkButton(buttons, text=self._i18n.t("general.ok"), width=90, height=28,
                               command=_confirm)
        apply_btn_style(ok_btn, self._style)
        ok_btn.pack(side="right", padx=(6, 0))
        cancel_btn = ctk.CTkButton(buttons, text=self._i18n.t("general.cancel"), width=90, height=28,
                                   command=lambda: self._close_modal(window, None))
        apply_btn_secondary(cancel_btn, self._style)
        cancel_btn.pack(side="right")

        window.grab_set()
        self.wait_window(window)
        self._flush_pending_wake()

        choice = getattr(window, "_result", None)
        if not choice:
            self.cfg["monitor_enabled"] = False
            self.cfg["setup_completed"] = True
            self._persist_config()
            return
        guid = self._target_guid_map.get(choice) or ""
        self.cfg["monitor_enabled"] = bool(enable)
        self.cfg["target_guid"] = guid
        self.cfg["setup_completed"] = True
        self.power_monitor.target_guid = guid or None
        if enable:
            self._start_monitor(save=False)
        self._sync_monitor_switch()
        self._persist_config()
        self._refresh_power_status()
        if guid:
            self._show_check_result(self._i18n.t("setup.done"), "ok")

    # ==================================================================
    # Explicit power scheme creation
    # ==================================================================

    def _create_missing_schemes(self):
        if self._closing or self._creating_schemes or self._check_running or self._switch_running:
            return
        self._creating_schemes = True
        self._sync_power_actions()
        self._show_check_result(self._i18n.t("power.create_running"), "info", persistent=True)

        def _work():
            results = []
            error = ""
            try:
                results = create_missing_builtin_schemes(self._power_plans_cache or None)
            except Exception as exc:
                log.exception("power scheme creation failed")
                error = str(exc)
            if self._closing:
                return
            self._post_ui(lambda: self._finish_create_schemes(results, error))

        threading.Thread(target=_work, name="CreateSchemes", daemon=True).start()

    def _finish_create_schemes(self, results, error: str = ""):
        self._creating_schemes = False
        if not self._closing:
            self._sync_power_actions()
        if self._closing or not self.winfo_exists():
            return
        if error:
            self._show_check_result(self._i18n.t("power.create_failed", reason=error),
                                    "error", persistent=True)
            self._refresh_power_status()
            return

        created, failed, existing = [], [], []
        for result in results:
            if result.error == "already_present":
                existing.append(result.name)
            elif result.ok:
                created.append(result.name + (f" ({result.guid[:8].upper()})" if result.guid else ""))
            else:
                failed.append(f"{result.name}: {result.error or 'error'}")

        lines = []
        if created:
            lines.append(self._i18n.t("power.create_created", names=", ".join(created)))
        if existing:
            lines.append(self._i18n.t("power.create_existing", names=", ".join(existing)))
        if failed:
            lines.append(self._i18n.t("power.create_failed_items", names="; ".join(failed)))
        message = " | ".join(lines) if lines else self._i18n.t("power.create_nothing")
        self._show_check_result(message, "error" if failed else "ok", persistent=bool(failed))
        self._refresh_power_status()

    # ==================================================================
    # Power plan actions
    # ==================================================================

    def _refresh_power_status(self):
        """Kick off one background power plan query. Never touches the system."""
        if self._closing:
            return
        if self._power_refresh_running:
            self._power_refresh_pending = True
            return
        self._power_refresh_running = True
        self._sync_power_actions()

        def _check():
            try:
                snapshot = get_power_snapshot()
            except Exception:
                log.exception("power plan query failed")
                snapshot = None
            try:
                self._post_ui(lambda: self._finish_power_refresh(snapshot))
            except Exception:
                # Window already torn down — drop the result silently.
                self._power_refresh_running = False
        threading.Thread(target=_check, name="PowerQuery", daemon=True).start()

    def _finish_power_refresh(self, snapshot):
        self._power_refresh_running = False
        if self._closing or not self.winfo_exists():
            return
        plans = list(snapshot.plans) if snapshot is not None else []
        active = snapshot.active if snapshot is not None else None
        self._power_plans_cache = plans
        self._sync_power_actions()
        self._update_power_ui(active, plans, snapshot)
        self._refresh_target_dropdown_items(plans)
        if self._power_refresh_pending:
            self._power_refresh_pending = False
            self._refresh_power_status()

    def _refresh_target_dropdown_items(self, plans=None):
        if plans is None:
            plans = self._power_plans_cache
        values = self._build_target_labels(plans)
        target_guid = self.power_monitor.target_guid or ""
        if target_guid and target_guid not in self._label_by_guid:
            # Keep the dead target visible so the user must re-choose.
            values.append(self._missing_target_label(target_guid))
        self._target_dropdown.configure(values=values)
        self._set_target_selection()

    def _update_power_ui(self, active, plans=None, snapshot=None):
        s = self._style
        if snapshot is None and active is None:
            self._report("读取电源方案失败，请重试。", "error", persistent=True)
        if snapshot is not None and snapshot.error:
            self._set_status_badge(
                self._i18n.t("power.read_failed", reason=self._power_error_text(snapshot)),
                s.error)
            self._set_active_plan_name(self._i18n.t("power.unable_detect"))
            self._report(self._i18n.t("power.read_failed",
                                      reason=self._power_error_text(snapshot)), "error",
                         persistent=True)
            self._rebuild_tray_menu(plans or [])
            return

        if active is None:
            self._set_active_plan_name(self._i18n.t("power.unable_detect"))
            self._set_status_badge(self._i18n.t("power.status_error"), s.error)
            return
        self._set_active_plan_name(active.name)

        target_guid = self.power_monitor.target_guid
        missing_target = bool(target_guid) and find_plan_by_guid(target_guid, plans or []) is None
        ok = is_plan_matching_target(active, target_guid, plans or [])

        if missing_target:
            self._set_status_badge(self._i18n.t("power.status_target_missing").format(
                guid=target_guid[:8].upper()), s.error)
            self._report(self._i18n.t("power.target_missing", guid=target_guid[:8].upper()),
                         "error", persistent=True)
        elif ok:
            self._set_status_badge("与监控目标一致", s.success)
        else:
            self._set_status_badge("与监控目标不同", s.text_secondary)
        self._rebuild_tray_menu(plans)

    def _set_status_badge(self, text: str, color: str):
        badge = self._power_status_badge
        scale = ctk.ScalingTracker.get_widget_scaling(badge)
        badge.configure(text=_ellipsize(badge, text, round(180 * scale)), text_color=color)
        self._fit_active_plan_name()

    def _set_active_plan_name(self, text):
        self._active_plan_name = text
        self._fit_active_plan_name()

    def _fit_active_plan_name(self):
        label = self._power_active_value
        scale = ctk.ScalingTracker.get_widget_scaling(label)
        width = (label.master.winfo_width() - self._active_plan_caption.winfo_reqwidth()
                 - self._power_status_badge.winfo_reqwidth() - round(20 * scale))
        label.configure(text=_ellipsize(label, self._active_plan_name, max(24, width)))

    def _power_error_text(self, snapshot) -> str:
        key = f"power.error_{snapshot.error}"
        return self._i18n.t(key) if self._i18n.has(key) else self._i18n.t("power.error_other")

    def _sync_power_actions(self):
        modifying = self._switch_running or self._check_running or self._creating_schemes
        state = "disabled" if modifying else "normal"
        self._power_switch_btn.configure(state=state,
                                         text="切换中…" if self._switch_running else "切换方案")
        self._check_btn.configure(state=state, text="纠正中…" if self._check_running else "立即纠正")
        self._create_schemes_btn.configure(state=state)
        self._power_refresh_btn.configure(
            state="disabled" if self._power_refresh_running else "normal",
            text="读取中…" if self._power_refresh_running else "刷新状态")

    def _do_switch_plan(self, guid: str):
        if self._closing or self._switch_running or self._check_running or self._creating_schemes:
            return
        self._switch_running = True
        self._sync_power_actions()
        plan = find_plan_by_guid(guid, self._power_plans_cache)
        name = plan.name if plan else guid[:8].upper()
        self._show_check_result(f"正在切换至「{name}」…", "info", persistent=True)

        def work():
            try:
                result = self.power_monitor.switch_now(guid)
            except Exception:
                log.exception("power plan switch failed")
                result = None
            self._post_ui(lambda: done(result))

        def done(result):
            if self._closing:
                return
            self._switch_running = False
            self._sync_power_actions()
            if result and result.ok:
                self._show_check_result(f"已切换至「{name}」", "ok")
            elif result and result.error == "busy":
                self._show_check_result("电源方案正在处理中，请稍候重试。", "warning")
            else:
                self._show_check_result(self._i18n.t("power.switch_failed", name=name),
                                        "error", persistent=True)
            self._refresh_power_status()

        threading.Thread(target=work, name="PowerSwitch", daemon=True).start()

    def _check_now(self):
        if self._closing or self._check_running or self._switch_running or self._creating_schemes:
            return
        self._check_running = True
        self._sync_power_actions()
        self._show_check_result("正在检查并纠正电源方案…", "info", persistent=True)
        revision = self.power_monitor.revision

        def work():
            try:
                result = self.power_monitor.check_now()
            except Exception as exc:
                log.exception("manual power check failed")
                result = {"status": "error", "error_detail": str(exc)}
            self._post_ui(lambda: done(result))

        def done(result):
            if self._closing:
                return
            self._check_running = False
            self._sync_power_actions()
            if revision == self.power_monitor.revision:
                self._recover_unusable_target(result)
                self._show_result_persistent(result)
            else:
                self._show_check_result("监控设置已更改，已刷新当前方案。", "info")
            self._refresh_power_status()

        threading.Thread(target=work, name="PowerCheckNow", daemon=True).start()

    def _recover_unusable_target(self, result: dict) -> None:
        """Tell the user their saved target is dead and refresh the target list.

        The target itself is not changed silently: the dropdown drops the dead
        plan and the next selection works. The check itself now falls back to the
        best usable plan instead of failing.
        """
        if result.get("status") not in ("target_blocked", "fix_failed", "target_missing"):
            return
        self._persist_config()          # store the newly blocked guid
        plan = find_plan_by_guid(result.get("target_guid") or "", self._power_plans_cache)
        name = result.get("target_name") or (plan.name if plan else "")
        guid = (result.get("target_guid") or "")[:8].upper()
        if not name:
            return
        self._report(self._i18n.t("power.target_unusable", name=name, guid=guid),
                     "error", persistent=True)

    def _tray_notify(self, title: str, message: str):
        """Show a balloon notification via the tray icon — non-intrusive, auto-dismiss."""
        if self._tray_icon is not None:
            try:
                self._tray_icon.notify(message, title)
            except Exception:
                pass

    def _show_result_persistent(self, result: dict, notify: bool = True):
        status = result.get("status", "")
        ts = time.strftime("%H:%M:%S")
        title = self._i18n.t("power.title")
        if status == "ok":
            msg = f"[{ts}] {self._i18n.t('power.already_ok', name=result.get('plan_name', ''))}"
            self._show_check_result(msg, "ok", persistent=True)
            if notify:
                self._tray_notify(title, result.get('plan_name', '') + " — " + self._i18n.t("power.status_ok"))
        elif status == "fixed":
            msg = f"[{ts}] {self._i18n.t('power.switched', from_=result.get('plan_name', ''), to=result.get('target_name', ''))}"
            skipped = [entry for entry in (result.get("skipped") or [])]
            if skipped:
                # Explain why a higher-priority plan was not used.
                msg += " " + self._i18n.t("power.skipped_plans", names=", ".join(skipped))
            self._show_check_result(msg, "ok", persistent=True)
            if notify:
                self._tray_notify(title, result.get('plan_name', '') + " → " + result.get('target_name', ''))
        elif status == "busy":
            # A scheduled check was already running; the result will arrive.
            self._show_check_result(self._i18n.t("power.check_busy"), "info", persistent=True)
        elif status == "fix_failed":
            msg = f"[{ts}] {self._i18n.t('power.switch_failed', name=result.get('plan_name', ''))}"
            skipped = result.get("skipped") or []
            if skipped:
                msg += " " + self._i18n.t("power.skipped_plans",
                                          names=", ".join(str(entry) for entry in skipped))
            elif result.get("error") == "not_supported":
                msg += " " + self._i18n.t("power.error_not_supported")
            self._show_check_result(msg, "error", persistent=True)
            if notify:
                self._tray_notify(title, self._i18n.t('power.switch_failed', name=result.get('plan_name', '')))
        elif status == "no_target":
            msg = f"[{ts}] {self._i18n.t('power.no_target')}"
            self._show_check_result(msg, "error", persistent=True)
            if notify:
                self._tray_notify(title, self._i18n.t('power.no_target'))
        elif status == "target_missing":
            guid = (result.get("target_guid") or "")[:8].upper()
            msg = f"[{ts}] {self._i18n.t('power.target_missing', guid=guid)}"
            self._show_check_result(msg, "error", persistent=True)
            if notify:
                self._tray_notify(title, self._i18n.t('power.target_missing', guid=guid))
        elif status == "target_blocked":
            guid = (result.get("target_guid") or "")[:8].upper()
            name = result.get("target_name", "")
            msg = f"[{ts}] {self._i18n.t('power.target_unusable', name=name, guid=guid)}"
            self._show_check_result(msg, "error", persistent=True)
            if notify:
                self._tray_notify(title, self._i18n.t('power.target_unusable',
                                                      name=name, guid=guid))
        elif status == "cancelled":
            self._show_check_result("监控设置已更改，本次检查结束。", "info")
        elif status == "error":
            reason = result.get("error_detail") or result.get("message") or ""
            msg = f"[{ts}] {self._i18n.t('power.read_failed', reason=reason or 'error')}"
            self._show_check_result(msg, "error", persistent=True)
            if notify:
                self._tray_notify(title, msg)
        else:
            msg = f"[{ts}] {status}: {result.get('plan_name', '')}"
            self._show_check_result(msg, "error", persistent=True)
            if notify:
                self._tray_notify(title, msg)

    def _on_power_status_change(self, result: dict):
        """Called from the monitor worker thread — hands the result to the UI queue."""
        if self._closing:
            return
        revision = self.power_monitor.revision
        self._post_ui(lambda: self._handle_power_status(result)
                      if revision == self.power_monitor.revision else None)

    def _handle_power_status(self, result: dict):
        if self._closing or not self.winfo_exists():
            return
        if self._window_visible:
            self._refresh_power_status()
            self._show_result_persistent(result, notify=False)

    def _on_monitor_state_change(self, running: bool):
        """Called from the monitor worker thread — keeps the UI honest."""
        if self._closing:
            return
        self._post_ui(lambda: self._apply_monitor_state(running)
                      if running == self.power_monitor.running else None)

    def _apply_monitor_state(self, running: bool):
        if self._closing or not self.winfo_exists():
            return
        was_running = self._monitor_running
        self._monitor_running = bool(running)
        self._sync_monitor_switch()
        if was_running and not running and self.cfg.get("monitor_enabled"):
            # The worker died on its own while monitoring was still wanted.
            self._report(self._i18n.t("power.monitor_stopped_unexpectedly"), "warning")

    def _sync_monitor_switch(self):
        switch = getattr(self, "_monitor_switch", None)
        if switch is None or not switch.winfo_exists():
            return
        self._syncing_monitor_switch = True
        try:
            if self._monitor_running:
                switch.select()
            else:
                switch.deselect()
        finally:
            self._syncing_monitor_switch = False

    def _show_check_result(self, msg: str, kind: str = "info", persistent: bool = False):
        self._status_message = msg
        self._status_kind = kind
        self._fit_status_text()
        if self._status_clear_job is not None:
            self.after_cancel(self._status_clear_job)
            self._status_clear_job = None
        if not persistent:
            self._status_clear_job = self.after(8000, self._clear_status_bar)

    def _fit_status_text(self):
        label = self._check_result_label
        width = label.master.winfo_width()
        text = _ellipsize(label, self._status_message, width)
        if text != self._status_message:
            self._status_details_btn.pack(side="right", padx=(5, 0))
            text = _ellipsize(label, self._status_message,
                              width - self._status_details_btn.winfo_reqwidth() - 10)
        else:
            self._status_details_btn.pack_forget()
        s = self._style
        color = {"ok": s.success, "error": s.error, "warning": s.warning}.get(
            self._status_kind, s.text_secondary)
        label.configure(text=text, text_color=color)

    def _show_status_details(self):
        window = self._modal("操作详情", width=430, height=180)
        body = ctk.CTkTextbox(window, wrap="word", font=ctk.CTkFont(
            family=_ui_font_family(), size=13))
        body.insert("1.0", self._status_message)
        body.configure(state="disabled")

        def close():
            self._close_modal(window, None)

        close_btn = ctk.CTkButton(window, text="关闭", command=close, width=66, height=28)
        apply_btn_secondary(close_btn, self._style)
        close_btn.pack(side="bottom", anchor="e", padx=9, pady=(0, 9))
        body.pack(fill="both", expand=True, padx=9, pady=(9, 5))
        window.bind("<Escape>", lambda _: close())
        window.grab_set()
        self.wait_window(window)
        self._flush_pending_wake()

    def _clear_status_bar(self):
        self._status_clear_job = None
        if not self._closing:
            self._show_check_result("", "info", persistent=True)

    def _apply_interval(self):
        entry = self._interval_entry
        try:
            secs = int(entry.get().strip())
            secs = max(10, min(3600, secs))
            entry.delete(0, "end")
            entry.insert(0, str(secs))
            self.power_monitor.interval = secs
            self.cfg["check_interval"] = secs
            self._persist_config()
            self._show_check_result(
                f"[{time.strftime('%H:%M:%S')}] {self._i18n.t('startup.interval_set', secs=secs)}", "ok")
        except ValueError:
            self._show_check_result(self._i18n.t("startup.interval_invalid"), "error")

    # ==================================================================
    # Monitor toggle
    # ==================================================================

    def _toggle_monitor(self):
        if getattr(self, '_syncing_monitor_switch', False):
            return
        if not self.cfg["setup_completed"]:
            self._sync_monitor_switch()
            self._show_check_result("请先完成首次设置。", "info")
            return
        if self._monitor_switch.get():
            self._start_monitor()
        else:
            self._stop_monitor()
        self._sync_monitor_switch()

    def _toggle_monitor_switch_for_test(self, enabled: bool):
        """Test seam: set the switch then run the real toggle handler."""
        if enabled:
            self._monitor_switch.select()
        else:
            self._monitor_switch.deselect()
        self._toggle_monitor()

    def _start_monitor(self, save: bool = True):
        """Start monitoring and record the user's preference."""
        if not self.cfg["setup_completed"]:
            return
        if not self.power_monitor.running:
            try:
                self.power_monitor.start()
            except Exception:
                log.exception("could not start power monitor")
                self._report(self._i18n.t("power.monitor_start_failed"), "error", persistent=True)
                return
            self._monitor_running = self.power_monitor.running
            if save and self._monitor_running:
                self._report(self._i18n.t("power.monitor_started"), "ok")
        if save:
            self.cfg["monitor_enabled"] = self._monitor_running
            self._persist_config()

    def _stop_monitor(self, save: bool = True):
        """Stop monitoring. ``save=False`` (app exit) keeps the user preference."""
        self.power_monitor.stop()
        self._monitor_running = False
        if save:
            self.cfg["monitor_enabled"] = False
            self._persist_config()
            self._report(self._i18n.t("power.monitor_stopped"), "ok")

    # ==================================================================
    # System tray
    # ==================================================================

    def _setup_tray(self) -> bool:
        if self._tray_thread is not None and self._tray_thread.is_alive():
            return self._tray_icon is not None and self._tray_icon.registered
        self._tray_icon = None
        ready = threading.Event()
        decided = threading.Event()
        accepted = False
        try:
            from pystray._win32 import Icon, win32

            class TrayIcon(Icon):
                # pystray 0.19.5 discards the native result and uses uID=0.
                registered = False
                _icon_handle = None  # The backend destructor also runs after constructor failure.
                _menu_handle = None  # Message-loop cleanup can run before the menu is built.

                def _message(self, code, flags, **data):
                    ok = win32.Shell_NotifyIcon(code, win32.NOTIFYICONDATAW(
                        cbSize=ctypes.sizeof(win32.NOTIFYICONDATAW),
                        hWnd=self._hwnd, uID=0, uFlags=flags, **data))
                    if code == win32.NIM_ADD:
                        self.registered = bool(ok)
                        if not ok:
                            log.warning("tray registration rejected by Windows (Shell_NotifyIconW NIM_ADD)")

                def _mark_ready(self):
                    try:
                        if not decided.is_set():
                            self.update_menu()
                            self.visible = True
                    except Exception:
                        log.exception("tray registration failed")
                    finally:
                        ready.set()
                    decided.wait()
                    if not accepted:
                        win32.PostQuitMessage(0)

                def stop(self):
                    self._stop()

            image = self._tray_icon_image()
            menu = self._tray_menu()
            icon = None

            def run_tray():
                nonlocal icon
                try:
                    icon = TrayIcon("pc_auto_scripts_rem", image, "PC 系统自动脚本", menu=menu)
                    # Register on the Windows event loop thread. Icon.run starts
                    # a second setup thread that can be stranded before readiness.
                    icon._run()
                except Exception:
                    log.exception("tray backend failed")
                    if icon is not None and not ready.is_set():
                        # The backend's message loop has not taken ownership yet.
                        for hwnd in (icon._hwnd, icon._menu_hwnd):
                            if hwnd:
                                icon._HWND_TO_ICON.pop(hwnd, None)
                                win32.DestroyWindow(hwnd)
                        icon._unregister_class(icon._atom)
                finally:
                    ready.set()
                    if icon is not None:
                        icon._release_icon()

            self._tray_thread = threading.Thread(target=run_tray, daemon=True)
            self._tray_thread.start()
            if not ready.wait(timeout=1.0):
                log.warning("timed out waiting for Windows tray registration")
            elif icon is not None and icon.registered:
                self._tray_icon = icon
                accepted = True
            return accepted
        except Exception:
            log.exception("tray setup failed")
            return False
        finally:
            decided.set()

    def _tray_switch_plan(self, guid: str):
        """Tray plan handlers carry the GUID, so same-named plans stay distinct."""
        def _handle(icon, item):
            try:
                self._post_ui(lambda: self._do_switch_plan(guid))
            except Exception:
                log.debug("dropping tray action after window close")
        return _handle

    def _rebuild_tray_menu(self, plans=None):
        """Rebuild the tray menu to reflect current power plan state."""
        if self._tray_icon is not None:
            try:
                self._tray_icon.menu = self._tray_menu(plans)
            except Exception:
                log.exception("tray menu rebuild failed")

    def _tray_menu(self, plans=None):
        import pystray

        def on_show(icon, item):
            self._post_ui(self.deiconify)

        def on_exit(icon, item):
            self._post_ui(self._force_quit)

        if plans is None:
            plans = self._power_plans_cache
        plan_items = []
        name_counts: Dict[str, int] = {}
        for plan in plans:
            key = plan.name.strip().lower()
            name_counts[key] = name_counts.get(key, 0) + 1
        for plan in plans:
            label = plan.name
            if name_counts.get(plan.name.strip().lower(), 0) > 1:
                label = f"{label} ({plan.guid[:8].upper()})"
            label += "  ✓" if plan.is_active else ""
            plan_items.append(
                pystray.MenuItem(label, self._tray_switch_plan(plan.guid)))
        if not plan_items:
            plan_items.append(
                pystray.MenuItem(self._i18n.t("power.unable_detect"),
                                 lambda: None, enabled=False))

        return pystray.Menu(
            pystray.MenuItem(self._i18n.t("tray.show"), on_show, default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(self._i18n.t("power.title"),
                             pystray.Menu(*plan_items)),
            pystray.MenuItem(self._i18n.t("tray.check"),
                             lambda: self._post_ui(self._check_now)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(self._i18n.t("tray.exit"), on_exit),
        )

    def _tray_icon_image(self):
        """Tray icon: Rem's face when the art theme is on, otherwise the plain bolt."""
        if not self._art_active():
            return _create_tray_icon(self._style.accent)
        art = rem_theme.rem_avatar(256, self._theme_palette(), self._style.char_ring)
        return _create_tray_icon(self._style.accent, art)

    def _update_tray_icon_color(self):
        if self._tray_icon is not None:
            try:
                self._tray_icon.icon = self._tray_icon_image()
            except Exception:
                log.debug("tray icon color update failed")

    # ==================================================================
    # Window lifecycle
    # ==================================================================

    def _on_close(self):
        if not self.cfg.get("minimize_to_tray", True):
            self._force_quit()
        elif self._setup_tray():
            self._window_visible = False
            self.withdraw()
        else:
            self.iconify()

    def _force_quit(self):
        self._running = False
        self._closing = True
        _stop_ipc_server()
        # Stop the worker for this run only — never rewrite the user preference.
        self._stop_monitor(save=False)
        if self._tray_icon is not None:
            try:
                self._tray_icon.stop()
            except Exception:
                pass
        try:
            self.destroy()
        except Exception:
            pass
        _release_instance_mutex()
        # Belt-and-suspenders: ensure the process actually exits
        sys.exit(0)

    def _restore_from_ipc(self):
        """Called when another instance tries to launch — restore the window.

        Runs on the Tk main thread. While a modal dialog is open this is queued
        by Tk, so the request is also recorded and replayed once the modal closes.
        """
        self._ipc_restore_pending = True
        self._window_visible = True
        self._show_window()

    def _flush_pending_wake(self) -> None:
        """Replay a wake-up that arrived while a modal dialog held the event queue."""
        if not self._ipc_restore_pending:
            return
        self._ipc_restore_pending = False
        self._window_visible = True
        self._show_window()

    def _show_window(self) -> None:
        if self._closing or not self.winfo_exists():
            return
        try:
            self.deiconify()
        except Exception:
            log.debug("could not raise the window")

    def deiconify(self):
        self._window_visible = True
        try:
            super().deiconify()
            self.lift()
            self.focus_force()
        except Exception:
            log.debug("deiconify failed")
        if self._ui_drain_id is not None and not self._power_refresh_running:
            self._refresh_power_status()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    if "--check-runtime" in sys.argv:
        # Validate the packaged runtime without creating a window or touching system settings.
        from tkinter import Tcl
        Tcl().eval("info patchlevel")
        rem_theme.draw_rem(32).load()
        return
    mode = _enable_dpi_awareness()
    setup_logging()
    log.info("starting: DPI awareness=%s", mode)
    ctk.set_appearance_mode("dark")
    if not _acquire_instance_mutex():
        _wake_existing_instance()
        sys.exit(0)

    ipc_ready = False
    try:
        app = App()
        ipc_ready = _start_ipc_server(app)
        if not ipc_ready:
            log.warning("IPC wake-up channel unavailable; single instance still enforced by mutex")
        app.mainloop()
    finally:
        _stop_ipc_server()
        _release_instance_mutex()


if __name__ == "__main__":
    main()
