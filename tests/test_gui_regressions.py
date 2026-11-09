"""GUI-level regression tests: real Tk window, but every system touch is mocked.

These prove the UI wiring the handoff report called out: pure reads, GUID-bound
selection, confirm-gated removal, preference-preserving shutdown, and the
sorted/filtered list refresh. No registry key, startup file, or power scheme is
ever touched.

Run:  python -m unittest discover -s tests -v
"""

from __future__ import annotations

import ctypes
import sys
import queue
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    import customtkinter  # noqa: F401
    import tkinter  # noqa: F401
    _GUI_IMPORT_ERROR = ""
except Exception as exc:  # pragma: no cover - environment dependent
    _GUI_IMPORT_ERROR = str(exc)

if not _GUI_IMPORT_ERROR:
    import main as main_module
    import power_manager as pm
    import startup_manager as sm


def setUpModule():
    if not _GUI_IMPORT_ERROR and sys.platform == "win32":
        from tests.isolated_desktop import enter_isolated_desktop
        enter_isolated_desktop()

BALANCED_GUID = "381b4222-f694-41f0-9685-ff5bb260df2e"
HIGH_PERF_GUID = "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c"
MISSING_GUID = "deadbeef-0000-0000-0000-000000000000"


def _plans():
    return [
        pm.PowerPlan(guid=BALANCED_GUID, name="平衡", is_active=True),
        pm.PowerPlan(guid=HIGH_PERF_GUID, name="高性能", is_acceptable=True),
    ]


