"""Tests for INFRA-007 — main decision loop."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

from ai.decision_engine import Decision, DecisionAction
from main import DecisionLoop, _handle_signal

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_redis() -> MagicMock:
    redis = MagicMock()
    redis.connect = MagicMock()
    redis.disconnect = MagicMock()
    redis.publish = MagicMock()
    redis.subscribe = MagicMock()
    return redis


def _mock_db() -> tuple[MagicMock, MagicMock]:
    db_manager = MagicMock()
    db_manager.create_tables = MagicMock()
    db_manager.close = MagicMock()
    repository = MagicMock()
    repository.get_trades = MagicMock(return_value=[])
    repository.get_positions = MagicMock(return_value=[])
    return db_manager, repository


def _mock_state() -> MagicMock:
    state = MagicMock()
    state.save = MagicMock()
    return state


def _make_loop() -> DecisionLoop:
    redis = _mock_redis()
    db_manager, repository = _mock_db()
    state = _mock_state()
    loop = DecisionLoop(redis, db_manager, repository, state)
    # Mock external calls
    loop.price_feed.fetch_prices = MagicMock(return_value={
        "ETH": {"price_usd": 2000},
        "USDC": {"price_usd": 1},
    })
    loop.gas_monitor.update = MagicMock(return_value=SimpleNamespace(
        fast=50, standard=30, slow=20, timestamp="2025-01-01T00:00:00Z",
    ))
    loop.gas_monitor.get_rolling_average = MagicMock(return_value=Decimal("30"))
    # Mock oracle guard to return safe by default
    loop.oracle_guard.check = MagicMock(return_value=SimpleNamespace(
        safe=True, deviations=[], stale=False, reason="ok",
    ))
    loop.oracle_guard.get_deviations = MagicMock(return_value={})
    return loop


def _make_event(event_type: str = "new_block") -> dict:
    return {
        "version": "1.0.0",
        "eventType": event_type,
        "chain": "ethereum",
        "timestamp": "2025-01-01T00:00:00Z",
        "correlationId": "test-cid",
    }


# ---------------------------------------------------------------------------
# DecisionLoop initialization
# ---------------------------------------------------------------------------

class TestDecisionLoopInit:

    def test_creates_all_components(self) -> None:
        loop = _make_loop()
        assert loop.price_feed is not None
        assert loop.gas_monitor is not None
        assert loop.allocator is not None
        assert loop.tracker is not None
        assert loop.drawdown is not None
        assert loop.gas_spike is not None
        assert loop.tx_failures is not None
        assert loop.exposure is not None
        assert loop.lifecycle is not None
        assert loop.synthesizer is not None
        assert loop.decision_engine is not None

    def test_initial_state(self) -> None:
        loop = _make_loop()
        assert loop._cycle_count == 0
        assert loop._adjustment_made is False


# ---------------------------------------------------------------------------
# Decision cycle
# ---------------------------------------------------------------------------

class TestRunCycle:

    def test_hold_on_no_signals(self) -> None:
        loop = _make_loop()
        loop.synthesizer.synthesize = MagicMock(return_value=SimpleNamespace(
            to_dict=lambda: {"active_signals": []},
        ))
        orders = loop.run_cycle(_make_event())
        assert orders == []
        assert loop._cycle_count == 1

    def test_increments_cycle_count(self) -> None:
        loop = _make_loop()
        loop.synthesizer.synthesize = MagicMock(return_value=SimpleNamespace(
            to_dict=lambda: {"active_signals": []},
        ))
        loop.run_cycle(_make_event())
        loop.run_cycle(_make_event())
        assert loop._cycle_count == 2

    def test_resets_adjustment_flag_each_cycle(self) -> None:
        loop = _make_loop()
        loop._adjustment_made = True
        loop.synthesizer.synthesize = MagicMock(return_value=SimpleNamespace(
            to_dict=lambda: {"active_signals": []},
        ))
        loop.run_cycle(_make_event())
        assert loop._adjustment_made is False

    def test_updates_prices(self) -> None:
        loop = _make_loop()
        loop.synthesizer.synthesize = MagicMock(return_value=SimpleNamespace(
            to_dict=lambda: {"active_signals": []},
        ))
        loop.run_cycle(_make_event())
        loop.price_feed.fetch_prices.assert_called_once()

    def test_updates_gas(self) -> None:
        loop = _make_loop()
        loop.synthesizer.synthesize = MagicMock(return_value=SimpleNamespace(
            to_dict=lambda: {"active_signals": []},
        ))
        loop.run_cycle(_make_event())
        loop.gas_monitor.update.assert_called_once()

    def test_skips_when_tx_failures_active(self) -> None:
        loop = _make_loop()
        loop.tx_failures.can_execute = MagicMock(return_value=False)
        orders = loop.run_cycle(_make_event())
        assert orders == []


# ---------------------------------------------------------------------------
# Deterministic fast-path
# ---------------------------------------------------------------------------

class TestDecide:

    def test_no_signals_returns_hold(self) -> None:
        loop = _make_loop()
        decision = loop._decide({"active_signals": []})
        assert decision.action == DecisionAction.HOLD

    def test_empty_signals_returns_hold(self) -> None:
        loop = _make_loop()
        decision = loop._decide({})
        assert decision.action == DecisionAction.HOLD

    def test_single_critical_signal_fast_path(self) -> None:
        loop = _make_loop()
        snapshot = {
            "active_signals": [{
                "type": "drawdown_alert",
                "urgency": "critical",
                "strategy_id": "STRAT-001",
                "parameters": {"chain": "ethereum"},
            }],
        }
        decision = loop._decide(snapshot)
        assert decision.action == DecisionAction.ADJUST
        assert decision.strategy == "STRAT-001"

    def test_budget_exhausted_returns_hold(self) -> None:
        loop = _make_loop()
        tracker = loop.decision_engine.cost_tracker
        tracker.cumulative_usd = Decimal("999")
        tracker.monthly_cap_usd = Decimal("0.01")
        # Prevent _maybe_reset from clearing cumulative_usd
        from datetime import UTC, datetime
        tracker._reset_month = datetime.now(UTC).month
        snapshot = {
            "active_signals": [
                {"type": "signal_a", "urgency": "low"},
                {"type": "signal_b", "urgency": "low"},
            ],
        }
        decision = loop._decide(snapshot)
        assert decision.action == DecisionAction.HOLD
        assert "budget" in decision.reasoning.lower()


# ---------------------------------------------------------------------------
# Risk gate
# ---------------------------------------------------------------------------

class TestRiskGate:

    def _make_decision(self, **kwargs: object) -> Decision:
        defaults = {
            "action": DecisionAction.ADJUST,
            "strategy": "STRAT-001",
            "reasoning": "test",
            "confidence": 0.9,
            "params": {
                "chain": "ethereum",
                "protocol": "aave_v3",
                "action": "supply",
                "tokenIn": "ETH",
                "amount": "1.0",
            },
        }
        defaults.update(kwargs)
        return Decision(**defaults)

    def test_approves_valid_decision(self) -> None:
        loop = _make_loop()
        decision = self._make_decision()
        orders = loop._apply_risk_gate(decision, "cid-1")
        assert len(orders) == 1
        assert loop._adjustment_made is True

    def test_blocks_second_adjustment(self) -> None:
        loop = _make_loop()
        loop._adjustment_made = True
        decision = self._make_decision()
        orders = loop._apply_risk_gate(decision, "cid-1")
        assert orders == []

    def test_blocks_when_gas_spike(self) -> None:
        loop = _make_loop()
        loop.gas_spike.is_operation_allowed = MagicMock(return_value=False)
        decision = self._make_decision()
        orders = loop._apply_risk_gate(decision, "cid-1")
        assert orders == []

    def test_blocks_when_drawdown_active(self) -> None:
        loop = _make_loop()
        loop.drawdown.can_open_position = MagicMock(return_value=False)
        decision = self._make_decision()
        orders = loop._apply_risk_gate(decision, "cid-1")
        assert orders == []

    def test_blocks_when_tx_failures_active(self) -> None:
        loop = _make_loop()
        loop.tx_failures.can_execute = MagicMock(return_value=False)
        decision = self._make_decision()
        orders = loop._apply_risk_gate(decision, "cid-1")
        assert orders == []

    def test_blocks_when_exposure_limit_exceeded(self) -> None:
        """RISK-008: Exposure limiter blocks orders that exceed limits."""
        loop = _make_loop()
        loop.tracker.query = MagicMock(return_value=[{
            "id": "pos1",
            "current_value": 3500,
            "protocol": "aave_v3",
            "asset": "ETH",
        }])
        loop.tracker.get_summary = MagicMock(return_value={"total_value": "10000"})
        decision = self._make_decision(params={
            "chain": "base",
            "protocol": "aave_v3",
            "action": "supply",
            "asset": "ETH",
            "value_usd": 1500,  # 3500 + 1500 = 5000 = 50% > 40%
        })
        orders = loop._apply_risk_gate(decision, "cid-1")
        assert orders == []

    def test_allows_when_exposure_within_limits(self) -> None:
        """RISK-008: Exposure limiter allows orders within limits."""
        loop = _make_loop()
        loop.tracker.query = MagicMock(return_value=[])
        loop.tracker.get_summary = MagicMock(return_value={"total_value": "10000"})
        decision = self._make_decision(params={
            "chain": "base",
            "protocol": "aave_v3",
            "action": "supply",
            "asset": "ETH",
            "value_usd": 2000,  # 20% < 40%
        })
        orders = loop._apply_risk_gate(decision, "cid-1")
        assert len(orders) == 1

    def test_exposure_skipped_without_order_details(self) -> None:
        """RISK-008: Orders without value_usd/asset skip exposure check."""
        loop = _make_loop()
        decision = self._make_decision(params={
            "chain": "base",
            "protocol": "aave_v3",
            "action": "supply",
        })
        orders = loop._apply_risk_gate(decision, "cid-1")
        assert len(orders) == 1

    def test_empty_parameters_produces_no_orders(self) -> None:
        loop = _make_loop()
        decision = self._make_decision(params={})
        orders = loop._apply_risk_gate(decision, "cid-1")
        assert orders == []


# ---------------------------------------------------------------------------
# Order generation
# ---------------------------------------------------------------------------

class TestDecisionToOrders:

    def test_generates_schema_compliant_order(self) -> None:
        loop = _make_loop()
        decision = Decision(
            action=DecisionAction.ADJUST,
            strategy="STRAT-001",
            reasoning="test",
            confidence=0.9,
            params={
                "chain": "ethereum",
                "protocol": "aave_v3",
                "action": "supply",
                "tokenIn": "ETH",
                "amount": "1.0",
            },
        )
        orders = loop._decision_to_orders(decision, "cid-1")
        assert len(orders) == 1
        order = orders[0]
        assert order["version"] == "1.0.0"
        assert "orderId" in order
        assert order["correlationId"] == "cid-1"
        assert order["chain"] == "ethereum"
        assert order["protocol"] == "aave_v3"
        assert order["strategy"] == "STRAT-001"

    def test_no_orders_without_parameters(self) -> None:
        loop = _make_loop()
        decision = Decision(
            action=DecisionAction.HOLD,
            strategy="system",
            reasoning="test",
            confidence=1.0,
        )
        orders = loop._decision_to_orders(decision, "cid-1")
        assert orders == []


# ---------------------------------------------------------------------------
# Execution result processing
# ---------------------------------------------------------------------------

class TestProcessResult:

    def test_confirmed_result(self) -> None:
        loop = _make_loop()
        loop.tx_failures.record_success = MagicMock()
        result = {
            "orderId": "order-1",
            "status": "confirmed",
            "position_id": "pos-1",
            "action": "open",
            "fill_price": "2000",
        }
        loop.process_result(result)
        loop.tx_failures.record_success.assert_called_once_with("order-1")

    def test_failed_result_records_failure(self) -> None:
        loop = _make_loop()
        loop.tx_failures.record_failure = MagicMock()
        result = {
            "orderId": "order-1",
            "status": "failed",
            "reason": "out_of_gas",
            "error": "insufficient gas",
        }
        loop.process_result(result)
        loop.tx_failures.record_failure.assert_called_once()


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------

class TestPersistState:

    def test_calls_state_save(self) -> None:
        loop = _make_loop()
        loop.persist_state()
        loop.state.save.assert_called_once()


# ---------------------------------------------------------------------------
# Signal handling
# ---------------------------------------------------------------------------

class TestSignalHandling:

    def test_sets_shutdown_flag(self) -> None:
        import main
        original = main._shutdown
        try:
            _handle_signal(15, None)
            assert main._shutdown is True
        finally:
            main._shutdown = original
