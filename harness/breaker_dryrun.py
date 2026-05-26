"""Circuit-breaker dry-run exercise (W11) — fire each of the 6 capital-
protecting risk breakers end-to-end with synthetic state, assert the
trigger semantics, and (where applicable) validate emitted ExecutionOrder
envelopes against the pydantic contract on both Base and Solana.

The 6 breakers exercised (from ``decision_engine.risk``):

  1. DrawdownBreaker        — emits CB:drawdown unwind orders
  2. PositionLossLimit      — emits CB:position_loss close orders
  3. TVLMonitor             — emits CB:tvl_drop withdrawal orders
  4. GasSpikeBreaker        — gate (pauses non-urgent ops); no order emission
  5. OracleGuard            — gate (rejects unsafe price reads); no order emission
  6. TxFailureMonitor       — gate (pauses execution after threshold); no order emission

For the 3 envelope-emitting breakers, we run two cases per breaker:

  a) Base path  — invoke the breaker directly with synthetic positions;
                  the breaker emits chain="base" orders. We validate each
                  emitted dict against ``ExecutionOrder`` pydantic and
                  assert ``chain == "base"`` + no ``solana_specific``.

  b) Solana path — construct the matching ExecutionOrder envelope directly
                  with chain="solana" + ``SolanaSpecificOrder``. This proves
                  the envelope contract is valid on both chains. (The
                  breaker modules themselves hardcode chain="base" per the
                  W3 TODO — per-position chain awareness lands W12+. The
                  Solana case here exercises the *envelope*, not the
                  breaker's choice of chain.)

For the 3 gate-style breakers we assert:
  - The gate fires exactly when expected given the synthetic input.
  - The state-snapshot (``get_state()``/``OracleCheckResult``) reflects
    the trigger.

Exit codes:
  0 = every breaker check PASSED
  1 = at least one breaker check FAILED
"""

from __future__ import annotations

import os
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock

import structlog

# Ensure src layout packages are importable when invoked under `uv run`.
# `uv run python harness/breaker_dryrun.py` sets PYTHONPATH via the
# workspace, but we belt-and-brace by appending the candidate src dirs
# here so the script also runs from `python harness/breaker_dryrun.py`.
_HARNESS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_HARNESS_DIR)
for _rel in ("lib/src", "decision-engine/src"):
    _p = os.path.join(_REPO_ROOT, _rel)
    if _p not in sys.path and os.path.isdir(_p):
        sys.path.insert(0, _p)

from decision_engine.risk.drawdown_breaker import DrawdownBreaker  # noqa: E402
from decision_engine.risk.gas_spike_breaker import GasSpikeBreaker  # noqa: E402
from decision_engine.risk.oracle_guard import OracleGuard  # noqa: E402
from decision_engine.risk.position_loss_limit import PositionLossLimit  # noqa: E402
from decision_engine.risk.tvl_monitor import TVLMonitor  # noqa: E402
from decision_engine.risk.tx_failure_monitor import TxFailureMonitor  # noqa: E402
from icarus.data.price_feed import PriceFeedManager, PriceResult  # noqa: E402
from icarus.envelopes import (  # noqa: E402
    ExecutionOrder,
    OrderLimits,
    OrderParams,
    SolanaSpecificOrder,
)

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.add_log_level,
        structlog.processors.JSONRenderer(),
    ],
)
_log = structlog.get_logger("breaker-dryrun")


# ──────────────────────────────────────────────────────────────────────────────
# Result aggregation
# ──────────────────────────────────────────────────────────────────────────────


@dataclass
class CheckResult:
    """Outcome of one breaker dry-run check."""

    name: str
    passed: bool
    detail: str = ""


def _ok(name: str, detail: str = "") -> CheckResult:
    _log.info("breaker_check_pass", check=name, detail=detail)
    return CheckResult(name=name, passed=True, detail=detail)


