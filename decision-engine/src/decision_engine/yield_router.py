"""Yield-leg router — sequence the multi-leg moves a rebalance needs (P2.3).

Pure function, no I/O. When a rebalance moves capital into or out of an asset
that is normally lent in a yield venue, a single swap is not enough: the funds
must first be **withdrawn** from the venue before swapping, and proceeds
**supplied** back after. `plan_yield_legs` turns one `RebalancePlan` swap into
the ordered leg sequence.

Atomicity contract: each leg is a separate `ExecutionOrder`; the cycle emits
leg N+1 only after leg N's `ExecutionResult` confirms (a sequential saga driven
by the results-consumer). No atomic multicall/flashloan bundling — this keeps
the risk gate + PnL attribution per-leg and avoids standing router approvals.
Wiring that saga across ticks is integration (deferred to the fill run); this
module is the pure planner the saga drives.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from decision_engine.rebalance import RebalancePlan

__all__ = ["YieldLeg", "plan_yield_legs"]


@dataclass(frozen=True)
class YieldLeg:
    """One ordered step of a rebalance.

    `swap` legs carry `from_symbol`/`to_symbol`; `supply`/`withdraw` legs carry
    `asset`. `usd_amount` is the leg's USD notional (constant across the
    sequence — the same value is withdrawn, swapped, and re-supplied).
    """

    action: Literal["withdraw", "swap", "supply"]
    usd_amount: Decimal
    from_symbol: str | None = None
    to_symbol: str | None = None
    asset: str | None = None


def plan_yield_legs(
    plan: RebalancePlan, *, deployed: Mapping[str, bool]
) -> list[YieldLeg]:
    """Expand a rebalance swap into withdraw → swap → supply legs as needed.

    `deployed[symbol]` is True for assets normally held in a lending venue
    (their balance must be freed before a swap and redeployed after). Assets
    absent from `deployed` are treated as held (no withdraw/supply). A `hold`
    plan yields no legs.
    """
    if plan.action != "rebalance":
        return []
    from_symbol = plan.from_symbol
    to_symbol = plan.to_symbol
    amount = plan.usd_amount
    if from_symbol is None or to_symbol is None or amount is None:
        raise ValueError(f"rebalance plan missing swap fields: {plan!r}")

    legs: list[YieldLeg] = []
    if deployed.get(from_symbol, False):
        legs.append(YieldLeg(action="withdraw", usd_amount=amount, asset=from_symbol))
    legs.append(
        YieldLeg(action="swap", usd_amount=amount, from_symbol=from_symbol, to_symbol=to_symbol)
    )
    if deployed.get(to_symbol, False):
        legs.append(YieldLeg(action="supply", usd_amount=amount, asset=to_symbol))
    return legs
