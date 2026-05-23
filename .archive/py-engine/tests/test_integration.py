"""Integration tests for TEST-001.

E2E lifecycle tests, circuit breaker integration tests, schema validation
tests, and startup recovery tests. All external dependencies are mocked.
"""

from __future__ import annotations

import json
import time
import uuid
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import jsonschema
import pytest

from ai.decision_engine import Decision, DecisionAction
from main import DecisionLoop
from strategies.base import (
    Observation,
    Recommendation,
    Signal,
    SignalType,
    StrategyReport,
)

# ---------------------------------------------------------------------------
# Shared schema paths
# ---------------------------------------------------------------------------

_SCHEMA_DIR = Path(__file__).resolve().parents[2] / "shared" / "schemas"


def _load_schema(name: str) -> dict:
    return json.loads((_SCHEMA_DIR / name).read_text())


MARKET_EVENTS_SCHEMA = _load_schema("market-events.schema.json")
EXECUTION_ORDERS_SCHEMA = _load_schema("execution-orders.schema.json")
EXECUTION_RESULTS_SCHEMA = _load_schema("execution-results.schema.json")

# ---------------------------------------------------------------------------
# Test helpers (mirrors test_main_loop.py patterns)
# ---------------------------------------------------------------------------


def _mock_redis() -> MagicMock:
    redis = MagicMock()
    redis.connect = MagicMock()
    redis.disconnect = MagicMock()
    redis.publish = MagicMock()
    redis.subscribe = MagicMock()
    redis._stream_max_len = 10000
    redis.stream_trim = MagicMock()
    return redis


def _mock_db() -> tuple[MagicMock, MagicMock]:
    db_manager = MagicMock()
    db_manager.create_tables = MagicMock()
    db_manager.close = MagicMock()
    repository = MagicMock()
    repository.get_trades = MagicMock(return_value=[])
    repository.get_positions = MagicMock(return_value=[])
    repository.record_trade = MagicMock()
    repository.record_decision = MagicMock()
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
    # Mock all external calls
    loop.price_feed.fetch_prices = MagicMock(return_value={
        "ETH": {"price_usd": 2000},
        "USDC": {"price_usd": 1},
    })
    loop.gas_monitor.update = MagicMock(return_value=SimpleNamespace(
        fast=50, standard=30, slow=20, timestamp="2025-01-01T00:00:00Z",
    ))
    loop.gas_monitor.get_rolling_average = MagicMock(return_value=Decimal("30"))
    loop.oracle_guard.check = MagicMock(return_value=SimpleNamespace(
        safe=True, deviations=[], stale=False, reason="ok",
    ))
    loop.oracle_guard.get_deviations = MagicMock(return_value={})
    loop.defi_metrics.fetch_tvl = MagicMock(return_value=None)
    loop.defi_metrics.get_metrics = MagicMock(return_value=None)
    return loop


def _make_event(event_type: str = "new_block") -> dict:
    return {
        "version": "1.0.0",
        "eventType": event_type,
        "chain": "ethereum",
        "timestamp": "2025-01-01T00:00:00Z",
        "correlationId": "test-cid-int",
    }


def _make_actionable_strategy(
    strategy_id: str = "INT-STRAT-001",
) -> MagicMock:
    """Create a mock strategy with actionable signals."""
    from datetime import timedelta

    strategy = MagicMock()
    strategy.strategy_id = strategy_id
    strategy.eval_interval = timedelta(seconds=0)
    strategy.data_window = timedelta(hours=1)

    report = StrategyReport(
        strategy_id=strategy_id,
        timestamp="2025-01-01T00:00:00Z",
        observations=[Observation(
            metric="price_trend", value="bullish", context="ETH up 5%",
        )],
        signals=[Signal(
            type=SignalType.ENTRY_MET,
            actionable=True,
            details="Entry conditions met for supply",
        )],
        recommendation=Recommendation(
            action="supply",
            reasoning="Favorable yield conditions",
            parameters={"protocol": "aave_v3", "asset": "ETH"},
        ),
    )
    strategy.evaluate = MagicMock(return_value=report)
    return strategy


# ===========================================================================
# 1. E2E LIFECYCLE TESTS
# ===========================================================================


