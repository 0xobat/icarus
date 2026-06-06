"""Unit tests for the multi-asset rebalance planner (managed-portfolio P2.1).

Generalizes the 2-asset planner to an N-asset target, picking ONE corrective
trade per cycle (most-out-of-band asset) routed through the USDC hub. The
existing 2-asset API in test_rebalance_unit.py is left untouched.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from decision_engine.rebalance import (
    MultiAssetTarget,
    RebalancePlan,
    plan_multi_rebalance,
)

# Base-first default target: USDC 40 / ETH 32 / WBTC 28, ±10% band, USDC hub.
_TARGET = MultiAssetTarget(
    weights={
        "USDC": Decimal("0.40"),
        "WETH": Decimal("0.32"),
        "cbBTC": Decimal("0.28"),
    },
    band=Decimal("0.10"),
    hub="USDC",
)

# Cost inputs that never bind unless a test sets them (correction >> margin*cost).
_NO_COST = Decimal("0")
_MARGIN = Decimal("4")


# --- MultiAssetTarget validation -------------------------------------------


def test_target_rejects_weights_not_summing_to_one() -> None:
    with pytest.raises(ValueError):
        MultiAssetTarget(
            weights={"USDC": Decimal("0.4"), "WETH": Decimal("0.4"), "cbBTC": Decimal("0.4")},
            band=Decimal("0.10"),
            hub="USDC",
        )


def test_target_rejects_non_positive_weight() -> None:
    with pytest.raises(ValueError):
        MultiAssetTarget(
            weights={"USDC": Decimal("0.5"), "WETH": Decimal("0.5"), "cbBTC": Decimal("0")},
            band=Decimal("0.10"),
            hub="USDC",
        )


def test_target_rejects_hub_not_in_weights() -> None:
    with pytest.raises(ValueError):
        MultiAssetTarget(
            weights={"USDC": Decimal("0.4"), "WETH": Decimal("0.32"), "cbBTC": Decimal("0.28")},
            band=Decimal("0.10"),
            hub="DAI",
        )


def test_target_rejects_band_out_of_range() -> None:
    with pytest.raises(ValueError):
        MultiAssetTarget(
            weights={"USDC": Decimal("0.4"), "WETH": Decimal("0.32"), "cbBTC": Decimal("0.28")},
            band=Decimal("0.6"),
            hub="USDC",
        )
    with pytest.raises(ValueError):
        MultiAssetTarget(
            weights={"USDC": Decimal("0.4"), "WETH": Decimal("0.32"), "cbBTC": Decimal("0.28")},
            band=Decimal("-0.01"),
            hub="USDC",
        )


def test_target_accepts_valid_construction() -> None:
    assert _TARGET.hub == "USDC"
    assert _TARGET.weights["WETH"] == Decimal("0.32")


# --- planner: hold paths ----------------------------------------------------


def test_within_band_holds() -> None:
    # All assets exactly on target → every drift 0 → hold.
    plan = plan_multi_rebalance(
        holdings={"USDC": Decimal("4000"), "WETH": Decimal("3200"), "cbBTC": Decimal("2800")},
        target=_TARGET,
        est_cost_usd=_NO_COST,
        cost_gate_margin=_MARGIN,
    )
    assert plan.action == "hold"
    assert "within band" in plan.reason
    assert plan.usd_amount is None


def test_empty_portfolio_holds() -> None:
    plan = plan_multi_rebalance(
        holdings={"USDC": Decimal("0"), "WETH": Decimal("0"), "cbBTC": Decimal("0")},
        target=_TARGET,
        est_cost_usd=_NO_COST,
        cost_gate_margin=_MARGIN,
    )
    assert plan.action == "hold"
    assert isinstance(plan, RebalancePlan)


# --- planner: single crypto out of band ------------------------------------


def test_one_crypto_overweight_sells_to_hub() -> None:
    # ETH 0.45 (drift +0.13, sole breach); USDC 0.335 & WBTC 0.215 within band.
    # nav $10k → sell drift*nav = $1300 ETH→USDC.
    plan = plan_multi_rebalance(
        holdings={"USDC": Decimal("3350"), "WETH": Decimal("4500"), "cbBTC": Decimal("2150")},
        target=_TARGET,
        est_cost_usd=_NO_COST,
        cost_gate_margin=_MARGIN,
    )
    assert plan.action == "rebalance"
    assert plan.from_symbol == "WETH"
    assert plan.to_symbol == "USDC"
    assert plan.usd_amount == Decimal("1300")


def test_one_crypto_underweight_buys_from_hub() -> None:
    # ETH 0.19 (drift -0.13, sole breach); USDC 0.465 & WBTC 0.345 within band.
    # nav $10k → buy -drift*nav = $1300 USDC→ETH.
    plan = plan_multi_rebalance(
        holdings={"USDC": Decimal("4650"), "WETH": Decimal("1900"), "cbBTC": Decimal("3450")},
        target=_TARGET,
        est_cost_usd=_NO_COST,
        cost_gate_margin=_MARGIN,
    )
    assert plan.action == "rebalance"
    assert plan.from_symbol == "USDC"
    assert plan.to_symbol == "WETH"
    assert plan.usd_amount == Decimal("1300")


def test_selects_single_largest_drift_when_two_out_of_band() -> None:
    # nav $10k: USDC 0.38 (within), ETH 0.46 (drift +0.14), WBTC 0.16 (drift -0.12).
    # Both ETH & WBTC breach, but |0.14| > |0.12| → correct ETH only (one trade).
    plan = plan_multi_rebalance(
        holdings={"USDC": Decimal("3800"), "WETH": Decimal("4600"), "cbBTC": Decimal("1600")},
        target=_TARGET,
        est_cost_usd=_NO_COST,
        cost_gate_margin=_MARGIN,
    )
    assert plan.action == "rebalance"
    assert plan.from_symbol == "WETH"
    assert plan.to_symbol == "USDC"
    assert plan.usd_amount == Decimal("1400")


# --- planner: hub itself out of band ---------------------------------------


def test_hub_overweight_buys_most_underweight_crypto() -> None:
    # USDC 0.52 (drift +0.12, sole breach). Crypto within band but ETH most
    # underweight (drift -0.08) vs WBTC (-0.04). Buy USDC→ETH, sized to the
    # smaller of hub excess ($1200) and ETH deficit ($800) → $800.
    plan = plan_multi_rebalance(
        holdings={"USDC": Decimal("5200"), "WETH": Decimal("2400"), "cbBTC": Decimal("2400")},
        target=_TARGET,
        est_cost_usd=_NO_COST,
        cost_gate_margin=_MARGIN,
    )
    assert plan.action == "rebalance"
    assert plan.from_symbol == "USDC"
    assert plan.to_symbol == "WETH"
    assert plan.usd_amount == Decimal("800")


def test_hub_underweight_sells_most_overweight_crypto() -> None:
    # USDC 0.28 (drift -0.12, sole breach). ETH most overweight (drift +0.08)
    # vs WBTC (+0.04). Sell ETH→USDC, sized to min(hub deficit $1200,
    # ETH excess $800) → $800.
    plan = plan_multi_rebalance(
        holdings={"USDC": Decimal("2800"), "WETH": Decimal("4000"), "cbBTC": Decimal("3200")},
        target=_TARGET,
        est_cost_usd=_NO_COST,
        cost_gate_margin=_MARGIN,
    )
    assert plan.action == "rebalance"
    assert plan.from_symbol == "WETH"
    assert plan.to_symbol == "USDC"
    assert plan.usd_amount == Decimal("800")


# --- planner: cost gate -----------------------------------------------------


def test_cost_gate_suppresses_small_rebalance() -> None:
    # Same ETH overweight as above ($1300 correction). est_cost $500, margin 4
    # → threshold $2000 > $1300 → cost-gated hold.
    plan = plan_multi_rebalance(
        holdings={"USDC": Decimal("3350"), "WETH": Decimal("4500"), "cbBTC": Decimal("2150")},
        target=_TARGET,
        est_cost_usd=Decimal("500"),
        cost_gate_margin=Decimal("4"),
    )
    assert plan.action == "hold"
    assert "cost-gated" in plan.reason


def test_cost_gate_allows_large_rebalance() -> None:
    # Same $1300 correction, est_cost $100, margin 4 → threshold $400 < $1300 → proceed.
    plan = plan_multi_rebalance(
        holdings={"USDC": Decimal("3350"), "WETH": Decimal("4500"), "cbBTC": Decimal("2150")},
        target=_TARGET,
        est_cost_usd=Decimal("100"),
        cost_gate_margin=Decimal("4"),
    )
    assert plan.action == "rebalance"
    assert plan.from_symbol == "WETH"
    assert plan.usd_amount == Decimal("1300")
