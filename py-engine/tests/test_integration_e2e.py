"""End-to-end integration tests — full lifecycle and Aave strategy evaluation cycle."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from monitoring.logger import correlation_context, get_correlation_id
from portfolio.position_tracker import PositionTracker
from strategies.aave_lending import (
    ALLOWED_PROTOCOL,
    AaveLendingStrategy,
)
from strategies.base import (
    GasInfo,
    MarketSnapshot,
    PoolState,
    SignalType,
)
from validation.schema_validator import validate

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 3, 8, 12, 0, 0, tzinfo=UTC)


def _pool(
    pool_id: str = "USDC",
    apy: float = 0.042,
    tvl: float = 5_000_000,
    protocol: str = ALLOWED_PROTOCOL,
) -> PoolState:
    return PoolState(
        protocol=protocol,
        pool_id=pool_id,
        tvl=tvl,
        apy=apy,
    )


def _gas(current: float = 0.05, avg_24h: float = 0.05) -> GasInfo:
    return GasInfo(current_gwei=current, avg_24h_gwei=avg_24h)


def _snapshot(
    pools: list[PoolState] | None = None,
    gas: GasInfo | None = None,
) -> MarketSnapshot:
    return MarketSnapshot(
        prices=[],
        gas=gas or _gas(),
        pools=pools or [_pool()],
        timestamp=_NOW,
    )


def _make_market_event(correlation_id: str) -> dict[str, Any]:
    """Build a schema-compliant market:events message (rate_change on Aave)."""
    return {
        "version": "1.0.0",
        "timestamp": datetime.now(UTC).isoformat(),
        "sequence": 1,
        "chain": "base",
        "eventType": "rate_change",
        "protocol": "aave_v3",
        "blockNumber": 12345678,
        "txHash": "0x" + "ab" * 32,
        "data": {
            "asset": "USDC",
            "newRate": "0.065",
            "correlationId": correlation_id,
        },
    }


def _make_execution_result(
    order: dict[str, Any],
    *,
    status: str = "confirmed",
    tx_hash: str | None = None,
) -> dict[str, Any]:
    """Build a schema-compliant execution:results message from an order."""
    return {
        "version": "1.0.0",
        "orderId": order["orderId"],
        "correlationId": order["correlationId"],
        "timestamp": datetime.now(UTC).isoformat(),
        "status": status,
        "txHash": tx_hash or ("0x" + "cd" * 32),
        "blockNumber": 12345680,
        "gasUsed": "250000",
        "effectiveGasPrice": "20000000000",
    }


class MockRedisManager:
    """Captures published messages and delivers to subscribers."""

    def __init__(self) -> None:
        self.published: list[tuple[str, dict[str, Any]]] = []

    def publish(self, channel: str, message: dict[str, Any]) -> None:
        self.published.append((channel, message))

    def get_messages(self, channel: str) -> list[dict[str, Any]]:
        return [msg for ch, msg in self.published if ch == channel]


# ---------------------------------------------------------------------------
# 1. End-to-end flow: detect → evaluate → report → (decision gate) → execute
# ---------------------------------------------------------------------------

class TestEndToEndFlow:
    """Full lifecycle: market event → strategy evaluation → report with signals →
    schema validation → execution result → position tracking → correlation ID."""

    def test_full_lifecycle_market_to_report(self) -> None:
        """Walk through the flow from market event to strategy report."""
        redis = MockRedisManager()
        strat = AaveLendingStrategy()
        correlation_id = uuid.uuid4().hex

        # --- Step 1: Market event arrives (rate_change on Aave) ---
        market_event = _make_market_event(correlation_id)
        valid, errors = validate("market-events", market_event)
        assert valid, f"market event schema invalid: {errors}"

        redis.publish("market:events", market_event)
        assert len(redis.get_messages("market:events")) == 1

        # --- Step 2: Strategy evaluates snapshot (high APY opportunity) ---
        pools = [_pool("USDC", apy=0.065), _pool("USDbC", apy=0.030)]
        report = strat.evaluate(_snapshot(pools=pools))
        assert report.strategy_id == "LEND-001"

        # Should have entry signal with recommendation
        entry_signals = [s for s in report.signals if s.type == SignalType.ENTRY_MET]
        assert len(entry_signals) == 1
        assert entry_signals[0].actionable is True
        assert report.recommendation is not None
        assert report.recommendation.action == "supply"

    def test_structured_logging_with_correlation_id(self) -> None:
        """Verify structured logger attaches correlation ID to log entries."""
        correlation_id = uuid.uuid4().hex

        with correlation_context(correlation_id):
            assert get_correlation_id() == correlation_id

            # Evaluate inside a correlation context
            strat = AaveLendingStrategy()
            pools = [_pool("USDC", apy=0.065)]
            report = strat.evaluate(_snapshot(pools=pools))
            assert report.strategy_id == "LEND-001"

        # After context exits, correlation ID is cleared
        assert get_correlation_id() is None

    def test_multiple_channels_message_flow(self) -> None:
        """Verify messages flow through correct Redis channels."""
        redis = MockRedisManager()
        cid = uuid.uuid4().hex

        # Market event on market:events
        redis.publish("market:events", _make_market_event(cid))

        # Strategy produces report (not orders directly)
        strat = AaveLendingStrategy()
        report = strat.evaluate(_snapshot(pools=[_pool("USDC", apy=0.065)]))
        assert report.recommendation is not None

        # Simulate decision gate creating an order from the report
        order = {
            "version": "1.0.0",
            "orderId": uuid.uuid4().hex,
            "correlationId": cid,
            "timestamp": datetime.now(UTC).isoformat(),
            "chain": "base",
            "protocol": "aave_v3",
            "action": "supply",
            "strategy": "LEND-001",
            "priority": "normal",
            "params": {"tokenIn": "USDC", "amount": "5000"},
            "limits": {
                "maxGasWei": "500000000000000",
                "maxSlippageBps": 50,
                "deadlineUnix": 9999999999,
            },
        }
        valid, errors = validate("execution-orders", order)
        assert valid, f"order schema invalid: {errors}"
        redis.publish("execution:orders", order)

        # Result on execution:results
        result = _make_execution_result(order)
        valid, errors = validate("execution-results", result)
        assert valid, f"result schema invalid: {errors}"
        redis.publish("execution:results", result)

        assert len(redis.get_messages("market:events")) == 1
        assert len(redis.get_messages("execution:orders")) == 1
        assert len(redis.get_messages("execution:results")) == 1


# ---------------------------------------------------------------------------
# 2. Aave strategy evaluation cycle
# ---------------------------------------------------------------------------

class TestAaveEvaluationCycle:
    """Aave strategy evaluation under different market conditions."""

    def test_entry_signal_on_best_market(self) -> None:
        """Strategy identifies best market and produces entry signal."""
        strat = AaveLendingStrategy()
        pools = [_pool("USDbC", apy=0.030), _pool("USDC", apy=0.042)]
        report = strat.evaluate(_snapshot(pools=pools))

        entry_signals = [s for s in report.signals if s.type == SignalType.ENTRY_MET]
        assert len(entry_signals) == 1
        assert report.recommendation is not None
        assert report.recommendation.action == "supply"
        assert "USDC" in report.recommendation.parameters.get("pool_id", "")

    def test_exit_signal_on_low_apy(self) -> None:
        """Exit signal when best APY drops below 1.0% floor."""
        strat = AaveLendingStrategy()
        pools = [_pool("USDC", apy=0.005, tvl=5_000_000)]
        report = strat.evaluate(_snapshot(pools=pools))

        exit_signals = [s for s in report.signals if s.type == SignalType.EXIT_MET]
        assert len(exit_signals) == 1
        assert exit_signals[0].actionable is True
        assert report.recommendation is not None
        assert report.recommendation.action == "withdraw"

    def test_no_actionable_signal_on_marginal_apy(self) -> None:
        """No entry signal when APY is too low and pool filtered by low TVL."""
        strat = AaveLendingStrategy()
        # Low TVL filters it out, so no eligible pools → no signals
        pools = [_pool("USDC", apy=0.003, tvl=500_000)]
        report = strat.evaluate(_snapshot(pools=pools))

        entry_signals = [s for s in report.signals if s.type == SignalType.ENTRY_MET]
        assert len(entry_signals) == 0
        assert report.recommendation is None

    def test_gas_spike_blocks_entry(self) -> None:
        """Gas spike prevents entry signal even with good APY."""
        strat = AaveLendingStrategy()
        pools = [_pool("USDC", apy=0.05, tvl=5_000_000)]
        gas = _gas(current=0.20, avg_24h=0.05)
        report = strat.evaluate(_snapshot(pools=pools, gas=gas))

        entry_signals = [s for s in report.signals if s.type == SignalType.ENTRY_MET]
        assert len(entry_signals) == 0

    def test_position_tracker_execution_result_handler(self) -> None:
        """PositionTracker.on_execution_result correctly handles confirmed/failed."""
        tracker = PositionTracker()

        # Open a position, then simulate confirmed close via execution result
        tracker.open_position(
            strategy="LEND-001",
            protocol="aave",
            chain="base",
            asset="USDC",
            entry_price="1",
            amount="5000",
            position_id="pos-1",
        )

        # Confirmed close
        tracker.on_execution_result({
            "position_id": "pos-1",
            "status": "confirmed",
            "action": "close",
            "fill_price": "1.01",
        })
        assert tracker.get_position("pos-1") is None
        summary = tracker.get_summary()
        assert summary["closed_count"] == 1
        assert Decimal(summary["total_realized_pnl"]) == Decimal("50")

        # Open another, test failed does not close
        tracker.open_position(
            strategy="LEND-001",
            protocol="aave",
            chain="base",
            asset="USDbC",
            entry_price="1",
            amount="3000",
            position_id="pos-2",
        )
        tracker.on_execution_result({
            "position_id": "pos-2",
            "status": "failed",
            "action": "close",
            "reason": "reverted",
        })
        assert tracker.get_position("pos-2") is not None
