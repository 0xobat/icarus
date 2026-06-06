"""Unit tests for the yield-leg router (managed-portfolio P2.3).

`plan_yield_legs` turns a single rebalance swap into the ordered multi-leg
sequence needed when capital lives in a lending venue: withdraw-before-swap and
supply-after-swap for assets marked `deployed`.
"""

from __future__ import annotations

from decimal import Decimal

from decision_engine.rebalance import RebalancePlan
from decision_engine.yield_router import YieldLeg, plan_yield_legs

# Base-first: stable + wBTC are lent on Aave; ETH is held (→ wstETH at P2.4).
_DEPLOYED = {"USDC": True, "cbBTC": True, "WETH": False}


def _swap(from_s: str, to_s: str, usd: str) -> RebalancePlan:
    return RebalancePlan(
        action="rebalance", reason="t",
        from_symbol=from_s, to_symbol=to_s, usd_amount=Decimal(usd),
    )


def test_hold_plan_has_no_legs() -> None:
    plan = RebalancePlan(action="hold", reason="within band")
    assert plan_yield_legs(plan, deployed=_DEPLOYED) == []


def test_sell_eth_to_usdc_swaps_then_supplies() -> None:
    # WETH (not deployed) → USDC (deployed): swap, then redeploy proceeds to Aave.
    legs = plan_yield_legs(_swap("WETH", "USDC", "2000"), deployed=_DEPLOYED)
    assert legs == [
        YieldLeg(action="swap", usd_amount=Decimal("2000"), from_symbol="WETH", to_symbol="USDC"),
        YieldLeg(action="supply", usd_amount=Decimal("2000"), asset="USDC"),
    ]


def test_buy_eth_from_usdc_withdraws_then_swaps() -> None:
    # USDC (deployed) → WETH (not deployed): free the funds from Aave, then swap.
    legs = plan_yield_legs(_swap("USDC", "WETH", "2000"), deployed=_DEPLOYED)
    assert legs == [
        YieldLeg(action="withdraw", usd_amount=Decimal("2000"), asset="USDC"),
        YieldLeg(action="swap", usd_amount=Decimal("2000"), from_symbol="USDC", to_symbol="WETH"),
    ]


def test_cbbtc_to_usdc_withdraws_swaps_supplies() -> None:
    # Both deployed → withdraw cbBTC, swap to USDC, supply USDC. (Three legs.)
    legs = plan_yield_legs(_swap("cbBTC", "USDC", "1500"), deployed=_DEPLOYED)
    assert legs == [
        YieldLeg(action="withdraw", usd_amount=Decimal("1500"), asset="cbBTC"),
        YieldLeg(action="swap", usd_amount=Decimal("1500"), from_symbol="cbBTC", to_symbol="USDC"),
        YieldLeg(action="supply", usd_amount=Decimal("1500"), asset="USDC"),
    ]


def test_plain_swap_when_neither_deployed() -> None:
    # Neither side lent → single swap leg, unchanged from P1 behaviour.
    legs = plan_yield_legs(_swap("WETH", "WETH2", "500"), deployed={"WETH": False, "WETH2": False})
    assert legs == [
        YieldLeg(action="swap", usd_amount=Decimal("500"), from_symbol="WETH", to_symbol="WETH2"),
    ]


def test_unknown_asset_defaults_to_not_deployed() -> None:
    # An asset absent from `deployed` is treated as held (no withdraw/supply).
    legs = plan_yield_legs(_swap("WETH", "USDC", "100"), deployed={"USDC": True})
    assert [leg.action for leg in legs] == ["swap", "supply"]
