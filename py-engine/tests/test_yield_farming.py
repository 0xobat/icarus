"""Tests for yield farming auto-compound strategy — STRAT-004."""

from __future__ import annotations

from decimal import Decimal

from portfolio.allocator import AllocatorConfig, PortfolioAllocator
from portfolio.position_tracker import PositionTracker
from strategies.yield_farming import (
    STRATEGY_ID,
    FarmOpportunity,
    YieldFarmingConfig,
    YieldFarmingStrategy,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_farm(
    farm_id: str = "aave-eth-farm",
    protocol: str = "aave_v3",
    asset: str = "ETH",
    reward_token: str = "AAVE",
    farm_apr: str = "0.08",
    tvl_usd: str = "50000000",
    reward_token_price_usd: str = "100",
    chain: str = "ethereum",
) -> FarmOpportunity:
    return FarmOpportunity(
        farm_id=farm_id,
        protocol=protocol,
        asset=asset,
        reward_token=reward_token,
        farm_apr=Decimal(farm_apr),
        tvl_usd=Decimal(tvl_usd),
        reward_token_price_usd=Decimal(reward_token_price_usd),
        chain=chain,
    )


def _sample_farms() -> list[FarmOpportunity]:
    return [
        _make_farm("aave-eth", farm_apr="0.08"),
        _make_farm("curve-3pool", protocol="curve", asset="USDC", farm_apr="0.06"),
        _make_farm("convex-eth", protocol="convex", farm_apr="0.10"),
    ]


def _make_strategy(
    total_capital: str = "10000",
    positions: dict | None = None,
    config: YieldFarmingConfig | None = None,
) -> tuple[YieldFarmingStrategy, PortfolioAllocator, PositionTracker]:
    alloc = PortfolioAllocator(
        Decimal(total_capital),
        positions or {},
        AllocatorConfig(),
    )
    tracker = PositionTracker()
    strat = YieldFarmingStrategy(alloc, tracker, config)
    return strat, alloc, tracker


# ---------------------------------------------------------------------------
# Farm evaluation
# ---------------------------------------------------------------------------

class TestFarmEvaluation:

    def test_ranks_by_apr_descending(self) -> None:
        strat, _, _ = _make_strategy()
        ranked = strat.evaluate(_sample_farms())
        aprs = [f.farm_apr for f in ranked]
        assert aprs == sorted(aprs, reverse=True)

    def test_filters_low_apr(self) -> None:
        strat, _, _ = _make_strategy()
        farms = [
            _make_farm(farm_apr="0.02"),  # below 5% threshold
            _make_farm(farm_apr="0.08"),
        ]
        ranked = strat.evaluate(farms)
        assert len(ranked) == 1

    def test_filters_low_tvl(self) -> None:
        strat, _, _ = _make_strategy()
        farms = [
            _make_farm(tvl_usd="1000"),  # below $10M threshold
            _make_farm(farm_apr="0.08"),
        ]
        ranked = strat.evaluate(farms)
        assert len(ranked) == 1

    def test_filters_zero_reward_price(self) -> None:
        strat, _, _ = _make_strategy()
        farms = [_make_farm(reward_token_price_usd="0")]
        ranked = strat.evaluate(farms)
        assert len(ranked) == 0

    def test_empty_farms(self) -> None:
        strat, _, _ = _make_strategy()
        assert strat.evaluate([]) == []


# ---------------------------------------------------------------------------
# Action threshold
# ---------------------------------------------------------------------------

class TestShouldAct:

    def test_reward_crash_triggers_exit(self) -> None:
        strat, _, _ = _make_strategy()
        assert strat.should_act(
            Decimal("0.08"), Decimal("5"),
            reward_token_price_change=Decimal("-0.35"),
        )

    def test_low_apr_triggers_exit(self) -> None:
        strat, _, _ = _make_strategy()
        assert strat.should_act(Decimal("0.02"), Decimal("5"))

    def test_sufficient_rewards_trigger_harvest(self) -> None:
        strat, _, _ = _make_strategy()
        # 2x gas = $20, rewards = $25
        assert strat.should_act(Decimal("0.08"), Decimal("25"))

    def test_insufficient_rewards_no_action(self) -> None:
        strat, _, _ = _make_strategy()
        # rewards $5 < 2*$10 = $20
        assert not strat.should_act(Decimal("0.08"), Decimal("5"))


# ---------------------------------------------------------------------------
# Harvest threshold
# ---------------------------------------------------------------------------

class TestShouldHarvest:

    def test_rewards_above_threshold(self) -> None:
        strat, _, _ = _make_strategy()
        assert strat.should_harvest(Decimal("25"))

    def test_rewards_below_threshold(self) -> None:
        strat, _, _ = _make_strategy()
        assert not strat.should_harvest(Decimal("15"))

    def test_rewards_at_threshold(self) -> None:
        strat, _, _ = _make_strategy()
        assert strat.should_harvest(Decimal("20"))


# ---------------------------------------------------------------------------
# Order generation
# ---------------------------------------------------------------------------

class TestOrderGeneration:

    def test_new_farm_supply_order(self) -> None:
        strat, _, _ = _make_strategy()
        orders = strat.generate_orders(_sample_farms())
        assert len(orders) == 1
        order = orders[0]
        assert order["version"] == "1.0.0"
        assert "orderId" in order
        assert order["action"] == "supply"
        assert order["strategy"] == STRATEGY_ID
        assert "tokenIn" in order["params"]
        assert "maxGasWei" in order["limits"]

    def test_exit_on_low_apr(self) -> None:
        strat, _, tracker = _make_strategy()
        tracker.open_position(
            strategy=STRATEGY_ID, protocol="aave_v3", chain="ethereum",
            asset="ETH", entry_price="2000", amount="2.0",
            position_id="farm-pos",
            protocol_data={
                "farm_apr": "0.02",
                "pending_rewards_usd": "5",
                "reward_token_price_change": "0",
                "reward_token": "AAVE",
                "farm_protocol": "aave_v3",
            },
        )
        orders = strat.generate_orders(_sample_farms())
        assert len(orders) == 1
        assert orders[0]["action"] == "withdraw"

    def test_exit_on_reward_crash(self) -> None:
        strat, _, tracker = _make_strategy()
        tracker.open_position(
            strategy=STRATEGY_ID, protocol="aave_v3", chain="ethereum",
            asset="ETH", entry_price="2000", amount="2.0",
            position_id="farm-pos",
            protocol_data={
                "farm_apr": "0.08",
                "pending_rewards_usd": "5",
                "reward_token_price_change": "-0.35",
                "reward_token": "AAVE",
                "farm_protocol": "aave_v3",
            },
        )
        orders = strat.generate_orders(_sample_farms())
        assert len(orders) == 1
        assert orders[0]["action"] == "withdraw"

    def test_harvest_and_compound(self) -> None:
        strat, _, tracker = _make_strategy()
        tracker.open_position(
            strategy=STRATEGY_ID, protocol="aave_v3", chain="ethereum",
            asset="ETH", entry_price="2000", amount="2.0",
            position_id="farm-pos",
            protocol_data={
                "farm_apr": "0.08",
                "pending_rewards_usd": "25",
                "reward_token_price_change": "0",
                "reward_token": "AAVE",
                "farm_protocol": "aave_v3",
            },
        )
        orders = strat.generate_orders(_sample_farms())
        assert len(orders) == 2
        assert orders[0]["action"] == "collect_fees"
        assert orders[1]["action"] == "supply"

    def test_no_orders_when_position_healthy_low_rewards(self) -> None:
        strat, _, tracker = _make_strategy()
        tracker.open_position(
            strategy=STRATEGY_ID, protocol="aave_v3", chain="ethereum",
            asset="ETH", entry_price="2000", amount="2.0",
            position_id="farm-pos",
            protocol_data={
                "farm_apr": "0.08",
                "pending_rewards_usd": "5",
                "reward_token_price_change": "0",
                "reward_token": "AAVE",
                "farm_protocol": "aave_v3",
            },
        )
        orders = strat.generate_orders(_sample_farms())
        assert len(orders) == 0

    def test_no_orders_below_min_capital(self) -> None:
        config = YieldFarmingConfig(min_position_value_usd=Decimal("999999"))
        strat, _, _ = _make_strategy(config=config)
        orders = strat.generate_orders(_sample_farms())
        assert len(orders) == 0

    def test_no_orders_on_empty_farms(self) -> None:
        strat, _, _ = _make_strategy()
        orders = strat.generate_orders([])
        assert len(orders) == 0

    def test_protocol_exposure_respected(self) -> None:
        positions = {
            "existing": {
                "value_usd": 4000, "protocol": "convex",
                "asset": "ETH", "tier": 2,
            },
        }
        strat, _, _ = _make_strategy(positions=positions)
        orders = strat.generate_orders(_sample_farms())
        assert len(orders) == 0


# ---------------------------------------------------------------------------
# Strategy status
# ---------------------------------------------------------------------------

class TestStrategyStatus:

    def test_initial_status_evaluating(self) -> None:
        strat, _, _ = _make_strategy()
        assert strat.status == "evaluating"
