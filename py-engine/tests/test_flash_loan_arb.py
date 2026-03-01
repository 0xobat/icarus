"""Tests for flash loan arbitrage strategy — STRAT-005."""

from __future__ import annotations

from decimal import Decimal

from portfolio.allocator import AllocatorConfig, PortfolioAllocator
from portfolio.position_tracker import PositionTracker
from strategies.flash_loan_arb import (
    STRATEGY_ID,
    ArbOpportunity,
    FlashLoanArbConfig,
    FlashLoanArbStrategy,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_opp(
    asset: str = "WETH",
    source_dex: str = "uniswap_v3",
    target_dex: str = "sushiswap",
    source_price: str = "2000.00",
    target_price: str = "2012.00",
    price_spread_pct: str = "0.006",
    available_liquidity: str = "500",
    estimated_gas_cost_usd: str = "20",
    estimated_profit_usd: str = "50",
    chain: str = "ethereum",
) -> ArbOpportunity:
    return ArbOpportunity(
        asset=asset,
        source_dex=source_dex,
        target_dex=target_dex,
        source_price=Decimal(source_price),
        target_price=Decimal(target_price),
        price_spread_pct=Decimal(price_spread_pct),
        available_liquidity=Decimal(available_liquidity),
        estimated_gas_cost_usd=Decimal(estimated_gas_cost_usd),
        estimated_profit_usd=Decimal(estimated_profit_usd),
        chain=chain,
    )


def _sample_opportunities() -> list[ArbOpportunity]:
    return [
        _make_opp(estimated_profit_usd="50", price_spread_pct="0.006"),
        _make_opp(
            asset="WBTC", source_dex="curve", target_dex="balancer",
            estimated_profit_usd="120", price_spread_pct="0.008",
        ),
    ]


def _make_strategy(
    total_capital: str = "100000",
    positions: dict | None = None,
    config: FlashLoanArbConfig | None = None,
) -> tuple[FlashLoanArbStrategy, PortfolioAllocator, PositionTracker]:
    alloc = PortfolioAllocator(
        Decimal(total_capital),
        positions or {},
        AllocatorConfig(),
    )
    tracker = PositionTracker()
    strat = FlashLoanArbStrategy(alloc, tracker, config)
    return strat, alloc, tracker


# ---------------------------------------------------------------------------
# Opportunity evaluation
# ---------------------------------------------------------------------------

class TestArbEvaluation:

    def test_ranks_by_profit_descending(self) -> None:
        strat, _, _ = _make_strategy()
        ranked = strat.evaluate(_sample_opportunities())
        profits = [o.estimated_profit_usd for o in ranked]
        assert profits == sorted(profits, reverse=True)

    def test_filters_low_spread(self) -> None:
        strat, _, _ = _make_strategy()
        opps = [
            _make_opp(price_spread_pct="0.002"),  # below 0.5%
            _make_opp(price_spread_pct="0.006"),
        ]
        ranked = strat.evaluate(opps)
        assert len(ranked) == 1
        assert ranked[0].price_spread_pct == Decimal("0.006")

    def test_filters_low_profit_gas_ratio(self) -> None:
        strat, _, _ = _make_strategy()
        opps = [
            _make_opp(estimated_profit_usd="15", estimated_gas_cost_usd="20"),  # 0.75x
            _make_opp(estimated_profit_usd="50", estimated_gas_cost_usd="20"),  # 2.5x
        ]
        ranked = strat.evaluate(opps)
        assert len(ranked) == 1
        assert ranked[0].estimated_profit_usd == Decimal("50")

    def test_filters_zero_liquidity(self) -> None:
        strat, _, _ = _make_strategy()
        opps = [_make_opp(available_liquidity="0")]
        ranked = strat.evaluate(opps)
        assert len(ranked) == 0

    def test_filters_zero_gas_cost(self) -> None:
        strat, _, _ = _make_strategy()
        opps = [_make_opp(estimated_gas_cost_usd="0")]
        ranked = strat.evaluate(opps)
        assert len(ranked) == 0

    def test_filters_zero_profit(self) -> None:
        strat, _, _ = _make_strategy()
        opps = [_make_opp(estimated_profit_usd="0")]
        ranked = strat.evaluate(opps)
        assert len(ranked) == 0

    def test_empty_input(self) -> None:
        strat, _, _ = _make_strategy()
        assert strat.evaluate([]) == []


# ---------------------------------------------------------------------------
# Action threshold
# ---------------------------------------------------------------------------

class TestShouldAct:

    def test_good_conditions_trigger_action(self) -> None:
        strat, _, _ = _make_strategy()
        opp = _make_opp()
        assert strat.should_act(opp, current_gas_gwei=Decimal("30"))

    def test_high_gas_blocks_action(self) -> None:
        strat, _, _ = _make_strategy()
        opp = _make_opp()
        assert not strat.should_act(opp, current_gas_gwei=Decimal("150"))

    def test_low_spread_blocks_action(self) -> None:
        strat, _, _ = _make_strategy()
        opp = _make_opp(price_spread_pct="0.003")
        assert not strat.should_act(opp)

    def test_low_profit_gas_ratio_blocks_action(self) -> None:
        strat, _, _ = _make_strategy()
        opp = _make_opp(estimated_profit_usd="15", estimated_gas_cost_usd="20")
        assert not strat.should_act(opp)

    def test_zero_gas_cost_blocks_action(self) -> None:
        strat, _, _ = _make_strategy()
        opp = _make_opp(estimated_gas_cost_usd="0")
        assert not strat.should_act(opp)


# ---------------------------------------------------------------------------
# Order generation
# ---------------------------------------------------------------------------

class TestOrderGeneration:

    def test_generates_flash_loan_order(self) -> None:
        strat, _, _ = _make_strategy()
        orders = strat.generate_orders(_sample_opportunities())
        assert len(orders) == 1
        order = orders[0]
        assert order["version"] == "1.0.0"
        assert "orderId" in order
        assert "correlationId" in order
        assert order["protocol"] == "aave_v3"
        assert order["action"] == "flash_loan"
        assert order["strategy"] == STRATEGY_ID
        assert order["priority"] == "urgent"
        assert order["useFlashbotsProtect"] is True
        assert "tokenIn" in order["params"]
        assert "amount" in order["params"]
        assert "sourceDex" in order["params"]
        assert "targetDex" in order["params"]
        assert "maxGasWei" in order["limits"]
        assert "maxSlippageBps" in order["limits"]
        assert "deadlineUnix" in order["limits"]

    def test_picks_most_profitable(self) -> None:
        strat, _, _ = _make_strategy()
        orders = strat.generate_orders(_sample_opportunities())
        assert len(orders) == 1
        # WBTC opportunity has $120 profit vs WETH $50
        assert orders[0]["params"]["tokenIn"] == "WBTC"

    def test_no_orders_when_all_filtered(self) -> None:
        strat, _, _ = _make_strategy()
        opps = [_make_opp(price_spread_pct="0.001")]  # too low
        orders = strat.generate_orders(opps)
        assert len(orders) == 0

    def test_no_orders_on_empty_input(self) -> None:
        strat, _, _ = _make_strategy()
        orders = strat.generate_orders([])
        assert len(orders) == 0

    def test_no_orders_below_min_position_value(self) -> None:
        config = FlashLoanArbConfig(min_position_value_usd=Decimal("999999"))
        strat, _, _ = _make_strategy(config=config)
        orders = strat.generate_orders(_sample_opportunities())
        assert len(orders) == 0

    def test_correlation_id_passthrough(self) -> None:
        strat, _, _ = _make_strategy()
        orders = strat.generate_orders(
            _sample_opportunities(), correlation_id="test-cid-123",
        )
        assert len(orders) == 1
        assert orders[0]["correlationId"] == "test-cid-123"

    def test_protocol_exposure_respected(self) -> None:
        positions = {
            "existing": {
                "value_usd": 40000, "protocol": "aave_v3",
                "asset": "WETH", "tier": 3,
            },
        }
        strat, _, _ = _make_strategy(positions=positions)
        orders = strat.generate_orders(_sample_opportunities())
        assert len(orders) == 0

    def test_caps_flash_loan_amount(self) -> None:
        config = FlashLoanArbConfig(max_flash_loan_eth=Decimal("100"))
        strat, _, _ = _make_strategy(config=config)
        opps = [_make_opp(available_liquidity="500")]
        orders = strat.generate_orders(opps)
        if orders:
            amount = Decimal(orders[0]["params"]["amount"])
            assert amount <= Decimal("100")


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
