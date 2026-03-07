"""Aerodrome stable LP auto-compound — LP-001 tests."""

from __future__ import annotations

from decimal import Decimal

from portfolio.allocator import AllocatorConfig, PortfolioAllocator
from portfolio.position_tracker import PositionTracker
from strategies.aerodrome_lp import (
    STRATEGY_ID,
    STRATEGY_TIER,
    AerodromeLpConfig,
    AerodromeLpStrategy,
    StablePool,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_pool(
    pool_id: str = "usdc-usdbc-stable",
    token_a: str = "USDC",
    token_b: str = "USDbC",
    emission_apr: str = "0.08",
    tvl_usd: str = "2000000",
    aero_price_usd: str = "0.50",
    gauge_address: str = "0x1234567890abcdef1234567890abcdef12345678",
    chain: str = "base",
) -> StablePool:
    return StablePool(
        pool_id=pool_id,
        token_a=token_a,
        token_b=token_b,
        emission_apr=Decimal(emission_apr),
        tvl_usd=Decimal(tvl_usd),
        aero_price_usd=Decimal(aero_price_usd),
        gauge_address=gauge_address,
        chain=chain,
    )


def _make_strategy(
    total_capital: str = "2000",
    positions: dict | None = None,
    config: AerodromeLpConfig | None = None,
) -> tuple[AerodromeLpStrategy, PortfolioAllocator, PositionTracker]:
    allocator = PortfolioAllocator(
        Decimal(total_capital),
        positions or {},
        AllocatorConfig(),
    )
    tracker = PositionTracker()
    return AerodromeLpStrategy(allocator, tracker, config), allocator, tracker


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestStrategyIdentity:
    def test_strategy_id(self):
        assert STRATEGY_ID == "LP-001"

    def test_strategy_tier(self):
        assert STRATEGY_TIER == 1


class TestEvaluate:
    def test_filters_by_min_apr(self):
        """Pools below 3% emission APR are excluded."""
        strategy, _, _ = _make_strategy()
        pools = [
            _make_pool(emission_apr="0.02"),  # below threshold
            _make_pool(pool_id="good", emission_apr="0.05"),
        ]
        result = strategy.evaluate(pools)
        assert len(result) == 1
        assert result[0].pool_id == "good"

    def test_filters_by_min_tvl(self):
        """Pools below $500K TVL are excluded."""
        strategy, _, _ = _make_strategy()
        pools = [_make_pool(tvl_usd="100000")]  # $100K, below $500K threshold
        result = strategy.evaluate(pools)
        assert len(result) == 0

    def test_rejects_non_base_chain(self):
        """Only Base chain pools accepted."""
        strategy, _, _ = _make_strategy()
        pools = [_make_pool(chain="ethereum")]
        result = strategy.evaluate(pools)
        assert len(result) == 0

    def test_ranks_by_emission_apr(self):
        """Pools ranked by emission APR descending."""
        strategy, _, _ = _make_strategy()
        pools = [
            _make_pool(pool_id="low", emission_apr="0.04"),
            _make_pool(pool_id="high", emission_apr="0.10"),
            _make_pool(pool_id="mid", emission_apr="0.06"),
        ]
        result = strategy.evaluate(pools)
        assert [p.pool_id for p in result] == ["high", "mid", "low"]

    def test_empty_input(self):
        strategy, _, _ = _make_strategy()
        assert strategy.evaluate([]) == []


class TestShouldHarvest:
    def test_harvest_above_threshold(self):
        """Harvest when pending AERO > $0.50."""
        strategy, _, _ = _make_strategy()
        assert strategy.should_harvest(Decimal("1.00")) is True

    def test_no_harvest_below_threshold(self):
        """Don't harvest when pending AERO < $0.50."""
        strategy, _, _ = _make_strategy()
        assert strategy.should_harvest(Decimal("0.30")) is False

    def test_harvest_at_exact_threshold(self):
        strategy, _, _ = _make_strategy()
        assert strategy.should_harvest(Decimal("0.50")) is True


class TestGenerateOrders:
    def test_enter_new_pool(self):
        """When no position exists, enter best pool with mint_lp."""
        strategy, _, _ = _make_strategy()
        pools = [_make_pool()]
        orders = strategy.generate_orders(pools)
        assert len(orders) >= 1
        assert orders[0]["action"] == "mint_lp"
        assert orders[0]["protocol"] == "aerodrome"
        assert orders[0]["chain"] == "base"

    def test_no_orders_when_no_eligible_pools(self):
        strategy, _, _ = _make_strategy()
        pools = [_make_pool(emission_apr="0.01")]  # below threshold
        orders = strategy.generate_orders(pools)
        assert orders == []

    def test_orders_are_schema_compliant(self):
        """All generated orders pass execution-orders schema validation."""
        from validation.schema_validator import validate
        strategy, _, _ = _make_strategy()
        pools = [_make_pool()]
        orders = strategy.generate_orders(pools)
        for order in orders:
            valid, errors = validate("execution-orders", order)
            assert valid, f"Schema validation failed: {errors}"

    def test_respects_allocation_limit(self):
        """Don't exceed 30% of portfolio."""
        strategy, _, _ = _make_strategy(total_capital="1000")
        pools = [_make_pool()]
        orders = strategy.generate_orders(pools)
        if orders:
            amount = Decimal(orders[0]["params"]["amount"])
            # 30% of 1000 = 300 max
            assert amount <= Decimal("300")


class TestExitConditions:
    def test_should_exit_low_apr(self):
        """Exit when APR drops below 1.5%."""
        strategy, _, _ = _make_strategy()
        assert strategy.should_exit(
            current_apr=Decimal("0.01"),
            aero_price_change=Decimal("0"),
        ) is True

    def test_should_exit_aero_crash(self):
        """Exit when AERO drops >50% in 24h."""
        strategy, _, _ = _make_strategy()
        assert strategy.should_exit(
            current_apr=Decimal("0.05"),
            aero_price_change=Decimal("-0.55"),
        ) is True

    def test_should_not_exit_healthy(self):
        """Don't exit when conditions are fine."""
        strategy, _, _ = _make_strategy()
        assert strategy.should_exit(
            current_apr=Decimal("0.05"),
            aero_price_change=Decimal("-0.10"),
        ) is False
