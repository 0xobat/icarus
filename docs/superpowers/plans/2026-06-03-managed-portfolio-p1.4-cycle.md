# Managed Portfolio P1.4 — Managed Portfolio Cycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** A new, additive `ManagedPortfolioCycle` that orchestrates one rebalance tick: read holdings → plan (P1.3) → if rebalancing, resolve params (P1.1) → build `ExecutionOrder` → risk gate → publish. Unit-tested end-to-end with injected stubs. Does NOT touch `__main__` or live service wiring (that's P1.5).

**Architecture:** New module `decision_engine/managed_cycle.py` with a `HoldingsProvider` Protocol (returns current crypto/stable USD), a `ManagedCycleConfig` (recipient/Safe address, slippage, cost-gate margin, gas units), and `ManagedPortfolioCycle` (all collaborators injected, like the existing `DecisionCycle`). Composes the three P1 cores + `RiskGate` + `ExecutorPublisher`. The existing lake `DecisionCycle` is left untouched; P1.5 swaps `__main__` over.

**Tech Stack:** Python 3.13, `uv`, `pytest` (`asyncio_mode=auto`), pydantic envelopes, structlog.

---

## Context the implementer needs (exact contracts — already verified)

- **Cores to compose:**
  - `decision_engine.order_resolver.resolve_swap_params(*, chain, token_in_symbol, token_out_symbol, usd_amount, price_in_usd, price_out_usd, recipient, slippage_bps, deadline_unix, stable=False) -> OrderParams`
  - `decision_engine.pricing.price_usd(symbol, market) -> Decimal` and `estimate_swap_cost_usd(*, trade_usd, slippage_bps, market, eth_price_usd, gas_units=DEFAULT_SWAP_GAS_UNITS) -> Decimal`
  - `decision_engine.rebalance.plan_rebalance(*, crypto_usd, stable_usd, target, est_cost_usd, cost_gate_margin) -> RebalancePlan` with `RebalanceTarget(crypto_symbol, stable_symbol, crypto_weight, band)` and `RebalancePlan(action, reason, from_symbol, to_symbol, usd_amount)`.
- **Order envelope** (`icarus.envelopes.orders`): `ExecutionOrder(version, order_id[min_len 8], correlation_id, timestamp, chain, protocol, action, strategy, template_id, candidate_id, priority, params, limits, solana_specific)`. Validator: when `template_id` and `candidate_id` are BOTH `None` and `strategy` does NOT start with `"CB:"`, it is accepted (use `strategy="REBAL:<chain>"`, template/candidate None). `OrderLimits(max_gas_wei=None, max_priority_fee_lamports=None, max_slippage_bps:int[0..1000], deadline_unix:int)`. For `chain="base"`, `solana_specific` MUST be `None`.
- **DataAdapter** (`icarus.protocols.data.DataAdapter`): `async fetch_live(chain) -> MarketSnapshot`. `MarketSnapshot` has `.prices: Mapping[str,Decimal]` (Base RPC adapter sets `{"ETH": <usd>}`) and `.gas_gwei: Decimal`.
- **Risk gate** (`decision_engine.risk_gate`): `RiskGate.check(order, ctx) -> RiskDecision` with `.passed: bool`, `.checker`, `.reason`. `RiskContext(portfolio: PortfolioSnapshot, market: MarketSnapshot)`.
- **PortfolioSnapshot** (`icarus.types`): `PortfolioSnapshot(nav_usd, positions: Mapping[str,Position], cash_usd, drawdown_from_peak, last_rebalance: datetime)`. For the managed cycle, positions can be `{}`.
- **Publisher** (`decision_engine.cycle.ExecutorPublisher` Protocol): `async publish_order(chain, order) -> None`. The production impl is `RedisExecutorPublisher`.
- **Style:** match `decision_engine/cycle.py` — `from __future__ import annotations`, `@dataclass`, `structlog.get_logger(service=...)`, injected collaborators, async `run_one`.
- Tests: `decision-engine/tests/test_managed_cycle_unit.py`. Run: `uv run pytest <path> -v`.

## File Structure
- **Create:** `decision-engine/src/decision_engine/managed_cycle.py` — `HoldingsProvider`, `ManagedCycleConfig`, `ManagedCycleResult`, `ManagedPortfolioCycle`.
- **Create:** `decision-engine/tests/test_managed_cycle_unit.py` — unit tests with stub holdings provider / adapter / publisher.

---

## Task 1: Scaffolding — config, protocols, result, and the hold path

