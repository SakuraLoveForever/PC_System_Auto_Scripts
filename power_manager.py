"""Power plan detection and management for Windows."""

import locale
import subprocess
import re
import threading
import time
from dataclasses import dataclass, field
from typing import List, Optional, Callable

_SYS_ENC = locale.getpreferredencoding() or "utf-8"

# Power plan names that are acceptable (case-insensitive matching)
HIGH_PERFORMANCE_PLANS = [
    "ultimate performance",
    "high performance",
    "卓越性能",
    "高性能",
]


@dataclass
class PowerPlan:
    guid: str
    name: str
    is_active: bool = False
    is_acceptable: bool = False


def _parse_powercfg_list(output: str) -> List[PowerPlan]:
    plans = []
    for line in output.splitlines():
        m = re.search(
            r"([a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12})",
            line,
        )
        if m:
            guid = m.group(1)
            rest = line[m.end():]
            name_m = re.search(r"\(([^)]+)\)", rest)
            name = name_m.group(1).strip() if name_m else guid
            is_active = "*" in line
            is_acceptable = _is_acceptable_name(name)
            plans.append(PowerPlan(
                guid=guid,
                name=name,
                is_active=is_active,
                is_acceptable=is_acceptable,
            ))
    return plans


def _is_acceptable_name(name: str) -> bool:
    name_lower = name.lower()
    return any(target in name_lower for target in HIGH_PERFORMANCE_PLANS)


def get_all_plans() -> List[PowerPlan]:
    try:
        output = subprocess.check_output(
            ["powercfg", "/list"],
            encoding=_SYS_ENC,
            errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except (subprocess.CalledProcessError, UnicodeDecodeError):
        return []
    return _parse_powercfg_list(output)


def get_active_plan() -> Optional[PowerPlan]:
    for p in get_all_plans():
        if p.is_active:
            return p
    return None


def set_active_plan(guid: str) -> bool:
    try:
        subprocess.run(
            ["powercfg", "/setactive", guid],
            check=True,
            capture_output=True,
            encoding=_SYS_ENC,
            errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return True
    except subprocess.CalledProcessError:
        return False


def get_acceptable_plans() -> List[PowerPlan]:
    return [p for p in get_all_plans() if p.is_acceptable]


def find_high_performance_plan() -> Optional[PowerPlan]:
    acceptable = get_acceptable_plans()
    return acceptable[0] if acceptable else None


def is_acceptable_plan(plan_name: str) -> bool:
    return _is_acceptable_name(plan_name)


class PowerMonitor:
    """Monitors power plan on a background thread and auto-corrects if needed."""

    def __init__(self, on_status_change: Optional[Callable] = None, interval_seconds: int = 60):
        self._interval = interval_seconds
        self._target_guid: Optional[str] = None  # None = auto-detect first acceptable
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._on_status_change = on_status_change
        self._last_status: Optional[str] = None

    @property
    def interval(self) -> int:
        return self._interval

    @interval.setter
    def interval(self, seconds: int):
        self._interval = max(10, seconds)

    @property
    def target_guid(self) -> Optional[str]:
        return self._target_guid

    @target_guid.setter
    def target_guid(self, guid: Optional[str]):
        self._target_guid = guid

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False

    def _loop(self):
        while self._running:
            self._check_and_fix()
            time.sleep(self._interval)

    def check_now(self) -> dict:
        return self._check_and_fix()

    def _resolve_target(self) -> Optional[PowerPlan]:
        if self._target_guid:
            for p in get_all_plans():
                if p.guid == self._target_guid:
                    return p
        return find_high_performance_plan()

    def _check_and_fix(self) -> dict:
        active = get_active_plan()
        if active is None:
            result = {"status": "error", "plan_name": "Unknown", "fixed": False}
        elif self._is_plan_ok(active):
            result = {"status": "ok", "plan_name": active.name, "fixed": False}
        else:
            target = self._resolve_target()
            if target:
                success = set_active_plan(target.guid)
                result = {
                    "status": "fixed" if success else "fix_failed",
                    "plan_name": active.name,
                    "target_name": target.name,
                    "fixed": success,
                }
            else:
                result = {"status": "no_target", "plan_name": active.name, "fixed": False}

        new_status = f"{result['status']}:{result.get('plan_name', '')}"
        if self._on_status_change and new_status != self._last_status:
            self._last_status = new_status
            self._on_status_change(result)

        return result

    def _is_plan_ok(self, active: PowerPlan) -> bool:
        """Check if the active plan matches the desired target."""
        if self._target_guid:
            return active.guid == self._target_guid
        return is_acceptable_plan(active.name)
