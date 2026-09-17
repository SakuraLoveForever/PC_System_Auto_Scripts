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
import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
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
_ledger_lock = threading.Lock()


class UnknownSourceError(ValueError):
    """The caller passed a startup source this module does not manage."""


@dataclass
class StartupItem:
    name: str
    path: str
    source: str

    @property
    def kind(self) -> str:
        return "folder" if self.source in FOLDER_SOURCES else "registry"

    def identity(self) -> str:
        """Stable row/list key. File items are keyed by path, not by name."""
        if self.kind == "folder":
            return f"{self.source}:{_normalize_path(self.path)}"
        return f"{self.source}:{self.name}"


def normalize_key(source: str, name_or_path: str) -> str:
    """Key helper for callers that only hold a source plus name/path."""
    if source in FOLDER_SOURCES:
        return f"{source}:{_normalize_path(name_or_path)}"
    return f"{source}:{name_or_path}"


@dataclass
class RegistryValueRef:
    """Everything needed to recreate a registry value exactly."""

    source: str
    name: str
    value_type: int
    data_b64: str

    def data(self):
        return base64.b64decode(self.data_b64)


def _normalize_path(path: str) -> str:
    """Absolute, case-normalized path used for identity and containment checks."""
    expanded = os.path.expandvars(str(path or "").strip().strip('"'))
    try:
        expanded = os.path.abspath(expanded)
    except OSError:
        pass
    return os.path.normcase(expanded)


def _stable_id(text: str) -> str:
    """Deterministic short id for backup file names (hash() is randomized)."""
    return hashlib.sha1(os.path.normcase(text).encode("utf-8", errors="replace")).hexdigest()[:8]


def _get_startup_folder() -> str:
    return os.path.join(
        os.environ.get("APPDATA", ""),
        r"Microsoft\Windows\Start Menu\Programs\Startup",
    )


def _get_common_startup_folder() -> str:
    return os.path.join(
        os.environ.get("PROGRAMDATA", ""),
        r"Microsoft\Windows\Start Menu\Programs\Startup",
    )


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


def _is_within(path: str, folder: str) -> bool:
    """True when path is inside folder (no prefix tricks, no traversal)."""
    if not path or not folder:
        return False
    target = _normalize_path(path)
    root = _normalize_path(folder)
    if not root:
        return False
    try:
        return os.path.commonpath([target, root]) == root
    except ValueError:
        return False


def get_registry_items() -> List[StartupItem]:
    """Get startup items from common registry Run keys."""
    items = []
    for source, root, path in REGISTRY_SOURCES:
        try:
            key = winreg.OpenKey(root, path)
            i = 0
            while True:
                try:
                    name, value, _ = winreg.EnumValue(key, i)
                    items.append(StartupItem(name=name, path=value, source=source))
                    i += 1
                except OSError:
                    break
            winreg.CloseKey(key)
        except OSError as exc:
            log.warning("registry startup read failed for %s: %s", source, exc)
    return items


def _get_startup_folder_items(folder: str, source: str) -> List[StartupItem]:
    items = []
    if not folder or not os.path.isdir(folder):
        return items
    for entry in os.listdir(folder):
        path = os.path.join(folder, entry)
        name = os.path.splitext(entry)[0]
        if entry.lower().endswith((".lnk", ".url")):
            items.append(StartupItem(name=name, path=path, source=source))
        elif os.path.isfile(path):
            items.append(StartupItem(name=name, path=path, source=source))
    return items


def get_startup_folder_items() -> List[StartupItem]:
    """Get startup items from user and common Startup folders."""
    items: List[StartupItem] = []
    for source in FOLDER_SOURCES:
        items.extend(_get_startup_folder_items(folder_for_source(source), source))
    return items


def get_all_items() -> List[StartupItem]:
    """Get all startup items from registry Run keys and Startup folders."""
    return get_registry_items() + get_startup_folder_items()


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
    if not target:
        raise ValueError("empty executable path")
    command = f'"{target}"'
    args = str(arguments or "").strip()
    return f"{command} {args}".strip()