def _fail(name: str, detail: str) -> CheckResult:
    _log.error("breaker_check_fail", check=name, detail=detail)
    return CheckResult(name=name, passed=False, detail=detail)


# ──────────────────────────────────────────────────────────────────────────────
# Envelope helpers
# ──────────────────────────────────────────────────────────────────────────────


def _validate_envelope(order_dict: dict[str, Any], expected_chain: str) -> str:
    """Round-trip an order dict through ``ExecutionOrder`` pydantic.

    Returns an empty string on success, else a human-readable error.
    """
    try:
        order = ExecutionOrder.model_validate(order_dict)
    except Exception as exc:
        return f"pydantic validation failed: {exc}"
    if order.chain != expected_chain:
        return f"expected chain={expected_chain!r}, got {order.chain!r}"
    if expected_chain == "solana" and order.solana_specific is None:
        return "chain='solana' but solana_specific is None"
    if expected_chain == "base" and order.solana_specific is not None:
        return "chain='base' but solana_specific is set"
    return ""


def _build_solana_unwind_order(
    *,
    strategy: str,
    asset: str,
    amount: Decimal,
    protocol: str,
    correlation_id: str,
) -> dict[str, Any]:
    """Construct the Solana-flavoured equivalent of a CB unwind order.

    The 3 v2-envelope breakers currently hardcode chain="base" (per the
    W3 TODO comment — per-position chain awareness lands W12+). This
    helper constructs the envelope a chain-aware breaker would emit
    on Solana so we can validate the envelope contract end-to-end on
    both chains *today*, ahead of the breaker-side wiring.
    """
    return ExecutionOrder(
        order_id=uuid.uuid4().hex,
        correlation_id=correlation_id,
        timestamp=datetime.now(UTC),
        chain="solana",
        protocol=protocol,
        action="withdraw",
        strategy=strategy,
        template_id=None,
        candidate_id=None,
        priority="urgent",
        params=OrderParams(token_in=asset, amount=amount),
        limits=OrderLimits(
            max_priority_fee_lamports=Decimal("5000"),
            max_slippage_bps=50,
            deadline_unix=int(time.time()) + 300,
        ),
        solana_specific=SolanaSpecificOrder(
            compute_unit_price=1000,
            compute_unit_limit=200_000,
            lookup_tables=[],
        ),
    ).model_dump(mode="json")


# ──────────────────────────────────────────────────────────────────────────────
# Breaker 1 — DrawdownBreaker (RISK-001)
# ──────────────────────────────────────────────────────────────────────────────


def run_drawdown_breaker() -> list[CheckResult]:
    """Push portfolio to 22% drawdown → critical → unwind orders."""
    results: list[CheckResult] = []
    name_base = "drawdown_breaker[base]"
    name_solana = "drawdown_breaker[solana]"

    breaker = DrawdownBreaker(initial_value=Decimal("100000"))
    # Step 1: nudge peak upward then crash. 100k → 110k → 85.8k = 22% DD.
    breaker.update(Decimal("110000"))
    state = breaker.update(Decimal("85800"))

    if not breaker.trading_halted:
        results.append(_fail(name_base, f"breaker did not halt at 22% DD: {state}"))
        return results

    positions = [
        {"asset": "USDC", "protocol": "aave_v3", "value": "50000"},
        {"asset": "WETH", "protocol": "aave_v3", "value": "30000"},
    ]
    orders = breaker.get_unwind_orders(positions, correlation_id="dryrun-dd-001")
    if len(orders) != len(positions):
        results.append(
            _fail(name_base, f"expected {len(positions)} orders, got {len(orders)}"),
        )
        return results

    for i, o in enumerate(orders):
        err = _validate_envelope(o, expected_chain="base")
        if err:
            results.append(_fail(name_base, f"order[{i}]: {err}"))
            return results
        if o.get("strategy") != "CB:drawdown":
            results.append(
                _fail(name_base, f"order[{i}].strategy != CB:drawdown ({o.get('strategy')!r})"),
            )
            return results
    results.append(_ok(name_base, f"halted at 22% DD; {len(orders)} CB:drawdown orders on base"))

    # Solana path — construct + validate the equivalent envelope.
    sol_orders = [
        _build_solana_unwind_order(
            strategy="CB:drawdown",
            asset=p["asset"],
            amount=Decimal(p["value"]),
            protocol="kamino",
            correlation_id="dryrun-dd-sol-001",
        )
        for p in positions
    ]
    for i, o in enumerate(sol_orders):
        err = _validate_envelope(o, expected_chain="solana")
        if err:
            results.append(_fail(name_solana, f"order[{i}]: {err}"))
            return results
    results.append(_ok(name_solana, f"{len(sol_orders)} CB:drawdown envelopes valid on solana"))
    return results


