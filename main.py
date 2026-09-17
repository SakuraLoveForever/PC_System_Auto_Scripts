"""
PC System Auto Scripts — Power Plan Monitor & Startup Manager
Dark-mode desktop app with 5 switchable design styles, EN/ZH i18n.
"""

from __future__ import annotations

import ctypes
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
from pathlib import Path
from typing import Dict, List, Optional

import customtkinter as ctk
from PIL import Image, ImageDraw
from tkinter import Canvas, Menu, filedialog, font as tkfont

from pypinyin import lazy_pinyin, Style
from styles import STYLES, DEFAULT_STYLE, STYLE_ALIASES, DesignStyle
from power_manager import (
    PowerMonitor,
    create_missing_builtin_schemes,
    find_auto_target,
    find_high_performance_plan,
    find_plan_by_guid,
    get_power_snapshot,
    is_plan_matching_target,
    set_active_plan,
    set_unsupported_targets,
    unsupported_targets,
)
from startup_manager import (
    StartupItem,
    add_registry_startup,
    backup_item,
    build_self_startup_command,
    get_all_items,
    get_app_exe_path,
    is_known_source,
    list_backups,
    open_startup_location,
    remove_item,
    remove_registry_startup,
    restore_last_backup,
)
from i18n import I18n, SUPPORTED_LANGS, LANG_LABELS, DEFAULT_LANG

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
    "monitor_enabled": True,
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


def _normalize_config(raw) -> dict:
    """Validate an arbitrary JSON object into a complete, usable config."""
    cfg = dict(CONFIG_DEFAULTS)
    if not isinstance(raw, dict):
        return cfg

    style = STYLE_ALIASES.get(raw.get("style"), raw.get("style"))
    cfg["style"] = style if style in STYLES else DEFAULT_STYLE
    cfg["language"] = _coerce_choice(raw.get("language"), SUPPORTED_LANGS, DEFAULT_LANG)
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

    A saved ``monitor_enabled`` value is always respected — the monitor is only
    enabled by default on a genuine first run (no config file yet).
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


def _create_tray_icon(color_hex: str = "#5e6ad2"):
    img = _create_icon_image(64, color_hex)
    img.save(str(ICON_FILE), format="ICO", sizes=[(64, 64)])
    return Image.open(str(ICON_FILE))


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
SIDEBAR_EXPANDED = 180
SIDEBAR_COLLAPSED = 56
COL_KEYS = ["name", "path", "source", "action"]
COL_DEFAULTS = [0.24, 0.43, 0.12, 0.18]
COL_MIN = 0.04
HANDLE_WIDTH = 4
ROW_HEIGHT = 24
AUTO_TARGET_LABEL = "Auto"
SELF_STARTUP_NAME = "PC_System_Auto_Scripts"
# A hover tooltip must never outlive the interaction that revealed it.
TIP_TIMEOUT_MS = 4000


# ======================================================================
# Style helper — apply a DesignStyle to a widget
# ======================================================================

def apply_btn_style(btn: ctk.CTkButton, s: DesignStyle):
    """Style a CTkButton as a Magic UI-inspired primary button."""
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


def apply_dropdown(dd: ctk.CTkOptionMenu, s: DesignStyle):
    """Style a CTkOptionMenu."""
    dd.configure(fg_color=s.card, text_color=s.text_primary,
                 button_color=s.accent, button_hover_color=s.accent_hover,
                 corner_radius=8,
                 dropdown_fg_color=s.card, dropdown_text_color=s.text_primary,
                 dropdown_hover_color=s.card_elevated)


def apply_entry(entry: ctk.CTkEntry, s: DesignStyle):
    """Style a CTkEntry."""
    entry.configure(fg_color=s.card, text_color=s.text_primary,
                    placeholder_text_color=s.text_muted,
                    border_color=s.border,
                    border_width=1,
                    corner_radius=min(s.input_radius, 8))


def apply_switch(sw: ctk.CTkSwitch, s: DesignStyle):
    """Style a CTkSwitch."""
    sw.configure(progress_color=s.accent, button_color=s.text_primary,
                  text_color=s.text_primary)


def apply_card(card: ctk.CTkFrame, s: DesignStyle):
    """Style a card frame."""
    card.configure(fg_color=s.card, corner_radius=s.card_radius,
                   border_width=1, border_color=s.border)


def apply_surface(sf: ctk.CTkFrame, s: DesignStyle):
    """Style a surface frame."""
    sf.configure(fg_color=s.surface)


def apply_surface_corner(sf: ctk.CTkFrame, s: DesignStyle):
    """Style a surface frame with corner radius."""
    sf.configure(fg_color=s.surface, corner_radius=8,
                 border_width=1, border_color=s.border)


