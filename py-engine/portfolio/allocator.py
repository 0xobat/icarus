"""Portfolio allocator — tier-based capital allocation with exposure limits."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from monitoring.logger import get_logger

_logger = get_logger("portfolio-allocator", enable_file=False)

# ---------------------------------------------------------------------------
# Stablecoin identifiers (case-insensitive matching)
# ---------------------------------------------------------------------------
STABLECOINS = frozenset({
    "usdc", "usdt", "dai", "frax", "lusd", "gusd", "tusd", "busd",
})


def _is_stablecoin(asset: str) -> bool:
    return asset.lower() in STABLECOINS


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class TierBounds:
    """Min/max allocation percentage for a strategy tier."""

    min_pct: Decimal
    max_pct: Decimal


@dataclass(frozen=True)
class AllocatorConfig:
    """All configurable limits, loaded from env vars or defaults."""

    tier_bounds: dict[int, TierBounds] = field(default_factory=lambda: {
        1: TierBounds(Decimal("0.50"), Decimal("0.60")),
        2: TierBounds(Decimal("0.25"), Decimal("0.35")),
        3: TierBounds(Decimal("0.10"), Decimal("0.20")),
    })
    min_stablecoin_reserve: Decimal = Decimal("0.15")
    max_protocol_exposure: Decimal = Decimal("0.40")
    max_asset_exposure: Decimal = Decimal("0.60")
    rebalance_threshold: Decimal = Decimal("0.05")


def _load_config() -> AllocatorConfig:
    """Load config from environment variables, falling back to defaults."""
    defaults = AllocatorConfig()
    return AllocatorConfig(
        tier_bounds={
            1: TierBounds(
                Decimal(os.environ.get("TIER1_MIN_PCT", str(defaults.tier_bounds[1].min_pct))),
                Decimal(os.environ.get("TIER1_MAX_PCT", str(defaults.tier_bounds[1].max_pct))),
            ),
            2: TierBounds(
                Decimal(os.environ.get("TIER2_MIN_PCT", str(defaults.tier_bounds[2].min_pct))),
                Decimal(os.environ.get("TIER2_MAX_PCT", str(defaults.tier_bounds[2].max_pct))),
            ),
            3: TierBounds(
                Decimal(os.environ.get("TIER3_MIN_PCT", str(defaults.tier_bounds[3].min_pct))),
                Decimal(os.environ.get("TIER3_MAX_PCT", str(defaults.tier_bounds[3].max_pct))),
            ),
        },
        min_stablecoin_reserve=Decimal(
            os.environ.get("MIN_STABLECOIN_RESERVE", str(defaults.min_stablecoin_reserve)),
        ),
        max_protocol_exposure=Decimal(
            os.environ.get("MAX_PROTOCOL_EXPOSURE", str(defaults.max_protocol_exposure)),
        ),
        max_asset_exposure=Decimal(
            os.environ.get("MAX_ASSET_EXPOSURE", str(defaults.max_asset_exposure)),
        ),
        rebalance_threshold=Decimal(
            os.environ.get("REBALANCE_THRESHOLD", str(defaults.rebalance_threshold)),
        ),
    )


# ---------------------------------------------------------------------------
# Allocator
# ---------------------------------------------------------------------------
@dataclass
class AllocationCheck:
    """Result of a pre-trade allocation check."""

    allowed: bool
    reason: str


class PortfolioAllocator:
    """Manages tier-based capital allocation with exposure limits.

    Reads current positions from a positions dict (compatible with
    StateManager.get_positions()). Each position is expected to have at
    minimum: ``value_usd``, ``protocol``, ``asset``, and ``tier``.
    """

    def __init__(
        self,
        total_capital: Decimal,
        positions: dict[str, dict[str, Any]] | None = None,
        config: AllocatorConfig | None = None,
    ) -> None:
        self.total_capital = total_capital
        self.positions = positions or {}
        self.config = config or _load_config()

    # ------------------------------------------------------------------
    # Aggregation helpers
    # ------------------------------------------------------------------

    def _deployed_by_tier(self) -> dict[int, Decimal]:
        """Sum of value_usd per tier across all positions."""
        by_tier: dict[int, Decimal] = {}
        for pos in self.positions.values():
            tier = pos.get("tier", 0)
            by_tier[tier] = by_tier.get(tier, Decimal(0)) + Decimal(str(pos["value_usd"]))
        return by_tier

    def _deployed_by_protocol(self) -> dict[str, Decimal]:
        by_proto: dict[str, Decimal] = {}
        for pos in self.positions.values():
            proto = pos["protocol"]
            by_proto[proto] = by_proto.get(proto, Decimal(0)) + Decimal(str(pos["value_usd"]))
        return by_proto

    def _deployed_by_asset(self) -> dict[str, Decimal]:
        by_asset: dict[str, Decimal] = {}
        for pos in self.positions.values():
            asset = pos["asset"]
            by_asset[asset] = by_asset.get(asset, Decimal(0)) + Decimal(str(pos["value_usd"]))
        return by_asset

    def _total_deployed(self) -> Decimal:
        return sum(
            (Decimal(str(p["value_usd"])) for p in self.positions.values()),
            Decimal(0),
        )

    def _stablecoin_value(self) -> Decimal:
        """Total value held in stablecoin positions + undeployed capital."""
        stable_deployed = sum(
            (Decimal(str(p["value_usd"])) for p in self.positions.values()
             if _is_stablecoin(p.get("asset", ""))),
            Decimal(0),
        )
        undeployed = self.total_capital - self._total_deployed()
        return stable_deployed + undeployed

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check_allocation(self, proposed: dict[str, Any]) -> AllocationCheck:
        """Validate a proposed position against all allocation limits.

        *proposed* must contain: ``value_usd``, ``protocol``, ``asset``,
        ``tier``.

        Returns an ``AllocationCheck`` with ``allowed=True`` if the
        position passes all checks, or ``allowed=False`` with a reason.
        """
        value = Decimal(str(proposed["value_usd"]))
        tier = proposed["tier"]
        protocol = proposed["protocol"]
        asset = proposed["asset"]

        if self.total_capital == 0:
            return AllocationCheck(False, "total capital is zero")

        # 1. Tier allocation check
        bounds = self.config.tier_bounds.get(tier)
        if bounds is None:
            return AllocationCheck(False, f"unknown tier {tier}")

        by_tier = self._deployed_by_tier()
        tier_after = by_tier.get(tier, Decimal(0)) + value
        tier_pct = tier_after / self.total_capital
        if tier_pct > bounds.max_pct:
            return AllocationCheck(
                False,
                f"tier {tier} would be {tier_pct:.1%} (max {bounds.max_pct:.0%})",
            )

        # 2. Protocol exposure check
        by_proto = self._deployed_by_protocol()
        proto_after = by_proto.get(protocol, Decimal(0)) + value
        proto_pct = proto_after / self.total_capital
        if proto_pct > self.config.max_protocol_exposure:
            return AllocationCheck(
                False,
                f"protocol '{protocol}' would be {proto_pct:.1%} "
                f"(max {self.config.max_protocol_exposure:.0%})",
            )

        # 3. Asset exposure check (stablecoins exempt)
        if not _is_stablecoin(asset):
            by_asset = self._deployed_by_asset()
            asset_after = by_asset.get(asset, Decimal(0)) + value
            asset_pct = asset_after / self.total_capital
            if asset_pct > self.config.max_asset_exposure:
                return AllocationCheck(
                    False,
                    f"asset '{asset}' would be {asset_pct:.1%} "
                    f"(max {self.config.max_asset_exposure:.0%})",
                )

        # 4. Stablecoin reserve check
        total_after = self._total_deployed() + value
        undeployed_after = self.total_capital - total_after
        stable_deployed = sum(
            (Decimal(str(p["value_usd"])) for p in self.positions.values()
             if _is_stablecoin(p.get("asset", ""))),
            Decimal(0),
        )
        # If the proposed position IS a stablecoin, it still counts as liquid
        if _is_stablecoin(asset):
            stable_deployed += value
        reserve_after = (stable_deployed + undeployed_after) / self.total_capital
        if reserve_after < self.config.min_stablecoin_reserve:
            return AllocationCheck(
                False,
                f"stablecoin reserve would be {reserve_after:.1%} "
                f"(min {self.config.min_stablecoin_reserve:.0%})",
            )

        _logger.debug(
            "Allocation check passed",
            extra={"data": {
                "tier": tier, "protocol": protocol, "asset": asset,
                "value_usd": str(value),
            }},
        )
        return AllocationCheck(True, "ok")

    def get_available_capital(self, tier: int) -> Decimal:
        """How much capital can still be deployed into *tier*.

        Returns the lesser of:
        - remaining room in the tier's max allocation
        - remaining capital after stablecoin reserve
        """
        bounds = self.config.tier_bounds.get(tier)
        if bounds is None:
            return Decimal(0)

        by_tier = self._deployed_by_tier()
        tier_deployed = by_tier.get(tier, Decimal(0))
        tier_room = (bounds.max_pct * self.total_capital) - tier_deployed

        # Also constrained by stablecoin reserve
        total_deployed = self._total_deployed()
        max_deployable = self.total_capital * (1 - self.config.min_stablecoin_reserve)
        reserve_room = max_deployable - total_deployed

        available = min(tier_room, reserve_room)
        return max(available, Decimal(0))

    def needs_rebalance(self) -> bool:
        """Return True if any tier's allocation drifts beyond threshold.

        Target is the midpoint of each tier's bounds. If actual allocation
        deviates by more than ``rebalance_threshold``, a rebalance is
        needed.
        """
        if self.total_capital == 0:
            return False

        by_tier = self._deployed_by_tier()
        for tier, bounds in self.config.tier_bounds.items():
            target = (bounds.min_pct + bounds.max_pct) / 2
            actual = by_tier.get(tier, Decimal(0)) / self.total_capital
            if abs(actual - target) > self.config.rebalance_threshold:
                _logger.info(
                    "Rebalance needed",
                    extra={"data": {
                        "tier": tier,
                        "target": str(target),
                        "actual": str(actual),
                        "drift": str(abs(actual - target)),
                    }},
                )
                return True
        return False

    def get_exposure_summary(self) -> dict[str, Any]:
        """Return a summary of current exposure for logging/dashboards."""
        total = self.total_capital
        if total == 0:
            return {"total_capital": "0", "deployed": "0"}

        deployed = self._total_deployed()
        return {
            "total_capital": str(total),
            "deployed": str(deployed),
            "deployed_pct": str(deployed / total),
            "stablecoin_reserve_pct": str(self._stablecoin_value() / total),
            "by_tier": {
                str(k): str(v) for k, v in self._deployed_by_tier().items()
            },
            "by_protocol": {
                k: str(v) for k, v in self._deployed_by_protocol().items()
            },
            "by_asset": {
                k: str(v) for k, v in self._deployed_by_asset().items()
            },
        }