**Files:** Create `decision-engine/src/decision_engine/managed_cycle.py`; Test `decision-engine/tests/test_managed_cycle_unit.py`.

- [ ] **Step 1: Write the failing test**

Create `decision-engine/tests/test_managed_cycle_unit.py`:

```python
"""Unit tests for the managed-portfolio cycle (P1.4)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from icarus.envelopes.orders import ExecutionOrder
from icarus.types import MarketSnapshot
from icarus.types.market import Chain

from decision_engine.cycle import ExecutorPublisher
from decision_engine.managed_cycle import (
    HoldingsProvider,
    ManagedCycleConfig,
    ManagedPortfolioCycle,
)
from decision_engine.rebalance import RebalanceTarget
from decision_engine.risk_gate import RiskGate

_SAFE = "0x1111111111111111111111111111111111111111"
_TARGET = RebalanceTarget(
    crypto_symbol="WETH", stable_symbol="USDC",
    crypto_weight=Decimal("0.6"), band=Decimal("0.10"),
)
_CONFIG = ManagedCycleConfig(
    recipient=_SAFE, protocol="aerodrome", slippage_bps=50,
    cost_gate_margin=Decimal("4"), gas_units=200_000, deadline_seconds=60,
)


class _FakeAdapter:
    name = "fake"
    historical_supported = False

    def __init__(self, eth_usd: Decimal, gas_gwei: Decimal) -> None:
        self._eth, self._gas = eth_usd, gas_gwei

    async def fetch_live(self, chain: Chain) -> MarketSnapshot:
        return MarketSnapshot(
            timestamp=datetime(2026, 6, 3, tzinfo=UTC), chain=chain,
            prices={"ETH": self._eth}, apys={}, pool_state={},
            gas_gwei=self._gas, metadata={},
        )


class _StubHoldings:
    def __init__(self, crypto_usd: Decimal, stable_usd: Decimal) -> None:
        self._c, self._s = crypto_usd, stable_usd

    async def current_usd_holdings(self) -> tuple[Decimal, Decimal]:
        return self._c, self._s


class _CapturePublisher:
    def __init__(self) -> None:
        self.published: list[tuple[str, ExecutionOrder]] = []

    async def publish_order(self, chain: Chain, order: ExecutionOrder) -> None:
        self.published.append((chain, order))


def _cycle(holdings: _StubHoldings, publisher: _CapturePublisher) -> ManagedPortfolioCycle:
    return ManagedPortfolioCycle(
        adapter=_FakeAdapter(Decimal("3000"), Decimal("1")),
        holdings=holdings,
        target=_TARGET,
        risk_gate=RiskGate(checkers=[]),  # permissive: empty gate passes everything
        publisher=publisher,
        config=_CONFIG,
    )


def test_protocols_satisfied() -> None:
    assert isinstance(_StubHoldings(Decimal("1"), Decimal("1")), HoldingsProvider)
    assert isinstance(_CapturePublisher(), ExecutorPublisher)


@pytest.mark.asyncio
async def test_within_band_publishes_nothing() -> None:
    publisher = _CapturePublisher()
    cycle = _cycle(_StubHoldings(Decimal("6500"), Decimal("3500")), publisher)
    result = await cycle.run_one()
    assert result.action == "hold"
    assert result.published is False
    assert publisher.published == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest decision-engine/tests/test_managed_cycle_unit.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'decision_engine.managed_cycle'`

- [ ] **Step 3: Implement**

Create `decision-engine/src/decision_engine/managed_cycle.py`:

```python
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
from icarus.types import PortfolioSnapshot
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
        self, plan: RebalancePlan, market: object, correlation_id: str
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
            price_in_usd=price_usd(plan.from_symbol, market),  # type: ignore[arg-type]
            price_out_usd=price_usd(plan.to_symbol, market),  # type: ignore[arg-type]
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
```

