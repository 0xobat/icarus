"""Yield farming auto-compound -- Tier 2 strategy (STRAT-004).

Identifies yield farming opportunities, enters positions, auto-harvests
rewards at gas-optimal intervals, and compounds harvested rewards back
into the farming position.
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

_logger = get_logger("yield-farming", enable_file=False)

STRATEGY_ID = "STRAT-004"
STRATEGY_TIER = 2


# ---------------------------------------------------------------------------
# Market data types
# ---------------------------------------------------------------------------
@dataclass
class FarmOpportunity:
    """Snapshot of a yield farming opportunity."""

    farm_id: str  # unique identifier for the farm
    protocol: str  # e.g. "aave_v3", "curve", "convex"
    asset: str  # primary deposit token
    reward_token: str  # token received as reward
    farm_apr: Decimal  # annualized farming APR
    tvl_usd: Decimal  # total value locked in the farm
    reward_token_price_usd: Decimal  # current price of reward token
    chain: str = "ethereum"


# ---------------------------------------------------------------------------
# Strategy
# ---------------------------------------------------------------------------
@dataclass
class YieldFarmingConfig:
    """Strategy configuration for yield farming auto-compound."""

    min_farm_apr: Decimal = Decimal("0.05")  # 5% entry threshold
    exit_apr_threshold: Decimal = Decimal("0.03")  # 3% exit threshold
    min_tvl_usd: Decimal = Decimal("10000000")  # $10M minimum TVL
    min_harvest_gas_multiple: Decimal = Decimal("2.0")  # harvest when reward >= 2x gas
    estimated_gas_cost_usd: Decimal = Decimal("10")  # per harvest TX
    reward_price_drop_exit: Decimal = Decimal("0.30")  # 30% reward token price drop
    min_position_value_usd: Decimal = Decimal("100")
    default_max_gas_wei: str = "500000000000000"  # 0.0005 ETH
    default_max_slippage_bps: int = 50
    default_deadline_seconds: int = 300


class YieldFarmingStrategy:
    """Tier 2: Yield farming with auto-compound.

    Evaluates farming opportunities by APR and TVL, enters positions,
    harvests rewards when gas-optimal, and compounds rewards back into
    the farming position. Exits when APR drops or reward token crashes.
    """

    def __init__(
        self,
        allocator: PortfolioAllocator,
        tracker: PositionTracker,
        config: YieldFarmingConfig | None = None,
    ) -> None:
        self.allocator = allocator
        self.tracker = tracker
        self.config = config or YieldFarmingConfig()
        self.status: str = "evaluating"

    # ------------------------------------------------------------------
    # Farm evaluation
    # ------------------------------------------------------------------

    def evaluate(
        self,
        farms: list[FarmOpportunity],
    ) -> list[FarmOpportunity]:
        """Rank farming opportunities by APR (descending).

        Filters to farms with sufficient TVL and APR above threshold.

        Args:
            farms: List of farming opportunity snapshots.

        Returns:
            Filtered and ranked list of farming opportunities.
        """
        eligible = [
            f for f in farms
            if f.farm_apr >= self.config.min_farm_apr
            and f.tvl_usd >= self.config.min_tvl_usd
            and f.reward_token_price_usd > 0
        ]
        ranked = sorted(eligible, key=lambda f: f.farm_apr, reverse=True)
        _logger.debug(
            "Farms evaluated",
            extra={"data": {
                "eligible_count": len(ranked),
                "top_farm": ranked[0].farm_id if ranked else None,
                "top_apr": str(ranked[0].farm_apr) if ranked else None,
            }},
        )
        return ranked

    def should_act(
        self,
        current_apr: Decimal,
        pending_rewards_usd: Decimal,
        reward_token_price_change: Decimal = Decimal("0"),
    ) -> bool:
        """Determine whether farming action is needed.

        Returns True if:
        - APR dropped below exit threshold
        - Pending rewards are worth harvesting (>= 2x gas)
        - Reward token has crashed

        Args:
            current_apr: Current farming APR.
            pending_rewards_usd: USD value of pending unharvested rewards.
            reward_token_price_change: Reward token 24h price change (negative = drop).

        Returns:
            True if action should be taken.
        """
        # Reward token crash
        if reward_token_price_change < -self.config.reward_price_drop_exit:
            return True

        # APR below exit
        if current_apr < self.config.exit_apr_threshold:
            return True

        # Rewards worth harvesting
        min_harvest = self.config.estimated_gas_cost_usd * self.config.min_harvest_gas_multiple
        if pending_rewards_usd >= min_harvest:
            return True

        return False

    def should_harvest(
        self,
        pending_rewards_usd: Decimal,
    ) -> bool:
        """Check if pending rewards justify a harvest transaction.

        Args:
            pending_rewards_usd: USD value of pending unharvested rewards.

        Returns:
            True if rewards exceed the gas cost multiple threshold.
        """
        min_harvest = self.config.estimated_gas_cost_usd * self.config.min_harvest_gas_multiple
        return pending_rewards_usd >= min_harvest

    # ------------------------------------------------------------------
    # Order generation
    # ------------------------------------------------------------------

    def generate_orders(
        self,
        farms: list[FarmOpportunity],
        correlation_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Generate execution:orders for yield farming operations.

        Returns schema-compliant order dicts. May return:
        - Empty list if no action needed
        - [withdraw] if exiting due to low APR or reward crash
        - [supply] if entering a new farming position
        - [collect_fees, supply] if harvesting and compounding

        Args:
            farms: List of farming opportunity snapshots.
            correlation_id: Optional correlation ID for order tracing.

        Returns:
            List of execution:orders-compliant order dicts.
        """
        ranked = self.evaluate(farms)
        cid = correlation_id or uuid.uuid4().hex
        now = datetime.now(UTC)
        deadline = int(now.timestamp()) + self.config.default_deadline_seconds

        # Check current farming positions
        current_positions = self.tracker.query(strategy=STRATEGY_ID)

        orders: list[dict[str, Any]] = []

        if current_positions:
            pos = current_positions[0]
            current_apr = Decimal(str(pos.protocol_data.get("farm_apr", "0")))
            pending_rewards = Decimal(
                str(pos.protocol_data.get("pending_rewards_usd", "0")),
            )
            reward_price_change = Decimal(
                str(pos.protocol_data.get("reward_token_price_change", "0")),
            )
            protocol = pos.protocol_data.get("farm_protocol", pos.protocol)

            # Exit conditions
            should_exit = (
                current_apr < self.config.exit_apr_threshold
                or reward_price_change < -self.config.reward_price_drop_exit
            )

            if should_exit:
                orders.append(self._make_order(
                    action="withdraw",
                    asset=pos.asset,
                    amount=str(pos.amount),
                    chain=pos.chain,
                    protocol=protocol,
                    correlation_id=cid,
                    deadline=deadline,
                ))
                return orders

            # Harvest and compound if rewards sufficient
            if self.should_harvest(pending_rewards):
                orders.append(self._make_order(
                    action="collect_fees",
                    asset=pos.protocol_data.get("reward_token", pos.asset),
                    amount=str(pending_rewards),
                    chain=pos.chain,
                    protocol=protocol,
                    correlation_id=cid,
                    deadline=deadline,
                ))
                # Compound back into position
                orders.append(self._make_order(
                    action="supply",
                    asset=pos.asset,
                    amount=str(pending_rewards),
                    chain=pos.chain,
                    protocol=protocol,
                    correlation_id=cid,
                    deadline=deadline,
                ))

            return orders

        # No existing position — enter new farm
        if not ranked:
            return []

        best = ranked[0]

        available = self.allocator.get_available_capital(STRATEGY_TIER)
        if available < self.config.min_position_value_usd:
            return []

        # Respect protocol exposure limit
        max_protocol = (
            self.allocator.config.max_protocol_exposure
            * self.allocator.total_capital
        )
        by_proto = self.allocator._deployed_by_protocol()
        proto_room = max_protocol - by_proto.get(best.protocol, Decimal(0))
        amount = min(available, proto_room)

        if amount < self.config.min_position_value_usd:
            return []

        check = self.allocator.check_allocation({
            "value_usd": float(amount),
            "protocol": best.protocol,
            "asset": best.asset,
            "tier": STRATEGY_TIER,
        })
        if not check.allowed:
            _logger.info(
                "Farm position blocked by allocator",
                extra={"data": {"reason": check.reason}},
            )
            return []

        orders.append(self._make_order(
            action="supply",
            asset=best.asset,
            amount=str(amount),
            chain=best.chain,
            protocol=best.protocol,
            correlation_id=cid,
            deadline=deadline,
        ))

        _logger.info(
            "Farm orders generated",
            extra={"data": {
                "order_count": len(orders),
                "target_farm": best.farm_id,
                "farm_apr": str(best.farm_apr),
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
            action: Order action (supply, withdraw, collect_fees).
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
