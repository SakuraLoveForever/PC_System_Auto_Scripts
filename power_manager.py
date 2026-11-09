"""Power plan detection and management for Windows — registry + Win32 API, with subprocess fallback.

Design rules (see AGENT_HANDOFF_RECOMMENDATIONS.md):

* Reading is pure. ``get_all_plans()`` / ``get_power_snapshot()`` never create or
  activate a scheme; creating a missing built-in scheme is an explicit,
  reportable operation (``create_missing_builtin_schemes``).
* A plan's identity is its GUID. Names are display data only, so renaming a
  built-in plan (or having two plans with the same name) can never make the
  monitor fight the user.
* ``PowerMonitor`` owns exactly one worker thread, can interrupt its sleep, and
  serializes manual checks against scheduled checks.
"""

from __future__ import annotations

import ctypes
import logging
import re
import subprocess
import threading
import time
import uuid
import winreg
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Sequence

log = logging.getLogger(__name__)


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

_POWERCFG_TIMEOUT = 10


def _is_hidden_plan(name: str) -> bool:
    name_lower = name.lower()
    return any(pattern in name_lower for pattern in _HIDDEN_PLAN_PATTERNS)


# Power plan names that are acceptable (case-insensitive matching).
# Only used for plans that are picked up by name; the built-in GUIDs below are
# the authoritative identity.
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
    priority: int = 100


# Windows 11 / Modern Standby machines often expose only Balanced until the
# classic schemes are duplicated from their built-in templates. Lower priority
# value wins when auto-selecting a target.
_BUILTIN_POWER_SCHEMES: Sequence[_BuiltinPowerScheme] = (
    _BuiltinPowerScheme(
        source_guid="e9a42b02-d5df-448d-aa00-03f14749eb61",
        default_name="卓越性能",
        match_names=("ultimate performance", "卓越性能"),
        is_acceptable=True,
        priority=0,
    ),
    _BuiltinPowerScheme(
        source_guid="8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c",
        default_name="高性能",
        match_names=("high performance", "高性能"),
        is_acceptable=True,
        priority=1,
    ),
)

_BUILTIN_HIGH_PERFORMANCE_GUIDS = {
    scheme.source_guid for scheme in _BUILTIN_POWER_SCHEMES if scheme.is_acceptable
}
_BUILTIN_GUID_PRIORITY = {
    scheme.source_guid: scheme.priority for scheme in _BUILTIN_POWER_SCHEMES
}
# Localized names that mean the same built-in scheme, so "高性能" and
# "High Performance" never count as two different plans.
_BUILTIN_NAME_KEYS = {
    scheme.source_guid: frozenset(name.strip().lower() for name in scheme.match_names)
    for scheme in _BUILTIN_POWER_SCHEMES
}


def _builtin_name_key(plan: PowerPlan) -> Optional[frozenset]:
    """The set of names that this built-in template may legitimately appear as."""
    return _BUILTIN_NAME_KEYS.get(plan.guid.lower())


def is_plan_usable(plan: PowerPlan, plans: Sequence[PowerPlan]) -> bool:
    """False for built-in *template* schemes that Windows refuses to activate.

    Windows keeps a template GUID in ``powercfg /list`` even when the scheme can
    no longer be activated because a real duplicate of it exists; switching to it
    fails with ERROR_NOT_SUPPORTED. Such a plan must never be offered as a target,
    or every attempt to switch to it reports "failed".

    A template is only rejected when another scheme of the same family is
    available, so renaming the only copy of your performance plan keeps working.
    """
    aliases = _builtin_name_key(plan)
    if aliases is None:
        return True                     # user / driver scheme
    if plan.name.strip().lower() in aliases:
        return True                     # still carrying its own name
    if plan.is_active:
        return True                     # it is running, so it is activatable
    for other in plans:
        if other.guid == plan.guid:
            continue
        if _scheme_name_key(other.name) == _scheme_name_key(plan.name):
            return False                # the template is shadowed by a real scheme
    return True


def find_usable_plans(plans: Sequence[PowerPlan]) -> List[PowerPlan]:
    """Plans that can actually be activated on this machine."""
    return [plan for plan in plans if is_plan_usable(plan, plans)]