Note on the `market: object` + `# type: ignore` in `_build_order`: `price_usd` expects a `MarketSnapshot`; passing it through is fine at runtime. If the implementer prefers, type the param as `MarketSnapshot` (import from `icarus.types`) and drop the ignores — cleaner. Use the cleaner form.

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest decision-engine/tests/test_managed_cycle_unit.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add decision-engine/src/decision_engine/managed_cycle.py decision-engine/tests/test_managed_cycle_unit.py
git commit -m "feat(daedalus): P1.4 managed cycle — scaffolding + hold path"
```

---

## Task 2: The rebalance path — build, gate, publish

**Files:** Modify `decision-engine/tests/test_managed_cycle_unit.py` (impl already complete from Task 1; these tests pin the rebalance path — immediate green is expected).

- [ ] **Step 1: Write the tests**

Append to `decision-engine/tests/test_managed_cycle_unit.py`:

```python
@pytest.mark.asyncio
async def test_overweight_publishes_weth_to_usdc_swap() -> None:
    # crypto 0.80 of $10k → sell $2000 WETH→USDC. cost: gas≈$0.60 + slip $10 = ~$10.60;
    # margin 4 → ~$42 threshold; correction $2000 >> threshold → proceeds.
    publisher = _CapturePublisher()
    cycle = _cycle(_StubHoldings(Decimal("8000"), Decimal("2000")), publisher)
    result = await cycle.run_one()
    assert result.action == "rebalance"
    assert result.published is True
    assert len(publisher.published) == 1
    chain, order = publisher.published[0]
    assert chain == "base"
    assert order.chain == "base"
    assert order.action == "swap"
    assert order.strategy == "REBAL:base"
    assert order.template_id is None and order.candidate_id is None
    assert order.solana_specific is None
    # WETH→USDC: token_in is WETH address, token_out is USDC address.
    assert order.params.token_in == "0x4200000000000000000000000000000000000006"
    assert order.params.token_out == "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
    assert order.params.recipient == _SAFE
    # $2000 of WETH at $3000 = 0.6666... WETH → 666666666666666666 wei (floored).
    assert order.params.amount == Decimal("666666666666666666")


@pytest.mark.asyncio
async def test_risk_gate_rejection_blocks_publish() -> None:
    from decision_engine.risk_gate import RiskContext, RiskDecision

    class _RejectAll:
        name = "reject_all"

        def check(self, order: ExecutionOrder, ctx: RiskContext) -> RiskDecision:
            return RiskDecision(passed=False, checker=self.name, reason="test reject")

    publisher = _CapturePublisher()
    cycle = ManagedPortfolioCycle(
        adapter=_FakeAdapter(Decimal("3000"), Decimal("1")),
        holdings=_StubHoldings(Decimal("8000"), Decimal("2000")),
        target=_TARGET,
        risk_gate=RiskGate(checkers=[_RejectAll()]),
        publisher=publisher,
        config=_CONFIG,
    )
    result = await cycle.run_one()
    assert result.action == "rebalance"
    assert result.published is False
    assert publisher.published == []
```

- [ ] **Step 2: Run the tests**

Run: `uv run pytest decision-engine/tests/test_managed_cycle_unit.py -v`
Expected: PASS (5 passed). (Pins behavior built in Task 1; immediate green is correct.)

Verify the wei figure: `$2000 / $3000 = 0.66666… WETH`; `× 1e18 = 666666666666666666.6…` → floored `666666666666666666`. ✓

- [ ] **Step 3: Run full decision-engine suite (no regressions)**

Run: `uv run pytest decision-engine/tests -q`
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add decision-engine/tests/test_managed_cycle_unit.py
git commit -m "feat(daedalus): P1.4 managed cycle — pin rebalance/gate/publish path"
```

---

## Self-Review (against design §3 decision loop + §4)

**1. Spec coverage:** read holdings → plan → resolve → build order → risk gate → publish, all present; hold path returns without publishing; rejected orders are not published. Uses the three P1 cores via their real interfaces. Out of scope (P1.5): real holdings, `__main__` swap, breaker live-state, reconciliation — flagged.

**2. Placeholder scan:** none — full code + exact commands + verified arithmetic.

**3. Type consistency:** `ManagedCycleConfig`, `ManagedCycleResult`, `HoldingsProvider.current_usd_holdings`, `ManagedPortfolioCycle(adapter, holdings, target, risk_gate, publisher, config)`, `run_one()` identical across tests and impl. `ExecutionOrder`/`OrderLimits`/`RiskContext`/`PortfolioSnapshot` fields match the verified envelope contracts. `strategy="REBAL:base"` with template/candidate None passes the envelope validator.

**4. Arithmetic verified:** $2000 WETH @ $3000 → 666666666666666666 wei (floored); cost ~$10.60 ⟹ threshold ~$42 ⟹ $2000 correction proceeds.

---

## Execution Handoff
Subagent-Driven (recommended) or Inline. Depends on P1.1, P1.2, P1.3 being merged (it imports all three). Leaves `__main__` untouched — P1.5 owns the live switchover.
