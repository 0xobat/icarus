"""Flash loan arbitrage -- Tier 3 strategy (STRAT-005).

Detects cross-DEX price discrepancies from market events and executes
atomic flash loan arbitrage sequences: borrow -> swap -> repay -> profit.
Uses flash loan executor (EXEC-007) for zero-capital execution.
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

_logger = get_logger("flash-loan-arb", enable_file=False)

STRATEGY_ID = "STRAT-005"
STRATEGY_TIER = 3


# ---------------------------------------------------------------------------
# Market data types
# ---------------------------------------------------------------------------
@dataclass
class ArbOpportunity:
    """A detected cross-DEX arbitrage opportunity."""

    asset: str  # token being arbitraged
    source_dex: str  # DEX with lower price
    target_dex: str  # DEX with higher price
    source_price: Decimal  # price on source DEX
    target_price: Decimal  # price on target DEX
    price_spread_pct: Decimal  # spread as percentage
    available_liquidity: Decimal  # max flash loan amount
    estimated_gas_cost_usd: Decimal  # expected gas cost
    estimated_profit_usd: Decimal  # expected profit after gas
    chain: str = "ethereum"


# ---------------------------------------------------------------------------
# Strategy
# ---------------------------------------------------------------------------
@dataclass
class FlashLoanArbConfig:
    """Strategy configuration for flash loan arbitrage."""

    min_profit_gas_multiple: Decimal = Decimal("2.0")  # profit must exceed 2x gas
    max_gas_gwei: Decimal = Decimal("100")  # max gas price in gwei
    max_flash_loan_eth: Decimal = Decimal("1000")  # max 1000 ETH equivalent
    min_spread_pct: Decimal = Decimal("0.005")  # 0.5% minimum spread
    min_position_value_usd: Decimal = Decimal("100")
    default_max_gas_wei: str = "2000000000000000"  # 0.002 ETH (higher for flash loans)
    default_max_slippage_bps: int = 30  # tighter slippage for arb
    default_deadline_seconds: int = 120  # shorter deadline for arb


class FlashLoanArbStrategy:
    """Tier 3: Flash loan arbitrage across DEXes.

    Detects cross-DEX price discrepancies, validates profitability after
    gas costs, and constructs atomic flash loan sequences for zero-capital
    execution. All positions open and close within a single transaction.
    """

    def __init__(
        self,
        allocator: PortfolioAllocator,
        tracker: PositionTracker,
        config: FlashLoanArbConfig | None = None,
    ) -> None:
        self.allocator = allocator
        self.tracker = tracker
        self.config = config or FlashLoanArbConfig()
        self.status: str = "evaluating"

    # ------------------------------------------------------------------
    # Opportunity evaluation
    # ------------------------------------------------------------------

    def evaluate(
        self,
        opportunities: list[ArbOpportunity],
    ) -> list[ArbOpportunity]:
        """Rank arbitrage opportunities by estimated profit (descending).

        Filters to opportunities with sufficient spread and profit/gas ratio.

        Args:
            opportunities: List of detected arbitrage opportunities.

        Returns:
            Filtered and ranked list of profitable opportunities.
        """
        eligible = [
            opp for opp in opportunities
            if opp.price_spread_pct >= self.config.min_spread_pct
            and opp.estimated_profit_usd > 0
            and opp.estimated_gas_cost_usd > 0
            and (opp.estimated_profit_usd / opp.estimated_gas_cost_usd)
            >= self.config.min_profit_gas_multiple
            and opp.available_liquidity > 0
        ]
        ranked = sorted(
            eligible, key=lambda o: o.estimated_profit_usd, reverse=True,
        )
        _logger.debug(
            "Arb opportunities evaluated",
            extra={"data": {
                "eligible_count": len(ranked),
                "top_profit": str(ranked[0].estimated_profit_usd) if ranked else None,
                "top_spread": str(ranked[0].price_spread_pct) if ranked else None,
            }},
        )
        return ranked

    def should_act(
        self,
        opportunity: ArbOpportunity,
        current_gas_gwei: Decimal = Decimal("30"),
    ) -> bool:
        """Determine whether an arbitrage opportunity should be executed.

        Validates profitability, gas conditions, and flash loan limits.

        Args:
            opportunity: The arbitrage opportunity to evaluate.
            current_gas_gwei: Current gas price in gwei.

        Returns:
            True if the opportunity should be executed.
        """
        # Gas price check
        if current_gas_gwei > self.config.max_gas_gwei:
            _logger.debug(
                "Gas too high for arb",
                extra={"data": {
                    "current_gas": str(current_gas_gwei),
                    "max_gas": str(self.config.max_gas_gwei),
                }},
            )
            return False

        # Spread check
        if opportunity.price_spread_pct < self.config.min_spread_pct:
            return False

        # Profit/gas ratio check
        if opportunity.estimated_gas_cost_usd <= 0:
            return False
        ratio = opportunity.estimated_profit_usd / opportunity.estimated_gas_cost_usd
        if ratio < self.config.min_profit_gas_multiple:
            return False

        # Flash loan size check
        if opportunity.available_liquidity > self.config.max_flash_loan_eth:
            # Cap at maximum, but still executable
            pass

        return True

    # ------------------------------------------------------------------
    # Order generation
    # ------------------------------------------------------------------

    def generate_orders(
        self,
        opportunities: list[ArbOpportunity],
        correlation_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Generate execution:orders for flash loan arbitrage.

        Returns a single flash_loan order that encapsulates the full
        atomic sequence: borrow -> swap on source -> swap on target -> repay.

        Args:
            opportunities: List of detected arbitrage opportunities.
            correlation_id: Optional correlation ID for order tracing.

        Returns:
            List of execution:orders-compliant order dicts (0 or 1 orders).
        """
        ranked = self.evaluate(opportunities)
        if not ranked:
            return []

        best = ranked[0]
        cid = correlation_id or uuid.uuid4().hex
        now = datetime.now(UTC)
        deadline = int(now.timestamp()) + self.config.default_deadline_seconds

        # Cap flash loan amount
        loan_amount = min(best.available_liquidity, self.config.max_flash_loan_eth)

        if loan_amount < self.config.min_position_value_usd:
            return []

        # Check tier allocation
        check = self.allocator.check_allocation({
            "value_usd": float(best.estimated_profit_usd),
            "protocol": "aave_v3",
            "asset": best.asset,
            "tier": STRATEGY_TIER,
        })
        if not check.allowed:
            _logger.info(
                "Flash loan arb blocked by allocator",
                extra={"data": {"reason": check.reason}},
            )
            return []

        order = self._make_order(
            action="flash_loan",
            asset=best.asset,
            amount=str(loan_amount),
            chain=best.chain,
            correlation_id=cid,
            deadline=deadline,
            extra_params={
                "sourceDex": best.source_dex,
                "targetDex": best.target_dex,
                "sourcePrice": str(best.source_price),
                "targetPrice": str(best.target_price),
                "estimatedProfitUsd": str(best.estimated_profit_usd),
                "estimatedGasCostUsd": str(best.estimated_gas_cost_usd),
            },
        )

        _logger.info(
            "Flash loan arb order generated",
            extra={"data": {
                "asset": best.asset,
                "spread": str(best.price_spread_pct),
                "profit_usd": str(best.estimated_profit_usd),
                "loan_amount": str(loan_amount),
            }},
        )
        return [order]

    def _make_order(
        self,
        *,
        action: str,
        asset: str,
        amount: str,
        chain: str,
        correlation_id: str,
        deadline: int,
        extra_params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build a schema-compliant execution:orders dict.

        Args:
            action: Order action (flash_loan).
            asset: Token symbol.
            amount: Amount as string.
            chain: Target chain.
            correlation_id: Correlation ID for tracing.
            deadline: Unix timestamp deadline.
            extra_params: Additional action-specific parameters.

        Returns:
            Schema-compliant order dictionary.
        """
        params: dict[str, Any] = {
            "tokenIn": asset,
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
            "protocol": "aave_v3",
            "action": action,
            "strategy": STRATEGY_ID,
            "priority": "urgent",
            "params": params,
            "limits": {
                "maxGasWei": self.config.default_max_gas_wei,
                "maxSlippageBps": self.config.default_max_slippage_bps,
                "deadlineUnix": deadline,
            },
            "useFlashbotsProtect": True,
        }