# ---------------------------------------------------------------------------
# Schemes Windows refuses to activate
#
# A scheme reported as "not supported" (error 50) is remembered here — usually a
# built-in performance template that a real duplicate has shadowed. Remembering it
# is what stops the app from picking the same dead target on every check. Nothing
# is probed actively: a scheme only lands here after Windows actually refused it.
# ---------------------------------------------------------------------------

_UNSUPPORTED_TARGETS: set = set()


def unsupported_targets() -> List[str]:
    """Guids known to be unactivatable, for persistence in the config."""
    return sorted(_UNSUPPORTED_TARGETS)


def set_unsupported_targets(values) -> None:
    global _UNSUPPORTED_TARGETS
    if not isinstance(values, (list, tuple, set)):
        return
    _UNSUPPORTED_TARGETS = {str(value).lower() for value in values if value}


def is_target_blocked(guid: Optional[str]) -> bool:
    return bool(guid) and guid.lower() in _UNSUPPORTED_TARGETS


def mark_target_unsupported(guid: Optional[str]) -> bool:
    """Remember a scheme Windows refused. Returns True when it is newly blocked."""
    if not guid:
        return False
    key = guid.lower()
    if key in _UNSUPPORTED_TARGETS:
        return False
    _UNSUPPORTED_TARGETS.add(key)
    log.info("scheme %s cannot be activated; it will not be used as a target", key)
    return True


def plan_is_activatable(plan: Optional[PowerPlan], plans: Sequence[PowerPlan]) -> bool:
    """Usability = not structurally shadowed AND not known to be refused."""
    if plan is None:
        return False
    if is_target_blocked(plan.guid):
        return False
    return is_plan_usable(plan, plans)

_GUID_RE = re.compile(
    r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"
)


@dataclass
class PowerPlan:
    guid: str
    name: str
    is_active: bool = False
    is_acceptable: bool = False
    # False for a built-in template that Windows refuses to activate (shadowed by
    # a real duplicate). Set when the snapshot is merged.
    is_usable: bool = True


@dataclass
class PlanSnapshot:
    """Result of one read-only power plan query."""

    plans: List[PowerPlan] = field(default_factory=list)
    error: str = ""          # "", "timeout", "permission", "unavailable", "error"
    error_detail: str = ""

    @property
    def ok(self) -> bool:
        return not self.error

    @property
    def active(self) -> Optional[PowerPlan]:
        for plan in self.plans:
            if plan.is_active:
                return plan
        return None


@dataclass
class SchemeCreateResult:
    """Outcome of one explicit ``powercfg /duplicatescheme`` call."""

    name: str
    ok: bool
    guid: str = ""
    output: str = ""
    error: str = ""


def _is_acceptable_name(name: str) -> bool:
    name_lower = name.lower()
    return any(target in name_lower for target in HIGH_PERFORMANCE_PLANS)


def _is_acceptable_guid(guid: str) -> bool:
    return guid.lower() in _BUILTIN_HIGH_PERFORMANCE_GUIDS


def _is_acceptable_plan(guid: str, name: str) -> bool:
    return _is_acceptable_guid(guid) or _is_acceptable_name(name)


def _plan_priority(plan: PowerPlan) -> int:
    """Fixed auto-selection priority: Ultimate Performance, High Performance, then name matches."""
    guid_priority = _BUILTIN_GUID_PRIORITY.get(plan.guid.lower())
    if guid_priority is not None:
        return guid_priority
    name_lower = plan.name.lower()
    for scheme in _BUILTIN_POWER_SCHEMES:
        if any(match in name_lower for match in scheme.match_names):
            return scheme.priority
    return 100


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


def _decode_powercfg_bytes(data: bytes) -> str:
    """Decode powercfg output once. mbcs is the native Windows console codepage."""
    for encoding in ("mbcs", "utf-8", "gbk"):
        try:
            text = data.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
        if text:
            return text
    return data.decode("utf-8", errors="replace")


class PowercfgError(Exception):
    """A powercfg invocation failed; carries the classified reason."""

    def __init__(self, kind: str, detail: str = ""):
        super().__init__(detail or kind)
        self.kind = kind
        self.detail = detail


