"""Pre-trade risk gate — thin wrapper over the existing risk modules.

Per blueprint §"Verification gate non-negotiable": every order published
to a chain executor MUST clear the deterministic pre-trade gate first.
This module is the gate. It composes the existing per-concern checkers
under `decision_engine.risk.*` (drawdown breaker, exposure limits, gas
spike breaker, oracle guard, position loss limit, TVL monitor, tx
failure monitor) behind a single, uniform interface:

    result = gate.check(order)
    if not result.passed:
        log_drop(reason=result.reason, checker=result.checker)
        return  # never publish

Design notes:

  * The wrapper does NOT re-implement risk logic. The existing modules
    are stateful classes with diverse APIs (different config shapes,
    different update cadences); the wrapper trusts the caller to keep
    them updated (market events, portfolio snapshots) and only asks
    them, at order-emission time, "should this order go out?".
  * Each module is wrapped in a `RiskChecker` adapter — a Protocol-like
    callable that maps `(ExecutionOrder, RiskContext) -> RiskDecision`.
    The cycle assembles a list of adapters at construction time; the
    gate iterates them in the supplied order and SHORT-CIRCUITS on the
    first fail (cheap → expensive ordering is the caller's choice).
  * Adapters live in this module rather than monkey-patching the risk
    modules themselves — per the task scope: "do NOT touch the risk
    modules themselves". Adding a new check is a new adapter class.

A `RiskContext` carries everything the adapters might need to consult
beyond the order itself: current portfolio NAV, current gas, the
inflight chain. Built once per cycle by the DecisionCycle and threaded
through every gate call so the adapters are pure(-ish) and deterministic
for that tick.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol, runtime_checkable

import structlog
from icarus.envelopes.orders import ExecutionOrder
from icarus.types import MarketSnapshot, PortfolioSnapshot

from decision_engine.risk.drawdown_breaker import DrawdownBreaker
from decision_engine.risk.exposure_limits import ExposureLimiter
from decision_engine.risk.gas_spike_breaker import GasSpikeBreaker
from decision_engine.risk.position_loss_limit import PositionLossLimit
from decision_engine.risk.tx_failure_monitor import TxFailureMonitor

logger = structlog.get_logger(service="decision-engine.risk_gate")


@dataclass(frozen=True)
class RiskContext:
    """Per-tick inputs shared by every adapter.

    Built by the cycle once it has fetched the snapshots, so adapters do
    not re-query anything. Decimal everywhere for amounts so we never
    round-trip through float.

    `order_value_usd` is the USD notional of the order under evaluation,
    supplied by the cycle that already priced it. The exposure checker
    needs it because `order.params.amount` is the token quantity in
    SMALLEST UNITS (wei/lamports), not dollars — there is no way to derive
    dollars from the order alone without re-pricing. None when the caller
    did not price the order (legacy/unpriced paths)."""

    portfolio: PortfolioSnapshot
    market: MarketSnapshot
    order_value_usd: Decimal | None = None


@dataclass(frozen=True)
class RiskDecision:
    """A single checker's verdict.

    `passed=False` means drop the order; `reason` is a human-readable
    string surfaced to the structured log and (eventually) the
    operator dashboard. `checker` is the adapter name so multi-checker
    audits ("which one rejected?") are trivial.
    """

    passed: bool
    checker: str
    reason: str = ""


@runtime_checkable
class RiskChecker(Protocol):
    """One pre-trade check. Pure function — never raises on a normal reject."""

    name: str

    def check(self, order: ExecutionOrder, ctx: RiskContext) -> RiskDecision: ...


# ---------------------------------------------------------------------------
# Concrete adapters — one per existing risk module.
#
# Each adapter is *thin*: it asks the underlying module the one question
# the gate cares about ("is this order allowed?") and translates the
# answer into a RiskDecision. Stateful modules (drawdown, gas spike,
# tx failure) expect their `.update()` methods to have been called
# elsewhere — typically by event consumers, NOT by the gate. The gate
# is read-only against the modules' state.
# ---------------------------------------------------------------------------


class DrawdownChecker:
    """Blocks orders if the drawdown breaker has halted trading.

    Mirrors the breaker's two thresholds:
      * `entries_paused`  — drop only `enter`-style actions
      * `trading_halted`  — drop everything (including exits) other than
                            explicit `CB:*` (circuit-breaker emissions)
    """

    name = "drawdown_breaker"
    # Actions that are "entries" — i.e. the kind blocked when the
    # breaker is in the *paused* (soft-halt) regime.
    _ENTRY_ACTIONS = frozenset(
        {
            "swap",
            "supply",
            "mint_lp",
            "stake",
            "deposit",
            "borrow",
            "open_perp",
        }
    )

    def __init__(self, breaker: DrawdownBreaker) -> None:
        self._breaker = breaker

    def check(self, order: ExecutionOrder, ctx: RiskContext) -> RiskDecision:
        is_cb_emission = order.strategy.startswith("CB:")
        if self._breaker.trading_halted and not is_cb_emission:
            return RiskDecision(
                passed=False,
                checker=self.name,
                reason=f"trading halted (drawdown={self._breaker.drawdown_pct})",
            )
        if (
            self._breaker.entries_paused
            and order.action in self._ENTRY_ACTIONS
            and not is_cb_emission
        ):
            return RiskDecision(
                passed=False,
                checker=self.name,
                reason=f"entries paused (drawdown={self._breaker.drawdown_pct})",
            )
        return RiskDecision(passed=True, checker=self.name)


class ExposureChecker:
    """Delegates to `ExposureLimiter.check_order`.

    The legacy limiter expects a `dict` order shape; we project the
    fields it actually reads from the v2 ExecutionOrder envelope. Any
    field the limiter wants and we don't have yet falls back to a
    conservative default that the limiter treats as "no info, evaluate
    against limits anyway".
    """

    name = "exposure_limits"

    def __init__(self, limiter: ExposureLimiter) -> None:
        self._limiter = limiter

    def check(self, order: ExecutionOrder, ctx: RiskContext) -> RiskDecision:
        # The limiter's check_order accepts a loose dict; translate from
        # the typed envelope. The legacy keys it reads (per
        # ExposureLimiter.check_order docstring) are
        # value_usd / protocol / asset.
        # USD notional. Prefer the value the cycle priced and threaded through
        # the context: `order.params.amount` is the token quantity in SMALLEST
        # UNITS (wei/lamports), NOT dollars — feeding it as `value_usd` overstates
        # exposure by ~1e18 and rejects every order against the protocol cap.
        # Fall back to the raw amount only for legacy/unpriced callers that did
        # not set order_value_usd.
        if ctx.order_value_usd is not None:
            value_usd = float(ctx.order_value_usd)
        else:
            value_usd = float(order.params.amount or Decimal("0"))
        legacy = {
            "strategy": order.strategy,
            "protocol": order.protocol,
            "chain": order.chain,
            "action": order.action,
            "asset": order.params.token_in or order.params.token_out or "",
            "value_usd": value_usd,
        }
        result = self._limiter.check_order(legacy)
        # ExposureCheckResult has .allowed/.reason fields; older variants
        # exposed `.passes`. Guard against both via getattr for safety.
        passed = getattr(result, "allowed", getattr(result, "passes", True))
        reason = getattr(result, "reason", "") or ""
        return RiskDecision(passed=bool(passed), checker=self.name, reason=reason)


class GasSpikeChecker:
    """Blocks non-urgent EVM orders while the gas-spike breaker is active."""

    name = "gas_spike_breaker"

    def __init__(self, breaker: GasSpikeBreaker) -> None:
        self._breaker = breaker

    def check(self, order: ExecutionOrder, ctx: RiskContext) -> RiskDecision:
        # Gas spike breaker is EVM-only; Solana orders bypass.
        if order.chain != "base":
            return RiskDecision(passed=True, checker=self.name)
        if not self._breaker.is_active:
            return RiskDecision(passed=True, checker=self.name)
        if order.priority == "urgent":
            # Urgent (stop-loss / forced unwind) bypasses throttles per
            # the OrderPriority docstring in envelopes/orders.py.
            return RiskDecision(passed=True, checker=self.name)
        if self._breaker.is_operation_allowed(order.action):
            return RiskDecision(passed=True, checker=self.name)
        return RiskDecision(
            passed=False,
            checker=self.name,
            reason=f"gas spike active (current={self._breaker.current_gas} gwei)",
        )


class PositionLossChecker:
    """Blocks new `enter`-style orders for strategies in cooldown."""

    name = "position_loss_limit"
    _ENTRY_ACTIONS = DrawdownChecker._ENTRY_ACTIONS

    def __init__(self, limiter: PositionLossLimit) -> None:
        self._limiter = limiter

    def check(self, order: ExecutionOrder, ctx: RiskContext) -> RiskDecision:
        if order.action not in self._ENTRY_ACTIONS:
            return RiskDecision(passed=True, checker=self.name)
        # `can_open_position` returns (bool, reason) per v4.2 contract.
        result = self._limiter.can_open_position(order.strategy)
        if isinstance(result, tuple):
            allowed, reason = result
        else:
            allowed, reason = bool(result), ""
        if allowed:
            return RiskDecision(passed=True, checker=self.name)
        return RiskDecision(passed=False, checker=self.name, reason=reason or "in cooldown")


class TxFailureChecker:
    """Blocks new orders while the tx-failure monitor has paused execution."""

    name = "tx_failure_monitor"

    def __init__(self, monitor: TxFailureMonitor) -> None:
        self._monitor = monitor

    def check(self, order: ExecutionOrder, ctx: RiskContext) -> RiskDecision:
        if self._monitor.can_execute():
            return RiskDecision(passed=True, checker=self.name)
        return RiskDecision(
            passed=False,
            checker=self.name,
            reason="tx failure monitor paused executions",
        )


# ---------------------------------------------------------------------------
# Composite gate
# ---------------------------------------------------------------------------


class RiskGate:
    """Composes a sequence of `RiskChecker`s; short-circuits on first fail.

    Construction is explicit so tests can pass any subset of checkers,
    and so production wiring is the single source of truth for check
    order (cheap → expensive). `__main__` builds the production
    instance from the existing risk modules.
    """

    def __init__(self, checkers: list[RiskChecker]) -> None:
        self._checkers = list(checkers)

    @property
    def checkers(self) -> tuple[RiskChecker, ...]:
        return tuple(self._checkers)

    def check(self, order: ExecutionOrder, ctx: RiskContext) -> RiskDecision:
        """Run each checker in order; return the first failure or the final pass.

        On failure, emits a structured log line so dropped orders are
        always traceable to the rejecting checker without re-running
        anything.

        Fails closed on checker exceptions. A capital-protection gate
        must not silently let an order through because a checker raised —
        the cycle's outer ``except Exception`` would swallow the trace
        and the order would never be published, but a future refactor
        that moves the call out from under that catch-all would silently
        pass orders. Translate exceptions to an explicit reject here.
        """
        for checker in self._checkers:
            try:
                decision = checker.check(order, ctx)
            except Exception as exc:
                logger.error(
                    "risk_gate_checker_exception",
                    order_id=order.order_id,
                    correlation_id=order.correlation_id,
                    chain=order.chain,
                    strategy=order.strategy,
                    checker=getattr(checker, "name", checker.__class__.__name__),
                    error_class=type(exc).__name__,
                    error=str(exc),
                    exc_info=True,
                )
                return RiskDecision(
                    passed=False,
                    checker=getattr(checker, "name", checker.__class__.__name__),
                    reason=f"checker_exception:{type(exc).__name__}:{exc}",
                )
            if not decision.passed:
                logger.warning(
                    "risk_gate_reject",
                    order_id=order.order_id,
                    correlation_id=order.correlation_id,
                    chain=order.chain,
                    strategy=order.strategy,
                    checker=decision.checker,
                    reason=decision.reason,
                )
                return decision
        return RiskDecision(passed=True, checker="composite", reason="")


__all__ = [
    "DrawdownChecker",
    "ExposureChecker",
    "GasSpikeChecker",
    "PositionLossChecker",
    "RiskChecker",
    "RiskContext",
    "RiskDecision",
    "RiskGate",
    "TxFailureChecker",
]
