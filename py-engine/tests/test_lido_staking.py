"""Tests for Lido liquid staking strategy — STRAT-002."""

from __future__ import annotations

from decimal import Decimal

from portfolio.allocator import AllocatorConfig, PortfolioAllocator
from portfolio.position_tracker import PositionTracker
from strategies.lido_staking import (
    STRATEGY_ID,
    LidoStakingConfig,
    LidoStakingData,
    LidoStakingStrategy,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_staking_data(
    staking_apr: str = "0.040",
    steth_eth_ratio: str = "1.000",
    total_staked: str = "5000000",
    available_eth: str = "10",
    chain: str = "ethereum",
) -> LidoStakingData:
    return LidoStakingData(
        staking_apr=Decimal(staking_apr),
        steth_eth_ratio=Decimal(steth_eth_ratio),
        total_staked=Decimal(total_staked),
        available_eth=Decimal(available_eth),
        chain=chain,
    )


def _sample_staking_data() -> list[LidoStakingData]:
    return [
        _make_staking_data("0.040"),
        _make_staking_data("0.035", chain="sepolia"),
    ]


def _make_strategy(
    total_capital: str = "10000",
    positions: dict | None = None,
    config: LidoStakingConfig | None = None,
) -> tuple[LidoStakingStrategy, PortfolioAllocator, PositionTracker]:
    alloc = PortfolioAllocator(
        Decimal(total_capital),
        positions or {},
        AllocatorConfig(),
    )
    tracker = PositionTracker()
    strat = LidoStakingStrategy(alloc, tracker, config)
    return strat, alloc, tracker


# ---------------------------------------------------------------------------
# Market evaluation
# ---------------------------------------------------------------------------

class TestStakingEvaluation:

    def test_ranks_by_apr_descending(self) -> None:
        strat, _, _ = _make_strategy()
        ranked = strat.evaluate(_sample_staking_data())
        aprs = [d.staking_apr for d in ranked]
        assert aprs == sorted(aprs, reverse=True)

    def test_filters_low_apr(self) -> None:
        strat, _, _ = _make_strategy()
        data = [
            _make_staking_data("0.020"),  # below 3% threshold
            _make_staking_data("0.040"),
        ]
        ranked = strat.evaluate(data)
        assert len(ranked) == 1
        assert ranked[0].staking_apr == Decimal("0.040")

    def test_filters_depegged(self) -> None:
        strat, _, _ = _make_strategy()
        data = [
            _make_staking_data(steth_eth_ratio="0.950"),  # 5% depeg
            _make_staking_data("0.040"),
        ]
        ranked = strat.evaluate(data)
        assert len(ranked) == 1

    def test_filters_zero_staked(self) -> None:
        strat, _, _ = _make_strategy()
        data = [_make_staking_data(total_staked="0")]
        ranked = strat.evaluate(data)
        assert len(ranked) == 0

    def test_empty_data(self) -> None:
        strat, _, _ = _make_strategy()
        assert strat.evaluate([]) == []


# ---------------------------------------------------------------------------
# Action threshold
# ---------------------------------------------------------------------------

class TestShouldAct:

    def test_good_apr_triggers_action(self) -> None:
        strat, _, _ = _make_strategy()
        assert strat.should_act(Decimal("0.040"), Decimal("1.000"))

    def test_low_apr_triggers_exit(self) -> None:
        strat, _, _ = _make_strategy()
        assert strat.should_act(Decimal("0.015"), Decimal("1.000"))

    def test_depeg_triggers_exit(self) -> None:
        strat, _, _ = _make_strategy()
        assert strat.should_act(Decimal("0.040"), Decimal("0.970"))

    def test_mid_apr_no_action(self) -> None:
        strat, _, _ = _make_strategy()
        # APR between exit (2%) and entry (3%), no depeg
        assert not strat.should_act(Decimal("0.025"), Decimal("1.000"))


# ---------------------------------------------------------------------------
# Order generation
# ---------------------------------------------------------------------------

class TestOrderGeneration:

    def test_new_stake_order(self) -> None:
        strat, _, _ = _make_strategy()
        orders = strat.generate_orders(_sample_staking_data())
        assert len(orders) == 1
        order = orders[0]
        assert order["version"] == "1.0.0"
        assert "orderId" in order
        assert "correlationId" in order
        assert order["protocol"] == "lido"
        assert order["action"] == "stake"
        assert order["strategy"] == STRATEGY_ID
        assert "tokenIn" in order["params"]
        assert "amount" in order["params"]
        assert "maxGasWei" in order["limits"]

    def test_unstake_on_depeg(self) -> None:
        strat, _, tracker = _make_strategy()
        tracker.open_position(
            strategy=STRATEGY_ID, protocol="lido", chain="ethereum",
            asset="stETH", entry_price="2000", amount="2.0",
            position_id="lido-pos",
            protocol_data={
                "staking_apr": "0.040",
                "steth_eth_ratio": "0.960",  # 4% depeg
            },
        )
        orders = strat.generate_orders(_sample_staking_data())
        assert len(orders) == 1
        assert orders[0]["action"] == "unstake"

    def test_unstake_on_low_apr(self) -> None:
        strat, _, tracker = _make_strategy()
        tracker.open_position(
            strategy=STRATEGY_ID, protocol="lido", chain="ethereum",
            asset="stETH", entry_price="2000", amount="2.0",
            position_id="lido-pos",
            protocol_data={
                "staking_apr": "0.015",
                "steth_eth_ratio": "1.000",
            },
        )
        orders = strat.generate_orders(_sample_staking_data())
        assert len(orders) == 1
        assert orders[0]["action"] == "unstake"

    def test_no_orders_when_position_healthy(self) -> None:
        strat, _, tracker = _make_strategy()
        tracker.open_position(
            strategy=STRATEGY_ID, protocol="lido", chain="ethereum",
            asset="stETH", entry_price="2000", amount="2.0",
            position_id="lido-pos",
            protocol_data={
                "staking_apr": "0.040",
                "steth_eth_ratio": "1.000",
            },
        )
        orders = strat.generate_orders(_sample_staking_data())
        assert len(orders) == 0

    def test_no_orders_below_min_capital(self) -> None:
        config = LidoStakingConfig(min_position_value_usd=Decimal("999999"))
        strat, _, _ = _make_strategy(config=config)
        orders = strat.generate_orders(_sample_staking_data())
        assert len(orders) == 0

    def test_no_orders_on_empty_data(self) -> None:
        strat, _, _ = _make_strategy()
        orders = strat.generate_orders([])
        assert len(orders) == 0

    def test_protocol_exposure_respected(self) -> None:
        positions = {
            "existing": {
                "value_usd": 4000, "protocol": "lido",
                "asset": "stETH", "tier": 1,
            },
        }
        strat, _, tracker = _make_strategy(positions=positions)
        orders = strat.generate_orders(_sample_staking_data())
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
