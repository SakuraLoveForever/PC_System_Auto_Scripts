"""Power plan detection and management for Windows — registry + Win32 API, with subprocess fallback."""

import ctypes
import re
import subprocess
import threading
import time
import uuid
import winreg
from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_uint32),
        ("Data2", ctypes.c_uint16),
        ("Data3", ctypes.c_uint16),
        ("Data4", ctypes.c_uint8 * 8),
    ]

# Power scheme registry path
_POWER_SCHEMES_KEY = r"SYSTEM\CurrentControlSet\Control\Power\User\PowerSchemes"

# Power plan names/patterns to exclude (internal/hidden Windows overlay schemes)
_HIDDEN_PLAN_PATTERNS = [
    "overlay",
]


def _is_hidden_plan(name: str) -> bool:
    name_lower = name.lower()
    return any(pattern in name_lower for pattern in _HIDDEN_PLAN_PATTERNS)


# Power plan names that are acceptable (case-insensitive matching)
HIGH_PERFORMANCE_PLANS = [
    "ultimate performance",
    "high performance",
    "卓越性能",
    "高性能",
]


@dataclass(frozen=True)
class _BuiltinPowerScheme:
    source_guid: str
    default_name: str
    match_names: Sequence[str]
    is_acceptable: bool = False


# Windows 11 / Modern Standby machines often expose only Balanced until the
# classic schemes are duplicated from their built-in templates.
_BUILTIN_POWER_SCHEMES: Sequence[_BuiltinPowerScheme] = (
    _BuiltinPowerScheme(
        source_guid="e9a42b02-d5df-448d-aa00-03f14749eb61",
        default_name="卓越性能",
        match_names=("ultimate performance", "卓越性能"),
        is_acceptable=True,
    ),
    _BuiltinPowerScheme(
        source_guid="8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c",
        default_name="高性能",
        match_names=("high performance", "高性能"),
        is_acceptable=True,
    ),
)

_BUILTIN_HIGH_PERFORMANCE_GUIDS = {
    scheme.source_guid for scheme in _BUILTIN_POWER_SCHEMES if scheme.is_acceptable
}
_builtin_recovery_attempted = False

_GUID_RE = re.compile(
    r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"
)


@dataclass
class PowerPlan:
    guid: str
    name: str
    is_active: bool = False
    is_acceptable: bool = False


def _is_acceptable_name(name: str) -> bool:
    name_lower = name.lower()
    return any(target in name_lower for target in HIGH_PERFORMANCE_PLANS)


def _is_acceptable_guid(guid: str) -> bool:
    return guid.lower() in _BUILTIN_HIGH_PERFORMANCE_GUIDS


def _is_acceptable_plan(guid: str, name: str) -> bool:
    return _is_acceptable_guid(guid) or _is_acceptable_name(name)


def _resolve_indirect_string(raw: str) -> str:
    """Resolve MUI indirect strings like @%SystemRoot%\\system32\\powrprof.dll,-19,fallback."""
    if not raw.startswith("@"):
        return raw
    # Try SHLoadIndirectString first
    try:
        buf = ctypes.create_unicode_buffer(512)
        result = ctypes.windll.shlwapi.SHLoadIndirectString(raw, buf, 512, None)
        if result == 0 and buf.value:
            return buf.value
    except OSError:
        pass
    # Fallback: extract text after the last comma (the display fallback)
    # Format: @path,-resId,Fallback Display Name
    comma_idx = raw.rfind(",")
    if comma_idx >= 0:
        after = raw[comma_idx + 1:]
        # Make sure it's not just a number (resource ID)
        if after and not after.lstrip("-").isdigit():
            return after
    return raw


def _read_reg_value(key_path: str, value_name: str) -> Optional[str]:
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as key:
            data, _ = winreg.QueryValueEx(key, value_name)
            return str(data)
    except OSError:
        return None


