"""Dashboard command listener — subscribes to dashboard:commands stream.

Dispatches control operations from the frontend dashboard.

Runs as an asyncio task alongside the DecisionLoop. Uses a Redis consumer
group for reliable delivery with acknowledgment. Commands older than 5 minutes
are discarded as stale. All per-message errors are caught — the listener loop
never crashes.
"""

from __future__ import annotations

import asyncio
import json
import platform
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from monitoring.logger import get_logger
from validation.schema_validator import validate

if TYPE_CHECKING:
    from db.repository import DatabaseRepository
    from harness.hold_mode import HoldMode
    from strategies.manager import StrategyManager

_logger = get_logger("command_listener", enable_file=False)

DASHBOARD_COMMANDS_STREAM = "dashboard:commands"
CONSUMER_GROUP = "dashboard-cmd-group"
STALE_THRESHOLD = timedelta(minutes=5)
AUTOCLAIM_MIN_IDLE_MS = 60_000


def _consumer_name() -> str:
    """Generate consumer name from hostname."""
    return f"py-engine-{platform.node()}"


async def listen_for_commands(
    redis_client: Any,
    strategy_manager: StrategyManager,
    hold_mode: HoldMode,
    circuit_breakers: dict[str, Any],
    event_emitter_fn: Callable[..., None],
    db_repo: DatabaseRepository,
) -> None:
    """Subscribe to dashboard:commands stream via consumer group, dispatch to handlers.

    Consumer group: dashboard-cmd-group
    Consumer name: py-engine-{hostname}

    Args:
        redis_client: RedisManager instance for stream access.
        strategy_manager: StrategyManager for activate/deactivate.
        hold_mode: HoldMode for enter/exit hold.
        circuit_breakers: Dict mapping breaker names to breaker objects.
        event_emitter_fn: emit_dashboard_event callable for command_ack.
        db_repo: DatabaseRepository for audit logging.
    """
    consumer = _consumer_name()
    client = redis_client.client

    # Create consumer group idempotently
    try:
        client.xgroup_create(
            DASHBOARD_COMMANDS_STREAM, CONSUMER_GROUP, id="$", mkstream=True,
        )
        _logger.info("Consumer group created", extra={"data": {
            "group": CONSUMER_GROUP, "stream": DASHBOARD_COMMANDS_STREAM,
        }})
    except Exception as e:
        if "BUSYGROUP" in str(e):
            _logger.debug("Consumer group already exists")
        else:
            _logger.warning(
                "Failed to create consumer group",
                extra={"data": {"error": str(e)}},
            )

    # Autoclaim pending messages older than 60s
    try:
        _autoclaim_pending(client, consumer)
    except Exception:
        _logger.debug("XAUTOCLAIM not available or no pending messages")

    _logger.info("Command listener started", extra={"data": {
        "consumer": consumer, "group": CONSUMER_GROUP,
    }})

    # Main loop
    while True:
        try:
            entries = await asyncio.to_thread(
                client.xreadgroup,
                CONSUMER_GROUP,
                consumer,
                {DASHBOARD_COMMANDS_STREAM: ">"},
                count=10,
                block=5000,
            )

            if not entries:
                continue

            for _stream_name, messages in entries:
                for msg_id, fields in messages:
                    await _process_command(
                        client=client,
                        msg_id=msg_id,
                        fields=fields,
                        redis_client=redis_client,
                        strategy_manager=strategy_manager,
                        hold_mode=hold_mode,
                        circuit_breakers=circuit_breakers,
                        event_emitter_fn=event_emitter_fn,
                        db_repo=db_repo,
                    )

        except asyncio.CancelledError:
            _logger.info("Command listener cancelled")
            return
        except Exception:
            _logger.exception("Error in command listener loop")
            await asyncio.sleep(1.0)


def _autoclaim_pending(client: Any, consumer: str) -> None:
    """Autoclaim pending messages older than 60s and process stale ones."""
    result = client.xautoclaim(
        DASHBOARD_COMMANDS_STREAM,
        CONSUMER_GROUP,
        consumer,
        min_idle_time=AUTOCLAIM_MIN_IDLE_MS,
        start_id="0-0",
    )
    if result and len(result) >= 2:
        claimed = result[1]
        if claimed:
            _logger.info(
                "Autoclaimed pending messages",
                extra={"data": {"count": len(claimed)}},
            )


