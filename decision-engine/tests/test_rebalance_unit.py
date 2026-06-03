"""Unit tests for the rebalance planner (managed-portfolio P1.3)."""

from __future__ import annotations

from decimal import Decimal

from decision_engine.rebalance import RebalancePlan, RebalanceTarget, plan_rebalance

_TARGET = RebalanceTarget(
    crypto_symbol="WETH",
    stable_symbol="USDC",
    crypto_weight=Decimal("0.6"),
    band=Decimal("0.10"),
)

# Cost inputs that never bind in band-only tests (correction >> margin*cost).
_NO_COST = Decimal("0")
_MARGIN = Decimal("4")


def test_within_band_holds() -> None:
    # crypto 0.65 of a $10k nav — inside [0.5, 0.7] → hold.
    plan = plan_rebalance(
        crypto_usd=Decimal("6500"),
        stable_usd=Decimal("3500"),
        target=_TARGET,
        est_cost_usd=_NO_COST,
        cost_gate_margin=_MARGIN,
    )
    assert plan.action == "hold"
    assert plan.usd_amount is None


def test_band_boundary_holds() -> None:
    # crypto exactly 0.70 → drift 0.10 == band → still hold (inclusive).
    plan = plan_rebalance(
        crypto_usd=Decimal("7000"),
        stable_usd=Decimal("3000"),
        target=_TARGET,
        est_cost_usd=_NO_COST,
        cost_gate_margin=_MARGIN,
    )
    assert plan.action == "hold"


def test_overweight_rebalances_crypto_to_stable() -> None:
    # crypto 0.80 (>0.70) of $10k → target crypto = $6000 → sell $2000 WETH→USDC.
    plan = plan_rebalance(
        crypto_usd=Decimal("8000"),
        stable_usd=Decimal("2000"),
        target=_TARGET,
        est_cost_usd=_NO_COST,
        cost_gate_margin=_MARGIN,
    )
    assert plan.action == "rebalance"
    assert plan.from_symbol == "WETH"
    assert plan.to_symbol == "USDC"
    assert plan.usd_amount == Decimal("2000")


def test_underweight_rebalances_stable_to_crypto() -> None:
    # crypto 0.40 (<0.50) of $10k → target crypto = $6000 → buy $2000 USDC→WETH.
    plan = plan_rebalance(
        crypto_usd=Decimal("4000"),
        stable_usd=Decimal("6000"),
        target=_TARGET,
        est_cost_usd=_NO_COST,
        cost_gate_margin=_MARGIN,
    )
    assert plan.action == "rebalance"
    assert plan.from_symbol == "USDC"
    assert plan.to_symbol == "WETH"
    assert plan.usd_amount == Decimal("2000")


def test_empty_portfolio_holds() -> None:
    plan = plan_rebalance(
        crypto_usd=Decimal("0"),
        stable_usd=Decimal("0"),
        target=_TARGET,
        est_cost_usd=_NO_COST,
        cost_gate_margin=_MARGIN,
    )
    assert plan.action == "hold"
    assert isinstance(plan, RebalancePlan)