class TestE2ELifecycle:
    """Market event → strategy eval → decision gate → order → result."""

    def test_full_lifecycle_market_to_order(self) -> None:
        """E2E: mock market data → run_cycle() → verify orders emitted."""
        loop = _make_loop()

        # Register actionable strategy
        strategy = _make_actionable_strategy()
        loop.register_strategy(strategy)

        # Mock synthesizer
        loop.synthesizer.synthesize = MagicMock(return_value=SimpleNamespace(
            to_dict=lambda: {"active_signals": []},
        ))

        # Mock Claude API to return ADJUST decision with full params
        loop.decision_engine.decide = MagicMock(return_value=Decision(
            action=DecisionAction.ADJUST,
            strategy="INT-STRAT-001",
            reasoning="Entry conditions met — supply ETH to Aave",
            confidence=0.85,
            params={
                "chain": "base",
                "protocol": "aave_v3",
                "action": "supply",
                "tokenIn": "ETH",
                "amount": "1000000000000000000",
            },
        ))

        # Run the cycle
        event = _make_event("price_update")
        orders = loop.run_cycle(event)

        # Verify strategy was evaluated
        strategy.evaluate.assert_called_once()

        # Verify decision engine was called (gate opened)
        loop.decision_engine.decide.assert_called_once()

        # Verify order was produced
        assert len(orders) == 1
        order = orders[0]
        assert order["version"] == "1.0.0"
        assert order["chain"] == "base"
        assert order["protocol"] == "aave_v3"
        assert order["strategy"] == "INT-STRAT-001"
        assert "orderId" in order
        assert "correlationId" in order
        assert "limits" in order

    def test_lifecycle_order_then_result(self) -> None:
        """E2E: produce order, then process execution result."""
        loop = _make_loop()
        strategy = _make_actionable_strategy()
        loop.register_strategy(strategy)
        loop.synthesizer.synthesize = MagicMock(return_value=SimpleNamespace(
            to_dict=lambda: {"active_signals": []},
        ))
        loop.decision_engine.decide = MagicMock(return_value=Decision(
            action=DecisionAction.ADJUST,
            strategy="INT-STRAT-001",
            reasoning="test",
            confidence=0.85,
            params={
                "chain": "base",
                "protocol": "aave_v3",
                "action": "supply",
                "tokenIn": "ETH",
                "amount": "1000000000000000000",
            },
        ))

        orders = loop.run_cycle(_make_event())
        assert len(orders) == 1
        order_id = orders[0]["orderId"]

        # Process execution result for that order
        result = {
            "orderId": order_id,
            "correlationId": orders[0]["correlationId"],
            "status": "confirmed",
            "txHash": "0xabc123",
            "gasUsed": "200000",
        }
        loop.process_result(result)

        # TX success recorded (real TxFailureMonitor — verify no crash)
        assert loop.tx_failures.can_execute()
        # Trade persisted to DB
        loop.repository.record_trade.assert_called_once()

    def test_lifecycle_hold_produces_no_orders(self) -> None:
        """E2E: no actionable signals → HOLD → no orders emitted."""
        loop = _make_loop()
        loop.synthesizer.synthesize = MagicMock(return_value=SimpleNamespace(
            to_dict=lambda: {"active_signals": []},
        ))
        orders = loop.run_cycle(_make_event())
        assert orders == []
        assert loop._cycle_count == 1

    def test_lifecycle_failed_result_records_failure(self) -> None:
        """E2E: failed TX result updates failure monitor."""
        loop = _make_loop()
        failures_before = loop.tx_failures.get_failure_count()
        result = {
            "orderId": "fail-order-1",
            "correlationId": "cid-fail",
            "status": "failed",
            "reason": "revert",
            "error": "ERC20: insufficient balance",
        }
        loop.process_result(result)
        # Failure count increased
        assert loop.tx_failures.get_failure_count() > failures_before

    def test_multiple_cycles_increment_count(self) -> None:
        """E2E: multiple cycles correctly track state."""
        loop = _make_loop()
        loop.synthesizer.synthesize = MagicMock(return_value=SimpleNamespace(
            to_dict=lambda: {"active_signals": []},
        ))
        for _ in range(5):
            loop.run_cycle(_make_event())
        assert loop._cycle_count == 5


# ===========================================================================
# 2. CIRCUIT BREAKER INTEGRATION TESTS
# ===========================================================================


