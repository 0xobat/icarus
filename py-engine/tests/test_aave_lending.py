"""Tests for Aave lending supply strategy — LEND-001."""

from __future__ import annotations

from decimal import Decimal

from portfolio.allocator import AllocatorConfig, PortfolioAllocator
from portfolio.position_tracker import PositionTracker
from strategies.aave_lending import (
    ALLOWED_CHAINS,
    WHITELISTED_ASSETS,
    AaveLendingConfig,
    AaveLendingStrategy,
    AaveMarket,
)

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


def _sample_markets() -> list[AaveMarket]:
    return [
        _make_market("USDC", "0.042"),
        _make_market("USDbC", "0.035"),
    ]


def _make_strategy(
    total_capital: str = "10000",
    positions: dict | None = None,
    config: AaveLendingConfig | None = None,
) -> tuple[AaveLendingStrategy, PortfolioAllocator, PositionTracker]:
    alloc = PortfolioAllocator(
        Decimal(total_capital),
        positions or {},
        AllocatorConfig(),
    )
    tracker = PositionTracker()
    strat = AaveLendingStrategy(alloc, tracker, config)
    return strat, alloc, tracker


# ---------------------------------------------------------------------------
# Market evaluation and ranking
# ---------------------------------------------------------------------------

class TestMarketEvaluation:
    """Strategy must identify and rank Aave markets by APY."""

    def test_ranks_by_apy_descending(self) -> None:
        strat, _, _ = _make_strategy()
        ranked = strat.evaluate(_sample_markets())
        apys = [m.supply_apy for m in ranked]
        assert apys == sorted(apys, reverse=True)

    def test_filters_non_whitelisted(self) -> None:
        strat, _, _ = _make_strategy()
        markets = [
            _make_market("USDC", "0.03"),
            _make_market("SHIB", "0.50"),  # not whitelisted
        ]
        ranked = strat.evaluate(markets)
        assert len(ranked) == 1
        assert ranked[0].asset == "USDC"

    def test_filters_zero_liquidity(self) -> None:
        strat, _, _ = _make_strategy()
        markets = [
            _make_market("USDC", "0.03", available_liquidity="0"),
            _make_market("USDbC", "0.04"),
        ]
        ranked = strat.evaluate(markets)
        assert len(ranked) == 1

    def test_filters_zero_apy(self) -> None:
        strat, _, _ = _make_strategy()
        markets = [
            _make_market("USDC", "0"),
            _make_market("USDbC", "0.04"),
        ]
        ranked = strat.evaluate(markets)
        assert len(ranked) == 1

    def test_empty_markets(self) -> None:
        strat, _, _ = _make_strategy()
        assert strat.evaluate([]) == []

    def test_whitelisted_assets_include_expected(self) -> None:
        for asset in ("USDC", "USDbC"):
            assert asset in WHITELISTED_ASSETS

    def test_rejects_non_base_chain(self) -> None:
        """LEND-001 only operates on Base."""
        strategy, _, _ = _make_strategy()
        markets = [_make_market(asset="USDC", chain="ethereum", supply_apy="0.06")]
        orders = strategy.generate_orders(markets)
        assert orders == []

    def test_rejects_non_stablecoin_asset(self) -> None:
        """LEND-001 only operates with stablecoins."""
        strategy, _, _ = _make_strategy()
        markets = [_make_market(asset="ETH", chain="base", supply_apy="0.06")]
        orders = strategy.generate_orders(markets)
        assert orders == []


# ---------------------------------------------------------------------------
# Rotation threshold
# ---------------------------------------------------------------------------

class TestRotationThreshold:
    """Rotation only happens when net improvement exceeds threshold."""

    def test_significant_improvement_rotates(self) -> None:
        strat, _, _ = _make_strategy()
        best = _make_market("USDC", "0.050")
        # current=3%, best=5%, position=$10000 → apy_diff=2%
        # gas: 2*$10 = $20, pct = $20/$10000 = 0.2%
        # net = 2% - 0.2% = 1.8% > 0.5% threshold
        assert strat.should_rotate(Decimal("0.030"), best, Decimal("10000"))

    def test_small_improvement_no_rotate(self) -> None:
        strat, _, _ = _make_strategy()
        best = _make_market("USDC", "0.036")
        # current=3.5%, best=3.6%, diff=0.1%
        # gas: $20/$10000 = 0.2%
        # net = 0.1% - 0.2% = -0.1% < 0.5%
        assert not strat.should_rotate(Decimal("0.035"), best, Decimal("10000"))

    def test_worse_market_no_rotate(self) -> None:
        strat, _, _ = _make_strategy()
        best = _make_market("USDC", "0.025")
        assert not strat.should_rotate(Decimal("0.030"), best, Decimal("10000"))

    def test_equal_apy_no_rotate(self) -> None:
        strat, _, _ = _make_strategy()
        best = _make_market("USDC", "0.035")
        assert not strat.should_rotate(Decimal("0.035"), best, Decimal("10000"))

    def test_zero_position_no_rotate(self) -> None:
        strat, _, _ = _make_strategy()
        best = _make_market("USDC", "0.050")
        assert not strat.should_rotate(Decimal("0.030"), best, Decimal("0"))


