"""Portfolio health monitor — turn breaker states into de-risk decisions (P3.2).

Pure aggregation, no I/O. Each tick the monitor looks at the USDC depeg breaker,
the per-LST depeg breakers (P3.1), and an injected set of unhealthy venues, and
returns the ordered de-risk actions warranted by the current holdings. Emitting
the urgent orders that carry those actions out is execution (deferred to the
fill run); this is the decision the design's exit criterion exercises ("a
simulated depeg/health event triggers the correct de-risk").

Severity order: `halt_all` (USDC depeg — every rebalance touches USDC) precedes
targeted `exit_position` actions (an LST trading off fair value, or an asset
sitting in an unhealthy venue).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from decision_engine.risk.depeg_breaker import DepegBreaker
from decision_engine.risk.lst_depeg_breaker import LstDepegBreaker

__all__ = ["DeRiskAction", "PortfolioHealthMonitor"]


@dataclass(frozen=True)
class DeRiskAction:
    """One de-risk directive. `halt_all` stops all rebalancing; `exit_position`
    unwinds the named asset toward the hub."""

    kind: Literal["halt_all", "exit_position"]
    reason: str
    asset: str | None = None


class PortfolioHealthMonitor:
    """Aggregates depeg + venue-health signals into ordered de-risk actions."""

    def __init__(
        self,
        *,
        usdc_depeg: DepegBreaker,
        lst_breakers: Mapping[str, LstDepegBreaker],
    ) -> None:
        self._usdc_depeg = usdc_depeg
        self._lst_breakers = dict(lst_breakers)

    def assess(
        self,
        *,
        holdings: Mapping[str, Decimal],
        venue_by_asset: Mapping[str, str] | None = None,
        unhealthy_venues: frozenset[str] = frozenset(),
    ) -> list[DeRiskAction]:
        """Return the de-risk actions warranted now (most severe first)."""
        actions: list[DeRiskAction] = []

        # 1. USDC depeg → halt everything (every rebalance touches USDC).
        if self._usdc_depeg.is_tripped:
            actions.append(
                DeRiskAction(
                    kind="halt_all",
                    reason=f"USDC depeg ({self._usdc_depeg.deviation_bps}bps off-peg)",
                )
            )

        def _held(asset: str) -> bool:
            return holdings.get(asset, Decimal("0")) > 0

        # 2. LST depeg → exit the affected (held) sleeve.
        for asset, breaker in self._lst_breakers.items():
            if breaker.is_tripped and _held(asset):
                actions.append(
                    DeRiskAction(
                        kind="exit_position", asset=asset,
                        reason=f"{asset} LST depeg ({breaker.deviation_bps}bps off fair value)",
                    )
                )

        # 3. Unhealthy venue → exit held assets sitting in it.
        if unhealthy_venues and venue_by_asset:
            for asset, venue in venue_by_asset.items():
                if venue in unhealthy_venues and _held(asset):
                    actions.append(
                        DeRiskAction(
                            kind="exit_position", asset=asset,
                            reason=f"{asset} in unhealthy venue {venue}",
                        )
                    )

        return actions
