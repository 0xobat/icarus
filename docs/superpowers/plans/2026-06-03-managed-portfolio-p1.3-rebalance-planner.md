# Managed Portfolio P1.3 — Rebalance Planner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** A pure "rebalance planner" — the core of the managed-portfolio brain. Given current holdings (crypto vs. stable in USD), the strategic target weight, the rebalancing band, and an estimated swap cost, it decides **hold** vs. **rebalance** and sizes the corrective swap. Encodes the strategic-allocation + band + cost-gate logic from the design.

**Architecture:** New module `decision_engine/rebalance.py` with `RebalanceTarget`, `RebalancePlan`, and `plan_rebalance()`. Pure function, no I/O — unit-tested directly. P1.4 wires it into a cycle (read holdings → plan → P1.1 resolver → risk gate → publish); P1.2's `estimate_swap_cost_usd` feeds the cost gate input.

**Tech Stack:** Python 3.13, `uv`, `pytest`, `Decimal`.

---

## Context the implementer needs

- The design (`docs/design-managed-portfolio.md`): strategic target **60% crypto / 40% stable**, rebalancing band **±10 absolute %** (crypto sleeve floats 50–70% before acting), cost gate "rebalance only if correction value ≥ ~4× est.(gas+slippage)".
- **Scope (YAGNI):** P1's trivial target is a **2-asset** portfolio (one crypto + one stable, e.g. WETH/USDC on Base). The planner handles the 2-asset case with a single corrective swap. Multi-asset generalization (SOL, wBTC) is a later phase — do NOT build it here.
- **Drift convention:** `crypto_weight = crypto_usd / nav`; `drift = crypto_weight - target_weight`. Hold while `abs(drift) <= band`. Outside the band, swap exactly enough to return crypto to its target USD value.
- Tests: `decision-engine/tests/test_rebalance_unit.py`. Run: `uv run pytest <path> -v`.

## File Structure
- **Create:** `decision-engine/src/decision_engine/rebalance.py` — `RebalanceTarget`, `RebalancePlan`, `plan_rebalance`.
- **Create:** `decision-engine/tests/test_rebalance_unit.py` — unit tests.

---

## Task 1: Types + band-only hold/rebalance decision

**Files:** Create `decision-engine/src/decision_engine/rebalance.py`; Test `decision-engine/tests/test_rebalance_unit.py`.

- [ ] **Step 1: Write the failing test**

Create `decision-engine/tests/test_rebalance_unit.py`:

```python
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest decision-engine/tests/test_rebalance_unit.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'decision_engine.rebalance'`

- [ ] **Step 3: Implement**

Create `decision-engine/src/decision_engine/rebalance.py`:

```python
"""Rebalance planner — the strategic-allocation core of the managed portfolio.

Managed-portfolio P1.3. Pure function, no I/O. Decides hold vs. rebalance for a
2-asset (crypto + stable) portfolio against a fixed target weight and a
symmetric drift band, then applies a cost gate (don't rebalance unless the
corrective value clears a multiple of the estimated swap cost).

Scope (YAGNI): exactly one crypto + one stable asset — the P1 trivial target.
Multi-asset allocation (SOL, wBTC, LP overlay) is a later phase.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal


@dataclass(frozen=True)
class RebalanceTarget:
    """Strategic target for a 2-asset portfolio.

    `crypto_weight` is the target fraction of NAV in the crypto asset (e.g.
    Decimal("0.6")); the stable asset takes the remainder. `band` is the
    symmetric absolute-weight tolerance — no rebalance while
    abs(crypto_weight - target) <= band.
    """

    crypto_symbol: str
    stable_symbol: str
    crypto_weight: Decimal
    band: Decimal


@dataclass(frozen=True)
class RebalancePlan:
    """The planner's verdict for one tick.

    action="hold" → do nothing (carries the reason for the log). action=
    "rebalance" → swap `usd_amount` of `from_symbol` into `to_symbol`.
    """

    action: Literal["hold", "rebalance"]
    reason: str
    from_symbol: str | None = None
    to_symbol: str | None = None
    usd_amount: Decimal | None = None


def plan_rebalance(
    *,
    crypto_usd: Decimal,
    stable_usd: Decimal,
    target: RebalanceTarget,
    est_cost_usd: Decimal,
    cost_gate_margin: Decimal,
) -> RebalancePlan:
    """Decide hold vs. rebalance for a 2-asset portfolio.

    1. nav = crypto_usd + stable_usd; hold if nav <= 0.
    2. hold if abs(crypto_weight - target.crypto_weight) <= target.band.
    3. else size the corrective swap to bring crypto to its target USD value.
    4. cost-gate: hold if correction_usd < cost_gate_margin * est_cost_usd.
    5. overweight crypto → sell crypto→stable; underweight → buy stable→crypto.
    """
    nav = crypto_usd + stable_usd
    if nav <= 0:
        return RebalancePlan(action="hold", reason="empty portfolio (nav<=0)")

    crypto_weight = crypto_usd / nav
    drift = crypto_weight - target.crypto_weight
    if abs(drift) <= target.band:
        return RebalancePlan(action="hold", reason=f"within band (drift={drift})")

    target_crypto_usd = target.crypto_weight * nav
    correction_usd = abs(crypto_usd - target_crypto_usd)

    if correction_usd < cost_gate_margin * est_cost_usd:
        return RebalancePlan(
            action="hold",
            reason=(
                f"cost-gated (correction={correction_usd} < "
                f"{cost_gate_margin}x cost={est_cost_usd})"
            ),
        )

    if drift > 0:  # crypto overweight → sell crypto for stable
        return RebalancePlan(
            action="rebalance",
            reason=f"crypto overweight (drift={drift})",
            from_symbol=target.crypto_symbol,
            to_symbol=target.stable_symbol,
            usd_amount=correction_usd,
        )
    # crypto underweight → buy crypto with stable
    return RebalancePlan(
        action="rebalance",
        reason=f"crypto underweight (drift={drift})",
        from_symbol=target.stable_symbol,
        to_symbol=target.crypto_symbol,
        usd_amount=correction_usd,
    )


__all__ = ["RebalanceTarget", "RebalancePlan", "plan_rebalance"]
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest decision-engine/tests/test_rebalance_unit.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add decision-engine/src/decision_engine/rebalance.py decision-engine/tests/test_rebalance_unit.py
git commit -m "feat(daedalus): P1.3 rebalance planner — strategic target + band"
```

