"""Unit tests for the execution-results consumer (managed-portfolio P1.5b)."""

from __future__ import annotations

from datetime import UTC, datetime

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


def test_failed_records_a_failure() -> None:
    monitor = TxFailureMonitor()
    consumer = ResultsConsumer(tx_failure=monitor)
    consumer.handle_result(_result("failed"))
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
