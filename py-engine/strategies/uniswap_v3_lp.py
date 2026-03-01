"""Uniswap V3 concentrated liquidity -- Tier 2 strategy (STRAT-003).

Manages concentrated liquidity positions on Uniswap V3 with dynamic
range adjustment based on volatility and price movement. Auto-collects
fees and recompounds at configurable intervals.
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

_logger = get_logger("uniswap-v3-lp", enable_file=False)

STRATEGY_ID = "STRAT-003"
STRATEGY_TIER = 2


# ---------------------------------------------------------------------------
# Market data types
# ---------------------------------------------------------------------------
@dataclass
class UniswapV3Pool:
    """Snapshot of a Uniswap V3 liquidity pool."""

    pair: str  # e.g. "ETH/USDC"
    token0: str
    token1: str
    current_price: Decimal
    fee_tier: int  # basis points: 100, 500, 3000, 10000
    tvl_usd: Decimal
    volume_24h_usd: Decimal
    fee_apr: Decimal  # estimated annualized fee APR
    volatility_7d: Decimal  # trailing 7-day price volatility
    chain: str = "ethereum"


@dataclass
class LPPosition:
    """State of an existing concentrated liquidity position."""

    pool_pair: str
    lower_tick: Decimal
    upper_tick: Decimal
    current_price: Decimal
    uncollected_fees_usd: Decimal
    impermanent_loss_pct: Decimal
    in_range: bool
    position_value_usd: Decimal


# ---------------------------------------------------------------------------
# Strategy
# ---------------------------------------------------------------------------
@dataclass
class UniswapV3LPConfig:
    """Strategy configuration for Uniswap V3 LP management."""

    min_pool_tvl_usd: Decimal = Decimal("1000000")  # $1M minimum TVL
    min_fee_apr: Decimal = Decimal("0.05")  # 5% minimum fee APR
    max_impermanent_loss_pct: Decimal = Decimal("0.05")  # 5% IL threshold
    range_rebalance_trigger: Decimal = Decimal("0.80")  # rebalance at 80% of range
    min_recompound_interval_hours: int = 24
    range_width_multiplier: Decimal = Decimal("2.0")  # range = multiplier * volatility
    min_position_value_usd: Decimal = Decimal("100")
    default_max_gas_wei: str = "500000000000000"  # 0.0005 ETH
    default_max_slippage_bps: int = 50
    default_deadline_seconds: int = 300
    min_fee_collect_value_usd: Decimal = Decimal("10")  # min fees to justify collection


class UniswapV3LPStrategy:
    """Tier 2: Uniswap V3 concentrated liquidity management.

    Evaluates Uniswap V3 pools by fee APR and TVL, opens concentrated
    liquidity positions with volatility-adjusted ranges, collects and
    recompounds fees, and exits when impermanent loss exceeds threshold.
    """

    def __init__(
        self,
        allocator: PortfolioAllocator,
        tracker: PositionTracker,
        config: UniswapV3LPConfig | None = None,
    ) -> None:
        self.allocator = allocator
        self.tracker = tracker
        self.config = config or UniswapV3LPConfig()
        self.status: str = "evaluating"

    # ------------------------------------------------------------------
    # Pool evaluation
    # ------------------------------------------------------------------

    def evaluate(
        self,
        pools: list[UniswapV3Pool],
    ) -> list[UniswapV3Pool]:
        """Rank Uniswap V3 pools by fee APR (descending).

        Filters to pools with sufficient TVL and fee APR above threshold.

        Args:
            pools: List of Uniswap V3 pool snapshots.

        Returns:
            Filtered and ranked list of pool opportunities.
        """
        eligible = [
            p for p in pools
            if p.tvl_usd >= self.config.min_pool_tvl_usd
            and p.fee_apr >= self.config.min_fee_apr
            and p.volume_24h_usd > 0
        ]
        ranked = sorted(eligible, key=lambda p: p.fee_apr, reverse=True)
        _logger.debug(
            "Pools evaluated",
            extra={"data": {
                "eligible_count": len(ranked),
                "top_pair": ranked[0].pair if ranked else None,
                "top_fee_apr": str(ranked[0].fee_apr) if ranked else None,
            }},
        )
        return ranked

    def should_act(
        self,
        lp_position: LPPosition | None,
        pool: UniswapV3Pool | None = None,
    ) -> bool:
        """Determine whether LP action is needed.

        Returns True if:
        - No position exists and an attractive pool is available
        - Position out of range and needs rebalancing
        - Impermanent loss exceeds threshold (exit)
        - Uncollected fees exceed minimum collection value

        Args:
            lp_position: Current LP position state, or None if no position.
            pool: Current pool data, or None.

        Returns:
            True if action should be taken.
        """
        if lp_position is None:
            # No position — act if pool is attractive
            return pool is not None and pool.fee_apr >= self.config.min_fee_apr

        # IL exceeds threshold
        if lp_position.impermanent_loss_pct > self.config.max_impermanent_loss_pct:
            return True

        # Position out of range
        if not lp_position.in_range:
            return True

        # Fees worth collecting
        if lp_position.uncollected_fees_usd >= self.config.min_fee_collect_value_usd:
            return True

        return False

    def calculate_range(
        self,
        current_price: Decimal,
        volatility_7d: Decimal,
    ) -> tuple[Decimal, Decimal]:
        """Calculate optimal price range based on volatility.

        Range width is proportional to trailing 7-day volatility,
        scaled by the configured multiplier.

        Args:
            current_price: Current token price.
            volatility_7d: Trailing 7-day price volatility (as fraction).

        Returns:
            Tuple of (lower_price, upper_price).
        """
        range_width = current_price * volatility_7d * self.config.range_width_multiplier
        half_width = range_width / 2
        lower = current_price - half_width
        upper = current_price + half_width
        # Ensure lower bound is positive
        if lower <= 0:
            lower = current_price * Decimal("0.01")
        return lower, upper

    # ------------------------------------------------------------------
    # Order generation
    # ------------------------------------------------------------------

    def generate_orders(
        self,
        pools: list[UniswapV3Pool],
        correlation_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Generate execution:orders for Uniswap V3 LP operations.

        Returns schema-compliant order dicts. May return:
        - Empty list if no action needed
        - [burn_lp] if exiting due to IL
        - [collect_fees] if harvesting fees
        - [mint_lp] if entering new position
        - [burn_lp, mint_lp] if rebalancing range

        Args:
            pools: List of Uniswap V3 pool snapshots.
            correlation_id: Optional correlation ID for order tracing.

        Returns:
            List of execution:orders-compliant order dicts.
        """
        ranked = self.evaluate(pools)
        cid = correlation_id or uuid.uuid4().hex
        now = datetime.now(UTC)
        deadline = int(now.timestamp()) + self.config.default_deadline_seconds

        # Check current positions
        current_positions = self.tracker.query(
            strategy=STRATEGY_ID, protocol="uniswap_v3",
        )

        orders: list[dict[str, Any]] = []

        if current_positions:
            pos = current_positions[0]
            il_pct = Decimal(str(pos.protocol_data.get("impermanent_loss_pct", "0")))
            in_range = pos.protocol_data.get("in_range", True)
            uncollected_fees = Decimal(
                str(pos.protocol_data.get("uncollected_fees_usd", "0")),
            )

            # Exit due to excessive IL
            if il_pct > self.config.max_impermanent_loss_pct:
                orders.append(self._make_order(
                    action="burn_lp",
                    token_in=pos.asset,
                    amount=str(pos.amount),
                    chain=pos.chain,
                    correlation_id=cid,
                    deadline=deadline,
                ))
                return orders

            # Collect fees if above threshold
            if uncollected_fees >= self.config.min_fee_collect_value_usd:
                orders.append(self._make_order(
                    action="collect_fees",
                    token_in=pos.asset,
                    amount=str(uncollected_fees),
                    chain=pos.chain,
                    correlation_id=cid,
                    deadline=deadline,
                ))

            # Rebalance if out of range
            if not in_range:
                orders.append(self._make_order(
                    action="burn_lp",
                    token_in=pos.asset,
                    amount=str(pos.amount),
                    chain=pos.chain,
                    correlation_id=cid,
                    deadline=deadline,
                ))
                # Re-enter at new range if pools still attractive
                if ranked:
                    best = ranked[0]
                    lower, upper = self.calculate_range(
                        best.current_price, best.volatility_7d,
                    )
                    orders.append(self._make_order(
                        action="mint_lp",
                        token_in=best.token0,
                        amount=str(pos.amount),
                        chain=best.chain,
                        correlation_id=cid,
                        deadline=deadline,
                        extra_params={
                            "tokenOut": best.token1,
                            "lowerPrice": str(lower),
                            "upperPrice": str(upper),
                            "feeTier": best.fee_tier,
                        },
                    ))

            return orders

        # No existing position — enter new LP position
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
        proto_room = max_protocol - by_proto.get("uniswap_v3", Decimal(0))
        amount = min(available, proto_room)

        if amount < self.config.min_position_value_usd:
            return []

        check = self.allocator.check_allocation({
            "value_usd": float(amount),
            "protocol": "uniswap_v3",
            "asset": best.token0,
            "tier": STRATEGY_TIER,
        })
        if not check.allowed:
            _logger.info(
                "LP position blocked by allocator",
                extra={"data": {"reason": check.reason}},
            )
            return []

        lower, upper = self.calculate_range(best.current_price, best.volatility_7d)
        orders.append(self._make_order(
            action="mint_lp",
            token_in=best.token0,
            amount=str(amount),
            chain=best.chain,
            correlation_id=cid,
            deadline=deadline,
            extra_params={
                "tokenOut": best.token1,
                "lowerPrice": str(lower),
                "upperPrice": str(upper),
                "feeTier": best.fee_tier,
            },
        ))

        _logger.info(
            "LP orders generated",
            extra={"data": {
                "order_count": len(orders),
                "target_pool": best.pair,
                "fee_apr": str(best.fee_apr),
                "range": f"{lower}-{upper}",
            }},
        )
        return orders

    def _make_order(
        self,
        *,
        action: str,
        token_in: str,
        amount: str,
        chain: str,
        correlation_id: str,
        deadline: int,
        extra_params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build a schema-compliant execution:orders dict.

        Args:
            action: Order action (mint_lp, burn_lp, collect_fees).
            token_in: Input token symbol.
            amount: Amount as string.
            chain: Target chain.
            correlation_id: Correlation ID for tracing.
            deadline: Unix timestamp deadline.
            extra_params: Additional action-specific parameters.

        Returns:
            Schema-compliant order dictionary.
        """
        params: dict[str, Any] = {
            "tokenIn": token_in,
            "amount": amount,
        }
        if extra_params:
            params.update(extra_params)

        return {
            "version": "1.0.0",
            "orderId": uuid.uuid4().hex,
            "correlationId": correlation_id,
            "timestamp": datetime.now(UTC).isoformat(),
            "chain": chain,
            "protocol": "uniswap_v3",
            "action": action,
            "strategy": STRATEGY_ID,
            "priority": "normal",
            "params": params,
            "limits": {
                "maxGasWei": self.config.default_max_gas_wei,
                "maxSlippageBps": self.config.default_max_slippage_bps,
                "deadlineUnix": deadline,
            },
            "useFlashbotsProtect": False,
        }
