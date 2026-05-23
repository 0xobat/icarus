"""Tests for hold mode — HARNESS-005."""

from __future__ import annotations

from unittest.mock import MagicMock

from harness.hold_mode import (
    REDIS_SYSTEM_STATUS_KEY,
    STATUS_HOLD,
    STATUS_NORMAL,
    HoldDiagnostics,
    HoldMode,
    HoldTrigger,
)


def _make_redis_mock(initial_status: str | None = None) -> MagicMock:
    """Create a mock Redis client with get/set support."""
    store: dict[str, str] = {}
    if initial_status is not None:
        store[REDIS_SYSTEM_STATUS_KEY] = initial_status

    mock = MagicMock()
    mock.get.side_effect = lambda key: store.get(key)
    mock.set.side_effect = lambda key, val: store.__setitem__(key, val)
    return mock


# ---------------------------------------------------------------------------
# Redis tracking
# ---------------------------------------------------------------------------
class TestRedisTracking:

    def test_sets_status_in_redis_on_enter(self) -> None:
        redis = _make_redis_mock()
        hold = HoldMode(redis=redis)
        hold.enter("API down", HoldTrigger.API_UNAVAILABLE)
        redis.set.assert_called_with(REDIS_SYSTEM_STATUS_KEY, STATUS_HOLD)

    def test_sets_status_in_redis_on_exit(self) -> None:
        redis = _make_redis_mock(initial_status=STATUS_HOLD)
        hold = HoldMode(redis=redis)
        hold.enter("API down", HoldTrigger.API_UNAVAILABLE)
        hold.exit()
        redis.set.assert_called_with(REDIS_SYSTEM_STATUS_KEY, STATUS_NORMAL)

    def test_reads_status_from_redis(self) -> None:
        redis = _make_redis_mock(initial_status=STATUS_HOLD)
        hold = HoldMode(redis=redis)
        assert hold.is_active()

    def test_normal_status_from_redis(self) -> None:
        redis = _make_redis_mock(initial_status=STATUS_NORMAL)
        hold = HoldMode(redis=redis)
        assert not hold.is_active()

    def test_none_redis_value_means_normal(self) -> None:
        redis = _make_redis_mock()  # no key set
        hold = HoldMode(redis=redis)
        assert not hold.is_active()

    def test_bytes_redis_value_decoded(self) -> None:
        mock = MagicMock()
        mock.get.return_value = b"hold"
        hold = HoldMode(redis=mock)
        assert hold.is_active()

    def test_fallback_to_in_memory_on_redis_error(self) -> None:
        mock = MagicMock()
        mock.get.side_effect = ConnectionError("Redis down")
        mock.set.side_effect = ConnectionError("Redis down")
        hold = HoldMode(redis=mock)
        # In-memory defaults to normal
        assert not hold.is_active()
        # Enter should still work via in-memory fallback
        hold.enter("Redis down", HoldTrigger.MANUAL)
        assert hold.is_active()


# ---------------------------------------------------------------------------
# In-memory (no Redis)
# ---------------------------------------------------------------------------
class TestInMemory:

    def test_works_without_redis(self) -> None:
        hold = HoldMode()
        assert not hold.is_active()
        hold.enter("test", HoldTrigger.MANUAL)
        assert hold.is_active()
        hold.exit()
        assert not hold.is_active()


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------
class TestEntry:

    def test_enters_hold_mode(self) -> None:
        hold = HoldMode()
        hold.enter("API timeout", HoldTrigger.API_UNAVAILABLE)
        assert hold.is_active()

    def test_records_trigger(self) -> None:
        hold = HoldMode()
        hold.enter("Budget exhausted", HoldTrigger.BUDGET_EXHAUSTED)
        assert hold.trigger == HoldTrigger.BUDGET_EXHAUSTED

    def test_records_reason(self) -> None:
        hold = HoldMode()
        hold.enter("API returned 429", HoldTrigger.API_UNAVAILABLE)
        assert hold.reason == "API returned 429"

    def test_records_entry_time(self) -> None:
        hold = HoldMode()
        hold.enter("test", HoldTrigger.MANUAL)
        assert hold.entry_time is not None

    def test_returns_diagnostics(self) -> None:
        hold = HoldMode()
        diag = hold.enter("test", HoldTrigger.MANUAL, context={"foo": "bar"})
        assert isinstance(diag, HoldDiagnostics)
        assert diag.trigger == "manual"
        assert diag.reason == "test"
        assert diag.context == {"foo": "bar"}

    def test_diagnostics_has_timestamp(self) -> None:
        hold = HoldMode()
        diag = hold.enter("test", HoldTrigger.MANUAL)
        assert diag.timestamp != ""

    def test_diagnostics_accessible_after_entry(self) -> None:
        hold = HoldMode()
        returned = hold.enter("test", HoldTrigger.MANUAL)
        assert hold.diagnostics is returned

    def test_double_enter_updates_trigger(self) -> None:
        hold = HoldMode()
        hold.enter("API down", HoldTrigger.API_UNAVAILABLE)
        hold.enter("Budget gone", HoldTrigger.BUDGET_EXHAUSTED)
        assert hold.trigger == HoldTrigger.BUDGET_EXHAUSTED
        assert hold.reason == "Budget gone"


# ---------------------------------------------------------------------------
# HoldTrigger enum
# ---------------------------------------------------------------------------
class TestHoldTrigger:

    def test_all_triggers_exist(self) -> None:
        assert HoldTrigger.API_UNAVAILABLE == "api_unavailable"
        assert HoldTrigger.BUDGET_EXHAUSTED == "budget_exhausted"
        assert HoldTrigger.TX_FAILURE_RATE == "tx_failure_rate"
        assert HoldTrigger.IRRECONCILABLE_STATE == "irreconcilable_state"
        assert HoldTrigger.MANUAL == "manual"

    def test_five_triggers(self) -> None:
        assert len(HoldTrigger) == 5


