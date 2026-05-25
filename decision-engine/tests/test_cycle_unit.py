"""DecisionCycle unit tests — end-to-end one-tick walks with stubs.

These tests exercise the orchestrator under controlled inputs: stub
DataAdapter, stub TemplateRegistry, stub Allocator, stub RegimeClassifier,
stub RiskGate, in-memory ExecutorPublisher. They are *not* mocking the
risk modules (which W2-convention forbids); the risk gate itself is
stubbed because the cycle's contract with the gate is just "call check
and respect the verdict". The real risk modules have their own
dedicated test suites under decision-engine/tests/risk/.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from decision_engine.cycle import DecisionCycle
from decision_engine.risk_gate import RiskContext, RiskDecision
from decision_engine.roster_listener import RosterCache, RosterEntry
from icarus.envelopes.orders import ExecutionOrder
from icarus.protocols.allocator import AllocationDecision
from icarus.protocols.regime import Regime
from icarus.types import Decision, MarketSnapshot, PortfolioSnapshot
from icarus.types.market import Chain

# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


def _market(chain: Chain) -> MarketSnapshot:
    return MarketSnapshot(
        timestamp=datetime.now(UTC),
        chain=chain,
        prices={"USDC": Decimal("1"), "ETH": Decimal("3500")},
        apys={"aave_v3.usdc.base": Decimal("0.06")},
        pool_state={},
        gas_gwei=Decimal("0.5"),
        metadata={},
    )


def _portfolio() -> PortfolioSnapshot:
    return PortfolioSnapshot(
        nav_usd=Decimal("100000"),
        positions={},
        cash_usd=Decimal("100000"),
        drawdown_from_peak=Decimal("0"),
        last_rebalance=datetime.now(UTC),
    )


def _regime() -> Regime:
    return Regime(
        volatility="low",
        funding="neutral",
        trend="trending_up",
        tvl="stable",
        confidence=Decimal("0.8"),
        features={},
        source="stub",
        rationale="",
    )


@dataclass
class StubAdapter:
    name: str = "stub-adapter"
    historical_supported: bool = False
    snapshots: dict[Chain, MarketSnapshot] = field(default_factory=dict)
    fail_chains: set[Chain] = field(default_factory=set)

    async def fetch_live(self, chain: Chain) -> MarketSnapshot:
        if chain in self.fail_chains:
            raise RuntimeError(f"stub failure for {chain}")
        return self.snapshots.get(chain) or _market(chain)

    def fetch_historical(self, chain, start, end):  # pragma: no cover
        raise NotImplementedError


@dataclass
class StubManifest:
    id: str
    chain: Chain
    protocol: str = "aave_v3"


@dataclass
class StubTemplate:
    manifest: StubManifest
    fn: Any  # Callable[[dict, MarketSnapshot, PortfolioSnapshot], Decision]

    def evaluate(self, params, market, portfolio):
        return self.fn(params, market, portfolio)


@dataclass
class StubRegistry:
    templates: dict[str, StubTemplate]

    def by_id(self, template_id: str) -> StubTemplate:
        return self.templates[template_id]


class StubAllocator:
    name = "stub-equal-weight"

    def allocate(
        self,
        candidate_decisions: Mapping[str, Decision],
        portfolio: PortfolioSnapshot,
        regime: Regime,
    ) -> AllocationDecision:
        actionable = {
            cid: d
            for cid, d in candidate_decisions.items()
            if d.action in ("enter", "rebalance", "exit") and d.target_size > 0
        }
        if not actionable:
            return AllocationDecision(
                target_usd_by_candidate={},
                mode="cold_start",
                template_caps_applied={},
                commentary="",
            )
        per = Decimal("1000")
        return AllocationDecision(
            target_usd_by_candidate={cid: per for cid in actionable},
            mode="cold_start",
            template_caps_applied={},
            commentary="",
        )


class StubRegimeClassifier:
    name = "stub-regime"

    def classify(self, market: MarketSnapshot) -> Regime:
        return _regime()


class StubRiskGateAllPass:
    def check(self, order: ExecutionOrder, ctx: RiskContext) -> RiskDecision:
        return RiskDecision(passed=True, checker="stub-pass")


class StubRiskGateAllFail:
    def check(self, order: ExecutionOrder, ctx: RiskContext) -> RiskDecision:
        return RiskDecision(passed=False, checker="stub-fail", reason="test reject")


class StubPublisher:
    """Captures published orders in-memory; satisfies `ExecutorPublisher`."""

    def __init__(self) -> None:
        self.published: dict[Chain, list[ExecutionOrder]] = {}

    async def publish_order(self, chain: Chain, order: ExecutionOrder) -> None:
        self.published.setdefault(chain, []).append(order)


class StubDB:
    """Minimal DatabaseManager surface: get_session() returning a fake session."""

    def get_session(self):
        return _NoOpSession()

    def close(self) -> None:  # pragma: no cover
        pass


class _NoOpSession:
    """Returns an empty position scalar result on every query."""

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, _stmt):
        return _EmptyResult()


class _EmptyResult:
    def scalars(self):
        return self

    def all(self):
        return []


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def _entry(candidate_id: str, template_id: str) -> RosterEntry:
    return RosterEntry(
        candidate_id=candidate_id,
        template_id=template_id,
        state="live_capped",
        allocation_usd=Decimal("0"),
        allocation_max_pct=Decimal("0.1"),
        breaker_tripped=False,
    )


def _enter_decision() -> Decision:
    return Decision(
        action="enter",
        target_size=Decimal("1000"),
        confidence=Decimal("0.9"),
        reasoning="test",
    )


async def test_cycle_emits_orders_when_risk_gate_passes() -> None:
    cache = RosterCache()
    cache.replace([_entry("CAND-001", "LEND-001")])

    registry = StubRegistry(
        templates={
            "LEND-001": StubTemplate(
                manifest=StubManifest(id="LEND-001", chain="base"),
                fn=lambda p, m, pf: _enter_decision(),
            )
        }
    )
    publisher = StubPublisher()
    cycle = DecisionCycle(
        adapter=StubAdapter(),
        registry=registry,
        allocator=StubAllocator(),
        regime_classifier=StubRegimeClassifier(),
        risk_gate=StubRiskGateAllPass(),
        executor_publisher=publisher,
        db=StubDB(),
        roster_cache=cache,
    )

    result = await cycle.run_one()

    assert result.candidates_evaluated == 1
    assert result.orders_emitted == 1
    assert result.orders_dropped == 0
    assert len(publisher.published.get("base", [])) == 1
    order = publisher.published["base"][0]
    assert order.template_id == "LEND-001"
    assert order.candidate_id == "CAND-001"
    assert order.strategy == "LEND-001:CAND-001"
    assert order.chain == "base"


async def test_cycle_drops_all_orders_when_risk_gate_rejects() -> None:
    cache = RosterCache()
    cache.replace([_entry("CAND-001", "LEND-001"), _entry("CAND-002", "LEND-001")])

    registry = StubRegistry(
        templates={
            "LEND-001": StubTemplate(
                manifest=StubManifest(id="LEND-001", chain="base"),
                fn=lambda p, m, pf: _enter_decision(),
            )
        }
    )
    publisher = StubPublisher()
    cycle = DecisionCycle(
        adapter=StubAdapter(),
        registry=registry,
        allocator=StubAllocator(),
        regime_classifier=StubRegimeClassifier(),
        risk_gate=StubRiskGateAllFail(),
        executor_publisher=publisher,
        db=StubDB(),
        roster_cache=cache,
    )

    result = await cycle.run_one()

    assert result.candidates_evaluated == 2
    assert result.orders_emitted == 0
    assert result.orders_dropped == 2
    assert result.drops_by_checker == {"stub-fail": 2}
    assert publisher.published == {}


async def test_cycle_routes_multi_chain_candidates_to_correct_channels() -> None:
    cache = RosterCache()
    cache.replace(
        [
            _entry("CAND-BASE", "LEND-BASE"),
            _entry("CAND-SOL", "LEND-SOL"),
        ]
    )

    registry = StubRegistry(
        templates={
            "LEND-BASE": StubTemplate(
                manifest=StubManifest(id="LEND-BASE", chain="base"),
                fn=lambda p, m, pf: _enter_decision(),
            ),
            "LEND-SOL": StubTemplate(
                manifest=StubManifest(id="LEND-SOL", chain="solana"),
                fn=lambda p, m, pf: _enter_decision(),
            ),
        }
    )
    publisher = StubPublisher()
    cycle = DecisionCycle(
        adapter=StubAdapter(),
        registry=registry,
        allocator=StubAllocator(),
        regime_classifier=StubRegimeClassifier(),
        risk_gate=StubRiskGateAllPass(),
        executor_publisher=publisher,
        db=StubDB(),
        roster_cache=cache,
    )

    result = await cycle.run_one()

    assert result.orders_emitted == 2
    assert len(publisher.published["base"]) == 1
    assert len(publisher.published["solana"]) == 1
    assert publisher.published["base"][0].chain == "base"
    assert publisher.published["solana"][0].chain == "solana"


async def test_cycle_skips_hold_decisions() -> None:
    cache = RosterCache()
    cache.replace([_entry("CAND-001", "LEND-001")])

    def hold(p, m, pf):
        return Decision(
            action="hold",
            target_size=Decimal("0"),
            confidence=Decimal("0.5"),
            reasoning="no signal",
        )

    registry = StubRegistry(
        templates={
            "LEND-001": StubTemplate(
                manifest=StubManifest(id="LEND-001", chain="base"),
                fn=hold,
            )
        }
    )
    publisher = StubPublisher()
    cycle = DecisionCycle(
        adapter=StubAdapter(),
        registry=registry,
        allocator=StubAllocator(),
        regime_classifier=StubRegimeClassifier(),
        risk_gate=StubRiskGateAllPass(),
        executor_publisher=publisher,
        db=StubDB(),
        roster_cache=cache,
    )

    result = await cycle.run_one()

    # The candidate IS evaluated (Decision recorded), but the allocator
    # zero-sizes hold decisions, so nothing gets published.
    assert result.candidates_evaluated == 1
    assert result.orders_emitted == 0
    assert result.decisions_by_action == {"hold": 1}


async def test_cycle_skips_breaker_tripped_candidates() -> None:
    cache = RosterCache()
    cache.replace(
        [
            RosterEntry(
                candidate_id="CAND-001",
                template_id="LEND-001",
                state="live_capped",
                allocation_usd=Decimal("0"),
                allocation_max_pct=Decimal("0.1"),
                breaker_tripped=True,  # breaker tripped → cycle ignores
            ),
        ]
    )

    registry = StubRegistry(
        templates={
            "LEND-001": StubTemplate(
                manifest=StubManifest(id="LEND-001", chain="base"),
                fn=lambda p, m, pf: _enter_decision(),
            )
        }
    )
    publisher = StubPublisher()
    cycle = DecisionCycle(
        adapter=StubAdapter(),
        registry=registry,
        allocator=StubAllocator(),
        regime_classifier=StubRegimeClassifier(),
        risk_gate=StubRiskGateAllPass(),
        executor_publisher=publisher,
        db=StubDB(),
        roster_cache=cache,
    )

    result = await cycle.run_one()
    assert result.candidates_evaluated == 0
    assert result.orders_emitted == 0


async def test_cycle_raises_on_stub_regime_classifier() -> None:
    class StubMissing:
        name = "missing"

        def classify(self, market):
            raise NotImplementedError("stream A not wired")

    cache = RosterCache()
    cache.replace([_entry("CAND-001", "LEND-001")])

    cycle = DecisionCycle(
        adapter=StubAdapter(),
        registry=StubRegistry(
            templates={
                "LEND-001": StubTemplate(
                    manifest=StubManifest(id="LEND-001", chain="base"),
                    fn=lambda p, m, pf: _enter_decision(),
                )
            }
        ),
        allocator=StubAllocator(),
        regime_classifier=StubMissing(),
        risk_gate=StubRiskGateAllPass(),
        executor_publisher=StubPublisher(),
        db=StubDB(),
        roster_cache=cache,
    )

    with pytest.raises(NotImplementedError, match="Stream A"):
        await cycle.run_one()