@unittest.skipIf(bool(_GUI_IMPORT_ERROR), f"GUI dependencies unavailable: {_GUI_IMPORT_ERROR}")
class GuiRegressionTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.saved = []
        self.created = []
        self.switched = []
        self.registry = {}

        self.snapshot = pm.PlanSnapshot(plans=_plans())
        self.confirm_answer = True
        self.confirm_calls = []

        self._patches = [
            mock.patch.object(main_module, "load_config", side_effect=self._load_config),
            mock.patch.object(main_module, "save_config", side_effect=self._save_config),
            mock.patch.object(main_module, "DATA_DIR", self.root),
            mock.patch.object(main_module, "ICON_FILE", self.root / "app_icon.ico"),
            mock.patch.object(main_module, "_create_tray_icon", return_value=object()),
            mock.patch.object(main_module, "get_power_snapshot", side_effect=lambda: self.snapshot),
            mock.patch.object(pm, "get_power_snapshot", side_effect=lambda: self.snapshot),
            mock.patch.object(main_module, "get_all_items", side_effect=self._get_all_items),
            mock.patch.object(pm, "switch_active_plan", side_effect=self._set_active_plan),
            mock.patch.object(main_module, "create_missing_builtin_schemes",
                              side_effect=self._create_schemes),
            mock.patch.object(main_module, "remove_with_backup", side_effect=self._remove_with_backup),
            mock.patch.object(main_module, "restore_backup", return_value=True),
            mock.patch.object(main_module, "get_registry_startup_command", side_effect=self._get_current_command),
            mock.patch.object(main_module, "list_backups", side_effect=lambda *a, **k: []),
            mock.patch.object(main_module, "add_registry_startup",
                              side_effect=self._add_registry_startup),
            mock.patch.object(main_module.App, "_setup_tray", return_value=False),
            mock.patch.object(main_module, "_start_ipc_server", return_value=True),
            # Confirm dialogs are exercised for content; here we auto-answer.
            mock.patch.object(main_module.App, "_ask_confirm", side_effect=self._confirm),
            mock.patch.object(main_module.App, "_show_first_run_setup", return_value=None),
        ]
        for patch in self._patches:
            patch.start()
            self.addCleanup(patch.stop)

        self.app = main_module.App()
        self.app.deiconify()
        self.assertTrue(self._wait_for(
            lambda: self.app._startup_ready and HIGH_PERF_GUID in self.app._label_by_guid, 4.0))

    def tearDown(self):
        try:
            self.app.power_monitor.stop()
            self.app.power_monitor.wait_until_stopped(2.0)
            self._destroy_app()
        except Exception:
            pass
        self._tmp.cleanup()

    def _destroy_app(self):
        self.app._closing = True
        for callback in self.app.tk.splitlist(self.app.tk.call("after", "info")):
            self.app.after_cancel(callback)
        self.app.destroy()

    # --- helpers / test doubles -------------------------------------------

    def _load_config(self):
        cfg = dict(main_module.CONFIG_DEFAULTS)
        cfg["start_to_tray"] = False
        cfg["monitor_enabled"] = False
        cfg["setup_completed"] = True
        return cfg

    def _save_config(self, cfg):
        self.saved.append(dict(cfg))
        return True, ""

    def _confirm(self, title, body, danger=False):
        self.confirm_calls.append({"title": title, "body": body, "danger": danger})
        return self.confirm_answer

    def _get_all_items(self):
        return list(self.registry.values())

    def _set_active_plan(self, guid, can_activate=None):
        if can_activate is not None and not can_activate():
            return pm.SwitchResult(ok=False, error="cancelled")
        self.switched.append(guid)
        return pm.SwitchResult(ok=True)

    def _create_schemes(self, plans=None):
        self.created.append(plans)
        return [pm.SchemeCreateResult(
            name="卓越性能", ok=True, guid="e9a42b02-d5df-448d-aa00-03f14749eb61")]

    def _remove_with_backup(self, item, data_root=None):
        self.registry.pop(item.identity(), None)
        return "removed"

    def _get_current_command(self, name):
        item = self.registry.get(sm.normalize_key("registry", name))
        return item.path if item else None

    def _add_registry_startup(self, name, path, arguments="", overwrite=False, expected_command=None):
        key = sm.normalize_key("registry", name)
        existing = self.registry.get(key)
        if existing is not None and not overwrite:
            return "exists"
        if overwrite and self._get_current_command(name) != expected_command:
            return "stale"
        self.registry[key] = sm.StartupItem(name=name, path=path, source="registry")
        return "overwritten" if existing is not None else "created"

    def _pump(self, seconds: float):
        deadline = time.time() + seconds
        while time.time() < deadline:
            self.app.update()
            time.sleep(0.01)

    def _wait_for(self, predicate, timeout: float = 3.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            self.app.update()
            if predicate():
                return True
            time.sleep(0.02)
        self.app.update()
        return bool(predicate())

    def _status_text(self) -> str:
        return (self.app._check_result_label.cget("text") or "")

    # --- P1.5: reads never modify the system ------------------------------

    def test_refresh_never_modifies_system(self):
        self.switched.clear()
        self.created.clear()
        self.app._refresh_power_status()
        self.app._refresh_startup_list(force=True)
        self._pump(0.4)
        self.assertEqual(self.switched, [], "a plain refresh must not call setactive")
        self.assertEqual(self.created, [], "a plain refresh must not call duplicatescheme")

    def test_repeated_check_clicks_share_one_operation(self):
        release = threading.Event()
        calls = queue.Queue()

        def slow_check():
            calls.put(True)
            release.wait(3)
            return {"status": "ok"}

        with mock.patch.object(self.app.power_monitor, "check_now", side_effect=slow_check):
            try:
                self.app._check_now()
                self.app._check_now()
                calls.get(timeout=1)
                with self.assertRaises(queue.Empty):
                    calls.get(timeout=0.2)
                self.app._nav_buttons["startup"].invoke()
                self.app.update()
                self.assertTrue(self.app._pages["startup"].winfo_ismapped())
                self.assertEqual(self.app._check_btn.cget("state"), "disabled")
            finally:
                release.set()
            self.assertTrue(self._wait_for(lambda: self.app._check_btn.cget("state") == "normal"))

    def test_chinese_only_and_theme_picker_present(self):
        self.switched.clear()
        self.created.clear()
        self.assertEqual(self.app._i18n.lang, "zh")
        self.assertEqual(self.app._style.name, "System")
        # English was dropped, so no language control exists; the theme picker
        # does, because six design systems ship.
        self.assertFalse(hasattr(self.app, "_lang_dropdown"))
        self.assertTrue(hasattr(self.app, "_style_dropdown"))
        self.app._nav_buttons["settings"].invoke()
        self.app.update()
        self.assertTrue(self.app._style_dropdown.winfo_ismapped())
        self.app._switch_style("Rem")
        self.assertEqual(self.app.cfg["ui_theme"], "Rem")
        self.app._nav_buttons["power"].invoke()
        self.assertEqual(self.switched, [])
        self.assertEqual(self.created, [])

    def test_creation_only_happens_on_explicit_action(self):
        self.app._create_missing_schemes()
        self.assertTrue(self._wait_for(lambda: bool(self.created), 2.0),
                        "the explicit button should create schemes")
        self.assertEqual(self.switched, [])

    # --- P1.6: GUID-bound target selection --------------------------------

    def test_dropdown_maps_labels_to_guids(self):
        values = self.app._build_target_labels(_plans())
        self.assertEqual(values[0], main_module.AUTO_TARGET_LABEL)
        self.assertIsNone(self.app._target_guid_map[main_module.AUTO_TARGET_LABEL])
        self.assertEqual(self.app._target_guid_map[values[1]], BALANCED_GUID)
        self.assertEqual(self.app._target_guid_map[values[2]], HIGH_PERF_GUID)

    def test_same_named_plans_get_distinct_labels(self):
        plans = [
            pm.PowerPlan(guid=HIGH_PERF_GUID, name="高性能", is_acceptable=True),
            pm.PowerPlan(guid=BALANCED_GUID, name="高性能"),
        ]
        values = self.app._build_target_labels(plans)
        self.assertEqual(len(set(values)), 3, "duplicate names must be disambiguated")
        self.assertEqual(self.app._target_guid_map[values[1]], HIGH_PERF_GUID)
        self.assertEqual(self.app._target_guid_map[values[2]], BALANCED_GUID)

    def test_selecting_a_plan_binds_its_guid(self):
        label = self.app._label_by_guid[HIGH_PERF_GUID]
        self.app._on_target_changed(label)
        self.assertEqual(self.app.power_monitor.target_guid, HIGH_PERF_GUID)
        self.assertEqual(self.app.cfg["target_guid"], HIGH_PERF_GUID)
        self.assertEqual(self.switched, [], "changing monitor policy must not switch the active plan")
        self.app._do_switch_plan(HIGH_PERF_GUID)
        self.assertTrue(self._wait_for(lambda: HIGH_PERF_GUID in self.switched, 2.0))

    def test_missing_target_is_surfaced_not_silently_replaced(self):
        self.app.cfg["target_guid"] = MISSING_GUID
        self.app.power_monitor.target_guid = MISSING_GUID
        self.app._refresh_power_status()
        self.assertTrue(self._wait_for(lambda: "DEADBEE" in self._status_text().upper(), 2.0),
                        f"the dead target must be reported, got: {self._status_text()!r}")
        self.assertEqual(self.app.power_monitor.target_guid, MISSING_GUID,
                         "the monitor must not silently adopt another target")
        self.assertIn("DEADBEE", self.app._target_dropdown.get().upper(),
                      "the dead target should stay visible in the dropdown")

    # --- P1.1: monitor preference ------------------------------------------

    def test_stopping_the_monitor_persists_the_preference(self):
        self.app._toggle_monitor_switch_for_test(True)
        self._wait_for(lambda: self.app._monitor_switch.get() == 1, 2.0)
        self.assertTrue(self.saved and self.saved[-1]["monitor_enabled"])

        self.app._toggle_monitor_switch_for_test(False)
        self._wait_for(lambda: self.app._monitor_switch.get() == 0, 2.0)
        self.assertFalse(self.saved[-1]["monitor_enabled"],
                         "turning the monitor off must be remembered")

    def test_quitting_does_not_rewrite_the_preference(self):
        self.app.cfg["monitor_enabled"] = True
        self.app._start_monitor(save=False)
        self._wait_for(lambda: self.app._monitor_switch.get() == 1, 2.0)
        before = [dict(entry) for entry in self.saved]
        self.app._stop_monitor(save=False)
        self._pump(0.3)
        self.assertEqual(self.saved, before,
                         "shutdown must not save monitor_enabled=false")
        self.assertTrue(self.app.cfg["monitor_enabled"])

    def test_monitor_switch_follows_real_state(self):
        self.app._start_monitor(save=False)
        self.assertTrue(self._wait_for(lambda: self.app._monitor_switch.get() == 1, 2.0),
                        "the switch must follow a really running worker")
        self.app._stop_monitor(save=False)
        self.assertTrue(self._wait_for(lambda: self.app._monitor_switch.get() == 0, 2.0),
                        "the switch must follow a stopped worker")

    def test_rapid_monitor_toggling_leaves_one_worker(self):
        threads = set()
        for _ in range(8):
            self.app._start_monitor(save=False)
            self._pump(0.05)
            if self.app.power_monitor.worker_thread is not None:
                threads.add(self.app.power_monitor.worker_thread)
            self.app._stop_monitor(save=False)
            self._pump(0.05)
            alive = [thread for thread in threads if thread.is_alive()]
            self.assertLessEqual(len(alive), 1, "at most one worker may be alive")
        self.assertTrue(self.app.power_monitor.wait_until_stopped(5.0))
    # --- P1.3 / P1.4: startup items ---------------------------------------

    def test_removal_asks_for_confirmation_with_details(self):
        item = sm.StartupItem(name="demo", path=r"C:\demo\demo.cmd", source="startup_folder")
        self.registry[item.identity()] = item
        self.confirm_answer = False
        self.app._remove_startup_item(item)
        self.assertTrue(self._wait_for(lambda: bool(self.confirm_calls)))
        self.assertEqual(len(self.confirm_calls), 1)
        body = self.confirm_calls[0]["body"]
        self.assertIn("demo", body)
        self.assertIn(r"C:\demo\demo.cmd", body)
        self.assertIn(item.identity(), self.registry, "a cancelled removal must not delete")

    def test_confirmed_removal_deletes_only_that_item(self):
        cmd = sm.StartupItem(name="demo", path=r"C:\demo\demo.cmd", source="startup_folder")
        lnk = sm.StartupItem(name="demo", path=r"C:\demo\demo.lnk", source="startup_folder")
        self.registry[cmd.identity()] = cmd
        self.registry[lnk.identity()] = lnk
        self.confirm_answer = True
        self.app._remove_startup_item(cmd)
        self.assertTrue(self._wait_for(lambda: cmd.identity() not in self.registry))
        self.assertNotIn(cmd.identity(), self.registry)
        self.assertIn(lnk.identity(), self.registry, "the same-named .lnk must survive")

    def test_unknown_source_is_refused(self):
        item = sm.StartupItem(name="x", path="x", source="mystery")
        self.app._remove_startup_item(item)
        self._pump(0.1)
        self.assertEqual(self.confirm_calls, [], "an unknown source must be refused outright")

    def test_failed_backup_leaves_entry_and_explains_why(self):
        item = sm.StartupItem(name="demo", path=r"C:\demo.exe", source="registry")
        self.registry[item.identity()] = item
        with mock.patch.object(main_module, "remove_with_backup", return_value="backup_failed"):
            self.app._remove_startup_item(item)
            self.assertTrue(self._wait_for(lambda: "备份失败" in self._status_text()))
        self.assertIn(item.identity(), self.registry)

    def _fill_startup_form(self, name, path):
        self.app._nav_buttons["startup"].invoke()
        self.app._startup_add_toggle.invoke()
        self.app._startup_name_entry.insert(0, name)
        self.app._startup_path_entry.insert(0, path)
        self.app._startup_add_btn.invoke()

    def test_added_item_follows_current_sort_and_filter(self):
        self.registry["registry:zeta"] = sm.StartupItem(
            name="zeta", path=r"C:\z\zeta.exe", source="registry")
        self.app._refresh_startup_list(force=True)
        self._wait_for(lambda: len(self.app._startup_items_cache) == 1, 2.0)

        self._fill_startup_form("alpha", r"C:\a\alpha.exe")
        self.assertTrue(self._wait_for(
            lambda: len(self.app._startup_items_cache) == 2, 2.0),
            "the new item must appear in the cache")

        self.assertEqual(self._visible_startup_names(), ["alpha", "zeta"])
        self.assertTrue(self.app._startup_count_label.cget("text").startswith("2"))

    def test_overwriting_does_not_add_a_duplicate_row(self):
        self.registry["registry:app"] = sm.StartupItem(
            name="app", path=r"C:\old.exe", source="registry")
        self.app._refresh_startup_list(force=True)
        self._wait_for(lambda: len(self.app._startup_items_cache) == 1, 2.0)

        self.confirm_answer = True
        self._fill_startup_form("app", r"C:\new.exe")
        self.assertTrue(self._wait_for(lambda: self.app._startup_table.set("registry:app", "path")
                                       == r"C:\new.exe"))
        self.assertEqual(self._visible_startup_names(), ["app"])
        self.assertEqual(self.confirm_calls[0]["body"].count(r"C:\old.exe"), 1,
                         "the prompt must show the command being replaced")

    def test_filter_hides_items_from_other_sources(self):
        self.registry["registry:a"] = sm.StartupItem(name="a", path="a", source="registry")
        self.registry["startup_folder:b"] = sm.StartupItem(
            name="b", path=str(self.root / "b.cmd"), source="startup_folder")
        self.app._refresh_startup_list(force=True)
        self._wait_for(lambda: len(self.app._startup_items_cache) == 2, 2.0)
        self.app._on_source_filter_changed(self.app._format_startup_source("registry"))
        self.assertEqual(self._visible_startup_names(), ["a"],
                         "a HKLM/HKCU filter must not show folder items")

    def test_startup_source_popup_stays_compact_after_refresh(self):
        self.registry["registry:a"] = sm.StartupItem(name="a", path="a", source="registry")
        self.registry["startup_folder:b"] = sm.StartupItem(
            name="b", path=str(self.root / "b.cmd"), source="startup_folder")
        self.app._refresh_startup_list(force=True)
        self.assertTrue(self._wait_for(lambda: len(self.app._startup_items_cache) == 2))
        self.app._nav_buttons["startup"].invoke()
        self.app.update()

        dropdown = self.app._source_filter_dropdown
        menu = dropdown._dropdown_menu
        opened = []
        self.app.after(100, lambda: opened.append(ctypes.windll.user32.EndMenu()))
        dropdown._canvas.event_generate("<Button-1>", x=4, y=4)
        self.assertEqual(opened, [1], "a real native menu must have opened")
        labels = [menu.entrycget(index, "label") for index in range(menu.index("end") + 1)]
        self.assertEqual(labels, dropdown.cget("values"), "displayed entries must not be space padded")
        scale = customtkinter.ScalingTracker.get_widget_scaling(dropdown)
        self.assertLessEqual(menu.yposition(1) - menu.yposition(0), round(21 * scale))

        label = self.app._format_startup_source("registry")
        menu.invoke(labels.index(label))
        self.assertEqual(dropdown.get(), label)
        self.assertEqual(self._visible_startup_names(), ["a"])

    def test_overwrite_prompt_can_be_declined(self):
        self.registry["registry:app"] = sm.StartupItem(
            name="app", path=r"C:\old.exe", source="registry")
        self.app._refresh_startup_list(force=True)
        self._wait_for(lambda: len(self.app._startup_items_cache) == 1, 2.0)

        self.confirm_answer = False
        self._fill_startup_form("app", r"C:\new.exe")
        self.assertTrue(self._wait_for(lambda: bool(self.confirm_calls)))
        self.assertEqual(len(self.confirm_calls), 1)
        self.assertIn(r"C:\old.exe", self.confirm_calls[0]["body"])
        self.assertEqual(self.registry["registry:app"].path, r"C:\old.exe",
                         "declining must leave the old command in place")

    # --- layout: content must fill the window (no blank strip) -------------

    def test_main_window_is_small_with_one_row_of_tabs(self):
        self.app.update()
        buttons = []

        def collect(parent):
            for child in parent.winfo_children():
                if child.winfo_ismapped():
                    if isinstance(child, customtkinter.CTkButton):
                        buttons.append(child.cget("text"))
                    collect(child)

        collect(self.app)
        self.assertNotIn("精简", buttons)
        width, height = self.app.geometry().split("+", 1)[0].split("x")
        self.assertLessEqual(int(width), 620)
        self.assertLessEqual(int(height), 370)
        tabs = tuple(self.app._nav_buttons.values())
        self.assertEqual(len({tab.winfo_rooty() for tab in tabs}), 1)
        self.assertEqual(len({tab.winfo_rootx() for tab in tabs}), 3)

    def _visible_startup_names(self):
        table = self.app._startup_table
        return [table.set(row, "name") for row in table.get_children()]

    def _click_header(self, column):
        table = self.app._startup_table
        index = main_module.COL_KEYS.index(column)
        x = sum(table.column(name, "width") for name in main_module.COL_KEYS[:index]) + 14
        table.event_generate("<ButtonPress-1>", x=x, y=10)
        table.event_generate("<ButtonRelease-1>", x=x, y=10)
        self.app.update()

    def test_clicking_name_header_reverses_visible_rows(self):
        for name in ("zeta", "alpha", "beta"):
            item = sm.StartupItem(name=name, path=name, source="registry")
            self.registry[item.identity()] = item
        self.app._nav_buttons["startup"].invoke()
        self.app._refresh_startup_list(force=True)
        self.assertTrue(self._wait_for(
            lambda: self._visible_startup_names() == ["alpha", "beta", "zeta"]))
        self._click_header("name")
        self.assertEqual(self._visible_startup_names(), ["zeta", "beta", "alpha"])
        self.assertIn("↓", self.app._startup_table.heading("name", "text"))
        self._click_header("source")
        self.assertIn("↑", self.app._startup_table.heading("source", "text"))

    def test_tab_round_trip_preserves_window_filter_and_selection(self):
        for name in ("alpha", "beta"):
            item = sm.StartupItem(name=name, path=name, source="registry")
            self.registry[item.identity()] = item
        self.app._nav_buttons["startup"].invoke()
        self.app._refresh_startup_list(force=True)
        self.assertTrue(self._wait_for(lambda: self._visible_startup_names() == ["alpha", "beta"]))
        self.app._startup_query.set("bet")
        self.assertTrue(self._wait_for(lambda: self._visible_startup_names() == ["beta"]))
        self.app._startup_table.selection_set("registry:beta")
        geometry = self.app.geometry()
        self.app._nav_buttons["settings"].invoke()
        self.assertTrue(self._wait_for(lambda: self.app._style_dropdown.winfo_ismapped()))
        self.app._nav_buttons["startup"].invoke()
        self.assertTrue(self._wait_for(lambda: self.app._startup_table.winfo_ismapped()))
        self.assertEqual(self.app.geometry(), geometry)
        self.assertEqual(self._visible_startup_names(), ["beta"])
        self.assertEqual(self.app._startup_table.selection(), ("registry:beta",))

    def test_page_controls_fit_at_minimum_window_size(self):
        width, height = main_module.FULL_MIN_SIZE
        self.assertLessEqual(width, 550)
        self.assertLessEqual(height, 340)
        self.app.geometry(f"{width}x{height}")
        controls = {
            "power": (self.app._check_btn, self.app._power_refresh_btn,
                      self.app._power_switch_btn, self.app._create_schemes_btn,
                      self.app._target_dropdown, self.app._apply_interval_btn),
            "startup": (self.app._startup_refresh_btn, self.app._startup_restore_btn,
                        self.app._startup_add_toggle, self.app._startup_table,
                        self.app._startup_delete_btn, self.app._startup_open_btn),
            "settings": (self.app._style_dropdown, self.app._startup_self_btn),
        }
        for key, widgets in controls.items():
            self.app._nav_buttons[key].invoke()
            self.app.update()
            page = self.app._pages[key]
            self.assertGreaterEqual(page.winfo_width(), 400)
            for widget in widgets:
                self._assert_inside_page(widget, page)

    def _assert_inside_page(self, widget, page):
        self.assertTrue(widget.winfo_ismapped(), widget)
        self.assertGreaterEqual(widget.winfo_rootx(), page.winfo_rootx(), widget)
        self.assertGreaterEqual(widget.winfo_rooty(), page.winfo_rooty(), widget)
        self.assertLessEqual(widget.winfo_rootx() + widget.winfo_width(),
                             page.winfo_rootx() + page.winfo_width(), widget)
        self.assertLessEqual(widget.winfo_rooty() + widget.winfo_height(),
                             page.winfo_rooty() + page.winfo_height(), widget)

    def test_add_form_only_uses_table_space_when_expanded(self):
        width, height = main_module.FULL_MIN_SIZE
        self.app.geometry(f"{width}x{height}")
        self.app._nav_buttons["startup"].invoke()
        self.app.update()
        table = self.app._startup_table
        full_height = table.winfo_height()
        self.assertFalse(self.app._startup_name_entry.winfo_ismapped())
        self.app._startup_add_toggle.invoke()
        self.app.update()
        for widget in (self.app._startup_name_entry, self.app._startup_path_entry,
                       self.app._startup_browse_btn, self.app._startup_add_btn, table):
            self._assert_inside_page(widget, self.app._pages["startup"])
        self.assertLess(table.winfo_height(), full_height)
        self.app._startup_add_toggle.invoke()
        self.app.update()
        self.assertEqual(table.winfo_height(), full_height)
        self.assertFalse(self.app._startup_name_entry.winfo_ismapped())

    def test_long_status_fits_in_the_tab_row_and_offers_details(self):
        self.app._nav_buttons["settings"].invoke()
        message = "x" * 400
        self.app._show_check_result(message, "error", persistent=True)
        self.app.update()
        label = self.app._check_result_label
        text = label.cget("text")
        self.assertTrue(label.winfo_ismapped())
        self.assertTrue(text.endswith("…"))
        self.assertLess(len(text), 400)
        self.assertTrue(self.app._status_details_btn.winfo_ismapped())

        def verify_dialog(window):
            try:
                self.assertTrue(self._wait_for(window.winfo_viewable))
                body = next(child for child in window.winfo_children()
                            if isinstance(child, customtkinter.CTkTextbox))
                self.assertEqual(body.get("1.0", "end-1c"), message)
                close = next(child for child in window.winfo_children()
                             if isinstance(child, customtkinter.CTkButton)
                             and child.cget("text") == "关闭")
                self._assert_inside_page(close, window)
                close.invoke()
                self.assertFalse(window.winfo_exists())
            finally:
                if window.winfo_exists():
                    self.app._close_modal(window, None)

        with mock.patch.object(self.app, "wait_window", side_effect=verify_dialog):
            self.app._status_details_btn.invoke()

    def test_selected_row_actions_point_to_the_exact_entry(self):
        item = sm.StartupItem(name="demo", path=r"C:\demo.cmd", source="startup_folder")
        self.registry[item.identity()] = item
        self.app._nav_buttons["startup"].invoke()
        self.app._refresh_startup_list(force=True)
        self.assertTrue(self._wait_for(lambda: self._visible_startup_names() == ["demo"]))
        table = self.app._startup_table
        self.assertEqual(table.set(item.identity(), "source"), "启动夹")
        self.assertEqual(self.app._startup_delete_btn.cget("state"), "disabled")
        table.selection_set(item.identity())
        self.assertTrue(self._wait_for(lambda: self.app._startup_delete_btn.cget("state") == "normal"))
        self.confirm_answer = False
        self.app._startup_delete_btn.invoke()
        self.assertIn(r"C:\demo.cmd", self.confirm_calls[0]["body"])
        self.assertIn(item.identity(), self.registry)

    # --- P2.8: no Tk errors after close -----------------------------------

    def test_late_background_results_do_not_raise(self):
        def slow_snapshot():
            time.sleep(0.3)
            return self.snapshot

        with mock.patch.object(main_module, "get_power_snapshot", side_effect=slow_snapshot):
            self.app._refresh_power_status()
            time.sleep(0.05)
            self._destroy_app()
            time.sleep(0.5)  # the worker finishes after the window is gone
        self.assertTrue(True, "no exception escaped the background query")


if __name__ == "__main__":
    unittest.main()
