# Managed Portfolio P1.5b — Execution-Results Consumer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development or superpowers:executing-plans. Checkbox (`- [ ]`) steps.

**Goal:** `ResultsConsumer` — turns `execution:results:{chain}` envelopes into (a) tx-failure-monitor updates (closing critique #2's "breakers never fed" for the tx-failure breaker) and (b) a structured audit log. Pure decision logic is unit-testable against the real in-memory `TxFailureMonitor`; the Redis subscribe loop is a thin glue method exercised live in P1.5c.

**Architecture:** New module `decision_engine/results_consumer.py`. `parse()` validates a JSON payload into `icarus.envelopes.results.ExecutionResult`; `handle_result()` maps status → monitor call + audit log; `run()` is the thin async Redis-subscribe loop (live). The mapping correctly treats `rejected_by_guard` as a *non-failure* (the guard working as designed), not a tx failure.

**Tech Stack:** Python 3.13, `uv`, `pytest`, pydantic envelopes, structlog.

---

## Context the implementer needs

- **`ExecutionResult`** (`icarus.envelopes.results`): fields `version, order_id, correlation_id, timestamp, chain, status, template_id, candidate_id, tx_hash, block_number, gas_used_wei, effective_gas_price_wei, fill_price, amount_out, solana_specific, revert_reason, error, retry_count`. `status: ExecutionStatus = Literal["confirmed","failed","reverted","timeout","rejected_by_guard"]`. For `chain="base"`, `solana_specific` must be `None`. Frozen, `extra="forbid"`. Construct minimal base result with `ExecutionResult(order_id=..., correlation_id=..., timestamp=..., chain="base", status=...)`.
- **`TxFailureMonitor`** (`decision_engine.risk.tx_failure_monitor`): `record_failure(*, tx_id, reason, details="", strategy_id=None, now=None)`, `record_success(tx_id, *, now=None)`, `can_execute() -> bool`, `get_failure_count(now=None) -> int`. Default threshold 3 → `can_execute()` becomes False after >3 failures in the window. Known failure `reason`s: `revert, out_of_gas, nonce_issue` (parameter), `timeout, network_error, rpc_error` (systemic); unknown reasons still count.
- **Status → action mapping:** `confirmed → record_success`; `reverted → record_failure(reason="revert")`; `failed → record_failure(reason="revert")`; `timeout → record_failure(reason="timeout")`; `rejected_by_guard → audit only (NOT a tx failure — the guard rejected pre-broadcast, which is correct behavior).`
- Tests: `decision-engine/tests/test_results_consumer_unit.py`. Run: `uv run pytest <path> -v`.

## File Structure
- **Create:** `decision-engine/src/decision_engine/results_consumer.py` — `ResultsConsumer`.
- **Create:** `decision-engine/tests/test_results_consumer_unit.py` — unit tests against a real `TxFailureMonitor`.

---

## Task 1: `parse` + `handle_result` (status → monitor)

**Files:** Create both files.

- [ ] **Step 1: Write the failing test**

Create `decision-engine/tests/test_results_consumer_unit.py`:

```python
"""Unit tests for the execution-results consumer (managed-portfolio P1.5b)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from icarus.envelopes.results import ExecutionResult

from decision_engine.results_consumer import ResultsConsumer
from decision_engine.risk.tx_failure_monitor import TxFailureMonitor


def _result(status: str, order_id: str = "o1") -> ExecutionResult:
    return ExecutionResult(
        order_id=order_id,
        correlation_id="c1",
        timestamp=datetime(2026, 6, 3, tzinfo=UTC),
        chain="base",
        status=status,  # type: ignore[arg-type]
    )


def test_parse_roundtrips_a_confirmed_result() -> None:
    monitor = TxFailureMonitor()
    consumer = ResultsConsumer(tx_failure=monitor)
    payload = _result("confirmed").model_dump_json()
    parsed = consumer.parse(payload)
    assert parsed.status == "confirmed"
    assert parsed.order_id == "o1"
    assert parsed.chain == "base"


def test_confirmed_keeps_execution_enabled() -> None:
    monitor = TxFailureMonitor()
    consumer = ResultsConsumer(tx_failure=monitor)
    consumer.handle_result(_result("confirmed"))
    assert monitor.can_execute() is True
    assert monitor.get_failure_count() == 0


def test_reverted_records_a_failure() -> None:
    monitor = TxFailureMonitor()
    consumer = ResultsConsumer(tx_failure=monitor)
    consumer.handle_result(_result("reverted"))
    assert monitor.get_failure_count() == 1


def test_timeout_records_a_failure() -> None:
    monitor = TxFailureMonitor()
    consumer = ResultsConsumer(tx_failure=monitor)
    consumer.handle_result(_result("timeout"))
    assert monitor.get_failure_count() == 1


def test_four_failures_trip_the_monitor() -> None:
    monitor = TxFailureMonitor()
    consumer = ResultsConsumer(tx_failure=monitor)
    for i in range(4):  # threshold is 3 → >3 trips
        consumer.handle_result(_result("reverted", order_id=f"o{i}"))
    assert monitor.can_execute() is False


def test_rejected_by_guard_is_not_a_failure() -> None:
    monitor = TxFailureMonitor()
    consumer = ResultsConsumer(tx_failure=monitor)
    consumer.handle_result(_result("rejected_by_guard"))
    assert monitor.get_failure_count() == 0
    assert monitor.can_execute() is True
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest decision-engine/tests/test_results_consumer_unit.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'decision_engine.results_consumer'`

- [ ] **Step 3: Implement**

Create `decision-engine/src/decision_engine/results_consumer.py`:

```python
"""Execution-results consumer — feeds the tx-failure monitor + audit log.

Managed-portfolio P1.5b. Subscribes to `execution:results:{chain}`, validates
each envelope into an ExecutionResult, and maps its status onto the tx-failure
monitor (closing the critique's "breakers never fed live state" gap for tx
failures). `rejected_by_guard` is NOT a tx failure — it is the pre-trade guard
working as designed — so it is audit-logged but not counted.

The `parse` + `handle_result` core is pure decision logic (unit-tested against
the real monitor); `run` is the thin async Redis-subscribe loop (exercised live
in P1.5c).
"""

from __future__ import annotations

from typing import Any

import structlog
from icarus.envelopes.results import ExecutionResult

from decision_engine.risk.tx_failure_monitor import TxFailureMonitor

logger = structlog.get_logger(service="decision-engine.results_consumer")

# Failure statuses → the reason string the tx-failure monitor classifies.
_STATUS_TO_REASON: dict[str, str] = {
    "reverted": "revert",
    "failed": "revert",
    "timeout": "timeout",
}


class ResultsConsumer:
    """Maps execution results onto the tx-failure monitor + audit log."""

    def __init__(self, *, tx_failure: TxFailureMonitor) -> None:
        self._tx_failure = tx_failure

    @staticmethod
    def parse(payload: str) -> ExecutionResult:
        """Validate a JSON payload into an ExecutionResult."""
        return ExecutionResult.model_validate_json(payload)

    def handle_result(self, result: ExecutionResult) -> None:
        """Route one result: success / failure / guard-rejection + audit."""
        if result.status == "confirmed":
            self._tx_failure.record_success(result.order_id)
        elif result.status in _STATUS_TO_REASON:
            self._tx_failure.record_failure(
                tx_id=result.order_id,
                reason=_STATUS_TO_REASON[result.status],
                details=(result.revert_reason or result.error or ""),
            )
        # rejected_by_guard (and any future non-failure status): audit only.

        logger.info(
            "execution_result",
            order_id=result.order_id,
            correlation_id=result.correlation_id,
            chain=result.chain,
            status=result.status,
            tx_hash=result.tx_hash,
            can_execute=self._tx_failure.can_execute(),
        )

    async def run(self, pubsub: Any) -> None:
        """Thin live loop: consume messages off a subscribed Redis pubsub.

        `pubsub` is an already-subscribed redis.asyncio PubSub. Each message's
        `data` is the JSON ExecutionResult payload. Validation errors are logged
        and skipped — a malformed result must not kill the consumer.
        """
        async for message in pubsub.listen():
            if message.get("type") != "message":
                continue
            try:
                result = self.parse(message["data"])
            except Exception:
                logger.warning("results_parse_failed", raw=str(message.get("data"))[:200], exc_info=True)
                continue
            self.handle_result(result)


__all__ = ["ResultsConsumer"]
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest decision-engine/tests/test_results_consumer_unit.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Run full decision-engine suite (no regressions)**

Run: `uv run pytest decision-engine/tests -q`
Expected: all pass (was 310 → 316).

- [ ] **Step 6: Commit**

```bash
git add decision-engine/src/decision_engine/results_consumer.py decision-engine/tests/test_results_consumer_unit.py
git commit -m "feat(daedalus): P1.5b execution-results consumer (feeds tx-failure monitor)"
```

---

## Self-Review (against P1.5 brief "Execution-results consumer")

**1. Spec coverage:** `parse` validates envelopes; `handle_result` feeds the tx-failure monitor (confirmed→success, reverted/failed/timeout→failure, rejected_by_guard→audit-only) + audit log; `run` is the thin live loop with fail-safe parse. Out of scope (flagged): full `PortfolioPosition` lot reconciliation (P2), gas/drawdown feeds (those are per-tick in the worker loop, P1.5c).

**2. Placeholder scan:** none — `run` is real (not a stub); the live loop is intentionally thin.

**3. Type consistency:** `ResultsConsumer(tx_failure=...)`, `parse(payload)`, `handle_result(result)`, `run(pubsub)`. `record_failure`/`record_success`/`can_execute`/`get_failure_count` match `tx_failure_monitor.py`. `ExecutionResult` construction matches the envelope (base chain → solana_specific None default).

**4. Behavior verified:** threshold 3 → 4 reverts trip `can_execute()=False`; `rejected_by_guard` counts 0; confirmed keeps enabled.

---

## Execution Handoff
Subagent-Driven or Inline. Additive; depends on `tx_failure_monitor` + `results` envelope (both present). The `run` loop is wired into `__main__` in P1.5c (live).
