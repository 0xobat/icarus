"""Smoke tests for BASIS-SOL-DRIFT-001 evaluate().

Loaded by `TemplateRegistry._run_smoke()` via the synthetic module
name `icarus.templates.basis_sol_drift_001`. From W4 onwards, a
failure here blocks registration.

Funding-rate and streak data live under
`market_data.metadata["funding_rates" | "funding_streaks"]` keyed by
the Drift market id (e.g. `drift:sol-perp`), per the v2 metadata-as-
extension-hatch convention. Fixtures populate them directly.
"""

from datetime import UTC, datetime
from decimal import Decimal

from icarus.types import MarketSnapshot, PortfolioSnapshot, Position


_SOL_VENUE = "drift:sol-perp"
_ETH_VENUE = "drift:eth-perp"


def _mk_market(funding: float, streak: int = 12, venue: str = _SOL_VENUE) -> MarketSnapshot:
    """Build a MarketSnapshot with Drift funding-rate metadata populated.

    `funding` is the hourly funding fraction (e.g. 0.0001 = 0.01% / hr).
    `streak` is consecutive hourly periods at-or-above funding_threshold.
    `venue` keys into the metadata maps (default SOL-perp).
    """
    return MarketSnapshot(
        timestamp=datetime.now(UTC),
        chain="solana",
        prices={"SOL": Decimal("150"), "ETH": Decimal("3500"), "USDC": Decimal("1.0")},
        apys={},
        pool_state={},
        gas_gwei=Decimal("0"),
        metadata={
            "funding_rates": {venue: Decimal(str(funding))},
            "funding_streaks": {venue: streak},
        },
    )


def _mk_portfolio(cash: float = 1000.0, with_position: bool = False) -> PortfolioSnapshot:
    positions = {}
    if with_position:
        positions["c-basis-sol-existing"] = Position(
            candidate_id="c-basis-sol-existing",
            template_id="BASIS-SOL-DRIFT-001",
            asset="SOL",
            size_usd=Decimal("500"),
            entry_price=Decimal("150"),
            entry_time=datetime.now(UTC),
        )
    return PortfolioSnapshot(
        nav_usd=Decimal(str(cash + 500 if with_position else cash)),
        positions=positions,
        cash_usd=Decimal(str(cash)),
        drawdown_from_peak=Decimal("0"),
        last_rebalance=datetime.now(UTC),
    )


def test_enter_when_funding_qualifies_and_streak_satisfied():
    # 0.0001/hr = ~88% annualized — well above threshold of 0.00005/hr.
    # Streak of 12 hours >> min_streak of 6.
    params = {
        "funding_threshold": "0.00005",
        "exit_funding_threshold": "0.000005",
        "min_funding_streak": "6",
        "position_size_pct": "0.5",
        "asset_variant": "SOL",
    }
    d = evaluate(params, _mk_market(funding=0.0001, streak=12), _mk_portfolio(cash=1000))
    assert d.action == "enter", f"expected enter, got {d.action}: {d.reasoning}"
    # 0.5 of cash $1000 = $500 per leg.
    assert d.target_size == Decimal("500.0"), f"size {d.target_size}"


def test_hold_when_funding_below_threshold_and_no_position():
    params = {
        "funding_threshold": "0.00005",
        "exit_funding_threshold": "0.000005",
        "min_funding_streak": "6",
        "asset_variant": "SOL",
    }
    d = evaluate(params, _mk_market(funding=0.00001, streak=12), _mk_portfolio(cash=1000))
    assert d.action == "hold", f"expected hold, got {d.action}"
    assert "threshold" in d.reasoning


def test_hold_when_streak_insufficient_despite_high_funding():
    """One-tick funding spike shouldn't trigger entry — streak filter active."""
    params = {
        "funding_threshold": "0.00005",
        "exit_funding_threshold": "0.000005",
        "min_funding_streak": "12",
        "asset_variant": "SOL",
    }
    d = evaluate(params, _mk_market(funding=0.0005, streak=3), _mk_portfolio(cash=1000))
    assert d.action == "hold", f"expected hold (streak), got {d.action}"
    assert "streak" in d.reasoning


def test_exit_when_funding_drops_below_exit_threshold_with_open_position():
    params = {
        "funding_threshold": "0.00005",
        "exit_funding_threshold": "0.000005",
        "asset_variant": "SOL",
    }
    d = evaluate(
        params,
        _mk_market(funding=0.000001, streak=0),
        _mk_portfolio(with_position=True),
    )
    assert d.action == "exit", f"expected exit, got {d.action}: {d.reasoning}"


def test_eth_perp_variant_reads_eth_venue_key():
    """Asset variant routing: ETH variant must read drift:eth-perp funding,
    not drift:sol-perp. With ETH funding above threshold but SOL absent,
    the ETH variant should still enter."""
    params = {
        "funding_threshold": "0.00005",
        "exit_funding_threshold": "0.000005",
        "min_funding_streak": "6",
        "position_size_pct": "0.25",
        "asset_variant": "ETH",
    }
    d = evaluate(
        params,
        _mk_market(funding=0.0002, streak=12, venue=_ETH_VENUE),
        _mk_portfolio(cash=1000),
    )
    assert d.action == "enter", f"expected enter on ETH variant, got {d.action}: {d.reasoning}"
    assert "drift:eth-perp" in d.reasoning
    # 0.25 of $1000 = $250 per leg.
    assert d.target_size == Decimal("250.0"), f"size {d.target_size}"