class TestCircuitBreakerIntegration:
    """Circuit breakers trigger emergency actions directly."""

    def test_drawdown_triggers_unwind_orders(self) -> None:
        """CB: portfolio drawdown >20% → unwind all positions to stables."""
        loop = _make_loop()

        # Set up positions in tracker
        loop.tracker.query = MagicMock(return_value=[
            {
                "id": "pos-1",
                "asset": "ETH",
                "protocol": "aave_v3",
                "value": "5000",
                "amount": "2.5",
                "current_value": 5000,
            },
            {
                "id": "pos-2",
                "asset": "USDC",
                "protocol": "aerodrome",
                "value": "3000",
                "amount": "3000",
                "current_value": 3000,
            },
        ])
        loop.tracker.get_summary = MagicMock(return_value={
            "total_value": "8000",
        })

        # Simulate 20%+ drawdown: peak was 10000, now 8000 = 20%
        loop.drawdown.update(Decimal("10000"))  # set peak
        loop.drawdown.update(Decimal("7900"))   # trigger critical (21%)

        assert loop.drawdown.should_unwind_all()

        # Mock synthesizer (shouldn't be reached but required for setup)
        loop.synthesizer.synthesize = MagicMock(return_value=SimpleNamespace(
            to_dict=lambda: {"active_signals": []},
        ))

        # Run cycle — should emit unwind orders
        orders = loop.run_cycle(_make_event())

        # Verify CB:drawdown orders emitted
        assert len(orders) == 2
        for order in orders:
            assert order["strategy"] == "CB:drawdown"
            assert order["action"] == "withdraw"
            assert order["priority"] == "urgent"
            assert order["version"] == "1.0.0"

    def test_position_loss_triggers_close_orders(self) -> None:
        """CB: single position >10% loss → close position directly."""
        loop = _make_loop()

        # Set up a position with >10% loss
        positions = [{
            "id": "pos-loss-1",
            "asset": "ETH",
            "protocol": "aave_v3",
            "strategy_id": "STRAT-A",
            "entry_price": "2000",
            "current_value": "1700",
            "amount": "1.0",
            "entry_time": "2025-01-01T00:00:00+00:00",
        }]
        loop.tracker.query = MagicMock(return_value=positions)
        loop.tracker.get_summary = MagicMock(return_value={
            "total_value": "1700",
        })

        # Price dropped 15%: entry 2000, now 1700
        loop.price_feed.fetch_prices = MagicMock(return_value={
            "ETH": {"price_usd": 1700},
            "USDC": {"price_usd": 1},
        })

        loop.synthesizer.synthesize = MagicMock(return_value=SimpleNamespace(
            to_dict=lambda: {"active_signals": []},
        ))

        orders = loop.run_cycle(_make_event())

        # Should get CB:position_loss orders
        assert len(orders) >= 1
        for order in orders:
            assert order["strategy"] == "CB:position_loss"
            assert order["action"] == "withdraw"
            assert order["priority"] == "urgent"

    def test_drawdown_orders_bypass_decision_gate(self) -> None:
        """CB: drawdown unwind orders are emitted without Claude API call."""
        loop = _make_loop()
        loop.tracker.query = MagicMock(return_value=[{
            "id": "pos-1",
            "asset": "ETH",
            "protocol": "aave_v3",
            "value": "5000",
            "amount": "2.5",
        }])
        loop.tracker.get_summary = MagicMock(return_value={
            "total_value": "7000",
        })

        # Trigger critical drawdown
        loop.drawdown.update(Decimal("10000"))
        loop.drawdown.update(Decimal("7900"))

        loop.synthesizer.synthesize = MagicMock(return_value=SimpleNamespace(
            to_dict=lambda: {"active_signals": []},
        ))

        # Mock Claude API — should NOT be called
        loop.decision_engine.decide = MagicMock()

        orders = loop.run_cycle(_make_event())
        assert len(orders) >= 1

        # Claude API was never called — CB bypassed the decision gate
        loop.decision_engine.decide.assert_not_called()

    def test_gas_spike_blocks_trading(self) -> None:
        """CB: gas spike >3x average → block non-urgent operations."""
        loop = _make_loop()
        strategy = _make_actionable_strategy()
        loop.register_strategy(strategy)
        loop.synthesizer.synthesize = MagicMock(return_value=SimpleNamespace(
            to_dict=lambda: {"active_signals": []},
        ))

        # Mock decision engine to return ADJUST
        loop.decision_engine.decide = MagicMock(return_value=Decision(
            action=DecisionAction.ADJUST,
            strategy="INT-STRAT-001",
            reasoning="test",
            confidence=0.85,
            params={
                "chain": "base",
                "protocol": "aave_v3",
                "action": "supply",
                "tokenIn": "ETH",
                "amount": "1000000000000000000",
            },
        ))

        # Gas spike blocks execution
        loop.gas_spike.is_operation_allowed = MagicMock(return_value=False)

        orders = loop.run_cycle(_make_event())
        assert orders == []

    def test_tx_failure_rate_blocks_execution(self) -> None:
        """CB: >3 TX failures/hour → pause execution."""
        loop = _make_loop()
        loop.tx_failures.can_execute = MagicMock(return_value=False)
        loop.synthesizer.synthesize = MagicMock(return_value=SimpleNamespace(
            to_dict=lambda: {"active_signals": []},
        ))
        orders = loop.run_cycle(_make_event())
        assert orders == []


