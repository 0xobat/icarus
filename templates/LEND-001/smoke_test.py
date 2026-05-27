"""Smoke tests for LEND-001 evaluate().

Loaded by `TemplateRegistry._run_smoke()` via the synthetic module name
`icarus.templates.lend_001`. From W4 onwards (per blueprint), a failure
here blocks registration; in W1-W3 it logs a warning.
"""

from datetime import UTC, datetime
from decimal import Decimal

from icarus.types import MarketSnapshot, PortfolioSnapshot, PoolState, Position



def _mk_market(apy: float, tvl: float = 5_000_000, gas: float = 0.05) -> MarketSnapshot:
    return MarketSnapshot(
        timestamp=datetime.now(UTC),
        chain="base",
        prices={"USDC": Decimal("1.0"), "ETH": Decimal("3500")},
        apys={"aave_v3.usdc.base": Decimal(str(apy))},
        pool_state={
            "base:aave_v3:usdc": PoolState(
                pool_id="base:aave_v3:usdc",
                tvl=Decimal(str(tvl)),
                depth=Decimal("100000"),
                fees_24h=Decimal("0"),
            )
        },
        gas_gwei=Decimal(str(gas)),
        metadata={},
    )


def _mk_portfolio(cash: float = 1000.0, with_position: bool = False) -> PortfolioSnapshot:
    positions = {}
    if with_position:
        positions["c-existing"] = Position(
            candidate_id="c-existing",
            template_id="LEND-001",
            asset="USDC",
            size_usd=Decimal("500"),
            entry_price=Decimal("1.0"),
            entry_time=datetime.now(UTC),
        )
    return PortfolioSnapshot(
        nav_usd=Decimal(str(cash + 500 if with_position else cash)),
        positions=positions,
        cash_usd=Decimal(str(cash)),
        drawdown_from_peak=Decimal("0"),
        last_rebalance=datetime.now(UTC),
    )


def test_enter_when_apy_above_threshold_and_tvl_ok():
    params = {"apy_threshold": "0.04", "min_liquidity_usd": "1000000"}
    d = evaluate(params, _mk_market(apy=0.06, tvl=5_000_000), _mk_portfolio(cash=1000))
    assert d.action == "enter", f"expected enter, got {d.action}: {d.reasoning}"
    assert d.target_size == Decimal("1000")


def test_hold_when_apy_below_threshold_and_no_position():
    params = {"apy_threshold": "0.05"}
    d = evaluate(params, _mk_market(apy=0.03), _mk_portfolio(cash=1000))
    assert d.action == "hold", f"expected hold, got {d.action}"


def test_exit_when_apy_drops_below_threshold_with_open_position():
    params = {"apy_threshold": "0.05"}
    d = evaluate(params, _mk_market(apy=0.02), _mk_portfolio(with_position=True))
    assert d.action == "exit", f"expected exit, got {d.action}"


def test_hold_when_tvl_below_min_liquidity():
    params = {"apy_threshold": "0.04", "min_liquidity_usd": "10000000"}
    d = evaluate(params, _mk_market(apy=0.06, tvl=2_000_000), _mk_portfolio(cash=1000))
    assert d.action == "hold", f"expected hold (tvl-limited), got {d.action}"
    assert "tvl" in d.reasoning


def test_hold_when_gas_amortization_exceeds_limit():
    # Massive gas relative to tiny APY → can't amortize.
    params = {"apy_threshold": "0.011", "gas_amortization_days": "1"}
    d = evaluate(params, _mk_market(apy=0.012, gas=200), _mk_portfolio(cash=1000))
    assert d.action == "hold", f"expected hold (gas-limited), got {d.action}"
    assert "gas" in d.reasoning