# ---------------------------------------------------------------------------
# Gas cost accounting
# ---------------------------------------------------------------------------

class TestGasCostAccounting:
    """Gas costs must reduce the effective improvement."""

    def test_high_gas_blocks_rotation(self) -> None:
        config = AaveLendingConfig(estimated_gas_cost_usd=Decimal("500"))
        strat, _, _ = _make_strategy(config=config)
        best = _make_market("USDC", "0.050")
        # diff=2%, gas: 2*$500/$10000 = 10%, net = 2%-10% = -8%
        assert not strat.should_rotate(Decimal("0.030"), best, Decimal("10000"))

    def test_small_position_gas_dominated(self) -> None:
        strat, _, _ = _make_strategy()
        best = _make_market("USDC", "0.050")
        # diff=2%, gas: 2*$10/$200 = 10%, net = 2%-10% = -8%
        assert not strat.should_rotate(Decimal("0.030"), best, Decimal("200"))

    def test_large_position_gas_negligible(self) -> None:
        strat, _, _ = _make_strategy()
        best = _make_market("USDC", "0.040")
        # diff=0.5%, gas: 2*$10/$100000 = 0.02%, net = 0.5%-0.02% ≈ 0.48%
        # 0.48% < 0.5% threshold — just barely not enough
        assert not strat.should_rotate(Decimal("0.035"), best, Decimal("100000"))


# ---------------------------------------------------------------------------
# Exposure limit respect
# ---------------------------------------------------------------------------

class TestExposureLimits:
    """Strategy must respect PortfolioAllocator limits."""

    def test_new_position_respects_protocol_limit(self) -> None:
        # Aave already at 40% → no room left under protocol limit
        positions = {
            "existing": {
                "value_usd": 4000, "protocol": "aave",
                "asset": "USDC", "tier": 1,
            },
        }
        strat, _, tracker = _make_strategy(positions=positions)
        orders = strat.generate_orders(_sample_markets())
        # proto_room = 4000 - 4000 = 0 → below min position → no orders
        assert len(orders) == 0

    def test_rotation_blocked_by_allocator(self) -> None:
        # Existing position in aave, rotation to aave on different asset
        # but with allocator configured to reject
        config = AllocatorConfig(max_protocol_exposure=Decimal("0.00"))
        alloc = PortfolioAllocator(Decimal("10000"), {}, config)
        tracker = PositionTracker()
        tracker.open_position(
            strategy="LEND-001", protocol="aave", chain="base",
            asset="USDbC", entry_price="1", amount="3000",
            position_id="aave-pos",
            protocol_data={"current_apy": "0.030"},
        )
        strat = AaveLendingStrategy(alloc, tracker)
        orders = strat.generate_orders(_sample_markets())
        assert len(orders) == 0


# ---------------------------------------------------------------------------
# Order generation (schema compliance)
# ---------------------------------------------------------------------------

