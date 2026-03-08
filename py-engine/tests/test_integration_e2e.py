"""End-to-end integration tests — full lifecycle and Aave supply/withdraw cycle."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from monitoring.logger import correlation_context, get_correlation_id
from portfolio.allocator import PortfolioAllocator
from portfolio.position_tracker import PositionTracker
from strategies.aave_lending import (
    AaveLendingConfig,
    AaveLendingStrategy,
    AaveMarket,
)
from validation.schema_validator import validate

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_market(
    asset: str = "USDC",
    supply_apy: str = "0.035",
    available_liquidity: str = "1000000",
    utilization_rate: str = "0.80",
    chain: str = "base",
) -> AaveMarket:
    return AaveMarket(
        asset=asset,
        supply_apy=Decimal(supply_apy),
        available_liquidity=Decimal(available_liquidity),
        utilization_rate=Decimal(utilization_rate),
        chain=chain,
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


def _make_strategy(
    total_capital: str = "10000",
    config: AaveLendingConfig | None = None,
) -> tuple[AaveLendingStrategy, PortfolioAllocator, PositionTracker]:
    alloc = PortfolioAllocator(
        Decimal(total_capital),
    )
    tracker = PositionTracker()
    strat = AaveLendingStrategy(alloc, tracker, config)
    return strat, alloc, tracker


class MockRedisManager:
    """Captures published messages and delivers to subscribers."""

    def __init__(self) -> None:
        self.published: list[tuple[str, dict[str, Any]]] = []

    def publish(self, channel: str, message: dict[str, Any]) -> None:
        self.published.append((channel, message))

    def get_messages(self, channel: str) -> list[dict[str, Any]]:
        return [msg for ch, msg in self.published if ch == channel]


# ---------------------------------------------------------------------------
# 1. End-to-end flow: detect → evaluate → approve → execute → confirm → log
# ---------------------------------------------------------------------------

class TestEndToEndFlow:
    """Full lifecycle: market event → strategy evaluation → order generation →
    schema validation → execution result → position tracking → correlation ID."""

    def test_full_lifecycle_market_to_position(self) -> None:
        """Walk through the entire flow from market event to confirmed position."""
        redis = MockRedisManager()
        strat, alloc, tracker = _make_strategy(total_capital="20000")
        correlation_id = uuid.uuid4().hex

        # --- Step 1: Market event arrives (rate_change on Aave) ---
        market_event = _make_market_event(correlation_id)
        valid, errors = validate("market-events", market_event)
        assert valid, f"market event schema invalid: {errors}"

        # Simulate publishing on market:events
        redis.publish("market:events", market_event)
        assert len(redis.get_messages("market:events")) == 1

        # --- Step 2: Strategy evaluates markets (high APY opportunity) ---
        markets = [
            _make_market("USDC", "0.065"),  # high APY from the event
            _make_market("USDbC", "0.030"),
        ]
        ranked = strat.evaluate(markets)
        assert ranked[0].asset == "USDC"
        assert ranked[0].supply_apy == Decimal("0.065")

        # --- Step 3: Strategy generates order ---
        orders = strat.generate_orders(markets, correlation_id=correlation_id)
        assert len(orders) == 1
        order = orders[0]
        assert order["action"] == "supply"
        assert order["params"]["tokenIn"] == "USDC"
        assert order["correlationId"] == correlation_id

        # --- Step 4: Validate order against schema ---
        valid, errors = validate("execution-orders", order)
        assert valid, f"execution order schema invalid: {errors}"

        # Publish order
        redis.publish("execution:orders", order)
        assert len(redis.get_messages("execution:orders")) == 1

        # --- Step 5: Simulate TS executor confirming the order ---
        result = _make_execution_result(order, status="confirmed")
        valid, errors = validate("execution-results", result)
        assert valid, f"execution result schema invalid: {errors}"

        redis.publish("execution:results", result)
        assert len(redis.get_messages("execution:results")) == 1

        # --- Step 6: Position tracker records the new position ---
        pos = tracker.open_position(
            strategy="LEND-001",
            protocol="aave",
            chain="base",
            asset="USDC",
            entry_price="1",
            amount=order["params"]["amount"],
            protocol_data={"current_apy": "0.065", "correlation_id": correlation_id},
        )
        positions = tracker.query(strategy="LEND-001", protocol="aave")
        assert len(positions) == 1
        assert positions[0].asset == "USDC"
        assert positions[0].status == "open"

        # --- Step 7: Verify correlation ID links the full lifecycle ---
        assert market_event["data"]["correlationId"] == correlation_id
        assert order["correlationId"] == correlation_id
        assert result["correlationId"] == correlation_id
        assert pos.protocol_data["correlation_id"] == correlation_id

    def test_structured_logging_with_correlation_id(self) -> None:
        """Verify structured logger attaches correlation ID to log entries."""
        log_output: list[str] = []
        handler = logging.Handler()
        handler.emit = lambda record: log_output.append(
            record.getMessage()
        )

        correlation_id = uuid.uuid4().hex

        with correlation_context(correlation_id):
            assert get_correlation_id() == correlation_id

            # Generate orders inside a correlation context
            strat, _, _ = _make_strategy()
            markets = [_make_market("USDC", "0.065")]
            orders = strat.generate_orders(markets, correlation_id=correlation_id)
            assert len(orders) == 1
            assert orders[0]["correlationId"] == correlation_id

        # After context exits, correlation ID is cleared
        assert get_correlation_id() is None

    def test_failed_execution_does_not_create_position(self) -> None:
        """When TS executor reports failure, no position should be opened."""
        redis = MockRedisManager()
        strat, _, tracker = _make_strategy()

        markets = [_make_market("USDC", "0.065")]
        orders = strat.generate_orders(markets)
        assert len(orders) == 1

        # Simulate failed execution
        result = _make_execution_result(orders[0], status="failed")
        valid, errors = validate("execution-results", result)
        assert valid, f"failed result schema invalid: {errors}"

        redis.publish("execution:results", result)

        # No position should be recorded
        assert len(tracker.query(strategy="LEND-001")) == 0

    def test_multiple_channels_message_flow(self) -> None:
        """Verify messages flow through correct Redis channels."""
        redis = MockRedisManager()
        cid = uuid.uuid4().hex

        # Market event on market:events
        redis.publish("market:events", _make_market_event(cid))

        # Order on execution:orders
        strat, _, _ = _make_strategy()
        orders = strat.generate_orders(
            [_make_market("USDC", "0.065")],
            correlation_id=cid,
        )
        for order in orders:
            redis.publish("execution:orders", order)

        # Result on execution:results
        for order in orders:
            redis.publish("execution:results", _make_execution_result(order))

        assert len(redis.get_messages("market:events")) == 1
        assert len(redis.get_messages("execution:orders")) == 1
        assert len(redis.get_messages("execution:results")) == 1


# ---------------------------------------------------------------------------
# 2. Aave supply/withdraw cycle test
# ---------------------------------------------------------------------------

class TestAaveSupplyWithdrawCycle:
    """Aave supply → execution → then rotation when better market appears."""

    def test_initial_supply_to_best_market(self) -> None:
        """Strategy identifies best market and generates supply order."""
        strat, alloc, tracker = _make_strategy(total_capital="10000")
        markets = [
            _make_market("USDbC", "0.030"),
            _make_market("USDC", "0.042"),
        ]
        orders = strat.generate_orders(markets)
        assert len(orders) == 1
        assert orders[0]["action"] == "supply"
        assert orders[0]["params"]["tokenIn"] == "USDC"

    def test_supply_then_rotate_on_better_market(self) -> None:
        """After initial supply, rotation happens when net improvement > 0.5%."""
        strat, alloc, tracker = _make_strategy(total_capital="20000")

        # Step 1: Open initial position in USDC at 4.2% APY
        # Position $5000 of $20000 = 25% protocol exposure (< 40% max)
        tracker.open_position(
            strategy="LEND-001",
            protocol="aave",
            chain="base",
            asset="USDC",
            entry_price="1",
            amount="5000",
            position_id="aave-usdc",
            protocol_data={"current_apy": "0.042"},
        )

        # Step 2: A better market appears — USDbC at 7% APY
        # Net improvement = 7% - 4.2% = 2.8%, gas = 2*$10/$5000 = 0.4%
        # net = 2.8% - 0.4% = 2.4% > 0.5% threshold → should rotate
        new_markets = [
            _make_market("USDC", "0.042"),
            _make_market("USDbC", "0.070"),  # much better APY
        ]
        orders = strat.generate_orders(new_markets)
        assert len(orders) == 2
        assert orders[0]["action"] == "withdraw"
        assert orders[0]["params"]["tokenIn"] == "USDC"
        assert orders[1]["action"] == "supply"
        assert orders[1]["params"]["tokenIn"] == "USDbC"

        # Same correlation ID links both orders
        assert orders[0]["correlationId"] == orders[1]["correlationId"]

    def test_no_rotation_when_improvement_below_threshold(self) -> None:
        """Rotation does NOT happen when net improvement <= 0.5% APY."""
        strat, alloc, tracker = _make_strategy(total_capital="10000")

        # Current position: USDC at 4.2%
        tracker.open_position(
            strategy="LEND-001",
            protocol="aave",
            chain="base",
            asset="USDC",
            entry_price="1",
            amount="5000",
            position_id="aave-usdc",
            protocol_data={"current_apy": "0.042"},
        )

        # USDbC at 4.5% — only 0.3% improvement before gas costs
        # gas = 2*$10/$5000 = 0.4%, net = 0.3% - 0.4% = -0.1% < 0.5%
        markets = [
            _make_market("USDC", "0.042"),
            _make_market("USDbC", "0.045"),  # marginal improvement
        ]
        orders = strat.generate_orders(markets)
        assert len(orders) == 0

    def test_gas_cost_blocks_rotation_for_small_positions(self) -> None:
        """Small positions have proportionally higher gas costs, blocking rotation."""
        strat, alloc, tracker = _make_strategy(total_capital="1000")

        # Small position: $500 in USDbC at 3%
        tracker.open_position(
            strategy="LEND-001",
            protocol="aave",
            chain="base",
            asset="USDbC",
            entry_price="1",
            amount="500",
            position_id="aave-usdbc",
            protocol_data={"current_apy": "0.030"},
        )

        # USDC at 5% — diff = 2%, but gas = 2*$10/$500 = 4%
        # net = 2% - 4% = -2% < 0.5% → no rotation
        markets = [
            _make_market("USDbC", "0.030"),
            _make_market("USDC", "0.050"),
        ]
        orders = strat.generate_orders(markets)
        assert len(orders) == 0

    def test_rotation_records_performance_history(self) -> None:
        """Rotation from old to new market records performance for old market."""
        strat, alloc, tracker = _make_strategy(total_capital="10000")

        tracker.open_position(
            strategy="LEND-001",
            protocol="aave",
            chain="base",
            asset="USDbC",
            entry_price="1",
            amount="3000",
            position_id="aave-usdbc",
            protocol_data={"current_apy": "0.020"},
        )

        # USDC at 6.5% — significant improvement
        markets = [
            _make_market("USDbC", "0.020"),
            _make_market("USDC", "0.065"),
        ]
        orders = strat.generate_orders(markets)
        assert len(orders) == 2

        history = strat.get_performance_history()
        assert len(history) == 1
        assert history[0]["asset"] == "USDbC"
        assert history[0]["apy_at_entry"] == "0.020"
        assert history[0]["exit_time"] is not None

    def test_full_supply_withdraw_resupply_cycle(self) -> None:
        """Complete cycle: supply → confirm → better market → withdraw + supply."""
        strat, alloc, tracker = _make_strategy(total_capital="20000")

        # --- Phase 1: Initial supply ---
        initial_markets = [
            _make_market("USDC", "0.042"),
            _make_market("USDbC", "0.030"),
        ]
        orders = strat.generate_orders(initial_markets)
        assert len(orders) == 1
        assert orders[0]["action"] == "supply"
        supply_asset = orders[0]["params"]["tokenIn"]
        supply_amount = orders[0]["params"]["amount"]

        # Validate against schema
        valid, errors = validate("execution-orders", orders[0])
        assert valid, f"supply order schema: {errors}"

        # Simulate confirmed execution result
        result = _make_execution_result(orders[0])
        valid, errors = validate("execution-results", result)
        assert valid, f"supply result schema: {errors}"

        # Record position after confirmed supply
        tracker.open_position(
            strategy="LEND-001",
            protocol="aave",
            chain="base",
            asset=supply_asset,
            entry_price="1",
            amount=supply_amount,
            position_id="aave-pos-1",
            protocol_data={"current_apy": "0.042"},
        )

        # --- Phase 2: Better market appears → rotation ---
        better_markets = [
            _make_market("USDC", "0.042"),
            _make_market("USDbC", "0.075"),  # much higher APY
        ]
        rotation_orders = strat.generate_orders(better_markets)
        assert len(rotation_orders) == 2
        assert rotation_orders[0]["action"] == "withdraw"
        assert rotation_orders[0]["params"]["tokenIn"] == "USDC"
        assert rotation_orders[1]["action"] == "supply"
        assert rotation_orders[1]["params"]["tokenIn"] == "USDbC"

        # Validate both rotation orders against schema
        for ro in rotation_orders:
            valid, errors = validate("execution-orders", ro)
            assert valid, f"rotation order schema: {errors}"

        # Simulate both confirmed
        for ro in rotation_orders:
            result = _make_execution_result(ro)
            valid, errors = validate("execution-results", result)
            assert valid, f"rotation result schema: {errors}"

        # Close old position, open new one
        tracker.close_position("aave-pos-1")
        tracker.open_position(
            strategy="LEND-001",
            protocol="aave",
            chain="base",
            asset="USDbC",
            entry_price="1",
            amount=supply_amount,
            position_id="aave-pos-2",
            protocol_data={"current_apy": "0.075"},
        )

        # Verify tracker state
        open_positions = tracker.query(strategy="LEND-001", protocol="aave")
        assert len(open_positions) == 1
        assert open_positions[0].asset == "USDbC"
        assert open_positions[0].id == "aave-pos-2"

        closed = tracker.query(
            strategy="LEND-001", protocol="aave", include_closed=True,
        )
        assert len(closed) == 2  # 1 open + 1 closed

    def test_no_rotation_when_already_in_best_market(self) -> None:
        """No orders if already supplying to the highest APY market."""
        strat, alloc, tracker = _make_strategy(total_capital="10000")

        tracker.open_position(
            strategy="LEND-001",
            protocol="aave",
            chain="base",
            asset="USDC",
            entry_price="1",
            amount="5000",
            position_id="aave-usdc",
            protocol_data={"current_apy": "0.042"},
        )

        markets = [
            _make_market("USDC", "0.042"),
            _make_market("USDbC", "0.030"),
        ]
        orders = strat.generate_orders(markets)
        assert len(orders) == 0

    def test_rotation_threshold_exactly_at_boundary(self) -> None:
        """Verify behavior right at the 0.5% APY improvement boundary."""
        # Config: min_apy_improvement=0.005 (0.5%), gas=$10 per TX
        # Use $20000 total capital so $5000 position = 25% protocol exposure (< 40%)
        strat, alloc, tracker = _make_strategy(total_capital="20000")

        tracker.open_position(
            strategy="LEND-001",
            protocol="aave",
            chain="base",
            asset="USDbC",
            entry_price="1",
            amount="5000",
            position_id="aave-usdbc",
            protocol_data={"current_apy": "0.030"},
        )

        # Position value = $5000
        # Need: apy_diff - (2*$10/$5000) >= 0.005
        # Need: apy_diff - 0.004 >= 0.005
        # Need: apy_diff >= 0.009
        # best = 3.0% + 0.9% = 3.9% → exactly at boundary

        # At boundary: should_rotate returns True when net == threshold
        markets_at_boundary = [
            _make_market("USDbC", "0.030"),
            _make_market("USDC", "0.039"),
        ]
        orders = strat.generate_orders(markets_at_boundary)
        # net_improvement = 0.009 - 0.004 = 0.005 = threshold → allowed
        assert len(orders) == 2

        # Just below: best = 3.89%
        strat2, _, tracker2 = _make_strategy(total_capital="20000")
        tracker2.open_position(
            strategy="LEND-001",
            protocol="aave",
            chain="base",
            asset="USDbC",
            entry_price="1",
            amount="5000",
            position_id="aave-usdbc2",
            protocol_data={"current_apy": "0.030"},
        )
        markets_below = [
            _make_market("USDbC", "0.030"),
            _make_market("USDC", "0.0389"),
        ]
        orders2 = strat2.generate_orders(markets_below)
        # net = 0.0089 - 0.004 = 0.0049 < 0.005 → blocked
        assert len(orders2) == 0

    def test_schema_validation_of_all_generated_orders(self) -> None:
        """Every order produced by the strategy must pass schema validation."""
        strat, _, tracker = _make_strategy(total_capital="10000")

        # New supply
        markets = [_make_market("USDC", "0.065")]
        orders = strat.generate_orders(markets)
        for order in orders:
            valid, errors = validate("execution-orders", order)
            assert valid, f"order schema invalid: {errors}"

        # Rotation (need existing position first)
        strat2, _, tracker2 = _make_strategy(total_capital="10000")
        tracker2.open_position(
            strategy="LEND-001",
            protocol="aave",
            chain="base",
            asset="USDbC",
            entry_price="1",
            amount="3000",
            position_id="aave-usdbc",
            protocol_data={"current_apy": "0.020"},
        )
        rotation_orders = strat2.generate_orders(
            [_make_market("USDbC", "0.020"), _make_market("USDC", "0.065")],
        )
        for order in rotation_orders:
            valid, errors = validate("execution-orders", order)
            assert valid, f"rotation order schema invalid: {errors}"

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
