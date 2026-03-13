"""Tests for monitoring/event_emitter.py — dashboard event emission."""

from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from monitoring.event_emitter import (
    DASHBOARD_EVENTS_MAXLEN,
    DASHBOARD_EVENTS_STREAM,
    emit_dashboard_event,
)


@pytest.fixture()
def mock_redis() -> MagicMock:
    """Create a mock RedisManager with a mock client."""
    redis_mgr = MagicMock()
    redis_mgr.client = MagicMock()
    return redis_mgr


class TestEmitDashboardEvent:
    """Tests for emit_dashboard_event()."""

    def test_publishes_to_correct_stream(self, mock_redis: MagicMock) -> None:
        emit_dashboard_event(mock_redis, "eval_complete", {
            "strategy_id": "LEND-001",
            "signals_count": 2,
            "actionable": True,
        })

        mock_redis.client.xadd.assert_called_once()
        call_args = mock_redis.client.xadd.call_args
        assert call_args[0][0] == DASHBOARD_EVENTS_STREAM

    def test_maxlen_1000(self, mock_redis: MagicMock) -> None:
        emit_dashboard_event(mock_redis, "eval_complete", {
            "strategy_id": "LEND-001",
            "signals_count": 1,
            "actionable": False,
        })

        call_kwargs = mock_redis.client.xadd.call_args
        assert call_kwargs.kwargs.get("maxlen") == DASHBOARD_EVENTS_MAXLEN
        assert call_kwargs.kwargs.get("approximate") is True

    def test_version_field_added(self, mock_redis: MagicMock) -> None:
        emit_dashboard_event(mock_redis, "hold_mode", {
            "active": True,
            "reason": "test",
        })

        raw = mock_redis.client.xadd.call_args[0][1]["data"]
        event = json.loads(raw)
        assert event["version"] == "1.0.0"

    def test_timestamp_is_iso8601(self, mock_redis: MagicMock) -> None:
        emit_dashboard_event(mock_redis, "hold_mode", {
            "active": False,
            "reason": "cleared",
        })

        raw = mock_redis.client.xadd.call_args[0][1]["data"]
        event = json.loads(raw)
        # Should parse without error
        dt = datetime.fromisoformat(event["timestamp"])
        assert dt is not None

    def test_eval_complete_payload(self, mock_redis: MagicMock) -> None:
        emit_dashboard_event(mock_redis, "eval_complete", {
            "strategy_id": "LP-001",
            "signals_count": 3,
            "actionable": True,
        })

        raw = mock_redis.client.xadd.call_args[0][1]["data"]
        event = json.loads(raw)
        assert event["eventType"] == "eval_complete"
        assert event["data"]["strategy_id"] == "LP-001"
        assert event["data"]["signals_count"] == 3
        assert event["data"]["actionable"] is True

    def test_decision_made_payload(self, mock_redis: MagicMock) -> None:
        emit_dashboard_event(mock_redis, "decision_made", {
            "decision_id": "abc123",
            "action": "ADJUST",
            "summary": "Rebalance LP",
            "order_count": 1,
        })

        raw = mock_redis.client.xadd.call_args[0][1]["data"]
        event = json.loads(raw)
        assert event["eventType"] == "decision_made"
        assert event["data"]["action"] == "ADJUST"

    def test_order_emitted_payload(self, mock_redis: MagicMock) -> None:
        emit_dashboard_event(mock_redis, "order_emitted", {
            "order_id": "ord-1",
            "strategy": "LEND-001",
            "protocol": "aave_v3",
            "action": "supply",
            "amount": "1000",
        })

        raw = mock_redis.client.xadd.call_args[0][1]["data"]
        event = json.loads(raw)
        assert event["eventType"] == "order_emitted"
        assert event["data"]["protocol"] == "aave_v3"

    def test_execution_result_payload(self, mock_redis: MagicMock) -> None:
        emit_dashboard_event(mock_redis, "execution_result", {
            "order_id": "ord-1",
            "tx_hash": "0xabc",
            "status": "confirmed",
            "gas_used": "21000",
            "effective_gas_price": "1000000000",
        })

        raw = mock_redis.client.xadd.call_args[0][1]["data"]
        event = json.loads(raw)
        assert event["eventType"] == "execution_result"
        assert event["data"]["status"] == "confirmed"

    def test_breaker_state_payload(self, mock_redis: MagicMock) -> None:
        emit_dashboard_event(mock_redis, "breaker_state", {
            "name": "drawdown",
            "status": "triggered",
            "current": "22.5",
            "limit": "20.0",
        })

        raw = mock_redis.client.xadd.call_args[0][1]["data"]
        event = json.loads(raw)
        assert event["eventType"] == "breaker_state"
        assert event["data"]["name"] == "drawdown"

    def test_hold_mode_payload(self, mock_redis: MagicMock) -> None:
        emit_dashboard_event(mock_redis, "hold_mode", {
            "active": True,
            "reason": "API unavailable",
        })

        raw = mock_redis.client.xadd.call_args[0][1]["data"]
        event = json.loads(raw)
        assert event["eventType"] == "hold_mode"
        assert event["data"]["active"] is True

    def test_command_ack_payload(self, mock_redis: MagicMock) -> None:
        emit_dashboard_event(mock_redis, "command_ack", {
            "command_id": "cmd-1",
            "command_type": "strategy:activate",
            "success": True,
            "error": None,
        })

        raw = mock_redis.client.xadd.call_args[0][1]["data"]
        event = json.loads(raw)
        assert event["eventType"] == "command_ack"
        assert event["data"]["success"] is True

    def test_system_health_payload(self, mock_redis: MagicMock) -> None:
        emit_dashboard_event(mock_redis, "system_health", {
            "service": "py-engine",
            "status": "healthy",
            "latency_ms": 42,
        })

        raw = mock_redis.client.xadd.call_args[0][1]["data"]
        event = json.loads(raw)
        assert event["eventType"] == "system_health"
        assert event["data"]["service"] == "py-engine"

    def test_invalid_event_type_not_published(self, mock_redis: MagicMock) -> None:
        emit_dashboard_event(mock_redis, "invalid_type", {"foo": "bar"})

        mock_redis.client.xadd.assert_not_called()

    def test_redis_error_does_not_raise(self, mock_redis: MagicMock) -> None:
        mock_redis.client.xadd.side_effect = ConnectionError("Redis down")

        # Should not raise
        emit_dashboard_event(mock_redis, "eval_complete", {
            "strategy_id": "LEND-001",
            "signals_count": 0,
            "actionable": False,
        })

    def test_multiple_events_independent(self, mock_redis: MagicMock) -> None:
        """Each event gets its own timestamp and version."""
        emit_dashboard_event(mock_redis, "eval_complete", {
            "strategy_id": "LEND-001",
            "signals_count": 1,
            "actionable": True,
        })
        emit_dashboard_event(mock_redis, "decision_made", {
            "decision_id": "d1",
            "action": "HOLD",
            "summary": "No action",
            "order_count": 0,
        })

        assert mock_redis.client.xadd.call_count == 2
        for call in mock_redis.client.xadd.call_args_list:
            raw = call[0][1]["data"]
            event = json.loads(raw)
            assert event["version"] == "1.0.0"
            assert "timestamp" in event