# ──────────────────────────────────────────────────────────────────────────────
# Breaker 2 — PositionLossLimit (RISK-002)
# ──────────────────────────────────────────────────────────────────────────────


def run_position_loss_limit() -> list[CheckResult]:
    """One position at -12% (above the 10% limit) → CB:position_loss order."""
    results: list[CheckResult] = []
    name_base = "position_loss_limit[base]"
    name_solana = "position_loss_limit[solana]"

    limit = PositionLossLimit()
    positions = [
        {
            "id": "pos-001",
            "asset": "WETH",
            "protocol": "aave_v3",
            "strategy_id": "LEND-001",
            "entry_price": "3000",
            "entry_time": datetime.now(UTC).isoformat(),
            "current_value": "26400",  # 12% loss on 30k
        },
        {
            "id": "pos-002",
            "asset": "USDC",
            "protocol": "aave_v3",
            "strategy_id": "LEND-001-stable",
            "entry_price": "1.00",
            "entry_time": datetime.now(UTC).isoformat(),
            "current_value": "10000",  # 1% loss — below limit
        },
    ]
    price_map = {"WETH": Decimal("2640"), "USDC": Decimal("0.99")}

    orders = limit.generate_close_orders(
        positions=positions, price_map=price_map, correlation_id="dryrun-pl-001",
    )
    if len(orders) != 1:
        results.append(
            _fail(name_base, f"expected 1 order (only WETH at -12% breaches), got {len(orders)}"),
        )
        return results
    err = _validate_envelope(orders[0], expected_chain="base")
    if err:
        results.append(_fail(name_base, err))
        return results
    if orders[0].get("strategy") != "CB:position_loss":
        results.append(
            _fail(name_base, f"strategy != CB:position_loss ({orders[0].get('strategy')!r})"),
        )
        return results
    if not limit.is_strategy_in_cooldown("LEND-001"):
        results.append(_fail(name_base, "strategy LEND-001 should be in cooldown post-trigger"))
        return results
    results.append(
        _ok(
            name_base,
            "WETH closed (-12%), LEND-001 cooldown active, CB:position_loss valid on base",
        ),
    )

    sol_order = _build_solana_unwind_order(
        strategy="CB:position_loss",
        asset="SOL",
        amount=Decimal("100"),
        protocol="drift",
        correlation_id="dryrun-pl-sol-001",
    )
    err = _validate_envelope(sol_order, expected_chain="solana")
    if err:
        results.append(_fail(name_solana, err))
        return results
    results.append(_ok(name_solana, "CB:position_loss envelope valid on solana"))
    return results


# ──────────────────────────────────────────────────────────────────────────────
# Breaker 3 — TVLMonitor (RISK-005)
# ──────────────────────────────────────────────────────────────────────────────


