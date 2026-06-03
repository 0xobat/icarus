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

from collections.abc import Callable
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

    def __init__(
        self,
        *,
        tx_failure: TxFailureMonitor,
        trade_sink: Callable[[dict], None] | None = None,
    ) -> None:
        self._tx_failure = tx_failure
        self._trade_sink = trade_sink

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

        if self._trade_sink is not None:
            self._trade_sink(
                {
                    "order_id": result.order_id,
                    "correlation_id": result.correlation_id,
                    "chain": result.chain,
                    "status": result.status,
                    "tx_hash": result.tx_hash,
                    "amount_out": str(result.amount_out) if result.amount_out is not None else None,
                    "fill_price": str(result.fill_price) if result.fill_price is not None else None,
                    "timestamp": result.timestamp.isoformat(),
                }
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
