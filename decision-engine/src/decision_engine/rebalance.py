"""Rebalance planner — the strategic-allocation core of the managed portfolio.

Managed-portfolio P1.3. Pure function, no I/O. Decides hold vs. rebalance for a
2-asset (crypto + stable) portfolio against a fixed target weight and a
symmetric drift band, then applies a cost gate (don't rebalance unless the
corrective value clears a multiple of the estimated swap cost).

Scope (YAGNI): exactly one crypto + one stable asset — the P1 trivial target.
Multi-asset allocation (SOL, wBTC, LP overlay) is a later phase.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

# Tolerance for the "weights sum to 1.0" construction check (Decimal-exact inputs
# expected; this only absorbs trailing-digit noise, not real misconfiguration).
_SUM_TOL = Decimal("1e-9")


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


@dataclass(frozen=True)
class MultiAssetTarget:
    """Strategic target for an N-asset portfolio (managed-portfolio P2.1).

    `weights` maps asset symbol → target fraction of NAV; they must sum to ~1.0.
    `band` is the symmetric absolute-weight tolerance (no rebalance while every
    asset's abs(weight - target) <= band). `hub` is the stable funding asset
    (default "USDC") through which all corrective trades route — so we never
    need direct crypto↔crypto liquidity.
    """

    weights: Mapping[str, Decimal]
    band: Decimal = Decimal("0.10")
    hub: str = "USDC"

    def __post_init__(self) -> None:
        if not self.weights:
            raise ValueError("weights must be non-empty")
        if any(w <= 0 for w in self.weights.values()):
            raise ValueError("every target weight must be positive")
        total = sum(self.weights.values(), Decimal("0"))
        if abs(total - Decimal("1")) > _SUM_TOL:
            raise ValueError(f"target weights must sum to 1.0 (got {total})")
        if self.hub not in self.weights:
            raise ValueError(f"hub {self.hub!r} not in weights")
        if not (Decimal("0") <= self.band <= Decimal("0.5")):
            raise ValueError(f"band must be in [0, 0.5] (got {self.band})")


def plan_multi_rebalance(
    *,
    holdings: Mapping[str, Decimal],
    target: MultiAssetTarget,
    est_cost_usd: Decimal,
    cost_gate_margin: Decimal,
) -> RebalancePlan:
    """Decide hold vs. one corrective trade for an N-asset portfolio.

    1. nav = sum(holdings); hold if nav <= 0.
    2. Per asset, drift = weight - target; pick the single most-out-of-band
       asset (max abs(drift) among those with abs(drift) > band). None → hold.
    3. Build the correction through the hub (one swap/cycle):
       - crypto breach overweight → sell asset→hub (drift*nav); underweight →
         buy hub→asset (-drift*nav).
       - hub breach → trade the most-opposite crypto, sized to the smaller of
         the hub's imbalance and that crypto's own imbalance.
    4. cost-gate: hold if usd_amount < cost_gate_margin * est_cost_usd.
    """
    nav = sum(holdings.values(), Decimal("0"))
    if nav <= 0:
        return RebalancePlan(action="hold", reason="empty portfolio (nav<=0)")

    drifts = {
        asset: holdings.get(asset, Decimal("0")) / nav - tw
        for asset, tw in target.weights.items()
    }

    breaches = [a for a, d in drifts.items() if abs(d) > target.band]
    if not breaches:
        return RebalancePlan(action="hold", reason="within band")

    asset = max(breaches, key=lambda a: abs(drifts[a]))
    drift = drifts[asset]

    if asset != target.hub:
        # A crypto asset breached: correct it directly against the hub.
        usd_amount = abs(drift) * nav
        if drift > 0:
            from_symbol, to_symbol, side = asset, target.hub, "overweight"
        else:
            from_symbol, to_symbol, side = target.hub, asset, "underweight"
    else:
        # The hub itself breached: route through the most-opposite crypto, sized
        # to the smaller of the hub's imbalance and that crypto's imbalance.
        hub_imbalance = abs(drift) * nav
        if drift > 0:
            # Hub overweight → crypto underweight overall; buy the most-negative.
            counter = min(
                (a for a in drifts if a != target.hub),
                key=lambda a: drifts[a],
            )
            counter_imbalance = -drifts[counter] * nav
            usd_amount = min(hub_imbalance, counter_imbalance)
            from_symbol, to_symbol, side = target.hub, counter, "overweight"
            asset, drift = counter, drifts[counter]
        else:
            # Hub underweight → crypto overweight overall; sell the most-positive.
            counter = max(
                (a for a in drifts if a != target.hub),
                key=lambda a: drifts[a],
            )
            counter_imbalance = drifts[counter] * nav
            usd_amount = min(hub_imbalance, counter_imbalance)
            from_symbol, to_symbol, side = counter, target.hub, "underweight"
            asset, drift = counter, drifts[counter]

    if usd_amount < cost_gate_margin * est_cost_usd:
        return RebalancePlan(
            action="hold",
            reason=(
                f"cost-gated (correction={usd_amount} < "
                f"{cost_gate_margin}x cost={est_cost_usd})"
            ),
        )

    return RebalancePlan(
        action="rebalance",
        reason=f"{asset} {side} (drift={drift})",
        from_symbol=from_symbol,
        to_symbol=to_symbol,
        usd_amount=usd_amount,
    )


__all__ = [
    "MultiAssetTarget",
    "RebalancePlan",
    "RebalanceTarget",
    "plan_multi_rebalance",
    "plan_rebalance",
]