def add_registry_startup(name: str, exe_path: str, arguments: str = "",
                         overwrite: bool = False) -> str:
    """Add an entry to HKCU Run.

    Returns one of:
      "created"   — the value did not exist and was written;
      "overwritten" — the value existed and was replaced (overwrite=True);
      "exists"    — the value existed and overwrite was False (nothing written);
      "invalid"   — empty name or command;
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
    except OSError:
        return None


def get_registry_value_ref(name: str, source: str = "registry") -> Optional[RegistryValueRef]:
    root, path = _get_registry_source(source)
    try:
        with winreg.OpenKey(root, path, 0, winreg.KEY_QUERY_VALUE) as key:
            data, value_type = winreg.QueryValueEx(key, name)
    except OSError:
        return None
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


def write_registry_value(ref: RegistryValueRef) -> bool:
    root, path = _get_registry_source(ref.source)
    raw = ref.data()
    if ref.value_type in (winreg.REG_SZ, winreg.REG_EXPAND_SZ):
        data = raw.decode("utf-16-le", errors="replace")
    elif ref.value_type == winreg.REG_MULTI_SZ:
        data = [part for part in raw.decode("utf-16-le", errors="replace").split("\0") if part]
    else:
        data = raw
    try:
        key = winreg.OpenKey(root, path, 0, winreg.KEY_SET_VALUE)
    except OSError as exc:
        log.warning("registry restore open failed for %s: %s", ref.source, exc)
        return False
    try:
        winreg.SetValueEx(key, ref.name, 0, ref.value_type, data)
        return True
    except OSError as exc:
        log.warning("registry restore failed for %s: %s", ref.name, exc)
        return False
    finally:
        try:
            winreg.CloseKey(key)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Remove
# ---------------------------------------------------------------------------

def remove_registry_startup(name: str) -> bool:
    """Remove a HKCU Run entry. Returns True on success."""
    return remove_registry_startup_by_source(name, "registry")


def remove_registry_startup_by_source(name: str, source: str) -> bool:
    """Remove a registry startup entry by source. HKLM sources may need admin."""
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
    if not _is_within(candidate, folder):
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
        except (OSError, json.JSONDecodeError):
            return []
    return raw if isinstance(raw, list) else []


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
    entries = _load_ledger(data_root)
    identity = entry.get("identity")
    entries = [existing for existing in entries if existing.get("identity") != identity]
    entries.append(entry)
    return _save_ledger(entries, data_root)


def backup_registry_value(name: str, source: str,
                          data_root: Optional[Path] = None) -> Optional[dict]:
    """Store a registry value (type + data) so removal can be undone."""
    ref = get_registry_value_ref(name, source)
    if ref is None:
        return None
    entry = {
        "identity": normalize_key(source, name),
        "kind": "registry",
        "source": source,
        "name": name,
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
    if not _is_within(path, folder) or not os.path.isfile(path):
        return None

    root = _data_root(data_root)
    backup_dir = root / BACKUP_DIRNAME
    try:
        backup_dir.mkdir(parents=True, exist_ok=True)
        original = os.path.abspath(path)
        target = backup_dir / f"{_stable_id(original)}_{os.path.basename(original)}"
        shutil.copy2(original, target)
    except OSError as exc:
        log.warning("could not back up startup file %s: %s", path, exc)
        return None

    entry = {
        "identity": normalize_key(source, path),
        "kind": "folder",
        "source": source,
        "name": os.path.splitext(os.path.basename(original))[0],
        "path": original,
        "backup_path": str(target),
    }
    return entry if _append_ledger_entry(entry, data_root) else None


def backup_item(item: StartupItem, data_root: Optional[Path] = None) -> Optional[dict]:
    if item.kind == "folder":
        return backup_folder_item(item.path, item.source, data_root)
    return backup_registry_value(item.name, item.source, data_root)


def list_backups(data_root: Optional[Path] = None) -> List[dict]:
    """Ledger entries whose original is currently missing (candidates to restore)."""
    entries = _load_ledger(data_root)
    pending: List[dict] = []
    for entry in reversed(entries):
        if entry.get("kind") == "folder":
            if not os.path.exists(entry.get("path", "")):
                pending.append(entry)
        else:
            if get_registry_value_ref(entry.get("name", ""), entry.get("source", "")) is None:
                pending.append(entry)
    return pending


def restore_backup(entry: dict, data_root: Optional[Path] = None) -> bool:
    """Restore one ledger entry. Removes it from the ledger on success."""
    identity = entry.get("identity")
    if entry.get("kind") == "folder":
        backup_path = entry.get("backup_path", "")
        target = entry.get("path", "")
        try:
            folder = folder_for_source(entry.get("source", ""))
        except UnknownSourceError:
            return False
        if not _is_within(target, folder) or not os.path.isfile(backup_path):
            return False
        try:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copy2(backup_path, target)
        except OSError as exc:
            log.warning("startup file restore failed for %s: %s", target, exc)
            return False
    else:
        ref = RegistryValueRef(
            source=entry.get("source", ""),
            name=entry.get("name", ""),
            value_type=int(entry.get("value_type", winreg.REG_SZ)),
            data_b64=entry.get("data_b64", ""),
        )
        try:
            _get_registry_source(ref.source)
        except UnknownSourceError:
            return False
        if not write_registry_value(ref):
            return False

    entries = [existing for existing in _load_ledger(data_root)
               if existing.get("identity") != identity]
    _save_ledger(entries, data_root)
    return True


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
    if getattr(sys, "frozen", False):
        return _build_registry_command(str(Path(sys.executable).resolve()))
    script = _script_path()
    if script.suffix.lower() == ".py":
        return _build_registry_command(str(Path(sys.executable).resolve()), f'"{script}"')
    return _build_registry_command(str(script))