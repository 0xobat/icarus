"""Tests for lending rate arbitrage strategy — STRAT-006."""

from __future__ import annotations

from decimal import Decimal

from portfolio.allocator import AllocatorConfig, PortfolioAllocator
from portfolio.position_tracker import PositionTracker
from strategies.rate_arb import (
    STRATEGY_ID,
    LendingRate,
    RateArbConfig,
    RateArbStrategy,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_rate(
    protocol: str = "aave_v3",
    asset: str = "USDC",
    supply_apy: str = "0.040",
    borrow_apy: str = "0.050",
    available_liquidity: str = "1000000",
    utilization_rate: str = "0.80",
    chain: str = "ethereum",
) -> LendingRate:
    return LendingRate(
        protocol=protocol,
        asset=asset,
        supply_apy=Decimal(supply_apy),
        borrow_apy=Decimal(borrow_apy),
        available_liquidity=Decimal(available_liquidity),
        utilization_rate=Decimal(utilization_rate),
        chain=chain,
    )


def _sample_rates_with_spread() -> list[LendingRate]:
    """Two protocols, same asset. compound borrow=2%, aave supply=4% → 2% spread."""
    return [
        _make_rate(protocol="compound", borrow_apy="0.020", supply_apy="0.025"),
        _make_rate(protocol="aave_v3", borrow_apy="0.045", supply_apy="0.040"),
    ]


def _sample_rates_no_spread() -> list[LendingRate]:
    """Two protocols with negligible spread."""
    return [
        _make_rate(protocol="compound", borrow_apy="0.040", supply_apy="0.035"),
        _make_rate(protocol="aave_v3", borrow_apy="0.041", supply_apy="0.036"),
    ]


def _make_strategy(
    total_capital: str = "100000",
    positions: dict | None = None,
    config: RateArbConfig | None = None,
) -> tuple[RateArbStrategy, PortfolioAllocator, PositionTracker]:
    alloc = PortfolioAllocator(
        Decimal(total_capital),
        positions or {},
        AllocatorConfig(),
    )
    tracker = PositionTracker()
    strat = RateArbStrategy(alloc, tracker, config)
    return strat, alloc, tracker


# ---------------------------------------------------------------------------
# Opportunity finding
# ---------------------------------------------------------------------------

class TestFindOpportunities:

    def test_finds_cross_protocol_spread(self) -> None:
        strat, _, _ = _make_strategy()
        opps = strat.find_opportunities(_sample_rates_with_spread())
        assert len(opps) >= 1
        best = opps[0]
        assert best.spread >= Decimal("0.010")
        assert best.borrow_protocol == "compound"
        assert best.supply_protocol == "aave_v3"

    def test_ranks_by_spread_descending(self) -> None:
        strat, _, _ = _make_strategy()
        rates = [
            _make_rate(protocol="compound", borrow_apy="0.020", supply_apy="0.025"),
            _make_rate(protocol="aave_v3", borrow_apy="0.035", supply_apy="0.040"),
            _make_rate(protocol="morpho", borrow_apy="0.015", supply_apy="0.045"),
        ]
        opps = strat.find_opportunities(rates)
        spreads = [o.spread for o in opps]
        assert spreads == sorted(spreads, reverse=True)

    def test_filters_low_spread(self) -> None:
        strat, _, _ = _make_strategy()
        opps = strat.find_opportunities(_sample_rates_no_spread())
        # All spreads below 1% min
        assert len(opps) == 0

    def test_needs_two_protocols(self) -> None:
        strat, _, _ = _make_strategy()
        opps = strat.find_opportunities([_make_rate()])
        assert len(opps) == 0

    def test_filters_zero_liquidity(self) -> None:
        strat, _, _ = _make_strategy()
        rates = [
            _make_rate(protocol="compound", borrow_apy="0.020", available_liquidity="0"),
            _make_rate(protocol="aave_v3", supply_apy="0.040"),
        ]
        opps = strat.find_opportunities(rates)
        assert len(opps) == 0

    def test_same_protocol_not_paired(self) -> None:
        strat, _, _ = _make_strategy()
        rates = [
            _make_rate(protocol="aave_v3", borrow_apy="0.020"),
            _make_rate(protocol="aave_v3", borrow_apy="0.025"),
        ]
        opps = strat.find_opportunities(rates)
        assert len(opps) == 0

    def test_empty_input(self) -> None:
        strat, _, _ = _make_strategy()
        assert strat.find_opportunities([]) == []

    def test_evaluate_aliases_find_opportunities(self) -> None:
        strat, _, _ = _make_strategy()
        rates = _sample_rates_with_spread()
        assert strat.evaluate(rates) == strat.find_opportunities(rates)


# ---------------------------------------------------------------------------
# Action threshold
# ---------------------------------------------------------------------------

class TestShouldAct:

    def test_attractive_spread_triggers_entry(self) -> None:
        strat, _, _ = _make_strategy()
        assert strat.should_act(Decimal("0.020"))

    def test_compressed_spread_triggers_unwind(self) -> None:
        strat, _, _ = _make_strategy()
        assert strat.should_act(Decimal("0.003"))  # below 0.5% exit

    def test_low_health_factor_triggers_unwind(self) -> None:
        strat, _, _ = _make_strategy()
        assert strat.should_act(Decimal("0.020"), health_factor=Decimal("1.2"))

    def test_mid_spread_no_action(self) -> None:
        strat, _, _ = _make_strategy()
        # Between exit (0.5%) and entry (1.0%)
        assert not strat.should_act(Decimal("0.007"))

    def test_exact_min_spread_triggers(self) -> None:
        strat, _, _ = _make_strategy()
        assert strat.should_act(Decimal("0.010"))


# ---------------------------------------------------------------------------
# Order generation — new positions
# ---------------------------------------------------------------------------

class TestOrderGenerationEntry:

    def test_generates_two_orders_for_entry(self) -> None:
        strat, _, _ = _make_strategy()
        orders = strat.generate_orders(_sample_rates_with_spread())
        assert len(orders) == 2
        # Both supply actions (borrow on one, supply on other)
        actions = {o["action"] for o in orders}
        assert actions == {"supply"}

    def test_order_schema_compliance(self) -> None:
        strat, _, _ = _make_strategy()
        orders = strat.generate_orders(_sample_rates_with_spread())
        for order in orders:
            assert order["version"] == "1.0.0"
            assert "orderId" in order
            assert "correlationId" in order
            assert order["strategy"] == STRATEGY_ID
            assert "tokenIn" in order["params"]
            assert "amount" in order["params"]
            assert "maxGasWei" in order["limits"]
            assert "deadlineUnix" in order["limits"]

    def test_no_orders_when_no_spread(self) -> None:
        strat, _, _ = _make_strategy()
        orders = strat.generate_orders(_sample_rates_no_spread())
        assert len(orders) == 0

    def test_no_orders_on_empty_input(self) -> None:
        strat, _, _ = _make_strategy()
        orders = strat.generate_orders([])
        assert len(orders) == 0

    def test_no_orders_below_min_capital(self) -> None:
        config = RateArbConfig(min_position_value_usd=Decimal("999999"))
        strat, _, _ = _make_strategy(config=config)
        orders = strat.generate_orders(_sample_rates_with_spread())
        assert len(orders) == 0

    def test_correlation_id_passthrough(self) -> None:
        strat, _, _ = _make_strategy()
        orders = strat.generate_orders(
            _sample_rates_with_spread(), correlation_id="test-cid-456",
        )
        for order in orders:
            assert order["correlationId"] == "test-cid-456"

    def test_protocol_exposure_respected(self) -> None:
        positions = {
            "existing": {
                "value_usd": 40000, "protocol": "aave_v3",
                "asset": "USDC", "tier": 3,
            },
        }
        strat, _, _ = _make_strategy(positions=positions)
        orders = strat.generate_orders(_sample_rates_with_spread())
        assert len(orders) == 0


# ---------------------------------------------------------------------------
# Order generation — unwind existing positions
# ---------------------------------------------------------------------------

class TestOrderGenerationUnwind:

    def test_unwind_on_spread_compression(self) -> None:
        strat, _, tracker = _make_strategy()
        tracker.open_position(
            strategy=STRATEGY_ID, protocol="aave_v3", chain="ethereum",
            asset="USDC", entry_price="1", amount="5000",
            position_id="rate-arb-pos",
            protocol_data={
                "current_spread": "0.003",
                "health_factor": "2.0",
                "supply_protocol": "aave_v3",
                "borrow_protocol": "compound",
            },
        )
        orders = strat.generate_orders(_sample_rates_with_spread())
        assert len(orders) == 2
        assert all(o["action"] == "withdraw" for o in orders)

    def test_unwind_on_low_health_factor(self) -> None:
        strat, _, tracker = _make_strategy()
        tracker.open_position(
            strategy=STRATEGY_ID, protocol="aave_v3", chain="ethereum",
            asset="USDC", entry_price="1", amount="5000",
            position_id="rate-arb-pos",
            protocol_data={
                "current_spread": "0.020",
                "health_factor": "1.2",
                "supply_protocol": "aave_v3",
                "borrow_protocol": "compound",
            },
        )
        orders = strat.generate_orders(_sample_rates_with_spread())
        assert len(orders) == 2
        assert all(o["action"] == "withdraw" for o in orders)

    def test_no_action_when_position_healthy(self) -> None:
        strat, _, tracker = _make_strategy()
        tracker.open_position(
            strategy=STRATEGY_ID, protocol="aave_v3", chain="ethereum",
            asset="USDC", entry_price="1", amount="5000",
            position_id="rate-arb-pos",
            protocol_data={
                "current_spread": "0.020",
                "health_factor": "2.0",
                "supply_protocol": "aave_v3",
                "borrow_protocol": "compound",
            },
        )
        orders = strat.generate_orders(_sample_rates_with_spread())
        assert len(orders) == 0


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
