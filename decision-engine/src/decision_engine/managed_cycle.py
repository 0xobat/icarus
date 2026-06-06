"""ManagedPortfolioCycle — one rebalance tick for the managed-portfolio brain.

Managed-portfolio P2.2 (multi-asset). Composes the P1/P2 cores (multi-asset
rebalance planner, order resolver, pricing) with the risk gate and the executor
publisher.

One tick (`run_one`):
  a. Pull a MarketSnapshot (prices + gas) from the injected DataAdapter.
  b. Read per-asset USD holdings ({symbol: usd}) from the HoldingsProvider.
  c. Two-pass cost gate: size the prospective trade, estimate its cost, then
     let plan_multi_rebalance's own gate decide hold-vs-go.
  d. plan_multi_rebalance(...) → hold or one sized corrective swap (most-out-of-
     band asset, routed through the hub).
  e. If rebalance: resolve params → build ExecutionOrder → risk gate → publish.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Protocol, runtime_checkable

import structlog
from icarus.envelopes.orders import ExecutionOrder, OrderLimits
from icarus.protocols.data import DataAdapter
from icarus.types import MarketSnapshot, PortfolioSnapshot
from icarus.types.market import Chain

from decision_engine.cycle import ExecutorPublisher
from decision_engine.order_resolver import DEFAULT_CHAIN_ID, resolve_swap_params
from decision_engine.pricing import estimate_swap_cost_usd, price_usd
from decision_engine.rebalance import (
    MultiAssetTarget,
    RebalancePlan,
    plan_multi_rebalance,
)
from decision_engine.risk_gate import RiskContext, RiskGate

logger = structlog.get_logger(service="decision-engine.managed_cycle")

_CHAIN: Chain = "base"


@runtime_checkable
class HoldingsProvider(Protocol):
    """Source of current portfolio holdings, valued in USD per asset.

    P2.2 reads on-chain balances for the N target assets and prices them via the
    pricing slice into a {symbol: usd} dict."""

    async def current_usd_by_asset(self) -> Mapping[str, Decimal]:
        """Return {symbol: usd} for each target asset."""
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
    chain_id: int = DEFAULT_CHAIN_ID
    # Which venue each asset sits in (USDC/cbBTC → "aave_v3"; WETH/wstETH →
    # "wallet"). Threaded to the P2.5 exposure checker via RiskContext.
    venue_by_asset: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ManagedCycleResult:
    """Summary of one managed tick."""

    action: str  # "hold" | "rebalance"
    reason: str
    published: bool
    correlation_id: str
    order_id: str | None = None
    from_symbol: str | None = None
    to_symbol: str | None = None
    usd_amount: Decimal | None = None


@dataclass
class ManagedPortfolioCycle:
    """One-tick managed-portfolio orchestrator. Stateless across ticks."""

    adapter: DataAdapter
    holdings: HoldingsProvider
    target: MultiAssetTarget
    risk_gate: RiskGate
    publisher: ExecutorPublisher
    config: ManagedCycleConfig

    async def run_one(self) -> ManagedCycleResult:
        correlation_id = f"managed-{uuid.uuid4().hex[:12]}"
        log = logger.bind(correlation_id=correlation_id)

        market = await self.adapter.fetch_live(_CHAIN)
        holdings = dict(await self.holdings.current_usd_by_asset())
        nav = sum(holdings.values(), Decimal("0"))

        # The N-asset correction size is whatever the planner selects, but the
        # cost gate needs that size. Two-pass: first price the prospective trade
        # with no cost gate, estimate its cost, then let the planner's own gate
        # decide hold-vs-go on the second pass. Both passes are pure + cheap.
        prospective = plan_multi_rebalance(
            holdings=holdings, target=self.target,
            est_cost_usd=Decimal("0"), cost_gate_margin=self.config.cost_gate_margin,
        )
        if prospective.action == "hold":
            log.info("managed_hold", reason=prospective.reason, nav_usd=str(nav))
            return ManagedCycleResult(
                action="hold", reason=prospective.reason, published=False,
                correlation_id=correlation_id,
            )

        est_cost = estimate_swap_cost_usd(
            trade_usd=prospective.usd_amount,
            slippage_bps=self.config.slippage_bps,
            market=market,
            eth_price_usd=price_usd("ETH", market),
            gas_units=self.config.gas_units,
        )
        plan = plan_multi_rebalance(
            holdings=holdings, target=self.target,
            est_cost_usd=est_cost, cost_gate_margin=self.config.cost_gate_margin,
        )

        if plan.action == "hold":
            log.info("managed_hold", reason=plan.reason, nav_usd=str(nav))
            return ManagedCycleResult(
                action="hold", reason=plan.reason, published=False,
                correlation_id=correlation_id,
            )

        order = self._build_order(plan, market, correlation_id)
        # Prospective post-trade holdings: the swap moves usd_amount from the
        # `from` asset into the `to` asset. Threaded to the exposure checker so
        # it caps concentration on the state this order WOULD produce (P2.5).
        prospective = dict(holdings)
        prospective[plan.from_symbol] = (
            prospective.get(plan.from_symbol, Decimal("0")) - plan.usd_amount
        )
        prospective[plan.to_symbol] = (
            prospective.get(plan.to_symbol, Decimal("0")) + plan.usd_amount
        )
        ctx = RiskContext(
            portfolio=PortfolioSnapshot(
                nav_usd=nav, positions={}, cash_usd=holdings.get(self.target.hub, Decimal("0")),
                drawdown_from_peak=Decimal("0"), last_rebalance=datetime.now(UTC),
            ),
            market=market,
            order_value_usd=plan.usd_amount,
            prospective_holdings=prospective,
            venue_by_asset=self.config.venue_by_asset,
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
            correlation_id=correlation_id, order_id=order.order_id,
            from_symbol=plan.from_symbol, to_symbol=plan.to_symbol,
            usd_amount=plan.usd_amount,
        )

    def _build_order(
        self, plan: RebalancePlan, market: MarketSnapshot, correlation_id: str
    ) -> ExecutionOrder:
        """Resolve the plan's swap into an executor-ready ExecutionOrder."""
        if not (plan.from_symbol and plan.to_symbol and plan.usd_amount is not None):
            raise ValueError(
                f"_build_order requires a fully-populated rebalance plan, got {plan!r}"
            )
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
            chain_id=self.config.chain_id,
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
