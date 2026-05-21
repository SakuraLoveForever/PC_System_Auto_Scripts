"""
PC System Auto Scripts — Power Plan Monitor & Startup Manager
Dark-mode desktop app with 5 switchable design styles, EN/ZH i18n.
"""

from __future__ import annotations

import json
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional

import customtkinter as ctk
from PIL import Image, ImageDraw
from tkinter import filedialog

from styles import STYLES, DEFAULT_STYLE, DesignStyle
from power_manager import (
    PowerMonitor,
    get_active_plan,
    get_all_plans,
    is_acceptable_plan,
)
from startup_manager import (
    get_all_items,
    add_registry_startup,
    remove_registry_startup,
    remove_startup_folder_item,
    get_app_exe_path,
    StartupItem,
)
from i18n import I18n, SUPPORTED_LANGS, LANG_LABELS, DEFAULT_LANG

# ---------------------------------------------------------------------------
# Paths & config
# ---------------------------------------------------------------------------
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "config.json"
ICON_FILE = BASE_DIR / "app_icon.ico"


def load_config() -> dict:
    defaults = {
        "style": DEFAULT_STYLE,
        "language": DEFAULT_LANG,
        "monitor_enabled": True,
        "check_interval": 60,
        "target_guid": "",
        "minimize_to_tray": True,
        "col_widths": [0.25, 0.42, 0.15, 0.18],
    }
    if CONFIG_FILE.exists():
        try:
            defaults.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            pass
    return defaults


def save_config(cfg: dict):
    try:
        CONFIG_FILE.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Single-instance enforcement (socket-based IPC)
# ---------------------------------------------------------------------------
_SINGLE_INSTANCE_PORT = 53942


