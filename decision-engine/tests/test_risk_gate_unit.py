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


# ─── W12 review regressions ────────────────────────────────────────────────


@dataclass
class _Raising:
    """Test double whose check() raises — proves the gate fails closed."""

    name: str

    def check(self, order, ctx):
        raise KeyError("simulated dict-key contract drift")


def test_gate_fails_closed_when_checker_raises():
    """W12 review #2 defense-in-depth: a checker exception must translate
    to a REJECT, never bubble up to a caller's catch-all where it would
    be swallowed and the order silently approved on the next attempt."""
    gate = RiskGate([_AlwaysPass("a"), _Raising("b"), _AlwaysPass("c")])
    verdict = gate.check(_order(), _ctx())
    assert verdict.passed is False
    assert verdict.checker == "b"
    assert "checker_exception" in verdict.reason
    assert "KeyError" in verdict.reason


def test_depeg_checker_rejects_when_breaker_tripped():
    """A tripped depeg breaker halts every rebalance; the reason mentions the
    depeg, the deviation in bps, and the threshold."""
    from decision_engine.risk.depeg_breaker import DepegBreaker
    from decision_engine.risk_gate import DepegChecker

    breaker = DepegBreaker(threshold_bps=100)
    breaker.update(Decimal("0.985"))  # 150 bps > 100 → tripped
    checker = DepegChecker(breaker)
    verdict = checker.check(_order(), _ctx())
    assert verdict.passed is False
    assert verdict.checker == "depeg_breaker"
    assert "depeg" in verdict.reason.lower()
    assert "USDC" in verdict.reason  # the depegging asset
    assert "0.985" in verdict.reason  # the actual off-peg price
    assert "150" in verdict.reason  # deviation bps
    assert "100" in verdict.reason  # threshold bps


def test_depeg_checker_passes_when_breaker_untripped():
    from decision_engine.risk.depeg_breaker import DepegBreaker
    from decision_engine.risk_gate import DepegChecker

    breaker = DepegBreaker(threshold_bps=100)
    breaker.update(Decimal("0.9996"))  # 4 bps, pegged
    checker = DepegChecker(breaker)
    verdict = checker.check(_order(), _ctx())
    assert verdict.passed is True
    assert verdict.checker == "depeg_breaker"


def test_depeg_checker_passes_before_any_update():
    """No feed configured → breaker never updated → never trips (backward compat)."""
    from decision_engine.risk.depeg_breaker import DepegBreaker
    from decision_engine.risk_gate import DepegChecker

    checker = DepegChecker(DepegBreaker())
    verdict = checker.check(_order(), _ctx())
    assert verdict.passed is True
