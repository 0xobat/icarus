"""Aave V3 lending supply — Tier 1 strategy (LEND-001).

Supplies stablecoins to Aave V3 on Base. Rotates to highest supply APY
market when the APY differential exceeds threshold after gas costs.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from monitoring.logger import get_logger
from portfolio.allocator import PortfolioAllocator
from portfolio.position_tracker import PositionTracker

_logger = get_logger("aave-lending", enable_file=False)

STRATEGY_ID = "LEND-001"
STRATEGY_TIER = 1

# LEND-001 operates exclusively on Base
ALLOWED_CHAINS = frozenset({"base"})

# Whitelisted stablecoin assets for Aave V3 supply on Base
WHITELISTED_ASSETS = frozenset({"USDC", "USDbC"})


# ---------------------------------------------------------------------------
# Market data types
# ---------------------------------------------------------------------------
@dataclass
class AaveMarket:
    """Snapshot of an Aave V3 supply market."""

    asset: str
    supply_apy: Decimal  # annualized, e.g. 0.035 = 3.5%
    available_liquidity: Decimal
    utilization_rate: Decimal
    chain: str = "ethereum"


@dataclass
class PerformanceRecord:
    """Historical performance for a market we supplied to."""

    asset: str
    apy_at_entry: Decimal
    entry_time: str
    exit_time: str | None = None
    realized_yield: Decimal | None = None


# ---------------------------------------------------------------------------
# Strategy
# ---------------------------------------------------------------------------
@dataclass
class AaveLendingConfig:
    """Strategy configuration."""

    min_apy_improvement: Decimal = Decimal("0.005")  # 0.5% APY
    min_supply_apy: Decimal = Decimal("0.01")  # 1.0% floor
    estimated_gas_cost_usd: Decimal = Decimal("10")  # per TX (supply or withdraw)
    gas_amortization_days: int = 14
    min_position_value_usd: Decimal = Decimal("100")
    min_monthly_gain_usd: Decimal = Decimal("1")
    default_max_gas_wei: str = "500000000000000"  # 0.0005 ETH
    default_max_slippage_bps: int = 50
    default_deadline_seconds: int = 300


class AaveLendingStrategy:
    """Tier 1: Aave V3 supply rotation for optimal yield.

    Scans whitelisted Aave markets, identifies the best supply APY,
    and rotates positions when net improvement exceeds threshold.
    """

    def __init__(
        self,
        allocator: PortfolioAllocator,
        tracker: PositionTracker,
        config: AaveLendingConfig | None = None,
    ) -> None:
        self.allocator = allocator
        self.tracker = tracker
        self.config = config or AaveLendingConfig()
        self.status: str = "evaluating"
        self.performance_history: list[PerformanceRecord] = []

    # ------------------------------------------------------------------
    # Market evaluation
    # ------------------------------------------------------------------

    def evaluate(
        self,
        markets: list[AaveMarket],
    ) -> list[AaveMarket]:
        """Rank whitelisted Aave markets by supply APY (descending).

        Filters to whitelisted assets with positive liquidity.
        """
        eligible = [
            m for m in markets
            if m.chain in ALLOWED_CHAINS
            and m.asset in WHITELISTED_ASSETS
            and m.available_liquidity > 0
            and m.supply_apy > 0
        ]
        ranked = sorted(eligible, key=lambda m: m.supply_apy, reverse=True)
        _logger.debug(
            "Markets evaluated",
            extra={"data": {
                "eligible_count": len(ranked),
                "top": ranked[0].asset if ranked else None,
                "top_apy": str(ranked[0].supply_apy) if ranked else None,
            }},
        )
        return ranked

    def should_rotate(
        self,
        current_apy: Decimal,
        best_market: AaveMarket,
        position_value_usd: Decimal,
    ) -> bool:
        """Determine if rotating to *best_market* is worth the gas cost.

        Rotation requires 2 TXs (withdraw + supply). We calculate the
        annualized gas cost relative to position size and subtract from
        the APY improvement.
        """
        apy_diff = best_market.supply_apy - current_apy
        if apy_diff <= 0:
            return False

        # Gas cost for 2 TXs (withdraw old + supply new)
        total_gas = self.config.estimated_gas_cost_usd * 2

        if position_value_usd <= 0:
            return False

        # Annualized gas cost as fraction of position
        gas_cost_pct = total_gas / position_value_usd
        net_improvement = apy_diff - gas_cost_pct

        should = net_improvement >= self.config.min_apy_improvement
        _logger.debug(
            "Rotation evaluation",
            extra={"data": {
                "current_apy": str(current_apy),
                "best_apy": str(best_market.supply_apy),
                "apy_diff": str(apy_diff),
                "gas_cost_pct": str(gas_cost_pct),
                "net_improvement": str(net_improvement),
                "should_rotate": should,
            }},
        )
        return should

    # ------------------------------------------------------------------
    # Order generation
    # ------------------------------------------------------------------

    def generate_orders(
        self,
        markets: list[AaveMarket],
        correlation_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Generate execution:orders for optimal Aave supply rotation.

        Returns a list of schema-compliant order dicts. May return:
        - Empty list if no action needed
        - [withdraw, supply] if rotating between markets
        - [supply] if entering a new market from idle capital
        """
        ranked = self.evaluate(markets)
        if not ranked:
            return []

        best = ranked[0]
        cid = correlation_id or uuid.uuid4().hex
        now = datetime.now(UTC)
        deadline = int(now.timestamp()) + self.config.default_deadline_seconds

        # Check current Aave positions
        current_positions = self.tracker.query(
            strategy=STRATEGY_ID, protocol="aave",
        )

        orders: list[dict[str, Any]] = []

        if current_positions:
            # We have an existing position — check if rotation is worthwhile
            pos = current_positions[0]  # primary Aave position
            current_apy = Decimal(
                str(pos.protocol_data.get("current_apy", "0")),
            )
            if pos.asset == best.asset:
                # Already in the best market
                return []

            if not self.should_rotate(
                current_apy, best, pos.current_value,
            ):
                return []

            # Check exposure limit for the new market
            check = self.allocator.check_allocation({
                "value_usd": float(pos.current_value),
                "protocol": "aave",
                "asset": best.asset,
                "tier": STRATEGY_TIER,
            })
            if not check.allowed:
                _logger.info(
                    "Rotation blocked by allocator",
                    extra={"data": {"reason": check.reason}},
                )
                return []

            # Withdraw from current market
            orders.append(self._make_order(
                action="withdraw",
                asset=pos.asset,
                amount=str(pos.amount),
                chain=pos.chain,
                correlation_id=cid,
                deadline=deadline,
            ))

            # Record performance
            self.performance_history.append(PerformanceRecord(
                asset=pos.asset,
                apy_at_entry=current_apy,
                entry_time=pos.entry_time,
                exit_time=now.isoformat(),
            ))

            # Supply to best market
            orders.append(self._make_order(
                action="supply",
                asset=best.asset,
                amount=str(pos.amount),
                chain=best.chain,
                correlation_id=cid,
                deadline=deadline,
            ))
        else:
            # No existing position — find max deployable amount
            available = self.allocator.get_available_capital(STRATEGY_TIER)
            if available < self.config.min_position_value_usd:
                return []

            # Also respect protocol exposure limit
            max_protocol = (
                self.allocator.config.max_protocol_exposure
                * self.allocator.total_capital
            )
            by_proto = self.allocator._deployed_by_protocol()
            proto_room = max_protocol - by_proto.get("aave", Decimal(0))
            amount = min(available, proto_room)

            if amount < self.config.min_position_value_usd:
                return []

            check = self.allocator.check_allocation({
                "value_usd": float(amount),
                "protocol": "aave",
                "asset": best.asset,
                "tier": STRATEGY_TIER,
            })
            if not check.allowed:
                _logger.info(
                    "New position blocked by allocator",
                    extra={"data": {"reason": check.reason}},
                )
                return []

            orders.append(self._make_order(
                action="supply",
                asset=best.asset,
                amount=str(amount),
                chain=best.chain,
                correlation_id=cid,
                deadline=deadline,
            ))

        _logger.info(
            "Orders generated",
            extra={"data": {
                "order_count": len(orders),
                "target_asset": best.asset,
                "target_apy": str(best.supply_apy),
            }},
        )
        return orders

    def _make_order(
        self,
        *,
        action: str,
        asset: str,
        amount: str,
        chain: str,
        correlation_id: str,
        deadline: int,
    ) -> dict[str, Any]:
        """Build a schema-compliant execution:orders dict."""
        return {
            "version": "1.0.0",
            "orderId": uuid.uuid4().hex,
            "correlationId": correlation_id,
            "timestamp": datetime.now(UTC).isoformat(),
            "chain": chain,
            "protocol": "aave_v3",
            "action": action,
            "strategy": STRATEGY_ID,
            "priority": "normal",
            "params": {
                "tokenIn": asset,
                "amount": amount,
            },
            "limits": {
                "maxGasWei": self.config.default_max_gas_wei,
                "maxSlippageBps": self.config.default_max_slippage_bps,
                "deadlineUnix": deadline,
            },
            "useFlashbotsProtect": False,
        }

    # ------------------------------------------------------------------
    # Performance tracking
    # ------------------------------------------------------------------

    def get_performance_history(self) -> list[dict[str, Any]]:
        """Return historical APY records for markets we supplied to."""
        return [
            {
                "asset": r.asset,
                "apy_at_entry": str(r.apy_at_entry),
                "entry_time": r.entry_time,
                "exit_time": r.exit_time,
                "realized_yield": str(r.realized_yield) if r.realized_yield else None,
            }
            for r in self.performance_history
        ]
