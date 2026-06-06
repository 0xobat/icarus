"""Unit tests for the managed exposure checker (P2.5).

Position-aware per-asset + per-venue concentration caps, evaluated on the
prospective post-trade holdings threaded through RiskContext.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from decision_engine.risk.managed_exposure import (
    ManagedExposureChecker,
    ManagedExposureConfig,
)
from decision_engine.risk_gate import RiskContext
from icarus.envelopes.orders import ExecutionOrder, OrderLimits, OrderParams
from icarus.types import MarketSnapshot, PortfolioSnapshot


def _order() -> ExecutionOrder:
    return ExecutionOrder(
        order_id="order123", correlation_id="c", timestamp=datetime(2026, 6, 6, tzinfo=UTC),
        chain="base", protocol="aerodrome", action="swap", strategy="REBAL:base",
        params=OrderParams(amount=Decimal("1")),
        limits=OrderLimits(max_slippage_bps=50, deadline_unix=1_900_000_000),
    )


def _ctx(
    prospective: dict[str, Decimal] | None,
    venues: dict[str, str] | None = None,
) -> RiskContext:
    market = MarketSnapshot(
        timestamp=datetime(2026, 6, 6, tzinfo=UTC), chain="base",
        prices={"ETH": Decimal("3000")}, apys={}, pool_state={},
        gas_gwei=Decimal("1"), metadata={},
    )
    portfolio = PortfolioSnapshot(
        nav_usd=Decimal("10000"), positions={}, cash_usd=Decimal("4000"),
        drawdown_from_peak=Decimal("0"), last_rebalance=datetime(2026, 6, 6, tzinfo=UTC),
    )
    return RiskContext(
        portfolio=portfolio, market=market,
        prospective_holdings=prospective, venue_by_asset=venues,
    )


_CONFIG = ManagedExposureConfig(max_asset_pct=Decimal("0.60"), max_venue_pct=Decimal("0.25"))


def test_within_caps_passes() -> None:
    checker = ManagedExposureChecker(_CONFIG)
    ctx = _ctx({"USDC": Decimal("4000"), "WETH": Decimal("3200"), "cbBTC": Decimal("2800")})
    assert checker.check(_order(), ctx).passed


def test_asset_over_cap_rejects() -> None:
    checker = ManagedExposureChecker(_CONFIG)
    # WETH 6500/10000 = 0.65 > 0.60 cap → reject.
    ctx = _ctx({"USDC": Decimal("1500"), "WETH": Decimal("6500"), "cbBTC": Decimal("2000")})
    decision = checker.check(_order(), ctx)
    assert not decision.passed
    assert "WETH" in decision.reason
    assert decision.checker == "managed_exposure"


def test_asset_at_upper_band_passes() -> None:
    # USDC at its 0.50 upper band must pass (cap 0.60 ≥ upper band → never blocks).
    checker = ManagedExposureChecker(_CONFIG)
    ctx = _ctx({"USDC": Decimal("5000"), "WETH": Decimal("3000"), "cbBTC": Decimal("2000")})
    assert checker.check(_order(), ctx).passed


def test_absent_holdings_passes() -> None:
    checker = ManagedExposureChecker(_CONFIG)
    assert checker.check(_order(), _ctx(None)).passed


def test_zero_nav_passes() -> None:
    checker = ManagedExposureChecker(_CONFIG)
    ctx = _ctx({"USDC": Decimal("0"), "WETH": Decimal("0")})
    assert checker.check(_order(), ctx).passed


def test_core_lending_venue_not_capped() -> None:
    # USDC + cbBTC on Aave = 0.68 of NAV, but aave_v3 is NOT in capped_venues →
    # governed by allocation bands, not the venue cap → pass.
    checker = ManagedExposureChecker(_CONFIG, capped_venues=frozenset({"aerodrome_lp"}))
    ctx = _ctx(
        {"USDC": Decimal("4000"), "cbBTC": Decimal("2800"), "WETH": Decimal("3200")},
        venues={"USDC": "aave_v3", "cbBTC": "aave_v3", "WETH": "wallet"},
    )
    assert checker.check(_order(), ctx).passed


def test_capped_venue_over_cap_rejects() -> None:
    # The LP overlay venue holds 0.30 of NAV > 0.25 cap → reject.
    checker = ManagedExposureChecker(_CONFIG, capped_venues=frozenset({"aerodrome_lp"}))
    ctx = _ctx(
        {"USDC": Decimal("4000"), "LP": Decimal("3000"), "WETH": Decimal("3000")},
        venues={"USDC": "aave_v3", "LP": "aerodrome_lp", "WETH": "wallet"},
    )
    decision = checker.check(_order(), ctx)
    assert not decision.passed
    assert "aerodrome_lp" in decision.reason