def _subprocess_flags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _run_powercfg(args: List[str]) -> Optional[str]:
    """Run powercfg and decode localized Windows output reliably."""
    encodings = ["mbcs", "utf-8", "gbk", "cp936"]
    for encoding in encodings:
        try:
            proc = subprocess.run(
                ["powercfg", *args],
                capture_output=True,
                text=True,
                encoding=encoding,
                errors="replace",
                creationflags=_subprocess_flags(),
                timeout=10,
            )
            if proc.returncode == 0 and proc.stdout:
                return proc.stdout
        except (LookupError, OSError, subprocess.SubprocessError):
            continue
    return None


def _decode_powercfg_bytes(data: bytes) -> str:
    for encoding in ("mbcs", "utf-8", "gbk", "cp936"):
        try:
            text = data.decode(encoding, errors="replace")
            if text:
                return text
        except LookupError:
            continue
    return data.decode("utf-8", errors="replace")


def _run_powercfg_once(args: List[str]) -> tuple[bool, str]:
    """Run a mutating powercfg command once and return combined output."""
    try:
        proc = subprocess.run(
            ["powercfg", *args],
            capture_output=True,
            creationflags=_subprocess_flags(),
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return False, ""

    output = _decode_powercfg_bytes((proc.stdout or b"") + (proc.stderr or b""))
    return proc.returncode == 0, output


def _plan_name_from_powercfg_line(line: str, guid: str) -> str:
    # powercfg /list prints localized labels, but the visible plan name is
    # consistently wrapped in parentheses before the optional active marker.
    match = re.search(r"\((.*?)\)\s*\*?\s*$", line)
    if match and match.group(1).strip():
        return match.group(1).strip()

    rest = line.split(guid, 1)[-1].strip()
    rest = rest.strip("*").strip()
    return rest or guid


def _get_plans_from_powercfg() -> List[PowerPlan]:
    output = _run_powercfg(["/list"])
    if not output:
        return []

    plans: List[PowerPlan] = []
    seen = set()
    for line in output.splitlines():
        guid_match = _GUID_RE.search(line)
        if not guid_match:
            continue
        guid = guid_match.group(1).lower()
        if guid in seen:
            continue
        seen.add(guid)

        name = _plan_name_from_powercfg_line(line, guid_match.group(1))
        if name.startswith("@") or _is_hidden_plan(name):
            continue

        plans.append(PowerPlan(
            guid=guid,
            name=name,
            is_active="*" in line,
            is_acceptable=_is_acceptable_plan(guid, name),
        ))

    if any(p.is_active for p in plans):
        return plans

    active_output = _run_powercfg(["/getactivescheme"])
    active_guid = None
    if active_output:
        match = _GUID_RE.search(active_output)
        if match:
            active_guid = match.group(1).lower()
    if active_guid:
        for plan in plans:
            plan.is_active = plan.guid == active_guid
    return plans


def _get_plans_from_registry() -> List[PowerPlan]:
    """Read power plans from registry as a fallback when powercfg is unavailable."""
    plans = []
    active_guid = (_read_reg_value(_POWER_SCHEMES_KEY, "ActivePowerScheme") or "").lower()

    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _POWER_SCHEMES_KEY) as schemes_key:
            i = 0
            while True:
                try:
                    guid = winreg.EnumKey(schemes_key, i)
                except OSError:
                    break
                i += 1

                try:
                    plan_key = winreg.OpenKey(schemes_key, guid)
                except OSError:
                    continue

                with plan_key:
                    name = guid
                    try:
                        raw_name, _ = winreg.QueryValueEx(plan_key, "FriendlyName")
                        name = _resolve_indirect_string(str(raw_name))
                    except OSError:
                        pass

                if name.startswith("@") or _is_hidden_plan(name):
                    continue

                normalized_guid = guid.lower()
                plans.append(PowerPlan(
                    guid=normalized_guid,
                    name=name,
                    is_active=normalized_guid == active_guid,
                    is_acceptable=_is_acceptable_plan(normalized_guid, name),
                ))
    except OSError:
        pass

    return plans


def _active_power_scheme_guid() -> Optional[str]:
    active_output = _run_powercfg(["/getactivescheme"])
    if active_output:
        match = _GUID_RE.search(active_output)
        if match:
            return match.group(1).lower()

    active_guid = (_read_reg_value(_POWER_SCHEMES_KEY, "ActivePowerScheme") or "").lower()
    return active_guid or None


