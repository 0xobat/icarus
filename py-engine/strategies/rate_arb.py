"""Lending rate arbitrage -- Tier 3 strategy (STRAT-006).

Monitors lending and borrowing rates across protocols (Aave, Compound),
identifies profitable rate differentials, borrows at lower rate and
supplies at higher rate to capture the spread. Auto-unwinds when spread
compresses below threshold.
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

_logger = get_logger("rate-arb", enable_file=False)

STRATEGY_ID = "STRAT-006"
STRATEGY_TIER = 3


# ---------------------------------------------------------------------------
# Market data types
# ---------------------------------------------------------------------------
@dataclass
class LendingRate:
    """Snapshot of a lending/borrowing rate on a protocol."""

    protocol: str  # e.g. "aave_v3", "compound"
    asset: str
    supply_apy: Decimal  # annualized supply APY
    borrow_apy: Decimal  # annualized borrow APY
    available_liquidity: Decimal  # available to borrow
    utilization_rate: Decimal
    chain: str = "ethereum"


@dataclass
class RateArbOpportunity:
    """A detected rate differential opportunity across two protocols."""

    asset: str
    borrow_protocol: str  # protocol to borrow from (lower rate)
    supply_protocol: str  # protocol to supply to (higher rate)
    borrow_apy: Decimal
    supply_apy: Decimal
    spread: Decimal  # supply_apy - borrow_apy
    max_size_usd: Decimal  # max position size constrained by liquidity
    chain: str = "ethereum"


# ---------------------------------------------------------------------------
# Strategy
# ---------------------------------------------------------------------------
@dataclass
class RateArbConfig:
    """Strategy configuration for lending rate arbitrage."""

    min_spread: Decimal = Decimal("0.010")  # 1.0% minimum rate spread
    exit_spread: Decimal = Decimal("0.005")  # 0.5% exit spread
    min_health_factor: Decimal = Decimal("1.5")  # min health factor on borrow
    max_position_duration_days: int = 7
    estimated_gas_cost_usd: Decimal = Decimal("10")  # per TX
    min_position_value_usd: Decimal = Decimal("100")
    default_max_gas_wei: str = "500000000000000"  # 0.0005 ETH
    default_max_slippage_bps: int = 50
    default_deadline_seconds: int = 300


class RateArbStrategy:
    """Tier 3: Lending rate arbitrage across protocols.

    Monitors lending/borrowing rates across Aave and Compound, identifies
    profitable differentials, opens borrow+supply positions to capture
    spread, and auto-unwinds when spread compresses.
    """

    def __init__(
        self,
        allocator: PortfolioAllocator,
        tracker: PositionTracker,
        config: RateArbConfig | None = None,
    ) -> None:
        self.allocator = allocator
        self.tracker = tracker
        self.config = config or RateArbConfig()
        self.status: str = "evaluating"

    # ------------------------------------------------------------------
    # Rate evaluation
    # ------------------------------------------------------------------

    def find_opportunities(
        self,
        rates: list[LendingRate],
    ) -> list[RateArbOpportunity]:
        """Find profitable rate arbitrage opportunities.

        Compares borrow rates on one protocol against supply rates on
        another for the same asset. Only returns opportunities where
        the spread exceeds the minimum threshold.

        Args:
            rates: List of lending rate snapshots across protocols.

        Returns:
            List of profitable rate arbitrage opportunities, sorted by spread.
        """
        # Group rates by asset
        by_asset: dict[str, list[LendingRate]] = {}
        for rate in rates:
            key = f"{rate.asset}:{rate.chain}"
            by_asset.setdefault(key, []).append(rate)

        opportunities: list[RateArbOpportunity] = []

        for _key, asset_rates in by_asset.items():
            if len(asset_rates) < 2:
                continue

            # Find best borrow (lowest) and supply (highest) across protocols
            for borrow_rate in asset_rates:
                for supply_rate in asset_rates:
                    if borrow_rate.protocol == supply_rate.protocol:
                        continue

                    spread = supply_rate.supply_apy - borrow_rate.borrow_apy
                    if spread < self.config.min_spread:
                        continue

                    max_size = min(
                        borrow_rate.available_liquidity,
                        supply_rate.available_liquidity,
                    )

                    if max_size <= 0:
                        continue

                    opportunities.append(RateArbOpportunity(
                        asset=borrow_rate.asset,
                        borrow_protocol=borrow_rate.protocol,
                        supply_protocol=supply_rate.protocol,
                        borrow_apy=borrow_rate.borrow_apy,
                        supply_apy=supply_rate.supply_apy,
                        spread=spread,
                        max_size_usd=max_size,
                        chain=borrow_rate.chain,
                    ))

        ranked = sorted(opportunities, key=lambda o: o.spread, reverse=True)
        _logger.debug(
            "Rate arb opportunities found",
            extra={"data": {
                "opportunity_count": len(ranked),
                "top_spread": str(ranked[0].spread) if ranked else None,
            }},
        )
        return ranked

    def evaluate(
        self,
        rates: list[LendingRate],
    ) -> list[RateArbOpportunity]:
        """Evaluate lending rates and return ranked arbitrage opportunities.

        Alias for find_opportunities that matches the common strategy interface.

        Args:
            rates: List of lending rate snapshots across protocols.

        Returns:
            Ranked list of rate arbitrage opportunities.
        """
        return self.find_opportunities(rates)

    def should_act(
        self,
        current_spread: Decimal,
        health_factor: Decimal = Decimal("2.0"),
    ) -> bool:
        """Determine whether rate arb action is needed.

        Returns True if:
        - Spread is attractive for entry (>= min_spread)
        - Spread has compressed below exit threshold (unwind)
        - Health factor is approaching danger zone

        Args:
            current_spread: Current rate spread between borrow and supply.
            health_factor: Current health factor on the borrow side.

        Returns:
            True if action should be taken.
        """
        # Health factor too low — must unwind
        if health_factor < self.config.min_health_factor:
            _logger.warning(
                "Health factor below minimum",
                extra={"data": {
                    "health_factor": str(health_factor),
                    "min_required": str(self.config.min_health_factor),
                }},
            )
            return True

        # Spread compressed — should unwind
        if current_spread < self.config.exit_spread:
            return True

        # Spread attractive — should enter
        if current_spread >= self.config.min_spread:
            return True

        return False

    # ------------------------------------------------------------------
    # Order generation
    # ------------------------------------------------------------------

    def generate_orders(
        self,
        rates: list[LendingRate],
        correlation_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Generate execution:orders for rate arbitrage operations.

        Returns schema-compliant order dicts. May return:
        - Empty list if no action needed
        - [withdraw, withdraw] if unwinding (withdraw supply + repay borrow)
        - [supply, supply] if entering (borrow on one + supply on other)

        Args:
            rates: List of lending rate snapshots across protocols.
            correlation_id: Optional correlation ID for order tracing.

        Returns:
            List of execution:orders-compliant order dicts.
        """
        cid = correlation_id or uuid.uuid4().hex
        now = datetime.now(UTC)
        deadline = int(now.timestamp()) + self.config.default_deadline_seconds

        # Check current rate arb positions
        current_positions = self.tracker.query(strategy=STRATEGY_ID)

        orders: list[dict[str, Any]] = []

        if current_positions:
            # Check if spread has compressed — unwind
            pos = current_positions[0]
            current_spread = Decimal(
                str(pos.protocol_data.get("current_spread", "0")),
            )
            health_factor = Decimal(
                str(pos.protocol_data.get("health_factor", "2.0")),
            )

            should_unwind = (
                current_spread < self.config.exit_spread
                or health_factor < self.config.min_health_factor
            )

            if should_unwind:
                # Withdraw from supply side
                supply_protocol = pos.protocol_data.get(
                    "supply_protocol", pos.protocol,
                )
                borrow_protocol = pos.protocol_data.get(
                    "borrow_protocol", pos.protocol,
                )

                orders.append(self._make_order(
                    action="withdraw",
                    asset=pos.asset,
                    amount=str(pos.amount),
                    chain=pos.chain,
                    protocol=supply_protocol,
                    correlation_id=cid,
                    deadline=deadline,
                ))
                # Repay borrow
                orders.append(self._make_order(
                    action="withdraw",
                    asset=pos.asset,
                    amount=str(pos.amount),
                    chain=pos.chain,
                    protocol=borrow_protocol,
                    correlation_id=cid,
                    deadline=deadline,
                ))
                return orders

            # Position OK — no action needed
            return []

        # No existing position — look for opportunities
        opportunities = self.find_opportunities(rates)
        if not opportunities:
            return []

        best = opportunities[0]

        available = self.allocator.get_available_capital(STRATEGY_TIER)
        if available < self.config.min_position_value_usd:
            return []

        # Respect protocol exposure limits
        max_protocol = (
            self.allocator.config.max_protocol_exposure
            * self.allocator.total_capital
        )
        by_proto = self.allocator._deployed_by_protocol()

        # Need room on both protocols
        borrow_room = max_protocol - by_proto.get(best.borrow_protocol, Decimal(0))
        supply_room = max_protocol - by_proto.get(best.supply_protocol, Decimal(0))
        proto_room = min(borrow_room, supply_room)

        amount = min(available, proto_room, best.max_size_usd)
        if amount < self.config.min_position_value_usd:
            return []

        # Check allocation for supply side
        check = self.allocator.check_allocation({
            "value_usd": float(amount),
            "protocol": best.supply_protocol,
            "asset": best.asset,
            "tier": STRATEGY_TIER,
        })
        if not check.allowed:
            _logger.info(
                "Rate arb blocked by allocator",
                extra={"data": {"reason": check.reason}},
            )
            return []

        # Borrow on low-rate protocol
        orders.append(self._make_order(
            action="supply",
            asset=best.asset,
            amount=str(amount),
            chain=best.chain,
            protocol=best.borrow_protocol,
            correlation_id=cid,
            deadline=deadline,
        ))

        # Supply on high-rate protocol
        orders.append(self._make_order(
            action="supply",
            asset=best.asset,
            amount=str(amount),
            chain=best.chain,
            protocol=best.supply_protocol,
            correlation_id=cid,
            deadline=deadline,
        ))

        _logger.info(
            "Rate arb orders generated",
            extra={"data": {
                "asset": best.asset,
                "spread": str(best.spread),
                "borrow_protocol": best.borrow_protocol,
                "supply_protocol": best.supply_protocol,
                "amount": str(amount),
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
        protocol: str,
        correlation_id: str,
        deadline: int,
    ) -> dict[str, Any]:
        """Build a schema-compliant execution:orders dict.

        Args:
            action: Order action (supply, withdraw).
            asset: Token symbol.
            amount: Amount as string.
            chain: Target chain.
            protocol: Protocol identifier.
            correlation_id: Correlation ID for tracing.
            deadline: Unix timestamp deadline.

        Returns:
            Schema-compliant order dictionary.
        """
        return {
            "version": "1.0.0",
            "orderId": uuid.uuid4().hex,
            "correlationId": correlation_id,
            "timestamp": datetime.now(UTC).isoformat(),
            "chain": chain,
            "protocol": protocol,
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
