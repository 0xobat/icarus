"""Unit tests for the portfolio health monitor (managed-portfolio P3.2)."""

from __future__ import annotations

from decimal import Decimal

from decision_engine.health_monitor import DeRiskAction, PortfolioHealthMonitor
from decision_engine.risk.depeg_breaker import DepegBreaker
from decision_engine.risk.lst_depeg_breaker import LstDepegBreaker

_HOLDINGS = {"USDC": Decimal("4000"), "wstETH": Decimal("3200"), "cbBTC": Decimal("2800")}


def _monitor(*, usdc_tripped: bool = False, wsteth_tripped: bool = False):
    usdc = DepegBreaker(threshold_bps=100)
    if usdc_tripped:
        usdc.update(Decimal("0.95"))  # 500 bps off-peg
    wsteth = LstDepegBreaker(threshold_bps=200, sustain=1)
    if wsteth_tripped:
        wsteth.update(market_price=Decimal("3400"), fair_price=Decimal("3540"))
    return PortfolioHealthMonitor(usdc_depeg=usdc, lst_breakers={"wstETH": wsteth})


def test_healthy_portfolio_no_actions() -> None:
    monitor = _monitor()
    assert monitor.assess(holdings=_HOLDINGS) == []


def test_usdc_depeg_halts_all() -> None:
    monitor = _monitor(usdc_tripped=True)
    actions = monitor.assess(holdings=_HOLDINGS)
    assert any(a.kind == "halt_all" for a in actions)
    # halt_all is the most severe → first.
    assert actions[0].kind == "halt_all"


def test_lst_depeg_exits_held_position() -> None:
    monitor = _monitor(wsteth_tripped=True)
    actions = monitor.assess(holdings=_HOLDINGS)
    assert DeRiskAction(kind="exit_position", reason=actions[0].reason, asset="wstETH") in actions
    exits = [a for a in actions if a.kind == "exit_position"]
    assert len(exits) == 1 and exits[0].asset == "wstETH"


def test_lst_depeg_on_unheld_position_no_action() -> None:
    # wstETH depeg tripped but the portfolio holds none → nothing to exit.
    monitor = _monitor(wsteth_tripped=True)
    actions = monitor.assess(holdings={"USDC": Decimal("10000")})
    assert actions == []


def test_unhealthy_venue_exits_its_assets() -> None:
    monitor = _monitor()
    actions = monitor.assess(
        holdings=_HOLDINGS,
        venue_by_asset={"USDC": "aave_v3", "cbBTC": "aave_v3", "wstETH": "wallet"},
        unhealthy_venues=frozenset({"aave_v3"}),
    )
    exited = {a.asset for a in actions if a.kind == "exit_position"}
    assert exited == {"USDC", "cbBTC"}  # both lent on the unhealthy venue


def test_halt_all_precedes_exits() -> None:
    monitor = _monitor(usdc_tripped=True, wsteth_tripped=True)
    actions = monitor.assess(holdings=_HOLDINGS)
    assert actions[0].kind == "halt_all"
    assert any(a.kind == "exit_position" and a.asset == "wstETH" for a in actions)