def _merge_plans(*sources: Sequence[PowerPlan]) -> List[PowerPlan]:
    merged: dict[str, PowerPlan] = {}
    for source in sources:
        for plan in source:
            guid = plan.guid.lower()
            if guid in merged:
                current = merged[guid]
                name = current.name
                if (not name or name == current.guid) and plan.name:
                    name = plan.name
                current.name = name
                current.is_active = current.is_active or plan.is_active
                current.is_acceptable = (
                    current.is_acceptable
                    or plan.is_acceptable
                    or _is_acceptable_plan(guid, name)
                )
            else:
                merged[guid] = PowerPlan(
                    guid=guid,
                    name=plan.name,
                    is_active=plan.is_active,
                    is_acceptable=plan.is_acceptable or _is_acceptable_plan(guid, plan.name),
                )

    active_guid = _active_power_scheme_guid()
    if active_guid:
        for plan in merged.values():
            plan.is_active = plan.guid == active_guid

    return list(merged.values())


def _matches_builtin_scheme(plan: PowerPlan, scheme: _BuiltinPowerScheme) -> bool:
    if plan.guid == scheme.source_guid:
        return True
    name_lower = plan.name.lower()
    return any(match_name in name_lower for match_name in scheme.match_names)


def _duplicate_builtin_scheme(scheme: _BuiltinPowerScheme) -> Optional[PowerPlan]:
    ok, output = _run_powercfg_once(["/duplicatescheme", scheme.source_guid])
    if not ok:
        return None

    match = _GUID_RE.search(output)
    guid = (match.group(1) if match else scheme.source_guid).lower()
    name = _plan_name_from_powercfg_line(output, guid) if output else scheme.default_name
    if name == guid:
        name = scheme.default_name

    return PowerPlan(
        guid=guid,
        name=name,
        is_active=False,
        is_acceptable=scheme.is_acceptable or _is_acceptable_plan(guid, name),
    )


def _restore_missing_builtin_schemes(plans: Sequence[PowerPlan]) -> List[PowerPlan]:
    global _builtin_recovery_attempted
    if _builtin_recovery_attempted:
        return []
    _builtin_recovery_attempted = True

    restored: List[PowerPlan] = []
    current = list(plans)
    for scheme in _BUILTIN_POWER_SCHEMES:
        if any(_matches_builtin_scheme(plan, scheme) for plan in current):
            continue
        plan = _duplicate_builtin_scheme(scheme)
        if plan:
            restored.append(plan)
            current.append(plan)
    return restored


def get_all_plans() -> List[PowerPlan]:
    """Read Windows power plans and recover hidden Win11 performance schemes."""
    plans = _merge_plans(_get_plans_from_powercfg(), _get_plans_from_registry())
    restored = _restore_missing_builtin_schemes(plans)
    if restored:
        plans = _merge_plans(plans, restored, _get_plans_from_powercfg(), _get_plans_from_registry())
    return plans


def get_active_plan() -> Optional[PowerPlan]:
    for p in get_all_plans():
        if p.is_active:
            return p
    return None


def set_active_plan(guid_str: str) -> bool:
    """Set active power plan via PowerSetActiveScheme with powercfg fallback."""
    # Try direct Win32 API first
    try:
        u = uuid.UUID(guid_str)
        g = _GUID.from_buffer_copy(u.bytes_le)
        result = ctypes.windll.powrprof.PowerSetActiveScheme(None, ctypes.byref(g))
        if result == 0:
            return True
    except Exception:
        pass
    # Fallback: powercfg (no console window)
    try:
        proc = subprocess.run(
            ["powercfg", "/setactive", guid_str],
            capture_output=True,
            creationflags=_subprocess_flags(),
            timeout=10,
        )
        return proc.returncode == 0
    except Exception:
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
        self._target_guid: Optional[str] = None
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
        if self._target_guid:
            return active.guid == self._target_guid
        return is_acceptable_plan(active.name)
