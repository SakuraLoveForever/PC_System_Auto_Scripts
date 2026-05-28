"""Windows startup items management via registry and startup folders."""

import os
import subprocess
import sys
import winreg
from dataclasses import dataclass
from typing import List, Tuple

STARTUP_REG_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
STARTUP_REG_PATH_WOW6432 = r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Run"

REGISTRY_SOURCES: Tuple[tuple, ...] = (
    ("registry", winreg.HKEY_CURRENT_USER, STARTUP_REG_PATH),
    ("registry_hklm", winreg.HKEY_LOCAL_MACHINE, STARTUP_REG_PATH),
    ("registry_hklm_wow6432", winreg.HKEY_LOCAL_MACHINE, STARTUP_REG_PATH_WOW6432),
)


@dataclass
class StartupItem:
    name: str
    path: str
    source: str


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


def _get_registry_source(source: str):
    for source_name, root, path in REGISTRY_SOURCES:
        if source_name == source:
            return root, path
    return winreg.HKEY_CURRENT_USER, STARTUP_REG_PATH


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
        except OSError:
            pass
    return items


def _get_startup_folder_items(folder: str, source: str) -> List[StartupItem]:
    items = []
    if not os.path.isdir(folder):
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
    return (
        _get_startup_folder_items(_get_startup_folder(), "startup_folder")
        + _get_startup_folder_items(_get_common_startup_folder(), "startup_folder_common")
    )


def get_all_items() -> List[StartupItem]:
    """Get all startup items from registry Run keys and Startup folders."""
    return get_registry_items() + get_startup_folder_items()


def add_registry_startup(name: str, exe_path: str) -> bool:
    """Add an entry to HKCU Run. Returns True on success."""
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            STARTUP_REG_PATH,
            0,
            winreg.KEY_SET_VALUE,
        )
        winreg.SetValueEx(key, name, 0, winreg.REG_SZ, exe_path)
        winreg.CloseKey(key)
        return True
    except OSError:
        return False


def remove_registry_startup(name: str) -> bool:
    """Remove a HKCU Run entry. Returns True on success."""
    return remove_registry_startup_by_source(name, "registry")


def remove_registry_startup_by_source(name: str, source: str) -> bool:
    """Remove a registry startup entry by source. HKLM sources may need admin."""
    root, path = _get_registry_source(source)
    try:
        key = winreg.OpenKey(
            root,
            path,
            0,
            winreg.KEY_SET_VALUE,
        )
        winreg.DeleteValue(key, name)
        winreg.CloseKey(key)
        return True
    except OSError:
        return False


def remove_startup_folder_item(name: str) -> bool:
    """Remove a file from the current user's startup folder."""
    return remove_startup_folder_item_by_source(name, "startup_folder")


def remove_startup_folder_item_by_source(name: str, source: str) -> bool:
    """Remove a file from a startup folder. Common folder may need admin."""
    folder = _get_common_startup_folder() if source == "startup_folder_common" else _get_startup_folder()
    if not os.path.isdir(folder):
        return False
    for entry in os.listdir(folder):
        entry_name = os.path.splitext(entry)[0]
        if entry_name == name:
            try:
                os.remove(os.path.join(folder, entry))
                return True
            except OSError:
                return False
    return False


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
    target = item.path if item.source.startswith("startup_folder") else resolve_startup_target_path(item.path)
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


def get_app_exe_path() -> str:
    """Get the path to the current executable or script."""
    if getattr(sys, "frozen", False):
        return sys.executable
    return sys.argv[0]
