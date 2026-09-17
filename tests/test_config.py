"""Regression tests for config loading/saving (main.py) — no GUI, no system writes.

Run:  python -m unittest discover -s tests -v
"""

from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class ConfigTestCase(unittest.TestCase):
    """Each test gets its own data directory via PC_AUTO_SCRIPTS_DATA_DIR."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old_env = os.environ.get("PC_AUTO_SCRIPTS_DATA_DIR")
        os.environ["PC_AUTO_SCRIPTS_DATA_DIR"] = self._tmp.name
        self.tmp = Path(self._tmp.name)
        self.main = self._fresh_main()

    def tearDown(self):
        if self._old_env is None:
            os.environ.pop("PC_AUTO_SCRIPTS_DATA_DIR", None)
        else:
            os.environ["PC_AUTO_SCRIPTS_DATA_DIR"] = self._old_env
        self._tmp.cleanup()

    def _fresh_main(self):
        for name in ("main",):
            sys.modules.pop(name, None)
        module = importlib.import_module("main")
        # Point the module at a throwaway config path (import-time constant).
        module.CONFIG_FILE = self.tmp / "config.json"
        return module

    def _write_config(self, payload) -> None:
        (self.tmp / "config.json").write_text(
            payload if isinstance(payload, str) else json.dumps(payload),
            encoding="utf-8")

    # --- P1.1: monitor_enabled must survive a restart ---------------------

    def test_saved_monitor_disabled_is_respected(self):
        self._write_config({"monitor_enabled": False})
        cfg = self.main.load_config()
        self.assertFalse(cfg["monitor_enabled"],
                         "a saved monitor_enabled=false must not be forced back to true")

    def test_saved_monitor_enabled_is_respected(self):
        self._write_config({"monitor_enabled": True})
        self.assertTrue(self.main.load_config()["monitor_enabled"])

    def test_first_run_defaults_to_monitoring_enabled(self):
        cfg = self.main.load_config()
        self.assertTrue(cfg["monitor_enabled"])
        self.assertTrue(self.main.CONFIG_FILE.exists(), "first run should create config.json")

    # --- P2.7: validation -------------------------------------------------

    def test_top_level_array_falls_back_to_defaults(self):
        self._write_config('[1, 2, 3]')
        cfg = self.main.load_config()
        self.assertEqual(cfg["check_interval"], 60)
        self.assertTrue(cfg["monitor_enabled"])

    def test_top_level_null_falls_back_to_defaults(self):
        self._write_config('null')
        cfg = self.main.load_config()
        self.assertEqual(cfg["language"], self.main.DEFAULT_LANG)

    def test_string_and_negative_interval_are_rejected(self):
        self._write_config({"check_interval": "abc"})
        self.assertEqual(self.main.load_config()["check_interval"], 60)
        self._write_config({"check_interval": -5})
        self.assertEqual(self.main.load_config()["check_interval"], 60)
        self._write_config({"check_interval": "0"})
        self.assertEqual(self.main.load_config()["check_interval"], 60)
        self._write_config({"check_interval": 999999})
        self.assertEqual(self.main.load_config()["check_interval"], 60)

    def test_valid_interval_is_kept(self):
        self._write_config({"check_interval": 120})
        self.assertEqual(self.main.load_config()["check_interval"], 120)

    def test_bad_column_widths_fall_back(self):
        for bad in ([0.1, 0.2], ["x", 0.2, 0.3, 0.4], [0.25, 0.25, 0.25, -1], None):
            self._write_config({"col_widths": bad})
            cfg = self.main.load_config()
            self.assertEqual(cfg["col_widths"], self.main.CONFIG_DEFAULTS["col_widths"],
                             f"col_widths {bad!r} should fall back")

    def test_guid_validation(self):
        self._write_config({"target_guid": "not-a-guid"})
        self.assertEqual(self.main.load_config()["target_guid"], "")
        valid = "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c"
        self._write_config({"target_guid": valid.upper()})
        self.assertEqual(self.main.load_config()["target_guid"], valid)

    def test_string_booleans_are_coerced(self):
        self._write_config({"monitor_enabled": "false", "minimize_to_tray": "true"})
        cfg = self.main.load_config()
        self.assertFalse(cfg["monitor_enabled"])
        self.assertTrue(cfg["minimize_to_tray"])

    def test_corrupt_json_is_quarantined_and_recovered(self):
        self._write_config("{ this is not json")
        cfg = self.main.load_config()
        self.assertEqual(cfg["check_interval"], 60)
        self.assertTrue(self.main.CONFIG_FILE.exists(), "a usable config should be written back")
        self.assertTrue((self.tmp / "config.json.corrupt").exists(),
                        "the damaged file should be kept for inspection")

    def test_broken_config_does_not_raise(self):
        for payload in ('{"monitor_enabled": ', '[]', '"text"', '123'):
            self._write_config(payload)
            try:
                self.main.load_config()
            except Exception as exc:  # pragma: no cover - failure path
                self.fail(f"load_config raised on {payload!r}: {exc!r}")

    # --- P2.7: atomic save and visible failure ----------------------------

    def test_save_is_atomic_and_leaves_no_temp_file(self):
        cfg = self.main.load_config()
        cfg["check_interval"] = 45
        ok, error = self.main.save_config(cfg)
        self.assertTrue(ok, error)
        self.assertEqual(json.loads(self.main.CONFIG_FILE.read_text(encoding="utf-8"))["check_interval"], 45)
        leftovers = list(self.tmp.glob("*.tmp"))
        self.assertEqual(leftovers, [], "temporary save files must not be left behind")

    def test_interrupted_save_keeps_previous_config_readable(self):
        cfg = self.main.load_config()
        cfg["check_interval"] = 45
        self.main.save_config(cfg)
        original = self.main.CONFIG_FILE.read_text(encoding="utf-8")
        # Simulate a crash between write and replace.
        (self.tmp / "config.json.tmp").write_text('{"check_interval": 999}', encoding="utf-8")
        self.assertEqual(self.main.CONFIG_FILE.read_text(encoding="utf-8"), original)
        self.assertEqual(self.main.load_config()["check_interval"], 45)

    def test_unwritable_target_reports_error(self):
        cfg = self.main.load_config()
        # A path whose parent is a regular file makes the write fail.
        blocker = self.tmp / "blocker"
        blocker.write_text("x", encoding="utf-8")
        self.main.CONFIG_FILE = blocker / "config.json"
        ok, error = self.main.save_config(cfg)
        self.assertFalse(ok)
        self.assertTrue(error, "a failed save must explain itself")

    def test_save_reports_serialization_error(self):
        ok, error = self.main.save_config({"bad": object()})
        self.assertFalse(ok)
        self.assertIn("序列化", error)


if __name__ == "__main__":
    unittest.main()
