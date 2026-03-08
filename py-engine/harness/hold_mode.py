"""Hold mode — system behavior when Claude API is unavailable or irreconcilable state is detected.

Tracked as ``system_status: "normal" | "hold"`` in Redis.

In hold mode:
- No new positions opened, no rebalances, no harvests
- Existing positions maintained as-is
- Strategy evaluation continues (reports stay fresh for Claude's return)
- Circuit breakers remain fully active (independent of Claude)
- Decision gate stays closed regardless of actionable signals
- Auto-resume when trigger condition clears

Implements HARNESS-005 from features.json.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from monitoring.logger import get_logger

if TYPE_CHECKING:
    from redis import Redis

_logger = get_logger("hold-mode", enable_file=False)

REDIS_SYSTEM_STATUS_KEY = "system_status"
STATUS_NORMAL = "normal"
STATUS_HOLD = "hold"


class HoldTrigger(StrEnum):
    """Events that can trigger hold mode."""

    API_UNAVAILABLE = "api_unavailable"
    BUDGET_EXHAUSTED = "budget_exhausted"
    TX_FAILURE_RATE = "tx_failure_rate"
    IRRECONCILABLE_STATE = "irreconcilable_state"
    MANUAL = "manual"


@dataclass
class HoldDiagnostics:
    """Diagnostic snapshot captured when hold mode is entered."""

    timestamp: str = ""
    trigger: str = ""
    reason: str = ""
    context: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = datetime.now(UTC).isoformat()


class HoldMode:
    """Manages hold mode lifecycle via Redis ``system_status`` key.

    Args:
        redis: Redis client instance. If ``None``, falls back to in-memory
            tracking (useful for tests without Redis).
    """

    def __init__(self, *, redis: Redis | None = None) -> None:
        self._redis = redis
        self._trigger: HoldTrigger | None = None
        self._reason: str | None = None
        self._entry_time: str | None = None
        self._diagnostics: HoldDiagnostics | None = None
        # In-memory fallback when Redis is unavailable
        self._in_memory_status: str = STATUS_NORMAL

    # -----------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------

    def is_active(self) -> bool:
        """Check whether the system is currently in hold mode."""
        return self._get_status() == STATUS_HOLD

    def should_block_decisions(self) -> bool:
        """Return True when hold mode is active (decision gate must stay closed)."""
        return self.is_active()

    @property
    def trigger(self) -> HoldTrigger | None:
        """Return the trigger that caused hold mode, or None if not active."""
        return self._trigger

    @property
    def reason(self) -> str | None:
        """Return the human-readable reason for hold mode."""
        return self._reason

    @property
    def entry_time(self) -> str | None:
        """Return ISO timestamp when hold mode was entered."""
        return self._entry_time

    @property
    def diagnostics(self) -> HoldDiagnostics | None:
        """Return diagnostic snapshot from hold mode entry."""
        return self._diagnostics

    def enter(
        self,
        reason: str,
        trigger: HoldTrigger,
        *,
        context: dict[str, Any] | None = None,
    ) -> HoldDiagnostics:
        """Enter hold mode. Sets Redis ``system_status`` to ``hold``.

        Args:
            reason: Human-readable description of why hold mode was entered.
            trigger: The ``HoldTrigger`` enum value identifying the cause.
            context: Optional dictionary with additional diagnostic context.

        Returns:
            A ``HoldDiagnostics`` snapshot captured at entry time.
        """
        if self.is_active():
            _logger.warning(
                "Hold mode already active, updating trigger",
                extra={"data": {
                    "previous_trigger": self._trigger,
                    "new_trigger": trigger.value,
                    "new_reason": reason,
                }},
            )

        self._trigger = trigger
        self._reason = reason
        self._entry_time = datetime.now(UTC).isoformat()

        self._set_status(STATUS_HOLD)

        diagnostics = HoldDiagnostics(
            trigger=trigger.value,
            reason=reason,
            context=context or {},
        )
        self._diagnostics = diagnostics

        _logger.critical(
            "HOLD MODE ENTERED",
            extra={"data": {
                "trigger": trigger.value,
                "reason": reason,
                "entry_time": self._entry_time,
                "context": context or {},
            }},
        )

        return diagnostics

    def exit(self, *, operator: str = "auto") -> None:
        """Exit hold mode. Sets Redis ``system_status`` back to ``normal``.

        Args:
            operator: Who/what is exiting hold mode (e.g. ``"auto"``, ``"manual"``).
        """
        if not self.is_active():
            _logger.debug("exit() called but hold mode not active")
            return

        previous_trigger = self._trigger
        previous_reason = self._reason

        self._set_status(STATUS_NORMAL)
        self._trigger = None
        self._reason = None

        _logger.info(
            "Hold mode exited",
            extra={"data": {
                "operator": operator,
                "previous_trigger": previous_trigger,
                "previous_reason": previous_reason,
                "entry_time": self._entry_time,
                "exit_time": datetime.now(UTC).isoformat(),
            }},
        )

        self._entry_time = None
        self._diagnostics = None

    def check_auto_resume(
        self,
        *,
        api_healthy: bool = False,
        budget_available: bool = False,
        state_reconciled: bool = False,
        tx_failure_rate_ok: bool = False,
    ) -> bool:
        """Check whether the trigger condition has cleared and auto-resume if so.

        Args:
            api_healthy: True if Claude API is responding.
            budget_available: True if API budget has been reset.
            state_reconciled: True if state discrepancy has been resolved.
            tx_failure_rate_ok: True if TX failure rate is below threshold.

        Returns:
            True if hold mode was exited (auto-resumed), False otherwise.
        """
        if not self.is_active():
            return False

        should_resume = False
        trigger = self._trigger

        if trigger == HoldTrigger.API_UNAVAILABLE and api_healthy:
            should_resume = True
        elif trigger == HoldTrigger.BUDGET_EXHAUSTED and budget_available:
            should_resume = True
        elif trigger == HoldTrigger.IRRECONCILABLE_STATE and state_reconciled:
            should_resume = True
        elif trigger == HoldTrigger.TX_FAILURE_RATE and tx_failure_rate_ok:
            should_resume = True
        elif trigger == HoldTrigger.MANUAL:
            # Manual hold mode requires manual exit — never auto-resumes
            should_resume = False

        if should_resume:
            _logger.info(
                "Auto-resume triggered — hold condition cleared",
                extra={"data": {
                    "trigger": trigger,
                    "api_healthy": api_healthy,
                    "budget_available": budget_available,
                    "state_reconciled": state_reconciled,
                    "tx_failure_rate_ok": tx_failure_rate_ok,
                }},
            )
            self.exit(operator="auto")

        return should_resume

    # -----------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------

    def _get_status(self) -> str:
        """Read system_status from Redis, falling back to in-memory."""
        if self._redis is not None:
            try:
                val = self._redis.get(REDIS_SYSTEM_STATUS_KEY)
                if val is None:
                    return STATUS_NORMAL
                if isinstance(val, bytes):
                    return val.decode("utf-8")
                return str(val)
            except Exception:
                _logger.warning(
                    "Failed to read system_status from Redis, using in-memory fallback",
                )
                return self._in_memory_status
        return self._in_memory_status

    def _set_status(self, status: str) -> None:
        """Write system_status to Redis and in-memory."""
        self._in_memory_status = status
        if self._redis is not None:
            try:
                self._redis.set(REDIS_SYSTEM_STATUS_KEY, status)
            except Exception:
                _logger.warning(
                    "Failed to write system_status to Redis, in-memory only",
                )
