"""Regression tests for power_manager: read-only reads, GUID identity, one worker.

Run:  python -m unittest discover -s tests -v
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import power_manager as pm

HIGH_PERF_GUID = "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c"
ULTIMATE_GUID = "e9a42b02-d5df-448d-aa00-03f14749eb61"
BALANCED_GUID = "381b4222-f694-41f0-9685-ff5bb260df2e"

LIST_OUTPUT = (
    "Existing Power Schemes (* Active)\n"
    "-----------------------------------\n"
    f"Power Scheme GUID: {BALANCED_GUID}  (平衡)\n"
    f"Power Scheme GUID: {HIGH_PERF_GUID}  (高性能) *\n"
)


def _run_result(args, stdout="", stderr="", code=0):
    return subprocess.CompletedProcess(args=args, returncode=code,
                                       stdout=stdout.encode("utf-8"),
                                       stderr=stderr.encode("utf-8"))


class ReadOnlyTestCase(unittest.TestCase):
    """get_all_plans()/get_power_snapshot() must never modify system state."""

    def setUp(self):
        self.calls = []

        def fake_run(argv, capture_output=True, creationflags=0, timeout=None):
            self.calls.append(list(argv))
            if argv[0] != "powercfg":
                return _run_result(argv, code=0)
            if argv[1] == "/list":
                return _run_result(argv, stdout=LIST_OUTPUT)
            if argv[1] == "/getactivescheme":
                return _run_result(argv, stdout=f"Power Scheme GUID: {HIGH_PERF_GUID}  (高性能)\n")
            raise AssertionError(f"unexpected powercfg call in a read path: {argv}")

        self._patcher = mock.patch.object(pm.subprocess, "run", side_effect=fake_run)
        self._patcher.start()
        self.addCleanup(self._patcher.stop)
        self._reg_patcher = mock.patch.object(pm, "_get_plans_from_registry", return_value=[])
        self._reg_patcher.start()
        self.addCleanup(self._reg_patcher.stop)

    def _mutations(self):
        return [call for call in self.calls
                if len(call) > 1 and call[1] in ("/duplicatescheme", "/setactive")]

    def test_read_does_not_duplicate_or_setactive(self):
        plans = pm.get_all_plans()
        self.assertTrue(plans)
        self.assertEqual(self._mutations(), [])
        self.assertEqual(len(self.calls), 1, "a plain read should run powercfg once")

    def test_snapshot_reports_active_plan(self):
        snapshot = pm.get_power_snapshot()
        self.assertTrue(snapshot.ok)
        self.assertIsNotNone(snapshot.active)
        self.assertEqual(snapshot.active.guid, HIGH_PERF_GUID)

    def test_repeated_reads_stay_pure(self):
        for _ in range(3):
            pm.get_all_plans()
        self.assertEqual(self._mutations(), [])

    def test_find_and_acceptability_helpers_are_pure(self):
        pm.find_high_performance_plan()
        pm.get_acceptable_plans()
        pm.get_active_plan()
        pm.find_plan_by_guid(HIGH_PERF_GUID)
        self.assertEqual(self._mutations(), [])

    # --- P1.6: GUID is the identity ---------------------------------------

    def test_renamed_builtin_plan_stays_acceptable(self):
        renamed = LIST_OUTPUT.replace("(高性能)", "(我的自定义名字)")
        with mock.patch.object(pm.subprocess, "run",
                               side_effect=lambda argv, **kw: _run_result(argv, stdout=renamed)):
            plan = pm.find_plan_by_guid(HIGH_PERF_GUID)
        self.assertIsNotNone(plan)
        self.assertTrue(plan.is_acceptable,
                        "a renamed built-in performance plan must still count as acceptable")

    def test_auto_target_prefers_ultimate_when_nothing_is_running(self):
        """With no active plan at all, the higher tier wins."""
        both = (LIST_OUTPUT.replace(" *", "")
                + f"Power Scheme GUID: {ULTIMATE_GUID}  (Ultimate Performance)\n")
        with mock.patch.object(pm.subprocess, "run",
                               side_effect=lambda argv, **kw: _run_result(argv, stdout=both)):
            plan = pm.find_high_performance_plan()
        self.assertEqual(plan.guid, ULTIMATE_GUID)

    def test_auto_target_skips_a_blocked_template(self):
        """A template Windows refused is never chosen again."""
        pm.set_unsupported_targets([ULTIMATE_GUID])
        try:
            plans = [
                pm.PowerPlan(guid=BALANCED_GUID, name="平衡", is_active=True),
                pm.PowerPlan(guid=ULTIMATE_GUID, name="Ultimate Performance",
                             is_acceptable=True),
                pm.PowerPlan(guid=HIGH_PERF_GUID, name="高性能", is_acceptable=True),
            ]
            self.assertEqual(pm.find_auto_target(plans).guid, HIGH_PERF_GUID)
        finally:
            pm.set_unsupported_targets([])

    def test_auto_target_adopts_the_running_performance_plan(self):
        """A running acceptable plan is kept — the monitor must not fight it."""
        plans = [
            pm.PowerPlan(guid=BALANCED_GUID, name="平衡"),
            pm.PowerPlan(guid=HIGH_PERF_GUID, name="高性能", is_active=True, is_acceptable=True),
            pm.PowerPlan(guid=ULTIMATE_GUID, name="Ultimate Performance", is_acceptable=True),
        ]
        target = pm.find_auto_target(plans)
        self.assertEqual(target.guid, HIGH_PERF_GUID)
        self.assertTrue(pm.is_plan_matching_target(plans[1], None, plans))

    def test_auto_target_falls_back_after_a_template_is_blocked(self):
        """Once Windows refuses a template, the real scheme of that family wins."""
        real_guid = "bfc4ac38-0621-4fcb-8781-c8ae6504897b"
        plans = [
            pm.PowerPlan(guid=BALANCED_GUID, name="平衡", is_active=True),
            pm.PowerPlan(guid=real_guid, name="高性能", is_acceptable=True),
            pm.PowerPlan(guid=HIGH_PERF_GUID, name="高性能", is_acceptable=True),
            pm.PowerPlan(guid=ULTIMATE_GUID, name="Ultimate Performance", is_acceptable=True),
        ]
        # Until it is known to be dead, the higher tier is preferred.
        self.assertEqual(pm.find_auto_target(plans).guid, ULTIMATE_GUID)

        pm.set_unsupported_targets([ULTIMATE_GUID])
        try:
            chosen = pm.find_auto_target(plans)
            self.assertNotEqual(chosen.guid, ULTIMATE_GUID,
                                "a blocked target must never be chosen again")
            self.assertTrue(chosen.is_acceptable)
            self.assertEqual(pm._scheme_name_key(chosen.name),
                             pm._scheme_name_key("High Performance"),
                             "the fallback must come from the High Performance family")
        finally:
            pm.set_unsupported_targets([])

    def test_auto_target_keeps_the_running_scheme_of_a_family(self):
        """A machine already on its own performance plan is left alone."""
        real_guid = "bfc4ac38-0621-4fcb-8781-c8ae6504897b"
        plans = [
            pm.PowerPlan(guid=BALANCED_GUID, name="平衡"),
            pm.PowerPlan(guid=real_guid, name="高性能", is_active=True, is_acceptable=True),
            pm.PowerPlan(guid=HIGH_PERF_GUID, name="高性能", is_acceptable=True),
            pm.PowerPlan(guid=ULTIMATE_GUID, name="Ultimate Performance", is_acceptable=True),
        ]
        self.assertEqual(pm.find_auto_target(plans).guid, real_guid)

    def test_switch_result_reports_not_supported(self):
        """A template Windows refuses to activate is reported, not hidden."""
        with mock.patch.object(pm, "_attempt_api_switch",
                               return_value=pm.SwitchResult(False, "api", "not_supported")), \
             mock.patch.object(pm, "_run_powercfg_raw",
                               side_effect=pm.PowercfgError("not_supported", "不受支持")), \
             mock.patch.object(pm, "active_plan_guid", return_value=BALANCED_GUID):
            result = pm.switch_active_plan(HIGH_PERF_GUID)
        self.assertFalse(result.ok)
        self.assertEqual(result.error, "not_supported")

    def test_switch_result_verified_against_the_real_active_plan(self):
        """If nothing actually changed, success must not be reported."""
        with mock.patch.object(pm, "_attempt_api_switch",
                               return_value=pm.SwitchResult(True, "api")), \
             mock.patch.object(pm, "_run_powercfg_raw", return_value=(0, "")), \
             mock.patch.object(pm, "active_plan_guid", return_value=BALANCED_GUID):
            result = pm.switch_active_plan(HIGH_PERF_GUID)
        self.assertFalse(result.ok, "an unchanged active scheme is a failure")

    def test_switching_to_the_active_plan_is_a_noop_success(self):
        with mock.patch.object(pm, "active_plan_guid", return_value=HIGH_PERF_GUID), \
             mock.patch.object(pm, "_attempt_api_switch") as api:
            result = pm.switch_active_plan(HIGH_PERF_GUID)
        self.assertTrue(result.ok)
        api.assert_not_called()

    def test_plan_matching_target_uses_guid(self):
        high = pm.PowerPlan(guid=HIGH_PERF_GUID, name="whatever", is_acceptable=True)
        self.assertTrue(pm.is_plan_matching_target(high, HIGH_PERF_GUID))
        self.assertFalse(pm.is_plan_matching_target(high, BALANCED_GUID))

    # --- P2.8: distinct failure reasons -----------------------------------

    def test_timeout_is_reported_as_timeout(self):
        with mock.patch.object(pm.subprocess, "run",
                               side_effect=subprocess.TimeoutExpired("powercfg", 10)):
            snapshot = pm.get_power_snapshot()
        self.assertEqual(snapshot.error, "timeout")
        self.assertEqual(self._mutations(), [])

    def test_permission_denied_is_reported(self):
        with mock.patch.object(pm.subprocess, "run",
                               side_effect=lambda argv, **kw: _run_result(
                                   argv, stdout="Access is denied.", code=1)):
            snapshot = pm.get_power_snapshot()
        self.assertEqual(snapshot.error, "permission")

    def test_empty_result_is_not_silent(self):
        with mock.patch.object(pm.subprocess, "run",
                               side_effect=lambda argv, **kw: _run_result(argv, stdout="")):
            snapshot = pm.get_power_snapshot()
        self.assertFalse(snapshot.ok)
        self.assertIn(snapshot.error, ("unavailable", "error"))


class SchemeCreationTestCase(unittest.TestCase):
    """Creating schemes is explicit, reported, and repeatable."""

    def _fake(self, existing_list, duplicate_guid=ULTIMATE_GUID):
        calls = []

        def fake_run(argv, capture_output=True, creationflags=0, timeout=None):
            calls.append(list(argv))
            if argv[1] == "/list":
                return _run_result(argv, stdout=existing_list)
            if argv[1] == "/duplicatescheme":
                return _run_result(
                    argv,
                    stdout=f"Power Scheme GUID: {duplicate_guid}  (卓越性能)\n")
            return _run_result(argv, code=1)
        return fake_run, calls

    def test_missing_scheme_is_created_once_and_reported(self):
        fake_run, calls = self._fake(f"Power Scheme GUID: {BALANCED_GUID}  (平衡)\n")
        with mock.patch.object(pm.subprocess, "run", side_effect=fake_run), \
             mock.patch.object(pm, "_get_plans_from_registry", return_value=[]):
            results = pm.create_missing_builtin_schemes()
        created = [r for r in results if r.ok and r.error != "already_present"]
        self.assertTrue(created, "a missing performance scheme should be created")
        self.assertTrue(all(r.guid for r in created))
        duplicates = [c for c in calls if len(c) > 1 and c[1] == "/duplicatescheme"]
        self.assertEqual(len(duplicates), len(created))

    def test_existing_schemes_are_not_duplicated_again(self):
        existing = (f"Power Scheme GUID: {ULTIMATE_GUID}  (卓越性能)\n"
                    f"Power Scheme GUID: {HIGH_PERF_GUID}  (高性能)\n")
        fake_run, calls = self._fake(existing)
        with mock.patch.object(pm.subprocess, "run", side_effect=fake_run), \
             mock.patch.object(pm, "_get_plans_from_registry", return_value=[]):
            results = pm.create_missing_builtin_schemes()
        self.assertTrue(all(r.error == "already_present" for r in results))
        self.assertEqual([c for c in calls if c[1] == "/duplicatescheme"], [],
                         "no duplicatescheme call when every scheme already exists")

    def test_failure_is_reported_and_retryable(self):
        def failing(argv, capture_output=True, creationflags=0, timeout=None):
            if argv[1] == "/list":
                return _run_result(argv, stdout=f"Power Scheme GUID: {BALANCED_GUID}  (平衡)\n")
            return _run_result(argv, stdout="Access is denied.", code=5)

        with mock.patch.object(pm.subprocess, "run", side_effect=failing), \
             mock.patch.object(pm, "_get_plans_from_registry", return_value=[]):
            first = pm.create_missing_builtin_schemes()
            second = pm.create_missing_builtin_schemes()
        self.assertTrue(any(not r.ok for r in first))
        self.assertTrue(any(r.error == "permission" for r in first))
        self.assertTrue(second, "a failed attempt must not block a retry")


class MonitorThreadTestCase(unittest.TestCase):
    """P1.2: at most one worker, interruptible stop, serialized checks."""

    def test_rapid_toggling_keeps_one_worker(self):
        states = []
        monitor = pm.PowerMonitor(interval_seconds=10,
                                  on_monitor_state_change=states.append)
        with mock.patch.object(pm, "get_power_snapshot") as snapshot:
            snapshot.return_value = pm.PlanSnapshot(
                plans=[pm.PowerPlan(guid=HIGH_PERF_GUID, name="高性能",
                                    is_active=True, is_acceptable=True)])
            threads = set()
            for _ in range(20):
                monitor.start()
                time.sleep(0.01)
                monitor.stop()
                time.sleep(0.01)
                if monitor.worker_thread is not None:
                    threads.add(monitor.worker_thread)
                alive = [t for t in threads if t.is_alive()]
                self.assertLessEqual(len(alive), 1,
                                     "more than one monitor worker was alive at once")
            monitor.stop()
            self.assertTrue(monitor.wait_until_stopped(5.0))

    def test_stop_returns_quickly_even_during_a_slow_check(self):
        monitor = pm.PowerMonitor(interval_seconds=10)
        with mock.patch.object(pm, "get_power_snapshot") as snapshot:
            snapshot.side_effect = lambda: (
                time.sleep(1.5),
                pm.PlanSnapshot(plans=[]),
            )[1]
            monitor.start()
            time.sleep(0.15)
            started = time.perf_counter()
            monitor.stop()
            elapsed = time.perf_counter() - started
            self.assertLess(elapsed, 1.0, "stop() must not block the GUI thread")
            self.assertTrue(monitor.wait_until_stopped(5.0))

    def test_check_now_never_overlaps(self):
        monitor = pm.PowerMonitor(interval_seconds=10)
        entered = threading.Event()
        release = threading.Event()
        concurrent = []

        calls = []
        lock = threading.Lock()

        def slow_snapshot():
            with lock:
                calls.append(1)
                current = len(calls)
            if current > 1:
                concurrent.append(current)
            entered.set()
            release.wait(2.0)
            return pm.PlanSnapshot(
                plans=[pm.PowerPlan(guid=HIGH_PERF_GUID, name="高性能",
                                    is_active=True, is_acceptable=True)])

        with mock.patch.object(pm, "get_power_snapshot", side_effect=slow_snapshot):
            worker = threading.Thread(target=monitor.check_now)
            worker.start()
            self.assertTrue(entered.wait(2.0))
            second = monitor.check_now()
            release.set()
            worker.join(5.0)
        self.assertEqual(concurrent, [], "two checks ran at the same time")
        self.assertEqual(second["status"], "busy")

    def test_worker_exception_is_reported_and_state_reflects_reality(self):
        results = []
        states = []
        monitor = pm.PowerMonitor(interval_seconds=10,
                                  on_status_change=results.append,
                                  on_monitor_state_change=states.append)
        with mock.patch.object(pm, "get_power_snapshot",
                               side_effect=RuntimeError("boom")):
            monitor.start()
            deadline = time.time() + 5
            while not results and time.time() < deadline:
                time.sleep(0.02)
            monitor.stop()
            monitor.wait_until_stopped(5.0)
        self.assertTrue(results, "an unexpected exception must be reported")
        self.assertEqual(results[0]["status"], "error")
        self.assertIn(False, states, "the monitor must report itself stopped")

    def test_running_property_reflects_worker(self):
        monitor = pm.PowerMonitor(interval_seconds=10)
        self.assertFalse(monitor.running)
        with mock.patch.object(pm, "get_power_snapshot") as snapshot:
            snapshot.return_value = pm.PlanSnapshot(plans=[])
            monitor.start()
            deadline = time.time() + 2
            while not monitor.running and time.time() < deadline:
                time.sleep(0.01)
            self.assertTrue(monitor.running)
            monitor.stop()
            monitor.wait_until_stopped(5.0)
        self.assertFalse(monitor.running)

    def test_interval_setter_validates(self):
        monitor = pm.PowerMonitor()
        monitor.interval = 30
        self.assertEqual(monitor.interval, 30)
        for bad in (0, -5, 5, 99999, "abc", None):
            with self.assertRaises((ValueError, TypeError)):
                monitor.interval = bad

    def test_initial_interval_is_validated(self):
        with self.assertRaises(ValueError):
            pm.PowerMonitor(interval_seconds=-1)

    # --- P1.6: a vanished target is never silently replaced ---------------

    def test_missing_target_is_reported_not_replaced(self):
        monitor = pm.PowerMonitor(interval_seconds=10)
        monitor.target_guid = "deadbeef-0000-0000-0000-000000000000"
        snapshot = pm.PlanSnapshot(plans=[
            pm.PowerPlan(guid=BALANCED_GUID, name="平衡", is_active=True),
            pm.PowerPlan(guid=HIGH_PERF_GUID, name="高性能", is_acceptable=True),
        ])
        with mock.patch.object(pm, "get_power_snapshot", return_value=snapshot), \
             mock.patch.object(pm, "set_active_plan") as setter:
            result = monitor.check_now()
        self.assertEqual(result["status"], "target_missing")
        setter.assert_not_called()
        self.assertEqual(monitor.target_guid, "deadbeef-0000-0000-0000-000000000000")

    def test_auto_mode_switches_to_high_performance(self):
        monitor = pm.PowerMonitor(interval_seconds=10)
        snapshot = pm.PlanSnapshot(plans=[
            pm.PowerPlan(guid=BALANCED_GUID, name="平衡", is_active=True),
            pm.PowerPlan(guid=HIGH_PERF_GUID, name="高性能", is_acceptable=True),
        ])
        with mock.patch.object(pm, "get_power_snapshot", return_value=snapshot), \
             mock.patch.object(pm, "switch_active_plan",
                               return_value=pm.SwitchResult(True, "api")) as switcher:
            result = monitor.check_now()
        self.assertEqual(result["status"], "fixed")
        switcher.assert_called_once_with(HIGH_PERF_GUID)

    def test_unusable_target_is_skipped_and_the_next_one_is_used(self):
        """A plan Windows rejects must not stop the app from fixing the plan."""
        pm.set_unsupported_targets([])
        monitor = pm.PowerMonitor(interval_seconds=10)
        snapshot = pm.PlanSnapshot(plans=[
            pm.PowerPlan(guid=BALANCED_GUID, name="平衡", is_active=True),
            pm.PowerPlan(guid=ULTIMATE_GUID, name="Ultimate Performance", is_acceptable=True),
            pm.PowerPlan(guid=HIGH_PERF_GUID, name="高性能", is_acceptable=True),
        ])
        outcomes = {
            ULTIMATE_GUID: pm.SwitchResult(False, "api", "not_supported"),
            HIGH_PERF_GUID: pm.SwitchResult(True, "api"),
        }
        try:
            with mock.patch.object(pm, "get_power_snapshot", return_value=snapshot), \
                 mock.patch.object(pm, "switch_active_plan",
                                   side_effect=lambda guid: outcomes[guid]) as switcher:
                result = monitor.check_now()
            self.assertEqual(result["status"], "fixed")
            self.assertEqual(result["target_guid"], HIGH_PERF_GUID)
            self.assertTrue(result["skipped"], "the skipped plan should be reported")
            self.assertEqual(switcher.call_count, 2)
            self.assertTrue(pm.is_target_blocked(ULTIMATE_GUID),
                            "the refused plan should be remembered")
        finally:
            pm.set_unsupported_targets([])

    def test_blocked_target_falls_back_instead_of_failing(self):
        """The screenshot case: a saved dead target must not fail every check."""
        pm.set_unsupported_targets([ULTIMATE_GUID])
        try:
            monitor = pm.PowerMonitor(interval_seconds=10)
            monitor.target_guid = ULTIMATE_GUID
            plans = [
                pm.PowerPlan(guid=BALANCED_GUID, name="平衡", is_active=True),
                pm.PowerPlan(guid=ULTIMATE_GUID, name="Ultimate Performance",
                             is_acceptable=True),
                pm.PowerPlan(guid=HIGH_PERF_GUID, name="高性能", is_acceptable=True),
            ]
            snapshot = pm.PlanSnapshot(plans=plans)
            def reject_dead(guid):
                if guid == ULTIMATE_GUID:
                    return pm.SwitchResult(False, "api", "not_supported")
                # A real switch changes the active scheme.
                for plan in plans:
                    plan.is_active = plan.guid == guid
                return pm.SwitchResult(True, "api")

            with mock.patch.object(pm, "get_power_snapshot", return_value=snapshot), \
                 mock.patch.object(pm, "switch_active_plan",
                                   side_effect=reject_dead) as switcher:
                events = []
                monitor._on_status_change = events.append
                result = monitor.check_now()
            self.assertTrue(result["fixed"], "the plan should still be switched")
            self.assertEqual([event["status"] for event in events],
                             ["target_blocked", "fixed"],
                             "the dead target is reported, then the switch that followed")
            self.assertNotIn(ULTIMATE_GUID,
                             [call.args[0] for call in switcher.call_args_list[1:]],
                             "the dead target must not be retried")

            # A later check must not repeat the same warning.
            events.clear()
            with mock.patch.object(pm, "get_power_snapshot", return_value=snapshot), \
                 mock.patch.object(pm, "switch_active_plan",
                                   side_effect=reject_dead):
                monitor.check_now()
            self.assertNotIn("target_blocked", [event["status"] for event in events],
                             "the warning must not be repeated on every check")
        finally:
            pm.set_unsupported_targets([])

    def test_dead_target_is_reported_once_then_remembered(self):
        """Windows refusing the target is surfaced, then never retried."""
        pm.set_unsupported_targets([])
        try:
            monitor = pm.PowerMonitor(interval_seconds=10)
            monitor.target_guid = ULTIMATE_GUID
            snapshot = pm.PlanSnapshot(plans=[
                pm.PowerPlan(guid=BALANCED_GUID, name="平衡", is_active=True),
                pm.PowerPlan(guid=ULTIMATE_GUID, name="Ultimate Performance",
                             is_acceptable=True),
            ])
            with mock.patch.object(pm, "get_power_snapshot", return_value=snapshot), \
                 mock.patch.object(pm, "switch_active_plan",
                                   return_value=pm.SwitchResult(False, "api", "not_supported")):
                first = monitor.check_now()
            self.assertEqual(first["status"], "target_blocked")
            self.assertTrue(pm.is_target_blocked(ULTIMATE_GUID))

            # The next check falls back instead of failing a second time.
            with mock.patch.object(pm, "get_power_snapshot", return_value=snapshot), \
                 mock.patch.object(pm, "switch_active_plan") as switcher:
                second = monitor.check_now()
            self.assertNotEqual(second["status"], "target_blocked")
        finally:
            pm.set_unsupported_targets([])

    def test_dead_target_switches_to_the_next_candidate_in_the_same_check(self):
        """One click should both explain the dead target and fix the plan."""
        pm.set_unsupported_targets([])
        monitor = pm.PowerMonitor(interval_seconds=10)
        monitor.target_guid = ULTIMATE_GUID
        snapshot = pm.PlanSnapshot(plans=[
            pm.PowerPlan(guid=BALANCED_GUID, name="平衡", is_active=True),
            pm.PowerPlan(guid=ULTIMATE_GUID, name="Ultimate Performance", is_acceptable=True),
            pm.PowerPlan(guid=HIGH_PERF_GUID, name="高性能", is_acceptable=True),
        ])
        calls = []

        def fake_switch(guid):
            calls.append(guid)
            if guid == HIGH_PERF_GUID:
                return pm.SwitchResult(True, "api")
            return pm.SwitchResult(False, "api", "not_supported")

        try:
            with mock.patch.object(pm, "get_power_snapshot", return_value=snapshot), \
                 mock.patch.object(pm, "switch_active_plan", side_effect=fake_switch):
                result = monitor.check_now()
            self.assertEqual(calls, [ULTIMATE_GUID, HIGH_PERF_GUID],
                             "the very first check should also try the fallback")
            self.assertEqual(result["status"], "fixed")
            self.assertEqual(result["target_guid"], HIGH_PERF_GUID)
        finally:
            pm.set_unsupported_targets([])

    def test_all_targets_unusable_is_reported_as_failure(self):
        monitor = pm.PowerMonitor(interval_seconds=10)
        snapshot = pm.PlanSnapshot(plans=[
            pm.PowerPlan(guid=BALANCED_GUID, name="平衡", is_active=True),
            pm.PowerPlan(guid=ULTIMATE_GUID, name="Ultimate Performance", is_acceptable=True),
        ])
        with mock.patch.object(pm, "get_power_snapshot", return_value=snapshot), \
             mock.patch.object(pm, "switch_active_plan",
                               return_value=pm.SwitchResult(False, "api", "not_supported")):
            result = monitor.check_now()
        self.assertEqual(result["status"], "fix_failed")
        self.assertEqual(result["error"], "not_supported")


if __name__ == "__main__":
    unittest.main()
