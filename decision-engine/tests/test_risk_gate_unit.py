"""RiskGate composite tests — pass-through and first-fail short-circuit.

The individual risk modules have their own dedicated test suites under
decision-engine/tests/risk/. These tests pin the wrapper's behaviour:
the composite calls each checker in order and short-circuits on the
first failure.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from decision_engine.risk_gate import RiskContext, RiskDecision, RiskGate
from icarus.envelopes.orders import ExecutionOrder, OrderLimits, OrderParams
from icarus.types import MarketSnapshot, PortfolioSnapshot


def _order() -> ExecutionOrder:
    return ExecutionOrder(
        order_id=uuid.uuid4().hex,
        correlation_id="test-corr",
        timestamp=datetime.now(UTC),
        chain="base",
        protocol="aave_v3",
        action="supply",
        strategy="LEND-001:CAND-001",
        template_id="LEND-001",
        candidate_id="CAND-001",
        params=OrderParams(amount=Decimal("100")),
        limits=OrderLimits(max_slippage_bps=50, deadline_unix=99999999999),
    )


def _ctx() -> RiskContext:
    return RiskContext(
        portfolio=PortfolioSnapshot(
            nav_usd=Decimal("100000"),
            positions={},
            cash_usd=Decimal("100000"),
            drawdown_from_peak=Decimal("0"),
            last_rebalance=datetime.now(UTC),
        ),
        market=MarketSnapshot(
            timestamp=datetime.now(UTC),
            chain="base",
            prices={},
            apys={},
            pool_state={},
            gas_gwei=Decimal("0.5"),
            metadata={},
        ),
    )


@dataclass
class _AlwaysPass:
    name: str

    def check(self, order, ctx):
        return RiskDecision(passed=True, checker=self.name)


@dataclass
class _AlwaysFail:
    name: str
    reason: str = "test reject"

    def check(self, order, ctx):
        return RiskDecision(passed=False, checker=self.name, reason=self.reason)


@dataclass
class _Tracking:
    name: str
    calls: list[str]

    def check(self, order, ctx):
        self.calls.append(self.name)
        return RiskDecision(passed=True, checker=self.name)


def test_gate_passes_when_all_checkers_pass():
    gate = RiskGate([_AlwaysPass("a"), _AlwaysPass("b"), _AlwaysPass("c")])
    verdict = gate.check(_order(), _ctx())
    assert verdict.passed is True
    assert verdict.checker == "composite"


def test_gate_first_fail_short_circuits():
    calls: list[str] = []
    gate = RiskGate(
        [
            _Tracking("first", calls),
            _AlwaysFail("second", reason="boom"),
            _Tracking("third", calls),  # should never run
        ]
    )
    verdict = gate.check(_order(), _ctx())
    assert verdict.passed is False
    assert verdict.checker == "second"
    assert verdict.reason == "boom"
    # third must not have been invoked due to short-circuit
    assert calls == ["first"]


def test_gate_runs_checkers_in_provided_order():
    calls: list[str] = []
    gate = RiskGate(
        [
            _Tracking("alpha", calls),
            _Tracking("beta", calls),
            _Tracking("gamma", calls),
        ]
    )
    gate.check(_order(), _ctx())
    assert calls == ["alpha", "beta", "gamma"]


def test_gate_empty_checker_list_is_pass():
    gate = RiskGate([])
    verdict = gate.check(_order(), _ctx())
    assert verdict.passed is True
    assert verdict.checker == "composite"


def test_gate_exposes_checker_tuple():
    a, b = _AlwaysPass("a"), _AlwaysPass("b")
    gate = RiskGate([a, b])
    assert gate.checkers == (a, b)
