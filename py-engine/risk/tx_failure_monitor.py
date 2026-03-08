"""TX failure rate monitor — rolling failure detection with hold mode (RISK-004).

Counts failed transactions in a rolling 1-hour window. At >3 failures/hour:
pause execution, enter hold mode via ``HoldTrigger.TX_FAILURE_RATE``.
Auto-clears when failure rate drops below threshold.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from monitoring.logger import get_logger

if TYPE_CHECKING:
    from harness.hold_mode import HoldMode

_logger = get_logger("tx-failure-monitor", enable_file=False)

DEFAULT_WINDOW_SECONDS = 3600  # 1 hour
DEFAULT_FAILURE_THRESHOLD = 3

# Failure categories
PARAMETER_ERRORS = frozenset({"revert", "out_of_gas", "nonce_issue"})
SYSTEMIC_ERRORS = frozenset({"timeout", "network_error", "rpc_error"})
ALL_FAILURE_REASONS = PARAMETER_ERRORS | SYSTEMIC_ERRORS


@dataclass
class TxFailure:
    """A recorded transaction failure."""

    tx_id: str
    reason: str
    category: str  # "parameter" or "systemic"
    details: str
    timestamp: str
    strategy_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return dictionary representation."""
        return {
            "tx_id": self.tx_id,
            "reason": self.reason,
            "category": self.category,
            "details": self.details,
            "timestamp": self.timestamp,
            "strategy_id": self.strategy_id,
        }


@dataclass
class MonitorState:
    """Current monitor state snapshot."""

    is_paused: bool
    diagnostic_mode: bool
    failures_in_window: int
    threshold: int
    window_seconds: int
    failure_breakdown: dict[str, int]
    last_failure: str | None = None