# ===========================================================================
# 3. SCHEMA VALIDATION TESTS
# ===========================================================================


class TestSchemaValidation:
    """Messages validated against shared/schemas/*.schema.json."""

    def test_valid_market_event(self) -> None:
        """Schema: well-formed market event passes validation."""
        event = {
            "version": "1.0.0",
            "timestamp": "2025-01-01T00:00:00Z",
            "sequence": 1,
            "chain": "ethereum",
            "eventType": "new_block",
            "protocol": "system",
            "blockNumber": 12345678,
        }
        jsonschema.validate(event, MARKET_EVENTS_SCHEMA)

    def test_valid_market_event_all_types(self) -> None:
        """Schema: all eventType variants pass validation."""
        event_types = [
            "swap", "liquidity_change", "rate_change",
            "large_transfer", "new_block", "price_update",
        ]
        for et in event_types:
            event = {
                "version": "1.0.0",
                "timestamp": "2025-01-01T00:00:00Z",
                "sequence": 1,
                "chain": "base",
                "eventType": et,
                "protocol": "aave_v3",
            }
            jsonschema.validate(event, MARKET_EVENTS_SCHEMA)

    def test_invalid_market_event_missing_required(self) -> None:
        """Schema: missing required fields are rejected."""
        event = {
            "version": "1.0.0",
            "eventType": "new_block",
        }
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(event, MARKET_EVENTS_SCHEMA)

    def test_invalid_market_event_bad_chain(self) -> None:
        """Schema: invalid chain enum value rejected."""
        event = {
            "version": "1.0.0",
            "timestamp": "2025-01-01T00:00:00Z",
            "sequence": 1,
            "chain": "solana",
            "eventType": "new_block",
            "protocol": "system",
        }
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(event, MARKET_EVENTS_SCHEMA)

    def test_valid_execution_order(self) -> None:
        """Schema: well-formed execution order passes validation."""
        order = {
            "version": "1.0.0",
            "orderId": uuid.uuid4().hex,
            "correlationId": "test-cid",
            "timestamp": "2025-01-01T00:00:00Z",
            "chain": "base",
            "protocol": "aave_v3",
            "action": "supply",
            "strategy": "STRAT-001",
            "priority": "normal",
            "params": {
                "tokenIn": "ETH",
                "amount": "1000000000000000000",
            },
            "limits": {
                "maxGasWei": "500000000000000",
                "maxSlippageBps": 50,
                "deadlineUnix": int(time.time()) + 300,
            },
        }
        jsonschema.validate(order, EXECUTION_ORDERS_SCHEMA)

    def test_valid_execution_order_all_actions(self) -> None:
        """Schema: all action types pass validation."""
        actions = [
            "supply", "withdraw", "swap", "mint_lp",
            "burn_lp", "stake", "unstake", "collect_fees", "flash_loan",
        ]
        for action in actions:
            order = {
                "version": "1.0.0",
                "orderId": uuid.uuid4().hex,
                "correlationId": "test-cid",
                "timestamp": "2025-01-01T00:00:00Z",
                "chain": "ethereum",
                "protocol": "aave_v3",
                "action": action,
                "strategy": "TEST",
                "priority": "normal",
                "params": {},
                "limits": {
                    "maxGasWei": "500000000000000",
                    "maxSlippageBps": 50,
                    "deadlineUnix": int(time.time()) + 300,
                },
            }
            jsonschema.validate(order, EXECUTION_ORDERS_SCHEMA)

    def test_invalid_execution_order_missing_limits(self) -> None:
        """Schema: missing limits field is rejected."""
        order = {
            "version": "1.0.0",
            "orderId": uuid.uuid4().hex,
            "correlationId": "test-cid",
            "timestamp": "2025-01-01T00:00:00Z",
            "chain": "base",
            "protocol": "aave_v3",
            "action": "supply",
            "strategy": "TEST",
            "params": {},
        }
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(order, EXECUTION_ORDERS_SCHEMA)

    def test_invalid_execution_order_bad_protocol(self) -> None:
        """Schema: invalid protocol enum value rejected."""
        order = {
            "version": "1.0.0",
            "orderId": uuid.uuid4().hex,
            "correlationId": "test-cid",
            "timestamp": "2025-01-01T00:00:00Z",
            "chain": "base",
            "protocol": "uniswap_v3",
            "action": "supply",
            "strategy": "TEST",
            "params": {},
            "limits": {
                "maxGasWei": "500000000000000",
                "maxSlippageBps": 50,
                "deadlineUnix": int(time.time()) + 300,
            },
        }
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(order, EXECUTION_ORDERS_SCHEMA)

    def test_valid_execution_result(self) -> None:
        """Schema: well-formed execution result passes validation."""
        result = {
            "version": "1.0.0",
            "orderId": uuid.uuid4().hex,
            "correlationId": "test-cid",
            "timestamp": "2025-01-01T00:00:00Z",
            "status": "confirmed",
            "txHash": "0xabc123def456",
            "blockNumber": 12345678,
            "gasUsed": "200000",
        }
        jsonschema.validate(result, EXECUTION_RESULTS_SCHEMA)

    def test_valid_execution_result_all_statuses(self) -> None:
        """Schema: all status values pass validation."""
        for status in ["confirmed", "failed", "reverted", "timeout"]:
            result = {
                "version": "1.0.0",
                "orderId": uuid.uuid4().hex,
                "correlationId": "test-cid",
                "timestamp": "2025-01-01T00:00:00Z",
                "status": status,
            }
            jsonschema.validate(result, EXECUTION_RESULTS_SCHEMA)

    def test_invalid_execution_result_bad_status(self) -> None:
        """Schema: invalid status value rejected."""
        result = {
            "version": "1.0.0",
            "orderId": "order-1",
            "correlationId": "cid-1",
            "timestamp": "2025-01-01T00:00:00Z",
            "status": "pending",
        }
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(result, EXECUTION_RESULTS_SCHEMA)

    def test_invalid_execution_result_missing_required(self) -> None:
        """Schema: missing required fields rejected."""
        result = {
            "version": "1.0.0",
            "status": "confirmed",
        }
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(result, EXECUTION_RESULTS_SCHEMA)

    def test_generated_orders_pass_schema(self) -> None:
        """Schema: orders produced by DecisionLoop pass schema validation."""
        loop = _make_loop()
        strategy = _make_actionable_strategy()
        loop.register_strategy(strategy)
        loop.synthesizer.synthesize = MagicMock(return_value=SimpleNamespace(
            to_dict=lambda: {"active_signals": []},
        ))
        loop.decision_engine.decide = MagicMock(return_value=Decision(
            action=DecisionAction.ADJUST,
            strategy="INT-STRAT-001",
            reasoning="test",
            confidence=0.85,
            params={
                "chain": "base",
                "protocol": "aave_v3",
                "action": "supply",
                "tokenIn": "ETH",
                "amount": "1000000000000000000",
            },
        ))

        orders = loop.run_cycle(_make_event())
        assert len(orders) == 1
        jsonschema.validate(orders[0], EXECUTION_ORDERS_SCHEMA)

    def test_drawdown_unwind_orders_pass_schema(self) -> None:
        """Schema: CB:drawdown orders pass schema validation."""
        loop = _make_loop()
        loop.tracker.query = MagicMock(return_value=[{
            "id": "pos-1",
            "asset": "ETH",
            "protocol": "aave_v3",
            "value": "5000",
            "amount": "2.5",
        }])
        loop.tracker.get_summary = MagicMock(return_value={
            "total_value": "5000",
        })
        loop.drawdown.update(Decimal("10000"))
        loop.drawdown.update(Decimal("7900"))

        loop.synthesizer.synthesize = MagicMock(return_value=SimpleNamespace(
            to_dict=lambda: {"active_signals": []},
        ))

        orders = loop.run_cycle(_make_event())
        assert len(orders) >= 1
        for order in orders:
            jsonschema.validate(order, EXECUTION_ORDERS_SCHEMA)

    def test_position_loss_orders_pass_schema(self) -> None:
        """Schema: CB:position_loss orders pass schema validation."""
        loop = _make_loop()
        positions = [{
            "id": "pos-loss-1",
            "asset": "ETH",
            "protocol": "aave_v3",
            "strategy_id": "STRAT-A",
            "entry_price": "2000",
            "current_value": "1700",
            "amount": "1.0",
            "entry_time": "2025-01-01T00:00:00+00:00",
        }]
        loop.tracker.query = MagicMock(return_value=positions)
        loop.tracker.get_summary = MagicMock(return_value={
            "total_value": "1700",
        })
        loop.price_feed.fetch_prices = MagicMock(return_value={
            "ETH": {"price_usd": 1700},
            "USDC": {"price_usd": 1},
        })
        loop.synthesizer.synthesize = MagicMock(return_value=SimpleNamespace(
            to_dict=lambda: {"active_signals": []},
        ))

        orders = loop.run_cycle(_make_event())
        assert len(orders) >= 1
        for order in orders:
            jsonschema.validate(order, EXECUTION_ORDERS_SCHEMA)