class TestOrderGeneration:
    """Orders must comply with execution:orders schema."""

    def test_new_supply_order(self) -> None:
        strat, _, _ = _make_strategy()
        orders = strat.generate_orders(_sample_markets())
        assert len(orders) == 1
        order = orders[0]
        # Schema required fields
        assert order["version"] == "1.0.0"
        assert "orderId" in order
        assert "correlationId" in order
        assert "timestamp" in order
        assert order["chain"] == "base"
        assert order["protocol"] == "aave_v3"
        assert order["action"] == "supply"
        assert order["strategy"] == "LEND-001"
        assert order["priority"] == "normal"
        assert "tokenIn" in order["params"]
        assert "amount" in order["params"]
        assert "maxGasWei" in order["limits"]
        assert "maxSlippageBps" in order["limits"]
        assert "deadlineUnix" in order["limits"]

    def test_rotation_generates_withdraw_and_supply(self) -> None:
        strat, _, tracker = _make_strategy()
        tracker.open_position(
            strategy="LEND-001", protocol="aave", chain="base",
            asset="USDbC", entry_price="1", amount="3000",
            position_id="aave-pos",
            protocol_data={"current_apy": "0.020"},
        )
        # Best market is USDC at 4.2%, current USDbC at 2% → big improvement
        orders = strat.generate_orders(_sample_markets())
        assert len(orders) == 2
        assert orders[0]["action"] == "withdraw"
        assert orders[0]["params"]["tokenIn"] == "USDbC"
        assert orders[1]["action"] == "supply"
        assert orders[1]["params"]["tokenIn"] == "USDC"

    def test_same_correlation_id_across_rotation(self) -> None:
        strat, _, tracker = _make_strategy()
        tracker.open_position(
            strategy="LEND-001", protocol="aave", chain="base",
            asset="USDbC", entry_price="1", amount="3000",
            position_id="aave-pos",
            protocol_data={"current_apy": "0.020"},
        )
        orders = strat.generate_orders(_sample_markets(), correlation_id="test-cid")
        assert len(orders) == 2
        assert orders[0]["correlationId"] == "test-cid"
        assert orders[1]["correlationId"] == "test-cid"

    def test_no_orders_when_already_in_best(self) -> None:
        strat, _, tracker = _make_strategy()
        tracker.open_position(
            strategy="LEND-001", protocol="aave", chain="base",
            asset="USDC", entry_price="1", amount="3000",
            position_id="aave-pos",
            protocol_data={"current_apy": "0.042"},
        )
        # USDC is already the best market at 4.2%
        orders = strat.generate_orders(_sample_markets())
        assert len(orders) == 0

    def test_no_orders_below_min_capital(self) -> None:
        config = AaveLendingConfig(min_position_value_usd=Decimal("999999"))
        strat, _, _ = _make_strategy(config=config)
        orders = strat.generate_orders(_sample_markets())
        assert len(orders) == 0

    def test_no_orders_on_empty_markets(self) -> None:
        strat, _, _ = _make_strategy()
        orders = strat.generate_orders([])
        assert len(orders) == 0


# ---------------------------------------------------------------------------
# Historical performance tracking
# ---------------------------------------------------------------------------

class TestPerformanceTracking:
    """Strategy tracks APY history per market supplied to."""

    def test_records_on_rotation(self) -> None:
        strat, _, tracker = _make_strategy()
        tracker.open_position(
            strategy="LEND-001", protocol="aave", chain="base",
            asset="USDbC", entry_price="1", amount="3000",
            position_id="aave-pos",
            protocol_data={"current_apy": "0.020"},
        )
        strat.generate_orders(_sample_markets())
        history = strat.get_performance_history()
        assert len(history) == 1
        assert history[0]["asset"] == "USDbC"
        assert history[0]["apy_at_entry"] == "0.020"
        assert history[0]["exit_time"] is not None

    def test_no_record_without_rotation(self) -> None:
        strat, _, tracker = _make_strategy()
        tracker.open_position(
            strategy="LEND-001", protocol="aave", chain="base",
            asset="USDC", entry_price="1", amount="3000",
            position_id="aave-pos",
            protocol_data={"current_apy": "0.042"},
        )
        strat.generate_orders(_sample_markets())
        assert len(strat.get_performance_history()) == 0

    def test_multiple_rotations_accumulate(self) -> None:
        strat, _, tracker = _make_strategy()
        # First rotation: USDbC → USDC
        tracker.open_position(
            strategy="LEND-001", protocol="aave", chain="base",
            asset="USDbC", entry_price="1", amount="3000",
            position_id="p1",
            protocol_data={"current_apy": "0.020"},
        )
        strat.generate_orders(_sample_markets())
        assert len(strat.get_performance_history()) == 1

        # Simulate: close old, open new position as USDC
        tracker.close_position("p1", exit_price="1")
        tracker.open_position(
            strategy="LEND-001", protocol="aave", chain="base",
            asset="USDC", entry_price="1", amount="3000",
            position_id="p2",
            protocol_data={"current_apy": "0.042"},
        )

        # Now try to generate orders — should not rotate (already in best)
        strat.generate_orders(_sample_markets())
        assert len(strat.get_performance_history()) == 1  # no new record


# ---------------------------------------------------------------------------
# Strategy status
# ---------------------------------------------------------------------------

class TestStrategyStatus:

    def test_initial_status_evaluating(self) -> None:
        strat, _, _ = _make_strategy()
        assert strat.status == "evaluating"

    def test_status_can_change(self) -> None:
        strat, _, _ = _make_strategy()
        strat.status = "active"
        assert strat.status == "active"
