"""Exposure limit enforcement — pre-trade checks for protocol, asset, and reserve limits."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from monitoring.logger import get_logger

_logger = get_logger("exposure-limits", enable_file=False)

# Stablecoins (matching portfolio/allocator.py)
STABLECOINS = frozenset({
    "usdc", "usdt", "dai", "frax", "lusd", "gusd", "tusd", "busd",
})


def _is_stablecoin(asset: str) -> bool:
    return asset.lower() in STABLECOINS


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ExposureLimitsConfig:
    """Configurable exposure limits. Loaded from env or defaults."""

    max_protocol_pct: Decimal = Decimal("0.40")   # 40% max per protocol
    max_asset_pct: Decimal = Decimal("0.60")       # 60% max per asset (ex stables)
    min_stablecoin_pct: Decimal = Decimal("0.15")  # 15% min in stables/liquid


def load_config() -> ExposureLimitsConfig:
    """Load config from environment variables, falling back to defaults."""
    defaults = ExposureLimitsConfig()
    return ExposureLimitsConfig(
        max_protocol_pct=Decimal(
            os.environ.get("MAX_PROTOCOL_EXPOSURE", str(defaults.max_protocol_pct))
        ),
        max_asset_pct=Decimal(
            os.environ.get("MAX_ASSET_EXPOSURE", str(defaults.max_asset_pct))
        ),
        min_stablecoin_pct=Decimal(
            os.environ.get("MIN_STABLECOIN_RESERVE", str(defaults.min_stablecoin_pct))
        ),
    )


# ---------------------------------------------------------------------------
# Check results
# ---------------------------------------------------------------------------

@dataclass
class ExposureCheckResult:
    """Result of a pre-trade exposure check."""

    allowed: bool
    reason: str
    limit_type: str = ""  # "protocol", "asset", "stablecoin_reserve"


@dataclass
class ExposureSummary:
    """Current exposure levels across the portfolio."""

    total_capital: str
    total_deployed: str
    by_protocol: dict[str, str]
    by_asset: dict[str, str]
    stablecoin_reserve_pct: str
    protocol_pcts: dict[str, str]
    asset_pcts: dict[str, str]


# ---------------------------------------------------------------------------
# Exposure Limiter
# ---------------------------------------------------------------------------

class ExposureLimiter:
    """Enforces exposure limits on every proposed order.

    Checks (before reaching TS executor):
    - Max 40% of portfolio in any single protocol
    - Max 60% of portfolio in any single asset (excluding stablecoins)
    - Min 15% in stablecoins/liquid reserves at all times

    Limits are configurable and changes are logged for audit trail.
    """

    def __init__(
        self,
        total_capital: Decimal | float | str,
        positions: dict[str, dict[str, Any]] | None = None,
        config: ExposureLimitsConfig | None = None,
    ) -> None:
        self._total_capital = Decimal(str(total_capital))
        self._positions = positions or {}
        self._config = config or load_config()

    @property
    def config(self) -> ExposureLimitsConfig:
        """Return the current exposure limits configuration."""
        return self._config

    @property
    def total_capital(self) -> Decimal:
        """Return the total capital amount."""
        return self._total_capital

    def update_config(self, **kwargs: Any) -> None:
        """Update limits. Logs the change for audit trail."""
        old = self._config
        new_vals = {
            "max_protocol_pct": kwargs.get("max_protocol_pct", old.max_protocol_pct),
            "max_asset_pct": kwargs.get("max_asset_pct", old.max_asset_pct),
            "min_stablecoin_pct": kwargs.get("min_stablecoin_pct", old.min_stablecoin_pct),
        }
        # Convert to Decimal if needed
        for k, v in new_vals.items():
            if not isinstance(v, Decimal):
                new_vals[k] = Decimal(str(v))

        self._config = ExposureLimitsConfig(**new_vals)

        _logger.info(
            "Exposure limits updated",
            extra={"data": {
                "timestamp": datetime.now(UTC).isoformat(),
                "old": {
                    "max_protocol_pct": str(old.max_protocol_pct),
                    "max_asset_pct": str(old.max_asset_pct),
                    "min_stablecoin_pct": str(old.min_stablecoin_pct),
                },
                "new": {
                    "max_protocol_pct": str(new_vals["max_protocol_pct"]),
                    "max_asset_pct": str(new_vals["max_asset_pct"]),
                    "min_stablecoin_pct": str(new_vals["min_stablecoin_pct"]),
                },
            }},
        )

    def update_positions(self, positions: dict[str, dict[str, Any]]) -> None:
        """Update the current positions snapshot."""
        self._positions = positions

    def update_capital(self, total_capital: Decimal | float | str) -> None:
        """Update total capital."""
        self._total_capital = Decimal(str(total_capital))

    # ------------------------------------------------------------------
    # Aggregation
    # ------------------------------------------------------------------

    def _total_deployed(self) -> Decimal:
        return sum(
            (Decimal(str(p["value_usd"])) for p in self._positions.values()),
            Decimal(0),
        )

    def _by_protocol(self) -> dict[str, Decimal]:
        by_proto: dict[str, Decimal] = {}
        for p in self._positions.values():
            proto = p["protocol"]
            by_proto[proto] = by_proto.get(proto, Decimal(0)) + Decimal(str(p["value_usd"]))
        return by_proto

    def _by_asset(self) -> dict[str, Decimal]:
        by_asset: dict[str, Decimal] = {}
        for p in self._positions.values():
            asset = p["asset"]
            by_asset[asset] = by_asset.get(asset, Decimal(0)) + Decimal(str(p["value_usd"]))
        return by_asset

    def _stablecoin_value(self) -> Decimal:
        """Stablecoin positions + undeployed capital."""
        stable = sum(
            (Decimal(str(p["value_usd"])) for p in self._positions.values()
             if _is_stablecoin(p.get("asset", ""))),
            Decimal(0),
        )
        undeployed = self._total_capital - self._total_deployed()
        return stable + undeployed

    # ------------------------------------------------------------------
    # Pre-trade check
    # ------------------------------------------------------------------

    def check_order(self, order: dict[str, Any]) -> ExposureCheckResult:
        """Validate a proposed order against all exposure limits.

        Required order fields: value_usd, protocol, asset.
        Returns ExposureCheckResult with allowed=True or rejection reason.
        """
        value = Decimal(str(order["value_usd"]))
        protocol = order["protocol"]
        asset = order["asset"]

        if self._total_capital == 0:
            return ExposureCheckResult(
                allowed=False,
                reason="total capital is zero",
                limit_type="capital",
            )

        # 1. Protocol exposure
        by_proto = self._by_protocol()
        proto_after = by_proto.get(protocol, Decimal(0)) + value
        proto_pct = proto_after / self._total_capital
        if proto_pct > self._config.max_protocol_pct:
            _logger.warning(
                "Order rejected: protocol exposure limit",
                extra={"data": {
                    "protocol": protocol,
                    "current_pct": str(by_proto.get(protocol, Decimal(0)) / self._total_capital),
                    "proposed_pct": str(proto_pct),
                    "limit": str(self._config.max_protocol_pct),
                    "order_value": str(value),
                }},
            )
            return ExposureCheckResult(
                allowed=False,
                reason=(
                    f"protocol '{protocol}' would be {proto_pct:.1%} "
                    f"(max {self._config.max_protocol_pct:.0%})"
                ),
                limit_type="protocol",
            )

        # 2. Asset exposure (stablecoins exempt)
        if not _is_stablecoin(asset):
            by_asset = self._by_asset()
            asset_after = by_asset.get(asset, Decimal(0)) + value
            asset_pct = asset_after / self._total_capital
            if asset_pct > self._config.max_asset_pct:
                _logger.warning(
                    "Order rejected: asset exposure limit",
                    extra={"data": {
                        "asset": asset,
                        "current_pct": str(by_asset.get(asset, Decimal(0)) / self._total_capital),
                        "proposed_pct": str(asset_pct),
                        "limit": str(self._config.max_asset_pct),
                        "order_value": str(value),
                    }},
                )
                return ExposureCheckResult(
                    allowed=False,
                    reason=(
                        f"asset '{asset}' would be {asset_pct:.1%} "
                        f"(max {self._config.max_asset_pct:.0%})"
                    ),
                    limit_type="asset",
                )

        # 3. Stablecoin reserve check
        total_after = self._total_deployed() + value
        undeployed_after = self._total_capital - total_after
        stable_deployed = sum(
            (Decimal(str(p["value_usd"])) for p in self._positions.values()
             if _is_stablecoin(p.get("asset", ""))),
            Decimal(0),
        )
        if _is_stablecoin(asset):
            stable_deployed += value
        reserve_after = (stable_deployed + undeployed_after) / self._total_capital
        if reserve_after < self._config.min_stablecoin_pct:
            _logger.warning(
                "Order rejected: stablecoin reserve limit",
                extra={"data": {
                    "current_reserve_pct": str(self._stablecoin_value() / self._total_capital),
                    "proposed_reserve_pct": str(reserve_after),
                    "limit": str(self._config.min_stablecoin_pct),
                    "order_value": str(value),
                }},
            )
            return ExposureCheckResult(
                allowed=False,
                reason=(
                    f"stablecoin reserve would be {reserve_after:.1%} "
                    f"(min {self._config.min_stablecoin_pct:.0%})"
                ),
                limit_type="stablecoin_reserve",
            )

        _logger.debug(
            "Order exposure check passed",
            extra={"data": {
                "protocol": protocol,
                "asset": asset,
                "value_usd": str(value),
            }},
        )
        return ExposureCheckResult(allowed=True, reason="ok")

    # ------------------------------------------------------------------
    # Exposure summary (queryable for dashboard)
    # ------------------------------------------------------------------

    def get_exposure(self) -> ExposureSummary:
        """Return current exposure levels for monitoring/dashboard."""
        total = self._total_capital
        deployed = self._total_deployed()
        by_proto = self._by_protocol()
        by_asset = self._by_asset()

        if total == 0:
            return ExposureSummary(
                total_capital="0",
                total_deployed="0",
                by_protocol={},
                by_asset={},
                stablecoin_reserve_pct="0",
                protocol_pcts={},
                asset_pcts={},
            )

        return ExposureSummary(
            total_capital=str(total),
            total_deployed=str(deployed),
            by_protocol={k: str(v) for k, v in by_proto.items()},
            by_asset={k: str(v) for k, v in by_asset.items()},
            stablecoin_reserve_pct=str(self._stablecoin_value() / total),
            protocol_pcts={k: str(v / total) for k, v in by_proto.items()},
            asset_pcts={k: str(v / total) for k, v in by_asset.items()},
        )
