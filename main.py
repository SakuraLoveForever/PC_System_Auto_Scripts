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
from tkinter import Menu, filedialog, font as tkfont

from pypinyin import lazy_pinyin, Style
from styles import STYLES, DEFAULT_STYLE, STYLE_ALIASES, DesignStyle
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
        "start_to_tray": True,
        "col_widths": [0.28, 0.56, 0.08, 0.08],
    }
    if CONFIG_FILE.exists():
        try:
            defaults.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            pass
    else:
        save_config(defaults.copy())
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
COL_DEFAULTS = [0.28, 0.56, 0.08, 0.08]
COL_MIN = 0.04
HANDLE_WIDTH = 4
ROW_HEIGHT = 26
AUTO_TARGET_LABEL = "Auto"


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
        self._tray_icon = None
        self._tray_thread = None
        self._closing = False
        self._running = True  # for IPC server loop

        # Sort state for startup list
        self._sort_key: str = "name"
        self._sort_ascending: bool = True

        # Compact mode
        self._compact_mode = False
        self._compact_frame: Optional[ctk.CTkFrame] = None
        self._full_geometry = "980x640"
        self._compact_geometry = "360x170"

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
        self.geometry("980x640")
        self.minsize(680, 480)
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

        if self.cfg.get("start_to_tray", True):
            if self._setup_tray():
                self._window_visible = False
                self.after(500, self.withdraw)

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
        if hasattr(self, '_compact_style_dd') and self._compact_style_dd.winfo_exists():
            self._compact_style_dd.set(name)
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

        # Section labels
        self._appearance_label = ctk.CTkLabel(
            self._sidebar, text=self._i18n.t("sidebar.appearance"),
            font=ctk.CTkFont(size=13), text_color=s.text_muted, anchor="w")
        self._appearance_label.pack(fill="x", padx=14, pady=(12, 4))
        self._reg(self._appearance_label, lambda w, s: w.configure(text_color=s.text_muted))

        # Style dropdown
        self._style_dropdown = ctk.CTkOptionMenu(
            self._sidebar, values=list(STYLES.keys()), font=ctk.CTkFont(size=15),
            command=self._switch_style)
        apply_dropdown(self._style_dropdown, s)
        _make_dropdown_toggle(self._style_dropdown)
        self._style_dropdown.set(self.cfg["style"])
        fit_option_width(self._style_dropdown, list(STYLES.keys()), min_width=118,
                         max_width=SIDEBAR_EXPANDED - 24)
        self._style_dropdown.pack(anchor="w", padx=12, pady=(2, 6))
        self._reg(self._style_dropdown, apply_dropdown)

        # Language dropdown
        self._lang_dropdown = ctk.CTkOptionMenu(
            self._sidebar, values=[LANG_LABELS[l] for l in SUPPORTED_LANGS],
            font=ctk.CTkFont(size=15), command=self._on_lang_changed)
        apply_dropdown(self._lang_dropdown, s)
        _make_dropdown_toggle(self._lang_dropdown)
        self._lang_dropdown.set(LANG_LABELS.get(self._i18n.lang, "中文"))
        fit_option_width(self._lang_dropdown, [LANG_LABELS[l] for l in SUPPORTED_LANGS],
                         min_width=96, max_width=SIDEBAR_EXPANDED - 24)
        self._lang_dropdown.pack(anchor="w", padx=12, pady=(2, 6))
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
        self._sep2.pack(fill="x", padx=12, pady=(14, 10))
        self._reg(self._sep2, lambda w, s: w.configure(fg_color=s.border))

        self._settings_label = ctk.CTkLabel(
            self._sidebar, text=self._i18n.t("sidebar.settings"),
            font=ctk.CTkFont(size=13), text_color=s.text_muted, anchor="w")
        self._settings_label.pack(fill="x", padx=14, pady=(0, 4))
        self._reg(self._settings_label, lambda w, s: w.configure(text_color=s.text_muted))

        # Close behavior toggle
        self._tray_toggle_switch = ctk.CTkSwitch(
            self._sidebar, text=self._i18n.t("tray.minimize_to_tray"),
            font=ctk.CTkFont(size=14), text_color=s.text_secondary,
            command=self._toggle_tray_behavior)
        apply_switch(self._tray_toggle_switch, s)
        self._tray_toggle_switch.pack(padx=14, pady=(2, 4), anchor="w")
        if self.cfg.get("minimize_to_tray", True):
            self._tray_toggle_switch.select()
        self._reg(self._tray_toggle_switch, apply_switch)

        # Version
        self._sidebar_version = ctk.CTkLabel(
            self._sidebar, text="v1.0.0", font=ctk.CTkFont(size=13), text_color=s.text_muted)
        self._sidebar_version.pack(side="bottom", pady=(0, 10))
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
        save_config(self.cfg)

    def _toggle_sidebar(self):
        delta = SIDEBAR_EXPANDED - SIDEBAR_COLLAPSED  # 150
        cur_w = self.winfo_width()
        cur_h = self.winfo_height()

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
            self._brand_frame.pack(fill="x", padx=14, pady=(16, 4))
            self._appearance_label.pack(fill="x", padx=14, pady=(12, 4))
            self._style_dropdown.pack(anchor="w", padx=12, pady=(2, 6))
            self._lang_dropdown.pack(anchor="w", padx=12, pady=(2, 6))
            self._sep2.pack(fill="x", padx=12, pady=(14, 10))
            self._settings_label.pack(fill="x", padx=14, pady=(0, 4))
            self._tray_toggle_switch.pack(padx=14, pady=(2, 4), anchor="w")
            self._sidebar_version.pack(side="bottom", pady=(0, 10))
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
        save_config(self.cfg)
        self._refresh_all_text()

    def _refresh_all_text(self):
        self.title(self._i18n.t("app.title"))
        self._header_title_label.configure(text=self._i18n.t("app.title"))
        self._power_title_label.configure(text="⚡ " + self._i18n.t("power.title"))
        self._power_target_label.configure(text=self._i18n.t("power.target_plan") + ":")
        self._power_interval_label.configure(text=self._i18n.t("power.interval") + ":")
        self._power_interval_suffix.configure(text=self._i18n.t("power.seconds"))
        self._apply_interval_btn.configure(text=self._i18n.t("general.apply"))
        self._check_btn.configure(text=self._i18n.t("power.check_now"))
        self._monitor_switch.configure(text=self._i18n.t("power.auto_monitor"))
        self._startup_title_label.configure(text=self._i18n.t("startup.title"))
        self._startup_name_entry.configure(placeholder_text=self._i18n.t("startup.name_placeholder"))
        self._startup_path_entry.configure(placeholder_text=self._i18n.t("startup.path_placeholder"))
        self._startup_add_btn.configure(text=self._i18n.t("startup.add_btn"))
        self._startup_add_self_btn.configure(text=self._i18n.t("startup.add_self_btn"))
        self._startup_remove_self_btn.configure(text=self._i18n.t("startup.remove_self_btn"))
        self._startup_refresh_btn.configure(text=self._i18n.t("startup.refresh_btn"))
        self._appearance_label.configure(text=self._i18n.t("sidebar.appearance"))
        self._settings_label.configure(text=self._i18n.t("sidebar.settings"))
        self._tray_toggle_switch.configure(text=self._i18n.t("tray.minimize_to_tray"))
        self._update_mode_switch_texts()
        if hasattr(self, '_compact_title') and self._compact_title.winfo_exists():
            self._compact_title.configure(text="⚡ " + self._i18n.t("power.title"))
        if hasattr(self, '_compact_active_label') and self._compact_active_label.winfo_exists():
            self._compact_active_label.configure(text=self._i18n.t("power.active_plan") + ":")
        if hasattr(self, '_compact_check_btn') and self._compact_check_btn.winfo_exists():
            self._compact_check_btn.configure(text=self._i18n.t("power.check_now"))
        if hasattr(self, '_compact_monitor_switch') and self._compact_monitor_switch.winfo_exists():
            self._compact_monitor_switch.configure(text=self._i18n.t("power.auto_monitor"))
        fit_option_width(self._style_dropdown, list(STYLES.keys()), min_width=118,
                         max_width=SIDEBAR_EXPANDED - 24)
        fit_option_width(self._lang_dropdown, [LANG_LABELS[l] for l in SUPPORTED_LANGS],
                         min_width=96, max_width=SIDEBAR_EXPANDED - 24)
        fit_entry_width(self._interval_entry, min_width=52, max_width=92, padding=30)
        fit_entry_width(self._startup_name_entry, min_width=112, max_width=190, padding=34)
        fit_entry_width(self._startup_path_entry, min_width=180, max_width=430, padding=38)
        fit_button_width(self._apply_interval_btn, min_width=56, max_width=96)
        fit_button_width(self._check_btn, min_width=92, max_width=180)
        fit_button_width(self._startup_refresh_btn, min_width=62, max_width=120)
        fit_button_width(self._startup_add_btn, min_width=70, max_width=126)
        fit_button_width(self._startup_add_self_btn, min_width=92, max_width=160)
        fit_button_width(self._startup_remove_self_btn, min_width=92, max_width=170)
        fit_button_width(self._compact_toggle_btn, min_width=74, max_width=110, padding=24)
        if hasattr(self, '_compact_full_btn') and self._compact_full_btn.winfo_exists():
            fit_button_width(self._compact_full_btn, min_width=82, max_width=124, padding=26)
        if self._sidebar_expanded:
            self._toggle_btn.configure(text="◀")
        else:
            self._toggle_btn.configure(text="▶")
        self._update_sort_indicator()
        self._refresh_power_status()
        self._refresh_startup_list()

    # ==================================================================
    # Main content
    # ==================================================================

    def _build_main_content(self):
        s = self._style
        main = ctk.CTkFrame(self, fg_color="transparent")
        self._main_frame_ref = main
        main.place(x=SIDEBAR_EXPANDED, y=0, relheight=1.0)
        self.bind("<Configure>", self._on_root_configure, add="+")

        # Header — minimal, single row
        header = ctk.CTkFrame(main, fg_color="transparent", height=32)
        header.pack(fill="x", padx=16, pady=(8, 0))
        header.pack_propagate(False)

        self._header_title_label = ctk.CTkLabel(
            header, text=self._i18n.t("app.title"),
            font=ctk.CTkFont(size=17, weight="bold"), text_color=s.text_primary,
            anchor="w")
        self._header_title_label.pack(side="left")
        self._reg(self._header_title_label, lambda w, s: w.configure(text_color=s.text_primary))

        self._monitor_switch = ctk.CTkSwitch(
            header, text=self._i18n.t("power.auto_monitor"), font=ctk.CTkFont(size=13),
            command=self._toggle_monitor)
        apply_switch(self._monitor_switch, s)
        self._monitor_switch.pack(side="right", padx=(0, 8))
        if self.cfg["monitor_enabled"]:
            self._monitor_switch.select()
        self._reg(self._monitor_switch, apply_switch)

        self._compact_toggle_btn = ctk.CTkButton(
            header, text=self._i18n.t("compact.toggle"), width=48, height=24,
            font=ctk.CTkFont(size=11), command=self._toggle_compact_mode)
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
            main, text="", font=ctk.CTkFont(size=11), text_color=s.text_muted, anchor="w")
        self._status_bar.pack(side="bottom", fill="x", padx=16, pady=(0, 4))
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

    def _relayout_main_frame(self):
        """Keep the main frame aligned with the sidebar's actual width."""
        if not hasattr(self, "_main_frame_ref") or not self._main_frame_ref.winfo_exists():
            return
        if self._compact_mode:
            return
        sidebar_fallback = SIDEBAR_EXPANDED if self._sidebar_expanded else SIDEBAR_COLLAPSED
        sidebar_w = sidebar_fallback
        try:
            if hasattr(self, "_sidebar") and self._sidebar.winfo_exists():
                sidebar_w = max(self._sidebar.winfo_width(), sidebar_fallback)
        except Exception:
            sidebar_w = sidebar_fallback
        total_w = self.winfo_width()
        content_w = max(total_w - sidebar_w, 100)
        self._main_frame_ref.place_configure(x=sidebar_w, y=0, relheight=1.0, width=content_w)

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
        card.pack(fill="x", padx=14, pady=(4, 2))
        self._reg(card, apply_card)

        # Row 1: title + status badge + active plan name
        r1 = ctk.CTkFrame(card, fg_color="transparent", height=30)
        r1.pack(fill="x", padx=10, pady=(6, 2))
        r1.pack_propagate(False)

        self._power_title_label = ctk.CTkLabel(
            r1, text="⚡ " + self._i18n.t("power.title"),
            font=ctk.CTkFont(size=13, weight="bold"), text_color=s.text_primary, anchor="w")
        self._power_title_label.pack(side="left")
        self._reg(self._power_title_label, lambda w, s: w.configure(text_color=s.text_primary))

        self._power_status_badge = ctk.CTkLabel(
            r1, text=self._i18n.t("power.status_checking"),
            font=ctk.CTkFont(size=11, weight="bold"), text_color=s.text_secondary,
            width=72, height=20, fg_color=s.surface, corner_radius=10)
        self._power_status_badge.pack(side="left", padx=(8, 10))
        self._reg(self._power_status_badge,
                  lambda w, s: w.configure(text_color=s.text_secondary, fg_color=s.surface))

        self._power_active_value = ctk.CTkLabel(
            r1, text="--", font=ctk.CTkFont(size=15, weight="bold"),
            text_color=s.text_primary, anchor="w")
        self._power_active_value.pack(side="left")
        self._reg(self._power_active_value, lambda w, s: w.configure(text_color=s.text_primary))

        # Row 2: all controls inline
        r2 = ctk.CTkFrame(card, fg_color="transparent", height=32)
        r2.pack(fill="x", padx=10, pady=(2, 6))
        r2.pack_propagate(False)

        self._power_target_label = ctk.CTkLabel(
            r2, text=self._i18n.t("power.target_plan") + ":",
            font=ctk.CTkFont(size=11), text_color=s.text_secondary, anchor="w")
        self._power_target_label.pack(side="left", padx=(0, 3))
        self._reg(self._power_target_label, lambda w, s: w.configure(text_color=s.text_secondary))

        self._target_dropdown = ctk.CTkOptionMenu(
            r2, values=["Auto"], font=ctk.CTkFont(size=12),
            height=26, width=110, dynamic_resizing=False, command=self._on_target_changed)
        apply_dropdown(self._target_dropdown, s)
        self._target_dropdown.pack(side="left", padx=(0, 10))
        self._reg(self._target_dropdown, apply_dropdown)
        self._populate_target_dropdown()
        _make_dropdown_toggle(self._target_dropdown)

        self._power_interval_label = ctk.CTkLabel(
            r2, text=self._i18n.t("power.interval") + ":",
            font=ctk.CTkFont(size=11), text_color=s.text_secondary, anchor="w")
        self._power_interval_label.pack(side="left", padx=(0, 2))
        self._reg(self._power_interval_label, lambda w, s: w.configure(text_color=s.text_secondary))

        self._interval_entry = ctk.CTkEntry(r2, width=42, height=26, font=ctk.CTkFont(size=12))
        apply_entry(self._interval_entry, s)
        self._interval_entry.pack(side="left")
        self._interval_entry.insert(0, str(self.cfg["check_interval"]))
        self._interval_entry.bind("<Return>", lambda e: self._apply_interval())
        self._reg(self._interval_entry, apply_entry)

        self._power_interval_suffix = ctk.CTkLabel(
            r2, text=self._i18n.t("power.seconds"),
            font=ctk.CTkFont(size=10), text_color=s.text_muted, width=14)
        self._power_interval_suffix.pack(side="left", padx=(2, 4))
        self._reg(self._power_interval_suffix, lambda w, s: w.configure(text_color=s.text_muted))

        self._apply_interval_btn = ctk.CTkButton(
            r2, text=self._i18n.t("general.apply"), width=44, height=26,
            font=ctk.CTkFont(size=11), command=self._apply_interval)
        apply_btn_style(self._apply_interval_btn, s)
        self._apply_interval_btn.pack(side="left", padx=(0, 10))
        self._reg(self._apply_interval_btn, apply_btn_style)

        self._check_btn = ctk.CTkButton(
            r2, text=self._i18n.t("power.check_now"), height=26,
            font=ctk.CTkFont(size=12), command=lambda: [self._btn_press_flash(self._check_btn), self._check_now()])
        apply_btn_style(self._check_btn, s)
        fit_button_width(self._check_btn, min_width=70, max_width=140)
        self._check_btn.pack(side="left", padx=(0, 10))
        self._reg(self._check_btn, apply_btn_style)

        self._check_result_label = ctk.CTkLabel(
            r2, text="", font=ctk.CTkFont(size=10),
            text_color=s.text_secondary, anchor="w", width=160)
        self._check_result_label.pack(side="left")
        self._reg(self._check_result_label, lambda w, s: w.configure(text_color=s.text_secondary))

        self._power_card = card

    def _populate_target_dropdown(self):
        plans = get_all_plans()
        values: List[str] = [AUTO_TARGET_LABEL]
        self._target_guid_map: Dict[str, Optional[str]] = {AUTO_TARGET_LABEL: None}
        for p in plans:
            marker = "✓ " if p.is_acceptable else ""
            label = f"{marker}{p.name}"
            values.append(label)
            self._target_guid_map[label] = p.guid
        fit_option_width(self._target_dropdown, values, min_width=110, max_width=300)
        self._target_dropdown.configure(values=values)
        saved_guid = self.cfg.get("target_guid", "")
        if saved_guid:
            for p in plans:
                if p.guid == saved_guid:
                    marker = "✓ " if p.is_acceptable else ""
                    self._target_dropdown.set(f"{marker}{p.name}")
                    return
        self._target_dropdown.set(AUTO_TARGET_LABEL)

    def _on_target_changed(self, choice: str):
        guid = self._target_guid_map.get(choice)
        self.power_monitor.target_guid = guid if guid else None
        self.cfg["target_guid"] = guid if guid else ""
        save_config(self.cfg)
        if guid:
            # Only switch if UI is fully built (skip init-triggered sets)
            if hasattr(self, '_power_card') and self._power_card.winfo_exists():
                self._do_switch_plan(guid)

    # ==================================================================
    # Startup items card
    # ==================================================================

    def _build_startup_card(self):
        s = self._style
        card = ctk.CTkFrame(self._main_frame)
        apply_card(card, s)
        card.pack(fill="both", expand=True, padx=16, pady=(0, 10))
        self._reg(card, apply_card)

        # Title
        title_row = ctk.CTkFrame(card, fg_color="transparent")
        title_row.pack(fill="x", padx=12, pady=(8, 4))
        self._startup_title_label = ctk.CTkLabel(
            title_row, text=self._i18n.t("startup.title"),
            font=ctk.CTkFont(size=15, weight="bold"), text_color=s.text_primary)
        self._startup_title_label.pack(side="left")
        self._reg(self._startup_title_label, lambda w, s: w.configure(text_color=s.text_primary))

        self._startup_count_label = ctk.CTkLabel(
            title_row, text="", font=ctk.CTkFont(size=13), text_color=s.text_secondary)
        self._startup_count_label.pack(side="right", padx=(0, 4))
        self._reg(self._startup_count_label, lambda w, s: w.configure(text_color=s.text_secondary))

        self._startup_refresh_btn = ctk.CTkButton(
            title_row, text=self._i18n.t("startup.refresh_btn"), width=64, height=24,
            font=ctk.CTkFont(size=12), command=self._refresh_startup_list)
        apply_btn_secondary(self._startup_refresh_btn, s)
        fit_button_width(self._startup_refresh_btn, min_width=56, max_width=100)
        self._startup_refresh_btn.pack(side="right")
        self._reg(self._startup_refresh_btn, apply_btn_secondary)

        # --- Add row ---
        add_row = ctk.CTkFrame(card)
        apply_surface_corner(add_row, s)
        add_row.pack(fill="x", padx=12, pady=(0, 8))
        self._reg(add_row, apply_surface_corner)

        self._startup_name_entry = ctk.CTkEntry(
            add_row, placeholder_text=self._i18n.t("startup.name_placeholder"),
            width=130, height=28, font=ctk.CTkFont(size=13))
        apply_entry(self._startup_name_entry, s)
        bind_entry_autofit(self._startup_name_entry, min_width=100, max_width=170, padding=32)
        self._startup_name_entry.pack(side="left", padx=(8, 4), pady=8)
        self._reg(self._startup_name_entry, apply_entry)

        self._startup_path_entry = ctk.CTkEntry(
            add_row, placeholder_text=self._i18n.t("startup.path_placeholder"),
            height=28, font=ctk.CTkFont(size=13))
        apply_entry(self._startup_path_entry, s)
        bind_entry_autofit(self._startup_path_entry, min_width=160, max_width=380, padding=36)
        self._startup_path_entry.pack(side="left", padx=(0, 4), pady=8)
        self._reg(self._startup_path_entry, apply_entry)

        self._startup_browse_btn = ctk.CTkButton(
            add_row, text="...", width=30, height=28, font=ctk.CTkFont(size=13),
            command=self._browse_startup_path)
        apply_btn_secondary(self._startup_browse_btn, s)
        self._startup_browse_btn.pack(side="left", padx=(0, 4), pady=8)
        self._reg(self._startup_browse_btn, apply_btn_secondary)

        self._startup_add_btn = ctk.CTkButton(
            add_row, text=self._i18n.t("startup.add_btn"), width=68, height=28,
            font=ctk.CTkFont(size=13), command=self._add_startup_item)
        apply_btn_style(self._startup_add_btn, s)
        fit_button_width(self._startup_add_btn, min_width=60, max_width=110)
        self._startup_add_btn.pack(side="left", padx=(0, 4), pady=8)
        self._reg(self._startup_add_btn, apply_btn_style)

        # Toggle button: "Add This App" / "Remove This App"
        self._startup_add_self_btn = ctk.CTkButton(
            add_row, text=self._i18n.t("startup.add_self_btn"), width=92, height=28,
            font=ctk.CTkFont(size=12), command=self._add_self_to_startup)
        apply_btn_secondary(self._startup_add_self_btn, s)
        fit_button_width(self._startup_add_self_btn, min_width=84, max_width=140)
        self._reg(self._startup_add_self_btn, apply_btn_secondary)

        self._startup_remove_self_btn = ctk.CTkButton(
            add_row, text=self._i18n.t("startup.remove_self_btn"), width=92, height=28,
            font=ctk.CTkFont(size=12), command=self._remove_self_from_startup)
        apply_btn_secondary(self._startup_remove_self_btn, s)
        fit_button_width(self._startup_remove_self_btn, min_width=84, max_width=150)
        self._reg(self._startup_remove_self_btn, apply_btn_secondary)

        # --- Column header ---
        self._header_frame = ctk.CTkFrame(card, height=26)
        apply_surface_corner(self._header_frame, s)
        self._header_frame.pack(fill="x", padx=12, pady=(0, 2))
        self._header_frame.pack_propagate(False)
        self._header_frame.bind("<Configure>", self._on_header_configure)
        self._reg(self._header_frame, apply_surface_corner)

        # Store: key → (label, handle, handle_bind_ids)
        self._col_header_widgets: Dict[str, tuple] = {}
        col_i18n = {"name": "startup.col_name", "path": "startup.col_path",
                     "source": "startup.col_source", "action": "startup.col_action"}

        for i, key in enumerate(COL_KEYS):
            lbl = ctk.CTkLabel(self._header_frame, text=self._i18n.t(col_i18n[key]),
                               font=ctk.CTkFont(size=12, weight="bold"),
                               text_color=s.text_secondary, anchor="w",
                               cursor="hand2" if key in ("name", "source") else None)
            if key in ("name", "source"):
                lbl.bind("<Button-1>", lambda e, k=key: self._toggle_sort(k))
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

        self._relayout_header()

        # --- Scrollable list ---
        self._startup_list_frame = ctk.CTkScrollableFrame(
            card, fg_color="transparent",
            scrollbar_button_color=s.border, scrollbar_button_hover_color=s.border_strong)
        self._startup_list_frame.pack(fill="both", expand=True, padx=12, pady=(0, 8))
        self._startup_list_frame._parent_canvas.configure(yscrollincrement=18,
                                                          xscrollincrement=18)
        self._reg(self._startup_list_frame,
                       lambda w, s: w.configure(scrollbar_button_color=s.border,
                                                scrollbar_button_hover_color=s.border_strong))
        self._table_rows: List[ctk.CTkFrame] = []

        self._startup_card = card
        self._refresh_startup_list()

    # ==================================================================
    # Compact view — minimal power-only window
    # ==================================================================

    def _build_compact_view(self):
        """Build a compact card with only: style, target plan, interval, check."""
        s = self._style
        frame = ctk.CTkFrame(self, fg_color="transparent")
        self._compact_frame = frame

        # Compact card
        card = ctk.CTkFrame(frame)
        apply_card(card, s)
        card.pack(fill="both", expand=True, padx=16, pady=(8, 6))
        self._reg(card, apply_card)

        # Title row with status badge and full-mode button
        title_row = ctk.CTkFrame(card, fg_color="transparent", height=32)
        title_row.pack(fill="x", padx=14, pady=(12, 10))
        title_row.pack_propagate(False)

        self._compact_title = ctk.CTkLabel(
            title_row, text="⚡ " + self._i18n.t("power.title"),
            font=ctk.CTkFont(size=16, weight="bold"), text_color=s.text_primary, anchor="w")
        self._compact_title.pack(side="left")
        self._reg(self._compact_title, lambda w, s: w.configure(text_color=s.text_primary))

        self._compact_full_btn = ctk.CTkButton(
            title_row, text=self._i18n.t("compact.full"), width=56, height=26,
            font=ctk.CTkFont(size=12), command=self._toggle_compact_mode)
        apply_btn_secondary(self._compact_full_btn, s)
        self._compact_full_btn.pack(side="right")
        self._reg(self._compact_full_btn, apply_btn_secondary)
        fit_button_width(self._compact_full_btn, min_width=82, max_width=124, padding=26)

        self._compact_status_badge = ctk.CTkLabel(
            title_row, text="--", font=ctk.CTkFont(size=13, weight="bold"),
            text_color=s.text_secondary, width=88, height=26,
            fg_color=s.surface, corner_radius=13)
        self._compact_status_badge.pack(side="right", padx=(0, 8))
        self._reg(self._compact_status_badge,
                  lambda w, s: w.configure(text_color=s.text_secondary, fg_color=s.surface))

        # Active plan display
        active_row = ctk.CTkFrame(card)
        apply_surface_corner(active_row, s)
        active_row.pack(fill="x", padx=14, pady=(0, 8))
        self._reg(active_row, apply_surface_corner)

        self._compact_active_label = ctk.CTkLabel(
            active_row, text=self._i18n.t("power.active_plan") + ":",
            font=ctk.CTkFont(size=12), text_color=s.text_secondary, anchor="w")
        self._compact_active_label.pack(fill="x", padx=12, pady=(10, 1))
        self._reg(self._compact_active_label, lambda w, s: w.configure(text_color=s.text_secondary))

        self._compact_active_value = ctk.CTkLabel(
            active_row, text="--", font=ctk.CTkFont(size=18, weight="bold"),
            text_color=s.text_primary, anchor="w", wraplength=380)
        self._compact_active_value.pack(fill="x", padx=12, pady=(0, 10))
        self._reg(self._compact_active_value, lambda w, s: w.configure(text_color=s.text_primary))

        # Controls area split into two calmer cards
        controls_row = ctk.CTkFrame(card, fg_color="transparent")
        controls_row.pack(fill="x", padx=14, pady=(0, 14))

        settings_panel = ctk.CTkFrame(controls_row)
        apply_surface_corner(settings_panel, s)
        settings_panel.pack(side="left", fill="both", expand=True, padx=(0, 8))
        self._reg(settings_panel, apply_surface_corner)

        actions_panel = ctk.CTkFrame(controls_row)
        apply_surface_corner(actions_panel, s)
        actions_panel.pack(side="left", fill="both", expand=True, padx=(8, 0))
        self._reg(actions_panel, apply_surface_corner)

        settings_lbl = ctk.CTkLabel(
            settings_panel, text=self._i18n.t("sidebar.appearance"),
            font=ctk.CTkFont(size=12, weight="bold"), text_color=s.text_primary, anchor="w")
        settings_lbl.pack(fill="x", padx=12, pady=(10, 6))
        self._reg(settings_lbl, lambda w, s: w.configure(text_color=s.text_primary))

        self._compact_style_dd = ctk.CTkOptionMenu(
            settings_panel, values=list(STYLES.keys()), font=ctk.CTkFont(size=14),
            height=30, width=150, command=self._switch_style)
        apply_dropdown(self._compact_style_dd, s)
        self._compact_style_dd.set(self.cfg["style"])
        fit_option_width(self._compact_style_dd, list(STYLES.keys()), min_width=124, max_width=190)
        self._compact_style_dd.pack(fill="x", padx=12)
        self._reg(self._compact_style_dd, apply_dropdown)
        _make_dropdown_toggle(self._compact_style_dd)

        target_lbl = ctk.CTkLabel(
            settings_panel, text=self._i18n.t("power.target_plan"),
            font=ctk.CTkFont(size=12, weight="bold"), text_color=s.text_primary, anchor="w")
        target_lbl.pack(fill="x", padx=12, pady=(12, 6))
        self._reg(target_lbl, lambda w, s: w.configure(text_color=s.text_primary))

        self._compact_target_dd = ctk.CTkOptionMenu(
            settings_panel, values=["Auto"], font=ctk.CTkFont(size=14),
            height=30, width=160, dynamic_resizing=False,
            command=self._on_compact_target_changed)
        apply_dropdown(self._compact_target_dd, s)
        self._compact_target_dd.pack(fill="x", padx=12, pady=(0, 12))
        self._reg(self._compact_target_dd, apply_dropdown)
        _make_dropdown_toggle(self._compact_target_dd)

        actions_lbl = ctk.CTkLabel(
            actions_panel, text=self._i18n.t("power.check_now"),
            font=ctk.CTkFont(size=12, weight="bold"), text_color=s.text_primary, anchor="w")
        actions_lbl.pack(fill="x", padx=12, pady=(10, 6))
        self._reg(actions_lbl, lambda w, s: w.configure(text_color=s.text_primary))

        self._compact_check_btn = ctk.CTkButton(
            actions_panel, text=self._i18n.t("power.check_now"),
            height=40, font=ctk.CTkFont(size=14, weight="bold"),
            command=lambda: [self._btn_press_flash(self._compact_check_btn), self._check_now()])
        apply_btn_style(self._compact_check_btn, s)
        self._compact_check_btn.pack(fill="x", padx=12)
        self._reg(self._compact_check_btn, apply_btn_style)

        interval_row = ctk.CTkFrame(actions_panel, fg_color="transparent", height=30)
        interval_row.pack(fill="x", padx=12, pady=(10, 0))
        interval_row.pack_propagate(False)

        int_lbl = ctk.CTkLabel(
            interval_row, text=self._i18n.t("power.interval"),
            font=ctk.CTkFont(size=12), text_color=s.text_secondary, width=58, anchor="w")
        int_lbl.pack(side="left")
        self._reg(int_lbl, lambda w, s: w.configure(text_color=s.text_secondary))

        self._compact_interval_entry = ctk.CTkEntry(
            interval_row, width=52, height=28, font=ctk.CTkFont(size=14))
        apply_entry(self._compact_interval_entry, s)
        self._compact_interval_entry.pack(side="left")
        self._compact_interval_entry.insert(0, str(self.cfg["check_interval"]))
        self._compact_interval_entry.bind("<Return>", lambda e: self._apply_interval())
        self._reg(self._compact_interval_entry, apply_entry)

        s_lbl = ctk.CTkLabel(
            interval_row, text=self._i18n.t("power.seconds"),
            font=ctk.CTkFont(size=12), text_color=s.text_muted, width=18)
        s_lbl.pack(side="left", padx=(3, 6))
        self._reg(s_lbl, lambda w, s: w.configure(text_color=s.text_muted))

        self._compact_apply_btn = ctk.CTkButton(
            interval_row, text=self._i18n.t("general.apply"), width=54, height=28,
            font=ctk.CTkFont(size=12), command=self._apply_interval)
        apply_btn_style(self._compact_apply_btn, s)
        self._compact_apply_btn.pack(side="left")
        self._reg(self._compact_apply_btn, apply_btn_style)

        self._compact_monitor_switch = ctk.CTkSwitch(
            actions_panel, text=self._i18n.t("power.auto_monitor"),
            font=ctk.CTkFont(size=12), command=self._toggle_monitor)
        apply_switch(self._compact_monitor_switch, s)
        self._compact_monitor_switch.pack(anchor="w", padx=12, pady=(10, 12))
        if self.cfg["monitor_enabled"]:
            self._compact_monitor_switch.select()
        self._reg(self._compact_monitor_switch, apply_switch)

        # Result line
        self._compact_result_label = ctk.CTkLabel(
            card, text="", font=ctk.CTkFont(size=12),
            text_color=s.text_secondary, anchor="w", height=20)
        self._compact_result_label.pack(fill="x", padx=14, pady=(0, 8))
        self._reg(self._compact_result_label, lambda w, s: w.configure(text_color=s.text_secondary))

        # Sync dropdowns with current state
        self._populate_compact_target_dropdown()

    def _populate_compact_target_dropdown(self):
        """Sync compact view target dropdown with power plans."""
        plans = get_all_plans()
        values: List[str] = [AUTO_TARGET_LABEL]
        self._compact_target_map: Dict[str, Optional[str]] = {AUTO_TARGET_LABEL: None}
        for p in plans:
            marker = "✓ " if p.is_acceptable else ""
            label = f"{marker}{p.name}"
            values.append(label)
            self._compact_target_map[label] = p.guid
        fit_option_width(self._compact_target_dd, values, min_width=120, max_width=280)
        self._compact_target_dd.configure(values=values)

        saved_guid = self.cfg.get("target_guid", "")
        if saved_guid:
            for p in plans:
                if p.guid == saved_guid:
                    marker = "✓ " if p.is_acceptable else ""
                    self._compact_target_dd.set(f"{marker}{p.name}")
                    return
        self._compact_target_dd.set(AUTO_TARGET_LABEL)

    def _on_compact_target_changed(self, choice: str):
        """Handle target plan change in compact view — sync to full view."""
        self._target_dropdown.set(choice)
        self._on_target_changed(choice)

    def _sync_compact_ui(self):
        """Sync compact view labels with full-view state."""
        if self._compact_frame is None:
            return
        # Active plan
        self._compact_active_value.configure(text=self._power_active_value.cget("text"))
        # Status badge
        self._compact_status_badge.configure(
            text=self._power_status_badge.cget("text"),
            text_color=self._power_status_badge.cget("text_color"))
        # Result
        self._compact_result_label.configure(text=self._check_result_label.cget("text"))
        # Style dropdown
        self._compact_style_dd.set(self._style_dropdown.get())
        # Target plan
        if hasattr(self, "_compact_target_dd") and self._compact_target_dd.winfo_exists():
            self._compact_target_dd.set(self._target_dropdown.get())
        # Interval
        self._compact_interval_entry.delete(0, "end")
        self._compact_interval_entry.insert(0, str(self.cfg["check_interval"]))
        # Monitor switch
        if self._monitor_running:
            self._compact_monitor_switch.select()
        else:
            self._compact_monitor_switch.deselect()
        self._update_mode_switch_texts()

    def _toggle_compact_mode(self):
        """Switch between full window and compact power-only window."""
        self._set_compact_mode(not self._compact_mode)

    def _set_compact_mode(self, compact: bool):
        """Apply compact/full mode without rebuilding widgets."""
        if self._compact_mode == compact:
            self._update_mode_switch_texts()
            return

        self._remember_geometry_for_mode()
        self._compact_mode = compact

        if compact:
            self._sidebar.pack_forget()
            self._main_frame_ref.place_forget()
            self._compact_frame.pack(fill="both", expand=True)
            self.minsize(460, 380)
        else:
            self._compact_frame.pack_forget()
            self._sidebar.pack(side="left", fill="y")
            self._sidebar.pack_propagate(False)
            if self._sidebar_expanded:
                self._main_frame_ref.place(x=SIDEBAR_EXPANDED, y=0, relheight=1.0)
            else:
                self._main_frame_ref.place(x=SIDEBAR_COLLAPSED, y=0, relheight=1.0)
            self._main_frame_ref.lift()
            self.minsize(680, 480)
            self._snap_main_to_sidebar()

        self._update_mode_switch_texts()
        self._apply_geometry_for_mode()
        self._sync_compact_ui()
        self._refresh_power_status()

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
        """Open a popup menu for switching the active power plan."""
        plans = get_all_plans()
        if not plans or not hasattr(self, "_compact_plan_btn"):
            return
        menu = Menu(self, tearoff=0)
        for p in plans:
            label = p.name + (" \u2713" if p.is_active else "")
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
            if self._sidebar_expanded:
                self._main_frame_ref.place(x=SIDEBAR_EXPANDED, y=0, relheight=1.0)
            else:
                self._main_frame_ref.place(x=SIDEBAR_COLLAPSED, y=0, relheight=1.0)
            self._main_frame_ref.lift()
            self.minsize(680, 480)

        self._update_mode_switch_texts()
        self._apply_geometry_for_mode()
        if not compact:
            self.update_idletasks()
            self._relayout_main_frame()
            self.after(0, self._relayout_main_frame)
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
        self._monitor_switch.configure(text=self._i18n.t("power.auto_monitor"))
        self._startup_title_label.configure(text=self._i18n.t("startup.title"))
        self._startup_name_entry.configure(placeholder_text=self._i18n.t("startup.name_placeholder"))
        self._startup_path_entry.configure(placeholder_text=self._i18n.t("startup.path_placeholder"))
        self._startup_add_btn.configure(text=self._i18n.t("startup.add_btn"))
        self._startup_add_self_btn.configure(text=self._i18n.t("startup.add_self_btn"))
        self._startup_remove_self_btn.configure(text=self._i18n.t("startup.remove_self_btn"))
        self._startup_refresh_btn.configure(text=self._i18n.t("startup.refresh_btn"))
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
        fit_option_width(self._style_dropdown, list(STYLES.keys()), min_width=118,
                         max_width=SIDEBAR_EXPANDED - 24)
        fit_option_width(self._lang_dropdown, [LANG_LABELS[l] for l in SUPPORTED_LANGS],
                         min_width=96, max_width=SIDEBAR_EXPANDED - 24)
        fit_entry_width(self._interval_entry, min_width=52, max_width=92, padding=30)
        fit_entry_width(self._startup_name_entry, min_width=112, max_width=190, padding=34)
        fit_entry_width(self._startup_path_entry, min_width=180, max_width=430, padding=38)
        fit_button_width(self._apply_interval_btn, min_width=56, max_width=96)
        fit_button_width(self._check_btn, min_width=92, max_width=180)
        fit_button_width(self._startup_refresh_btn, min_width=62, max_width=120)
        fit_button_width(self._startup_add_btn, min_width=70, max_width=126)
        fit_button_width(self._startup_add_self_btn, min_width=92, max_width=160)
        fit_button_width(self._startup_remove_self_btn, min_width=92, max_width=170)
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
        # During drag: only update header (cheap, just 8 widgets)
        self._relayout_header()

    def _end_resize(self):
        self._resizing_col = -1
        # Only relayout all rows once, when drag ends
        self._relayout_all_rows()
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

        # Hover glow — elevate bg and add accent border
        def on_enter(e):
            row.configure(fg_color=s.card_elevated, border_color=s.accent, border_width=1)
        def on_leave(e):
            row.configure(fg_color=s.surface, border_color=s.surface, border_width=0)
        row.bind("<Enter>", on_enter)
        row.bind("<Leave>", on_leave)

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
            fg_color=s.card_elevated, hover_color=s.error,
            text_color=s.text_secondary, corner_radius=3,
            command=lambda it=item: self._remove_startup_item(it))
        self._reg(remove_btn, lambda w, s: w.configure(
            fg_color=s.card_elevated, hover_color=s.error,
            text_color=s.text_secondary))
        return row

    def _toggle_sort(self, key: str):
        """Toggle sort direction when clicking a sortable column header."""
        if self._sort_key == key:
            self._sort_ascending = not self._sort_ascending
        else:
            self._sort_key = key
            self._sort_ascending = True
        self._update_sort_indicator()
        self._refresh_startup_list()

    def _update_sort_indicator(self):
        """Update column header labels to show sort direction (▲/▼)."""
        col_i18n = {"name": "startup.col_name", "path": "startup.col_path",
                     "source": "startup.col_source", "action": "startup.col_action"}
        for key, (lbl, _, _) in self._col_header_widgets.items():
            base = self._i18n.t(col_i18n.get(key, key))
            if key == self._sort_key:
                arrow = " ▲" if self._sort_ascending else " ▼"
                lbl_text = base + arrow
            else:
                lbl_text = base
            lbl.configure(text=lbl_text)

    def _refresh_startup_list(self):
        for w in self._startup_list_frame.winfo_children():
            w.destroy()
        self._table_rows.clear()
        self._item_rows.clear()
        # Clean stale startup row registrations from _stylables
        self._stylables = [(w, fn) for w, fn in self._stylables if w.winfo_exists()]

        items = get_all_items()
        items.sort(key=lambda it: ''.join(
            lazy_pinyin(getattr(it, self._sort_key, ""), style=Style.TONE3)
        ), reverse=not self._sort_ascending)
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
            fit_entry_width(self._startup_path_entry, min_width=180,
                            max_width=430, padding=38)

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
            self._startup_remove_self_btn.pack(side="left", padx=(0, 10), pady=10)
        else:
            self._startup_remove_self_btn.pack_forget()
            self._startup_add_self_btn.pack(side="left", padx=(0, 10), pady=10)

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
        values: List[str] = [AUTO_TARGET_LABEL]
        self._target_guid_map.clear()
        self._target_guid_map[AUTO_TARGET_LABEL] = None
        for p in plans:
            marker = "✓ " if p.is_acceptable else ""
            label = f"{marker}{p.name}"
            values.append(label)
            self._target_guid_map[label] = p.guid
        fit_option_width(self._target_dropdown, values, min_width=110, max_width=300)
        self._target_dropdown.configure(values=values)
        if current in values:
            self._target_dropdown.set(current)
            if self._compact_mode:
                self._populate_compact_target_dropdown()
            return

        target_guid = self.power_monitor.target_guid or self.cfg.get("target_guid", "")
        if target_guid:
            for label, guid in self._target_guid_map.items():
                if guid == target_guid:
                    self._target_dropdown.set(label)
                    if self._compact_mode:
                        self._populate_compact_target_dropdown()
                    return
        self._target_dropdown.set(AUTO_TARGET_LABEL)
        if self._compact_mode:
            self._populate_compact_target_dropdown()

    def _update_power_ui(self, active):
        s = self._style
        if active is None:
            self._power_active_value.configure(text=self._i18n.t("power.unable_detect"))
            self._power_status_badge.configure(
                text=self._i18n.t("power.status_error"), text_color=s.error)
            if self._compact_mode and hasattr(self, "_compact_active_value") and self._compact_active_value.winfo_exists():
                self._compact_active_value.configure(text=self._i18n.t("power.unable_detect"))
            return
        self._power_active_value.configure(text=active.name)

        # Check if active plan matches the selected target
        target_guid = self.power_monitor.target_guid
        if target_guid:
            ok = (active.guid == target_guid)
        else:
            ok = is_acceptable_plan(active.name)

        prev = self._power_status_badge.cget("text")
        if ok:
            txt, clr = self._i18n.t("power.status_ok"), s.success
        else:
            txt, clr = self._i18n.t("power.status_needs_fix"), s.warning
        self._power_status_badge.configure(text=txt, text_color=clr)
        if prev and prev != txt:
            self._status_pulse(self._power_status_badge, clr)
        self._rebuild_tray_menu()

        # Sync compact view
        if self._compact_mode and hasattr(self, "_compact_active_value") and self._compact_active_value.winfo_exists():
            self._compact_active_value.configure(text=active.name)

    def _do_switch_plan(self, guid: str):
        """Switch to the given power plan guid."""
        from power_manager import set_active_plan
        def _switch():
            success = set_active_plan(guid)
            if success:
                self.after(0, self._refresh_power_status)
            else:
                self.after(0, lambda: self._show_check_result(
                    self._i18n.t("power.switch_failed", name=""),
                    "error", persistent=True))
        threading.Thread(target=_switch, daemon=True).start()

    def _check_now(self):
        self._show_check_result(self._i18n.t("power.status_checking"), "info", persistent=True)
        def _do():
            result = self.power_monitor.check_now()
            self.after(0, lambda: self._refresh_power_status())
            self.after(0, lambda: self._show_result_persistent(result))
        threading.Thread(target=_do, daemon=True).start()

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
            self._show_check_result(msg, "ok", persistent=True)
            if notify:
                self._tray_notify(title, result.get('plan_name', '') + " → " + result.get('target_name', ''))
        elif status == "fix_failed":
            msg = f"[{ts}] {self._i18n.t('power.switch_failed', name=result.get('plan_name', ''))}"
            self._show_check_result(msg, "error", persistent=True)
            if notify:
                self._tray_notify(title, self._i18n.t('power.switch_failed', name=result.get('plan_name', '')))
        elif status == "no_target":
            msg = f"[{ts}] {self._i18n.t('power.no_target')}"
            self._show_check_result(msg, "error", persistent=True)
            if notify:
                self._tray_notify(title, self._i18n.t('power.no_target'))
        else:
            msg = f"[{ts}] {status}: {result.get('plan_name', '')}"
            self._show_check_result(msg, "error", persistent=True)
            if notify:
                self._tray_notify(title, msg)

    def _on_power_status_change(self, result: dict):
        if self._window_visible:
            self.after(0, lambda: self._refresh_power_status())
            self.after(0, lambda: self._show_result_persistent(result, notify=False))

    def _show_check_result(self, msg: str, kind: str = "info", persistent: bool = False):
        s = self._style
        colors = {
            "ok": s.success,
            "error": s.error,
            "warning": s.warning,
            "info": s.info,
        }
        # Truncate to single line to avoid layout shift
        if len(msg) > 60:
            msg = msg[:57] + "..."
        self._check_result_label.configure(
            text=msg, text_color=colors.get(kind, self._style.text_secondary))
        compact_result = getattr(self, "_compact_result_label", None)
        if compact_result is not None and compact_result.winfo_exists():
            compact_result.configure(
                text=msg, text_color=colors.get(kind, self._style.text_secondary))
        if not persistent:
            self._status_bar.configure(
                text=msg, text_color=colors.get(kind, self._style.text_secondary))
            self.after(8000, lambda: self._status_bar.configure(text=""))

    def _apply_interval(self):
        # Read from the right entry depending on mode
        entry = self._compact_interval_entry if self._compact_mode else self._interval_entry
        try:
            secs = int(entry.get().strip())
            secs = max(10, min(3600, secs))
            entry.delete(0, "end")
            entry.insert(0, str(secs))
            self.power_monitor.interval = secs
            self.cfg["check_interval"] = secs
            save_config(self.cfg)
            # Sync other entry
            other = self._interval_entry if self._compact_mode else self._compact_interval_entry
            if other.winfo_exists():
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
        self._syncing_monitor_switch = True
        try:
            main_on = self._monitor_switch.get()
            compact_switch = getattr(self, "_compact_monitor_switch", None)
            compact_on = (compact_switch is not None
                          and compact_switch.winfo_exists()
                          and compact_switch.get())
            if main_on or compact_on:
                self._start_monitor()
            else:
                self._stop_monitor()
            if self._monitor_running:
                self._monitor_switch.select()
                if compact_switch is not None and compact_switch.winfo_exists():
                    compact_switch.select()
            else:
                self._monitor_switch.deselect()
                if compact_switch is not None and compact_switch.winfo_exists():
                    compact_switch.deselect()
        finally:
            self._syncing_monitor_switch = False

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

            def _switch_plan(icon, item):
                plan_name = item.text.rstrip(" ✓")
                plans = get_all_plans()
                for p in plans:
                    if p.name == plan_name:
                        self.after(0, lambda g=p.guid: self._do_switch_plan(g))
                        break

            self._tray_icon = pystray.Icon(
                "pc_auto_scripts", icon_img, "PC System Auto Scripts")
            self._tray_thread = threading.Thread(target=self._tray_icon.run, daemon=True)
            self._tray_thread.start()

            self._rebuild_tray_menu()
            return True
        except Exception:
            return False

    def _rebuild_tray_menu(self):
        """Rebuild the tray menu to reflect current power plan state."""
        if self._tray_icon is None:
            return
        try:
            import pystray

            def on_show(icon, item):
                self._window_visible = True
                self.after(0, self.deiconify)

            def on_exit(icon, item):
                self._closing = True
                icon.stop()
                self.after(0, self._force_quit)

            def _switch_plan(icon, item):
                plan_name = item.text.rstrip(" ✓")
                plans = get_all_plans()
                for p in plans:
                    if p.name == plan_name:
                        self.after(0, lambda g=p.guid: self._do_switch_plan(g))
                        break

            plans = get_all_plans()
            plan_items = []
            for p in plans:
                label = p.name + ("  ✓" if p.is_active else "")
                plan_items.append(
                    pystray.MenuItem(label, _switch_plan))
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
                                 lambda: self.after(0, self._check_now)),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem(self._i18n.t("tray.exit"), on_exit),
            )
            self._tray_icon.menu = menu
            self._tray_icon.update_menu()
        except Exception:
            pass

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