def run_tvl_monitor() -> list[CheckResult]:
    """Pool TVL crashes 70% (peak 100M → current 30M) → critical → withdraw."""
    results: list[CheckResult] = []
    name_base = "tvl_monitor[base]"
    name_solana = "tvl_monitor[solana]"

    monitor = TVLMonitor()
    monitor.record_tvl("aerodrome", "base", Decimal("100000000"), "defillama")
    monitor.record_tvl("aerodrome", "base", Decimal("30000000"), "defillama")  # -70%

    if not monitor.should_withdraw("aerodrome", "base"):
        results.append(_fail(name_base, "monitor should signal withdraw at 70% drop"))
        return results

    positions = [
        {
            "protocol": "aerodrome",
            "asset": "USDC",
            "current_value": "25000",
        },
        {
            "protocol": "aerodrome",
            "asset": "WETH",
            "current_value": "20000",
        },
        {
            # Should NOT be unwound — unaffected protocol.
            "protocol": "aave_v3",
            "asset": "USDC",
            "current_value": "10000",
        },
    ]
    orders = monitor.generate_withdrawal_orders(positions, correlation_id="dryrun-tvl-001")
    if len(orders) != 2:
        results.append(
            _fail(name_base, f"expected 2 orders (only aerodrome positions), got {len(orders)}"),
        )
        return results
    for i, o in enumerate(orders):
        err = _validate_envelope(o, expected_chain="base")
        if err:
            results.append(_fail(name_base, f"order[{i}]: {err}"))
            return results
        if o.get("strategy") != "CB:tvl_drop":
            results.append(
                _fail(name_base, f"order[{i}].strategy != CB:tvl_drop ({o.get('strategy')!r})"),
            )
            return results
        if o.get("protocol") != "aerodrome":
            results.append(
                _fail(name_base, f"order[{i}].protocol != aerodrome ({o.get('protocol')!r})"),
            )
            return results
    results.append(
        _ok(
            name_base,
            "aerodrome -70% TVL → 2 CB:tvl_drop orders on base, aave_v3 unaffected",
        ),
    )

    sol_order = _build_solana_unwind_order(
        strategy="CB:tvl_drop",
        asset="USDC",
        amount=Decimal("25000"),
        protocol="kamino",
        correlation_id="dryrun-tvl-sol-001",
    )
    err = _validate_envelope(sol_order, expected_chain="solana")
    if err:
        results.append(_fail(name_solana, err))
        return results
    results.append(_ok(name_solana, "CB:tvl_drop envelope valid on solana"))
    return results


# ──────────────────────────────────────────────────────────────────────────────
# Breaker 4 — GasSpikeBreaker (RISK-003) — gate-only
# ──────────────────────────────────────────────────────────────────────────────


def run_gas_spike_breaker() -> list[CheckResult]:
    """Gas spikes from 30 gwei to 500 gwei (~16x average) → active."""
    results: list[CheckResult] = []
    name = "gas_spike_breaker"

    breaker = GasSpikeBreaker()
    # Avg gas ~30 gwei (30e9 wei); spike to 500 gwei (500e9 wei) = ~16x.
    state = breaker.update(current_gas=Decimal("500000000000"), average_gas=Decimal("30000000000"))

    if not breaker.is_active:
        results.append(_fail(name, f"breaker did not activate at 500 gwei vs 30 gwei: {state}"))
        return results
    if breaker.is_operation_allowed("rebalance"):
        results.append(_fail(name, "non-urgent op 'rebalance' should be blocked when active"))
        return results
    if not breaker.is_operation_allowed("stop_loss"):
        results.append(_fail(name, "urgent op 'stop_loss' must remain allowed when active"))
        return results
    results.append(_ok(name, "active at 500 gwei (16x avg); non-urgent blocked, urgent allowed"))
    return results


# ──────────────────────────────────────────────────────────────────────────────
# Breaker 5 — OracleGuard (RISK-007) — gate-only
# ──────────────────────────────────────────────────────────────────────────────


