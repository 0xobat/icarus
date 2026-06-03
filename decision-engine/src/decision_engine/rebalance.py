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