# ---------------------------------------------------------------------------
# Decision blocking
# ---------------------------------------------------------------------------
class TestDecisionBlocking:

    def test_not_blocked_initially(self) -> None:
        hold = HoldMode()
        assert hold.should_block_decisions() is False

    def test_blocked_in_hold_mode(self) -> None:
        hold = HoldMode()
        hold.enter("test", HoldTrigger.MANUAL)
        assert hold.should_block_decisions() is True

    def test_unblocked_after_exit(self) -> None:
        hold = HoldMode()
        hold.enter("test", HoldTrigger.MANUAL)
        hold.exit()
        assert hold.should_block_decisions() is False


# ---------------------------------------------------------------------------
# Exit
# ---------------------------------------------------------------------------
class TestExit:

    def test_exit_clears_active(self) -> None:
        hold = HoldMode()
        hold.enter("test", HoldTrigger.MANUAL)
        hold.exit()
        assert not hold.is_active()

    def test_exit_clears_trigger_and_reason(self) -> None:
        hold = HoldMode()
        hold.enter("test", HoldTrigger.MANUAL)
        hold.exit()
        assert hold.trigger is None
        assert hold.reason is None

    def test_exit_clears_diagnostics(self) -> None:
        hold = HoldMode()
        hold.enter("test", HoldTrigger.MANUAL)
        hold.exit()
        assert hold.diagnostics is None
        assert hold.entry_time is None

    def test_exit_when_not_active_is_noop(self) -> None:
        hold = HoldMode()
        hold.exit()  # should not raise
        assert not hold.is_active()

    def test_can_reenter_after_exit(self) -> None:
        hold = HoldMode()
        hold.enter("first", HoldTrigger.API_UNAVAILABLE)
        hold.exit()
        hold.enter("second", HoldTrigger.BUDGET_EXHAUSTED)
        assert hold.is_active()
        assert hold.trigger == HoldTrigger.BUDGET_EXHAUSTED


# ---------------------------------------------------------------------------
# Auto-resume
# ---------------------------------------------------------------------------
class TestAutoResume:

    def test_auto_resume_api_unavailable(self) -> None:
        hold = HoldMode()
        hold.enter("API down", HoldTrigger.API_UNAVAILABLE)
        resumed = hold.check_auto_resume(api_healthy=True)
        assert resumed is True
        assert not hold.is_active()

    def test_auto_resume_budget_exhausted(self) -> None:
        hold = HoldMode()
        hold.enter("Budget gone", HoldTrigger.BUDGET_EXHAUSTED)
        resumed = hold.check_auto_resume(budget_available=True)
        assert resumed is True
        assert not hold.is_active()

    def test_auto_resume_irreconcilable_state(self) -> None:
        hold = HoldMode()
        hold.enter("State mismatch", HoldTrigger.IRRECONCILABLE_STATE)
        resumed = hold.check_auto_resume(state_reconciled=True)
        assert resumed is True
        assert not hold.is_active()

    def test_auto_resume_tx_failure_rate(self) -> None:
        hold = HoldMode()
        hold.enter("TX failures", HoldTrigger.TX_FAILURE_RATE)
        resumed = hold.check_auto_resume(tx_failure_rate_ok=True)
        assert resumed is True
        assert not hold.is_active()

    def test_manual_trigger_never_auto_resumes(self) -> None:
        hold = HoldMode()
        hold.enter("Manual hold", HoldTrigger.MANUAL)
        resumed = hold.check_auto_resume(
            api_healthy=True,
            budget_available=True,
            state_reconciled=True,
            tx_failure_rate_ok=True,
        )
        assert resumed is False
        assert hold.is_active()

    def test_no_resume_when_condition_not_cleared(self) -> None:
        hold = HoldMode()
        hold.enter("API down", HoldTrigger.API_UNAVAILABLE)
        # Wrong flag — budget, not API
        resumed = hold.check_auto_resume(budget_available=True)
        assert resumed is False
        assert hold.is_active()

    def test_no_resume_when_not_in_hold(self) -> None:
        hold = HoldMode()
        resumed = hold.check_auto_resume(api_healthy=True)
        assert resumed is False

    def test_auto_resume_clears_state(self) -> None:
        hold = HoldMode()
        hold.enter("API down", HoldTrigger.API_UNAVAILABLE)
        hold.check_auto_resume(api_healthy=True)
        assert hold.trigger is None
        assert hold.reason is None
        assert hold.diagnostics is None


# ---------------------------------------------------------------------------
# Diagnostic logging
# ---------------------------------------------------------------------------
class TestDiagnosticLogging:

    def test_entry_context_preserved(self) -> None:
        hold = HoldMode()
        diag = hold.enter(
            "State mismatch",
            HoldTrigger.IRRECONCILABLE_STATE,
            context={
                "expected_balance": "1000 USDC",
                "actual_balance": "800 USDC",
                "discrepancy_pct": 20.0,
            },
        )
        assert diag.context["expected_balance"] == "1000 USDC"
        assert diag.context["actual_balance"] == "800 USDC"
        assert diag.context["discrepancy_pct"] == 20.0

    def test_empty_context_default(self) -> None:
        hold = HoldMode()
        diag = hold.enter("test", HoldTrigger.MANUAL)
        assert diag.context == {}