def run_oracle_guard() -> list[CheckResult]:
    """USDC price disagreement (1.00 vs 1.05 ≈ 4.9%) breaches 2% threshold."""
    results: list[CheckResult] = []
    name = "oracle_guard"

    mock_redis = MagicMock()
    pf = PriceFeedManager(
        redis=mock_redis,
        deviation_threshold=0.02,
        fetch_fn=MagicMock(),
    )
    pf.is_any_stale = MagicMock(return_value=False)
    guard = OracleGuard(pf, deviation_threshold=0.02)

    alchemy = {"USDC": PriceResult("USDC", 1.00, "alchemy", "2026-05-25T00:00:00Z")}
    defillama = {"USDC": PriceResult("USDC", 1.05, "defillama", "2026-05-25T00:00:00Z")}
    result = guard.validate_prices(alchemy, defillama)

    if result.safe:
        results.append(_fail(name, f"guard should reject 4.9% deviation: {result.reason}"))
        return results
    if not any(d.exceeded for d in result.deviations):
        results.append(_fail(name, "no deviation marked exceeded"))
        return results
    if "USDC" not in result.reason:
        results.append(_fail(name, f"reason should mention USDC: {result.reason!r}"))
        return results
    results.append(_ok(name, f"rejected: {result.reason}"))
    return results


# ──────────────────────────────────────────────────────────────────────────────
# Breaker 6 — TxFailureMonitor (RISK-004) — gate-only
# ──────────────────────────────────────────────────────────────────────────────


def run_tx_failure_monitor() -> list[CheckResult]:
    """Record 4 failures in window (> threshold 3) → paused + diagnostic mode."""
    results: list[CheckResult] = []
    name = "tx_failure_monitor"

    monitor = TxFailureMonitor()
    for i in range(4):
        monitor.record_failure(
            tx_id=f"tx-{i:03d}",
            reason="revert",
            details="synthetic dry-run",
            strategy_id="LEND-001",
        )

    if not monitor.is_paused:
        results.append(_fail(name, "monitor should be paused after 4 failures > threshold 3"))
        return results
    if not monitor.diagnostic_mode:
        results.append(_fail(name, "diagnostic_mode should be active"))
        return results
    if monitor.can_execute():
        results.append(_fail(name, "can_execute() should be False when paused"))
        return results
    snap = monitor.get_state()
    if snap.failures_in_window != 4:
        results.append(_fail(name, f"expected 4 failures in window, got {snap.failures_in_window}"))
        return results
    results.append(_ok(name, "paused after 4 reverts; diagnostic_mode active; can_execute()=False"))
    return results


# ──────────────────────────────────────────────────────────────────────────────
# Driver
# ──────────────────────────────────────────────────────────────────────────────


BREAKER_CHECKS = [
    ("drawdown_breaker", run_drawdown_breaker),
    ("position_loss_limit", run_position_loss_limit),
    ("tvl_monitor", run_tvl_monitor),
    ("gas_spike_breaker", run_gas_spike_breaker),
    ("oracle_guard", run_oracle_guard),
    ("tx_failure_monitor", run_tx_failure_monitor),
]


def run_all() -> list[CheckResult]:
    """Run every breaker dry-run check and aggregate results."""
    all_results: list[CheckResult] = []
    for breaker_name, fn in BREAKER_CHECKS:
        _log.info("breaker_check_start", breaker=breaker_name)
        try:
            all_results.extend(fn())
        except Exception as exc:  # driver must keep running across breakers
            all_results.append(
                _fail(breaker_name, f"unexpected exception: {type(exc).__name__}: {exc}"),
            )
    return all_results


def main() -> int:
    """Entry point — returns 0 if all checks pass, 1 otherwise."""
    results = run_all()
    pass_count = sum(1 for r in results if r.passed)
    fail_count = sum(1 for r in results if not r.passed)
    _log.info(
        "breaker_dryrun_summary",
        total=len(results),
        passed=pass_count,
        failed=fail_count,
    )
    if fail_count:
        for r in results:
            if not r.passed:
                _log.error("breaker_check_failed", check=r.name, detail=r.detail)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
