"""Windows startup items management via registry."""

import os
import sys
import winreg
from dataclasses import dataclass
from typing import List

STARTUP_REG_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"


@dataclass
class StartupItem:
    name: str
    path: str
    source: str  # "registry" or "startup_folder"


def _get_startup_folder() -> str:
    return os.path.join(
        os.environ.get("APPDATA", ""),
        r"Microsoft\Windows\Start Menu\Programs\Startup",
    )


def get_registry_items() -> List[StartupItem]:
    """Get startup items from HKCU registry Run key."""
    items = []
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, STARTUP_REG_PATH)
        i = 0
        while True:
            try:
                name, value, _ = winreg.EnumValue(key, i)
                items.append(StartupItem(name=name, path=value, source="registry"))
                i += 1
            except OSError:
                break
        winreg.CloseKey(key)
    except OSError:
        pass
    return items


def get_startup_folder_items() -> List[StartupItem]:
    """Get startup items from the Startup folder (shortcuts)."""
    items = []
    folder = _get_startup_folder()
    if not os.path.isdir(folder):
        return items
    for entry in os.listdir(folder):
        path = os.path.join(folder, entry)
        name = os.path.splitext(entry)[0]
        if entry.lower().endswith((".lnk", ".url")):
            items.append(StartupItem(name=name, path=path, source="startup_folder"))
        elif os.path.isfile(path):
            items.append(StartupItem(name=name, path=path, source="startup_folder"))
    return items


def get_all_items() -> List[StartupItem]:
    """Get all startup items from both registry and startup folder."""
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
    """Remove an entry from HKCU Run. Returns True on success."""
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            STARTUP_REG_PATH,
            0,
            winreg.KEY_SET_VALUE,
        )
        winreg.DeleteValue(key, name)
        winreg.CloseKey(key)
        return True
    except OSError:
        return False


def remove_startup_folder_item(name: str) -> bool:
    """Remove a file from the startup folder. Returns True on success."""
    folder = _get_startup_folder()
    for entry in os.listdir(folder):
        entry_name = os.path.splitext(entry)[0]
        if entry_name == name:
            try:
                os.remove(os.path.join(folder, entry))
                return True
            except OSError:
                return False
    return False


def get_app_exe_path() -> str:
    """Get the path to the current executable or script."""
    if getattr(sys, "frozen", False):
        return sys.executable
    return sys.argv[0]
