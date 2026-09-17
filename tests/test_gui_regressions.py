"""GUI-level regression tests: real Tk window, but every system touch is mocked.

These prove the UI wiring the handoff report called out: pure reads, GUID-bound
selection, confirm-gated removal, preference-preserving shutdown, and the
sorted/filtered list refresh. No registry key, startup file, or power scheme is
ever touched.

Run:  python -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import tempfile
import time
import tkinter
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
            mock.patch.object(main_module, "get_all_items", side_effect=self._get_all_items),
            mock.patch.object(main_module, "set_active_plan", side_effect=self._set_active_plan),
            mock.patch.object(main_module, "create_missing_builtin_schemes",
                              side_effect=self._create_schemes),
            mock.patch.object(main_module, "backup_item", side_effect=self._backup_item),
            mock.patch.object(main_module, "remove_item", side_effect=self._remove_item),
            mock.patch.object(main_module, "restore_last_backup", return_value={"name": "x"}),
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
        self._wait_for(lambda: self.app._startup_items_cache is not None, 2.0)
        self._pump(0.15)

    def tearDown(self):
        try:
            self.app.power_monitor.stop()
            self.app.power_monitor.wait_until_stopped(2.0)
            self.app._closing = True
            self.app.destroy()
        except Exception:
            pass
        self._tmp.cleanup()

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

    def _set_active_plan(self, guid):
        self.switched.append(guid)
        return True

    def _create_schemes(self, plans=None):
        self.created.append(plans)
        return [pm.SchemeCreateResult(
            name="卓越性能", ok=True, guid="e9a42b02-d5df-448d-aa00-03f14749eb61")]

    def _backup_item(self, item, data_root=None):
        return {"identity": item.identity(), "kind": item.kind}

    def _remove_item(self, item):
        self.registry.pop(item.identity(), None)
        return True

    def _add_registry_startup(self, name, path, arguments="", overwrite=False):
        key = f"registry:{name}"
        existing = self.registry.get(key)
        if existing is not None and not overwrite:
            return "exists"
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

    def test_language_switch_never_modifies_system(self):
        self.switched.clear()
        self.created.clear()
        self.app._cycle_language()
        self._pump(0.3)
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
        self._pump(0.2)
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
        self._pump(0.2)
        self.assertNotIn(cmd.identity(), self.registry)
        self.assertIn(lnk.identity(), self.registry, "the same-named .lnk must survive")

    def test_unknown_source_is_refused(self):
        item = sm.StartupItem(name="x", path="x", source="mystery")
        self.app._remove_startup_item(item)
        self._pump(0.1)
        self.assertEqual(self.confirm_calls, [], "an unknown source must be refused outright")

    def test_added_item_follows_current_sort_and_filter(self):
        self.registry["registry:zeta"] = sm.StartupItem(
            name="zeta", path=r"C:\z\zeta.exe", source="registry")
        self.app._refresh_startup_list(force=True)
        self._wait_for(lambda: len(self.app._startup_items_cache) == 1, 2.0)

        self.app._startup_name_entry.delete(0, "end")
        self.app._startup_name_entry.insert(0, "alpha")
        self.app._startup_path_entry.delete(0, "end")
        self.app._startup_path_entry.insert(0, r"C:\a\alpha.exe")
        self.app._add_startup_item()
        self.assertTrue(self._wait_for(
            lambda: len(self.app._startup_items_cache) == 2, 2.0),
            "the new item must appear in the cache")

        rendered = self.app._sorted_startup_items()
        names = [item.name for item in rendered]
        self.assertEqual(names, ["alpha", "zeta"], "the current sort must be re-applied")
        self.assertEqual(len(self.app._table_rows), 2, "exactly one row per item")
        self.assertTrue(self.app._startup_count_label.cget("text").startswith("2"))

    def test_overwriting_does_not_add_a_duplicate_row(self):
        self.registry["registry:app"] = sm.StartupItem(
            name="app", path=r"C:\old.exe", source="registry")
        self.app._refresh_startup_list(force=True)
        self._wait_for(lambda: len(self.app._startup_items_cache) == 1, 2.0)

        self.app._startup_name_entry.delete(0, "end")
        self.app._startup_name_entry.insert(0, "app")
        self.app._startup_path_entry.delete(0, "end")
        self.app._startup_path_entry.insert(0, r"C:\new.exe")
        self.confirm_answer = True
        self.app._add_startup_item()
        self._pump(0.3)
        self.assertEqual(len(self.app._startup_items_cache), 1, "no duplicate entry")
        self.assertEqual(len(self.app._table_rows), 1, "no duplicate row")
        self.assertEqual(self.confirm_calls[0]["body"].count(r"C:\old.exe"), 1,
                         "the prompt must show the command being replaced")

    def test_filter_hides_items_from_other_sources(self):
        self.registry["registry:a"] = sm.StartupItem(name="a", path="a", source="registry")
        self.registry["startup_folder:b"] = sm.StartupItem(
            name="b", path=str(self.root / "b.cmd"), source="startup_folder")
        self.app._refresh_startup_list(force=True)
        self._wait_for(lambda: len(self.app._startup_items_cache) == 2, 2.0)
        self.app._startup_source_filter = "registry"
        rendered = self.app._sorted_startup_items()
        self.assertEqual([item.name for item in rendered], ["a"],
                         "a HKLM/HKCU filter must not show folder items")

    def test_overwrite_prompt_can_be_declined(self):
        self.registry["registry:app"] = sm.StartupItem(
            name="app", path=r"C:\old.exe", source="registry")
        self.app._refresh_startup_list(force=True)
        self._wait_for(lambda: len(self.app._startup_items_cache) == 1, 2.0)

        self.app._startup_name_entry.delete(0, "end")
        self.app._startup_name_entry.insert(0, "app")
        self.app._startup_path_entry.delete(0, "end")
        self.app._startup_path_entry.insert(0, r"C:\new.exe")
        self.confirm_answer = False
        self.app._add_startup_item()
        self._pump(0.2)
        self.assertEqual(len(self.confirm_calls), 1)
        self.assertIn(r"C:\old.exe", self.confirm_calls[0]["body"])
        self.assertEqual(self.registry["registry:app"].path, r"C:\old.exe",
                         "declining must leave the old command in place")

    # --- layout: content must fill the window (no blank strip) -------------

    def _frame_span(self) -> tuple:
        """(relx, relwidth) of the main content frame."""
        info = self.app._main_frame.place_info()
        return float(info.get("relx") or 0.0), float(info.get("relwidth") or 1.0)

    def test_main_content_fills_the_window(self):
        """A stale main-frame width leaves a large empty strip on the right."""
        def spans_to_edge():
            self.app.update_idletasks()
            relx, relwidth = self._frame_span()
            return relx + relwidth >= 0.995

        self.assertTrue(self._wait_for(spans_to_edge, 3.0),
                        f"content does not reach the right edge: {self._frame_span()}")

        self.app.geometry("1200x700")
        self.assertTrue(self._wait_for(spans_to_edge, 3.0),
                        f"content lost the right edge after growing: {self._frame_span()}")

        self.app.geometry("880x580")
        self.assertTrue(self._wait_for(spans_to_edge, 3.0),
                        f"content lost the right edge after shrinking: {self._frame_span()}")

    def test_main_content_starts_at_the_sidebar(self):
        relx, _ = self._frame_span()
        self.assertAlmostEqual(relx, main_module.SIDEBAR_EXPANDED / 880.0, places=2)

    def test_collapsed_sidebar_still_fills_the_window(self):
        expanded_relx, _ = self._frame_span()
        self.app._toggle_sidebar()
        self.assertTrue(self._wait_for(
            lambda: self._frame_span()[0] < expanded_relx - 0.05, 3.0),
            "main content should follow the collapsed sidebar")
        relx, relwidth = self._frame_span()
        self.assertGreaterEqual(relx + relwidth, 0.995,
                                "collapsing must not leave a blank strip")

    def test_compact_mode_round_trip_keeps_the_layout(self):
        """Returning from compact mode must not push the content off the window."""
        before = self._frame_span()

        self.app._toggle_compact_mode()
        self.assertTrue(self._wait_for(lambda: self.app._compact_mode, 3.0))
        self.assertTrue(self._wait_for(
            lambda: self.app._compact_frame.winfo_ismapped(), 3.0))

        self.app._toggle_compact_mode()
        self.assertTrue(self._wait_for(lambda: not self.app._compact_mode, 3.0))
        self.assertTrue(self._wait_for(
            lambda: not self.app._compact_frame.winfo_ismapped(), 3.0))

        after = self._wait_for(lambda: self._frame_span() == before, 3.0)
        relx, relwidth = self._frame_span()
        self.assertTrue(after, f"fractions changed after compact: {before} -> "
                               f"{(relx, relwidth)}")
        self.assertGreaterEqual(relx + relwidth, 0.995,
                                "content no longer reaches the right edge")

        # No stale absolute x may survive alongside the fraction: tkinter gives
        # the pixel option priority, which is what scrambled the layout.
        info = self.app._main_frame.place_info()
        self.assertIn(info.get("x"), (None, "", "0"),
                      f"stale pixel x in place options: {info}")

    def test_compact_round_trip_with_collapsed_sidebar(self):
        self.app._toggle_sidebar()
        self._wait_for(lambda: self._frame_span()[0] < 0.1, 3.0)
        self.app._toggle_compact_mode()
        self.assertTrue(self._wait_for(lambda: self.app._compact_mode, 3.0))
        self.app._toggle_compact_mode()
        self.assertTrue(self._wait_for(lambda: not self.app._compact_mode, 3.0))
        relx, relwidth = self._frame_span()
        self.assertGreaterEqual(relx + relwidth, 0.995,
                                f"blank strip after round trip: {(relx, relwidth)}")
        self._wait_for(lambda: self.app._sidebar.winfo_ismapped(), 3.0)
        self.assertTrue(self.app._sidebar.winfo_ismapped())

    # --- sorting must be reachable by clicking the column header -----------

    def _click_header(self, key: str):
        """Click a header cell the way the user does (on its click target)."""
        label = self.app._col_header_widgets[key][0]
        target = next((child for child in label.winfo_children()
                       if isinstance(child, tkinter.Canvas)), None)
        self.assertIsNotNone(target, "the header cell should expose a click target")
        self.assertTrue(target.bind("<Button-1>"),
                        f"the '{key}' header has no click binding")
        # Synthetic events all carry time=0; a real click carries a timestamp, and
        # the header guard uses it to ignore one click arriving through two
        # bindings. Distinct timestamps keep consecutive test clicks separate.
        self._click_clock = getattr(self, "_click_clock", 0) + 500
        target.event_generate("<Button-1>", x=4, y=4, time=self._click_clock)
        self.app.update()

    def test_clicking_the_name_header_toggles_the_sort(self):
        for name in ("beta", "alpha", "gamma"):
            self.registry[f"registry:{name}"] = sm.StartupItem(
                name=name, path=rf"C:\{name}.exe", source="registry")
        self.app._refresh_startup_list(force=True)
        self._wait_for(lambda: len(self.app._table_rows) == 3, 2.0)
        self.app._sort_key = "name"
        self.app._sort_ascending = True

        self._click_header("name")
        self.assertFalse(self.app._sort_ascending,
                         "clicking the Name header should flip the sort direction")
        self.assertEqual([item.name for item in self.app._sorted_startup_items()],
                         ["gamma", "beta", "alpha"])

        self._click_header("name")
        self.assertTrue(self.app._sort_ascending)
        self.assertEqual([item.name for item in self.app._sorted_startup_items()],
                         ["alpha", "beta", "gamma"])

    def test_clicking_the_source_header_switches_the_sort_key(self):
        self.app._sort_key = "name"
        self._click_header("source")
        self.assertEqual(self.app._sort_key, "source")

    def test_header_shows_the_sort_direction(self):
        self.app._sort_key = "name"
        self.app._sort_ascending = True
        self.app._update_sort_indicator()
        self.app.update_idletasks()
        ascending = self.app._col_header_widgets["name"][0].cget("text")
        self.app._toggle_sort("name")
        self.app.update_idletasks()
        descending = self.app._col_header_widgets["name"][0].cget("text")
        self.assertNotEqual(ascending, descending,
                            "the header must show which direction is active")
        self.assertTrue(descending.endswith("↓") or descending.endswith("↑"))

    def test_source_column_shows_the_short_tag(self):
        self.registry["registry_hklm"] = sm.StartupItem(name="a", path=r"C:\a.exe",
                                                        source="registry_hklm")
        self.app._refresh_startup_list(force=True)
        self._wait_for(lambda: len(self.app._table_rows) == 1, 2.0)
        self.app.update_idletasks()
        widgets = self.app._row_widgets[id(self.app._table_rows[0])]
        self.assertEqual(widgets["source"].cget("text"), "HKLM",
                         "the cell must use the short source tag, not a clipped label")
        self.assertEqual(self.app._format_startup_source("registry_hklm"), "全局 Run")

    def test_row_cells_stay_inside_the_card(self):
        self.registry["registry:a"] = sm.StartupItem(name="a", path=r"C:\a.exe",
                                                     source="registry")
        self.app._refresh_startup_list(force=True)
        self._wait_for(lambda: len(self.app._table_rows) == 1, 2.0)
        self.app.update_idletasks()
        row = self.app._table_rows[0]
        widgets = self.app._row_widgets[id(row)]
        row_width = row.winfo_width()
        self.assertGreater(row_width, 100, "the row must have a real width")
        for key in ("name", "path", "source", "open", "remove"):
            widget = widgets[key]
            right = widget.winfo_x() + widget.winfo_width()
            self.assertLessEqual(right, row_width + 2,
                                 f"{key} extends past the row ({right} > {row_width})")

    # --- compactness: no row may overflow its card -------------------------

    def _overflow(self, row) -> int:
        children = [kid for kid in row.winfo_children() if kid.winfo_ismapped()]
        if not children:
            return 0
        right = max(kid.winfo_x() + kid.winfo_width() for kid in children)
        return right - row.winfo_width()

    def _card_rows_fit(self) -> str:
        """Empty string when every row of both cards fits."""
        self.app.update_idletasks()
        for card_name in ("_power_card", "_startup_card"):
            card = getattr(self.app, card_name)
            for index, row in enumerate(card.winfo_children()):
                if row is getattr(self.app, "_startup_list_frame", None):
                    continue
                overflow = self._overflow(row)
                if overflow > 2:
                    return f"{card_name} row{index} overflows by {overflow}px"
        return ""

    def test_cards_do_not_overflow_at_any_width(self):
        for size in ("880x580", "620x420", "1200x760"):
            self.app.geometry(size)
            self._wait_for(lambda: self._card_rows_fit() == "", 2.5)
            self.assertEqual(self._card_rows_fit(), "",
                             f"layout overflows at {size}")

    def test_title_row_shows_the_check_result_inline(self):
        self.app._show_check_result("hello state", "ok", persistent=True)
        self.app.update_idletasks()
        self.assertIn("hello state", self.app._check_result_label.cget("text"))
        # The result shares the title row: the power card must stay compact.
        rows = [row for row in self.app._power_card.winfo_children()]
        self.assertLessEqual(len(rows), 2,
                             "the power card should keep at most two rows")

    def test_long_messages_are_trimmed(self):
        self.app._show_check_result("x" * 400, "error", persistent=True)
        self.app.update_idletasks()
        text = self.app._check_result_label.cget("text")
        self.assertLessEqual(len(text), 111, "a long message must be trimmed")
        self.assertTrue(text.endswith("…"))
        self.assertEqual(self._card_rows_fit(), "",
                         "a long message must not overflow the title row")

    def test_status_bar_only_takes_space_while_showing(self):
        self.app._clear_status_bar()
        self.app.update_idletasks()
        self.assertFalse(self.app._status_bar.winfo_ismapped(),
                         "an empty status strip must not occupy space")
        self.app._show_check_result("transient note", "info")
        self.app.update_idletasks()
        self.assertTrue(self.app._status_bar.winfo_ismapped())
        self.app._clear_status_bar()
        self.app.update_idletasks()
        self.assertFalse(self.app._status_bar.winfo_ismapped())

    # --- hover tooltip must never be left behind ---------------------------

    def _tooltip_alive(self) -> bool:
        window = getattr(self.app, "_cell_tip", None)
        try:
            return window is not None and bool(window.winfo_exists())
        except Exception:
            return False

    def test_hover_tooltip_is_hidden_again(self):
        self.registry["registry:a"] = sm.StartupItem(
            name="a", path=r"C:\very\long\path\that\is\clipped\a.exe", source="registry")
        self.app._refresh_startup_list(force=True)
        self._wait_for(lambda: len(self.app._table_rows) == 1, 2.0)
        row = self.app._table_rows[0]

        self.app._show_cell_tip(row)
        self.app.update_idletasks()
        self.assertTrue(self._tooltip_alive(), "hovering a row should show the tooltip")

        self.app._hide_cell_tip()
        self.app.update_idletasks()
        self.assertFalse(self._tooltip_alive(), "the tooltip must be destroyed on hide")

    def test_tooltip_is_cleared_before_a_dialog_opens(self):
        self.registry["registry:a"] = sm.StartupItem(name="a", path=r"C:\a.exe",
                                                     source="registry")
        self.app._refresh_startup_list(force=True)
        self._wait_for(lambda: len(self.app._table_rows) == 1, 2.0)
        self.app._show_cell_tip(self.app._table_rows[0])
        self.app.update_idletasks()
        self.assertTrue(self._tooltip_alive())

        with mock.patch.object(self.app, "wait_window", return_value=None):
            self.app._modal("test", width=200, height=120)
        self.app.update_idletasks()
        self.assertFalse(self._tooltip_alive(),
                         "a leftover tooltip would float over the dialog")

    def test_tooltip_is_cleared_when_the_list_re_renders(self):
        self.registry["registry:a"] = sm.StartupItem(name="a", path=r"C:\a.exe",
                                                     source="registry")
        self.app._refresh_startup_list(force=True)
        self._wait_for(lambda: len(self.app._table_rows) == 1, 2.0)
        self.app._show_cell_tip(self.app._table_rows[0])
        self.app.update_idletasks()
        self.assertTrue(self._tooltip_alive())

        self.app._render_startup_items(self.app._sorted_startup_items())
        self.app.update_idletasks()
        self.assertFalse(self._tooltip_alive(),
                         "re-rendering must not leave a tooltip behind")

    def test_tooltip_auto_hides(self):
        self.registry["registry:a"] = sm.StartupItem(name="a", path=r"C:\a.exe",
                                                     source="registry")
        self.app._refresh_startup_list(force=True)
        self._wait_for(lambda: len(self.app._table_rows) == 1, 2.0)
        self.app._show_cell_tip(self.app._table_rows[0])
        self.app.update_idletasks()
        self.assertTrue(self._tooltip_alive())
        self.assertTrue(
            self._wait_for(lambda: not self._tooltip_alive(),
                           main_module.TIP_TIMEOUT_MS / 1000.0 + 2.0),
            "the tooltip must disappear on its own")

    # --- P2.8: no Tk errors after close -----------------------------------

    def test_late_background_results_do_not_raise(self):
        def slow_snapshot():
            time.sleep(0.3)
            return self.snapshot

        with mock.patch.object(main_module, "get_power_snapshot", side_effect=slow_snapshot):
            self.app._refresh_power_status()
            time.sleep(0.05)
            self.app._closing = True
            self.app.destroy()
            time.sleep(0.5)  # the worker finishes after the window is gone
        self.assertTrue(True, "no exception escaped the background query")


if __name__ == "__main__":
    unittest.main()
