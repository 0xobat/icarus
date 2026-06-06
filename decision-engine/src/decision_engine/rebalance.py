"""Rebalance planner — the strategic-allocation core of the managed portfolio.

Managed-portfolio P2.1. Pure function, no I/O. Decides hold vs. one corrective
trade for an N-asset target against a symmetric drift band, routing the
correction through the stable hub, then applies a cost gate (don't rebalance
unless the corrective value clears a multiple of the estimated swap cost).

The original 2-asset `RebalanceTarget`/`plan_rebalance` (P1.3) was removed once
P2.2 migrated the cycle to the multi-asset planner; `.archive/` retains it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType
from typing import Literal

# Tolerance for the "weights sum to 1.0" construction check (Decimal-exact inputs
# expected; this only absorbs trailing-digit noise, not real misconfiguration).
_SUM_TOL = Decimal("1e-9")


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
        # Freeze the mapping: a frozen dataclass still shares the caller's dict by
        # reference, so an external mutation would silently change this target's
        # weights. Store a read-only copy to make the immutability real.
        object.__setattr__(self, "weights", MappingProxyType(dict(self.weights)))


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
            # The reason describes the corrected (counter) asset: it is underweight
            # (drift < 0), which is why we buy it. `side`/`drift` stay consistent.
            from_symbol, to_symbol, side = target.hub, counter, "underweight"
            asset, drift = counter, drifts[counter]
        else:
            # Hub underweight → crypto overweight overall; sell the most-positive.
            counter = max(
                (a for a in drifts if a != target.hub),
                key=lambda a: drifts[a],
            )
            counter_imbalance = drifts[counter] * nav
            usd_amount = min(hub_imbalance, counter_imbalance)
            # The corrected (counter) asset is overweight (drift > 0) → we sell it.
            from_symbol, to_symbol, side = counter, target.hub, "overweight"
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
    "plan_multi_rebalance",
]
