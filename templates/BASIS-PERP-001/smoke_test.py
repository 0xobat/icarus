"""Smoke tests for BASIS-PERP-001 evaluate().

Loaded by `TemplateRegistry._run_smoke()` via the synthetic module
name `icarus.templates.basis_perp_001`. From W4 onwards, a failure
here blocks registration; in W1-W3 it logs a warning.

The funding-rate and streak data live under
`market_data.metadata["funding_rates" | "funding_streaks"]` per the
v2 metadata-as-extension-hatch convention; the smoke test fixtures
populate them directly.
"""

from datetime import UTC, datetime
from decimal import Decimal

from icarus.types import MarketSnapshot, PortfolioSnapshot, Position


_VENUE_KEY = "synthetix_perps_base:eth-usd"


def _mk_market(funding: float, streak: int = 6) -> MarketSnapshot:
    """Build a MarketSnapshot with funding-rate metadata populated.

    `funding` is the 8-hour funding fraction (e.g. 0.0001 = 0.01% / 8h).
    `streak` is consecutive periods at-or-above funding_threshold.
    """
    return MarketSnapshot(
        timestamp=datetime.now(UTC),
        chain="base",
        prices={"ETH": Decimal("3500"), "USDC": Decimal("1.0")},
        apys={},
        pool_state={},
        gas_gwei=Decimal("0.05"),
        metadata={
            "funding_rates": {_VENUE_KEY: Decimal(str(funding))},
            "funding_streaks": {_VENUE_KEY: streak},
        },
    )


def _mk_portfolio(cash: float = 1000.0, with_position: bool = False) -> PortfolioSnapshot:
    positions = {}
    if with_position:
        positions["c-basis-existing"] = Position(
            candidate_id="c-basis-existing",
            template_id="BASIS-PERP-001",
            asset="ETH",
            size_usd=Decimal("500"),
            entry_price=Decimal("3500"),
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
    # 0.02% / 8h = ~22% annualized funding, well above 0.01% threshold;
    # streak of 6 periods >> min_streak of 3.
    params = {
        "funding_threshold": "0.0001",
        "exit_funding_threshold": "0.00001",
        "min_funding_streak": "3",
        "position_size_pct": "0.5",
    }
    d = evaluate(params, _mk_market(funding=0.0002, streak=6), _mk_portfolio(cash=1000))
    assert d.action == "enter", f"expected enter, got {d.action}: {d.reasoning}"
    # 0.5 of cash $1000 = $500 per leg.
    assert d.target_size == Decimal("500.0"), f"size {d.target_size}"


def test_hold_when_funding_below_threshold_and_no_position():
    params = {
        "funding_threshold": "0.0001",
        "exit_funding_threshold": "0.00001",
        "min_funding_streak": "3",
    }
    d = evaluate(params, _mk_market(funding=0.00005, streak=6), _mk_portfolio(cash=1000))
    assert d.action == "hold", f"expected hold, got {d.action}"
    assert "threshold" in d.reasoning


def test_hold_when_streak_insufficient_despite_high_funding():
    """One-tick funding spike shouldn't trigger entry — streak filter active."""
    params = {
        "funding_threshold": "0.0001",
        "exit_funding_threshold": "0.00001",
        "min_funding_streak": "6",
    }
    d = evaluate(params, _mk_market(funding=0.0010, streak=2), _mk_portfolio(cash=1000))
    assert d.action == "hold", f"expected hold (streak), got {d.action}"
    assert "streak" in d.reasoning


def test_exit_when_funding_drops_below_exit_threshold_with_open_position():
    params = {
        "funding_threshold": "0.0001",
        "exit_funding_threshold": "0.00001",
    }
    d = evaluate(
        params, _mk_market(funding=0.000005, streak=0), _mk_portfolio(with_position=True)
    )
    assert d.action == "exit", f"expected exit, got {d.action}: {d.reasoning}"


def test_hold_with_open_position_when_funding_still_above_exit_threshold():
    """Hysteresis: funding between exit_threshold and entry_threshold should HOLD,
    not flip-flop out and back in."""
    params = {
        "funding_threshold": "0.0002",
        "exit_funding_threshold": "0.00005",
    }
    # Funding at 0.0001 — above exit but below re-entry. Should hold the open trade.
    d = evaluate(
        params, _mk_market(funding=0.0001, streak=10), _mk_portfolio(with_position=True)
    )
    assert d.action == "hold", f"expected hold (hysteresis band), got {d.action}"
