"""Smoke tests for LP-AERO-001 evaluate().

Loaded by `TemplateRegistry._run_smoke()` via the synthetic module name
`icarus.templates.lp_aero_001`. From W4 onwards, a failure here blocks
registration.

Pool state (tvl + fees_24h) comes from `market_data.pool_state` keyed
by `base:aerodrome:usdc-usdbc`. Fee yield is computed by the strategy
as `fees_24h * 365 / tvl`; the fixtures pick fees/tvl pairs that imply
the yield we want to test.
"""

from datetime import UTC, datetime
from decimal import Decimal

from icarus.types import MarketSnapshot, PoolState, PortfolioSnapshot, Position


_POOL_KEY = "base:aerodrome:usdc-usdbc"


def _mk_market(
    fee_yield_apr: float,
    tvl: float = 8_000_000,
    gas: float = 0.05,
) -> MarketSnapshot:
    """Build a MarketSnapshot with Aerodrome pool state populated.

    `fee_yield_apr` is the annualized fee yield we want the strategy
    to see (e.g. 0.15 = 15% APR). We back out fees_24h = yield * tvl / 365.
    """
    fees_24h = Decimal(str(fee_yield_apr)) * Decimal(str(tvl)) / Decimal("365")
    return MarketSnapshot(
        timestamp=datetime.now(UTC),
        chain="base",
        prices={"USDC": Decimal("1.0"), "ETH": Decimal("3500")},
        apys={},
        pool_state={
            _POOL_KEY: PoolState(
                pool_id=_POOL_KEY,
                tvl=Decimal(str(tvl)),
                depth=Decimal("250000"),
                fees_24h=fees_24h,
            )
        },
        gas_gwei=Decimal(str(gas)),
        metadata={},
    )


def _mk_portfolio(cash: float = 1000.0, with_position: bool = False) -> PortfolioSnapshot:
    positions = {}
    if with_position:
        positions["c-lp-existing"] = Position(
            candidate_id="c-lp-existing",
            template_id="LP-AERO-001",
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


def test_enter_when_fee_yield_above_threshold_and_tvl_ok():
    params = {
        "fee_yield_threshold": "0.10",
        "min_pool_tvl_usd": "2000000",
        "gas_amortization_days": "7",
    }
    d = evaluate(
        params, _mk_market(fee_yield_apr=0.18, tvl=8_000_000), _mk_portfolio(cash=1000)
    )
    assert d.action == "enter", f"expected enter, got {d.action}: {d.reasoning}"
    assert d.target_size == Decimal("1000")


def test_hold_when_fee_yield_below_threshold_and_no_position():
    params = {
        "fee_yield_threshold": "0.15",
        "min_pool_tvl_usd": "2000000",
    }
    d = evaluate(
        params, _mk_market(fee_yield_apr=0.08, tvl=8_000_000), _mk_portfolio(cash=1000)
    )
    assert d.action == "hold", f"expected hold, got {d.action}"
    assert "threshold" in d.reasoning


def test_exit_when_fee_yield_collapses_with_open_position():
    params = {
        "fee_yield_threshold": "0.10",
        "min_pool_tvl_usd": "2000000",
    }
    d = evaluate(
        params,
        _mk_market(fee_yield_apr=0.03, tvl=8_000_000),
        _mk_portfolio(with_position=True),
    )
    assert d.action == "exit", f"expected exit (yield), got {d.action}: {d.reasoning}"
    assert "fee yield" in d.reasoning


def test_exit_on_capital_flight_when_tvl_drops_below_floor():
    """Capital-flight signal: TVL drops below safety floor → exit even if
    fee yield (computed from stale fees_24h) still looks healthy."""
    params = {
        "fee_yield_threshold": "0.05",
        "min_pool_tvl_usd": "5000000",
    }
    # TVL is 1M (below 5M floor) but the implied yield is high — capital
    # flight signal must dominate.
    d = evaluate(
        params,
        _mk_market(fee_yield_apr=0.30, tvl=1_000_000),
        _mk_portfolio(with_position=True),
    )
    assert d.action == "exit", f"expected exit (TVL flight), got {d.action}: {d.reasoning}"
    assert "capital-flight" in d.reasoning


def test_hold_when_gas_amortization_exceeds_limit():
    # Massive gas relative to small yield → can't amortize round-trip in window.
    params = {
        "fee_yield_threshold": "0.05",
        "min_pool_tvl_usd": "2000000",
        "gas_amortization_days": "1",
    }
    d = evaluate(
        params,
        _mk_market(fee_yield_apr=0.06, tvl=8_000_000, gas=500),
        _mk_portfolio(cash=1000),
    )
    assert d.action == "hold", f"expected hold (gas-limited), got {d.action}: {d.reasoning}"
    assert "gas" in d.reasoning
