"""Windows startup items management via registry and startup folders.

Design rules (see AGENT_HANDOFF_RECOMMENDATIONS.md):

* A startup-folder entry is identified by its normalized absolute path, never by
  a name with the extension stripped — ``demo.cmd`` and ``demo.lnk`` in the same
  folder are two items and each can be removed independently.
* Removal only accepts an explicit file path that is verified to live inside the
  corresponding Startup folder, or a registry value name in a known Run key.
  Unknown sources are rejected instead of silently defaulting to HKCU.
* Removal is reversible: the previous registry value (with its type) or a copy of
  the file is stored in a small backup ledger first.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import winreg
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

log = logging.getLogger(__name__)

STARTUP_REG_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
STARTUP_REG_PATH_WOW6432 = r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Run"

REGISTRY_SOURCES: Tuple[tuple, ...] = (
    ("registry", winreg.HKEY_CURRENT_USER, STARTUP_REG_PATH),
    ("registry_hklm", winreg.HKEY_LOCAL_MACHINE, STARTUP_REG_PATH),
    ("registry_hklm_wow6432", winreg.HKEY_LOCAL_MACHINE, STARTUP_REG_PATH_WOW6432),
)

FOLDER_SOURCES: Tuple[str, ...] = ("startup_folder", "startup_folder_common")

KNOWN_SOURCES: Tuple[str, ...] = tuple(name for name, _, _ in REGISTRY_SOURCES) + FOLDER_SOURCES

BACKUP_FILENAME = "startup_backups.json"
BACKUP_DIRNAME = "startup_backups"
_MAX_BACKUP_ENTRIES = 50

# The ledger is read from the GUI thread while removals may run elsewhere.
_ledger_lock = threading.RLock()


class UnknownSourceError(ValueError):
    """The caller passed a startup source this module does not manage."""


class StartupReadError(OSError):
    """One or more sources were unavailable. Other sources may still be shown."""

    def __init__(self, items: List[StartupItem], errors: List[str]):
        super().__init__(" / ".join(errors))
        self.items = items


@dataclass
class StartupItem:
    name: str
    path: str
    source: str
    file_version: Optional[Tuple[int, int, int, int]] = None

    @property
    def kind(self) -> str:
        return "folder" if self.source in FOLDER_SOURCES else "registry"

    def identity(self) -> str:
        """Stable row/list key. File items are keyed by path, not by name."""
        return normalize_key(self.source, self.path if self.kind == "folder" else self.name)


def normalize_key(source: str, name_or_path: str) -> str:
    """Key helper for callers that only hold a source plus name/path."""
    if source in FOLDER_SOURCES:
        return f"{source}:{_normalize_path(name_or_path)}"
    return f"{source}:{name_or_path.lower()}"


@dataclass
class RegistryValueRef:
    """Everything needed to recreate a registry value exactly."""

    source: str
    name: str
    value_type: int
    data_b64: str

    def data(self):
        return base64.b64decode(self.data_b64, validate=True)


def _normalize_path(path: str) -> str:
    """Absolute, case-normalized path used for identity and containment checks."""
    expanded = os.path.expandvars(str(path or "").strip().strip('"'))
    try:
        expanded = os.path.abspath(expanded)
    except OSError:
        pass
    return os.path.normcase(expanded)


def _file_version(info: os.stat_result) -> Tuple[int, int, int, int]:
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def _get_startup_folder() -> str:
    base = os.environ.get("APPDATA")
    return os.path.join(base, r"Microsoft\Windows\Start Menu\Programs\Startup") if base else ""


def _get_common_startup_folder() -> str:
    base = os.environ.get("PROGRAMDATA")
    return os.path.join(base, r"Microsoft\Windows\Start Menu\Programs\Startup") if base else ""


def folder_for_source(source: str) -> str:
    """Startup folder path for a folder source. Raises on unknown sources."""
    if source == "startup_folder":
        return _get_startup_folder()
    if source == "startup_folder_common":
        return _get_common_startup_folder()
    raise UnknownSourceError(f"not a startup folder source: {source!r}")


def _get_registry_source(source: str) -> tuple:
    for source_name, root, path in REGISTRY_SOURCES:
        if source_name == source:
            return root, path
    raise UnknownSourceError(f"unknown registry startup source: {source!r}")


def is_known_source(source: str) -> bool:
    return source in KNOWN_SOURCES


def _is_startup_file_path(path: str, folder: str) -> bool:
    # Enumeration lists direct children. A descendant can cross a junction.
    return bool(path and folder and os.path.dirname(_normalize_path(path)) == _normalize_path(folder))


def get_registry_items(errors: Optional[List[str]] = None) -> List[StartupItem]:
    """Get startup items from common registry Run keys."""
    items = []
    for source, root, path in REGISTRY_SOURCES:
        try:
            with winreg.OpenKey(root, path, 0, winreg.KEY_QUERY_VALUE) as key:
                i = 0
                while True:
                    try:
                        name, value, _ = winreg.EnumValue(key, i)
                    except OSError as exc:
                        if getattr(exc, "winerror", None) == 259:  # ERROR_NO_MORE_ITEMS
                            break
                        raise
                    # Run entries have a value name. Leave the key default alone.
                    if name and isinstance(value, str):
                        items.append(StartupItem(name=name, path=value, source=source))
                    i += 1
        except FileNotFoundError:
            continue
        except OSError as exc:
            log.warning("registry startup read failed for %s: %s", source, exc)
            if errors is not None:
                errors.append(f"{source}: {exc}")
    return items


def _get_startup_folder_items(folder: str, source: str) -> List[StartupItem]:
    items = []
    if not folder:
        return items
    try:
        entries = os.listdir(folder)
    except FileNotFoundError:
        return items
    for entry in entries:
        path = os.path.join(folder, entry)
        try:
            info = os.stat(path, follow_symlinks=False)
        except FileNotFoundError:
            continue
        if stat.S_ISREG(info.st_mode):
            items.append(StartupItem(os.path.splitext(entry)[0], path, source, _file_version(info)))
    return items


def get_startup_folder_items(errors: Optional[List[str]] = None) -> List[StartupItem]:
    """Get startup items from user and common Startup folders."""
    items: List[StartupItem] = []
    for source in FOLDER_SOURCES:
        try:
            items.extend(_get_startup_folder_items(folder_for_source(source), source))
        except OSError as exc:
            if errors is None:
                raise
            errors.append(f"{source}: {exc}")
    return items


def get_all_items() -> List[StartupItem]:
    """Get all startup items from registry Run keys and Startup folders."""
    errors: List[str] = []
    items = get_registry_items(errors) + get_startup_folder_items(errors)
    if errors:
        raise StartupReadError(items, errors)
    return items


# ---------------------------------------------------------------------------
# Add
# ---------------------------------------------------------------------------

def _build_registry_command(exe_path: str, arguments: str = "") -> str:
    """Build a startup command: executable always quoted, arguments verbatim.

    Always quoting avoids the "path with spaces" ambiguity entirely, including
    paths that gain spaces later (e.g. a renamed user profile folder).
    """
    target = os.path.expandvars(str(exe_path or "").strip())
    if len(target) >= 2 and target[0] == '"' and target[-1] == '"':
        target = target[1:-1]
    if not target or any(char in target for char in '"\0\r\n'):
        raise ValueError("invalid executable path")
    command = f'"{target}"'
    args = str(arguments or "").strip()
    return f"{command} {args}".strip()


def add_registry_startup(name: str, exe_path: str, arguments: str = "",
                         overwrite: bool = False, *, expected_command: Optional[str] = None) -> str:
    """Add an entry to HKCU Run.

    Returns one of:
      "created"   — the value did not exist and was written;
      "overwritten" — the value existed and was replaced (overwrite=True);
      "exists"    — the value existed and overwrite was False (nothing written);
      "invalid"   — empty name or command;
      "stale"     — the value no longer matches the confirmed command;
      "failed"    — the registry write failed.
    """
    name = str(name or "").strip()
    if not name:
        return "invalid"
    try:
        command = _build_registry_command(exe_path, arguments)
    except ValueError:
        return "invalid"

    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            STARTUP_REG_PATH,
            0,
            winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE,
        )
    except OSError as exc:
        log.warning("HKCU Run open failed: %s", exc)
        return "failed"

    try:
        existing = _query_value(key, name)
        if existing is not None and not overwrite:
            return "exists"
        if expected_command is not None and (existing is None or existing[0] != expected_command):
            return "stale"
        winreg.SetValueEx(key, name, 0, winreg.REG_SZ, command)
        return "overwritten" if existing is not None else "created"
    except OSError as exc:
        log.warning("HKCU Run write failed for %s: %s", name, exc)
        return "failed"
    finally:
        try:
            winreg.CloseKey(key)
        except OSError:
            pass


def _query_value(key, name: str) -> Optional[tuple]:
    try:
        return winreg.QueryValueEx(key, name)
    except FileNotFoundError:
        return None


def get_registry_startup_command(name: str, source: str = "registry") -> Optional[str]:
    """Get the current command for confirmation, propagating access errors."""
    root, path = _get_registry_source(source)
    try:
        with winreg.OpenKey(root, path, 0, winreg.KEY_QUERY_VALUE) as key:
            value = _query_value(key, name)
    except FileNotFoundError:
        return None
    if value is None:
        return None
    if not isinstance(value[0], str):
        raise OSError("启动项注册表值不是文本命令")
    return value[0]


def get_registry_value_ref(name: str, source: str = "registry") -> Optional[RegistryValueRef]:
    root, path = _get_registry_source(source)
    try:
        with winreg.OpenKey(root, path, 0, winreg.KEY_QUERY_VALUE) as key:
            data, value_type = winreg.QueryValueEx(key, name)
    except OSError:
        return None
    return _registry_ref(name, source, data, value_type)


def _registry_ref(name: str, source: str, data, value_type: int) -> RegistryValueRef:
    if isinstance(data, str):
        raw = data.encode("utf-16-le")
    elif isinstance(data, (bytes, bytearray)):
        raw = bytes(data)
    elif isinstance(data, list):
        raw = "\0".join(str(part) for part in data).encode("utf-16-le")
    else:
        raw = str(data).encode("utf-16-le")
    return RegistryValueRef(source=source, name=name, value_type=value_type,
                            data_b64=base64.b64encode(raw).decode("ascii"))


def write_registry_value(ref: RegistryValueRef, *, overwrite: bool = True) -> bool:
    try:
        root, path = _get_registry_source(ref.source)
        raw = ref.data()
        if ref.value_type in (winreg.REG_SZ, winreg.REG_EXPAND_SZ):
            data = raw.decode("utf-16-le")
        elif ref.value_type == winreg.REG_MULTI_SZ:
            data = raw.decode("utf-16-le").split("\0") if raw else []
        elif ref.value_type in (winreg.REG_DWORD, winreg.REG_DWORD_BIG_ENDIAN, winreg.REG_QWORD):
            data = int(raw.decode("utf-16-le"))
        else:
            data = raw
        access = winreg.KEY_SET_VALUE | (0 if overwrite else winreg.KEY_QUERY_VALUE)
        with winreg.OpenKey(root, path, 0, access) as key:
            if not overwrite and _query_value(key, ref.name) is not None:
                return False
            winreg.SetValueEx(key, ref.name, 0, ref.value_type, data)
            return True
    except (OSError, ValueError, TypeError, OverflowError) as exc:
        log.warning("registry restore failed for %s: %s", ref.name, exc)
        return False


# ---------------------------------------------------------------------------
# Remove
# ---------------------------------------------------------------------------

def remove_registry_startup(name: str) -> bool:
    """Remove a HKCU Run entry. Returns True on success."""
    return remove_registry_startup_by_source(name, "registry")


def remove_registry_startup_by_source(name: str, source: str) -> bool:
    """Remove a registry startup entry by source. HKLM sources may need admin."""
    if not name:
        return False
    try:
        root, path = _get_registry_source(source)
    except UnknownSourceError as exc:
        log.warning("%s", exc)
        return False
    try:
        key = winreg.OpenKey(root, path, 0, winreg.KEY_SET_VALUE)
    except OSError as exc:
        log.warning("registry startup removal open failed (%s): %s", source, exc)
        return False
    try:
        winreg.DeleteValue(key, name)
        return True
    except OSError as exc:
        log.warning("registry startup removal failed for %s: %s", name, exc)
        return False
    finally:
        try:
            winreg.CloseKey(key)
        except OSError:
            pass


def remove_startup_folder_item(path: str, source: str = "startup_folder") -> bool:
    """Remove one specific file from a startup folder.

    ``path`` must be an explicit file path inside that startup folder; a bare
    name is rejected so a same-named item in another extension is never touched.
    """
    try:
        folder = folder_for_source(source)
    except UnknownSourceError as exc:
        log.warning("%s", exc)
        return False

    candidate = _normalize_path(path)
    if not candidate or os.path.basename(candidate) in ("", ".", ".."):
        return False
    if not _is_startup_file_path(candidate, folder):
        log.warning("refusing to delete outside %s: %s", folder, path)
        return False
    if not os.path.isfile(candidate):
        return False
    try:
        os.remove(candidate)
        return True
    except OSError as exc:
        log.warning("startup folder removal failed for %s: %s", candidate, exc)
        return False


def remove_startup_folder_item_by_source(name: str, source: str) -> bool:
    """Legacy name-based removal.

    Only accepted when the name resolves to exactly one file in the folder;
    ambiguous names (demo.cmd + demo.lnk) are refused. Prefer
    :func:`remove_startup_folder_item` with an explicit path.
    """
    try:
        folder = folder_for_source(source)
    except UnknownSourceError as exc:
        log.warning("%s", exc)
        return False

    matches = [item for item in _get_startup_folder_items(folder, source) if item.name == name]
    if len(matches) != 1:
        log.warning("ambiguous or missing startup folder name %r in %s (%d matches)",
                    name, folder, len(matches))
        return False
    return remove_startup_folder_item(matches[0].path, source)


def remove_item(item: StartupItem) -> bool:
    """Remove a startup item using its own identity."""
    if item.kind == "folder":
        return remove_startup_folder_item(item.path, item.source)
    return remove_registry_startup_by_source(item.name, item.source)


# ---------------------------------------------------------------------------
# Backup / restore
# ---------------------------------------------------------------------------

def _data_root(data_root: Optional[Path] = None) -> Path:
    if data_root is not None:
        return Path(data_root)
    env = os.environ.get("PC_AUTO_SCRIPTS_DATA_DIR")
    if env:
        return Path(env)
    return Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve().parent


def _load_ledger(data_root: Optional[Path] = None) -> List[dict]:
    store = _data_root(data_root) / BACKUP_FILENAME
    with _ledger_lock:
        try:
            raw = json.loads(store.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
    if not isinstance(raw, list) or any(not isinstance(entry, dict) for entry in raw):
        raise ValueError("启动项备份台账格式错误")
    return raw


def _save_ledger(entries: List[dict], data_root: Optional[Path] = None) -> bool:
    root = _data_root(data_root)
    store = root / BACKUP_FILENAME
    with _ledger_lock:
        try:
            root.mkdir(parents=True, exist_ok=True)
            tmp = store.with_suffix(store.suffix + ".tmp")
            tmp.write_text(json.dumps(entries[-_MAX_BACKUP_ENTRIES:], indent=2, ensure_ascii=False),
                           encoding="utf-8")
            os.replace(tmp, store)
            return True
        except OSError as exc:
            log.warning("could not write startup backup ledger: %s", exc)
            return False


def _append_ledger_entry(entry: dict, data_root: Optional[Path] = None) -> bool:
    with _ledger_lock:
        try:
            entries = _load_ledger(data_root)
        except (OSError, ValueError) as exc:
            log.warning("could not read startup backup ledger: %s", exc)
            return False
        identity = _entry_identity(entry)
        if identity is None:
            return False
        kept = [existing for existing in entries if _entry_identity(existing) != identity]
        kept = (kept + [entry])[-_MAX_BACKUP_ENTRIES:]
        if not _save_ledger(kept, data_root):
            return False
        for displaced in entries:
            if displaced not in kept:
                _delete_backup_file(displaced, data_root)
        return True


def _entry_identity(entry: dict) -> Optional[str]:
    source = entry.get("source")
    kind = entry.get("kind")
    if kind == "folder" and source in FOLDER_SOURCES:
        field = entry.get("path")
    elif kind == "registry" and source in KNOWN_SOURCES and source not in FOLDER_SOURCES:
        field = entry.get("name")
    else:
        return None
    return normalize_key(source, field) if isinstance(field, str) and field else None


def _backup_file_path(entry: dict, data_root: Optional[Path]) -> Optional[Path]:
    path = entry.get("backup_path")
    if entry.get("kind") != "folder" or not isinstance(path, str) or not path:
        return None
    try:
        backup_root = (_data_root(data_root) / BACKUP_DIRNAME).resolve()
        candidate = Path(path).resolve(strict=True)
        if candidate.parent == backup_root and candidate.is_file():
            return candidate
    except (OSError, RuntimeError, ValueError):
        pass
    return None


def _delete_backup_file(entry: dict, data_root: Optional[Path]) -> None:
    backup_path = _backup_file_path(entry, data_root)
    if backup_path is not None:
        try:
            backup_path.unlink(missing_ok=True)
        except OSError as exc:
            log.warning("could not delete obsolete backup file %s: %s", backup_path, exc)


def backup_registry_value(name: str, source: str,
                          data_root: Optional[Path] = None) -> Optional[dict]:
    """Store a registry value (type + data) so removal can be undone."""
    ref = get_registry_value_ref(name, source)
    if ref is None:
        return None
    return _backup_registry_ref(ref, data_root)


def _backup_registry_ref(ref: RegistryValueRef, data_root: Optional[Path]) -> Optional[dict]:
    entry = {
        "identity": normalize_key(ref.source, ref.name),
        "kind": "registry",
        "source": ref.source,
        "name": ref.name,
        "value_type": ref.value_type,
        "data_b64": ref.data_b64,
    }
    return entry if _append_ledger_entry(entry, data_root) else None


def backup_folder_item(path: str, source: str,
                       data_root: Optional[Path] = None) -> Optional[dict]:
    """Copy a startup file into the backup folder so removal can be undone."""
    try:
        folder = folder_for_source(source)
    except UnknownSourceError:
        return None
    if not _is_startup_file_path(path, folder):
        return None

    root = _data_root(data_root)
    backup_dir = root / BACKUP_DIRNAME
    target = None
    try:
        original = os.path.abspath(path)
        if not stat.S_ISREG(os.stat(original, follow_symlinks=False).st_mode):
            return None
        backup_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=backup_dir, delete=False) as backup:
            target = Path(backup.name)
        shutil.copy2(original, target)
    except OSError as exc:
        log.warning("could not back up startup file %s: %s", path, exc)
        if target is not None:
            _delete_backup_file({"kind": "folder", "backup_path": str(target)}, data_root)
        return None

    entry = {
        "identity": normalize_key(source, path),
        "kind": "folder",
        "source": source,
        "name": os.path.splitext(os.path.basename(original))[0],
        "path": original,
        "backup_path": str(target),
    }
    if _append_ledger_entry(entry, data_root):
        return entry
    _delete_backup_file(entry, data_root)
    return None


def backup_item(item: StartupItem, data_root: Optional[Path] = None) -> Optional[dict]:
    if item.kind == "folder":
        return backup_folder_item(item.path, item.source, data_root)
    return backup_registry_value(item.name, item.source, data_root)


def remove_with_backup(item: StartupItem, data_root: Optional[Path] = None) -> str:
    """Delete the listed item after backing it up. Return a user-facing outcome key."""
    with _ledger_lock:
        try:
            if item.kind == "folder":
                folder = folder_for_source(item.source)
                if not _is_startup_file_path(item.path, folder):
                    return "remove_failed"
                info = os.stat(item.path, follow_symlinks=False)
                version = _file_version(info)
                if not stat.S_ISREG(info.st_mode) or (
                        item.file_version is not None and item.file_version != version):
                    return "stale"
                if backup_folder_item(item.path, item.source, data_root) is None:
                    return "backup_failed"
                if _file_version(os.stat(item.path, follow_symlinks=False)) != version:
                    return "stale"
                os.remove(item.path)
            else:
                if not item.name:
                    return "remove_failed"
                root, path = _get_registry_source(item.source)
                with winreg.OpenKey(root, path, 0, winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE) as key:
                    current = _query_value(key, item.name)
                    if current is None or current[0] != item.path:
                        return "stale"
                    ref = _registry_ref(item.name, item.source, *current)
                    if _backup_registry_ref(ref, data_root) is None:
                        return "backup_failed"
                    if _query_value(key, item.name) != current:
                        return "stale"
                    winreg.DeleteValue(key, item.name)
        except FileNotFoundError:
            return "stale"
        except (OSError, ValueError) as exc:
            log.warning("startup removal failed for %s: %s", item.name, exc)
            return "remove_failed"
    return "removed"


def list_backups(data_root: Optional[Path] = None) -> List[dict]:
    """Ledger entries whose original is currently missing (candidates to restore)."""
    entries = _load_ledger(data_root)
    pending: List[dict] = []
    seen = set()
    for entry in reversed(entries):
        identity = _entry_identity(entry)
        if identity is None or identity in seen:
            continue
        seen.add(identity)
        if entry.get("kind") == "folder":
            if not os.path.lexists(entry["path"]):
                pending.append(entry)
        else:
            root, path = _get_registry_source(entry["source"])
            try:
                with winreg.OpenKey(root, path, 0, winreg.KEY_QUERY_VALUE) as key:
                    if _query_value(key, entry["name"]) is None:
                        pending.append(entry)
            except FileNotFoundError:
                pending.append(entry)
            except OSError as exc:
                log.warning("could not check startup backup %s: %s", identity, exc)
    return pending


def restore_backup(entry: dict, data_root: Optional[Path] = None) -> bool:
    """Restore the confirmed ledger entry if its destination is still absent."""
    if not isinstance(entry, dict):
        return False
    with _ledger_lock:
        try:
            identity = _entry_identity(entry)
            entries = _load_ledger(data_root)
            matches = [current for current in entries if _entry_identity(current) == identity]
            if identity is None or not matches or entry != matches[-1]:
                return False
            if not _restore_entry(entry, data_root):
                return False
        except (OSError, ValueError, TypeError) as exc:
            log.warning("could not restore startup entry %s: %s", entry.get("name"), exc)
            return False
        if _save_ledger([current for current in entries if current not in matches], data_root):
            for restored in matches:
                _delete_backup_file(restored, data_root)
        return True


def _restore_entry(entry: dict, data_root: Optional[Path]) -> bool:
    if entry.get("kind") == "folder":
        backup_path = _backup_file_path(entry, data_root)
        target = Path(entry["path"])
        folder = folder_for_source(entry["source"])
        if backup_path is None or not _is_startup_file_path(str(target), folder):
            return False
        target.parent.mkdir(parents=True, exist_ok=True)
        created = False
        try:
            with backup_path.open("rb") as original, target.open("xb") as destination:
                created = True
                shutil.copyfileobj(original, destination)
            shutil.copystat(backup_path, target)
        except OSError:
            if created:
                target.unlink(missing_ok=True)
            raise
        return True
    value_type = entry.get("value_type")
    data_b64 = entry.get("data_b64")
    if not isinstance(value_type, int) or not isinstance(data_b64, str):
        return False
    ref = RegistryValueRef(entry["source"], entry["name"], value_type, data_b64)
    return write_registry_value(ref, overwrite=False)


def restore_last_backup(data_root: Optional[Path] = None) -> Optional[dict]:
    """Restore the most recent removable entry. Returns the restored entry."""
    pending = list_backups(data_root)
    if not pending:
        return None
    entry = pending[0]
    return entry if restore_backup(entry, data_root) else None


# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------

def resolve_startup_target_path(command: str) -> str:
    """Best-effort extraction of the file path from a startup command."""
    raw = command.strip()
    if not raw:
        return ""

    if raw[0] in ("'", '"'):
        quote = raw[0]
        end = raw.find(quote, 1)
        if end > 0:
            return raw[1:end]

    lower = raw.lower()
    for ext in (".exe", ".bat", ".cmd", ".ps1", ".vbs", ".lnk", ".url"):
        idx = lower.find(ext)
        if idx >= 0:
            return raw[:idx + len(ext)].strip().strip('"')

    return raw.split()[0].strip('"')


def open_startup_location(item: StartupItem) -> bool:
    """Open Explorer at the startup item target, selecting the file when possible."""
    target = item.path if item.kind == "folder" else resolve_startup_target_path(item.path)
    if not target:
        return False

    target = os.path.expandvars(target).strip()
    if os.path.exists(target):
        subprocess.Popen(["explorer", f"/select,{target}"])
        return True

    parent = os.path.dirname(target)
    if parent and os.path.isdir(parent):
        subprocess.Popen(["explorer", parent])
        return True

    return False


def _script_path() -> Path:
    """The real script path, even when argv[0] is a python flag such as '-c'."""
    argv0 = sys.argv[0] if getattr(sys, "argv", None) else ""
    if argv0 and not argv0.startswith("-"):
        try:
            candidate = Path(argv0).resolve()
            if candidate.is_file():
                return candidate
        except OSError:
            pass
    return Path(__file__).resolve()


def get_app_exe_path() -> str:
    """Absolute path to the current executable, or to the running script."""
    if getattr(sys, "frozen", False):
        return str(Path(sys.executable).resolve())
    return str(_script_path())


def build_self_startup_command() -> str:
    """Correct startup command for this app, quoting each path separately."""
    return _build_registry_command(*build_self_startup_parts())


def build_self_startup_parts() -> Tuple[str, str]:
    """Executable and arguments for add_registry_startup."""
    if getattr(sys, "frozen", False):
        return str(Path(sys.executable).resolve()), "--startup"
    script = _script_path()
    if script.suffix.lower() == ".py":
        return str(Path(sys.executable).resolve()), f'"{script}" --startup'
    return str(script), "--startup"
