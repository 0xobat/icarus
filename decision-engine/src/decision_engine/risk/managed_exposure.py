"""Managed-model exposure caps — per-asset + per-venue concentration (P2.5).

Re-introduces the exposure control deferred at the P1 gate right-sizing, but
purpose-built for the managed model: it consumes the prospective post-trade
per-asset USD holdings (threaded on RiskContext each tick — real positions, not
the lake-era position-blind limiter) and rejects an order that would push any
asset, or any *capped* venue, past its NAV-fraction cap.

Two caps:
  * per-asset — a safety net ABOVE the allocation band the planner enforces; set
    >= the largest upper band so it never blocks a legitimate rebalance, only a
    bug / extreme drift.
  * per-venue — applies ONLY to venues in `capped_venues` (the P3 LP overlay,
    perps). Core lending of allocation assets (Aave) is governed by the bands,
    not this cap — so it is intentionally left uncapped.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from icarus.envelopes.orders import ExecutionOrder

from decision_engine.risk_gate import RiskContext, RiskDecision

__all__ = ["ManagedExposureChecker", "ManagedExposureConfig"]


@dataclass(frozen=True)
class ManagedExposureConfig:
    """Concentration caps as NAV fractions."""

    max_asset_pct: Decimal = Decimal("0.60")  # >= the largest upper band (safety net)
    max_venue_pct: Decimal = Decimal("0.25")  # applies to capped (overlay) venues only


class ManagedExposureChecker:
    """Per-asset + per-venue concentration cap on the prospective holdings."""

    name = "managed_exposure"

    def __init__(
        self,
        config: ManagedExposureConfig,
        *,
        capped_venues: frozenset[str] = frozenset(),
    ) -> None:
        self._config = config
        self._capped_venues = capped_venues

    def check(self, order: ExecutionOrder, ctx: RiskContext) -> RiskDecision:
        holdings = ctx.prospective_holdings
        if not holdings:
            return RiskDecision(passed=True, checker=self.name)
        nav = sum(holdings.values(), Decimal("0"))
        if nav <= 0:
            return RiskDecision(passed=True, checker=self.name)

        # 1. Per-asset concentration.
        for asset, usd in holdings.items():
            pct = usd / nav
            if pct > self._config.max_asset_pct:
                return RiskDecision(
                    passed=False, checker=self.name,
                    reason=(
                        f"asset {asset} concentration {pct:.1%} > "
                        f"{self._config.max_asset_pct:.0%} cap"
                    ),
                )

        # 2. Per-venue concentration (capped/overlay venues only).
        if self._capped_venues:
            venues = ctx.venue_by_asset or {}
            by_venue: dict[str, Decimal] = {}
            for asset, usd in holdings.items():
                venue = venues.get(asset, "wallet")
                if venue in self._capped_venues:
                    by_venue[venue] = by_venue.get(venue, Decimal("0")) + usd
            for venue, usd in by_venue.items():
                pct = usd / nav
                if pct > self._config.max_venue_pct:
                    return RiskDecision(
                        passed=False, checker=self.name,
                        reason=(
                            f"venue {venue} concentration {pct:.1%} > "
                            f"{self._config.max_venue_pct:.0%} cap"
                        ),
                    )

        return RiskDecision(passed=True, checker=self.name)
