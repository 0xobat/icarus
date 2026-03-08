"""Aerodrome stable LP auto-compound — Tier 1 strategy (LP-001).

Provides liquidity to Aerodrome stable pools on Base, stakes LP tokens
in gauges for AERO emissions, harvests when gas-optimal, and compounds
rewards back into the LP position.
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

_logger = get_logger("aerodrome-lp", enable_file=False)

STRATEGY_ID = "LP-001"
STRATEGY_TIER = 1

# LP-001 operates exclusively on Base
ALLOWED_CHAINS = frozenset({"base"})

# Known stable pairs for Aerodrome on Base
STABLE_PAIRS = frozenset({
    ("USDC", "USDbC"),
    ("USDC", "DAI"),
    ("USDC", "USDT"),
    ("USDbC", "DAI"),
})


# ---------------------------------------------------------------------------
# Market data types
# ---------------------------------------------------------------------------
@dataclass
class StablePool:
    """Snapshot of an Aerodrome stable liquidity pool."""

    pool_id: str
    token_a: str
    token_b: str
    emission_apr: Decimal  # AERO emission APR, e.g. 0.08 = 8%
    tvl_usd: Decimal
    aero_price_usd: Decimal
    gauge_address: str
    chain: str = "base"


# ---------------------------------------------------------------------------
# Strategy
# ---------------------------------------------------------------------------
@dataclass
class AerodromeLpConfig:
    """Strategy configuration for Aerodrome stable LP."""

    min_emission_apr: Decimal = Decimal("0.03")  # 3% entry threshold
    min_tvl_usd: Decimal = Decimal("500000")  # $500K minimum TVL
    min_harvest_value_usd: Decimal = Decimal("0.50")  # $0.50 min harvest
    exit_apr_threshold: Decimal = Decimal("0.015")  # 1.5% exit threshold
    aero_crash_threshold: Decimal = Decimal("-0.50")  # -50% AERO price drop
    max_allocation_pct: Decimal = Decimal("0.30")  # 30% of portfolio
    default_max_gas_wei: str = "500000000000000"  # 0.0005 ETH
    default_max_slippage_bps: int = 50
    default_deadline_seconds: int = 300


class AerodromeLpStrategy:
    """Tier 1: Aerodrome stable LP with auto-compound.

    Evaluates Aerodrome stable pools by emission APR and TVL, enters
    positions via mint_lp, stakes in gauges, harvests AERO rewards when
    gas-optimal, and compounds back into the LP. Exits when APR drops
    or AERO price crashes.
    """

    def __init__(
        self,
        allocator: PortfolioAllocator,
        tracker: PositionTracker,
        config: AerodromeLpConfig | None = None,
    ) -> None:
        self.allocator = allocator
        self.tracker = tracker
        self.config = config or AerodromeLpConfig()
        self.status: str = "evaluating"

    # ------------------------------------------------------------------
    # Pool evaluation
    # ------------------------------------------------------------------

    def evaluate(self, pools: list[StablePool]) -> list[StablePool]:
        """Filter and rank stable pools by emission APR (descending).

        Args:
            pools: List of Aerodrome stable pool snapshots.

        Returns:
            Filtered and ranked list of eligible pools.
        """
        eligible = [
            p for p in pools
            if p.chain in ALLOWED_CHAINS
            and p.emission_apr >= self.config.min_emission_apr
            and p.tvl_usd >= self.config.min_tvl_usd
        ]
        ranked = sorted(eligible, key=lambda p: p.emission_apr, reverse=True)
        _logger.debug(
            "Pools evaluated",
            extra={"data": {
                "eligible_count": len(ranked),
                "top_pool": ranked[0].pool_id if ranked else None,
                "top_apr": str(ranked[0].emission_apr) if ranked else None,
            }},
        )
        return ranked

    # ------------------------------------------------------------------
    # Harvest / exit decisions
    # ------------------------------------------------------------------

    def should_harvest(self, pending_aero_value_usd: Decimal) -> bool:
        """Check if pending AERO rewards justify a harvest transaction.

        Args:
            pending_aero_value_usd: USD value of pending AERO rewards.

        Returns:
            True if rewards meet or exceed the minimum harvest threshold.
        """
        return pending_aero_value_usd >= self.config.min_harvest_value_usd

    def should_exit(
        self,
        current_apr: Decimal,
        aero_price_change: Decimal,
    ) -> bool:
        """Determine if the LP position should be exited.

        Args:
            current_apr: Current emission APR of the pool.
            aero_price_change: 24h AERO price change as decimal (e.g. -0.55).

        Returns:
            True if APR is below exit threshold or AERO has crashed.
        """
        if current_apr < self.config.exit_apr_threshold:
            return True
        if aero_price_change < self.config.aero_crash_threshold:
            return True
        return False

    # ------------------------------------------------------------------
    # Order generation
    # ------------------------------------------------------------------

    def generate_orders(
        self,
        pools: list[StablePool],
        correlation_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Generate execution:orders for Aerodrome LP operations.

        Args:
            pools: List of Aerodrome stable pool snapshots.
            correlation_id: Optional correlation ID for order tracing.

        Returns:
            List of schema-compliant order dicts.
        """
        ranked = self.evaluate(pools)
        if not ranked:
            return []

        best = ranked[0]
        cid = correlation_id or uuid.uuid4().hex
        now = datetime.now(UTC)
        deadline = int(now.timestamp()) + self.config.default_deadline_seconds

        # Compute max deployable amount respecting allocation limit
        available = self.allocator.get_available_capital(STRATEGY_ID)
        amount = min(available, self.config.max_allocation_pct * self.allocator.total_capital)

        if amount < Decimal("1"):
            return []

        check = self.allocator.check_allocation({
            "value_usd": float(amount),
            "strategy": STRATEGY_ID,
        })
        if not check.allowed:
            _logger.info(
                "LP position blocked by allocator",
                extra={"data": {"reason": check.reason}},
            )
            return []

        orders: list[dict[str, Any]] = []

        orders.append(self._make_order(
            action="mint_lp",
            token_in=best.token_a,
            amount=str(amount),
            chain=best.chain,
            correlation_id=cid,
            deadline=deadline,
            extra_params={"tokenOut": best.token_b, "gauge": best.gauge_address},
        ))

        _logger.info(
            "LP orders generated",
            extra={"data": {
                "order_count": len(orders),
                "target_pool": best.pool_id,
                "emission_apr": str(best.emission_apr),
                "amount": str(amount),
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
        extra_params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Build a schema-compliant execution:orders dict.

        Args:
            action: Order action (mint_lp, burn_lp, stake, etc.).
            token_in: Primary input token symbol.
            amount: Amount as string.
            chain: Target chain.
            correlation_id: Correlation ID for tracing.
            deadline: Unix timestamp deadline.
            extra_params: Additional params (tokenOut, gauge, etc.).

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
            "protocol": "aerodrome",
            "action": action,
            "strategy": STRATEGY_ID,
            "priority": "normal",
            "params": params,
            "limits": {
                "maxGasWei": self.config.default_max_gas_wei,
                "maxSlippageBps": self.config.default_max_slippage_bps,
                "deadlineUnix": deadline,
            },
        }
