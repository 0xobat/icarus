"""Tests for gas spike circuit breaker — RISK-003."""

from __future__ import annotations

from decimal import Decimal

from risk.gas_spike_breaker import (
    URGENT_OPERATIONS,
    GasSpikeBreaker,
)


def _make_breaker(**kwargs) -> GasSpikeBreaker:
    return GasSpikeBreaker(**kwargs)


# ---------------------------------------------------------------------------
# Spike detection
# ---------------------------------------------------------------------------
class TestSpikeDetection:

    def test_activates_above_3x(self) -> None:
        b = _make_breaker()
        b.update(Decimal("90"), Decimal("25"))  # 3.6x
        assert b.is_active

    def test_not_active_below_3x(self) -> None:
        b = _make_breaker()
        b.update(Decimal("70"), Decimal("25"))  # 2.8x
        assert not b.is_active

    def test_not_active_at_exactly_3x(self) -> None:
        b = _make_breaker()
        b.update(Decimal("75"), Decimal("25"))  # exactly 3x
        assert not b.is_active

    def test_deactivates_when_gas_drops(self) -> None:
        b = _make_breaker()
        b.update(Decimal("90"), Decimal("25"))  # spike
        assert b.is_active
        b.update(Decimal("70"), Decimal("25"))  # drops
        assert not b.is_active

    def test_custom_multiplier(self) -> None:
        b = _make_breaker(spike_multiplier=Decimal("2"))
        b.update(Decimal("60"), Decimal("25"))  # 2.4x > 2x
        assert b.is_active

    def test_zero_average_safe(self) -> None:
        b = _make_breaker()
        state = b.update(Decimal("100"), Decimal("0"))
        assert not b.is_active
        assert state.threshold == Decimal(0)


# ---------------------------------------------------------------------------
# Operation filtering
# ---------------------------------------------------------------------------
class TestOperationFiltering:

    def test_all_allowed_when_inactive(self) -> None:
        b = _make_breaker()
        assert b.is_operation_allowed("new_entry")
        assert b.is_operation_allowed("rebalancing")
        assert b.is_operation_allowed("harvesting")
        assert b.is_operation_allowed("stop_loss")

    def test_non_urgent_blocked_when_active(self) -> None:
        b = _make_breaker()
        b.update(Decimal("90"), Decimal("25"))
        assert not b.is_operation_allowed("new_entry")
        assert not b.is_operation_allowed("rebalancing")
        assert not b.is_operation_allowed("harvesting")

    def test_urgent_allowed_when_active(self) -> None:
        b = _make_breaker()
        b.update(Decimal("90"), Decimal("25"))
        assert b.is_operation_allowed("stop_loss")
        assert b.is_operation_allowed("emergency_withdrawal")
        assert b.is_operation_allowed("liquidation_protection")
        assert b.is_operation_allowed("close_position")

    def test_urgent_operations_set(self) -> None:
        assert "stop_loss" in URGENT_OPERATIONS
        assert "emergency_withdrawal" in URGENT_OPERATIONS
        assert "liquidation_protection" in URGENT_OPERATIONS
        assert "close_position" in URGENT_OPERATIONS


# ---------------------------------------------------------------------------
# Operation queuing
# ---------------------------------------------------------------------------
class TestOperationQueuing:

    def test_queues_non_urgent(self) -> None:
        b = _make_breaker()
        b.update(Decimal("90"), Decimal("25"))
        op = b.queue_operation(
            operation_id="op1",
            operation_type="new_entry",
            payload={"asset": "ETH", "amount": "1.0"},
            strategy_id="STRAT-001",
        )
        assert op is not None
        assert op.operation_id == "op1"
        assert op.operation_type == "new_entry"
        assert len(b.queued_operations) == 1

    def test_does_not_queue_urgent(self) -> None:
        b = _make_breaker()
        b.update(Decimal("90"), Decimal("25"))
        op = b.queue_operation(
            operation_id="op1",
            operation_type="stop_loss",
            payload={"position_id": "p1"},
        )
        assert op is None
        assert len(b.queued_operations) == 0

    def test_does_not_queue_when_inactive(self) -> None:
        b = _make_breaker()
        op = b.queue_operation(
            operation_id="op1",
            operation_type="new_entry",
            payload={"asset": "ETH"},
        )
        assert op is None
        assert len(b.queued_operations) == 0

    def test_multiple_queued(self) -> None:
        b = _make_breaker()
        b.update(Decimal("90"), Decimal("25"))
        b.queue_operation(
            operation_id="op1", operation_type="new_entry",
            payload={},
        )
        b.queue_operation(
            operation_id="op2", operation_type="rebalancing",
            payload={},
        )
        assert len(b.queued_operations) == 2

    def test_release_queue(self) -> None:
        b = _make_breaker()
        b.update(Decimal("90"), Decimal("25"))
        b.queue_operation(
            operation_id="op1", operation_type="new_entry",
            payload={"asset": "ETH"},
        )
        b.queue_operation(
            operation_id="op2", operation_type="rebalancing",
            payload={"target": "50/50"},
        )
        released = b.release_queue()
        assert len(released) == 2
        assert released[0].operation_id == "op1"
        assert released[1].operation_id == "op2"
        assert len(b.queued_operations) == 0

    def test_release_empty_queue(self) -> None:
        b = _make_breaker()
        released = b.release_queue()
        assert len(released) == 0


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------
class TestAlerts:

    def test_alert_on_activation(self) -> None:
        b = _make_breaker()
        b.update(Decimal("90"), Decimal("25"))
        assert len(b.alerts) == 1
        assert b.alerts[0]["event"] == "gas_spike_activated"

    def test_alert_on_deactivation(self) -> None:
        b = _make_breaker()
        b.update(Decimal("90"), Decimal("25"))
        b.update(Decimal("70"), Decimal("25"))
        assert len(b.alerts) == 2
        assert b.alerts[1]["event"] == "gas_spike_deactivated"

    def test_no_duplicate_activation_alerts(self) -> None:
        b = _make_breaker()
        b.update(Decimal("90"), Decimal("25"))
        b.update(Decimal("95"), Decimal("25"))
        activation_alerts = [
            a for a in b.alerts
            if a["event"] == "gas_spike_activated"
        ]
        assert len(activation_alerts) == 1

    def test_no_alert_when_normal(self) -> None:
        b = _make_breaker()
        b.update(Decimal("50"), Decimal("25"))
        assert len(b.alerts) == 0


# ---------------------------------------------------------------------------
# State snapshot
# ---------------------------------------------------------------------------
class TestStateSnapshot:

    def test_state_inactive(self) -> None:
        b = _make_breaker()
        b.update(Decimal("50"), Decimal("25"))
        state = b.get_state()
        assert not state.is_active
        assert state.current_gas == Decimal("50")
        assert state.average_gas == Decimal("25")
        assert state.threshold == Decimal("75")
        assert state.queued_count == 0

    def test_state_active(self) -> None:
        b = _make_breaker()
        b.update(Decimal("90"), Decimal("25"))
        state = b.get_state()
        assert state.is_active
        assert state.activated_at is not None

    def test_state_with_queue(self) -> None:
        b = _make_breaker()
        b.update(Decimal("90"), Decimal("25"))
        b.queue_operation(
            operation_id="op1", operation_type="new_entry",
            payload={},
        )
        state = b.get_state()
        assert state.queued_count == 1
