"""Lido liquid staking -- Tier 1 strategy (STRAT-002).

Stakes ETH via Lido to receive stETH, wraps to wstETH for further DeFi
deployment. Monitors staking APR and adjusts position sizing to maintain
optimal yield while preserving liquidity.
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

_logger = get_logger("lido-staking", enable_file=False)

STRATEGY_ID = "STRAT-002"
STRATEGY_TIER = 1


# ---------------------------------------------------------------------------
# Market data types
# ---------------------------------------------------------------------------
@dataclass
class LidoStakingData:
    """Snapshot of Lido staking conditions."""

    staking_apr: Decimal  # annualized, e.g. 0.035 = 3.5%
    steth_eth_ratio: Decimal  # stETH/ETH price ratio, ideally ~1.0
    total_staked: Decimal  # total ETH staked in Lido
    available_eth: Decimal  # ETH available in portfolio
    chain: str = "ethereum"


# ---------------------------------------------------------------------------
# Strategy
# ---------------------------------------------------------------------------
@dataclass
class LidoStakingConfig:
    """Strategy configuration for Lido liquid staking."""

    min_staking_apr: Decimal = Decimal("0.030")  # 3.0% entry threshold
    exit_apr_threshold: Decimal = Decimal("0.020")  # 2.0% exit threshold
    max_peg_deviation: Decimal = Decimal("0.01")  # 1% stETH/ETH depeg tolerance
    exit_peg_deviation: Decimal = Decimal("0.02")  # 2% depeg triggers exit
    max_position_adjustment_pct: Decimal = Decimal("0.10")  # 10% per cycle
    min_position_value_usd: Decimal = Decimal("100")
    default_max_gas_wei: str = "500000000000000"  # 0.0005 ETH
    default_max_slippage_bps: int = 50
    default_deadline_seconds: int = 300


class LidoStakingStrategy:
    """Tier 1: Lido liquid staking for consistent ETH yield.

    Stakes ETH through Lido to earn staking rewards, monitors the
    staking APR, and manages stETH/wstETH positions. Exits when
    APR drops or stETH depegs beyond threshold.
    """

    def __init__(
        self,
        allocator: PortfolioAllocator,
        tracker: PositionTracker,
        config: LidoStakingConfig | None = None,
    ) -> None:
        self.allocator = allocator
        self.tracker = tracker
        self.config = config or LidoStakingConfig()
        self.status: str = "evaluating"

    # ------------------------------------------------------------------
    # Market evaluation
    # ------------------------------------------------------------------

    def evaluate(
        self,
        staking_data: list[LidoStakingData],
    ) -> list[LidoStakingData]:
        """Rank Lido staking opportunities by APR (descending).

        Filters to entries with acceptable stETH/ETH peg and positive APR
        above the minimum threshold.

        Args:
            staking_data: List of Lido staking snapshots across chains.

        Returns:
            Filtered and ranked list of staking opportunities.
        """
        eligible = [
            d for d in staking_data
            if d.staking_apr >= self.config.min_staking_apr
            and abs(d.steth_eth_ratio - Decimal("1")) <= self.config.max_peg_deviation
            and d.total_staked > 0
        ]
        ranked = sorted(eligible, key=lambda d: d.staking_apr, reverse=True)
        _logger.debug(
            "Staking data evaluated",
            extra={"data": {
                "eligible_count": len(ranked),
                "top_apr": str(ranked[0].staking_apr) if ranked else None,
            }},
        )
        return ranked

    def should_act(
        self,
        current_apr: Decimal,
        steth_eth_ratio: Decimal,
    ) -> bool:
        """Determine whether staking or unstaking action is needed.

        Returns True if:
        - No current position and APR is attractive
        - Current position and APR dropped below exit threshold
        - stETH depeg exceeds exit tolerance

        Args:
            current_apr: Current Lido staking APR.
            steth_eth_ratio: Current stETH/ETH price ratio.

        Returns:
            True if action should be taken.
        """
        peg_deviation = abs(steth_eth_ratio - Decimal("1"))

        # Depeg triggers action
        if peg_deviation > self.config.exit_peg_deviation:
            _logger.info(
                "Depeg detected",
                extra={"data": {
                    "peg_deviation": str(peg_deviation),
                    "threshold": str(self.config.exit_peg_deviation),
                }},
            )
            return True

        # APR below exit threshold triggers action
        if current_apr < self.config.exit_apr_threshold:
            return True

        # APR above entry threshold on new position
        if current_apr >= self.config.min_staking_apr:
            return True

        return False

    # ------------------------------------------------------------------
    # Order generation
    # ------------------------------------------------------------------

    def generate_orders(
        self,
        staking_data: list[LidoStakingData],
        correlation_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Generate execution:orders for Lido staking operations.

        Returns schema-compliant order dicts. May return:
        - Empty list if no action needed
        - [unstake] if exiting due to depeg or low APR
        - [stake] if entering a new staking position

        Args:
            staking_data: List of Lido staking snapshots.
            correlation_id: Optional correlation ID for order tracing.

        Returns:
            List of execution:orders-compliant order dicts.
        """
        ranked = self.evaluate(staking_data)
        cid = correlation_id or uuid.uuid4().hex
        now = datetime.now(UTC)
        deadline = int(now.timestamp()) + self.config.default_deadline_seconds

        # Check current Lido positions
        current_positions = self.tracker.query(
            strategy=STRATEGY_ID, protocol="lido",
        )

        orders: list[dict[str, Any]] = []

        if current_positions:
            pos = current_positions[0]
            current_apr = Decimal(
                str(pos.protocol_data.get("staking_apr", "0")),
            )
            steth_ratio = Decimal(
                str(pos.protocol_data.get("steth_eth_ratio", "1")),
            )

            peg_deviation = abs(steth_ratio - Decimal("1"))

            # Exit conditions: depeg or low APR
            should_exit = (
                peg_deviation > self.config.exit_peg_deviation
                or current_apr < self.config.exit_apr_threshold
            )

            if should_exit:
                orders.append(self._make_order(
                    action="unstake",
                    asset="stETH",
                    amount=str(pos.amount),
                    chain=pos.chain,
                    correlation_id=cid,
                    deadline=deadline,
                ))
                return orders

            # No action needed if position exists and conditions are fine
            return []

        # No existing position — consider staking
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
        proto_room = max_protocol - by_proto.get("lido", Decimal(0))
        amount = min(available, proto_room)

        if amount < self.config.min_position_value_usd:
            return []

        # Check allocation limits
        check = self.allocator.check_allocation({
            "value_usd": float(amount),
            "protocol": "lido",
            "asset": "ETH",
            "tier": STRATEGY_TIER,
        })
        if not check.allowed:
            _logger.info(
                "Staking blocked by allocator",
                extra={"data": {"reason": check.reason}},
            )
            return []

        # Apply max position adjustment limit
        max_adjustment = self.allocator.total_capital * self.config.max_position_adjustment_pct
        amount = min(amount, max_adjustment)

        if amount < self.config.min_position_value_usd:
            return []

        orders.append(self._make_order(
            action="stake",
            asset="ETH",
            amount=str(amount),
            chain=best.chain,
            correlation_id=cid,
            deadline=deadline,
        ))

        _logger.info(
            "Staking orders generated",
            extra={"data": {
                "order_count": len(orders),
                "target_apr": str(best.staking_apr),
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
        correlation_id: str,
        deadline: int,
    ) -> dict[str, Any]:
        """Build a schema-compliant execution:orders dict.

        Args:
            action: Order action (stake, unstake).
            asset: Token symbol.
            amount: Amount as string.
            chain: Target chain.
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
            "protocol": "lido",
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
