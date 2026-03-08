"""Tests for drawdown circuit breaker — RISK-001."""

from __future__ import annotations

from decimal import Decimal

from risk.drawdown_breaker import (
    CRITICAL_THRESHOLD,
    WARNING_THRESHOLD,
    DrawdownBreaker,
)


def _make_breaker(**kwargs) -> DrawdownBreaker:
    return DrawdownBreaker(**kwargs)


# ---------------------------------------------------------------------------
# Peak tracking
# ---------------------------------------------------------------------------
class TestPeakTracking:

    def test_initial_peak(self) -> None:
        b = _make_breaker(initial_value=Decimal("10000"))
        assert b.peak_value == Decimal("10000")

    def test_peak_updates_upward(self) -> None:
        b = _make_breaker(initial_value=Decimal("10000"))
        b.update(Decimal("11000"))
        assert b.peak_value == Decimal("11000")

    def test_peak_does_not_decrease(self) -> None:
        b = _make_breaker(initial_value=Decimal("10000"))
        b.update(Decimal("11000"))
        b.update(Decimal("9000"))
        assert b.peak_value == Decimal("11000")

    def test_drawdown_pct_calculation(self) -> None:
        b = _make_breaker(initial_value=Decimal("10000"))
        b.update(Decimal("9000"))
        assert b.drawdown_pct == Decimal("0.1")  # 10%

    def test_zero_drawdown_at_peak(self) -> None:
        b = _make_breaker(initial_value=Decimal("10000"))
        assert b.drawdown_pct == Decimal(0)

    def test_drawdown_pct_zero_peak(self) -> None:
        b = _make_breaker()
        assert b.drawdown_pct == Decimal(0)


# ---------------------------------------------------------------------------
# Warning threshold (15%)
# ---------------------------------------------------------------------------
class TestWarningThreshold:

    def test_warning_at_15pct(self) -> None:
        b = _make_breaker(initial_value=Decimal("10000"))
        b.update(Decimal("8500"))
        assert b.entries_paused
        assert not b.trading_halted
        assert b.level == "warning"

    def test_no_warning_below_15pct(self) -> None:
        b = _make_breaker(initial_value=Decimal("10000"))
        b.update(Decimal("8600"))
        assert not b.entries_paused
        assert b.level == "normal"

    def test_can_open_position_normal(self) -> None:
        b = _make_breaker(initial_value=Decimal("10000"))
        assert b.can_open_position()

    def test_cannot_open_position_warning(self) -> None:
        b = _make_breaker(initial_value=Decimal("10000"))
        b.update(Decimal("8500"))
        assert not b.can_open_position()

    def test_alert_generated_on_warning(self) -> None:
        b = _make_breaker(initial_value=Decimal("10000"))
        b.update(Decimal("8500"))
        assert len(b.alerts) == 1
        assert b.alerts[0]["level"] == "warning"
        assert b.alerts[0]["action"] == "pause_new_entries"

    def test_warning_alert_only_once(self) -> None:
        b = _make_breaker(initial_value=Decimal("10000"))
        b.update(Decimal("8500"))
        b.update(Decimal("8400"))
        # Only one warning alert, not two
        warning_alerts = [a for a in b.alerts if a["level"] == "warning"]
        assert len(warning_alerts) == 1

    def test_default_thresholds(self) -> None:
        assert WARNING_THRESHOLD == Decimal("0.15")
        assert CRITICAL_THRESHOLD == Decimal("0.20")