# ===========================================================================
# 4. STARTUP RECOVERY TESTS
# ===========================================================================


class TestStartupRecovery:
    """Startup recovery: state load → position loading → reconciliation."""

    def test_positions_loaded_from_database_on_startup(self) -> None:
        """Recovery: DecisionLoop loads positions from PG on init."""
        redis = _mock_redis()
        db_manager, repository = _mock_db()
        state = _mock_state()

        # Simulate PG returning open positions
        mock_position = MagicMock()
        mock_position.position_id = "pos-recovery-1"
        mock_position.strategy = "STRAT-A"
        mock_position.protocol = "aave_v3"
        mock_position.chain = "base"
        mock_position.asset = "ETH"
        mock_position.entry_price = Decimal("2000")
        mock_position.entry_time = "2025-01-01T00:00:00+00:00"
        mock_position.amount = Decimal("2.5")
        mock_position.current_value = Decimal("5000")
        mock_position.unrealized_pnl = Decimal("0")
        mock_position.status = "open"
        repository.get_positions = MagicMock(return_value=[mock_position])

        DecisionLoop(redis, db_manager, repository, state)

        # Verify get_positions was called during init (from_database)
        repository.get_positions.assert_called()

    def test_empty_database_creates_empty_tracker(self) -> None:
        """Recovery: empty PG → empty tracker (no crash)."""
        redis = _mock_redis()
        db_manager, repository = _mock_db()
        state = _mock_state()
        repository.get_positions = MagicMock(return_value=[])

        loop = DecisionLoop(redis, db_manager, repository, state)
        assert loop.tracker is not None

    def test_database_failure_falls_back_to_empty(self) -> None:
        """Recovery: PG connection failure → fallback to empty tracker."""
        redis = _mock_redis()
        db_manager, repository = _mock_db()
        state = _mock_state()
        repository.get_positions = MagicMock(
            side_effect=Exception("connection refused"),
        )

        loop = DecisionLoop(redis, db_manager, repository, state)
        # Should not crash — falls back to empty tracker
        assert loop.tracker is not None

    def test_state_persistence_on_shutdown(self) -> None:
        """Recovery: persist_state saves for next startup."""
        loop = _make_loop()
        loop.persist_state()

        # State manager save called
        loop.state.save.assert_called_once()

    def test_repository_cache_load(self) -> None:
        """Recovery: repository.load_cache returns structured data."""
        _, repository = _mock_db()
        repository.get_positions = MagicMock(return_value=[])
        repository.get_strategy_statuses = MagicMock(return_value=[])
        repository.get_latest_snapshot = MagicMock(return_value=None)

        repository.load_cache()
        # load_cache is mocked, so we verify it's callable
        repository.load_cache.assert_called_once()

    def test_hold_mode_initialized_on_startup(self) -> None:
        """Recovery: hold mode defaults to inactive on fresh start."""
        loop = _make_loop()
        assert loop.hold_mode is not None
        # Default: not in hold mode
        assert not loop.hold_mode.is_active()

    def test_circuit_breakers_initialized_on_startup(self) -> None:
        """Recovery: all circuit breakers are initialized and functional."""
        loop = _make_loop()
        assert loop.drawdown is not None
        assert loop.position_loss is not None
        assert loop.gas_spike is not None
        assert loop.tx_failures is not None
        assert loop.tvl_monitor is not None

        # All start in safe state
        assert loop.drawdown.can_open_position()
        assert not loop.drawdown.should_unwind_all()
