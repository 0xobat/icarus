"""Tests for diagnostic mode — HARNESS-004."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from harness.diagnostic_mode import (
    DiagnosticMode,
    DiagnosticTrigger,
    StateDump,
)
from harness.state_manager import StateManager


def _make_state_manager(tmp_path: Path) -> StateManager:
    return StateManager(state_path=tmp_path / "state.json")


def _make_redis_mock(
    orders: list[dict[str, Any]] | None = None,
    results: list[dict[str, Any]] | None = None,
) -> MagicMock:
    mock = MagicMock()
    mock.stream_read.side_effect = lambda channel, **kw: (
        (orders or []) if "orders" in channel else (results or [])
    )
    return mock


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------

class TestEntry:

    def test_enters_diagnostic_mode(self, tmp_path: Path) -> None:
        sm = _make_state_manager(tmp_path)
        diag = DiagnosticMode(state_manager=sm)
        assert diag.is_active is False
        diag.enter(DiagnosticTrigger.MANUAL_COMMAND)
        assert diag.is_active is True

    def test_sets_operational_flags(self, tmp_path: Path) -> None:
        sm = _make_state_manager(tmp_path)
        diag = DiagnosticMode(state_manager=sm)
        diag.enter(DiagnosticTrigger.STARTUP_RECONCILIATION)
        flags = sm.get_operational_flags()
        assert flags["diagnostic_mode"] is True
        assert flags["trading_paused"] is True

    def test_records_entry_time(self, tmp_path: Path) -> None:
        sm = _make_state_manager(tmp_path)
        diag = DiagnosticMode(state_manager=sm)
        diag.enter(DiagnosticTrigger.MANUAL_COMMAND)
        assert diag.entry_time is not None

    def test_double_enter_raises(self, tmp_path: Path) -> None:
        sm = _make_state_manager(tmp_path)
        diag = DiagnosticMode(state_manager=sm)
        diag.enter(DiagnosticTrigger.MANUAL_COMMAND)
        with pytest.raises(RuntimeError, match="Already in diagnostic mode"):
            diag.enter(DiagnosticTrigger.MANUAL_COMMAND)

    def test_returns_state_dump(self, tmp_path: Path) -> None:
        sm = _make_state_manager(tmp_path)
        diag = DiagnosticMode(state_manager=sm)
        dump = diag.enter(DiagnosticTrigger.MANUAL_COMMAND)
        assert isinstance(dump, StateDump)
        assert dump.trigger == DiagnosticTrigger.MANUAL_COMMAND.value


# ---------------------------------------------------------------------------
# Triggers
# ---------------------------------------------------------------------------

class TestTriggers:

    def test_startup_reconciliation_trigger(self, tmp_path: Path) -> None:
        sm = _make_state_manager(tmp_path)
        diag = DiagnosticMode(state_manager=sm)
        dump = diag.enter(DiagnosticTrigger.STARTUP_RECONCILIATION)
        assert dump.trigger == "startup_reconciliation"

    def test_critical_circuit_breaker_trigger(self, tmp_path: Path) -> None:
        sm = _make_state_manager(tmp_path)
        diag = DiagnosticMode(state_manager=sm)
        dump = diag.enter(DiagnosticTrigger.CRITICAL_CIRCUIT_BREAKER)
        assert dump.trigger == "critical_circuit_breaker"

    def test_manual_command_trigger(self, tmp_path: Path) -> None:
        sm = _make_state_manager(tmp_path)
        diag = DiagnosticMode(state_manager=sm)
        dump = diag.enter(DiagnosticTrigger.MANUAL_COMMAND)
        assert dump.trigger == "manual_command"


# ---------------------------------------------------------------------------
# State dump
# ---------------------------------------------------------------------------

class TestStateDump:

    def test_captures_agent_state(self, tmp_path: Path) -> None:
        sm = _make_state_manager(tmp_path)
        sm.set_position("aave-eth", {"protocol": "aave", "amount": 2.0})
        diag = DiagnosticMode(state_manager=sm)
        dump = diag.enter(DiagnosticTrigger.MANUAL_COMMAND)
        assert "aave-eth" in dump.agent_state["positions"]

    def test_captures_strategy_statuses(self, tmp_path: Path) -> None:
        sm = _make_state_manager(tmp_path)
        sm.set_strategy_status("STRAT-001", "active")
        diag = DiagnosticMode(state_manager=sm)
        dump = diag.enter(DiagnosticTrigger.MANUAL_COMMAND)
        assert dump.agent_state["strategy_statuses"]["STRAT-001"] == "active"

    def test_captures_redis_state(self, tmp_path: Path) -> None:
        sm = _make_state_manager(tmp_path)
        redis = _make_redis_mock(
            orders=[{"id": "1", "data": {}}],
            results=[{"id": "2", "data": {}}, {"id": "3", "data": {}}],
        )
        diag = DiagnosticMode(state_manager=sm, redis_manager=redis)
        dump = diag.enter(DiagnosticTrigger.MANUAL_COMMAND)
        assert dump.redis_stream_state["pending_orders"] == 1
        assert dump.redis_stream_state["pending_results"] == 2

    def test_no_redis_empty_stream_state(self, tmp_path: Path) -> None:
        sm = _make_state_manager(tmp_path)
        diag = DiagnosticMode(state_manager=sm, redis_manager=None)
        dump = diag.enter(DiagnosticTrigger.MANUAL_COMMAND)
        assert dump.redis_stream_state == {}

    def test_redis_error_captured(self, tmp_path: Path) -> None:
        sm = _make_state_manager(tmp_path)
        redis = MagicMock()
        redis.stream_read.side_effect = ConnectionError("down")
        diag = DiagnosticMode(state_manager=sm, redis_manager=redis)
        dump = diag.enter(DiagnosticTrigger.MANUAL_COMMAND)
        assert "error" in dump.redis_stream_state

    def test_additional_context_captured(self, tmp_path: Path) -> None:
        sm = _make_state_manager(tmp_path)
        diag = DiagnosticMode(state_manager=sm)
        dump = diag.enter(
            DiagnosticTrigger.MANUAL_COMMAND,
            additional_context={"reason": "manual investigation"},
        )
        assert dump.additional_context["reason"] == "manual investigation"

    def test_dump_has_timestamp(self, tmp_path: Path) -> None:
        sm = _make_state_manager(tmp_path)
        diag = DiagnosticMode(state_manager=sm)
        dump = diag.enter(DiagnosticTrigger.MANUAL_COMMAND)
        assert dump.timestamp != ""

    def test_state_dump_accessible_after_entry(self, tmp_path: Path) -> None:
        sm = _make_state_manager(tmp_path)
        diag = DiagnosticMode(state_manager=sm)
        returned_dump = diag.enter(DiagnosticTrigger.MANUAL_COMMAND)
        assert diag.state_dump is returned_dump


# ---------------------------------------------------------------------------
# Trading blocked
# ---------------------------------------------------------------------------

class TestTradingBlocked:

    def test_trading_not_blocked_initially(self, tmp_path: Path) -> None:
        sm = _make_state_manager(tmp_path)
        diag = DiagnosticMode(state_manager=sm)
        assert diag.should_block_trading() is False

    def test_trading_blocked_in_diagnostic(self, tmp_path: Path) -> None:
        sm = _make_state_manager(tmp_path)
        diag = DiagnosticMode(state_manager=sm)
        diag.enter(DiagnosticTrigger.MANUAL_COMMAND)
        assert diag.should_block_trading() is True

    def test_trading_unblocked_after_exit(self, tmp_path: Path) -> None:
        sm = _make_state_manager(tmp_path)
        diag = DiagnosticMode(state_manager=sm)
        diag.enter(DiagnosticTrigger.MANUAL_COMMAND)
        diag.exit()
        assert diag.should_block_trading() is False


# ---------------------------------------------------------------------------
# Exit
# ---------------------------------------------------------------------------

class TestExit:

    def test_exit_clears_active(self, tmp_path: Path) -> None:
        sm = _make_state_manager(tmp_path)
        diag = DiagnosticMode(state_manager=sm)
        diag.enter(DiagnosticTrigger.MANUAL_COMMAND)
        diag.exit()
        assert diag.is_active is False

    def test_exit_clears_operational_flags(self, tmp_path: Path) -> None:
        sm = _make_state_manager(tmp_path)
        diag = DiagnosticMode(state_manager=sm)
        diag.enter(DiagnosticTrigger.MANUAL_COMMAND)
        diag.exit()
        flags = sm.get_operational_flags()
        assert flags["diagnostic_mode"] is False
        assert flags["trading_paused"] is False

    def test_exit_when_not_active_raises(self, tmp_path: Path) -> None:
        sm = _make_state_manager(tmp_path)
        diag = DiagnosticMode(state_manager=sm)
        with pytest.raises(RuntimeError, match="Not in diagnostic mode"):
            diag.exit()

    def test_exit_clears_state_dump(self, tmp_path: Path) -> None:
        sm = _make_state_manager(tmp_path)
        diag = DiagnosticMode(state_manager=sm)
        diag.enter(DiagnosticTrigger.MANUAL_COMMAND)
        diag.exit()
        assert diag.state_dump is None
        assert diag.entry_time is None

    def test_can_reenter_after_exit(self, tmp_path: Path) -> None:
        sm = _make_state_manager(tmp_path)
        diag = DiagnosticMode(state_manager=sm)
        diag.enter(DiagnosticTrigger.MANUAL_COMMAND)
        diag.exit()
        diag.enter(DiagnosticTrigger.CRITICAL_CIRCUIT_BREAKER)
        assert diag.is_active is True


# ---------------------------------------------------------------------------
# No auto-resume
# ---------------------------------------------------------------------------

class TestNoAutoResume:

    def test_stays_active_without_manual_exit(self, tmp_path: Path) -> None:
        sm = _make_state_manager(tmp_path)
        diag = DiagnosticMode(state_manager=sm)
        diag.enter(DiagnosticTrigger.MANUAL_COMMAND)
        assert diag.is_active is True
        assert diag.should_block_trading() is True