def _measure_widget_text(widget, text: str, fallback_size: int = 14) -> int:
    """Measure rendered text width for adaptive CTk control sizing."""
    text = str(text or "")
    try:
        font_obj = widget.cget("font")
        if hasattr(font_obj, "measure"):
            return font_obj.measure(text)
        return tkfont.Font(font=font_obj).measure(text)
    except Exception:
        return max(1, len(text)) * max(7, fallback_size // 2)


def _text_width(widget, text: str, min_width: int, max_width: int, padding: int) -> int:
    measured = _measure_widget_text(widget, text)
    return max(min_width, min(max_width, measured + padding))


def fit_option_width(dd: ctk.CTkOptionMenu, values: List[str], min_width: int = 88,
                     max_width: int = 320, padding: int = 58):
    longest = max([str(v) for v in values] or [dd.get()], key=len)
    width = _text_width(dd, longest, min_width, max_width, padding)
    dd.configure(width=width, dynamic_resizing=False)
    return width


def fit_entry_width(entry: ctk.CTkEntry, text: str = "", min_width: int = 56,
                    max_width: int = 220, padding: int = 28):
    if not text:
        try:
            text = entry.get()
        except Exception:
            text = ""
    if not text:
        try:
            text = entry.cget("placeholder_text")
        except Exception:
            text = ""
    entry.configure(width=_text_width(entry, text, min_width, max_width, padding))


def fit_button_width(btn: ctk.CTkButton, text: str = "", min_width: int = 44,
                     max_width: int = 220, padding: int = 32):
    if not text:
        try:
            text = btn.cget("text")
        except Exception:
            text = ""
    width = _text_width(btn, text, min_width, max_width, padding)
    btn.configure(width=width)
    return width


def bind_entry_autofit(entry: ctk.CTkEntry, min_width: int = 56,
                       max_width: int = 220, padding: int = 28):
    fit_entry_width(entry, min_width=min_width, max_width=max_width, padding=padding)
    entry.bind(
        "<KeyRelease>",
        lambda _e: fit_entry_width(entry, min_width=min_width,
                                   max_width=max_width, padding=padding),
        add="+",
    )


# ======================================================================
# Dropdown toggle helper — makes CTkOptionMenu toggle on click
# ======================================================================

def _make_dropdown_toggle(dd: ctk.CTkOptionMenu):
    """Patch a CTkOptionMenu: click to open, click again to close."""
    import tkinter as _tk
    _orig_open = dd._open_dropdown_menu
    _orig_callback = dd._dropdown_callback
    dd._menu_open = False

    def _toggle_open():
        if dd._menu_open:
            try:
                dd._dropdown_menu.unpost()
            except _tk.TclError:
                pass
            dd._menu_open = False
        else:
            _orig_open()
            dd._menu_open = True

    def _on_select(value):
        dd._menu_open = False
        # Clear stale ref so next _orig_open creates a fresh menu
        dd.after(10, lambda: setattr(dd, '_dropdown_menu', None))
        _orig_callback(value)

    dd._open_dropdown_menu = _toggle_open
    dd._dropdown_callback = _on_select


# ======================================================================
# Main Application
# ======================================================================

class App(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.cfg = load_config()
        self.cfg["style"] = STYLE_ALIASES.get(self.cfg.get("style"), self.cfg.get("style"))
        if self.cfg.get("style") not in STYLES:
            self.cfg["style"] = DEFAULT_STYLE
        self._style: DesignStyle = STYLES[self.cfg["style"]]
        self._i18n = I18n(self.cfg.get("language", DEFAULT_LANG))
        self._sidebar_expanded = True
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

        # Compact mode
        self._compact_mode = False
        self._compact_frame: Optional[ctk.CTkFrame] = None
        self._full_geometry = "880x580"
        self._compact_geometry = "260x168"

        # Column widths (fractions, sum ~1.0)
        self._col_widths = list(self.cfg.get("col_widths", COL_DEFAULTS))
        if len(self._col_widths) != 4:
            self._col_widths = list(COL_DEFAULTS)
        elif self._col_widths[3] < 0.14:
            self._col_widths = list(COL_DEFAULTS)
        self._resizing_col: int = -1
        self._resize_start_x: int = 0
        self._resize_start_w0: float = 0.0
        self._resize_start_w1: float = 0.0
        self._header_width: int = 500

        # Registry of widgets that need restyling on theme switch
        # Each entry: (widget, styler_function)
        self._stylables: List[tuple] = []
        # Track startup item → row widget for surgical removal
        self._item_rows: Dict[str, ctk.CTkFrame] = {}
        # row id → its cell widgets (kept out of the widget tree to save paint cost)
        self._row_widgets: Dict[int, dict] = {}
        self._row_pool: List[ctk.CTkFrame] = []
        self._startup_items_cache: List[StartupItem] = []
        self._startup_sources_cache: List[str] = []
        self._startup_refresh_running = False
        self._startup_refresh_pending = False
        self._startup_requery_pending = False
        self._relayout_rows_after_id = None
        self._power_refresh_running = False
        self._power_refresh_pending = False
        self._power_plans_cache: List = []
        # GUID ↔ dropdown/channel labels
        self._target_guid_map: Dict[str, Optional[str]] = {AUTO_TARGET_LABEL: None}
        self._label_by_guid: Dict[str, str] = {}
        self._missing_target_guid: str = ""
        self._creating_schemes = False

        self.power_monitor = PowerMonitor(
            on_status_change=self._on_power_status_change,
            interval_seconds=self.cfg["check_interval"],
            on_monitor_state_change=self._on_monitor_state_change,
        )
        if self.cfg.get("target_guid"):
            self.power_monitor.target_guid = self.cfg["target_guid"]

        self.title(self._i18n.t("app.title"))
        self.geometry("880x580")
        self.minsize(620, 420)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # Do NOT call set_default_color_theme — we manage colors manually
        ctk.set_appearance_mode("dark")
        self.configure(fg_color=self._style.canvas)

        self._build_sidebar()
        self._build_main_content()

        # add="+" — the main-content builder already bound <Configure> for the
        # sidebar/content alignment; a plain bind() would silently replace it and
        # the content would keep its build-time width after any resize.
        self.bind("<Configure>", self._on_window_configure, add="+")

        # The initial <Configure> can be missed while the window is being mapped,
        # so align the content explicitly once the real size is known.
        self.after_idle(self._relayout_main_frame)
        self.after(120, self._relayout_main_frame)
        self._refresh_ui_queue()
        if self.cfg.get("monitor_enabled"):
            self._start_monitor(save=False)
        self._sync_monitor_switch()

        self.after(400, self._refresh_power_status)

        first_run = not self.cfg.get("setup_completed", False)
        if first_run:
            # First run always shows the window so the user sees the current plan,
            # the target policy and the monitor switch before anything is chosen.
            self.after(700, self._show_first_run_setup)
        elif self.cfg.get("start_to_tray", True):
            if self._setup_tray():
                self._window_visible = False
                self.after(500, self.withdraw)

        if not first_run and CONFIG_FILE_ERROR:
            self.after(900, lambda: self._show_check_result(CONFIG_FILE_ERROR, "warning"))

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

    # ==================================================================
    # Micro-interaction helpers
    # ==================================================================

    def _btn_press_flash(self, btn: ctk.CTkButton):
        """Brief flash on button press — instant darken, spring back."""
        if not btn.winfo_exists():
            return
        orig = btn.cget("fg_color")
        hover = btn.cget("hover_color")
        if isinstance(orig, tuple):
            orig = orig[0] if orig[1] == orig[0] else orig[0]
        if isinstance(hover, tuple):
            hover = hover[0]
        dark = self._style.card  # press color
        btn.configure(fg_color=dark)
        btn.after(60, lambda: btn.configure(fg_color=hover))
        btn.after(150, lambda: btn.configure(fg_color=orig))

    def _status_pulse(self, label: ctk.CTkLabel, color: str):
        """Pulse a label — briefly scale text via font size bump."""
        if not label.winfo_exists():
            return
        font = label.cget("font")
        try:
            base_size = font.cget("size")
        except Exception:
            return
        big = ctk.CTkFont(size=base_size + 2, weight="bold")
        label.configure(font=big, text_color=color)
        label.after(300, lambda: label.configure(font=font, text_color=color))

    # ==================================================================
    # Style switching
    # ==================================================================

    def _switch_style(self, name: str):
        name = STYLE_ALIASES.get(name, name)
        if name not in STYLES:
            return
        self._style = STYLES[name]
        self.cfg["style"] = name
        self._persist_config()

        # Update root window
        self.configure(fg_color=self._style.canvas)

        # Re-apply style to all registered widgets
        for widget, styler in self._stylables:
            try:
                if widget.winfo_exists():
                    styler(widget, self._style)
            except Exception:
                pass

        self.update_idletasks()
        self._update_tray_icon_color()
        # Defer rebuild to avoid conflicts with dropdown menu closing
        if hasattr(self, '_deferred_refresh_id'):
            self.after_cancel(self._deferred_refresh_id)
        self._deferred_refresh_id = self.after(100, self._deferred_refresh_startup)

    def _deferred_refresh_startup(self):
        self._refresh_startup_list()

    def _reg(self, widget, styler):
        """Register a widget for automatic restyling on theme switch."""
        self._stylables.append((widget, styler))

    def _mode_button_text(self) -> str:
        return self._i18n.t("compact.toggle") if not self._compact_mode else self._i18n.t("compact.full")

    def _remember_geometry_for_mode(self):
        geom = self.geometry()
        if not geom or "x" not in geom:
            return
        if self._compact_mode:
            self._compact_geometry = geom
        else:
            self._full_geometry = geom

    def _apply_geometry_for_mode(self):
        geom = self._compact_geometry if self._compact_mode else self._full_geometry
        if geom:
            try:
                self.geometry(geom)
            except Exception:
                pass

    def _update_mode_switch_texts(self):
        text = self._mode_button_text()
        if hasattr(self, "_compact_toggle_btn") and self._compact_toggle_btn.winfo_exists():
            self._compact_toggle_btn.configure(text=text)
        if hasattr(self, "_compact_full_btn") and self._compact_full_btn.winfo_exists():
            self._compact_full_btn.configure(text=self._i18n.t("compact.full"))

    # ==================================================================
    # Sidebar
    # ==================================================================

    def _build_sidebar(self):
        s = self._style

        self._sidebar = ctk.CTkFrame(self, width=SIDEBAR_EXPANDED, corner_radius=0)
        apply_surface(self._sidebar, s)
        self._sidebar.pack(side="left", fill="y")
        self._sidebar.pack_propagate(False)
        self._reg(self._sidebar, apply_surface)

        # Brand
        self._brand_frame = ctk.CTkFrame(self._sidebar, fg_color="transparent")
        self._brand_frame.pack(fill="x", padx=10, pady=(12, 3))
        self._brand_icon = ctk.CTkLabel(self._brand_frame, text="⚡", font=ctk.CTkFont(size=20),
                                        text_color=s.accent, anchor="w")
        self._brand_icon.pack(side="left")
        self._reg(self._brand_icon, lambda w, s: w.configure(text_color=s.accent))
        self._brand_text = ctk.CTkLabel(self._brand_frame, text="PC Auto",
                                        font=ctk.CTkFont(size=15, weight="bold"),
                                        text_color=s.text_primary, anchor="w")
        self._brand_text.pack(side="left", padx=(8, 0))
        self._reg(self._brand_text, lambda w, s: w.configure(text_color=s.text_primary))

        # Section labels
        self._appearance_label = ctk.CTkLabel(
            self._sidebar, text=self._i18n.t("sidebar.appearance"),
            font=ctk.CTkFont(size=11), text_color=s.text_muted, anchor="w")
        self._appearance_label.pack(fill="x", padx=10, pady=(8, 3))
        self._reg(self._appearance_label, lambda w, s: w.configure(text_color=s.text_muted))

        # Style dropdown
        self._style_dropdown = ctk.CTkOptionMenu(
            self._sidebar, values=list(STYLES.keys()), font=ctk.CTkFont(size=13),
            command=self._switch_style)
        apply_dropdown(self._style_dropdown, s)
        _make_dropdown_toggle(self._style_dropdown)
        self._style_dropdown.set(self.cfg["style"])
        fit_option_width(self._style_dropdown, list(STYLES.keys()), min_width=100,
                         max_width=SIDEBAR_EXPANDED - 20)
        self._style_dropdown.pack(anchor="w", padx=8, pady=(2, 4))
        self._reg(self._style_dropdown, apply_dropdown)

        # Language dropdown
        self._lang_dropdown = ctk.CTkOptionMenu(
            self._sidebar, values=[LANG_LABELS[l] for l in SUPPORTED_LANGS],
            font=ctk.CTkFont(size=13), command=self._on_lang_changed)
        apply_dropdown(self._lang_dropdown, s)
        _make_dropdown_toggle(self._lang_dropdown)
        self._lang_dropdown.set(LANG_LABELS.get(self._i18n.lang, "中文"))
        fit_option_width(self._lang_dropdown, [LANG_LABELS[l] for l in SUPPORTED_LANGS],
                         min_width=80, max_width=SIDEBAR_EXPANDED - 20)
        self._lang_dropdown.pack(anchor="w", padx=8, pady=(2, 4))
        self._reg(self._lang_dropdown, apply_dropdown)

        # Icon buttons for collapsed mode
        self._style_icon_btn = ctk.CTkButton(
            self._sidebar, text="🎨", width=44, height=36,
            font=ctk.CTkFont(size=18), command=self._cycle_style)
        self._reg(self._style_icon_btn, apply_btn_secondary)
        apply_btn_secondary(self._style_icon_btn, s)

        self._lang_icon_btn = ctk.CTkButton(
            self._sidebar, text="🌐", width=44, height=36,
            font=ctk.CTkFont(size=18), command=self._cycle_language)
        self._reg(self._lang_icon_btn, apply_btn_secondary)
        apply_btn_secondary(self._lang_icon_btn, s)

        # Separator + settings section
        self._sep2 = ctk.CTkFrame(self._sidebar, height=1)
        self._sep2.configure(fg_color=s.border)
        self._sep2.pack(fill="x", padx=8, pady=(10, 6))
        self._reg(self._sep2, lambda w, s: w.configure(fg_color=s.border))

        self._settings_label = ctk.CTkLabel(
            self._sidebar, text=self._i18n.t("sidebar.settings"),
            font=ctk.CTkFont(size=11), text_color=s.text_muted, anchor="w")
        self._settings_label.pack(fill="x", padx=10, pady=(0, 3))
        self._reg(self._settings_label, lambda w, s: w.configure(text_color=s.text_muted))

        # Close behavior toggle
        self._tray_toggle_switch = ctk.CTkSwitch(
            self._sidebar, text=self._i18n.t("tray.minimize_to_tray"),
            font=ctk.CTkFont(size=12), text_color=s.text_secondary,
            command=self._toggle_tray_behavior)
        apply_switch(self._tray_toggle_switch, s)
        self._tray_toggle_switch.pack(padx=10, pady=(2, 3), anchor="w")
        if self.cfg.get("minimize_to_tray", True):
            self._tray_toggle_switch.select()
        self._reg(self._tray_toggle_switch, apply_switch)

        # Version
        self._sidebar_version = ctk.CTkLabel(
            self._sidebar, text="v1.0.0", font=ctk.CTkFont(size=11), text_color=s.text_muted)
        self._sidebar_version.pack(side="bottom", pady=(0, 6))
        self._reg(self._sidebar_version, lambda w, s: w.configure(text_color=s.text_muted))

        # Toggle button — placed on the right edge of the sidebar,
        # owned by the root window so it floats at the boundary.
        self._toggle_btn = ctk.CTkButton(
            self, text="◀", width=22, height=64,
            font=ctk.CTkFont(size=13), command=self._toggle_sidebar,
            corner_radius=6)
        apply_btn_secondary(self._toggle_btn, s)
        self._reg(self._toggle_btn, apply_btn_secondary)
        self._toggle_btn.place(x=SIDEBAR_EXPANDED - 2, rely=0.5, anchor="w")

    def _toggle_tray_behavior(self):
        self.cfg["minimize_to_tray"] = bool(self._tray_toggle_switch.get())
        self._persist_config()

    def _toggle_sidebar(self):
        delta = SIDEBAR_EXPANDED - SIDEBAR_COLLAPSED  # 150
        cur_w, cur_h = self._requested_size()

        if self._sidebar_expanded:
            # Collapse: shrink sidebar, shrink window, main content stays same size
            self._sidebar.configure(width=SIDEBAR_COLLAPSED)
            self._toggle_btn.configure(text="▶")
            for w in [self._brand_frame, self._appearance_label,
                       self._sep2, self._settings_label,
                       self._tray_toggle_switch, self._sidebar_version]:
                if w: w.pack_forget()
            self._style_dropdown.pack_forget()
            self._lang_dropdown.pack_forget()
            self._style_icon_btn.pack(pady=(8, 2), padx=6)
            self._lang_icon_btn.pack(pady=(2, 8), padx=6)
            new_win_w = max(400, cur_w - delta)
            self._toggle_btn.place(x=SIDEBAR_COLLAPSED - 2, rely=0.5, anchor="w")
        else:
            # Expand: grow sidebar, grow window, main content stays same size
            self._sidebar.configure(width=SIDEBAR_EXPANDED)
            self._toggle_btn.configure(text="◀")
            self._style_icon_btn.pack_forget()
            self._lang_icon_btn.pack_forget()
            self._brand_frame.pack(fill="x", padx=10, pady=(12, 3))
            self._appearance_label.pack(fill="x", padx=10, pady=(8, 3))
            self._style_dropdown.pack(anchor="w", padx=8, pady=(2, 4))
            self._lang_dropdown.pack(anchor="w", padx=8, pady=(2, 4))
            self._sep2.pack(fill="x", padx=8, pady=(10, 6))
            self._settings_label.pack(fill="x", padx=10, pady=(0, 3))
            self._tray_toggle_switch.pack(padx=10, pady=(2, 3), anchor="w")
            self._sidebar_version.pack(side="bottom", pady=(0, 6))
            new_win_w = cur_w + delta
            self._toggle_btn.place(x=SIDEBAR_EXPANDED - 2, rely=0.5, anchor="w")

        self._sidebar_expanded = not self._sidebar_expanded
        self.geometry(f"{new_win_w}x{cur_h}")
        self._snap_main_to_sidebar()

    def _cycle_style(self):
        """Cycle to the next design style (icon button in collapsed sidebar)."""
        names = list(STYLES.keys())
        cur = self._style_dropdown.get()
        idx = names.index(cur) if cur in names else 0
        next_name = names[(idx + 1) % len(names)]
        self._style_dropdown.set(next_name)
        self._switch_style(next_name)

    def _cycle_language(self):
        """Toggle language (icon button in collapsed sidebar)."""
        cur = self._lang_dropdown.get()
        labels = [LANG_LABELS[l] for l in SUPPORTED_LANGS]
        idx = labels.index(cur) if cur in labels else 0
        next_label = labels[(idx + 1) % len(labels)]
        self._lang_dropdown.set(next_label)
        self._on_lang_changed(next_label)

    def _on_lang_changed(self, label: str):
        lang_code = None
        for code, lbl in LANG_LABELS.items():
            if lbl == label:
                lang_code = code
                break
        if lang_code is None:
            return
        self._i18n.lang = lang_code
        self.cfg["language"] = lang_code
        self._persist_config()
        self._refresh_all_text()

    # ==================================================================
    # Main content
    # ==================================================================

    def _build_main_content(self):
        s = self._style
        main = ctk.CTkFrame(self, fg_color="transparent")
        self._main_frame_ref = main
        # Fractional placement only; no pixel width, which would be ambiguous
        # under Windows DPI scaling. _relayout_main_frame() sets the fractions.
        main.place(relx=SIDEBAR_EXPANDED / 880.0, y=0, relheight=1.0,
                   relwidth=1.0 - SIDEBAR_EXPANDED / 880.0)
        self.bind("<Configure>", self._on_root_configure, add="+")

        # Header — minimal, single row
        header = ctk.CTkFrame(main, fg_color="transparent", height=26)
        header.pack(fill="x", padx=10, pady=(4, 0))
        header.pack_propagate(False)

        self._header_title_label = ctk.CTkLabel(
            header, text=self._i18n.t("app.title"),
            font=ctk.CTkFont(size=15, weight="bold"), text_color=s.text_primary,
            anchor="w")
        self._header_title_label.pack(side="left")
        self._reg(self._header_title_label, lambda w, s: w.configure(text_color=s.text_primary))

        self._monitor_switch = ctk.CTkSwitch(
            header, text=self._i18n.t("power.auto_monitor"), font=ctk.CTkFont(size=11),
            command=self._toggle_monitor)
        apply_switch(self._monitor_switch, s)
        self._monitor_switch.pack(side="right", padx=(0, 8))
        if self.cfg["monitor_enabled"]:
            self._monitor_switch.select()
        self._reg(self._monitor_switch, apply_switch)

        self._compact_toggle_btn = ctk.CTkButton(
            header, text=self._i18n.t("compact.toggle"), width=44, height=22,
            font=ctk.CTkFont(size=10), command=self._toggle_compact_mode)
        apply_btn_secondary(self._compact_toggle_btn, s)
        self._compact_toggle_btn.pack(side="right")
        self._reg(self._compact_toggle_btn, apply_btn_secondary)
        fit_button_width(self._compact_toggle_btn, min_width=74, max_width=110, padding=24)

        self._main_frame = main
        self._build_power_card()
        self._build_startup_card()

        # Build compact view (hidden by default)
        self._build_compact_view()

        self._status_bar = ctk.CTkLabel(
            main, text="", font=ctk.CTkFont(size=10), text_color=s.text_muted, anchor="w")
        # Reserved only while a message is showing: an always-present empty strip
        # at the bottom of the window wasted a row of space.
        self._status_bar_visible = False
        self._reg(self._status_bar, lambda w, s: w.configure(text_color=s.text_muted))

    def _on_root_configure(self, event):
        """Update main content when user resizes window (only when expanded)."""
        if event.widget != self:
            return
        if self._compact_mode:
            return
        if not self._sidebar_expanded:
            return
        self._relayout_main_frame()

    def _snap_main_to_sidebar(self):
        """Position main content flush to the sidebar edge."""
        self._relayout_main_frame()

    def _window_size(self) -> tuple:
        """Reported window size — the same space used by place() and winfo_*.

        Note that these are physical pixels while the geometry *string* is in
        logical units, so scaling them must go through the geometry string.
        """
        return self.winfo_width(), self.winfo_height()

    def _requested_size(self) -> tuple:
        """The geometry string's size (logical units) — the safe space to scale."""
        try:
            size = self.geometry().split("+")[0]
            width, height = size.split("x")
            return int(width), int(height)
        except (ValueError, AttributeError):
            return self._window_size()

    def _relayout_main_frame(self):
        """Keep the main content beside the sidebar, filling the rest of the window.

        Only fractional placement is used: absolute pixel units mean different
        things to the geometry string, winfo_* and place() under Windows DPI
        scaling, and mixing them is what leaves a large empty strip on the right.
        """
        if not hasattr(self, "_main_frame_ref") or not self._main_frame_ref.winfo_exists():
            return
        if self._compact_mode:
            return
        width, _ = self._requested_size()
        width = max(width, 200)
        sidebar = SIDEBAR_EXPANDED if self._sidebar_expanded else SIDEBAR_COLLAPSED
        relx = min(max(sidebar / width, 0.02), 0.8)
        self._main_frame_ref.place_configure(relx=relx, y=0, relheight=1.0,
                                             relwidth=1.0 - relx)

    def _on_window_configure(self, event):
        if event.widget != self:
            return
        if hasattr(self, '_header_frame') and self._header_frame.winfo_exists():
            w = self._header_frame.winfo_width()
            if w > 10:
                self._header_width = w
                self._relayout_header()
                self._schedule_relayout_all_rows()

    # ==================================================================
    # Power plan card
    # ==================================================================

    def _build_power_card(self):
        s = self._style
        card = ctk.CTkFrame(self._main_frame)
        apply_card(card, s)
        card.pack(fill="x", padx=8, pady=(2, 1))
        self._reg(card, apply_card)

        # Row 1: title + status badge + active plan name
        r1 = ctk.CTkFrame(card, fg_color="transparent", height=28)
        r1.pack(fill="x", padx=8, pady=(4, 1))
        r1.pack_propagate(False)

        self._power_title_label = ctk.CTkLabel(
            r1, text="⚡ " + self._i18n.t("power.title"),
            font=ctk.CTkFont(size=12, weight="bold"), text_color=s.text_primary, anchor="w")
        self._power_title_label.pack(side="left")
        self._reg(self._power_title_label, lambda w, s: w.configure(text_color=s.text_primary))

        self._power_status_badge = ctk.CTkLabel(
            r1, text=self._i18n.t("power.status_checking"),
            font=ctk.CTkFont(size=10, weight="bold"), text_color=s.text_secondary,
            width=64, height=18, fg_color=s.surface, corner_radius=9)
        self._power_status_badge.pack(side="left", padx=(6, 8))
        self._reg(self._power_status_badge,
                  lambda w, s: w.configure(text_color=s.text_secondary, fg_color=s.surface))

        self._power_active_value = ctk.CTkLabel(
            r1, text="--", font=ctk.CTkFont(size=14, weight="bold"),
            text_color=s.text_primary, anchor="w")
        self._power_active_value.pack(side="left")
        self._reg(self._power_active_value, lambda w, s: w.configure(text_color=s.text_primary))

        # The latest check result shares this row instead of owning one: a whole
        # row that is usually empty made the card feel bulky and hollow.
        self._check_result_label = ctk.CTkLabel(
            r1, text="", font=ctk.CTkFont(size=10), text_color=s.text_secondary,
            anchor="w", justify="left")
        self._check_result_label.pack(side="left", padx=(10, 6))
        self._reg(self._check_result_label, lambda w, s: w.configure(text_color=s.text_secondary))

        # Row 1 (right side): the two system-modifying actions live here, because
        # row 2 already fills the card at the minimum window width.
        self._create_schemes_btn = ctk.CTkButton(
            r1, text=self._i18n.t("power.create_schemes_btn"), height=22,
            font=ctk.CTkFont(size=10), command=self._create_missing_schemes)
        apply_btn_secondary(self._create_schemes_btn, s)
        fit_button_width(self._create_schemes_btn, min_width=72, max_width=130, padding=22)
        self._create_schemes_btn.pack(side="right")
        self._reg(self._create_schemes_btn, apply_btn_secondary)

        self._check_btn = ctk.CTkButton(
            r1, text=self._i18n.t("power.check_now"), height=22,
            font=ctk.CTkFont(size=10),
            command=lambda: [self._btn_press_flash(self._check_btn), self._check_now()])
        apply_btn_style(self._check_btn, s)
        fit_button_width(self._check_btn, min_width=64, max_width=110, padding=22)
        self._check_btn.pack(side="right", padx=(0, 6))
        self._reg(self._check_btn, apply_btn_style)

        # Row 2: target + interval controls, kept adjacent so the row has no hole
        # in the middle at wide window sizes.
        r2 = ctk.CTkFrame(card, fg_color="transparent", height=28)
        r2.pack(fill="x", padx=8, pady=(2, 4))
        r2.pack_propagate(False)

        self._power_target_label = ctk.CTkLabel(
            r2, text=self._i18n.t("power.target_plan") + ":",
            font=ctk.CTkFont(size=10), text_color=s.text_secondary, anchor="w")
        self._power_target_label.pack(side="left", padx=(0, 2))
        self._reg(self._power_target_label, lambda w, s: w.configure(text_color=s.text_secondary))

        self._target_dropdown = ctk.CTkOptionMenu(
            r2, values=["Auto"], font=ctk.CTkFont(size=11),
            height=24, width=110, dynamic_resizing=False, command=self._on_target_changed)
        apply_dropdown(self._target_dropdown, s)
        self._target_dropdown.pack(side="left", padx=(0, 12))
        self._reg(self._target_dropdown, apply_dropdown)
        self._populate_target_dropdown()
        _make_dropdown_toggle(self._target_dropdown)

        self._power_interval_label = ctk.CTkLabel(
            r2, text=self._i18n.t("power.interval") + ":",
            font=ctk.CTkFont(size=10), text_color=s.text_secondary, anchor="w")
        self._power_interval_label.pack(side="left", padx=(0, 2))
        self._reg(self._power_interval_label, lambda w, s: w.configure(text_color=s.text_secondary))

        self._interval_entry = ctk.CTkEntry(r2, width=38, height=24, font=ctk.CTkFont(size=11))
        apply_entry(self._interval_entry, s)
        self._interval_entry.pack(side="left")
        self._interval_entry.insert(0, str(self.cfg["check_interval"]))
        self._interval_entry.bind("<Return>", lambda e: self._apply_interval())
        self._reg(self._interval_entry, apply_entry)

        self._power_interval_suffix = ctk.CTkLabel(
            r2, text=self._i18n.t("power.seconds"),
            font=ctk.CTkFont(size=9), text_color=s.text_muted, width=12)
        self._power_interval_suffix.pack(side="left", padx=(2, 3))
        self._reg(self._power_interval_suffix, lambda w, s: w.configure(text_color=s.text_muted))

        self._apply_interval_btn = ctk.CTkButton(
            r2, text=self._i18n.t("general.apply"), width=40, height=24,
            font=ctk.CTkFont(size=10), command=self._apply_interval)
        apply_btn_style(self._apply_interval_btn, s)
        self._apply_interval_btn.pack(side="left")
        self._reg(self._apply_interval_btn, apply_btn_style)

        self._power_card = card

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

    def _set_target_selection(self, plans=None) -> None:
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
        self._missing_target_guid = target_guid

    def _populate_target_dropdown(self):
        snapshot = get_power_snapshot()
        self._power_plans_cache = list(snapshot.plans)
        values = self._build_target_labels(snapshot.plans)
        fit_option_width(self._target_dropdown, values, min_width=110, max_width=300)
        self._target_dropdown.configure(values=values)
        self._set_target_selection(snapshot.plans)

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
        self._missing_target_guid = ""
        self.power_monitor.target_guid = guid or None
        self.cfg["target_guid"] = guid
        self._persist_config()
        if guid:
            # Only switch if UI is fully built (skip init-triggered sets)
            if hasattr(self, '_power_card') and self._power_card.winfo_exists():
                self._do_switch_plan(guid)
        else:
            self._refresh_power_status()

    # ==================================================================
    # Startup items card
    # ==================================================================

    def _build_startup_card(self):
        s = self._style
        card = ctk.CTkFrame(self._main_frame)
        apply_card(card, s)
        card.pack(fill="both", expand=True, padx=10, pady=(0, 6))
        self._reg(card, apply_card)

        # Title
        title_row = ctk.CTkFrame(card, fg_color="transparent")
        title_row.pack(fill="x", padx=8, pady=(6, 3))
        self._startup_title_label = ctk.CTkLabel(
            title_row, text=self._i18n.t("startup.title"),
            font=ctk.CTkFont(size=13, weight="bold"), text_color=s.text_primary)
        self._startup_title_label.pack(side="left")
        self._reg(self._startup_title_label, lambda w, s: w.configure(text_color=s.text_primary))

        self._startup_count_label = ctk.CTkLabel(
            title_row, text="", font=ctk.CTkFont(size=11), text_color=s.text_secondary)
        self._startup_count_label.pack(side="right", padx=(0, 3))
        self._reg(self._startup_count_label, lambda w, s: w.configure(text_color=s.text_secondary))

        self._startup_refresh_btn = ctk.CTkButton(
            title_row, text=self._i18n.t("startup.refresh_btn"), width=56, height=22,
            font=ctk.CTkFont(size=11), command=lambda: self._refresh_startup_list(force=True))
        apply_btn_secondary(self._startup_refresh_btn, s)
        fit_button_width(self._startup_refresh_btn, min_width=50, max_width=90)
        self._startup_refresh_btn.pack(side="right")
        self._reg(self._startup_refresh_btn, apply_btn_secondary)

        # Shown only while a reversible removal is waiting to be undone.
        self._startup_restore_btn = ctk.CTkButton(
            title_row, text=self._i18n.t("startup.restore_btn", count=0), width=70, height=22,
            font=ctk.CTkFont(size=11), command=self._restore_last_removed)
        apply_btn_secondary(self._startup_restore_btn, s)
        fit_button_width(self._startup_restore_btn, min_width=64, max_width=150)
        self._reg(self._startup_restore_btn, apply_btn_secondary)

        self._source_filter_dropdown = ctk.CTkOptionMenu(
            title_row, values=[self._i18n.t("startup.source_all")],
            font=ctk.CTkFont(size=11), height=22, dynamic_resizing=False,
            command=self._on_source_filter_changed)
        apply_dropdown(self._source_filter_dropdown, s)
        _make_dropdown_toggle(self._source_filter_dropdown)
        fit_option_width(self._source_filter_dropdown, [self._i18n.t("startup.source_all")],
                         min_width=96, max_width=170, padding=28)
        self._source_filter_dropdown.pack(side="right", padx=(0, 6))
        self._reg(self._source_filter_dropdown, apply_dropdown)

        # --- Add row ---
        add_row = ctk.CTkFrame(card)
        apply_surface_corner(add_row, s)
        add_row.pack(fill="x", padx=8, pady=(0, 6))
        self._reg(add_row, apply_surface_corner)

        self._startup_name_entry = ctk.CTkEntry(
            add_row, placeholder_text=self._i18n.t("startup.name_placeholder"),
            height=24, font=ctk.CTkFont(size=12))
        apply_entry(self._startup_name_entry, s)
        bind_entry_autofit(self._startup_name_entry, min_width=90, max_width=160, padding=28)
        self._startup_name_entry.pack(side="left", fill="x", expand=True, padx=(6, 3), pady=6)
        self._reg(self._startup_name_entry, apply_entry)

        self._startup_path_entry = ctk.CTkEntry(
            add_row, placeholder_text=self._i18n.t("startup.path_placeholder"),
            height=24, font=ctk.CTkFont(size=12))
        apply_entry(self._startup_path_entry, s)
        bind_entry_autofit(self._startup_path_entry, min_width=140, max_width=350, padding=32)
        self._startup_path_entry.pack(side="left", fill="x", expand=True, padx=(0, 3), pady=6)
        self._reg(self._startup_path_entry, apply_entry)

        self._startup_browse_btn = ctk.CTkButton(
            add_row, text="...", width=28, height=24, font=ctk.CTkFont(size=12),
            command=self._browse_startup_path)
        apply_btn_secondary(self._startup_browse_btn, s)
        self._startup_browse_btn.pack(side="left", padx=(0, 3), pady=6)
        self._reg(self._startup_browse_btn, apply_btn_secondary)

        self._startup_add_btn = ctk.CTkButton(
            add_row, text=self._i18n.t("startup.add_btn"), width=60, height=24,
            font=ctk.CTkFont(size=12), command=self._add_startup_item)
        apply_btn_style(self._startup_add_btn, s)
        fit_button_width(self._startup_add_btn, min_width=54, max_width=100)
        self._startup_add_btn.pack(side="left", padx=(0, 8), pady=6)
        self._reg(self._startup_add_btn, apply_btn_style)

        # Toggle button: "Add This App" / "Remove This App" — pinned right so the
        # row has no dead space in the middle.
        self._startup_add_self_btn = ctk.CTkButton(
            add_row, text=self._i18n.t("startup.add_self_btn"), width=82, height=24,
            font=ctk.CTkFont(size=11), command=self._add_self_to_startup)
        apply_btn_secondary(self._startup_add_self_btn, s)
        fit_button_width(self._startup_add_self_btn, min_width=74, max_width=130)
        self._reg(self._startup_add_self_btn, apply_btn_secondary)

        self._startup_remove_self_btn = ctk.CTkButton(
            add_row, text=self._i18n.t("startup.remove_self_btn"), width=82, height=24,
            font=ctk.CTkFont(size=11), command=self._remove_self_from_startup)
        apply_btn_secondary(self._startup_remove_self_btn, s)
        fit_button_width(self._startup_remove_self_btn, min_width=74, max_width=140)
        self._reg(self._startup_remove_self_btn, apply_btn_secondary)
        self._update_self_buttons()

        # --- Column header ---
        self._header_frame = ctk.CTkFrame(card, height=24)
        apply_surface_corner(self._header_frame, s)
        self._header_frame.pack(fill="x", padx=8, pady=(0, 2))
        self._header_frame.pack_propagate(False)
        self._header_frame.bind("<Configure>", self._on_header_configure)
        self._reg(self._header_frame, apply_surface_corner)

        # Store: key → (label, handle, handle_bind_ids)
        self._col_header_widgets: Dict[str, tuple] = {}
        col_i18n = {"name": "startup.col_name", "path": "startup.col_path",
                     "source": "startup.col_source", "action": "startup.col_action"}

        for i, key in enumerate(COL_KEYS):
            lbl = ctk.CTkLabel(self._header_frame, text=self._i18n.t(col_i18n[key]),
                               font=ctk.CTkFont(size=11, weight="bold"),
                               text_color=s.text_secondary, anchor="w",
                               cursor="hand2" if key in ("name", "source") else None)
            if key in ("name", "source"):
                self._bind_header_sort(lbl, key)
            self._reg(lbl, lambda w, s: w.configure(text_color=s.text_secondary))

            if i < 3:
                handle = ctk.CTkFrame(self._header_frame, width=HANDLE_WIDTH, height=18,
                                      corner_radius=0, cursor="sb_h_double_arrow")
                handle.configure(fg_color=s.border)
                handle._col_index = i
                handle.bind("<Button-1>", lambda e, idx=i: self._start_resize(idx, e))
                handle.bind("<B1-Motion>", lambda e, idx=i: self._do_resize(idx, e))
                handle.bind("<ButtonRelease-1>", lambda e: self._end_resize())
                handle.bind("<Enter>", lambda e, h=handle: h.configure(fg_color=s.accent))
                handle.bind("<Leave>", lambda e, h=handle: h.configure(fg_color=s.border))
                self._reg(handle, lambda w, s: w.configure(fg_color=s.border))
                self._col_header_widgets[key] = (lbl, handle, None)
            else:
                self._col_header_widgets[key] = (lbl, None, None)

        self._update_sort_indicator()
        self._relayout_header()

        # --- Scrollable list ---
        self._startup_list_frame = ctk.CTkScrollableFrame(
            card, fg_color="transparent",
            scrollbar_fg_color=s.surface,
            scrollbar_button_color=s.border_strong,
            scrollbar_button_hover_color=s.accent)
        self._startup_list_frame.pack(fill="both", expand=True, padx=8, pady=(0, 4))
        self._startup_list_frame._parent_canvas.configure(yscrollincrement=3,
                                                          xscrollincrement=3)
        # Any scroll, click or mode change must clear a lingering tooltip.
        self._startup_list_frame._parent_canvas.bind(
            "<MouseWheel>", lambda e: self._hide_cell_tip(), add="+")
        self._startup_list_frame._parent_canvas.bind(
            "<Button-1>", lambda e: self._hide_cell_tip(), add="+")
        self._startup_list_frame._parent_canvas.bind(
            "<B1-Motion>", lambda e: self._hide_cell_tip(), add="+")
        self._reg(self._startup_list_frame,
                       lambda w, s: w.configure(
                           scrollbar_fg_color=s.surface,
                           scrollbar_button_color=s.border_strong,
                           scrollbar_button_hover_color=s.accent))
        self._table_rows: List[ctk.CTkFrame] = []

        self._startup_card = card
        self._refresh_startup_list()

    def _build_compact_view(self):
        """Build a tiny titlebar-free compact controller."""
        s = self._style
        frame = ctk.CTkFrame(self, fg_color="transparent")
        self._compact_frame = frame

        card = ctk.CTkFrame(frame)
        apply_card(card, s)
        card.pack(fill="both", expand=True, padx=4, pady=4)
        self._reg(card, apply_card)

        status_row = ctk.CTkFrame(card, fg_color="transparent", height=22)
        status_row.pack(fill="x", padx=6, pady=(5, 2))
        status_row.pack_propagate(False)

        self._compact_active_value = ctk.CTkLabel(
            status_row, text="--", font=ctk.CTkFont(size=11, weight="bold"),
            text_color=s.text_primary, anchor="w")
        self._compact_active_value.pack(side="left", fill="x", expand=True)
        self._reg(self._compact_active_value, lambda w, s: w.configure(text_color=s.text_primary))

        self._compact_full_btn = ctk.CTkButton(
            status_row, text=self._i18n.t("compact.full"), height=22,
            font=ctk.CTkFont(size=10), command=self._toggle_compact_mode)
        apply_btn_secondary(self._compact_full_btn, s)
        self._compact_full_btn.pack(side="right")
        self._reg(self._compact_full_btn, apply_btn_secondary)

        actions = ctk.CTkFrame(card, fg_color="transparent")
        actions.pack(fill="x", padx=6, pady=(2, 4))

        self._compact_plan_btn = ctk.CTkButton(
            actions, text=self._i18n.t("compact.switch_power"), height=24,
            font=ctk.CTkFont(size=10), command=self._show_compact_plan_menu)
        apply_btn_style(self._compact_plan_btn, s)
        self._compact_plan_btn.pack(fill="x", pady=(0, 4))
        self._reg(self._compact_plan_btn, apply_btn_style)

        self._compact_lang_btn = ctk.CTkButton(
            actions, text=self._i18n.t("compact.switch_language"), height=24,
            font=ctk.CTkFont(size=10), command=self._cycle_language)
        apply_btn_secondary(self._compact_lang_btn, s)
        self._compact_lang_btn.pack(fill="x", pady=(0, 4))
        self._reg(self._compact_lang_btn, apply_btn_secondary)

        self._compact_style_btn = ctk.CTkButton(
            actions, text=self._i18n.t("compact.switch_style"), height=24,
            font=ctk.CTkFont(size=10), command=self._cycle_style)
        apply_btn_secondary(self._compact_style_btn, s)
        self._compact_style_btn.pack(fill="x")
        self._reg(self._compact_style_btn, apply_btn_secondary)

        fit_button_width(self._compact_plan_btn, min_width=88, max_width=132, padding=14)
        fit_button_width(self._compact_lang_btn, min_width=88, max_width=132, padding=14)
        fit_button_width(self._compact_style_btn, min_width=88, max_width=132, padding=14)
        fit_button_width(self._compact_full_btn, min_width=58, max_width=88, padding=10)

        def _bind_drag(widget):
            widget.bind("<ButtonPress-1>", self._start_compact_drag, add="+")
            widget.bind("<B1-Motion>", self._drag_compact_window, add="+")
            widget.bind("<ButtonRelease-1>", self._end_compact_drag, add="+")

        for widget in (card, status_row, actions):
            _bind_drag(widget)

    def _show_compact_plan_menu(self):
        """Open a popup menu for switching the active power plan (GUID-bound)."""
        plans = self._power_plans_cache
        if not plans or not hasattr(self, "_compact_plan_btn"):
            self._refresh_power_status()
            return
        name_counts: Dict[str, int] = {}
        for plan in plans:
            key = plan.name.strip().lower()
            name_counts[key] = name_counts.get(key, 0) + 1
        menu = Menu(self, tearoff=0)
        for p in plans:
            label = p.name
            if name_counts.get(p.name.strip().lower(), 0) > 1:
                label = f"{label} ({p.guid[:8].upper()})"
            label += " \u2713" if p.is_active else ""
            menu.add_command(label=label, command=lambda g=p.guid: self._do_switch_plan(g))
        try:
            x = self._compact_plan_btn.winfo_rootx()
            y = self._compact_plan_btn.winfo_rooty() + self._compact_plan_btn.winfo_height()
            self._compact_plan_menu = menu
            menu.tk_popup(x, y)
        finally:
            try:
                menu.grab_release()
            except Exception:
                pass

    def _start_compact_drag(self, event):
        if not self._compact_mode:
            return
        self._compact_drag_offset = (event.x_root - self.winfo_x(), event.y_root - self.winfo_y())

    def _drag_compact_window(self, event):
        if not self._compact_mode or not hasattr(self, "_compact_drag_offset"):
            return
        dx, dy = self._compact_drag_offset
        self.geometry(f"+{event.x_root - dx}+{event.y_root - dy}")

    def _end_compact_drag(self, event):
        if self._compact_mode:
            self._compact_geometry = self.geometry()

    def _sync_compact_ui(self):
        """Sync compact view labels with full-view state."""
        if self._compact_frame is None:
            return
        self._compact_active_value.configure(text=self._power_active_value.cget("text"))
        self._update_mode_switch_texts()

    def _toggle_compact_mode(self):
        """Switch between full window and compact power-control dashboard."""
        self._set_compact_mode(not self._compact_mode)

    def _set_compact_mode(self, compact: bool):
        """Apply compact/full mode without rebuilding widgets."""
        self._hide_cell_tip()
        if self._compact_mode == compact:
            self._update_mode_switch_texts()
            return

        self._remember_geometry_for_mode()
        self._compact_mode = compact

        if compact:
            x, y = self.winfo_x(), self.winfo_y()
            self._compact_geometry = f"260x168+{max(0, x)}+{max(0, y)}"
            self._sidebar.pack_forget()
            self._main_frame_ref.place_forget()
            self._compact_frame.pack(fill="both", expand=True)
            self._compact_frame.lift()
            self.overrideredirect(True)
            self.resizable(False, False)
            self.minsize(260, 168)
            self.geometry(self._compact_geometry)
        else:
            self.overrideredirect(False)
            self.resizable(True, True)
            self._compact_frame.pack_forget()
            self._sidebar.pack(side="left", fill="y")
            self._sidebar.pack_propagate(False)
            self.minsize(620, 420)

        self._update_mode_switch_texts()
        self._apply_geometry_for_mode()
        if not compact:
            # Realign with fractional placement only. An absolute pixel x here
            # would stick in the place options and override the fraction, pushing
            # the whole layout off the window edge.
            self.update_idletasks()
            self._relayout_main_frame()
            self.after(0, self._relayout_main_frame)
            self.after(150, self._relayout_main_frame)
        self._sync_compact_ui()
        self._refresh_power_status()

    def _refresh_all_text(self):
        self.title(self._i18n.t("app.title"))
        self._header_title_label.configure(text=self._i18n.t("app.title"))
        self._power_title_label.configure(text="\u26a1 " + self._i18n.t("power.title"))
        self._power_target_label.configure(text=self._i18n.t("power.target_plan") + ":")
        self._power_interval_label.configure(text=self._i18n.t("power.interval") + ":")
        self._power_interval_suffix.configure(text=self._i18n.t("power.seconds"))
        self._apply_interval_btn.configure(text=self._i18n.t("general.apply"))
        self._check_btn.configure(text=self._i18n.t("power.check_now"))
        self._create_schemes_btn.configure(text=self._i18n.t("power.create_schemes_btn"))
        self._monitor_switch.configure(text=self._i18n.t("power.auto_monitor"))
        self._startup_title_label.configure(text=self._i18n.t("startup.title"))
        self._startup_name_entry.configure(placeholder_text=self._i18n.t("startup.name_placeholder"))
        self._startup_path_entry.configure(placeholder_text=self._i18n.t("startup.path_placeholder"))
        self._startup_add_btn.configure(text=self._i18n.t("startup.add_btn"))
        self._startup_add_self_btn.configure(text=self._i18n.t("startup.add_self_btn"))
        self._startup_remove_self_btn.configure(text=self._i18n.t("startup.remove_self_btn"))
        self._startup_refresh_btn.configure(text=self._i18n.t("startup.refresh_btn"))
        self._update_source_filter_dropdown()
        self._appearance_label.configure(text=self._i18n.t("sidebar.appearance"))
        self._settings_label.configure(text=self._i18n.t("sidebar.settings"))
        self._tray_toggle_switch.configure(text=self._i18n.t("tray.minimize_to_tray"))
        self._update_mode_switch_texts()
        if hasattr(self, "_compact_plan_btn") and self._compact_plan_btn.winfo_exists():
            self._compact_plan_btn.configure(text=self._i18n.t("compact.switch_power"))
        if hasattr(self, "_compact_lang_btn") and self._compact_lang_btn.winfo_exists():
            self._compact_lang_btn.configure(text=self._i18n.t("compact.switch_language"))
        if hasattr(self, "_compact_style_btn") and self._compact_style_btn.winfo_exists():
            self._compact_style_btn.configure(text=self._i18n.t("compact.switch_style"))
        fit_option_width(self._style_dropdown, list(STYLES.keys()), min_width=100,
                         max_width=SIDEBAR_EXPANDED - 20)
        fit_option_width(self._lang_dropdown, [LANG_LABELS[l] for l in SUPPORTED_LANGS],
                         min_width=80, max_width=SIDEBAR_EXPANDED - 20)
        fit_entry_width(self._interval_entry, min_width=48, max_width=80, padding=28)
        fit_entry_width(self._startup_name_entry, min_width=90, max_width=160, padding=28)
        fit_entry_width(self._startup_path_entry, min_width=140, max_width=350, padding=32)
        fit_button_width(self._apply_interval_btn, min_width=50, max_width=80)
        fit_button_width(self._check_btn, min_width=80, max_width=160)
        fit_button_width(self._startup_refresh_btn, min_width=50, max_width=90)
        fit_button_width(self._startup_add_btn, min_width=54, max_width=100)
        fit_button_width(self._startup_add_self_btn, min_width=74, max_width=130)
        fit_button_width(self._startup_remove_self_btn, min_width=74, max_width=140)
        if hasattr(self, "_compact_plan_btn") and self._compact_plan_btn.winfo_exists():
            fit_button_width(self._compact_plan_btn, min_width=88, max_width=132, padding=14)
        if hasattr(self, "_compact_lang_btn") and self._compact_lang_btn.winfo_exists():
            fit_button_width(self._compact_lang_btn, min_width=88, max_width=132, padding=14)
        if hasattr(self, "_compact_style_btn") and self._compact_style_btn.winfo_exists():
            fit_button_width(self._compact_style_btn, min_width=88, max_width=132, padding=14)
        if hasattr(self, "_compact_full_btn") and self._compact_full_btn.winfo_exists():
            fit_button_width(self._compact_full_btn, min_width=58, max_width=88, padding=10)
        if self._sidebar_expanded:
            self._toggle_btn.configure(text="\u25c0")
        else:
            self._toggle_btn.configure(text="\u25b6")
        self._update_sort_indicator()
        self._refresh_power_status()
        self._refresh_startup_list()

    # ==================================================================
    # Column header layout & resize
    # ==================================================================

    def _on_header_configure(self, event):
        w = event.width
        if w > 10:
            self._header_width = w
            self._relayout_header()
            self._schedule_relayout_all_rows()
            self._fit_narrow_layout()

    def _fit_narrow_layout(self) -> None:
        """Shrink optional text when the window is too narrow for the full row.

        The measured width is the header's reported width (the same space the
        packed children use), so the thresholds below are in that space.
        """
        width = max(self._header_width, 50)
        compact = width < 1100

        full_count = f"{len(self._startup_items_cache)}{self._i18n.t('startup.entries')}"
        short_count = str(len(self._startup_items_cache))
        self._startup_count_label.configure(text=short_count if compact else full_count)

        dropdown = self._source_filter_dropdown
        if dropdown.winfo_exists():
            labels_list = list(dropdown.cget("values") or [])
            if labels_list:
                fit_option_width(dropdown, labels_list,
                                 min_width=64 if compact else 96,
                                 max_width=100 if compact else 180,
                                 padding=28)

        full_msg = getattr(self, "_check_result_full_text", "")
        if full_msg:
            limit = 110 if width >= 1200 else (60 if width >= 900 else 28)
            display = full_msg if len(full_msg) <= limit else full_msg[:limit - 1] + "…"
            if self._check_result_label.cget("text") != display:
                self._check_result_label.configure(text=display)

    def _relayout_header(self):
        w = max(self._header_width, 50)
        offset = 0.02
        col_w = self._col_widths
        gap = 0.005

        for i, key in enumerate(COL_KEYS):
            lbl, handle, _ = self._col_header_widgets.get(key, (None, None, None))
            x_frac = offset + sum(col_w[:i]) + gap * i
            if lbl:
                lbl.place(relx=x_frac, rely=0.5, anchor="w")

            if i < 3 and handle:
                hx = offset + sum(col_w[:i+1]) + gap * i + gap / 2
                handle.place(relx=hx - 0.004, rely=0.5, anchor="c",
                             relheight=0.65, relwidth=0.01)

    def _start_resize(self, col_idx: int, event):
        self._resizing_col = col_idx
        self._resize_start_x = event.x_root
        self._resize_start_w0 = self._col_widths[col_idx]
        self._resize_start_w1 = self._col_widths[col_idx + 1]

    def _do_resize(self, col_idx: int, event):
        if self._resizing_col < 0:
            return
        dx = (event.x_root - self._resize_start_x) / max(self._header_width, 50)
        new_w0 = max(COL_MIN, self._resize_start_w0 + dx)
        new_w1 = max(COL_MIN, self._resize_start_w1 - dx)
        self._col_widths[col_idx] = new_w0
        self._col_widths[col_idx + 1] = new_w1
        # During drag: only update header (cheap, just 8 widgets)
        self._relayout_header()

    def _end_resize(self):
        self._resizing_col = -1
        # Only relayout all rows once, when drag ends
        self._relayout_all_rows()
        self.cfg["col_widths"] = list(self._col_widths)
        self._persist_config()

    def _schedule_relayout_all_rows(self, delay_ms: int = 35):
        if self._relayout_rows_after_id is not None:
            try:
                self.after_cancel(self._relayout_rows_after_id)
            except Exception:
                pass
        self._relayout_rows_after_id = self.after(delay_ms, self._run_scheduled_relayout)

    def _run_scheduled_relayout(self):
        self._relayout_rows_after_id = None
        self._relayout_all_rows()

    def _relayout_all_rows(self):
        """Position every cell using the same fractions as the column header.

        Fractions are used deliberately: a row's own coordinate space is not the
        window's (CustomTkinter re-scales widget widths), so absolute pixels would
        drift out of alignment with the header.
        """
        offset = 0.02
        col_w = self._col_widths
        gap = 0.005

        starts = [offset + sum(col_w[:i]) + gap * i for i in range(len(COL_KEYS))]
        cell_w = [max(0.03, col_w[i] - 0.01) for i in range(3)]
        action_x = starts[3]

        for row in self._table_rows:
            if not row.winfo_exists():
                continue
            widgets = self._row_widgets.get(id(row))
            if not widgets:
                continue
            for key, x, w in (("name", starts[0], cell_w[0]),
                              ("path", starts[1], cell_w[1]),
                              ("source", starts[2], cell_w[2])):
                label = widgets[key]
                label.place(relx=x, rely=0.5, anchor="w", relwidth=w)
                full_text = getattr(label, "_pc_full_text", None)
                if full_text is None:
                    full_text = label.cget("text")
                    label._pc_full_text = full_text
                self._fit_text_fractional(label, full_text, w)
            # Both actions sit inside the action column, right-aligned, with the
            # same fractions the header uses for that column.
            widgets["open"].place(relx=0.885, rely=0.5, anchor="e", relwidth=0.075)
            widgets["remove"].place(relx=0.965, rely=0.5, anchor="e", relwidth=0.075)

    def _fit_text_fractional(self, label, text: str, fraction: float) -> None:
        """Trim text to fit ``fraction`` of the label's row, measured in pixels.

        One character is dropped at a time: cutting in blocks mangled short labels
        such as "当前用户 Run" into " Run".
        """
        row = label.master
        try:
            available = int(row.winfo_width() * fraction) - 8
        except Exception:
            available = 0
        if available <= 20:
            if label.cget("text") != text:
                label.configure(text=text)
            return
        try:
            measure = tkfont.Font(font=label.cget("font")).measure
        except Exception:
            measure = lambda value: len(value) * 7      # noqa: E731
        if measure(text) <= available:
            if label.cget("text") != text:
                label.configure(text=text)
            return
        # Keep the tail of the value (the file name matters most) behind an ellipsis.
        tail = text
        while tail and measure("…" + tail) > available:
            tail = tail[1:]
        trimmed = "…" + tail
        if trimmed == "…":
            # Nothing of the tail fits: fall back to a plain prefix.
            head = ""
            for index in range(1, len(text) + 1):
                if measure(text[:index] + "…") > available:
                    break
                head = text[:index]
            trimmed = head + "…" if head else text[:1]
        if label.cget("text") != trimmed:
            label.configure(text=trimmed)

    # ==================================================================
    # Startup list
    # ==================================================================

    @staticmethod
    def _make_item_key(item: StartupItem) -> str:
        """Row key. Folder items are keyed by path, so demo.cmd ≠ demo.lnk."""
        return item.identity()

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
        fit_option_width(self._source_filter_dropdown, labels, min_width=96, max_width=180, padding=28)

    def _on_source_filter_changed(self, label: str):
        self._startup_source_filter = self._source_filter_label_to_key.get(label, "all")
        if self._startup_items_cache:
            self._render_startup_items(self._sorted_startup_items())
        else:
            self._refresh_startup_list()

    def _row_fonts(self) -> Dict[str, ctk.CTkFont]:
        """One font set for every row — building fonts per row is very slow."""
        cached = getattr(self, "_row_font_cache", None)
        if cached is None:
            cached = {
                "name": ctk.CTkFont(size=12, weight="bold"),
                "path": ctk.CTkFont(size=11),
                "source": ctk.CTkFont(size=11),
                "button": ctk.CTkFont(size=11),
            }
            self._row_font_cache = cached
        return cached

    def _create_startup_row(self, item: StartupItem) -> ctk.CTkFrame:
        """Build one row: 4 labels + 2 buttons placed directly on the row frame.

        Keeping the widget count low matters: scroll and paint cost on Windows
        grows almost linearly with the number of widgets in the list.
        """
        s = self._style
        fonts = self._row_fonts()
        row = ctk.CTkFrame(self._startup_list_frame, height=ROW_HEIGHT, corner_radius=4)
        apply_surface_corner(row, s)
        row.pack(fill="x", pady=1)
        row.pack_propagate(False)
        self._reg(row, apply_surface_corner)

        # Hover glow — elevate bg, accent border, and reveal the full cell values.
        def on_enter(e):
            row.configure(fg_color=s.card_elevated, border_color=s.accent, border_width=1)
            self._show_cell_tip(row)

        def on_leave(e):
            row.configure(fg_color=s.surface, border_color=s.surface, border_width=0)
            self._hide_cell_tip()

        row.bind("<Enter>", on_enter)
        row.bind("<Leave>", on_leave)

        name_lbl = ctk.CTkLabel(row, text=item.name, font=fonts["name"],
                                text_color=s.text_primary, anchor="w")
        self._reg(name_lbl, lambda w, s: w.configure(text_color=s.text_primary))

        path_lbl = ctk.CTkLabel(row, text=item.path, font=fonts["path"],
                                text_color=s.text_secondary, anchor="w")
        self._reg(path_lbl, lambda w, s: w.configure(text_color=s.text_secondary))

        # Short source tag: the full description lives in the filter dropdown and
        # in the hover tooltip, so long labels are never squeezed.
        src_lbl = ctk.CTkLabel(row, text=self._source_tag(item.source), font=fonts["source"],
                               text_color=s.text_muted, anchor="w")
        self._reg(src_lbl, lambda w, s: w.configure(text_color=s.text_muted))

        open_btn = ctk.CTkButton(
            row, text=self._i18n.t("startup.location_btn"), width=38, height=18,
            font=fonts["button"],
            fg_color=s.card_elevated, hover_color=s.accent,
            text_color=s.text_secondary, corner_radius=3,
            command=lambda it=item: self._open_startup_location(it))
        self._reg(open_btn, lambda w, s: w.configure(
            fg_color=s.card_elevated, hover_color=s.accent,
            text_color=s.text_secondary))

        remove_btn = ctk.CTkButton(
            row, text=self._i18n.t("startup.remove_btn"), width=30, height=18,
            font=fonts["button"],
            fg_color=s.card_elevated, hover_color=s.error,
            text_color=s.text_secondary, corner_radius=3,
            command=lambda it=item: self._remove_startup_item(it))
        self._reg(remove_btn, lambda w, s: w.configure(
            fg_color=s.card_elevated, hover_color=s.error,
            text_color=s.text_secondary))

        self._row_widgets[id(row)] = {
            "name": name_lbl,
            "path": path_lbl,
            "source": src_lbl,
            "open": open_btn,
            "remove": remove_btn,
            "tip": (f"{item.name}\n{item.path}\n{self._format_startup_source(item.source)}",),
        }
        self._row_pool.append(row)
        return row

    def _style_startup_row(self, row, item: StartupItem) -> None:
        """Point an existing row at another item — far cheaper than rebuilding."""
        widgets = self._row_widgets.get(id(row))
        if not widgets:
            return
        widgets["name"].configure(text=item.name)
        widgets["path"].configure(text=item.path)
        widgets["source"].configure(text=self._source_tag(item.source))
        widgets["open"].configure(command=lambda it=item: self._open_startup_location(it))
        widgets["remove"].configure(command=lambda it=item: self._remove_startup_item(it))
        widgets["tip"] = (f"{item.name}\n{item.path}\n"
                          f"{self._format_startup_source(item.source)}",)

    def _show_cell_tip(self, row) -> None:
        """Tooltip with the full value of every cell of a row (needed when clipped).

        It also self-destructs after a few seconds and on every interaction that
        can move or cover the row, because a leave event alone would let the popup
        survive clicks and dialogs as a floating remnant.
        """
        tip = self._row_widgets.get(id(row), {}).get("tip", ("",))[0]
        if not tip:
            return
        try:
            x = row.winfo_rootx() + 12
            y = row.winfo_rooty() + row.winfo_height() + 2
        except Exception:
            return
        if getattr(self, "_cell_tip_row", None) == row and self._cell_tip_alive():
            return                       # already showing for this row
        self._hide_cell_tip()
        window = ctk.CTkToplevel(self)
        window.overrideredirect(True)
        window.configure(fg_color=self._style.card_elevated)
        label = ctk.CTkLabel(window, text=tip, font=ctk.CTkFont(size=11),
                             text_color=self._style.text_primary, justify="left",
                             anchor="w")
        label.pack(padx=8, pady=6)
        window.geometry(f"+{x}+{y}")
        self._cell_tip = window
        self._cell_tip_row = row
        self._cell_tip_job = self.after(TIP_TIMEOUT_MS, self._hide_cell_tip)

    def _cell_tip_alive(self) -> bool:
        window = getattr(self, "_cell_tip", None)
        try:
            return window is not None and bool(window.winfo_exists())
        except Exception:
            return False

    def _hide_cell_tip(self) -> None:
        job = getattr(self, "_cell_tip_job", None)
        if job is not None:
            self._cell_tip_job = None
            try:
                self.after_cancel(job)
            except Exception:
                pass
        window = getattr(self, "_cell_tip", None)
        self._cell_tip = None
        self._cell_tip_row = None
        if window is None:
            return
        try:
            window.destroy()
        except Exception:
            pass

    def _source_tag(self, source: str) -> str:
        """Short source label for the narrow table column."""
        tag = f"startup.tag_{source}"
        if self._i18n.has(tag):
            return self._i18n.t(tag)
        return self._format_startup_source(source)

    def _take_row(self, item: StartupItem, index: int) -> ctk.CTkFrame:
        if index < len(self._row_pool):
            row = self._row_pool[index]
            if row.winfo_exists():
                self._style_startup_row(row, item)
                return row
        return self._create_startup_row(item)

    @staticmethod
    def _short_path(path: str) -> str:
        text = str(path or "")
        return "..." + text[-57:] if len(text) > 60 else text


    def _sorted_startup_items(self, items: Optional[List[StartupItem]] = None) -> List[StartupItem]:
        source_labels = self._startup_source_labels()
        source_filter = self._startup_source_filter
        sort_key = self._sort_key
        reverse = not self._sort_ascending
        base_items = list(self._startup_items_cache if items is None else items)
        filtered = [
            item for item in base_items
            if source_filter == "all" or item.source == source_filter
        ]

        def _sort_value(item: StartupItem) -> str:
            if sort_key == "source":
                return source_labels.get(item.source, item.source)
            return getattr(item, sort_key, "")

        return sorted(
            filtered,
            key=lambda it: ''.join(lazy_pinyin(_sort_value(it), style=Style.TONE3)),
            reverse=reverse,
        )

    def _toggle_sort(self, key: str):
        """Toggle sort direction when clicking a sortable column header."""
        if key not in ("name", "source"):
            return
        if self._sort_key == key:
            self._sort_ascending = not self._sort_ascending
        else:
            self._sort_key = key
            self._sort_ascending = True
        self._update_sort_indicator()
        self.update_idletasks()
        if self._startup_items_cache:
            self._render_startup_items(self._sorted_startup_items())
        else:
            self._refresh_startup_list()

    def _bind_header_sort(self, label, key: str) -> None:
        """Make a whole header cell clickable.

        CustomTkinter draws a label inside an internal canvas and the canvas is
        the widget that receives the click, so the label alone would never fire.
        One physical click can therefore arrive through two bindings; the guard in
        _on_header_sort keeps it to a single toggle.
        """
        targets = [label]
        try:
            targets.extend(child for child in label.winfo_children()
                           if isinstance(child, Canvas))
        except Exception:
            pass
        for target in targets:
            try:
                target.configure(cursor="hand2")
            except Exception:
                pass
            target.bind("<Button-1>", lambda e, k=key: self._on_header_sort(k, e), add="+")

    def _on_header_sort(self, key: str, event=None) -> None:
        """Toggle the sort once per physical click, whatever widget delivered it."""
        stamp = getattr(event, "time", None) if event is not None else None
        if stamp is not None and stamp == getattr(self, "_last_sort_click", None):
            return
        if stamp is not None:
            self._last_sort_click = stamp
        self._toggle_sort(key)

    def _update_sort_indicator(self):
        """Update sortable column labels to show direction beside the text."""
        col_i18n = {"name": "startup.col_name", "path": "startup.col_path",
                     "source": "startup.col_source", "action": "startup.col_action"}
        for key, (lbl, _, _) in self._col_header_widgets.items():
            base = self._i18n.t(col_i18n.get(key, key))
            if key == self._sort_key:
                arrow = " " + (chr(0x2191) if self._sort_ascending else chr(0x2193))
                lbl.configure(text=base + arrow)
            else:
                lbl.configure(text=base)

    def _refresh_startup_list(self, force: bool = False, requery: bool = False):
        """Re-render the list and, when needed, re-query the OS in the background.

        ``force=True`` always re-queries; ``requery=True`` schedules a re-query
        after an immediate re-render from the local cache.

        Queries are never discarded as "stale": the latest snapshot always wins,
        and a request that arrives while a query runs simply queues another one.
        """
        if self._closing:
            return
        if requery or force:
            self._startup_requery_pending = True
        if not force and self._startup_items_cache:
            self._render_startup_items(self._sorted_startup_items())
            if not self._startup_requery_pending:
                return

        if self._startup_refresh_running:
            self._startup_refresh_pending = True
            return

        self._startup_refresh_running = True
        self._startup_requery_pending = False
        source_labels = self._startup_source_labels()

        def _load():
            error = ""
            try:
                all_items = get_all_items()
                sources = sorted({item.source for item in all_items},
                                 key=lambda key: source_labels.get(key, key))
            except Exception as exc:
                log.exception("startup item query failed")
                all_items = []
                sources = []
                error = str(exc)
            self._post_ui(lambda: self._finish_startup_refresh(all_items, sources, error))

        threading.Thread(target=_load, name="StartupQuery", daemon=True).start()

    def _finish_startup_refresh(self, items: List[StartupItem], sources=None, error: str = ""):
        self._startup_refresh_running = False
        if self._closing or not self.winfo_exists():
            return
        if self._startup_refresh_pending:
            self._startup_refresh_pending = False
            self._refresh_startup_list(force=True)
            return
        if error:
            # A failed read must never be presented as "no startup items".
            self._report(self._i18n.t("startup.read_failed", reason=error), "error",
                         persistent=True)
        self._update_source_filter_dropdown(sources)
        self._startup_items_cache = list(items)
        self._startup_sources_cache = list(sources or [])
        self._render_startup_items(self._sorted_startup_items(items))

    def _render_startup_items(self, items: List[StartupItem]):
        """Re-render the list, reusing existing row widgets where possible.

        Rebuilding ~8 widgets per row costs seconds on a long list, so the rows
        are pooled and only hidden/destroyed when the list actually shrinks.
        """
        self._hide_cell_tip()          # rows are about to move or disappear
        self._table_rows.clear()
        self._item_rows.clear()

        for index, item in enumerate(items):
            row = self._take_row(item, index)
            row.pack(fill="x", pady=1)
            self._table_rows.append(row)
            self._item_rows[self._make_item_key(item)] = row

        # Drop rows that are no longer needed and drop the empty-state label.
        self._row_pool = self._row_pool[:len(self._table_rows)]
        for widget in self._startup_list_frame.winfo_children():
            if widget not in self._table_rows:
                self._row_widgets.pop(id(widget), None)
                widget.destroy()
        self._stylables = [(w, fn) for w, fn in self._stylables if w.winfo_exists()]

        self._startup_count_label.configure(
            text=f"{len(items)}{self._i18n.t('startup.entries')}")

        if not items:
            empty = ctk.CTkLabel(self._startup_list_frame,
                                 text=self._i18n.t("startup.empty"),
                                 font=self._row_fonts()["name"], text_color=self._style.text_muted)
            empty.pack(pady=16)
            self._update_self_buttons(items)
            self._update_restore_button()
            return

        self._relayout_all_rows()
        self._update_self_buttons(items)
        self._update_restore_button()

    # ==================================================================
    # Startup CRUD
    # ==================================================================

    def _browse_startup_path(self):
        path = filedialog.askopenfilename(title=self._i18n.t("startup.title"))
        if path:
            self._startup_path_entry.delete(0, "end")
            self._startup_path_entry.insert(0, path)
            fit_entry_width(self._startup_path_entry, min_width=180,
                            max_width=430, padding=38)

    def _add_startup_item(self):
        name = self._startup_name_entry.get().strip()
        path = self._startup_path_entry.get().strip()
        if not name or not path:
            self._show_check_result(self._i18n.t("startup.fill_both"), "warning")
            return

        status = add_registry_startup(name, path)
        if status == "exists":
            # Same-name overwrite is destructive: ask first, showing the old command.
            existing = next((item.path for item in self._startup_items_cache
                             if item.source == "registry" and item.name == name), "")
            if not self._ask_confirm(
                    self._i18n.t("dialog.overwrite_title"),
                    self._i18n.t("dialog.overwrite_body", name=name, old=existing, new=path),
                    danger=False):
                self._show_check_result(self._i18n.t("startup.overwrite_cancelled", name=name),
                                        "info")
                return
            status = add_registry_startup(name, path, overwrite=True)

        if status in ("created", "overwritten"):
            self._startup_name_entry.delete(0, "end")
            self._startup_path_entry.delete(0, "end")
            key = f"registry:{name}"
            self._startup_items_cache = [
                cached for cached in self._startup_items_cache
                if cached.identity() != key
            ]
            self._startup_items_cache.append(StartupItem(name=name, path=path, source="registry"))
            if "registry" not in self._startup_sources_cache:
                self._startup_sources_cache.append("registry")
            self._refresh_startup_list()
            message = (self._i18n.t("startup.overwritten", name=name) if status == "overwritten"
                       else self._i18n.t("startup.added", name=name))
            self._show_check_result(message, "ok")
        elif status == "invalid":
            self._show_check_result(self._i18n.t("startup.fill_both"), "warning")
        else:
            self._show_check_result(self._i18n.t("startup.add_failed"), "error")

    def _self_item(self, command: str) -> StartupItem:
        return StartupItem(name=SELF_STARTUP_NAME, path=command, source="registry")

    def _add_self_to_startup(self):
        try:
            command = build_self_startup_command()
        except ValueError:
            self._show_check_result(self._i18n.t("startup.add_failed"), "error")
            return

        status = add_registry_startup(SELF_STARTUP_NAME, command)
        if status == "exists":
            existing = next((item.path for item in self._startup_items_cache
                             if item.source == "registry" and item.name == SELF_STARTUP_NAME), "")
            if not self._ask_confirm(
                    self._i18n.t("dialog.overwrite_title"),
                    self._i18n.t("dialog.overwrite_body", name=SELF_STARTUP_NAME,
                                 old=existing, new=command),
                    danger=False):
                self._show_check_result(
                    self._i18n.t("startup.overwrite_cancelled", name=SELF_STARTUP_NAME), "info")
                return
            status = add_registry_startup(SELF_STARTUP_NAME, command, overwrite=True)

        if status in ("created", "overwritten"):
            key = f"registry:{SELF_STARTUP_NAME}"
            self._startup_items_cache = [
                cached for cached in self._startup_items_cache
                if cached.identity() != key
            ]
            self._startup_items_cache.append(self._self_item(command))
            if "registry" not in self._startup_sources_cache:
                self._startup_sources_cache.append("registry")
            self._refresh_startup_list()
            self._update_self_buttons()
            self._show_check_result(self._i18n.t("startup.added_self"), "ok")
        else:
            self._show_check_result(self._i18n.t("startup.add_failed"), "error")

    def _remove_self_from_startup(self):
        item = self._self_item(get_app_exe_path())
        if not self._ask_confirm(
                self._i18n.t("dialog.remove_title"),
                self._i18n.t("dialog.remove_body", name=SELF_STARTUP_NAME,
                             source=self._format_startup_source("registry"),
                             target=self._command_text_for(item)),
                danger=True):
            return
        backup = backup_item(item, DATA_DIR)
        if remove_registry_startup(SELF_STARTUP_NAME):
            key = f"registry:{SELF_STARTUP_NAME}"
            self._startup_items_cache = [
                cached for cached in self._startup_items_cache
                if cached.identity() != key
            ]
            self._refresh_startup_list()
            self._update_self_buttons()
            message = self._i18n.t("startup.remove_self")
            if backup is None:
                message += " " + self._i18n.t("startup.no_backup")
            else:
                message += " " + self._i18n.t("startup.backup_ready")
            self._show_check_result(message, "ok")
        else:
            self._show_check_result(
                self._i18n.t("startup.remove_failed", name=SELF_STARTUP_NAME), "error")

    def _has_self_in_startup(self, items=None) -> bool:
        items = items if items is not None else self._startup_items_cache
        return any(
            item.name == SELF_STARTUP_NAME and item.source == "registry"
            for item in items
        )

    def _update_self_buttons(self, items=None):
        # Pinned to the right edge of the add row so the row stays balanced
        # instead of leaving a gap in the middle.
        if self._has_self_in_startup(items):
            self._startup_add_self_btn.pack_forget()
            self._startup_remove_self_btn.pack(side="right", padx=(6, 0), pady=6)
        else:
            self._startup_remove_self_btn.pack_forget()
            self._startup_add_self_btn.pack(side="right", padx=(6, 0), pady=6)

    def _open_startup_location(self, item: StartupItem):
        if open_startup_location(item):
            self._show_check_result(self._i18n.t("startup.opened_location", name=item.name), "ok")
        else:
            self._show_check_result(self._i18n.t("startup.open_location_failed", name=item.name), "error")

    def _command_text_for(self, item: StartupItem) -> str:
        if item.kind == "folder":
            return item.path
        return item.path

    def _remove_startup_item(self, item: StartupItem):
        if not is_known_source(item.source):
            self._show_check_result(self._i18n.t("startup.unknown_source", source=item.source),
                                    "error")
            return
        if not self._ask_confirm(
                self._i18n.t("dialog.remove_title"),
                self._i18n.t("dialog.remove_body", name=item.name,
                             source=self._format_startup_source(item.source),
                             target=self._command_text_for(item)),
                danger=True):
            return

        backup = backup_item(item, DATA_DIR)
        ok = remove_item(item)
        if ok:
            key = item.identity()
            self._startup_items_cache = [
                cached for cached in self._startup_items_cache
                if cached.identity() != key
            ]
            self._refresh_startup_list()
            self._update_self_buttons()
            message = self._i18n.t("startup.removed", name=item.name)
            message += (" " + self._i18n.t("startup.backup_ready") if backup is not None
                        else " " + self._i18n.t("startup.no_backup"))
            self._show_check_result(message, "ok")
        else:
            self._show_check_result(
                self._i18n.t("startup.remove_failed", name=item.name), "error")

    def _restore_last_removed(self):
        try:
            pending = list_backups(DATA_DIR)
        except Exception:
            log.exception("could not read startup backup ledger")
            pending = []
        if not pending:
            self._show_check_result(self._i18n.t("startup.nothing_to_restore"), "info")
            return
        entry = pending[0]
        label = entry.get("name", "")
        if not self._ask_confirm(
                self._i18n.t("dialog.restore_title"),
                self._i18n.t("dialog.restore_body", name=label,
                             target=entry.get("path", "")),
                danger=False):
            return
        if restore_last_backup(DATA_DIR) is None:
            self._show_check_result(self._i18n.t("startup.restore_failed", name=label), "error")
            return
        self._refresh_startup_list(force=True)
        self._show_check_result(self._i18n.t("startup.restored", name=label), "ok")

    def _update_restore_button(self):
        button = getattr(self, "_startup_restore_btn", None)
        if button is None or not button.winfo_exists():
            return
        try:
            pending = list_backups(DATA_DIR)
        except Exception:
            pending = []
        if pending:
            button.configure(state="normal",
                             text=self._i18n.t("startup.restore_btn",
                                               count=len(pending)))
            self._startup_restore_btn.pack(side="right", padx=(0, 6))
        else:
            button.pack_forget()

    # ==================================================================
    # Dialogs
    # ==================================================================

    def _modal(self, title: str, width: int = 460, height: int = 250):
        """Create a themed modal Toplevel centred on the main window."""
        self._hide_cell_tip()      # a leftover tooltip would float over the dialog
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

        buttons = ctk.CTkFrame(window, fg_color="transparent")
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
        if self._closing or not self.winfo_exists():
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

        enable_var = ctk.BooleanVar(value=bool(self.cfg.get("monitor_enabled", False)))

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

        choices = ctk.CTkFrame(window, fg_color="transparent")
        choices.pack(fill="x", padx=16, pady=(0, 4))

        enable_switch = ctk.CTkSwitch(
            choices, text=self._i18n.t("setup.enable_monitor"),
            variable=enable_var, font=ctk.CTkFont(size=12))
        apply_switch(enable_switch, s)
        enable_switch.pack(side="left")

        def _choose_auto():
            self._close_modal(window, "auto")

        def _choose_plan():
            if not [plan for plan in state["plans"] if plan.is_acceptable]:
                self._report(self._i18n.t("setup.no_performance_plan"), "warning")
                return
            self._close_modal(window, "pick")

        buttons = ctk.CTkFrame(window, fg_color="transparent")
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
            plan = find_high_performance_plan()
            _finish(bool(enable_var.get()), plan.guid if plan else "")
            self._show_check_result(self._i18n.t("setup.done"), "ok")
        elif result == "pick":
            self._pick_target_from_setup(enable_var.get())
        elif result == "skip":
            # Keep the current settings untouched and remember we asked.
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
        _make_dropdown_toggle(dropdown)

        def _confirm():
            self._close_modal(window, dropdown.get())

        buttons = ctk.CTkFrame(window, fg_color="transparent")
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
        if self._creating_schemes:
            return
        self._creating_schemes = True
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
        self._update_power_ui(active, plans, snapshot)
        self._refresh_target_dropdown_items(plans)
        if self._power_refresh_pending:
            self._power_refresh_pending = False
            self._refresh_power_status()

    def _refresh_target_dropdown_items(self, plans=None):
        if plans is None:
            plans = self._power_plans_cache
        if not plans:
            return
        values = self._build_target_labels(plans)
        target_guid = self.power_monitor.target_guid or ""
        if target_guid and target_guid not in self._label_by_guid:
            # Keep the dead target visible so the user must re-choose.
            values.append(self._missing_target_label(target_guid))
        fit_option_width(self._target_dropdown, values, min_width=110, max_width=300)
        self._target_dropdown.configure(values=values)
        self._set_target_selection(plans)

    def _update_power_ui(self, active, plans=None, snapshot=None):
        s = self._style
        if snapshot is not None and snapshot.error:
            self._set_status_badge(
                self._i18n.t("power.read_failed", reason=self._power_error_text(snapshot)),
                s.error, pulse=True)
            self._power_active_value.configure(text=self._i18n.t("power.unable_detect"))
            self._report(self._i18n.t("power.read_failed",
                                      reason=self._power_error_text(snapshot)), "error",
                         persistent=True)
            self._sync_compact_active(self._i18n.t("power.unable_detect"))
            self._rebuild_tray_menu(plans or [])
            return

        if active is None:
            self._power_active_value.configure(text=self._i18n.t("power.unable_detect"))
            self._set_status_badge(self._i18n.t("power.status_error"), s.error, pulse=True)
            self._sync_compact_active(self._i18n.t("power.unable_detect"))
            return
        self._power_active_value.configure(text=active.name)

        target_guid = self.power_monitor.target_guid
        missing_target = bool(target_guid) and find_plan_by_guid(target_guid, plans or []) is None
        ok = is_plan_matching_target(active, target_guid, plans or [])

        if missing_target:
            self._set_status_badge(self._i18n.t("power.status_target_missing").format(
                guid=target_guid[:8].upper()), s.error, pulse=True)
            self._report(self._i18n.t("power.target_missing", guid=target_guid[:8].upper()),
                         "error", persistent=True)
        elif ok:
            self._set_status_badge(self._i18n.t("power.status_ok"), s.success)
        else:
            self._set_status_badge(self._i18n.t("power.status_needs_fix"), s.warning)
        self._rebuild_tray_menu(plans)
        self._sync_compact_active(active.name)

    def _set_status_badge(self, text: str, color: str, pulse: bool = False):
        prev = self._power_status_badge.cget("text")
        self._power_status_badge.configure(text=text, text_color=color)
        if pulse and prev and prev != text:
            self._status_pulse(self._power_status_badge, color)

    def _sync_compact_active(self, text: str):
        compact = getattr(self, "_compact_active_value", None)
        if self._compact_mode and compact is not None and compact.winfo_exists():
            compact.configure(text=text)

    def _power_error_text(self, snapshot) -> str:
        key = f"power.error_{snapshot.error}"
        return self._i18n.t(key) if self._i18n.has(key) else self._i18n.t("power.error_other")

    def _do_switch_plan(self, guid: str):
        """Switch to the given power plan guid."""
        def _switch():
            try:
                success = set_active_plan(guid)
            except Exception:
                log.exception("power plan switch failed")
                success = False
            if self._closing:
                return
            if success:
                self._post_ui(self._refresh_power_status)
            else:
                plan = find_plan_by_guid(guid, self._power_plans_cache)
                name = plan.name if plan else guid[:8].upper()
                self._post_ui(lambda: self._report(
                    self._i18n.t("power.switch_failed", name=name), "error", persistent=True))
        threading.Thread(target=_switch, name="PowerSwitch", daemon=True).start()

    def _check_now(self):
        self._show_check_result(self._i18n.t("power.status_checking"), "info", persistent=True)

        def _do():
            result = self.power_monitor.check_now()
            if self._closing:
                return
            self._post_ui(lambda: self._recover_unusable_target(result))
            self._post_ui(lambda: self._refresh_power_status())
            self._post_ui(lambda: self._show_result_persistent(result))
        threading.Thread(target=_do, name="PowerCheckNow", daemon=True).start()

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
        self._post_ui(lambda: self._handle_power_status(result))

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
        self._post_ui(lambda: self._apply_monitor_state(running))

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
        s = self._style
        colors = {
            "ok": s.success,
            "error": s.error,
            "warning": s.warning,
            "info": s.info,
        }
        # The label shares the title row, so very long text is trimmed rather than
        # allowed to push the row (and the whole card) taller.
        self._check_result_full_text = msg
        width = max(getattr(self, "_header_width", 0), 0)
        limit = 110 if width >= 1200 else (60 if width >= 900 else 28)
        display = msg if len(msg) <= limit else msg[:limit - 1] + "…"
        self._check_result_label.configure(
            text=display, text_color=colors.get(kind, self._style.text_secondary))
        compact_result = getattr(self, "_compact_result_label", None)
        if compact_result is not None and compact_result.winfo_exists():
            compact_msg = msg if len(msg) <= 90 else msg[:87] + "..."
            compact_result.configure(
                text=compact_msg, text_color=colors.get(kind, self._style.text_secondary))
        if not persistent:
            # Transient messages only: the strip is packed while it has text and
            # removed again so the list keeps the full height.
            self._status_bar.configure(
                text=msg, text_color=colors.get(kind, self._style.text_secondary))
            self._show_status_bar()
            self.after(8000, self._clear_status_bar)

    def _show_status_bar(self):
        if not getattr(self, "_status_bar_visible", False):
            self._status_bar.pack(side="bottom", fill="x", padx=10, pady=(0, 2))
            self._status_bar_visible = True

    def _clear_status_bar(self):
        if self._closing or not self._status_bar.winfo_exists():
            return
        self._status_bar.configure(text="")
        if getattr(self, "_status_bar_visible", False):
            self._status_bar.pack_forget()
            self._status_bar_visible = False

    def _apply_interval(self):
        # Always read from the main interval entry
        entry = self._interval_entry
        try:
            secs = int(entry.get().strip())
            secs = max(10, min(3600, secs))
            entry.delete(0, "end")
            entry.insert(0, str(secs))
            self.power_monitor.interval = secs
            self.cfg["check_interval"] = secs
            self._persist_config()
            # Sync compact entry if it exists
            other = getattr(self, '_compact_interval_entry', None)
            if other is not None and other.winfo_exists():
                other.delete(0, "end")
                other.insert(0, str(secs))
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
        if save:
            self.cfg["monitor_enabled"] = True
        if not self.power_monitor.running:
            try:
                self.power_monitor.start()
            except Exception:
                log.exception("could not start power monitor")
                self._report(self._i18n.t("power.monitor_start_failed"), "error", persistent=True)
                return
            self._monitor_running = True
            if save:
                self._report(self._i18n.t("power.monitor_started"), "ok")
        if save:
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

    def _setup_tray(self):
        if self._tray_icon is not None:
            return True
        try:
            import pystray
            icon_img = _create_tray_icon(self._style.accent)

            def on_show(icon, item):
                self._window_visible = True
                self._post_ui(self.deiconify)

            def on_exit(icon, item):
                self._closing = True
                icon.stop()
                self._post_ui(self._force_quit)

            self._tray_icon = pystray.Icon(
                "pc_auto_scripts", icon_img, "PC System Auto Scripts")
            self._tray_thread = threading.Thread(target=self._tray_icon.run, daemon=True)
            self._tray_thread.start()

            self._rebuild_tray_menu()
            return True
        except Exception:
            log.exception("tray setup failed")
            return False

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
        if self._tray_icon is None:
            return
        try:
            import pystray

            def on_show(icon, item):
                self._window_visible = True
                self._post_ui(self.deiconify)

            def on_exit(icon, item):
                self._closing = True
                icon.stop()
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

            menu = pystray.Menu(
                pystray.MenuItem(self._i18n.t("tray.show"), on_show, default=True),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem(self._i18n.t("power.title"),
                                 pystray.Menu(*plan_items)),
                pystray.MenuItem(self._i18n.t("tray.check"),
                                 lambda: self._post_ui(self._check_now)),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem(self._i18n.t("tray.exit"), on_exit),
            )
            self._tray_icon.menu = menu
            self._tray_icon.update_menu()
        except Exception:
            log.exception("tray menu rebuild failed")

    def _update_tray_icon_color(self):
        if self._tray_icon is not None:
            try:
                self._tray_icon.icon = _create_tray_icon(self._style.accent)
            except Exception:
                log.debug("tray icon color update failed")

    # ==================================================================
    # Window lifecycle
    # ==================================================================

    def _on_close(self):
        self._hide_cell_tip()
        if self.cfg.get("minimize_to_tray", True) and self._setup_tray():
            self._window_visible = False
            self.withdraw()
        else:
            self._force_quit()

    def _force_quit(self):
        self._running = False
        self._closing = True
        self._hide_cell_tip()
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
        self._refresh_power_status()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
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