def _is_already_running() -> bool:
    """Check if another instance is already running."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.5)
        s.connect(("127.0.0.1", _SINGLE_INSTANCE_PORT))
        s.sendall(b"show")
        s.close()
        return True
    except (OSError, ConnectionRefusedError, TimeoutError):
        return False


def _start_ipc_server(app):
    """Listen for 'show' messages from other instance attempts."""
    def _serve():
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", _SINGLE_INSTANCE_PORT))
            s.listen(1)
            s.settimeout(2.0)
        except OSError:
            return
        while getattr(app, "_running", True):
            try:
                conn, _ = s.accept()
                data = conn.recv(1024)
                if data == b"show":
                    app.after(0, app._restore_from_ipc)
                conn.close()
            except socket.timeout:
                continue
            except OSError:
                break
        s.close()
    threading.Thread(target=_serve, daemon=True).start()


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
SIDEBAR_EXPANDED = 210
SIDEBAR_COLLAPSED = 60
COL_KEYS = ["name", "path", "source", "action"]
COL_DEFAULTS = [0.25, 0.42, 0.15, 0.18]
COL_MIN = 0.04
HANDLE_WIDTH = 4
ROW_HEIGHT = 26


# ======================================================================
# Style helper — apply a DesignStyle to a widget
# ======================================================================

def apply_btn_style(btn: ctk.CTkButton, s: DesignStyle):
    """Style a CTkButton as a primary accent button."""
    btn.configure(fg_color=s.accent, hover_color=s.accent_hover,
                  text_color=s.accent_text,
                  corner_radius=min(s.button_radius, 12))


def apply_btn_secondary(btn: ctk.CTkButton, s: DesignStyle):
    """Style a CTkButton as a secondary/surface button."""
    btn.configure(fg_color=s.card_elevated, hover_color=s.border,
                  text_color=s.text_primary,
                  corner_radius=min(s.button_radius, 12))


def apply_dropdown(dd: ctk.CTkOptionMenu, s: DesignStyle):
    """Style a CTkOptionMenu."""
    dd.configure(fg_color=s.card, text_color=s.text_primary,
                 button_color=s.accent, button_hover_color=s.accent_hover,
                 corner_radius=6,
                 dropdown_fg_color=s.card, dropdown_text_color=s.text_primary,
                 dropdown_hover_color=s.card_elevated)


def apply_entry(entry: ctk.CTkEntry, s: DesignStyle):
    """Style a CTkEntry."""
    entry.configure(fg_color=s.surface, text_color=s.text_primary,
                    placeholder_text_color=s.text_muted,
                    border_color=s.border,
                    corner_radius=min(s.input_radius, 8))


def apply_switch(sw: ctk.CTkSwitch, s: DesignStyle):
    """Style a CTkSwitch."""
    sw.configure(progress_color=s.accent, button_color=s.text_primary,
                  text_color=s.text_primary)


def apply_card(card: ctk.CTkFrame, s: DesignStyle):
    """Style a card frame."""
    card.configure(fg_color=s.card, corner_radius=s.card_radius)


def apply_surface(sf: ctk.CTkFrame, s: DesignStyle):
    """Style a surface frame."""
    sf.configure(fg_color=s.surface)


def apply_surface_corner(sf: ctk.CTkFrame, s: DesignStyle):
    """Style a surface frame with corner radius."""
    sf.configure(fg_color=s.surface, corner_radius=4)


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
        self._style: DesignStyle = STYLES[self.cfg["style"]]
        self._i18n = I18n(self.cfg.get("language", DEFAULT_LANG))
        self._sidebar_expanded = True
        self._monitor_running = False
        self._window_visible = True
        self._tray_icon = None
        self._tray_thread = None
        self._closing = False
        self._running = True  # for IPC server loop

        # Column widths (fractions, sum ~1.0)
        self._col_widths = list(self.cfg.get("col_widths", COL_DEFAULTS))
        if len(self._col_widths) != 4:
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

        self.power_monitor = PowerMonitor(
            on_status_change=self._on_power_status_change,
            interval_seconds=self.cfg["check_interval"],
        )
        if self.cfg.get("target_guid"):
            self.power_monitor.target_guid = self.cfg["target_guid"]

        self.title(self._i18n.t("app.title"))
        self.geometry("820x580")
        self.minsize(520, 420)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # Do NOT call set_default_color_theme — we manage colors manually
        ctk.set_appearance_mode("dark")
        self.configure(fg_color=self._style.canvas)

        self._build_sidebar()
        self._build_main_content()

        self.bind("<Configure>", self._on_window_configure)

        if self.cfg["monitor_enabled"]:
            self._start_monitor()

        self.after(400, self._refresh_power_status)

    # ==================================================================
    # Style switching
    # ==================================================================

    def _switch_style(self, name: str):
        if name not in STYLES:
            return
        self._style = STYLES[name]
        self.cfg["style"] = name
        save_config(self.cfg)

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
        self._brand_frame.pack(fill="x", padx=14, pady=(16, 4))
        self._brand_icon = ctk.CTkLabel(self._brand_frame, text="⚡", font=ctk.CTkFont(size=22),
                                        text_color=s.accent, anchor="w")
        self._brand_icon.pack(side="left")
        self._reg(self._brand_icon, lambda w, s: w.configure(text_color=s.accent))
        self._brand_text = ctk.CTkLabel(self._brand_frame, text="PC Auto",
                                        font=ctk.CTkFont(size=17, weight="bold"),
                                        text_color=s.text_primary, anchor="w")
        self._brand_text.pack(side="left", padx=(8, 0))
        self._reg(self._brand_text, lambda w, s: w.configure(text_color=s.text_primary))

        # Separator
        self._sep1 = ctk.CTkFrame(self._sidebar, height=1)
        self._sep1.configure(fg_color=s.border)
        self._sep1.pack(fill="x", padx=12, pady=(10, 6))
        self._reg(self._sep1, lambda w, s: w.configure(fg_color=s.border))

        # Style dropdown
        self._style_dropdown = ctk.CTkOptionMenu(
            self._sidebar, values=list(STYLES.keys()), font=ctk.CTkFont(size=15),
            command=self._switch_style)
        apply_dropdown(self._style_dropdown, s)
        _make_dropdown_toggle(self._style_dropdown)
        self._style_dropdown.pack(fill="x", padx=12, pady=(8, 8))
        self._style_dropdown.set(self.cfg["style"])
        self._reg(self._style_dropdown, apply_dropdown)

        # Language dropdown (expanded mode)
        self._lang_dropdown = ctk.CTkOptionMenu(
            self._sidebar, values=[LANG_LABELS[l] for l in SUPPORTED_LANGS],
            font=ctk.CTkFont(size=15), command=self._on_lang_changed)
        apply_dropdown(self._lang_dropdown, s)
        _make_dropdown_toggle(self._lang_dropdown)
        self._lang_dropdown.pack(fill="x", padx=12, pady=(2, 8))
        self._lang_dropdown.set(LANG_LABELS.get(self._i18n.lang, "中文"))
        self._reg(self._lang_dropdown, apply_dropdown)

        # Icon buttons for collapsed mode (hidden initially)
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

        # Separator
        self._sep2 = ctk.CTkFrame(self._sidebar, height=1)
        self._sep2.configure(fg_color=s.border)
        self._sep2.pack(fill="x", padx=12, pady=(10, 6))
        self._reg(self._sep2, lambda w, s: w.configure(fg_color=s.border))

        # Close behavior toggle
        self._tray_toggle_switch = ctk.CTkSwitch(
            self._sidebar, text=self._i18n.t("tray.minimize_to_tray"),
            font=ctk.CTkFont(size=14), text_color=s.text_secondary,
            command=self._toggle_tray_behavior)
        apply_switch(self._tray_toggle_switch, s)
        self._tray_toggle_switch.pack(padx=12, pady=(2, 4), anchor="w")
        if self.cfg.get("minimize_to_tray", True):
            self._tray_toggle_switch.select()
        self._reg(self._tray_toggle_switch, apply_switch)

        # Separator before toggle
        self._sep3 = ctk.CTkFrame(self._sidebar, height=1)
        self._sep3.configure(fg_color=s.border)
        self._sep3.pack(side="bottom", fill="x", padx=12, pady=(10, 6))
        self._reg(self._sep3, lambda w, s: w.configure(fg_color=s.border))

        # Toggle button at bottom
        self._toggle_btn = ctk.CTkButton(
            self._sidebar, text="◀  " + self._i18n.t("sidebar.collapse"),
            height=30, font=ctk.CTkFont(size=15),
            command=self._toggle_sidebar)
        apply_btn_secondary(self._toggle_btn, s)
        self._toggle_btn.pack(side="bottom", fill="x", padx=10, pady=(2, 4))
        self._reg(self._toggle_btn, apply_btn_secondary)

        # Version
        self._sidebar_version = ctk.CTkLabel(
            self._sidebar, text="v1.0.0", font=ctk.CTkFont(size=13), text_color=s.text_muted)
        self._sidebar_version.pack(side="bottom", pady=(0, 10))
        self._reg(self._sidebar_version, lambda w, s: w.configure(text_color=s.text_muted))

    def _toggle_tray_behavior(self):
        self.cfg["minimize_to_tray"] = bool(self._tray_toggle_switch.get())
        save_config(self.cfg)

    def _toggle_sidebar(self):
        if self._sidebar_expanded:
            # Collapse: swap dropdowns → icon buttons
            self._sidebar.configure(width=SIDEBAR_COLLAPSED)
            self._toggle_btn.configure(text="▶")
            for w in [self._brand_frame, self._sep1, self._sep2,
                       self._tray_toggle_switch, self._sep3, self._sidebar_version]:
                if w: w.pack_forget()
            self._style_dropdown.pack_forget()
            self._style_icon_btn.pack(fill="x", padx=6, pady=(8, 8))
            self._lang_dropdown.pack_forget()
            self._lang_icon_btn.pack(fill="x", padx=6, pady=(2, 8))
        else:
            # Expand: restore dropdowns, hide icon buttons
            self._sidebar.configure(width=SIDEBAR_EXPANDED)
            self._toggle_btn.configure(text="◀  " + self._i18n.t("sidebar.collapse"))
            self._style_icon_btn.pack_forget()
            self._lang_icon_btn.pack_forget()
            self._brand_frame.pack(fill="x", padx=14, pady=(16, 4))
            self._sep1.pack(fill="x", padx=12, pady=(10, 6))
            self._style_dropdown.pack(fill="x", padx=12, pady=(8, 8))
            self._lang_dropdown.pack(fill="x", padx=12, pady=(2, 8))
            self._sep2.pack(fill="x", padx=12, pady=(10, 6))
            self._tray_toggle_switch.pack(padx=12, pady=(2, 4), anchor="w")
            self._sep3.pack(side="bottom", fill="x", padx=12, pady=(10, 6))
            self._sidebar_version.pack(side="bottom", pady=(0, 10))
        self._sidebar_expanded = not self._sidebar_expanded

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
        save_config(self.cfg)
        self._refresh_all_text()

    def _refresh_all_text(self):
        self.title(self._i18n.t("app.title"))
        self._power_title_label.configure(text="⚡ " + self._i18n.t("power.title"))
        self._power_active_label.configure(text=self._i18n.t("power.active_plan") + ":")
        self._power_target_label.configure(text=self._i18n.t("power.target_plan") + ":")
        self._power_interval_label.configure(text=self._i18n.t("power.interval") + ":")
        self._power_interval_suffix.configure(text=self._i18n.t("power.seconds"))
        self._check_btn.configure(text=self._i18n.t("power.check_now"))
        self._monitor_switch.configure(text=self._i18n.t("power.auto_monitor"))
        self._startup_title_label.configure(text=self._i18n.t("startup.title"))
        self._startup_name_entry.configure(placeholder_text=self._i18n.t("startup.name_placeholder"))
        self._startup_path_entry.configure(placeholder_text=self._i18n.t("startup.path_placeholder"))
        self._startup_add_btn.configure(text=self._i18n.t("startup.add_btn"))
        self._startup_add_self_btn.configure(text=self._i18n.t("startup.add_self_btn"))
        self._startup_remove_self_btn.configure(text=self._i18n.t("startup.remove_self_btn"))
        self._startup_refresh_btn.configure(text=self._i18n.t("startup.refresh_btn"))
        self._tray_toggle_switch.configure(text=self._i18n.t("tray.minimize_to_tray"))
        if self._sidebar_expanded:
            self._toggle_btn.configure(text="◀  " + self._i18n.t("sidebar.collapse"))
        else:
            self._toggle_btn.configure(text="▶")
        col_i18n = {"name": "startup.col_name", "path": "startup.col_path",
                     "source": "startup.col_source", "action": "startup.col_action"}
        for key, (lbl, _, _, _) in self._col_header_widgets.items():
            lbl.configure(text=self._i18n.t(col_i18n.get(key, key)))
        self._refresh_power_status()
        self._refresh_startup_list()

    # ==================================================================
    # Main content
    # ==================================================================

    def _build_main_content(self):
        s = self._style
        main = ctk.CTkFrame(self, fg_color="transparent")
        main.pack(side="right", fill="both", expand=True)

        # Header
        header = ctk.CTkFrame(main, fg_color="transparent", height=44)
        header.pack(fill="x", padx=20, pady=(14, 0))
        self._header_title_label = ctk.CTkLabel(
            header, text=self._i18n.t("app.title"),
            font=ctk.CTkFont(size=21, weight="bold"), text_color=s.text_primary)
        self._header_title_label.pack(side="left")
        self._reg(self._header_title_label, lambda w, s: w.configure(text_color=s.text_primary))

        self._monitor_switch = ctk.CTkSwitch(
            header, text=self._i18n.t("power.auto_monitor"), font=ctk.CTkFont(size=15),
            command=self._toggle_monitor)
        apply_switch(self._monitor_switch, s)
        self._monitor_switch.pack(side="right")
        if self.cfg["monitor_enabled"]:
            self._monitor_switch.select()
        self._reg(self._monitor_switch, apply_switch)

        self._main_frame = main
        self._build_power_card()
        self._build_startup_card()

        self._status_bar = ctk.CTkLabel(
            main, text="", font=ctk.CTkFont(size=13), text_color=s.text_muted, anchor="w")
        self._status_bar.pack(side="bottom", fill="x", padx=20, pady=(0, 6))
        self._reg(self._status_bar, lambda w, s: w.configure(text_color=s.text_muted))

    def _on_window_configure(self, event):
        if event.widget != self:
            return
        if hasattr(self, '_header_frame') and self._header_frame.winfo_exists():
            w = self._header_frame.winfo_width()
            if w > 10:
                self._header_width = w
                self._relayout_header()
                self._relayout_all_rows()

    # ==================================================================
    # Power plan card
    # ==================================================================

    def _build_power_card(self):
        s = self._style
        card = ctk.CTkFrame(self._main_frame)
        apply_card(card, s)
        card.pack(fill="x", padx=20, pady=(14, 8))
        self._reg(card, apply_card)

        # Title row
        title_row = ctk.CTkFrame(card, fg_color="transparent")
        title_row.pack(fill="x", padx=16, pady=(12, 2))

        self._power_title_label = ctk.CTkLabel(
            title_row, text="⚡ " + self._i18n.t("power.title"),
            font=ctk.CTkFont(size=17, weight="bold"), text_color=s.text_primary)
        self._power_title_label.pack(side="left")
        self._reg(self._power_title_label, lambda w, s: w.configure(text_color=s.text_primary))

        self._power_status_badge = ctk.CTkLabel(
            title_row, text=self._i18n.t("power.status_checking"),
            font=ctk.CTkFont(size=15), text_color=s.text_secondary)
        self._power_status_badge.pack(side="right")
        self._reg(self._power_status_badge, lambda w, s: w.configure(text_color=s.text_secondary))

        # Content grid
        grid = ctk.CTkFrame(card, fg_color="transparent")
        grid.pack(fill="x", padx=16, pady=(4, 6))

        # Row 1: Active | Target
        row1 = ctk.CTkFrame(grid, fg_color="transparent")
        row1.pack(fill="x", pady=(0, 6))

        self._power_active_label = ctk.CTkLabel(
            row1, text=self._i18n.t("power.active_plan") + ":",
            font=ctk.CTkFont(size=15), text_color=s.text_secondary, width=70, anchor="w")
        self._power_active_label.pack(side="left")
        self._reg(self._power_active_label, lambda w, s: w.configure(text_color=s.text_secondary))

        self._power_active_value = ctk.CTkLabel(
            row1, text="--", font=ctk.CTkFont(size=15, weight="bold"),
            text_color=s.text_primary, anchor="w")
        self._power_active_value.pack(side="left", padx=(0, 20))
        self._reg(self._power_active_value, lambda w, s: w.configure(text_color=s.text_primary))

        self._power_target_label = ctk.CTkLabel(
            row1, text=self._i18n.t("power.target_plan") + ":",
            font=ctk.CTkFont(size=15), text_color=s.text_secondary, width=70, anchor="w")
        self._power_target_label.pack(side="left")
        self._reg(self._power_target_label, lambda w, s: w.configure(text_color=s.text_secondary))

        self._target_dropdown = ctk.CTkOptionMenu(
            row1, values=["Auto"], font=ctk.CTkFont(size=15),
            command=self._on_target_changed)
        apply_dropdown(self._target_dropdown, s)
        _make_dropdown_toggle(self._target_dropdown)
        self._target_dropdown.pack(side="left", padx=(4, 0))
        self._reg(self._target_dropdown, apply_dropdown)
        self._populate_target_dropdown()

        # Row 2: Interval | Check
        row2 = ctk.CTkFrame(grid, fg_color="transparent")
        row2.pack(fill="x")

        self._power_interval_label = ctk.CTkLabel(
            row2, text=self._i18n.t("power.interval") + ":",
            font=ctk.CTkFont(size=15), text_color=s.text_secondary, width=70, anchor="w")
        self._power_interval_label.pack(side="left")
        self._reg(self._power_interval_label, lambda w, s: w.configure(text_color=s.text_secondary))

        self._interval_entry = ctk.CTkEntry(row2, width=56, height=26, font=ctk.CTkFont(size=15))
        apply_entry(self._interval_entry, s)
        self._interval_entry.pack(side="left")
        self._interval_entry.insert(0, str(self.cfg["check_interval"]))
        self._interval_entry.bind("<Return>", lambda e: self._apply_interval())
        self._reg(self._interval_entry, apply_entry)

        self._power_interval_suffix = ctk.CTkLabel(
            row2, text=self._i18n.t("power.seconds"),
            font=ctk.CTkFont(size=14), text_color=s.text_muted)
        self._power_interval_suffix.pack(side="left", padx=(4, 12))
        self._reg(self._power_interval_suffix, lambda w, s: w.configure(text_color=s.text_muted))

        self._apply_interval_btn = ctk.CTkButton(
            row2, text=self._i18n.t("general.apply"), width=48, height=26,
            font=ctk.CTkFont(size=14), command=self._apply_interval)
        apply_btn_style(self._apply_interval_btn, s)
        self._apply_interval_btn.pack(side="left")
        self._reg(self._apply_interval_btn, apply_btn_style)

        self._check_btn = ctk.CTkButton(
            row2, text=self._i18n.t("power.check_now"), width=80, height=26,
            font=ctk.CTkFont(size=14), command=self._check_now)
        apply_btn_style(self._check_btn, s)
        self._check_btn.pack(side="right")
        self._reg(self._check_btn, apply_btn_style)

        # Row 3: Persistent result
        self._check_result_label = ctk.CTkLabel(
            grid, text="", font=ctk.CTkFont(size=14),
            text_color=s.text_secondary, anchor="w", wraplength=400)
        self._check_result_label.pack(fill="x", pady=(4, 0))
        self._reg(self._check_result_label, lambda w, s: w.configure(text_color=s.text_secondary))

        self._power_card = card

    def _populate_target_dropdown(self):
        plans = get_all_plans()
        values: List[str] = []
        self._target_guid_map: Dict[str, str] = {}
        for p in plans:
            marker = "✓ " if p.is_acceptable else ""
            label = f"{marker}{p.name}"
            values.append(label)
            self._target_guid_map[label] = p.guid
        self._target_dropdown.configure(values=values)
        if values:
            self._target_dropdown.set(values[0])
        saved_guid = self.cfg.get("target_guid", "")
        if saved_guid:
            for p in plans:
                if p.guid == saved_guid:
                    marker = "✓ " if p.is_acceptable else ""
                    self._target_dropdown.set(f"{marker}{p.name}")
                    break

    def _on_target_changed(self, choice: str):
        guid = self._target_guid_map.get(choice)
        self.power_monitor.target_guid = guid if guid else None
        self.cfg["target_guid"] = guid if guid else ""
        save_config(self.cfg)

    # ==================================================================
    # Startup items card
    # ==================================================================

    def _build_startup_card(self):
        s = self._style
        card = ctk.CTkFrame(self._main_frame)
        apply_card(card, s)
        card.pack(fill="both", expand=True, padx=20, pady=(8, 12))
        self._reg(card, apply_card)

        # Title
        title_row = ctk.CTkFrame(card, fg_color="transparent")
        title_row.pack(fill="x", padx=14, pady=(10, 4))
        self._startup_title_label = ctk.CTkLabel(
            title_row, text=self._i18n.t("startup.title"),
            font=ctk.CTkFont(size=17, weight="bold"), text_color=s.text_primary)
        self._startup_title_label.pack(side="left")
        self._reg(self._startup_title_label, lambda w, s: w.configure(text_color=s.text_primary))

        self._startup_count_label = ctk.CTkLabel(
            title_row, text="", font=ctk.CTkFont(size=14), text_color=s.text_secondary)
        self._startup_count_label.pack(side="right", padx=(0, 6))
        self._reg(self._startup_count_label, lambda w, s: w.configure(text_color=s.text_secondary))

        self._startup_refresh_btn = ctk.CTkButton(
            title_row, text=self._i18n.t("startup.refresh_btn"), width=64, height=22,
            font=ctk.CTkFont(size=13), command=self._refresh_startup_list)
        apply_btn_secondary(self._startup_refresh_btn, s)
        self._startup_refresh_btn.pack(side="right")
        self._reg(self._startup_refresh_btn, apply_btn_secondary)

        # --- Column header ---
        self._header_frame = ctk.CTkFrame(card, height=24)
        apply_surface_corner(self._header_frame, s)
        self._header_frame.pack(fill="x", padx=14, pady=(2, 2))
        self._header_frame.pack_propagate(False)
        self._header_frame.bind("<Configure>", self._on_header_configure)
        self._reg(self._header_frame, apply_surface_corner)

        # Store: key → (label, handle, handle_bind_ids)
        self._col_header_widgets: Dict[str, tuple] = {}
        col_i18n = {"name": "startup.col_name", "path": "startup.col_path",
                     "source": "startup.col_source", "action": "startup.col_action"}

        for i, key in enumerate(COL_KEYS):
            lbl = ctk.CTkLabel(self._header_frame, text=self._i18n.t(col_i18n[key]),
                               font=ctk.CTkFont(size=13, weight="bold"),
                               text_color=s.text_secondary, anchor="w")
            self._reg(lbl, lambda w, s: w.configure(text_color=s.text_secondary))

            if i < 3:
                handle = ctk.CTkFrame(self._header_frame, width=HANDLE_WIDTH, height=20,
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

        self._relayout_header()

        # --- Scrollable list ---
        self._startup_list_frame = ctk.CTkScrollableFrame(
            card, fg_color="transparent",
            scrollbar_button_color=s.border, scrollbar_button_hover_color=s.border_strong)
        self._startup_list_frame.pack(fill="both", expand=True, padx=14, pady=(0, 2))
        # Limit scroll speed to prevent visual ghosting
        self._startup_list_frame._parent_canvas.configure(yscrollincrement=18,
                                                          xscrollincrement=18)
        self._reg(self._startup_list_frame,
                       lambda w, s: w.configure(scrollbar_button_color=s.border,
                                                scrollbar_button_hover_color=s.border_strong))
        self._table_rows: List[ctk.CTkFrame] = []

        # --- Add row ---
        add_row = ctk.CTkFrame(card, fg_color="transparent")
        add_row.pack(fill="x", padx=14, pady=(2, 10))

        self._startup_name_entry = ctk.CTkEntry(
            add_row, placeholder_text=self._i18n.t("startup.name_placeholder"),
            width=110, height=26, font=ctk.CTkFont(size=14))
        apply_entry(self._startup_name_entry, s)
        self._startup_name_entry.pack(side="left", padx=(0, 5))
        self._reg(self._startup_name_entry, apply_entry)

        self._startup_path_entry = ctk.CTkEntry(
            add_row, placeholder_text=self._i18n.t("startup.path_placeholder"),
            height=26, font=ctk.CTkFont(size=14))
        apply_entry(self._startup_path_entry, s)
        self._startup_path_entry.pack(side="left", fill="x", expand=True, padx=(0, 3))
        self._reg(self._startup_path_entry, apply_entry)

        self._startup_browse_btn = ctk.CTkButton(
            add_row, text="...", width=28, height=26, font=ctk.CTkFont(size=14),
            command=self._browse_startup_path)
        apply_btn_secondary(self._startup_browse_btn, s)
        self._startup_browse_btn.pack(side="left", padx=(0, 5))
        self._reg(self._startup_browse_btn, apply_btn_secondary)

        self._startup_add_btn = ctk.CTkButton(
            add_row, text=self._i18n.t("startup.add_btn"), width=68, height=26,
            font=ctk.CTkFont(size=14), command=self._add_startup_item)
        apply_btn_style(self._startup_add_btn, s)
        self._startup_add_btn.pack(side="left", padx=(0, 4))
        self._reg(self._startup_add_btn, apply_btn_style)

        # Toggle button: "Add This App" / "Remove This App"
        self._startup_add_self_btn = ctk.CTkButton(
            add_row, text=self._i18n.t("startup.add_self_btn"), width=96, height=26,
            font=ctk.CTkFont(size=13), command=self._add_self_to_startup)
        apply_btn_secondary(self._startup_add_self_btn, s)
        self._reg(self._startup_add_self_btn, apply_btn_secondary)

        self._startup_remove_self_btn = ctk.CTkButton(
            add_row, text=self._i18n.t("startup.remove_self_btn"), width=96, height=26,
            font=ctk.CTkFont(size=13), command=self._remove_self_from_startup)
        apply_btn_secondary(self._startup_remove_self_btn, s)
        self._reg(self._startup_remove_self_btn, apply_btn_secondary)

        self._startup_card = card
        self._refresh_startup_list()

    # ==================================================================
    # Column header layout & resize
    # ==================================================================

    def _on_header_configure(self, event):
        w = event.width
        if w > 10:
            self._header_width = w
            self._relayout_header()
            self._relayout_all_rows()

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
        self._relayout_header()
        # Throttle row relayout: cancel pending, schedule new one
        if hasattr(self, '_resize_after_id'):
            self.after_cancel(self._resize_after_id)
        self._resize_after_id = self.after(16, self._relayout_all_rows)  # ~60fps

    def _end_resize(self):
        self._resizing_col = -1
        self.cfg["col_widths"] = list(self._col_widths)
        save_config(self.cfg)

    def _relayout_all_rows(self):
        offset = 0.02
        col_w = self._col_widths
        gap = 0.005

        for row in self._table_rows:
            if not row.winfo_exists():
                continue
            children = row.winfo_children()
            if len(children) < 4:
                continue
            for i, key in enumerate(COL_KEYS):
                if i >= len(children):
                    break
                x_frac = offset + sum(col_w[:i]) + gap * i
                if key == "action":
                    children[i].place(relx=x_frac, rely=0.5, anchor="w")
                else:
                    children[i].place(relx=x_frac, rely=0.5, anchor="w",
                                      relwidth=col_w[i] - 0.01)

    # ==================================================================
    # Startup list
    # ==================================================================

    @staticmethod
    def _make_item_key(item: StartupItem) -> str:
        return f"{item.source}:{item.name}"

    def _create_startup_row(self, item: StartupItem) -> ctk.CTkFrame:
        s = self._style
        row = ctk.CTkFrame(self._startup_list_frame, height=ROW_HEIGHT, corner_radius=4)
        apply_surface_corner(row, s)
        row.pack(fill="x", pady=1)
        row.pack_propagate(False)
        self._reg(row, apply_surface_corner)

        # Name
        name_lbl = ctk.CTkLabel(row, text=item.name,
                                font=ctk.CTkFont(size=14, weight="bold"),
                                text_color=s.text_primary, anchor="w")
        self._reg(name_lbl, lambda w, s: w.configure(text_color=s.text_primary))

        # Path (truncated)
        path_text = item.path
        if len(path_text) > 60:
            path_text = "..." + path_text[-57:]
        path_lbl = ctk.CTkLabel(row, text=path_text, font=ctk.CTkFont(size=13),
                                text_color=s.text_secondary, anchor="w")
        self._reg(path_lbl, lambda w, s: w.configure(text_color=s.text_secondary))

        # Source
        src_lbl = ctk.CTkLabel(row, text=item.source, font=ctk.CTkFont(size=13),
                               text_color=s.text_muted, anchor="center")
        self._reg(src_lbl, lambda w, s: w.configure(text_color=s.text_muted))

        # Remove btn
        remove_btn = ctk.CTkButton(
            row, text=self._i18n.t("startup.remove_btn"), width=40, height=20,
            font=ctk.CTkFont(size=12),
            fg_color=s.card_elevated, hover_color="#c64545",
            text_color=s.text_secondary, corner_radius=3,
            command=lambda it=item: self._remove_startup_item(it))
        self._reg(remove_btn, lambda w, s: w.configure(
            fg_color=s.card_elevated, hover_color="#c64545",
            text_color=s.text_secondary))
        return row

    def _refresh_startup_list(self):
        for w in self._startup_list_frame.winfo_children():
            w.destroy()
        self._table_rows.clear()
        self._item_rows.clear()
        # Clean stale startup row registrations from _stylables
        self._stylables = [(w, fn) for w, fn in self._stylables if w.winfo_exists()]

        items = get_all_items()
        self._startup_count_label.configure(
            text=f"{len(items)}{self._i18n.t('startup.entries')}")

        if not items:
            empty = ctk.CTkLabel(self._startup_list_frame,
                                 text=self._i18n.t("startup.empty"),
                                 font=ctk.CTkFont(size=14), text_color=self._style.text_muted)
            empty.pack(pady=16)
            self._update_self_buttons()
            return

        for item in items:
            row = self._create_startup_row(item)
            self._table_rows.append(row)
            self._item_rows[self._make_item_key(item)] = row

        self._relayout_all_rows()
        self._update_self_buttons()

    # ==================================================================
    # Startup CRUD
    # ==================================================================

    def _browse_startup_path(self):
        path = filedialog.askopenfilename(title=self._i18n.t("startup.title"))
        if path:
            self._startup_path_entry.delete(0, "end")
            self._startup_path_entry.insert(0, path)

    def _add_startup_item(self):
        name = self._startup_name_entry.get().strip()
        path = self._startup_path_entry.get().strip()
        if not name or not path:
            self._show_check_result(self._i18n.t("startup.fill_both"), "warning")
            return
        if add_registry_startup(name, path):
            self._startup_name_entry.delete(0, "end")
            self._startup_path_entry.delete(0, "end")
            item = StartupItem(name=name, path=path, source="registry")
            self._surgically_add_row(item)
            self._show_check_result(self._i18n.t("startup.added", name=name), "ok")
        else:
            self._show_check_result(self._i18n.t("startup.add_failed"), "error")

    def _add_self_to_startup(self):
        exe_path = get_app_exe_path()
        if exe_path.endswith(".py"):
            exe_path = f'"{sys.executable}" "{exe_path}"'
        if add_registry_startup("PC_System_Auto_Scripts", exe_path):
            item = StartupItem(name="PC_System_Auto_Scripts", path=exe_path, source="registry")
            self._surgically_add_row(item)
            self._update_self_buttons()
            self._show_check_result(self._i18n.t("startup.added_self"), "ok")
        else:
            self._show_check_result(self._i18n.t("startup.add_failed"), "error")

    def _remove_self_from_startup(self):
        if remove_registry_startup("PC_System_Auto_Scripts"):
            key = "registry:PC_System_Auto_Scripts"
            row = self._item_rows.pop(key, None)
            if row is not None and row.winfo_exists():
                children_ids = {id(c) for c in row.winfo_children()}
                children_ids.add(id(row))
                self._stylables = [(w, fn) for w, fn in self._stylables
                                   if id(w) not in children_ids]
                if row in self._table_rows:
                    self._table_rows.remove(row)
                row.pack_forget()
                row.destroy()
                self._startup_count_label.configure(
                    text=f"{len(self._table_rows)}{self._i18n.t('startup.entries')}")
                if not self._table_rows:
                    empty = ctk.CTkLabel(self._startup_list_frame,
                                         text=self._i18n.t("startup.empty"),
                                         font=ctk.CTkFont(size=14),
                                         text_color=self._style.text_muted)
                    empty.pack(pady=16)
                self._relayout_all_rows()
            self._update_self_buttons()
            self._show_check_result(self._i18n.t("startup.remove_self"), "ok")
        else:
            self._show_check_result(self._i18n.t("startup.remove_failed", name="PC_System_Auto_Scripts"), "error")

    def _has_self_in_startup(self) -> bool:
        return any(
            item.name == "PC_System_Auto_Scripts" and item.source == "registry"
            for item in get_all_items()
        )

    def _update_self_buttons(self):
        if self._has_self_in_startup():
            self._startup_add_self_btn.pack_forget()
            self._startup_remove_self_btn.pack(side="left")
        else:
            self._startup_remove_self_btn.pack_forget()
            self._startup_add_self_btn.pack(side="left")

    def _surgically_add_row(self, item: StartupItem):
        # Remove empty-state label if present
        if not self._table_rows:
            for w in self._startup_list_frame.winfo_children():
                if isinstance(w, ctk.CTkLabel):
                    w.destroy()
        row = self._create_startup_row(item)
        self._table_rows.append(row)
        self._item_rows[self._make_item_key(item)] = row
        self._startup_count_label.configure(
            text=f"{len(self._table_rows)}{self._i18n.t('startup.entries')}")
        self._relayout_all_rows()

    def _remove_startup_item(self, item: StartupItem):
        ok = (remove_registry_startup(item.name) if item.source == "registry"
              else remove_startup_folder_item(item.name))
        if ok:
            key = self._make_item_key(item)
            row = self._item_rows.pop(key, None)
            if row is not None and row.winfo_exists():
                # Remove row's widgets from _stylables
                children_ids = {id(c) for c in row.winfo_children()}
                children_ids.add(id(row))
                self._stylables = [(w, fn) for w, fn in self._stylables
                                   if id(w) not in children_ids]
                if row in self._table_rows:
                    self._table_rows.remove(row)
                row.pack_forget()
                row.destroy()
                self._startup_count_label.configure(
                    text=f"{len(self._table_rows)}{self._i18n.t('startup.entries')}")
                # Show empty label if no items left
                if not self._table_rows:
                    empty = ctk.CTkLabel(self._startup_list_frame,
                                         text=self._i18n.t("startup.empty"),
                                         font=ctk.CTkFont(size=14),
                                         text_color=self._style.text_muted)
                    empty.pack(pady=16)
                self._relayout_all_rows()
            self._show_check_result(self._i18n.t("startup.removed", name=item.name), "ok")
            # Update self-button visibility after removal
            self._update_self_buttons()
        else:
            self._show_check_result(self._i18n.t("startup.remove_failed", name=item.name), "error")

    # ==================================================================
    # Power plan actions
    # ==================================================================

    def _refresh_power_status(self):
        def _check():
            active = get_active_plan()
            self.after(0, lambda: self._update_power_ui(active))
            self.after(0, self._refresh_target_dropdown_items)
        threading.Thread(target=_check, daemon=True).start()

    def _refresh_target_dropdown_items(self):
        plans = get_all_plans()
        if not plans:
            return
        current = self._target_dropdown.get()
        values: List[str] = []
        self._target_guid_map.clear()
        for p in plans:
            marker = "✓ " if p.is_acceptable else ""
            label = f"{marker}{p.name}"
            values.append(label)
            self._target_guid_map[label] = p.guid
        self._target_dropdown.configure(values=values)
        if current in values:
            self._target_dropdown.set(current)
        elif values:
            self._target_dropdown.set(values[0])

    def _update_power_ui(self, active):
        if active is None:
            self._power_active_value.configure(text=self._i18n.t("power.unable_detect"))
            self._power_status_badge.configure(
                text=self._i18n.t("power.status_error"), text_color="#e52020")
            return
        self._power_active_value.configure(text=active.name)

        # Check if active plan matches the selected target
        target_guid = self.power_monitor.target_guid
        if target_guid:
            ok = (active.guid == target_guid)
        else:
            ok = is_acceptable_plan(active.name)

        if ok:
            self._power_status_badge.configure(
                text=self._i18n.t("power.status_ok"), text_color="#5db872")
        else:
            self._power_status_badge.configure(
                text=self._i18n.t("power.status_needs_fix"), text_color="#e8a55a")

    def _check_now(self):
        self._show_check_result(self._i18n.t("power.status_checking"), "info", persistent=True)
        def _do():
            result = self.power_monitor.check_now()
            self.after(0, lambda: self._refresh_power_status())
            self.after(0, lambda: self._show_result_persistent(result))
        threading.Thread(target=_do, daemon=True).start()

    def _show_result_persistent(self, result: dict):
        status = result.get("status", "")
        ts = time.strftime("%H:%M:%S")
        if status == "ok":
            msg = f"[{ts}] {self._i18n.t('power.already_ok', name=result.get('plan_name', ''))}"
            self._show_check_result(msg, "ok", persistent=True)
        elif status == "fixed":
            msg = f"[{ts}] {self._i18n.t('power.switched', from_=result.get('plan_name', ''), to=result.get('target_name', ''))}"
            self._show_check_result(msg, "ok", persistent=True)
        elif status == "fix_failed":
            msg = f"[{ts}] {self._i18n.t('power.switch_failed', name=result.get('plan_name', ''))}"
            self._show_check_result(msg, "error", persistent=True)
        elif status == "no_target":
            msg = f"[{ts}] {self._i18n.t('power.no_target')}"
            self._show_check_result(msg, "error", persistent=True)
        else:
            msg = f"[{ts}] {status}: {result.get('plan_name', '')}"
            self._show_check_result(msg, "error", persistent=True)

    def _on_power_status_change(self, result: dict):
        if self._window_visible:
            self.after(0, lambda: self._refresh_power_status())
            self.after(0, lambda: self._show_result_persistent(result))

    def _show_check_result(self, msg: str, kind: str = "info", persistent: bool = False):
        colors = {
            "ok": self._style.accent if self._style.name == "NVIDIA" else "#5db872",
            "error": "#e52020",
            "warning": "#e8a55a",
            "info": self._style.text_secondary,
        }
        self._check_result_label.configure(
            text=msg, text_color=colors.get(kind, self._style.text_secondary))
        if not persistent:
            self._status_bar.configure(
                text=msg, text_color=colors.get(kind, self._style.text_secondary))
            self.after(8000, lambda: self._status_bar.configure(text=""))

    def _apply_interval(self):
        try:
            secs = int(self._interval_entry.get().strip())
            secs = max(10, min(3600, secs))
            self._interval_entry.delete(0, "end")
            self._interval_entry.insert(0, str(secs))
            self.power_monitor.interval = secs
            self.cfg["check_interval"] = secs
            save_config(self.cfg)
            self._show_check_result(
                f"[{time.strftime('%H:%M:%S')}] {self._i18n.t('startup.interval_set', secs=secs)}", "ok")
        except ValueError:
            self._show_check_result(self._i18n.t("startup.interval_invalid"), "error")

    # ==================================================================
    # Monitor toggle
    # ==================================================================

    def _toggle_monitor(self):
        if self._monitor_switch.get():
            self._start_monitor()
        else:
            self._stop_monitor()

    def _start_monitor(self):
        if not self._monitor_running:
            self.power_monitor.start()
            self._monitor_running = True
            self.cfg["monitor_enabled"] = True
            save_config(self.cfg)

    def _stop_monitor(self):
        self.power_monitor.stop()
        self._monitor_running = False
        self.cfg["monitor_enabled"] = False
        save_config(self.cfg)

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
                self.after(0, self.deiconify)

            def on_exit(icon, item):
                self._closing = True
                icon.stop()
                self.after(0, self._force_quit)

            menu = pystray.Menu(
                pystray.MenuItem(self._i18n.t("tray.show"), on_show, default=True),
                pystray.MenuItem(self._i18n.t("tray.check"),
                                 lambda: self.after(0, self._check_now)),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem(self._i18n.t("tray.exit"), on_exit),
            )
            self._tray_icon = pystray.Icon(
                "pc_auto_scripts", icon_img, "PC System Auto Scripts", menu)
            self._tray_thread = threading.Thread(target=self._tray_icon.run, daemon=True)
            self._tray_thread.start()
            return True
        except Exception:
            return False

    def _update_tray_icon_color(self):
        if self._tray_icon is not None:
            try:
                self._tray_icon.icon = _create_tray_icon(self._style.accent)
            except Exception:
                pass

    # ==================================================================
    # Window lifecycle
    # ==================================================================

    def _on_close(self):
        if self.cfg.get("minimize_to_tray", True) and self._setup_tray():
            self._window_visible = False
            self.withdraw()
        else:
            self._force_quit()

    def _force_quit(self):
        self._running = False
        self._closing = True
        self._stop_monitor()
        if self._tray_icon is not None:
            try:
                self._tray_icon.stop()
            except Exception:
                pass
        try:
            self.destroy()
        except Exception:
            pass
        # Belt-and-suspenders: ensure the process actually exits
        sys.exit(0)

    def _restore_from_ipc(self):
        """Called when another instance tries to launch — restore window."""
        self._window_visible = True
        self.deiconify()

    def deiconify(self):
        self._window_visible = True
        super().deiconify()
        self._refresh_power_status()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    if _is_already_running():
        sys.exit(0)
    ctk.set_appearance_mode("dark")
    app = App()
    _start_ipc_server(app)
    app.mainloop()


if __name__ == "__main__":
    main()
