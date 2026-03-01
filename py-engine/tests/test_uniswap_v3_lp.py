"""Tests for Uniswap V3 concentrated liquidity strategy — STRAT-003."""

from __future__ import annotations

from decimal import Decimal

from portfolio.allocator import AllocatorConfig, PortfolioAllocator
from portfolio.position_tracker import PositionTracker
from strategies.uniswap_v3_lp import (
    STRATEGY_ID,
    LPPosition,
    UniswapV3LPConfig,
    UniswapV3LPStrategy,
    UniswapV3Pool,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_pool(
    pair: str = "ETH/USDC",
    token0: str = "ETH",
    token1: str = "USDC",
    current_price: str = "2000",
    fee_tier: int = 3000,
    tvl_usd: str = "50000000",
    volume_24h_usd: str = "10000000",
    fee_apr: str = "0.12",
    volatility_7d: str = "0.05",
    chain: str = "ethereum",
) -> UniswapV3Pool:
    return UniswapV3Pool(
        pair=pair,
        token0=token0,
        token1=token1,
        current_price=Decimal(current_price),
        fee_tier=fee_tier,
        tvl_usd=Decimal(tvl_usd),
        volume_24h_usd=Decimal(volume_24h_usd),
        fee_apr=Decimal(fee_apr),
        volatility_7d=Decimal(volatility_7d),
        chain=chain,
    )


def _sample_pools() -> list[UniswapV3Pool]:
    return [
        _make_pool("ETH/USDC", fee_apr="0.12"),
        _make_pool("WBTC/ETH", token0="WBTC", token1="ETH", fee_apr="0.08"),
        _make_pool("ETH/DAI", token1="DAI", fee_apr="0.10"),
    ]


def _make_strategy(
    total_capital: str = "10000",
    positions: dict | None = None,
    config: UniswapV3LPConfig | None = None,
) -> tuple[UniswapV3LPStrategy, PortfolioAllocator, PositionTracker]:
    alloc = PortfolioAllocator(
        Decimal(total_capital),
        positions or {},
        AllocatorConfig(),
    )
    tracker = PositionTracker()
    strat = UniswapV3LPStrategy(alloc, tracker, config)
    return strat, alloc, tracker


def _make_lp_position(
    pool_pair: str = "ETH/USDC",
    in_range: bool = True,
    impermanent_loss_pct: str = "0.01",
    uncollected_fees_usd: str = "5",
) -> LPPosition:
    return LPPosition(
        pool_pair=pool_pair,
        lower_tick=Decimal("1800"),
        upper_tick=Decimal("2200"),
        current_price=Decimal("2000"),
        uncollected_fees_usd=Decimal(uncollected_fees_usd),
        impermanent_loss_pct=Decimal(impermanent_loss_pct),
        in_range=in_range,
        position_value_usd=Decimal("5000"),
    )


# ---------------------------------------------------------------------------
# Pool evaluation
# ---------------------------------------------------------------------------

class TestPoolEvaluation:

    def test_ranks_by_fee_apr_descending(self) -> None:
        strat, _, _ = _make_strategy()
        ranked = strat.evaluate(_sample_pools())
        aprs = [p.fee_apr for p in ranked]
        assert aprs == sorted(aprs, reverse=True)

    def test_filters_low_tvl(self) -> None:
        strat, _, _ = _make_strategy()
        pools = [
            _make_pool(tvl_usd="100"),  # below $1M threshold
            _make_pool(fee_apr="0.10"),
        ]
        ranked = strat.evaluate(pools)
        assert len(ranked) == 1

    def test_filters_low_fee_apr(self) -> None:
        strat, _, _ = _make_strategy()
        pools = [
            _make_pool(fee_apr="0.01"),  # below 5% threshold
            _make_pool(fee_apr="0.10"),
        ]
        ranked = strat.evaluate(pools)
        assert len(ranked) == 1

    def test_filters_zero_volume(self) -> None:
        strat, _, _ = _make_strategy()
        pools = [_make_pool(volume_24h_usd="0")]
        ranked = strat.evaluate(pools)
        assert len(ranked) == 0

    def test_empty_pools(self) -> None:
        strat, _, _ = _make_strategy()
        assert strat.evaluate([]) == []


# ---------------------------------------------------------------------------
# Action threshold
# ---------------------------------------------------------------------------

class TestShouldAct:

    def test_no_position_attractive_pool(self) -> None:
        strat, _, _ = _make_strategy()
        pool = _make_pool(fee_apr="0.10")
        assert strat.should_act(None, pool)

    def test_no_position_no_pool(self) -> None:
        strat, _, _ = _make_strategy()
        assert not strat.should_act(None, None)

    def test_high_il_triggers_action(self) -> None:
        strat, _, _ = _make_strategy()
        lp = _make_lp_position(impermanent_loss_pct="0.06")
        assert strat.should_act(lp)

    def test_out_of_range_triggers_action(self) -> None:
        strat, _, _ = _make_strategy()
        lp = _make_lp_position(in_range=False)
        assert strat.should_act(lp)

    def test_fees_above_threshold_triggers_action(self) -> None:
        strat, _, _ = _make_strategy()
        lp = _make_lp_position(uncollected_fees_usd="15")
        assert strat.should_act(lp)

    def test_healthy_position_no_action(self) -> None:
        strat, _, _ = _make_strategy()
        lp = _make_lp_position(
            impermanent_loss_pct="0.01",
            uncollected_fees_usd="5",
            in_range=True,
        )
        assert not strat.should_act(lp)


# ---------------------------------------------------------------------------
# Range calculation
# ---------------------------------------------------------------------------

class TestRangeCalculation:

    def test_symmetric_around_price(self) -> None:
        strat, _, _ = _make_strategy()
        lower, upper = strat.calculate_range(
            Decimal("2000"), Decimal("0.05"),
        )
        # range_width = 2000 * 0.05 * 2.0 = 200
        # lower = 2000 - 100 = 1900, upper = 2000 + 100 = 2100
        assert lower == Decimal("1900")
        assert upper == Decimal("2100")

    def test_lower_bound_stays_positive(self) -> None:
        strat, _, _ = _make_strategy()
        lower, upper = strat.calculate_range(
            Decimal("100"), Decimal("0.80"),
        )
        assert lower > 0

    def test_zero_volatility(self) -> None:
        strat, _, _ = _make_strategy()
        lower, upper = strat.calculate_range(
            Decimal("2000"), Decimal("0"),
        )
        assert lower == Decimal("2000")
        assert upper == Decimal("2000")


# ---------------------------------------------------------------------------
# Order generation
# ---------------------------------------------------------------------------

class TestOrderGeneration:

    def test_new_mint_lp_order(self) -> None:
        strat, _, _ = _make_strategy()
        orders = strat.generate_orders(_sample_pools())
        assert len(orders) == 1
        order = orders[0]
        assert order["version"] == "1.0.0"
        assert "orderId" in order
        assert order["protocol"] == "uniswap_v3"
        assert order["action"] == "mint_lp"
        assert order["strategy"] == STRATEGY_ID
        assert "tokenIn" in order["params"]
        assert "lowerPrice" in order["params"]
        assert "upperPrice" in order["params"]

    def test_exit_on_high_il(self) -> None:
        strat, _, tracker = _make_strategy()
        tracker.open_position(
            strategy=STRATEGY_ID, protocol="uniswap_v3", chain="ethereum",
            asset="ETH", entry_price="2000", amount="1.0",
            position_id="uni-pos",
            protocol_data={
                "impermanent_loss_pct": "0.06",
                "in_range": True,
                "uncollected_fees_usd": "0",
            },
        )
        orders = strat.generate_orders(_sample_pools())
        assert len(orders) == 1
        assert orders[0]["action"] == "burn_lp"

    def test_collect_fees(self) -> None:
        strat, _, tracker = _make_strategy()
        tracker.open_position(
            strategy=STRATEGY_ID, protocol="uniswap_v3", chain="ethereum",
            asset="ETH", entry_price="2000", amount="1.0",
            position_id="uni-pos",
            protocol_data={
                "impermanent_loss_pct": "0.01",
                "in_range": True,
                "uncollected_fees_usd": "15",
            },
        )
        orders = strat.generate_orders(_sample_pools())
        assert len(orders) == 1
        assert orders[0]["action"] == "collect_fees"

    def test_rebalance_out_of_range(self) -> None:
        strat, _, tracker = _make_strategy()
        tracker.open_position(
            strategy=STRATEGY_ID, protocol="uniswap_v3", chain="ethereum",
            asset="ETH", entry_price="2000", amount="1.0",
            position_id="uni-pos",
            protocol_data={
                "impermanent_loss_pct": "0.01",
                "in_range": False,
                "uncollected_fees_usd": "0",
            },
        )
        orders = strat.generate_orders(_sample_pools())
        assert len(orders) == 2
        assert orders[0]["action"] == "burn_lp"
        assert orders[1]["action"] == "mint_lp"

    def test_no_orders_below_min_capital(self) -> None:
        config = UniswapV3LPConfig(min_position_value_usd=Decimal("999999"))
        strat, _, _ = _make_strategy(config=config)
        orders = strat.generate_orders(_sample_pools())
        assert len(orders) == 0

    def test_no_orders_on_empty_pools(self) -> None:
        strat, _, _ = _make_strategy()
        orders = strat.generate_orders([])
        assert len(orders) == 0

    def test_protocol_exposure_respected(self) -> None:
        positions = {
            "existing": {
                "value_usd": 4000, "protocol": "uniswap_v3",
                "asset": "ETH", "tier": 2,
            },
        }
        strat, _, _ = _make_strategy(positions=positions)
        orders = strat.generate_orders(_sample_pools())
        assert len(orders) == 0


# ---------------------------------------------------------------------------
# Strategy status
# ---------------------------------------------------------------------------

class TestStrategyStatus:

    def test_initial_status_evaluating(self) -> None:
        strat, _, _ = _make_strategy()
        assert strat.status == "evaluating"
