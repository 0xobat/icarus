"""Tests for TX failure rate monitor — RISK-004."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from risk.tx_failure_monitor import (
    DEFAULT_FAILURE_THRESHOLD,
    DEFAULT_WINDOW_SECONDS,
    PARAMETER_ERRORS,
    SYSTEMIC_ERRORS,
    TxFailureMonitor,
)


def _make_monitor(**kwargs) -> TxFailureMonitor:
    return TxFailureMonitor(**kwargs)


def _now() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# Failure recording
# ---------------------------------------------------------------------------
class TestFailureRecording:

    def test_records_failure(self) -> None:
        mon = _make_monitor()
        f = mon.record_failure(
            tx_id="tx1", reason="revert", details="ERC20 approval",
        )
        assert f.tx_id == "tx1"
        assert f.reason == "revert"
        assert f.category == "parameter"
        assert f.details == "ERC20 approval"

    def test_failure_count_increments(self) -> None:
        mon = _make_monitor()
        mon.record_failure(tx_id="tx1", reason="revert")
        mon.record_failure(tx_id="tx2", reason="timeout")
        assert mon.get_failure_count() == 2

    def test_to_dict(self) -> None:
        mon = _make_monitor()
        f = mon.record_failure(
            tx_id="tx1", reason="revert",
            strategy_id="STRAT-001",
        )
        d = f.to_dict()
        assert d["tx_id"] == "tx1"
        assert d["reason"] == "revert"
        assert d["category"] == "parameter"
        assert d["strategy_id"] == "STRAT-001"

    def test_defaults(self) -> None:
        assert DEFAULT_FAILURE_THRESHOLD == 3
        assert DEFAULT_WINDOW_SECONDS == 3600


# ---------------------------------------------------------------------------
# Failure classification
# ---------------------------------------------------------------------------
class TestFailureClassification:

    def test_revert_is_parameter(self) -> None:
        mon = _make_monitor()
        f = mon.record_failure(tx_id="tx1", reason="revert")
        assert f.category == "parameter"

    def test_out_of_gas_is_parameter(self) -> None:
        mon = _make_monitor()
        f = mon.record_failure(tx_id="tx1", reason="out_of_gas")
        assert f.category == "parameter"

    def test_nonce_issue_is_parameter(self) -> None:
        mon = _make_monitor()
        f = mon.record_failure(tx_id="tx1", reason="nonce_issue")
        assert f.category == "parameter"

    def test_timeout_is_systemic(self) -> None:
        mon = _make_monitor()
        f = mon.record_failure(tx_id="tx1", reason="timeout")
        assert f.category == "systemic"

    def test_network_error_is_systemic(self) -> None:
        mon = _make_monitor()
        f = mon.record_failure(tx_id="tx1", reason="network_error")
        assert f.category == "systemic"

    def test_rpc_error_is_systemic(self) -> None:
        mon = _make_monitor()
        f = mon.record_failure(tx_id="tx1", reason="rpc_error")
        assert f.category == "systemic"

    def test_unknown_reason(self) -> None:
        mon = _make_monitor()
        f = mon.record_failure(tx_id="tx1", reason="alien_abduction")
        assert f.category == "unknown"

    def test_parameter_errors_set(self) -> None:
        assert "revert" in PARAMETER_ERRORS
        assert "out_of_gas" in PARAMETER_ERRORS
        assert "nonce_issue" in PARAMETER_ERRORS

    def test_systemic_errors_set(self) -> None:
        assert "timeout" in SYSTEMIC_ERRORS
        assert "network_error" in SYSTEMIC_ERRORS
        assert "rpc_error" in SYSTEMIC_ERRORS


# ---------------------------------------------------------------------------
# Threshold breach → pause + diagnostic mode
# ---------------------------------------------------------------------------
class TestThresholdBreach:

    def test_pauses_at_threshold(self) -> None:
        mon = _make_monitor(failure_threshold=3)
        mon.record_failure(tx_id="tx1", reason="revert")
        mon.record_failure(tx_id="tx2", reason="timeout")
        mon.record_failure(tx_id="tx3", reason="out_of_gas")
        assert not mon.is_paused  # at threshold, not over
        mon.record_failure(tx_id="tx4", reason="revert")
        assert mon.is_paused
        assert mon.diagnostic_mode

    def test_cannot_execute_when_paused(self) -> None:
        mon = _make_monitor(failure_threshold=2)
        mon.record_failure(tx_id="tx1", reason="revert")
        mon.record_failure(tx_id="tx2", reason="timeout")
        assert mon.can_execute()
        mon.record_failure(tx_id="tx3", reason="revert")
        assert not mon.can_execute()

    def test_alert_on_breach(self) -> None:
        mon = _make_monitor(failure_threshold=2)
        mon.record_failure(tx_id="tx1", reason="revert")
        mon.record_failure(tx_id="tx2", reason="timeout")
        mon.record_failure(tx_id="tx3", reason="revert")
        assert len(mon.alerts) == 1
        assert mon.alerts[0]["event"] == "tx_failure_threshold_breached"
        assert mon.alerts[0]["failures_in_window"] == 3

    def test_only_one_breach_alert(self) -> None:
        mon = _make_monitor(failure_threshold=2)
        for i in range(5):
            mon.record_failure(tx_id=f"tx{i}", reason="revert")
        breach_alerts = [
            a for a in mon.alerts
            if a["event"] == "tx_failure_threshold_breached"
        ]
        assert len(breach_alerts) == 1


# ---------------------------------------------------------------------------
# Rolling window
# ---------------------------------------------------------------------------
class TestRollingWindow:

    def test_old_failures_pruned(self) -> None:
        mon = _make_monitor(window_seconds=3600)
        old_time = _now() - timedelta(hours=2)
        mon.record_failure(
            tx_id="tx1", reason="revert", now=old_time,
        )
        # After pruning, old failure should be gone
        assert mon.get_failure_count() == 0

    def test_recent_failures_kept(self) -> None:
        mon = _make_monitor(window_seconds=3600)
        mon.record_failure(tx_id="tx1", reason="revert")
        assert mon.get_failure_count() == 1

    def test_mixed_old_and_new(self) -> None:
        mon = _make_monitor(window_seconds=3600)
        old_time = _now() - timedelta(hours=2)
        mon.record_failure(
            tx_id="tx_old", reason="revert", now=old_time,
        )
        mon.record_failure(tx_id="tx_new", reason="timeout")
        assert mon.get_failure_count() == 1

    def test_get_failures_in_window(self) -> None:
        mon = _make_monitor()
        mon.record_failure(tx_id="tx1", reason="revert")
        mon.record_failure(tx_id="tx2", reason="timeout")
        failures = mon.get_failures_in_window()
        assert len(failures) == 2
        assert failures[0].tx_id == "tx1"


# ---------------------------------------------------------------------------
# No auto-resume
# ---------------------------------------------------------------------------
class TestNoAutoResume:

    def test_does_not_auto_resume(self) -> None:
        mon = _make_monitor(failure_threshold=2)
        now = _now()
        for i in range(4):
            mon.record_failure(
                tx_id=f"tx{i}", reason="revert", now=now,
            )
        assert mon.is_paused
        # Even after window expires, still paused
        future = now + timedelta(hours=2)
        assert mon.get_failure_count(now=future) == 0
        assert mon.is_paused  # still paused!

    def test_manual_resume_required(self) -> None:
        mon = _make_monitor(failure_threshold=2)
        for i in range(4):
            mon.record_failure(tx_id=f"tx{i}", reason="revert")
        assert mon.is_paused
        assert mon.manual_resume()
        assert not mon.is_paused
        assert not mon.diagnostic_mode
        assert mon.can_execute()

    def test_manual_resume_when_not_paused(self) -> None:
        mon = _make_monitor()
        assert not mon.manual_resume()


# ---------------------------------------------------------------------------
# Category breakdown
# ---------------------------------------------------------------------------
class TestCategoryBreakdown:

    def test_breakdown_by_category(self) -> None:
        mon = _make_monitor()
        mon.record_failure(tx_id="tx1", reason="revert")
        mon.record_failure(tx_id="tx2", reason="timeout")
        mon.record_failure(tx_id="tx3", reason="revert")
        breakdown = mon.get_category_breakdown()
        assert breakdown["parameter"] == 2
        assert breakdown["systemic"] == 1

    def test_empty_breakdown(self) -> None:
        mon = _make_monitor()
        assert mon.get_category_breakdown() == {}


# ---------------------------------------------------------------------------
# State snapshot
# ---------------------------------------------------------------------------
class TestStateSnapshot:

    def test_state_normal(self) -> None:
        mon = _make_monitor()
        state = mon.get_state()
        assert not state.is_paused
        assert not state.diagnostic_mode
        assert state.failures_in_window == 0
        assert state.threshold == DEFAULT_FAILURE_THRESHOLD
        assert state.last_failure is None

    def test_state_paused(self) -> None:
        mon = _make_monitor(failure_threshold=1)
        mon.record_failure(tx_id="tx1", reason="revert")
        mon.record_failure(tx_id="tx2", reason="timeout")
        state = mon.get_state()
        assert state.is_paused
        assert state.diagnostic_mode
        assert state.failures_in_window == 2
        assert state.last_failure is not None
        assert "revert" in state.failure_breakdown

    def test_state_breakdown(self) -> None:
        mon = _make_monitor()
        mon.record_failure(tx_id="tx1", reason="revert")
        mon.record_failure(tx_id="tx2", reason="revert")
        mon.record_failure(tx_id="tx3", reason="timeout")
        state = mon.get_state()
        assert state.failure_breakdown["revert"] == 2
        assert state.failure_breakdown["timeout"] == 1


# ---------------------------------------------------------------------------
# Hold mode integration (RISK-004)
# ---------------------------------------------------------------------------
class TestHoldModeIntegration:

    def test_enters_hold_mode_on_breach(self) -> None:
        from harness.hold_mode import HoldMode, HoldTrigger

        hm = HoldMode()
        mon = _make_monitor(failure_threshold=2, hold_mode=hm)
        for i in range(3):
            mon.record_failure(tx_id=f"tx{i}", reason="revert")
        assert mon.is_paused
        assert hm.is_active()
        assert hm.trigger == HoldTrigger.TX_FAILURE_RATE

    def test_hold_mode_not_entered_below_threshold(self) -> None:
        from harness.hold_mode import HoldMode

        hm = HoldMode()
        mon = _make_monitor(failure_threshold=3, hold_mode=hm)
        for i in range(3):
            mon.record_failure(tx_id=f"tx{i}", reason="revert")
        assert not mon.is_paused
        assert not hm.is_active()

    def test_works_without_hold_mode(self) -> None:
        mon = _make_monitor(failure_threshold=2)
        for i in range(3):
            mon.record_failure(tx_id=f"tx{i}", reason="revert")
        assert mon.is_paused  # still pauses internally

    def test_hold_mode_diagnostics_context(self) -> None:
        from harness.hold_mode import HoldMode

        hm = HoldMode()
        mon = _make_monitor(failure_threshold=2, hold_mode=hm)
        for i in range(3):
            mon.record_failure(tx_id=f"tx{i}", reason="revert")
        diag = hm.diagnostics
        assert diag is not None
        assert diag.trigger == "tx_failure_rate"
        assert "failures_in_window" in diag.context


# ---------------------------------------------------------------------------
# is_triggered
# ---------------------------------------------------------------------------
class TestIsTriggered:

    def test_not_triggered_initially(self) -> None:
        mon = _make_monitor()
        assert not mon.is_triggered()

    def test_triggered_when_over_threshold(self) -> None:
        mon = _make_monitor(failure_threshold=2)
        for i in range(3):
            mon.record_failure(tx_id=f"tx{i}", reason="revert")
        assert mon.is_triggered()

    def test_not_triggered_at_threshold(self) -> None:
        mon = _make_monitor(failure_threshold=3)
        for i in range(3):
            mon.record_failure(tx_id=f"tx{i}", reason="revert")
        assert not mon.is_triggered()

    def test_triggered_clears_after_window(self) -> None:
        mon = _make_monitor(failure_threshold=2, window_seconds=3600)
        now = _now()
        for i in range(3):
            mon.record_failure(tx_id=f"tx{i}", reason="revert", now=now)
        assert mon.is_triggered(now=now)
        future = now + timedelta(hours=2)
        assert not mon.is_triggered(now=future)


# ---------------------------------------------------------------------------
# Auto-clear (RISK-004)
# ---------------------------------------------------------------------------
class TestAutoClear:

    def test_auto_clears_on_success_after_window_expires(self) -> None:
        from harness.hold_mode import HoldMode

        hm = HoldMode()
        mon = _make_monitor(
            failure_threshold=2, window_seconds=3600, hold_mode=hm,
        )
        now = _now()
        for i in range(3):
            mon.record_failure(tx_id=f"tx{i}", reason="revert", now=now)
        assert mon.is_paused
        assert hm.is_active()

        # After window expires, record a success → should auto-clear
        future = now + timedelta(hours=2)
        mon.record_success("tx_ok", now=future)
        assert not mon.is_paused
        assert not mon.diagnostic_mode
        assert mon.can_execute()
        assert not hm.is_active()

    def test_no_auto_clear_while_still_over_threshold(self) -> None:
        from harness.hold_mode import HoldMode

        hm = HoldMode()
        mon = _make_monitor(
            failure_threshold=2, window_seconds=3600, hold_mode=hm,
        )
        now = _now()
        for i in range(3):
            mon.record_failure(tx_id=f"tx{i}", reason="revert", now=now)
        assert mon.is_paused

        # Success within the same window — still over threshold
        mon.record_success("tx_ok", now=now)
        assert mon.is_paused
        assert hm.is_active()

    def test_auto_clear_without_hold_mode(self) -> None:
        mon = _make_monitor(
            failure_threshold=2, window_seconds=3600,
        )
        now = _now()
        for i in range(3):
            mon.record_failure(tx_id=f"tx{i}", reason="revert", now=now)
        assert mon.is_paused

        future = now + timedelta(hours=2)
        mon.record_success("tx_ok", now=future)
        assert not mon.is_paused
        assert mon.can_execute()