---

## Task 2: Cost gate

**Files:** Modify nothing in impl (cost gate is already in `plan_rebalance` from Task 1) — this task ADDS the tests that pin the cost-gate behavior, proving it. (If Task 1's implementation already includes the cost gate as written above, these tests pass immediately; that is expected and acceptable.)

Test: `decision-engine/tests/test_rebalance_unit.py`.

- [ ] **Step 1: Write the cost-gate tests**

Append to `decision-engine/tests/test_rebalance_unit.py`:

```python
def test_cost_gate_suppresses_small_rebalance() -> None:
    # crypto 0.75 of $10k → outside band (>0.70). target crypto = $6000 →
    # correction = $1500. With est_cost=$500 and margin=4, threshold = $2000.
    # 1500 < 2000 → cost-gated hold.
    plan = plan_rebalance(
        crypto_usd=Decimal("7500"),
        stable_usd=Decimal("2500"),
        target=_TARGET,
        est_cost_usd=Decimal("500"),
        cost_gate_margin=Decimal("4"),
    )
    assert plan.action == "hold"
    assert "cost-gated" in plan.reason


def test_cost_gate_allows_large_rebalance() -> None:
    # Same drift but correction $1500 with est_cost=$100, margin=4 →
    # threshold = $400. 1500 >= 400 → rebalance proceeds.
    plan = plan_rebalance(
        crypto_usd=Decimal("7500"),
        stable_usd=Decimal("2500"),
        target=_TARGET,
        est_cost_usd=Decimal("100"),
        cost_gate_margin=Decimal("4"),
    )
    assert plan.action == "rebalance"
    assert plan.from_symbol == "WETH"
    assert plan.usd_amount == Decimal("1500")
```

- [ ] **Step 2: Run the tests**

Run: `uv run pytest decision-engine/tests/test_rebalance_unit.py -v`
Expected: PASS (7 passed). (These tests pin behavior already implemented in Task 1; immediate green is correct — do not alter the implementation to force a red.)

- [ ] **Step 3: Run full decision-engine suite (no regressions)**

Run: `uv run pytest decision-engine/tests -q`
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add decision-engine/tests/test_rebalance_unit.py
git commit -m "feat(daedalus): P1.3 rebalance planner — pin cost-gate behavior"
```

---

## Self-Review (against design §3 decision loop + §2 params)

**1. Spec coverage:** strategic target (60/40) → `RebalanceTarget.crypto_weight`; ±10% band → `band` + the `abs(drift) <= band` hold; cost gate (≥4× cost) → the `correction < margin*cost` hold. Corrective sizing returns crypto to exactly its target USD. Out of scope (later): multi-asset, LP overlay, staking — flagged.

**2. Placeholder scan:** none.

**3. Type consistency:** `RebalanceTarget(crypto_symbol, stable_symbol, crypto_weight, band)`, `RebalancePlan(action, reason, from_symbol, to_symbol, usd_amount)`, `plan_rebalance(*, crypto_usd, stable_usd, target, est_cost_usd, cost_gate_margin)` identical in tests and impl.

**4. Arithmetic verified:** $10k nav, target 0.6 → target crypto $6000. Overweight 0.80→$8000: correction $2000 ✓. Underweight 0.40→$4000: correction $2000 ✓. Cost gate: 0.75→$7500 correction $1500; 4×$500=$2000>1500 hold ✓; 4×$100=$400<1500 rebalance ✓. Band boundary 0.70 drift exactly 0.10 == band → hold ✓.

---

## Execution Handoff
Subagent-Driven (recommended) or Inline. Pure module; depends on nothing from P1.1/P1.2 at import time — they all compose in P1.4 (the cycle).