# ---------------------------------------------------------------------------
# Critical threshold (20%)
# ---------------------------------------------------------------------------
class TestCriticalThreshold:

    def test_critical_at_20pct(self) -> None:
        b = _make_breaker(initial_value=Decimal("10000"))
        b.update(Decimal("7999"))
        assert b.trading_halted
        assert b.entries_paused
        assert b.level == "critical"

    def test_should_unwind_all_at_critical(self) -> None:
        b = _make_breaker(initial_value=Decimal("10000"))
        b.update(Decimal("7999"))
        assert b.should_unwind_all()

    def test_not_unwind_below_critical(self) -> None:
        b = _make_breaker(initial_value=Decimal("10000"))
        b.update(Decimal("8500"))
        assert not b.should_unwind_all()

    def test_alert_generated_on_critical(self) -> None:
        b = _make_breaker(initial_value=Decimal("10000"))
        b.update(Decimal("7999"))
        critical_alerts = [
            a for a in b.alerts if a["level"] == "critical"
        ]
        assert len(critical_alerts) == 1
        assert critical_alerts[0]["action"] == "halt_all_trading"

    def test_cannot_open_position_critical(self) -> None:
        b = _make_breaker(initial_value=Decimal("10000"))
        b.update(Decimal("7999"))
        assert not b.can_open_position()


# ---------------------------------------------------------------------------
# Boundary: exactly 20% must NOT trigger critical (spec says >20%)
# ---------------------------------------------------------------------------
class TestCriticalBoundary:

    def test_exactly_20pct_does_not_trigger_critical(self) -> None:
        b = _make_breaker(initial_value=Decimal("10000"))
        b.update(Decimal("8000"))  # exactly 20%
        assert not b.trading_halted
        assert not b.should_unwind_all()

    def test_just_over_20pct_triggers_critical(self) -> None:
        b = _make_breaker(initial_value=Decimal("10000"))
        b.update(Decimal("7999"))  # 20.01%
        assert b.trading_halted
        assert b.should_unwind_all()


# ---------------------------------------------------------------------------
# Unwind orders
# ---------------------------------------------------------------------------
class TestUnwindOrders:

    def test_generates_schema_compliant_orders(self) -> None:
        b = _make_breaker(initial_value=Decimal("10000"))
        b.update(Decimal("7999"))
        positions = [
            {"id": "p1", "asset": "ETH", "protocol": "aave_v3", "value": "5000"},
            {"id": "p2", "asset": "WBTC", "protocol": "aerodrome", "value": "3000"},
        ]
        orders = b.get_unwind_orders(positions, correlation_id="corr-123")
        assert len(orders) == 2

        # Verify first order is fully schema-compliant
        o = orders[0]
        assert o["version"] == "1.0.0"
        assert o["orderId"]  # non-empty UUID
        assert o["correlationId"] == "corr-123"
        assert o["timestamp"]  # ISO 8601 string
        assert o["chain"] == "base"
        assert o["protocol"] == "aave_v3"
        assert o["action"] == "withdraw"
        assert o["strategy"] == "CB:drawdown"
        assert o["priority"] == "urgent"
        assert o["params"]["tokenIn"] == "ETH"
        assert o["params"]["amount"] == "5000"
        assert o["limits"]["maxGasWei"] == "500000000000000"
        assert o["limits"]["maxSlippageBps"] == 50
        assert isinstance(o["limits"]["deadlineUnix"], int)

        # Verify second order picks up its protocol
        assert orders[1]["protocol"] == "aerodrome"
        assert orders[1]["params"]["tokenIn"] == "WBTC"

    def test_cb_drawdown_strategy_prefix(self) -> None:
        b = _make_breaker(initial_value=Decimal("10000"))
        b.update(Decimal("7999"))
        orders = b.get_unwind_orders(
            [{"id": "p1", "asset": "ETH", "protocol": "aave_v3"}],
            correlation_id="c1",
        )
        assert orders[0]["strategy"] == "CB:drawdown"

    def test_no_orders_when_not_critical(self) -> None:
        b = _make_breaker(initial_value=Decimal("10000"))
        b.update(Decimal("8500"))
        orders = b.get_unwind_orders([{"id": "p1", "asset": "ETH"}])
        assert len(orders) == 0

    def test_empty_positions_returns_empty(self) -> None:
        b = _make_breaker(initial_value=Decimal("10000"))
        b.update(Decimal("7999"))
        assert b.get_unwind_orders([]) == []

    def test_default_protocol_is_aave_v3(self) -> None:
        b = _make_breaker(initial_value=Decimal("10000"))
        b.update(Decimal("7999"))
        orders = b.get_unwind_orders([{"id": "p1", "asset": "ETH"}])
        assert orders[0]["protocol"] == "aave_v3"

    def test_unique_order_ids(self) -> None:
        b = _make_breaker(initial_value=Decimal("10000"))
        b.update(Decimal("7999"))
        positions = [
            {"id": "p1", "asset": "ETH"},
            {"id": "p2", "asset": "WBTC"},
        ]
        orders = b.get_unwind_orders(positions)
        assert orders[0]["orderId"] != orders[1]["orderId"]


