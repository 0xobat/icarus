"""Diagnostic mode — halt trading, dump state, alert owner."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from monitoring.logger import get_logger

_logger = get_logger("diagnostic-mode", enable_file=False)


class DiagnosticTrigger(StrEnum):
    STARTUP_RECONCILIATION = "startup_reconciliation"
    CRITICAL_CIRCUIT_BREAKER = "critical_circuit_breaker"
    MANUAL_COMMAND = "manual_command"


@dataclass
class StateDump:
    """Captured state at the time diagnostic mode was entered."""

    timestamp: str = ""
    trigger: str = ""
    agent_state: dict[str, Any] = field(default_factory=dict)
    redis_stream_state: dict[str, Any] = field(default_factory=dict)
    on_chain_positions: dict[str, Any] = field(default_factory=dict)
    additional_context: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = datetime.now(UTC).isoformat()


class DiagnosticMode:
    """Manages diagnostic mode lifecycle.

    When entered:
    - All new trading operations are halted
    - Existing positions are maintained (not unwound)
    - Full state dump is logged
    - Alert is sent to owner (structured log event)
    - Manual exit required — no auto-resume
    """

    def __init__(
        self,
        *,
        state_manager: Any,
        redis_manager: Any | None = None,
    ) -> None:
        self._state_manager = state_manager
        self._redis = redis_manager
        self._active = False
        self._state_dump: StateDump | None = None
        self._entry_time: str | None = None

    @property
    def is_active(self) -> bool:
        return self._active

    @property
    def state_dump(self) -> StateDump | None:
        return self._state_dump

    @property
    def entry_time(self) -> str | None:
        return self._entry_time

    def enter(
        self,
        trigger: DiagnosticTrigger,
        *,
        additional_context: dict[str, Any] | None = None,
    ) -> StateDump:
        """Enter diagnostic mode. Halts trading and captures state.

        Raises RuntimeError if already in diagnostic mode.
        """
        if self._active:
            raise RuntimeError("Already in diagnostic mode")

        self._active = True
        self._entry_time = datetime.now(UTC).isoformat()

        # Set operational flag on state manager
        self._state_manager.set_operational_flag("diagnostic_mode", True)
        self._state_manager.set_operational_flag("trading_paused", True)

        # Capture state dump
        dump = self._capture_state(trigger, additional_context)
        self._state_dump = dump

        # Log all state dump info
        self._log_state_dump(dump)

        # Alert owner (structured log event — Discord integration is P2)
        _logger.critical(
            "DIAGNOSTIC MODE ENTERED",
            extra={"data": {
                "trigger": trigger.value,
                "entry_time": self._entry_time,
                "alert_type": "owner_notification",
                "positions_count": len(dump.agent_state.get("positions", {})),
                "additional_context": additional_context or {},
            }},
        )

        return dump

    def exit(self, *, operator: str = "manual") -> None:
        """Exit diagnostic mode. Must be manually triggered.

        Raises RuntimeError if not in diagnostic mode.
        """
        if not self._active:
            raise RuntimeError("Not in diagnostic mode")

        self._active = False

        # Clear operational flags
        self._state_manager.set_operational_flag("diagnostic_mode", False)
        self._state_manager.set_operational_flag("trading_paused", False)

        _logger.info(
            "Diagnostic mode exited",
            extra={"data": {
                "operator": operator,
                "entry_time": self._entry_time,
                "exit_time": datetime.now(UTC).isoformat(),
            }},
        )

        self._entry_time = None
        self._state_dump = None

    def should_block_trading(self) -> bool:
        """Check if trading should be blocked. Used by order pipeline."""
        return self._active

    def _capture_state(
        self,
        trigger: DiagnosticTrigger,
        additional_context: dict[str, Any] | None = None,
    ) -> StateDump:
        """Capture full state from all sources."""
        # Agent state
        agent_state: dict[str, Any] = {}
        try:
            agent_state = {
                "positions": self._state_manager.get_positions(),
                "strategy_statuses": self._state_manager.get_strategy_statuses(),
                "operational_flags": self._state_manager.get_operational_flags(),
                "last_reconciliation": self._state_manager.get_last_reconciliation(),
            }
        except Exception as exc:
            agent_state = {"error": f"Failed to read agent state: {exc}"}

        # Redis stream state
        redis_state: dict[str, Any] = {}
        if self._redis is not None:
            try:
                orders = self._redis.stream_read(
                    "execution:orders", from_id="0-0", count=100,
                )
                results = self._redis.stream_read(
                    "execution:results", from_id="0-0", count=100,
                )
                redis_state = {
                    "pending_orders": len(orders),
                    "pending_results": len(results),
                    "orders_sample": orders[:5],
                    "results_sample": results[:5],
                }
            except Exception as exc:
                redis_state = {"error": f"Failed to read Redis streams: {exc}"}

        return StateDump(
            trigger=trigger.value,
            agent_state=agent_state,
            redis_stream_state=redis_state,
            additional_context=additional_context or {},
        )

    def _log_state_dump(self, dump: StateDump) -> None:
        """Log the full state dump for debugging."""
        _logger.info(
            "State dump captured",
            extra={"data": {
                "trigger": dump.trigger,
                "timestamp": dump.timestamp,
                "agent_state": dump.agent_state,
                "redis_stream_state": dump.redis_stream_state,
                "additional_context": dump.additional_context,
            }},
        )
