"""Tests for harness/command_listener.py — dashboard command listener."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from harness.command_listener import (
    CONSUMER_GROUP,
    DASHBOARD_COMMANDS_STREAM,
    _dispatch_command,
    _emit_ack,
    _is_stale,
    listen_for_commands,
)


def _make_command(
    command_type: str,
    data: dict | None = None,
    *,
    command_id: str = "cmd-001",
    timestamp: str | None = None,
) -> dict:
    """Build a valid dashboard command message."""
    if timestamp is None:
        timestamp = datetime.now(UTC).isoformat()
    return {
        "version": "1.0.0",
        "command_id": command_id,
        "timestamp": timestamp,
        "commandType": command_type,
        "data": data or {},
    }


def _setup_mock_redis(mock_redis: MagicMock, messages: list | None = None) -> None:
    """Configure mock Redis for listen_for_commands tests.

    Delivers `messages` on the first xreadgroup call, then raises
    CancelledError on the second call to exit the loop.
    """
    call_count = 0

    def xreadgroup_side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1 and messages:
            return messages
        raise asyncio.CancelledError()

    mock_redis.client.xreadgroup.side_effect = xreadgroup_side_effect
    mock_redis.client.xautoclaim.return_value = ("0-0", [], [])


@pytest.fixture()
def mock_redis() -> MagicMock:
    """Create a mock RedisManager with a mock client."""
    redis_mgr = MagicMock()
    redis_mgr.client = MagicMock()
    return redis_mgr


@pytest.fixture()
def mock_strategy_manager() -> MagicMock:
    mgr = MagicMock()
    return mgr


@pytest.fixture()
def mock_hold_mode() -> MagicMock:
    hm = MagicMock()
    hm.is_active.return_value = False
    return hm


@pytest.fixture()
def mock_breakers() -> dict[str, MagicMock]:
    drawdown = MagicMock()
    drawdown.manual_restart = MagicMock(return_value=True)
    gas_spike = MagicMock(spec=[])  # no manual_restart or reset
    return {"drawdown": drawdown, "gas_spike": gas_spike}


@pytest.fixture()
def mock_db_repo() -> MagicMock:
    return MagicMock()


@pytest.fixture()
def mock_emitter() -> MagicMock:
    return MagicMock()


class TestDispatchCommand:
    """Tests for _dispatch_command routing."""

    def test_strategy_activate(
        self, mock_strategy_manager: MagicMock, mock_hold_mode: MagicMock,
        mock_breakers: dict, mock_db_repo: MagicMock,
    ) -> None:
        _dispatch_command(
            command_type="strategy:activate",
            cmd_data={"strategy_id": "LEND-001"},
            strategy_manager=mock_strategy_manager,
            hold_mode=mock_hold_mode,
            circuit_breakers=mock_breakers,
            db_repo=mock_db_repo,
        )
        mock_strategy_manager.activate.assert_called_once_with("LEND-001")

    def test_strategy_deactivate(
        self, mock_strategy_manager: MagicMock, mock_hold_mode: MagicMock,
        mock_breakers: dict, mock_db_repo: MagicMock,
    ) -> None:
        _dispatch_command(
            command_type="strategy:deactivate",
            cmd_data={"strategy_id": "LP-001"},
            strategy_manager=mock_strategy_manager,
            hold_mode=mock_hold_mode,
            circuit_breakers=mock_breakers,
            db_repo=mock_db_repo,
        )
        mock_strategy_manager.deactivate.assert_called_once_with("LP-001")

    def test_system_enter_hold(
        self, mock_strategy_manager: MagicMock, mock_hold_mode: MagicMock,
        mock_breakers: dict, mock_db_repo: MagicMock,
    ) -> None:
        _dispatch_command(
            command_type="system:enter_hold",
            cmd_data={"reason": "Maintenance window"},
            strategy_manager=mock_strategy_manager,
            hold_mode=mock_hold_mode,
            circuit_breakers=mock_breakers,
            db_repo=mock_db_repo,
        )
        mock_hold_mode.enter.assert_called_once()
        call_args = mock_hold_mode.enter.call_args
        # reason may be positional or keyword
        reason = call_args.kwargs.get("reason") or call_args.args[0]
        assert reason == "Maintenance window"

    def test_system_exit_hold(
        self, mock_strategy_manager: MagicMock, mock_hold_mode: MagicMock,
        mock_breakers: dict, mock_db_repo: MagicMock,
    ) -> None:
        _dispatch_command(
            command_type="system:exit_hold",
            cmd_data={},
            strategy_manager=mock_strategy_manager,
            hold_mode=mock_hold_mode,
            circuit_breakers=mock_breakers,
            db_repo=mock_db_repo,
        )
        mock_hold_mode.exit.assert_called_once_with(operator="manual")

    def test_breaker_reset_with_manual_restart(
        self, mock_strategy_manager: MagicMock, mock_hold_mode: MagicMock,
        mock_breakers: dict, mock_db_repo: MagicMock,
    ) -> None:
        _dispatch_command(
            command_type="breaker:reset",
            cmd_data={"breaker_name": "drawdown"},
            strategy_manager=mock_strategy_manager,
            hold_mode=mock_hold_mode,
            circuit_breakers=mock_breakers,
            db_repo=mock_db_repo,
        )
        mock_breakers["drawdown"].manual_restart.assert_called_once()

    def test_breaker_reset_records_audit_alert(
        self, mock_strategy_manager: MagicMock, mock_hold_mode: MagicMock,
        mock_breakers: dict, mock_db_repo: MagicMock,
    ) -> None:
        _dispatch_command(
            command_type="breaker:reset",
            cmd_data={"breaker_name": "drawdown"},
            strategy_manager=mock_strategy_manager,
            hold_mode=mock_hold_mode,
            circuit_breakers=mock_breakers,
            db_repo=mock_db_repo,
        )
        mock_db_repo.create_alert.assert_called_once()
        alert_data = mock_db_repo.create_alert.call_args[0][0]
        assert alert_data["category"] == "manual_reset"
        assert "drawdown" in alert_data["message"]
        assert alert_data["data"]["source"] == "manual_reset"

    def test_breaker_reset_unknown_breaker(
        self, mock_strategy_manager: MagicMock, mock_hold_mode: MagicMock,
        mock_breakers: dict, mock_db_repo: MagicMock,
    ) -> None:
        with pytest.raises(KeyError, match="Unknown breaker"):
            _dispatch_command(
                command_type="breaker:reset",
                cmd_data={"breaker_name": "nonexistent"},
                strategy_manager=mock_strategy_manager,
                hold_mode=mock_hold_mode,
                circuit_breakers=mock_breakers,
                db_repo=mock_db_repo,
            )

    def test_unknown_command_type(
        self, mock_strategy_manager: MagicMock, mock_hold_mode: MagicMock,
        mock_breakers: dict, mock_db_repo: MagicMock,
    ) -> None:
        with pytest.raises(ValueError, match="Unknown command type"):
            _dispatch_command(
                command_type="invalid:command",
                cmd_data={},
                strategy_manager=mock_strategy_manager,
                hold_mode=mock_hold_mode,
                circuit_breakers=mock_breakers,
                db_repo=mock_db_repo,
            )


class TestIsStale:
    """Tests for _is_stale timestamp check."""

    def test_fresh_command_not_stale(self) -> None:
        ts = datetime.now(UTC).isoformat()
        assert _is_stale(ts) is False

    def test_old_command_is_stale(self) -> None:
        old = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()
        assert _is_stale(old) is True

    def test_exactly_at_threshold_not_stale(self) -> None:
        ts = (datetime.now(UTC) - timedelta(minutes=4, seconds=59)).isoformat()
        assert _is_stale(ts) is False

    def test_invalid_timestamp_not_stale(self) -> None:
        assert _is_stale("not-a-timestamp") is False


class TestEmitAck:
    """Tests for _emit_ack helper."""

    def test_emits_success_ack(
        self, mock_redis: MagicMock, mock_emitter: MagicMock,
    ) -> None:
        _emit_ack(mock_redis, mock_emitter, "cmd-001", "strategy:activate",
                  success=True, error=None)
        mock_emitter.assert_called_once()
        args = mock_emitter.call_args[0]
        assert args[0] is mock_redis
        assert args[1] == "command_ack"
        payload = args[2]
        assert payload["command_id"] == "cmd-001"
        assert payload["command_type"] == "strategy:activate"
        assert payload["success"] is True
        assert payload["error"] is None

    def test_emits_failure_ack(
        self, mock_redis: MagicMock, mock_emitter: MagicMock,
    ) -> None:
        _emit_ack(mock_redis, mock_emitter, "cmd-002", "breaker:reset",
                  success=False, error="Breaker not found")
        payload = mock_emitter.call_args[0][2]
        assert payload["success"] is False
        assert payload["error"] == "Breaker not found"

    def test_emitter_failure_does_not_raise(
        self, mock_redis: MagicMock,
    ) -> None:
        bad_emitter = MagicMock(side_effect=RuntimeError("emit failed"))
        # Should not raise
        _emit_ack(mock_redis, bad_emitter, "cmd-003", "unknown",
                  success=False, error="test")


class TestConsumerGroupCreation:
    """Tests for consumer group creation idempotency."""

    @pytest.mark.asyncio()
    async def test_creates_group_on_startup(
        self, mock_redis: MagicMock, mock_strategy_manager: MagicMock,
        mock_hold_mode: MagicMock, mock_breakers: dict,
        mock_emitter: MagicMock, mock_db_repo: MagicMock,
    ) -> None:
        _setup_mock_redis(mock_redis)

        await listen_for_commands(
            mock_redis, mock_strategy_manager, mock_hold_mode,
            mock_breakers, mock_emitter, mock_db_repo,
        )

        mock_redis.client.xgroup_create.assert_called_once_with(
            DASHBOARD_COMMANDS_STREAM, CONSUMER_GROUP, id="0", mkstream=True,
        )

    @pytest.mark.asyncio()
    async def test_group_creation_idempotent_on_busygroup(
        self, mock_redis: MagicMock, mock_strategy_manager: MagicMock,
        mock_hold_mode: MagicMock, mock_breakers: dict,
        mock_emitter: MagicMock, mock_db_repo: MagicMock,
    ) -> None:
        import redis as redis_lib
        mock_redis.client.xgroup_create.side_effect = redis_lib.ResponseError(
            "BUSYGROUP Consumer Group name already exists",
        )
        _setup_mock_redis(mock_redis)

        # Should not crash — BUSYGROUP is handled gracefully
        await listen_for_commands(
            mock_redis, mock_strategy_manager, mock_hold_mode,
            mock_breakers, mock_emitter, mock_db_repo,
        )


class TestCommandProcessing:
    """Integration tests for full command processing flow."""

    @pytest.mark.asyncio()
    async def test_processes_strategy_activate(
        self, mock_redis: MagicMock, mock_strategy_manager: MagicMock,
        mock_hold_mode: MagicMock, mock_breakers: dict,
        mock_emitter: MagicMock, mock_db_repo: MagicMock,
    ) -> None:
        cmd = _make_command("strategy:activate", {"strategy_id": "LEND-001"})
        messages = [
            (DASHBOARD_COMMANDS_STREAM, [("1-0", {"data": json.dumps(cmd)})]),
        ]
        _setup_mock_redis(mock_redis, messages)

        await listen_for_commands(
            mock_redis, mock_strategy_manager, mock_hold_mode,
            mock_breakers, mock_emitter, mock_db_repo,
        )

        mock_strategy_manager.activate.assert_called_once_with("LEND-001")

    @pytest.mark.asyncio()
    async def test_xack_called_after_processing(
        self, mock_redis: MagicMock, mock_strategy_manager: MagicMock,
        mock_hold_mode: MagicMock, mock_breakers: dict,
        mock_emitter: MagicMock, mock_db_repo: MagicMock,
    ) -> None:
        cmd = _make_command("strategy:deactivate", {"strategy_id": "LP-001"})
        messages = [
            (DASHBOARD_COMMANDS_STREAM, [("2-0", {"data": json.dumps(cmd)})]),
        ]
        _setup_mock_redis(mock_redis, messages)

        await listen_for_commands(
            mock_redis, mock_strategy_manager, mock_hold_mode,
            mock_breakers, mock_emitter, mock_db_repo,
        )

        mock_redis.client.xack.assert_called_with(
            DASHBOARD_COMMANDS_STREAM, CONSUMER_GROUP, "2-0",
        )

    @pytest.mark.asyncio()
    async def test_command_ack_emitted_on_success(
        self, mock_redis: MagicMock, mock_strategy_manager: MagicMock,
        mock_hold_mode: MagicMock, mock_breakers: dict,
        mock_emitter: MagicMock, mock_db_repo: MagicMock,
    ) -> None:
        cmd = _make_command("system:exit_hold", {}, command_id="ack-test-001")
        messages = [
            (DASHBOARD_COMMANDS_STREAM, [("3-0", {"data": json.dumps(cmd)})]),
        ]
        _setup_mock_redis(mock_redis, messages)

        await listen_for_commands(
            mock_redis, mock_strategy_manager, mock_hold_mode,
            mock_breakers, mock_emitter, mock_db_repo,
        )

        mock_emitter.assert_called()
        ack_call = mock_emitter.call_args
        assert ack_call[0][1] == "command_ack"
        payload = ack_call[0][2]
        assert payload["command_id"] == "ack-test-001"
        assert payload["success"] is True
        assert payload["error"] is None

    @pytest.mark.asyncio()
    async def test_stale_command_discarded(
        self, mock_redis: MagicMock, mock_strategy_manager: MagicMock,
        mock_hold_mode: MagicMock, mock_breakers: dict,
        mock_emitter: MagicMock, mock_db_repo: MagicMock,
    ) -> None:
        old_ts = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()
        cmd = _make_command("strategy:activate", {"strategy_id": "LEND-001"},
                           timestamp=old_ts, command_id="stale-cmd")
        messages = [
            (DASHBOARD_COMMANDS_STREAM, [("4-0", {"data": json.dumps(cmd)})]),
        ]
        _setup_mock_redis(mock_redis, messages)

        await listen_for_commands(
            mock_redis, mock_strategy_manager, mock_hold_mode,
            mock_breakers, mock_emitter, mock_db_repo,
        )

        # Strategy should NOT be activated
        mock_strategy_manager.activate.assert_not_called()
        # XACK should still be called (message acknowledged)
        mock_redis.client.xack.assert_called()
        # command_ack should be emitted with error
        ack_payload = mock_emitter.call_args[0][2]
        assert ack_payload["success"] is False
        assert "stale" in ack_payload["error"].lower()

    @pytest.mark.asyncio()
    async def test_invalid_schema_rejected(
        self, mock_redis: MagicMock, mock_strategy_manager: MagicMock,
        mock_hold_mode: MagicMock, mock_breakers: dict,
        mock_emitter: MagicMock, mock_db_repo: MagicMock,
    ) -> None:
        bad_cmd = {"commandType": "strategy:activate"}
        messages = [
            (DASHBOARD_COMMANDS_STREAM, [("5-0", {"data": json.dumps(bad_cmd)})]),
        ]
        _setup_mock_redis(mock_redis, messages)

        await listen_for_commands(
            mock_redis, mock_strategy_manager, mock_hold_mode,
            mock_breakers, mock_emitter, mock_db_repo,
        )

        mock_strategy_manager.activate.assert_not_called()
        mock_redis.client.xack.assert_called()

    @pytest.mark.asyncio()
    async def test_command_ack_emitted_on_failure(
        self, mock_redis: MagicMock, mock_strategy_manager: MagicMock,
        mock_hold_mode: MagicMock, mock_breakers: dict,
        mock_emitter: MagicMock, mock_db_repo: MagicMock,
    ) -> None:
        mock_strategy_manager.activate.side_effect = KeyError("Unknown strategy: FAKE-001")
        cmd = _make_command("strategy:activate", {"strategy_id": "FAKE-001"},
                           command_id="fail-cmd")
        messages = [
            (DASHBOARD_COMMANDS_STREAM, [("6-0", {"data": json.dumps(cmd)})]),
        ]
        _setup_mock_redis(mock_redis, messages)

        await listen_for_commands(
            mock_redis, mock_strategy_manager, mock_hold_mode,
            mock_breakers, mock_emitter, mock_db_repo,
        )

        ack_payload = mock_emitter.call_args[0][2]
        assert ack_payload["success"] is False
        assert ack_payload["error"] is not None

    @pytest.mark.asyncio()
    async def test_breaker_reset_full_flow(
        self, mock_redis: MagicMock, mock_strategy_manager: MagicMock,
        mock_hold_mode: MagicMock, mock_breakers: dict,
        mock_emitter: MagicMock, mock_db_repo: MagicMock,
    ) -> None:
        cmd = _make_command("breaker:reset", {"breaker_name": "drawdown"},
                           command_id="reset-cmd")
        messages = [
            (DASHBOARD_COMMANDS_STREAM, [("7-0", {"data": json.dumps(cmd)})]),
        ]
        _setup_mock_redis(mock_redis, messages)

        await listen_for_commands(
            mock_redis, mock_strategy_manager, mock_hold_mode,
            mock_breakers, mock_emitter, mock_db_repo,
        )

        # Breaker reset called
        mock_breakers["drawdown"].manual_restart.assert_called_once()
        # Audit alert recorded
        mock_db_repo.create_alert.assert_called_once()
        # command_ack success
        ack_payload = mock_emitter.call_args[0][2]
        assert ack_payload["success"] is True
        assert ack_payload["command_id"] == "reset-cmd"