# ---------------------------------------------------------------------------
# Manual restart (cannot be overridden programmatically)
# ---------------------------------------------------------------------------
class TestManualRestart:

    def test_restart_clears_halt(self) -> None:
        b = _make_breaker(initial_value=Decimal("10000"))
        b.update(Decimal("7999"))
        assert b.trading_halted
        assert b.manual_restart()
        assert not b.trading_halted
        assert not b.entries_paused
        assert b.can_open_position()

    def test_restart_resets_peak(self) -> None:
        b = _make_breaker(initial_value=Decimal("10000"))
        b.update(Decimal("7999"))
        b.manual_restart()
        assert b.peak_value == Decimal("7999")

    def test_restart_when_not_halted_returns_false(self) -> None:
        b = _make_breaker(initial_value=Decimal("10000"))
        assert not b.manual_restart()

    def test_no_auto_resume_from_critical(self) -> None:
        b = _make_breaker(initial_value=Decimal("10000"))
        b.update(Decimal("7999"))
        assert b.trading_halted
        # Even if value recovers, critical halt persists
        b.update(Decimal("9500"))
        assert b.trading_halted


# ---------------------------------------------------------------------------
# Recovery from warning (but not critical)
# ---------------------------------------------------------------------------
class TestRecovery:

    def test_warning_clears_on_recovery(self) -> None:
        b = _make_breaker(initial_value=Decimal("10000"))
        b.update(Decimal("8500"))
        assert b.entries_paused
        # Value recovers above 15% threshold
        b.update(Decimal("10000"))
        assert not b.entries_paused
        assert b.level == "normal"

    def test_critical_does_not_clear_on_recovery(self) -> None:
        b = _make_breaker(initial_value=Decimal("10000"))
        b.update(Decimal("7999"))
        assert b.trading_halted
        b.update(Decimal("10000"))
        assert b.trading_halted


# ---------------------------------------------------------------------------
# State snapshot
# ---------------------------------------------------------------------------
class TestStateSnapshot:

    def test_get_state_normal(self) -> None:
        b = _make_breaker(initial_value=Decimal("10000"))
        state = b.get_state()
        assert state.level == "normal"
        assert state.peak_value == Decimal("10000")
        assert state.drawdown_pct == Decimal(0)
        assert not state.entries_paused
        assert not state.trading_halted

    def test_get_state_warning(self) -> None:
        b = _make_breaker(initial_value=Decimal("10000"))
        state = b.update(Decimal("8500"))
        assert state.level == "warning"
        assert state.entries_paused
        assert state.triggered_at is not None

    def test_get_state_critical(self) -> None:
        b = _make_breaker(initial_value=Decimal("10000"))
        state = b.update(Decimal("7999"))
        assert state.level == "critical"
        assert state.trading_halted
        assert state.triggered_at is not None


# ---------------------------------------------------------------------------
# Custom thresholds
# ---------------------------------------------------------------------------
class TestCustomThresholds:

    def test_custom_warning_threshold(self) -> None:
        b = _make_breaker(
            initial_value=Decimal("10000"),
            warning_threshold=Decimal("0.05"),
        )
        b.update(Decimal("9400"))
        assert b.entries_paused

    def test_custom_critical_threshold(self) -> None:
        b = _make_breaker(
            initial_value=Decimal("10000"),
            critical_threshold=Decimal("0.10"),
        )
        b.update(Decimal("8999"))
        assert b.trading_halted
