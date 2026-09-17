"""Regression tests for startup_manager: path identity, safe removal, backup/restore.

Every test uses a temporary startup folder and a temporary backup root; the real
registry and the real Startup folders are never touched.

Run:  python -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
import winreg
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import startup_manager as sm


class FakeRegistry:
    """Minimal in-memory stand-in for winreg's HKCU/HKLM Run keys."""

    def __init__(self):
        self.values = {}          # (source, name) -> (data, type)
        self.fail_open = set()
        self.fail_set = set()

    def open_key(self, root, path, reserved=0, access=0):
        source = self._source_for(root, path)
        if source in self.fail_open:
            raise OSError(5, "Access is denied")
        return _FakeKey(self, source)

    def _source_for(self, root, path):
        for source, key_root, key_path in sm.REGISTRY_SOURCES:
            if key_root == root and key_path == path:
                return source
        raise OSError(2, "not found")

    def query(self, source, name):
        if (source, name) not in self.values:
            raise OSError(2, "value not found")
        return self.values[(source, name)]

    def set(self, source, name, value_type, data):
        if source in self.fail_set:
            raise OSError(5, "Access is denied")
        self.values[(source, name)] = (data, value_type)

    def delete(self, source, name):
        if (source, name) not in self.values:
            raise OSError(2, "value not found")
        del self.values[(source, name)]

    def enumerate(self, source):
        return [(name, data, vtype) for (src, name), (data, vtype) in self.values.items()
                if src == source]


class _FakeKey:
    def __init__(self, registry, source):
        self.registry = registry
        self.source = source

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def close(self):
        pass


class StartupTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        # Never let another test's data-directory override leak into the ledger.
        self._old_data_env = os.environ.pop("PC_AUTO_SCRIPTS_DATA_DIR", None)
        self.addCleanup(self._restore_data_env)
        self.root = Path(self._tmp.name)
        self.user_folder = self.root / "user_startup"
        self.common_folder = self.root / "common_startup"
        self.user_folder.mkdir()
        self.common_folder.mkdir()
        self.backup_root = self.root / "data"
        self.backup_root.mkdir()

        self.registry = FakeRegistry()
        self._patches = [
            mock.patch.object(sm, "_get_startup_folder", return_value=str(self.user_folder)),
            mock.patch.object(sm, "_get_common_startup_folder", return_value=str(self.common_folder)),
            mock.patch.object(sm.winreg, "OpenKey", side_effect=self.registry.open_key),
            mock.patch.object(sm.winreg, "CloseKey", side_effect=lambda key: None),
            mock.patch.object(sm.winreg, "QueryValueEx",
                              side_effect=lambda key, name: self.registry.query(key.source, name)),
            mock.patch.object(sm.winreg, "SetValueEx",
                              side_effect=lambda key, name, reserved, vtype, data:
                              self.registry.set(key.source, name, vtype, data)),
            mock.patch.object(sm.winreg, "DeleteValue",
                              side_effect=lambda key, name: self.registry.delete(key.source, name)),
            mock.patch.object(sm.winreg, "EnumValue",
                              side_effect=self._enum_value),
        ]
        for patch in self._patches:
            patch.start()
            self.addCleanup(patch.stop)

    def _enum_value(self, key, index):
        rows = self.registry.enumerate(key.source)
        if index >= len(rows):
            raise OSError(22, "no more data")
        return rows[index]

    def _restore_data_env(self):
        if self._old_data_env is not None:
            os.environ["PC_AUTO_SCRIPTS_DATA_DIR"] = self._old_data_env

    def tearDown(self):
        self._tmp.cleanup()

    # --- P1.3: file identity is the path ----------------------------------

    def test_same_name_different_extension_are_distinct_items(self):
        (self.user_folder / "demo.cmd").write_text("@echo off", encoding="utf-8")
        (self.user_folder / "demo.lnk").write_bytes(b"lnk")
        items = [item for item in sm.get_startup_folder_items()
                 if item.source == "startup_folder"]
        names = sorted(item.name for item in items)
        self.assertEqual(names, ["demo", "demo"])
        keys = {item.identity() for item in items}
        self.assertEqual(len(keys), 2, "demo.cmd and demo.lnk must have distinct identities")

    def test_removing_one_same_named_file_leaves_the_other(self):
        cmd = self.user_folder / "demo.cmd"
        lnk = self.user_folder / "demo.lnk"
        cmd.write_text("@echo off", encoding="utf-8")
        lnk.write_bytes(b"lnk")
        self.assertTrue(sm.remove_startup_folder_item(str(cmd), "startup_folder"))
        self.assertFalse(cmd.exists())
        self.assertTrue(lnk.exists(), "the other same-named file must be untouched")

    def test_legacy_name_removal_refuses_ambiguous_names(self):
        (self.user_folder / "demo.cmd").write_text("@echo off", encoding="utf-8")
        (self.user_folder / "demo.lnk").write_bytes(b"lnk")
        self.assertFalse(sm.remove_startup_folder_item_by_source("demo", "startup_folder"),
                         "an ambiguous name must not delete an arbitrary file")
        self.assertTrue((self.user_folder / "demo.cmd").exists())
        self.assertTrue((self.user_folder / "demo.lnk").exists())

    def test_legacy_name_removal_works_when_unambiguous(self):
        only = self.user_folder / "solo.cmd"
        only.write_text("@echo off", encoding="utf-8")
        self.assertTrue(sm.remove_startup_folder_item_by_source("solo", "startup_folder"))
        self.assertFalse(only.exists())

    # --- P1.3: containment and source validation --------------------------

    def test_path_outside_the_startup_folder_is_rejected(self):
        outside = self.root / "evil.exe"
        outside.write_text("x", encoding="utf-8")
        self.assertFalse(sm.remove_startup_folder_item(str(outside), "startup_folder"))
        self.assertTrue(outside.exists())

    def test_prefix_sibling_folder_is_rejected(self):
        sibling = Path(str(self.user_folder) + "_evil")
        sibling.mkdir()
        target = sibling / "x.cmd"
        target.write_text("x", encoding="utf-8")
        self.assertFalse(sm.remove_startup_folder_item(str(target), "startup_folder"))
        self.assertTrue(target.exists())

    def test_traversal_is_rejected(self):
        target = self.user_folder / ".." / "escape.cmd"
        (self.root / "escape.cmd").write_text("x", encoding="utf-8")
        self.assertFalse(sm.remove_startup_folder_item(str(target), "startup_folder"))
        self.assertTrue((self.root / "escape.cmd").exists())

    def test_wrong_folder_for_source_is_rejected(self):
        common_file = self.common_folder / "shared.cmd"
        common_file.write_text("x", encoding="utf-8")
        # Asking to remove a common-folder file as if it were the user folder.
        self.assertFalse(sm.remove_startup_folder_item(str(common_file), "startup_folder"))
        self.assertTrue(common_file.exists())

    def test_unknown_sources_are_rejected(self):
        with self.assertRaises(sm.UnknownSourceError):
            sm.folder_for_source("startup_folder_perhaps")
        with self.assertRaises(sm.UnknownSourceError):
            sm._get_registry_source("registry_somewhere")
        self.assertFalse(sm.remove_registry_startup_by_source("x", "registry_bogus"))
        self.assertFalse(sm.remove_startup_folder_item_by_source("x", "startup_folder_bogus"))
        self.assertFalse(sm.is_known_source("nope"))

    def test_unknown_source_item_is_not_removed(self):
        item = sm.StartupItem(name="x", path=str(self.user_folder / "x.cmd"), source="bogus")
        self.assertFalse(sm.remove_item(item))

    def test_registry_items_are_read_per_source(self):
        self.registry.set("registry", "A", winreg.REG_SZ, r"C:\a.exe")
        self.registry.set("registry_hklm", "B", winreg.REG_SZ, r"C:\b.exe")
        items = sm.get_registry_items()
        sources = sorted((item.name, item.source) for item in items)
        self.assertEqual(sources, [("A", "registry"), ("B", "registry_hklm")])

    # --- P1.4: reliable commands ------------------------------------------

    def test_path_with_spaces_is_quoted(self):
        command = sm._build_registry_command(r"C:\Program Files\App\app.exe")
        self.assertEqual(command, r'"C:\Program Files\App\app.exe"')

    def test_chinese_path_with_spaces_is_quoted(self):
        command = sm._build_registry_command(r"C:\程序 文件\我的 应用.exe")
        self.assertEqual(command, r'"C:\程序 文件\我的 应用.exe"')

    def test_arguments_are_appended(self):
        command = sm._build_registry_command(r"C:\Tools\app.exe", "--silent --log a.txt")
        self.assertEqual(command, r'"C:\Tools\app.exe" --silent --log a.txt')

    def test_already_quoted_path_is_not_double_quoted(self):
        command = sm._build_registry_command(r'"C:\Tools\app.exe"', "--x")
        self.assertEqual(command, r'"C:\Tools\app.exe" --x')

    def test_empty_path_is_invalid(self):
        self.assertEqual(sm.add_registry_startup("name", "   "), "invalid")
        self.assertEqual(sm.add_registry_startup("  ", r"C:\a.exe"), "invalid")

    def test_self_command_uses_absolute_paths(self):
        with mock.patch.object(sm.sys, "frozen", False, create=True), \
             mock.patch.object(sm.sys, "argv", ["main.py"]), \
             mock.patch.object(sm.sys, "executable", r"C:\Python 3\python.exe"):
            command = sm.build_self_startup_command()
        self.assertIn('"C:\\Python 3\\python.exe"', command)
        self.assertIn(str(Path("main.py").resolve()), command)

    def test_self_command_for_frozen_exe(self):
        with mock.patch.object(sm.sys, "frozen", True, create=True), \
             mock.patch.object(sm.sys, "executable", r"C:\Apps\My App\app.exe"):
            command = sm.build_self_startup_command()
        self.assertEqual(command, r'"C:\Apps\My App\app.exe"')

    def test_self_command_falls_back_when_argv0_is_a_python_flag(self):
        # `python -c ...` or an interactive session must not produce '-c' as the target.
        with mock.patch.object(sm.sys, "frozen", False, create=True), \
             mock.patch.object(sm.sys, "argv", ["-c"]), \
             mock.patch.object(sm.sys, "executable", r"C:\Python\python.exe"):
            command = sm.build_self_startup_command()
        self.assertIn("startup_manager.py", command)
        self.assertNotIn('"-c"', command)

    def test_get_app_exe_path_is_absolute(self):
        with mock.patch.object(sm.sys, "frozen", False, create=True), \
             mock.patch.object(sm.sys, "argv", ["relative\\path.py"]):
            resolved = sm.get_app_exe_path()
        self.assertTrue(os.path.isabs(resolved))
        self.assertTrue(resolved.lower().endswith("startup_manager.py"),
                        "a non-existent argv[0] must fall back to the real module")

    # --- P1.4: overwrite protection ---------------------------------------

    def test_add_reports_exists_and_does_not_overwrite(self):
        self.registry.set("registry", "app", winreg.REG_SZ, r"C:\old.exe")
        status = sm.add_registry_startup("app", r"C:\new.exe")
        self.assertEqual(status, "exists")
        self.assertEqual(self.registry.values[("registry", "app")][0], r"C:\old.exe")

    def test_add_with_overwrite_replaces_and_reports(self):
        self.registry.set("registry", "app", winreg.REG_SZ, r"C:\old.exe")
        status = sm.add_registry_startup("app", r"C:\new.exe", overwrite=True)
        self.assertEqual(status, "overwritten")
        self.assertEqual(self.registry.values[("registry", "app")][0], r'"C:\new.exe"')

    def test_add_creates_new_value(self):
        self.assertEqual(sm.add_registry_startup("fresh", r"C:\new.exe"), "created")
        self.assertEqual(self.registry.values[("registry", "fresh")][0], r'"C:\new.exe"')

    def test_add_reports_failure(self):
        self.registry.fail_set.add("registry")
        self.assertEqual(sm.add_registry_startup("x", r"C:\x.exe"), "failed")

    # --- P1.4: backup and restore -----------------------------------------

    def test_folder_backup_then_restore_roundtrip(self):
        target = self.user_folder / "tool.cmd"
        target.write_text("@echo off\n", encoding="utf-8")
        item = sm.StartupItem(name="tool", path=str(target), source="startup_folder")

        entry = sm.backup_item(item, self.backup_root)
        self.assertIsNotNone(entry)
        self.assertEqual(sm.list_backups(self.backup_root), [],
                         "an intact original is not pending a restore")

        self.assertTrue(sm.remove_item(item))
        self.assertFalse(target.exists())
        pending = sm.list_backups(self.backup_root)
        self.assertEqual(len(pending), 1, "the removed file should be pending restore")

        restored = sm.restore_last_backup(self.backup_root)
        self.assertIsNotNone(restored)
        self.assertTrue(target.exists())
        self.assertEqual(target.read_text(encoding="utf-8"), "@echo off\n")
        self.assertEqual(sm.list_backups(self.backup_root), [],
                         "a restored entry leaves the pending list")

    def test_registry_backup_restores_value_and_type(self):
        self.registry.set("registry", "app", winreg.REG_EXPAND_SZ, r"%ProgramFiles%\a.exe")
        item = sm.StartupItem(name="app", path=r"%ProgramFiles%\a.exe", source="registry")

        entry = sm.backup_item(item, self.backup_root)
        self.assertIsNotNone(entry)
        self.assertEqual(entry["value_type"], winreg.REG_EXPAND_SZ)

        self.assertTrue(sm.remove_item(item))
        self.assertNotIn(("registry", "app"), self.registry.values)
        self.assertEqual(len(sm.list_backups(self.backup_root)), 1)

        self.assertIsNotNone(sm.restore_last_backup(self.backup_root))
        data, value_type = self.registry.values[("registry", "app")]
        self.assertEqual(data, r"%ProgramFiles%\a.exe")
        self.assertEqual(value_type, winreg.REG_EXPAND_SZ,
                         "the original value type must be restored")

    def test_registry_backup_ledger_is_written_atomically(self):
        self.registry.set("registry", "app", winreg.REG_SZ, r"C:\a.exe")
        sm.backup_registry_value("app", "registry", self.backup_root)
        store = self.backup_root / sm.BACKUP_FILENAME
        self.assertTrue(store.exists())
        json.loads(store.read_text(encoding="utf-8"))
        self.assertEqual(list(self.backup_root.glob("*.tmp")), [])

    def test_restore_without_backup_is_a_noop(self):
        self.assertIsNone(sm.restore_last_backup(self.backup_root))

    def test_backup_rejects_out_of_folder_paths(self):
        outside = self.root / "outside.cmd"
        outside.write_text("x", encoding="utf-8")
        self.assertIsNone(sm.backup_folder_item(str(outside), "startup_folder", self.backup_root))

    def test_backup_of_missing_registry_value_is_none(self):
        self.assertIsNone(sm.backup_registry_value("nope", "registry", self.backup_root))

    # --- misc -------------------------------------------------------------

    def test_registry_removal_failure_is_reported(self):
        self.registry.fail_open.add("registry_hklm")
        self.assertTrue(sm.remove_registry_startup_by_source("x", "registry_hklm") is False)


if __name__ == "__main__":
    unittest.main()