async def _process_command(
    *,
    client: Any,
    msg_id: str,
    fields: dict[str, str],
    redis_client: Any,
    strategy_manager: StrategyManager,
    hold_mode: HoldMode,
    circuit_breakers: dict[str, Any],
    event_emitter_fn: Callable[..., None],
    db_repo: DatabaseRepository,
) -> None:
    """Process a single command message from the stream."""
    command_id = None
    command_type = None

    try:
        raw = fields.get("data")
        if not raw:
            client.xack(DASHBOARD_COMMANDS_STREAM, CONSUMER_GROUP, msg_id)
            return

        data = json.loads(raw)
        command_id = data.get("command_id", "unknown")
        command_type = data.get("commandType", "unknown")
        timestamp_str = data.get("timestamp")

        # Validate against schema
        valid, errors = validate("dashboard-commands", data)
        if not valid:
            _logger.warning(
                "Command failed schema validation",
                extra={"data": {
                    "command_id": command_id,
                    "errors": errors,
                }},
            )
            client.xack(DASHBOARD_COMMANDS_STREAM, CONSUMER_GROUP, msg_id)
            _emit_ack(redis_client, event_emitter_fn, command_id, command_type,
                      success=False, error=f"Schema validation failed: {'; '.join(errors)}")
            return

        # Check staleness
        if timestamp_str and _is_stale(timestamp_str):
            _logger.warning(
                "Discarding stale command",
                extra={"data": {
                    "command_id": command_id,
                    "commandType": command_type,
                    "timestamp": timestamp_str,
                }},
            )
            client.xack(DASHBOARD_COMMANDS_STREAM, CONSUMER_GROUP, msg_id)
            _emit_ack(redis_client, event_emitter_fn, command_id, command_type,
                      success=False, error="Command discarded: stale (>5 minutes old)")
            return

        # Dispatch
        cmd_data = data.get("data", {})
        _dispatch_command(
            command_type=command_type,
            cmd_data=cmd_data,
            strategy_manager=strategy_manager,
            hold_mode=hold_mode,
            circuit_breakers=circuit_breakers,
            db_repo=db_repo,
        )

        # ACK and emit success
        client.xack(DASHBOARD_COMMANDS_STREAM, CONSUMER_GROUP, msg_id)
        _emit_ack(redis_client, event_emitter_fn, command_id, command_type,
                  success=True, error=None)

        _logger.info(
            "Command processed",
            extra={"data": {
                "command_id": command_id,
                "commandType": command_type,
            }},
        )

    except Exception as e:
        _logger.exception(
            "Command processing failed",
            extra={"data": {
                "command_id": command_id,
                "commandType": command_type,
            }},
        )
        # Always ACK to prevent infinite redelivery
        try:
            client.xack(DASHBOARD_COMMANDS_STREAM, CONSUMER_GROUP, msg_id)
        except Exception:
            pass
        _emit_ack(redis_client, event_emitter_fn,
                  command_id or "unknown", command_type or "unknown",
                  success=False, error=str(e))


def _is_stale(timestamp_str: str) -> bool:
    """Check if a command timestamp is older than 5 minutes."""
    try:
        cmd_time = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
        now = datetime.now(UTC)
        return (now - cmd_time) > STALE_THRESHOLD
    except (ValueError, TypeError):
        return False


def _dispatch_command(
    *,
    command_type: str,
    cmd_data: dict[str, Any],
    strategy_manager: StrategyManager,
    hold_mode: HoldMode,
    circuit_breakers: dict[str, Any],
    db_repo: DatabaseRepository,
) -> None:
    """Route a command to the appropriate handler.

    Args:
        command_type: The commandType from the message.
        cmd_data: The data payload from the message.
        strategy_manager: StrategyManager instance.
        hold_mode: HoldMode instance.
        circuit_breakers: Dict of breaker name → breaker object.
        db_repo: DatabaseRepository for audit logging.

    Raises:
        KeyError: If strategy_id or breaker_name is unknown.
        ValueError: If command_type is unknown.
    """
    if command_type == "strategy:activate":
        strategy_manager.activate(cmd_data["strategy_id"])

    elif command_type == "strategy:deactivate":
        strategy_manager.deactivate(cmd_data["strategy_id"])

    elif command_type == "system:enter_hold":
        from harness.hold_mode import HoldTrigger
        hold_mode.enter(
            reason=cmd_data.get("reason", "Manual hold via dashboard"),
            trigger=HoldTrigger.MANUAL,
        )

    elif command_type == "system:exit_hold":
        hold_mode.exit(operator="manual")

    elif command_type == "breaker:reset":
        breaker_name = cmd_data["breaker_name"]
        breaker = circuit_breakers.get(breaker_name)
        if breaker is None:
            msg = f"Unknown breaker: {breaker_name}"
            raise KeyError(msg)

        # Reset the breaker using its available method
        if hasattr(breaker, "manual_restart"):
            breaker.manual_restart()
        elif hasattr(breaker, "reset"):
            breaker.reset()

        # Audit log
        db_repo.create_alert({
            "severity": "warning",
            "category": "manual_reset",
            "message": f"Circuit breaker '{breaker_name}' manually reset via dashboard",
            "data": {"breaker_name": breaker_name, "source": "manual_reset"},
        })

    else:
        msg = f"Unknown command type: {command_type}"
        raise ValueError(msg)


def _emit_ack(
    redis_client: Any,
    event_emitter_fn: Callable[..., None],
    command_id: str,
    command_type: str,
    *,
    success: bool,
    error: str | None,
) -> None:
    """Emit a command_ack event to dashboard:events."""
    try:
        event_emitter_fn(redis_client, "command_ack", {
            "command_id": command_id,
            "command_type": command_type,
            "success": success,
            "error": error,
        })
    except Exception:
        _logger.debug(
            "Failed to emit command_ack event",
            extra={"data": {"command_id": command_id}},
        )
