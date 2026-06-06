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


def test_exposure_checker_uses_value_usd_key_not_amount_usd():
    """W12 review #2 root cause: ExposureChecker previously wrote
    `amount_usd` while ExposureLimiter.check_order reads `value_usd`,
    raising KeyError and silently bypassing the gate via the cycle's
    outer except. Pin the exact key so a future rename trips here loudly.
    """
    from decision_engine.risk.exposure_limits import ExposureLimiter
    from decision_engine.risk_gate import ExposureChecker

    limiter = ExposureLimiter(total_capital=Decimal("10000"))
    checker = ExposureChecker(limiter)
    # Should NOT raise KeyError. With $100 order against $10k capital
    # the proportion is 1%, well under default 40% protocol limit.
    verdict = checker.check(_order(), _ctx())
    assert verdict.passed is True, (
        f"exposure gate must allow a 1% order; got reject: {verdict.reason}"
    )


def test_exposure_checker_uses_ctx_usd_notional_not_wei_amount():
    """Regression (testnet rebalance): `params.amount` is the token quantity in
    SMALLEST UNITS (wei), not USD. A 0.0365 WETH sell is 3.65e16 wei but only
    ~$57 of notional. The checker must value it via ctx.order_value_usd; using
    the wei amount as value_usd reads as $3.65e16 and trips the 40% protocol cap
    on every rebalance.
    """
    from decision_engine.risk.exposure_limits import ExposureLimiter
    from decision_engine.risk_gate import ExposureChecker

    limiter = ExposureLimiter(total_capital=Decimal("10000"))
    checker = ExposureChecker(limiter)

    order = ExecutionOrder(
        order_id=uuid.uuid4().hex,
        correlation_id="test-corr",
        timestamp=datetime.now(UTC),
        chain="base",
        protocol="aerodrome",
        action="swap",
        strategy="REBAL:base",
        params=OrderParams(
            token_in="0x4200000000000000000000000000000000000006",  # WETH
            token_out="0x036CbD53842c5426634e7929541eC2318f3dCF7e",  # USDC
            amount=Decimal("36527250765241030"),  # 0.0365 WETH in wei
        ),
        limits=OrderLimits(max_slippage_bps=50, deadline_unix=99999999999),
    )
    # True USD notional ~$57 → 0.57% of $10k capital → well under the 40% cap.
    ctx = RiskContext(
        portfolio=PortfolioSnapshot(
            nav_usd=Decimal("218"),
            positions={},
            cash_usd=Decimal("30"),
            drawdown_from_peak=Decimal("0"),
            last_rebalance=datetime.now(UTC),
        ),
        market=MarketSnapshot(
            timestamp=datetime.now(UTC), chain="base",
            prices={}, apys={}, pool_state={}, gas_gwei=Decimal("0"), metadata={},
        ),
        order_value_usd=Decimal("57"),
    )
    verdict = checker.check(order, ctx)
    assert verdict.passed is True, (
        f"exposure gate must value the order at $57, not 3.65e16 wei; "
        f"got reject: {verdict.reason}"
    )