class TxFailureMonitor:
    """TX failure rate monitor with hold mode integration.

    - Tracks failed transactions in rolling 1-hour window
    - At >3 failures/hour: pause all execution, enter hold mode
    - Failures categorized: revert, out of gas, timeout, nonce issue
    - Auto-clears when failure rate drops below threshold
    - Distinguishes parameter errors vs systemic failures
    """

    def __init__(
        self,
        *,
        window_seconds: int = DEFAULT_WINDOW_SECONDS,
        failure_threshold: int = DEFAULT_FAILURE_THRESHOLD,
        hold_mode: HoldMode | None = None,
    ) -> None:
        self._window_seconds = window_seconds
        self._failure_threshold = failure_threshold
        self._hold_mode = hold_mode
        self._failures: list[TxFailure] = []
        self._is_paused = False
        self._diagnostic_mode = False
        self._alerts: list[dict[str, Any]] = []

    @property
    def is_paused(self) -> bool:
        """Check whether execution is paused due to failure threshold."""
        return self._is_paused

    @property
    def diagnostic_mode(self) -> bool:
        """Check whether diagnostic mode is active."""
        return self._diagnostic_mode

    @property
    def alerts(self) -> list[dict[str, Any]]:
        """Return a copy of all failure threshold alerts."""
        return list(self._alerts)

    def is_triggered(self, now: datetime | None = None) -> bool:
        """Check whether the failure rate exceeds the threshold.

        Returns:
            True if failures in the rolling window exceed the threshold.
        """
        self._prune_old_failures(now)
        return len(self._failures) > self._failure_threshold

    def _classify_failure(self, reason: str) -> str:
        """Classify a failure reason as parameter or systemic."""
        if reason in PARAMETER_ERRORS:
            return "parameter"
        if reason in SYSTEMIC_ERRORS:
            return "systemic"
        return "unknown"

    def _prune_old_failures(self, now: datetime | None = None) -> None:
        """Remove failures outside the rolling window."""
        current = now or datetime.now(UTC)
        cutoff = current - timedelta(seconds=self._window_seconds)
        self._failures = [
            f for f in self._failures
            if datetime.fromisoformat(f.timestamp) > cutoff
        ]

    def record_failure(
        self,
        *,
        tx_id: str,
        reason: str,
        details: str = "",
        strategy_id: str | None = None,
        now: datetime | None = None,
    ) -> TxFailure:
        """Record a transaction failure.

        If the rolling failure count exceeds the threshold, pauses
        all execution and enters diagnostic mode.
        """
        current = now or datetime.now(UTC)
        category = self._classify_failure(reason)

        failure = TxFailure(
            tx_id=tx_id,
            reason=reason,
            category=category,
            details=details,
            timestamp=current.isoformat(),
            strategy_id=strategy_id,
        )
        self._failures.append(failure)

        _logger.warning(
            "Transaction failure recorded",
            extra={"data": failure.to_dict()},
        )

        # Prune old failures and check threshold
        self._prune_old_failures(current)
        count = len(self._failures)

        if count > self._failure_threshold and not self._is_paused:
            self._is_paused = True
            self._diagnostic_mode = True
            alert = {
                "event": "tx_failure_threshold_breached",
                "failures_in_window": count,
                "threshold": self._failure_threshold,
                "breakdown": self._get_breakdown(),
                "timestamp": current.isoformat(),
            }
            self._alerts.append(alert)
            _logger.critical(
                "TX failure threshold breached — execution PAUSED",
                extra={"data": alert},
            )
            self._enter_hold_mode(count)

        return failure

    def record_success(self, tx_id: str, *, now: datetime | None = None) -> None:
        """Record a successful transaction.

        After recording, checks whether the failure rate has dropped
        below the threshold and auto-clears if so.
        """
        _logger.debug(
            "Transaction success recorded",
            extra={"data": {"tx_id": tx_id}},
        )
        self._check_auto_clear(now)

    def get_failures_in_window(
        self, now: datetime | None = None,
    ) -> list[TxFailure]:
        """Get all failures within the rolling window."""
        self._prune_old_failures(now)
        return list(self._failures)

    def get_failure_count(
        self, now: datetime | None = None,
    ) -> int:
        """Count failures in the rolling window."""
        self._prune_old_failures(now)
        return len(self._failures)

    def _get_breakdown(self) -> dict[str, int]:
        """Failure count by reason."""
        breakdown: dict[str, int] = {}
        for f in self._failures:
            breakdown[f.reason] = breakdown.get(f.reason, 0) + 1
        return breakdown

    def get_category_breakdown(
        self, now: datetime | None = None,
    ) -> dict[str, int]:
        """Failure count by category (parameter vs systemic)."""
        self._prune_old_failures(now)
        breakdown: dict[str, int] = {}
        for f in self._failures:
            breakdown[f.category] = breakdown.get(f.category, 0) + 1
        return breakdown

    def _enter_hold_mode(self, failure_count: int) -> None:
        """Enter hold mode via HoldTrigger.TX_FAILURE_RATE if hold_mode is set."""
        if self._hold_mode is None:
            return
        from harness.hold_mode import HoldTrigger
        self._hold_mode.enter(
            reason=(
                f"TX failure rate exceeded: {failure_count} failures "
                f"in window (threshold: {self._failure_threshold})"
            ),
            trigger=HoldTrigger.TX_FAILURE_RATE,
            context={
                "failures_in_window": failure_count,
                "threshold": self._failure_threshold,
                "breakdown": self._get_breakdown(),
            },
        )

    def _check_auto_clear(self, now: datetime | None = None) -> None:
        """Auto-clear pause when failure rate drops below threshold."""
        if not self._is_paused:
            return
        self._prune_old_failures(now)
        if len(self._failures) <= self._failure_threshold:
            _logger.info(
                "TX failure rate dropped below threshold — auto-clearing",
                extra={"data": {
                    "failures_in_window": len(self._failures),
                    "threshold": self._failure_threshold,
                }},
            )
            self._is_paused = False
            self._diagnostic_mode = False
            if self._hold_mode is not None:
                self._hold_mode.check_auto_resume(tx_failure_rate_ok=True)

    def manual_resume(self) -> bool:
        """Manually resume after investigation.

        This is the only way to resume execution after a failure
        threshold breach. Clears diagnostic mode and pause state.
        Returns False if not currently paused.
        """
        if not self._is_paused:
            return False

        _logger.info(
            "Manual resume — clearing diagnostic mode",
            extra={"data": {
                "failures_at_resume": len(self._failures),
            }},
        )
        self._is_paused = False
        self._diagnostic_mode = False
        return True

    def can_execute(self) -> bool:
        """Check if transaction execution is allowed."""
        return not self._is_paused

    def get_state(self, now: datetime | None = None) -> MonitorState:
        """Get current monitor state snapshot."""
        self._prune_old_failures(now)
        last = self._failures[-1].timestamp if self._failures else None
        return MonitorState(
            is_paused=self._is_paused,
            diagnostic_mode=self._diagnostic_mode,
            failures_in_window=len(self._failures),
            threshold=self._failure_threshold,
            window_seconds=self._window_seconds,
            failure_breakdown=self._get_breakdown(),
            last_failure=last,
        )