def _run_powercfg_raw(args: Sequence[str], timeout: int = _POWERCFG_TIMEOUT) -> tuple[int, str]:
    """Run powercfg exactly once and decode the captured bytes.

    Raises PowercfgError("timeout" | "unavailable" | "error").
    """
    try:
        proc = subprocess.run(
            ["powercfg", *args],
            capture_output=True,
            creationflags=_subprocess_flags(),
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise PowercfgError("timeout", f"powercfg {' '.join(args)} timed out after {timeout}s") from exc
    except FileNotFoundError as exc:
        raise PowercfgError("unavailable", str(exc)) from exc
    except OSError as exc:
        raise PowercfgError("unavailable", str(exc)) from exc
    except subprocess.SubprocessError as exc:
        raise PowercfgError("error", str(exc)) from exc

    output = _decode_powercfg_bytes((proc.stdout or b"") + (proc.stderr or b""))
    return proc.returncode, output


def _classify_failure(returncode: int, output: str) -> str:
    """Map a failed powercfg exit (or message) to a coarse reason for the UI."""
    lowered = output.lower()
    if returncode == 5 or "access is denied" in lowered or "拒绝访问" in output or "权限" in output:
        return "permission"
    # powercfg reports unsupported schemes with localized text and exit code 0.
    if "not supported" in lowered or "不受支持" in output or "不支持" in output:
        return "not_supported"
    if "not found" in lowered or "找不到" in output or "不存在" in output:
        return "not_found"
    return "error"


def _plan_name_from_powercfg_line(line: str, guid: str) -> str:
    # powercfg /list prints localized labels, but the visible plan name is
    # consistently wrapped in parentheses before the optional active marker.
    match = re.search(r"\((.*?)\)\s*\*?\s*$", line)
    if match and match.group(1).strip():
        return match.group(1).strip()

    rest = line.split(guid, 1)[-1].strip()
    rest = rest.strip("*").strip()
    return rest or guid


def _parse_plans_from_powercfg_list(output: str) -> List[PowerPlan]:
    """Parse `powercfg /list` output. Pure function, no subprocess."""
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

    return plans


def _get_plans_from_powercfg() -> List[PowerPlan]:
    try:
        returncode, output = _run_powercfg_raw(["/list"])
    except PowercfgError as exc:
        log.warning("powercfg /list failed: %s", exc.detail or exc.kind)
        return []
    if returncode != 0:
        return []
    return _parse_plans_from_powercfg_list(output)


def _get_plans_from_registry() -> List[PowerPlan]:
    """Read power plans from registry as a fallback when powercfg is unavailable."""
    plans: List[PowerPlan] = []
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
    except OSError as exc:
        log.warning("power scheme registry read failed: %s", exc)

    return plans


def _active_power_scheme_guid() -> Optional[str]:
    """Fallback active-scheme probe, only used when /list showed no active marker."""
    try:
        returncode, output = _run_powercfg_raw(["/getactivescheme"])
    except PowercfgError:
        returncode, output = 1, ""
    if returncode == 0 and output:
        match = _GUID_RE.search(output)
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

    if not any(plan.is_active for plan in merged.values()):
        active_guid = _active_power_scheme_guid()
        if active_guid:
            for plan in merged.values():
                plan.is_active = plan.guid == active_guid

    plans = list(merged.values())
    for plan in plans:
        plan.is_usable = is_plan_usable(plan, plans)
    return plans


def _matches_builtin_scheme(plan: PowerPlan, scheme: _BuiltinPowerScheme) -> bool:
    if plan.guid == scheme.source_guid:
        return True
    name_lower = plan.name.lower()
    return any(match_name in name_lower for match_name in scheme.match_names)


# ---------------------------------------------------------------------------
# Read-only API
# ---------------------------------------------------------------------------

def get_all_plans() -> List[PowerPlan]:
    """Read the current Windows power plans. Never modifies system state."""
    return get_power_snapshot().plans


def get_power_snapshot() -> PlanSnapshot:
    """Read all power plans once and report failures distinctly.

    Never calls ``/duplicatescheme`` or ``/setactive``.
    """
    error = ""
    detail = ""
    powercfg_plans: List[PowerPlan] = []
    try:
        returncode, output = _run_powercfg_raw(["/list"])
        if returncode != 0:
            error = _classify_failure(returncode, output)
            detail = output.strip()
        else:
            powercfg_plans = _parse_plans_from_powercfg_list(output)
    except PowercfgError as exc:
        error = exc.kind
        detail = exc.detail

    if not powercfg_plans and not error:
        error = "unavailable"
        detail = detail or "powercfg /list returned no plan data"

    registry_plans = _get_plans_from_registry()
    plans = _merge_plans(powercfg_plans, registry_plans)

    if not plans and not error:
        error = "unavailable"
        detail = detail or "no power schemes found"
    elif plans and error:
        # A partial read is still useful to the user; keep the plans and log why
        # the read was incomplete instead of showing a misleading empty list.
        log.info("partial power plan read (%s): %d plans available", error, len(plans))
        error = ""

    return PlanSnapshot(plans=plans, error=error, error_detail=detail)


def get_active_plan() -> Optional[PowerPlan]:
    return get_power_snapshot().active


def get_acceptable_plans() -> List[PowerPlan]:
    return [p for p in get_all_plans() if p.is_acceptable]


def find_high_performance_plan() -> Optional[PowerPlan]:
    """Deterministic auto target: Ultimate Performance, then High Performance."""
    return find_auto_target(get_all_plans())


def _scheme_name_key(name: str) -> str:
    """Group the localized and English spellings of one built-in scheme together."""
    key = (name or "").strip().lower()
    for aliases in _BUILTIN_NAME_KEYS.values():
        if key in aliases:
            return "builtin:" + sorted(aliases)[0]
    return key


def is_shadow_template(plan: PowerPlan) -> bool:
    """A built-in scheme whose name no longer matches its template.

    Namely a renamed built-in, or a built-in template that a real duplicate /
    another localization has shadowed. Such a scheme usually cannot be activated.
    """
    aliases = _builtin_name_key(plan)
    return aliases is not None and plan.name.strip().lower() not in aliases


def order_family(group: Sequence[PowerPlan]) -> List[PowerPlan]:
    """One built-in family, best candidate first.

    The running scheme wins; real schemes come before shadow templates, which are
    only used when nothing else of that family exists (a renamed built-in).
    """
    active = [plan for plan in group if plan.is_active]
    real = [plan for plan in group if not is_shadow_template(plan)]
    templates = [plan for plan in group if is_shadow_template(plan)]
    ordered: List[PowerPlan] = []
    for plan in active + real + templates:
        if plan not in ordered:
            ordered.append(plan)
    return ordered


def find_auto_target(plans: Sequence[PowerPlan]) -> Optional[PowerPlan]:
    """The plan the Auto policy should maintain.

    Candidates are grouped by resolved scheme name ("高性能" and "High Performance"
    are the same scheme) and each group is ordered so a plan Windows can actually
    activate is preferred.
    """
    candidates = [plan for plan in plans
                  if plan.is_acceptable or _is_acceptable_plan(plan.guid, plan.name)]
    blocked = [plan for plan in candidates if not is_target_blocked(plan.guid)]
    candidates = blocked or candidates
    if not candidates:
        return None

    groups: dict = {}
    for plan in candidates:
        groups.setdefault(_scheme_name_key(plan.name), []).append(plan)

    chosen = [order_family(group)[0] for group in groups.values()]

    # A running plan outranks a nominally higher tier: adopting it is what keeps
    # the monitor from switching a machine that is already fine.
    return sorted(chosen, key=lambda plan: (not plan.is_active,
                                            _plan_priority(plan),
                                            plan.name.lower()))[0]


def find_plan_by_guid(guid: str, plans: Optional[Sequence[PowerPlan]] = None) -> Optional[PowerPlan]:
    if not guid:
        return None
    wanted = guid.lower()
    for plan in (plans if plans is not None else get_all_plans()):
        if plan.guid.lower() == wanted:
            return plan
    return None


def is_acceptable_plan(plan_name: str) -> bool:
    """Name-based check, kept for callers that only have a display name."""
    return _is_acceptable_name(plan_name)


def is_plan_matching_target(plan: Optional[PowerPlan],
                            target_guid: Optional[str],
                            plans: Optional[Sequence[PowerPlan]] = None) -> bool:
    """Single source of truth for the monitor, the UI and the tray.

    With an explicit target the plan's GUID must match. Under the Auto policy the
    plan must be the one Auto maintains, so a plan that Windows cannot activate
    is reported as needing a fix and Check Now actually switches.
    """
    if plan is None:
        return False
    if target_guid:
        return plan.guid.lower() == target_guid.lower()
    if not (plan.is_acceptable or _is_acceptable_plan(plan.guid, plan.name)):
        return False
    if plans is None:
        return True
    best = find_auto_target(plans)
    if best is None:
        return True
    return plan.guid.lower() == best.guid.lower()


@dataclass
class SwitchResult:
    """Outcome of an attempt to activate a power scheme."""

    ok: bool
    method: str = ""            # "api", "powercfg", or "already_active"
    error: str = ""
    detail: str = ""

    @property
    def unsupported(self) -> bool:
        return self.error == "not_supported"


def active_plan_guid() -> Optional[str]:
    """Guid of the active plan, read without mutating anything."""
    snapshot = get_power_snapshot()
    active = snapshot.active
    return active.guid if active else None


def _attempt_api_switch(guid_str: str) -> SwitchResult:
    try:
        u = uuid.UUID(guid_str)
        g = _GUID.from_buffer_copy(u.bytes_le)
        if hasattr(ctypes, "set_last_error"):
            ctypes.set_last_error(0)
        rc = ctypes.windll.powrprof.PowerSetActiveScheme(None, ctypes.byref(g))
    except (ValueError, AttributeError, OSError) as exc:
        return SwitchResult(False, "api", "error", str(exc))

    if rc == 0:
        return SwitchResult(True, "api")
    err = ctypes.get_last_error() if hasattr(ctypes, "get_last_error") else 0
    if rc == 50 or err == 50:            # ERROR_NOT_SUPPORTED
        return SwitchResult(False, "api", "not_supported", f"PowerSetActiveScheme rc=50")
    if rc == 5 or err == 5:              # ERROR_ACCESS_DENIED
        return SwitchResult(False, "api", "permission", f"PowerSetActiveScheme rc=5")
    return SwitchResult(False, "api", "error", f"PowerSetActiveScheme rc={rc}")


def set_active_plan(guid_str: str) -> bool:
    """Activate a power plan and verify the requested GUID is active."""
    return switch_active_plan(guid_str).ok


def switch_active_plan(guid_str: str, *,
                       can_activate: Optional[Callable[[], bool]] = None) -> SwitchResult:
    """Activate a power plan and report exactly what happened.

    The Win32 API is tried first and ``powercfg`` second. Success is VERIFIED by
    re-reading the active GUID. The monitor may invalidate a pending activation
    while a read or the previous activation attempt is still in progress.
    """
    before = active_plan_guid()
    wanted = (guid_str or "").lower()
    if can_activate is not None and not can_activate():
        return SwitchResult(False, error="cancelled")
    if before and wanted and before.lower() == wanted:
        return SwitchResult(True, "already_active")

    api_result = _attempt_api_switch(guid_str)
    if api_result.ok and _active_guid_matches(wanted):
        return api_result

    if can_activate is not None and not can_activate():
        return SwitchResult(False, error="cancelled")
    cmd_result = SwitchResult(False, "powercfg")
    try:
        returncode, output = _run_powercfg_raw(["/setactive", guid_str])
        if returncode == 0:
            cmd_result = SwitchResult(True, "powercfg")
        else:
            cmd_result = SwitchResult(False, "powercfg",
                                      _classify_failure(returncode, output), output.strip())
    except PowercfgError as exc:
        cmd_result = SwitchResult(False, "powercfg", exc.kind, exc.detail)

    if cmd_result.ok and _active_guid_matches(wanted):
        return cmd_result

    # The requested plan is unverified: report the most informative failure.
    if cmd_result.error:
        return cmd_result
    if api_result.error:
        return api_result
    return SwitchResult(False, cmd_result.method or api_result.method, "error",
                        "the requested scheme did not become active")


def _active_guid_matches(wanted: str) -> bool:
    now = active_plan_guid()
    return bool(now and now.lower() == wanted)


# ---------------------------------------------------------------------------
# Explicit, user-triggered system modification
# ---------------------------------------------------------------------------

def create_missing_builtin_schemes(plans: Optional[Sequence[PowerPlan]] = None) -> List[SchemeCreateResult]:
    """Duplicate the built-in performance templates that are missing.

    This is an explicit operation: it creates power schemes on the system. It
    reports per-scheme success/failure so the caller can show the outcome and
    let the user retry.
    """
    current = list(plans if plans is not None else get_all_plans())
    results: List[SchemeCreateResult] = []

    for scheme in _BUILTIN_POWER_SCHEMES:
        if any(_matches_builtin_scheme(plan, scheme) for plan in current):
            results.append(SchemeCreateResult(name=scheme.default_name, ok=True,
                                              error="already_present"))
            continue

        try:
            returncode, output = _run_powercfg_raw(["/duplicatescheme", scheme.source_guid])
        except PowercfgError as exc:
            results.append(SchemeCreateResult(name=scheme.default_name, ok=False,
                                              error=exc.kind, output=exc.detail))
            continue

        if returncode != 0:
            results.append(SchemeCreateResult(
                name=scheme.default_name, ok=False,
                error=_classify_failure(returncode, output), output=output.strip()))
            continue

        match = _GUID_RE.search(output)
        new_guid = (match.group(1) if match else "").lower()
        if not new_guid:
            results.append(SchemeCreateResult(
                name=scheme.default_name, ok=False, error="no_guid", output=output.strip()))
            continue

        name = _plan_name_from_powercfg_line(output, new_guid) if output else scheme.default_name
        if name == new_guid:
            name = scheme.default_name
        created = PowerPlan(guid=new_guid, name=name, is_acceptable=True)
        current.append(created)
        results.append(SchemeCreateResult(name=name, ok=True, guid=new_guid, output=output.strip()))

    return results


# ---------------------------------------------------------------------------
# Monitor
# ---------------------------------------------------------------------------

class PowerMonitor:
    """Monitors power plan on a background thread and auto-corrects if needed.

    Guarantees:
    * restarting reuses the worker if it is still finishing a check;
    * ``stop()`` interrupts the wait without joining the worker on the GUI thread;
    * scheduled checks and user-triggered checks/switches share one operation lock;
    * results and pending writes from a superseded check are discarded.
    """

    def __init__(self, on_status_change: Optional[Callable] = None,
                 interval_seconds: int = 60,
                 on_monitor_state_change: Optional[Callable] = None):
        self._interval = 60
        self.interval = interval_seconds
        self._target_guid: Optional[str] = None
        self._lock = threading.RLock()
        self._wake = threading.Condition(self._lock)
        self._check_lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._enabled = False
        self._revision = 0
        self._on_status_change = on_status_change
        self._on_monitor_state_change = on_monitor_state_change
        self._last_status: Optional[str] = None
        self._reported_blocked: set = set()

    # -- properties ---------------------------------------------------------

    @property
    def interval(self) -> int:
        return self._interval

    @interval.setter
    def interval(self, seconds) -> None:
        try:
            value = int(seconds)
        except (TypeError, ValueError):
            raise ValueError(f"invalid interval: {seconds!r}")
        if value < 10 or value > 3600:
            raise ValueError(f"interval out of range (10-3600): {value}")
        self._interval = value

    @property
    def target_guid(self) -> Optional[str]:
        with self._lock:
            return self._target_guid

    @target_guid.setter
    def target_guid(self, guid: Optional[str]) -> None:
        with self._lock:
            target = guid or None
            if self._target_guid != target:
                self._target_guid = target
                self._revision += 1
                self._last_status = None

    @property
    def revision(self) -> int:
        """Changes when the monitoring session or its target is superseded."""
        with self._lock:
            return self._revision

    @property
    def running(self) -> bool:
        """True while scheduled monitoring is enabled."""
        with self._lock:
            return self._enabled

    @property
    def worker_thread(self) -> Optional[threading.Thread]:
        with self._lock:
            return self._thread

    # -- lifecycle ----------------------------------------------------------

    def start(self) -> None:
        with self._wake:
            if self._enabled:
                return
            self._enabled = True
            self._revision += 1
            self._last_status = None
            if self._thread is None:
                self._thread = threading.Thread(
                    target=self._worker_entry, name="PowerMonitor", daemon=True)
                try:
                    self._thread.start()
                except RuntimeError:
                    self._enabled = False
                    self._thread = None
                    raise
            self._wake.notify()
            self._emit_state(True)

    def stop(self) -> None:
        """Stop monitoring and invalidate pending power writes."""
        with self._wake:
            self._revision += 1
            if self._enabled:
                self._enabled = False
                self._wake.notify()
                self._emit_state(False)

    def wait_until_stopped(self, timeout: float = 5.0) -> bool:
        """Block until no worker is alive. Used on exit and by tests."""
        with self._lock:
            thread = self._thread
        if thread is None:
            return True
        if thread is threading.current_thread():
            return False
        thread.join(timeout=timeout)
        return not thread.is_alive()

    def _worker_entry(self) -> None:
        try:
            while True:
                with self._wake:
                    if not self._enabled:
                        # Release ownership under the same lock start() uses.
                        self._thread = None
                        return
                    revision = self._revision
                with self._check_lock:
                    try:
                        self._check_and_fix(revision)
                    except Exception as exc:
                        log.exception("power monitor check failed")
                        with self._lock:
                            if revision == self._revision:
                                self._emit({
                                    "status": "error",
                                    "plan_name": "",
                                    "fixed": False,
                                    "message": str(exc),
                                })
                with self._wake:
                    self._wake.wait_for(lambda: revision != self._revision,
                                        timeout=self._interval)
        finally:
            with self._lock:
                if self._thread is threading.current_thread():
                    self._thread = None
                    self._enabled = False
                    self._revision += 1
                    self._emit_state(False)

    def _emit(self, result: dict) -> None:
        if self._on_status_change is None:
            return
        try:
            self._on_status_change(result)
        except Exception:
            log.exception("power monitor status callback failed")

    def _emit_state(self, running: bool) -> None:
        if self._on_monitor_state_change is None:
            return
        try:
            self._on_monitor_state_change(running)
        except Exception:
            log.exception("power monitor state callback failed")

    # -- checks -------------------------------------------------------------

    def check_now(self) -> dict:
        """Run one check. Returns a 'busy' result if a check is already running."""
        if not self._check_lock.acquire(blocking=False):
            return {"status": "busy", "plan_name": "", "fixed": False}
        try:
            result = self._check_and_fix(self.revision)
            if result is None:
                return {"status": "cancelled", "plan_name": "", "fixed": False}
            return result
        finally:
            self._check_lock.release()

    def switch_now(self, guid: str) -> SwitchResult:
        """Activate a user-selected scheme, or report that a check is busy."""
        if not self._check_lock.acquire(blocking=False):
            return SwitchResult(False, error="busy")
        try:
            revision = self.revision
            return switch_active_plan(guid, can_activate=lambda: revision == self.revision)
        finally:
            self._check_lock.release()

    def _resolve_targets(self, plans: Sequence[PowerPlan],
                         target_guid: Optional[str]) -> List[PowerPlan]:
        """Plans to try, best first.

        An explicit target is used as long as it exists and is not known to be
        unactivatable; otherwise the best usable plan is substituted so a single
        dead target can never make every switch fail.
        """
        if target_guid:
            plan = find_plan_by_guid(target_guid, plans)
            if plan is None:
                # The configured target no longer exists: report it so the user
                # can pick another one.
                return []
            # The configured target is tried first; the usable plans of its own
            # family, then every other family, follow. That way one dead target
            # can neither fail the switch nor make it land on a worse tier.
            ordered = [plan]
            fallbacks = self._auto_candidates(plans, prefer=plan)
            return ordered + [candidate for candidate in fallbacks
                              if candidate.guid != plan.guid]
        return self._auto_candidates(plans)

    def _auto_candidates(self, plans: Sequence[PowerPlan],
                         prefer: Optional[PowerPlan] = None) -> List[PowerPlan]:
        """Usable plans in the order they should be tried.

        The family of ``prefer`` (the configured target) comes first so a fallback
        stays as close as possible to what the user asked for.
        """
        candidates = [plan for plan in plans
                      if plan.is_acceptable or _is_acceptable_plan(plan.guid, plan.name)]
        groups: dict = {}
        for plan in candidates:
            groups.setdefault(_scheme_name_key(plan.name), []).append(plan)

        families = [order_family(group) for group in groups.values()]
        families.sort(key=lambda group: (_plan_priority(group[0]), group[0].name.lower()))

        if prefer is not None:
            wanted = _scheme_name_key(prefer.name)
            preferred = [group for group in families
                         if _scheme_name_key(group[0].name) == wanted]
            families = preferred + [group for group in families
                                    if _scheme_name_key(group[0].name) != wanted]

        ordered: List[PowerPlan] = []
        for group in families:
            for plan in group:
                if plan_is_activatable(plan, plans):
                    ordered.append(plan)
        return ordered

    def _check_and_fix(self, revision: int) -> Optional[dict]:
        with self._lock:
            if revision != self._revision:
                return None
            target_guid = self._target_guid

        snapshot = get_power_snapshot()
        if revision != self.revision:
            return None
        plans = snapshot.plans
        active = snapshot.active

        if active is None:
            result = {
                "status": "error",
                "plan_name": "",
                "fixed": False,
                "error": snapshot.error or "no_active_plan",
                "error_detail": snapshot.error_detail,
            }
        elif is_plan_matching_target(active, target_guid, plans):
            result = {"status": "ok", "plan_name": active.name, "fixed": False}
        else:
            targets = self._resolve_targets(plans, target_guid)
            if not targets:
                # Distinguish "configured target vanished" from "no candidate".
                status = "target_missing" if target_guid else "no_target"
                result = {
                    "status": status,
                    "plan_name": active.name,
                    "fixed": False,
                    "target_guid": target_guid or "",
                }
            else:
                result = self._try_targets(active, targets, target_guid, revision)
                if result is None:
                    return None

        status = result["status"]
        new_status = (f"{status}:{result.get('plan_name', '')}:{result.get('target_guid', '')}"
                      f":{'|'.join(result.get('skipped', []))}")
        with self._lock:
            if revision != self._revision:
                return None
            if new_status != self._last_status:
                self._last_status = new_status
                self._emit(result)

        return result

    def _try_targets(self, active: PowerPlan, targets: Sequence[PowerPlan],
                     target_guid: Optional[str], revision: int) -> Optional[dict]:
        """Switch to the best usable target, remembering plans Windows rejects.

        A configured-but-dead target is reported *and* skipped, so one click both
        explains why the choice cannot be used and still fixes the power plan.
        """
        rejected: List[str] = []
        dead_configured: Optional[PowerPlan] = None
        switched_to: Optional[PowerPlan] = None
        last_error = ""

        def can_activate() -> bool:
            return revision == self.revision

        for target in targets:
            if not can_activate():
                return None
            outcome = switch_active_plan(target.guid, can_activate=can_activate)
            with self._lock:
                if revision != self._revision or outcome.error == "cancelled":
                    return None
                if outcome.ok:
                    switched_to = target
                    break
                last_error = outcome.error
                if not (outcome.unsupported or outcome.error == "permission"):
                    break
                # This plan cannot be activated: remember it and try the next one.
                mark_target_unsupported(target.guid)
                rejected.append(f"{target.name} ({outcome.error})")
                if target_guid and target_guid.lower() == target.guid.lower():
                    dead_configured = target

        if dead_configured is not None:
            blocked = {
                "status": "target_blocked",
                "plan_name": active.name,
                "target_name": dead_configured.name,
                "target_guid": dead_configured.guid,
                "fixed": switched_to is not None,
                "error": last_error,
                "next_target_name": switched_to.name if switched_to else "",
                "skipped": list(rejected),
            }
            with self._lock:
                if revision != self._revision:
                    return None
                guid_key = dead_configured.guid.lower()
                if guid_key not in self._reported_blocked:
                    self._reported_blocked.add(guid_key)
                    if switched_to is not None:
                        self._emit(blocked)
            if switched_to is None:
                return blocked
        if switched_to is not None:
            return {
                "status": "fixed",
                "plan_name": active.name,
                "target_name": switched_to.name,
                "target_guid": switched_to.guid,
                "fixed": True,
                "skipped": list(rejected),
            }
        return {
            "status": "fix_failed",
            "plan_name": active.name,
            "target_name": targets[0].name if targets else "",
            "target_guid": targets[0].guid if targets else "",
            "fixed": False,
            "error": last_error,
            "skipped": list(rejected),
        }

    def _is_plan_ok(self, active: PowerPlan) -> bool:
        return is_plan_matching_target(active, self.target_guid, [active])
