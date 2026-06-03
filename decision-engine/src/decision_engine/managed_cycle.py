"""ManagedPortfolioCycle — one rebalance tick for the managed-portfolio brain.

Managed-portfolio P1.4. Composes the three P1 cores (rebalance planner, order
resolver, pricing) with the risk gate and the executor publisher. Additive: the
existing lake `DecisionCycle` is untouched; P1.5 swaps `__main__` over to this.

One tick (`run_one`):
  a. Pull a MarketSnapshot (prices + gas) from the injected DataAdapter.
  b. Read current crypto/stable USD holdings from the HoldingsProvider.
  c. Estimate swap cost for the prospective correction.
  d. plan_rebalance(...) → hold or a sized corrective swap.
  e. If rebalance: resolve params → build ExecutionOrder → risk gate → publish.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Protocol, runtime_checkable

import structlog
from icarus.envelopes.orders import ExecutionOrder, OrderLimits
from icarus.protocols.data import DataAdapter
from icarus.types import MarketSnapshot, PortfolioSnapshot
from icarus.types.market import Chain

from decision_engine.cycle import ExecutorPublisher
from decision_engine.order_resolver import resolve_swap_params
from decision_engine.pricing import price_usd, estimate_swap_cost_usd
from decision_engine.rebalance import RebalancePlan, RebalanceTarget, plan_rebalance
from decision_engine.risk_gate import RiskContext, RiskGate

logger = structlog.get_logger(service="decision-engine.managed_cycle")

_CHAIN: Chain = "base"


@runtime_checkable
class HoldingsProvider(Protocol):
    """Source of current portfolio holdings, valued in USD.

    P1.4 uses stubs; P1.5 ships an on-chain balance reader that prices
    balances via the same pricing slice."""

    async def current_usd_holdings(self) -> tuple[Decimal, Decimal]:
        """Return (crypto_usd, stable_usd) for the target's two assets."""
        ...


@dataclass(frozen=True)
class ManagedCycleConfig:
    """Static knobs for the managed cycle."""

    recipient: str  # Safe address that receives swap output
    protocol: str  # e.g. "aerodrome"
    slippage_bps: int
    cost_gate_margin: Decimal
    gas_units: int
    deadline_seconds: int


@dataclass(frozen=True)
class ManagedCycleResult:
    """Summary of one managed tick."""

    action: str  # "hold" | "rebalance"
    reason: str
    published: bool
    correlation_id: str


@dataclass
class ManagedPortfolioCycle:
    """One-tick managed-portfolio orchestrator. Stateless across ticks."""

    adapter: DataAdapter
    holdings: HoldingsProvider
    target: RebalanceTarget
    risk_gate: RiskGate
    publisher: ExecutorPublisher
    config: ManagedCycleConfig

    async def run_one(self) -> ManagedCycleResult:
        correlation_id = f"managed-{uuid.uuid4().hex[:12]}"
        log = logger.bind(correlation_id=correlation_id)

        market = await self.adapter.fetch_live(_CHAIN)
        crypto_usd, stable_usd = await self.holdings.current_usd_holdings()
        nav = crypto_usd + stable_usd

        # Estimate cost on the prospective correction so the cost gate has a
        # size-aware figure. Mirrors plan_rebalance's correction formula.
        target_crypto_usd = self.target.crypto_weight * nav
        prospective_correction = abs(crypto_usd - target_crypto_usd)
        eth_price = price_usd("ETH", market)
        est_cost = estimate_swap_cost_usd(
            trade_usd=prospective_correction,
            slippage_bps=self.config.slippage_bps,
            market=market,
            eth_price_usd=eth_price,
            gas_units=self.config.gas_units,
        )

        plan = plan_rebalance(
            crypto_usd=crypto_usd,
            stable_usd=stable_usd,
            target=self.target,
            est_cost_usd=est_cost,
            cost_gate_margin=self.config.cost_gate_margin,
        )

        if plan.action == "hold":
            log.info("managed_hold", reason=plan.reason, nav_usd=str(nav))
            return ManagedCycleResult(
                action="hold", reason=plan.reason, published=False,
                correlation_id=correlation_id,
            )

        order = self._build_order(plan, market, correlation_id)
        ctx = RiskContext(
            portfolio=PortfolioSnapshot(
                nav_usd=nav, positions={}, cash_usd=stable_usd,
                drawdown_from_peak=Decimal("0"), last_rebalance=datetime.now(UTC),
            ),
            market=market,
        )
        verdict = self.risk_gate.check(order, ctx)
        if not verdict.passed:
            log.warning("managed_order_dropped", checker=verdict.checker, reason=verdict.reason)
            return ManagedCycleResult(
                action="rebalance", reason=f"dropped:{verdict.checker}:{verdict.reason}",
                published=False, correlation_id=correlation_id,
            )

        await self.publisher.publish_order(_CHAIN, order)
        log.info(
            "managed_order_published", order_id=order.order_id,
            from_symbol=plan.from_symbol, to_symbol=plan.to_symbol,
            usd_amount=str(plan.usd_amount),
        )
        return ManagedCycleResult(
            action="rebalance", reason=plan.reason, published=True,
            correlation_id=correlation_id,
        )

    def _build_order(
        self, plan: RebalancePlan, market: MarketSnapshot, correlation_id: str
    ) -> ExecutionOrder:
        """Resolve the plan's swap into an executor-ready ExecutionOrder."""
        assert plan.from_symbol and plan.to_symbol and plan.usd_amount is not None
        now = datetime.now(UTC)
        deadline_unix = int(now.timestamp()) + self.config.deadline_seconds
        params = resolve_swap_params(
            chain=_CHAIN,
            token_in_symbol=plan.from_symbol,
            token_out_symbol=plan.to_symbol,
            usd_amount=plan.usd_amount,
            price_in_usd=price_usd(plan.from_symbol, market),
            price_out_usd=price_usd(plan.to_symbol, market),
            recipient=self.config.recipient,
            slippage_bps=self.config.slippage_bps,
            deadline_unix=deadline_unix,
        )
        limits = OrderLimits(
            max_slippage_bps=self.config.slippage_bps,
            deadline_unix=deadline_unix,
        )
        return ExecutionOrder(
            order_id=uuid.uuid4().hex,
            correlation_id=correlation_id,
            timestamp=now,
            chain=_CHAIN,
            protocol=self.config.protocol,
            action="swap",
            strategy=f"REBAL:{_CHAIN}",
            params=params,
            limits=limits,
            solana_specific=None,
        )


__all__ = [
    "HoldingsProvider",
    "ManagedCycleConfig",
    "ManagedCycleResult",
    "ManagedPortfolioCycle",
]
