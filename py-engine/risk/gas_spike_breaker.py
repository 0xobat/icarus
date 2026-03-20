"""Gas spike circuit breaker — pause non-urgent operations during high gas (RISK-003).

Compares current gas price against 24h rolling average. At >3x average:
pause non-urgent operations. Urgent operations (stop-loss, emergency
withdrawals) are exempt. Queued operations execute when gas returns below
threshold.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from monitoring.logger import get_logger

_logger = get_logger("gas-spike-breaker", enable_file=False)

DEFAULT_SPIKE_MULTIPLIER = Decimal(3)

# Operation priority classification
URGENT_OPERATIONS = frozenset({
    "stop_loss",
    "emergency_withdrawal",
    "liquidation_protection",
    "close_position",
})


@dataclass
class QueuedOperation:
    """An operation deferred due to gas spike."""

    operation_id: str
    operation_type: str
    payload: dict[str, Any]
    queued_at: str
    strategy_id: str | None = None


@dataclass
class GasSpikeState:
    """Current gas spike breaker state."""

    is_active: bool
    current_gas: Decimal
    average_gas: Decimal
    multiplier: Decimal
    threshold: Decimal
    queued_count: int
    activated_at: str | None = None


class GasSpikeBreaker:
    """Gas spike circuit breaker.

    - Compares current gas vs 24h rolling average
    - At >3x average: pause non-urgent operations
    - Urgent operations (stop-loss, emergency) are exempt
    - Queued operations held until gas returns below threshold
    - Alerts on activation and deactivation
    """

    def __init__(
        self,
        *,
        spike_multiplier: Decimal = DEFAULT_SPIKE_MULTIPLIER,
    ) -> None:
        self._spike_multiplier = spike_multiplier
        self._is_active = False
        self._current_gas = Decimal(0)
        self._average_gas = Decimal(0)
        self._queue: list[QueuedOperation] = []
        self._activated_at: str | None = None
        self._alerts: list[dict[str, Any]] = []
        self._max_alerts = 1000

    def _prune_alerts(self) -> None:
        if len(self._alerts) > self._max_alerts:
            self._alerts = self._alerts[-(self._max_alerts // 2):]

    @property
    def is_active(self) -> bool:
        """Check whether the gas spike breaker is currently active."""
        return self._is_active

    @property
    def current_gas(self) -> Decimal:
        """Return the most recent gas price."""
        return self._current_gas

    @property
    def average_gas(self) -> Decimal:
        """Return the rolling average gas price."""
        return self._average_gas

    @property
    def queued_operations(self) -> list[QueuedOperation]:
        """Return a copy of all queued operations."""
        return list(self._queue)

    @property
    def alerts(self) -> list[dict[str, Any]]:
        """Return a copy of all gas spike alerts."""
        return list(self._alerts)

    def update(
        self,
        current_gas: Decimal,
        average_gas: Decimal,
    ) -> GasSpikeState:
        """Update with current and average gas prices.

        Activates/deactivates circuit breaker based on threshold.
        Returns current state.
        """
        self._current_gas = current_gas
        self._average_gas = average_gas

        if average_gas <= 0:
            return self.get_state()

        threshold = average_gas * self._spike_multiplier
        now = datetime.now(UTC).isoformat()

        if current_gas > threshold and not self._is_active:
            self._is_active = True
            self._activated_at = now
            alert = {
                "event": "gas_spike_activated",
                "current_gas": str(current_gas),
                "average_gas": str(average_gas),
                "multiplier": str(current_gas / average_gas),
                "threshold_multiplier": str(self._spike_multiplier),
                "timestamp": now,
            }
            self._alerts.append(alert)
            self._prune_alerts()
            _logger.warning(
                "Gas spike breaker ACTIVATED",
                extra={"data": alert},
            )

        elif current_gas <= threshold and self._is_active:
            self.deactivate()

        return self.get_state()

    def deactivate(self) -> None:
        """Deactivate the gas spike breaker and release all queued operations."""
        now = datetime.now(UTC).isoformat()
        alert = {
            "event": "gas_spike_deactivated",
            "current_gas": str(self._current_gas),
            "average_gas": str(self._average_gas),
            "queued_released": len(self._queue),
            "timestamp": now,
        }
        self._is_active = False
        self._alerts.append(alert)
        self._prune_alerts()
        _logger.info(
            "Gas spike breaker DEACTIVATED",
            extra={"data": alert},
        )
        self._activated_at = None
        self.release_queue()

    def is_operation_allowed(self, operation_type: str) -> bool:
        """Check if an operation is allowed given current gas conditions.

        Urgent operations are always allowed. Non-urgent operations
        are blocked when circuit breaker is active.
        """
        if not self._is_active:
            return True
        return operation_type in URGENT_OPERATIONS

    def queue_operation(
        self,
        *,
        operation_id: str,
        operation_type: str,
        payload: dict[str, Any],
        strategy_id: str | None = None,
    ) -> QueuedOperation | None:
        """Queue a non-urgent operation for later execution.

        Returns the queued operation, or None if the operation is
        urgent (and should proceed immediately).
        """
        if operation_type in URGENT_OPERATIONS:
            return None

        if not self._is_active:
            return None

        op = QueuedOperation(
            operation_id=operation_id,
            operation_type=operation_type,
            payload=payload,
            queued_at=datetime.now(UTC).isoformat(),
            strategy_id=strategy_id,
        )
        self._queue.append(op)

        _logger.info(
            "Operation queued due to gas spike",
            extra={"data": {
                "operation_id": operation_id,
                "operation_type": operation_type,
                "queue_size": len(self._queue),
            }},
        )
        return op

    def release_queue(self) -> list[QueuedOperation]:
        """Release all queued operations.

        Called when gas returns below threshold. Returns the list
        of operations that should now be executed.
        """
        released = list(self._queue)
        self._queue.clear()

        if released:
            _logger.info(
                "Queued operations released",
                extra={"data": {"count": len(released)}},
            )
        return released

    def get_state(self) -> GasSpikeState:
        """Get current circuit breaker state."""
        threshold = (
            self._average_gas * self._spike_multiplier
            if self._average_gas > 0
            else Decimal(0)
        )
        return GasSpikeState(
            is_active=self._is_active,
            current_gas=self._current_gas,
            average_gas=self._average_gas,
            multiplier=self._spike_multiplier,
            threshold=threshold,
            queued_count=len(self._queue),
            activated_at=self._activated_at,
        )
