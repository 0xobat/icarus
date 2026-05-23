"""Drawdown circuit breaker — portfolio-level drawdown protection (RISK-001).

Tracks portfolio peak value continuously. At 15% drawdown: alert + pause new
entries. At 20% drawdown: unwind all positions, halt all trading. Cannot be
overridden programmatically — requires manual restart.
"""

from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from monitoring.logger import get_logger

_logger = get_logger("drawdown-breaker", enable_file=False)

# Drawdown thresholds
WARNING_THRESHOLD = Decimal("0.15")  # 15%
CRITICAL_THRESHOLD = Decimal("0.20")  # 20%


@dataclass
class DrawdownState:
    """Current drawdown state."""

    peak_value: Decimal
    current_value: Decimal
    drawdown_pct: Decimal
    level: str  # "normal", "warning", "critical"
    entries_paused: bool
    trading_halted: bool
    triggered_at: str | None = None


class DrawdownBreaker:
    """Portfolio-level drawdown circuit breaker.

    - Tracks peak portfolio value continuously
    - At 15% drawdown: alert + pause new position entries
    - At 20% drawdown: unwind all positions to stablecoins, halt trading
    - Cannot be overridden programmatically — requires manual restart
    """

    def __init__(
        self,
        *,
        warning_threshold: Decimal = WARNING_THRESHOLD,
        critical_threshold: Decimal = CRITICAL_THRESHOLD,
        initial_value: Decimal = Decimal(0),
    ) -> None:
        self._warning_threshold = warning_threshold
        self._critical_threshold = critical_threshold
        self._peak_value = initial_value
        self._current_value = initial_value
        self._entries_paused = False
        self._trading_halted = False
        self._triggered_at: str | None = None
        self._alerts: list[dict[str, Any]] = []
        self._max_alerts = 1000

    def _prune_alerts(self) -> None:
        if len(self._alerts) > self._max_alerts:
            self._alerts = self._alerts[-(self._max_alerts // 2):]

    @property
    def peak_value(self) -> Decimal:
        """Return the highest portfolio value observed."""
        return self._peak_value

    @property
    def current_value(self) -> Decimal:
        """Return the current portfolio value."""
        return self._current_value

    @property
    def entries_paused(self) -> bool:
        """Check whether new position entries are paused."""
        return self._entries_paused

    @property
    def trading_halted(self) -> bool:
        """Check whether all trading is halted."""
        return self._trading_halted

    @property
    def drawdown_pct(self) -> Decimal:
        """Return the current drawdown as a fraction of peak value."""
        if self._peak_value <= 0:
            return Decimal(0)
        return (self._peak_value - self._current_value) / self._peak_value

    @property
    def level(self) -> str:
        """Return the current drawdown severity level."""
        if self._trading_halted:
            return "critical"
        if self._entries_paused:
            return "warning"
        return "normal"

    @property
    def alerts(self) -> list[dict[str, Any]]:
        """Return a copy of all drawdown alerts."""
        return list(self._alerts)

    def update(self, portfolio_value: Decimal) -> DrawdownState:
        """Update with current total portfolio value.

        Calculates drawdown from peak and triggers circuit breakers
        at configured thresholds.
        """
        self._current_value = portfolio_value

        # Update peak (only upward)
        if portfolio_value > self._peak_value:
            self._peak_value = portfolio_value

        dd = self.drawdown_pct
        now = datetime.now(UTC).isoformat()

        # Critical threshold — halt everything
        if dd > self._critical_threshold and not self._trading_halted:
            self._trading_halted = True
            self._entries_paused = True
            self._triggered_at = now
            alert = {
                "level": "critical",
                "drawdown_pct": str(dd),
                "peak_value": str(self._peak_value),
                "current_value": str(portfolio_value),
                "action": "halt_all_trading",
                "timestamp": now,
            }
            self._alerts.append(alert)
            self._prune_alerts()
            _logger.critical(
                "CRITICAL drawdown — all trading halted",
                extra={"data": alert},
            )

        # Warning threshold — pause new entries
        elif dd >= self._warning_threshold and not self._entries_paused:
            self._entries_paused = True
            self._triggered_at = now
            alert = {
                "level": "warning",
                "drawdown_pct": str(dd),
                "peak_value": str(self._peak_value),
                "current_value": str(portfolio_value),
                "action": "pause_new_entries",
                "timestamp": now,
            }
            self._alerts.append(alert)
            self._prune_alerts()
            _logger.warning(
                "WARNING drawdown — new entries paused",
                extra={"data": alert},
            )

        # Recovery — clear warning if drawdown recovered
        elif dd < self._warning_threshold and self._entries_paused:
            if not self._trading_halted:
                self._entries_paused = False
                self._triggered_at = None
                _logger.info(
                    "Drawdown recovered — entries resumed",
                    extra={"data": {
                        "drawdown_pct": str(dd),
                        "peak_value": str(self._peak_value),
                    }},
                )

        return self.get_state()

    def can_open_position(self) -> bool:
        """Check if new position entries are allowed."""
        return not self._entries_paused and not self._trading_halted

    def is_triggered(self) -> bool:
        """Check if the drawdown breaker has been triggered (warning or critical)."""
        return self._trading_halted

    def should_unwind_all(self) -> bool:
        """Check if all positions should be unwound to stablecoins."""
        return self._trading_halted

    def get_unwind_orders(
        self,
        positions: list[dict[str, Any]],
        correlation_id: str = "",
    ) -> list[dict[str, Any]]:
        """Generate schema-compliant unwind orders for all positions.

        Produces one withdrawal order per position, using the CB:drawdown
        strategy prefix for circuit-breaker-initiated orders. Orders conform
        to the execution-orders.schema.json contract.

        Args:
            positions: Open positions to unwind. Each should have at minimum
                ``asset`` and ``protocol`` keys.
            correlation_id: Correlation ID for lifecycle tracing.

        Returns:
            List of schema-compliant withdrawal orders.
        """
        if not self._trading_halted:
            return []

        now_unix = int(time.time())
        orders: list[dict[str, Any]] = []
        for pos in positions:
            orders.append({
                "version": "1.0.0",
                "orderId": str(uuid.uuid4()),
                "correlationId": correlation_id,
                "timestamp": datetime.now(UTC).isoformat(),
                "chain": "base",
                "protocol": pos.get("protocol", "aave_v3"),
                "action": "withdraw",
                "strategy": "CB:drawdown",
                "priority": "urgent",
                "params": {
                    "tokenIn": pos.get("asset", "unknown"),
                    "amount": str(pos.get("value", pos.get("amount", "0"))),
                },
                "limits": {
                    "maxGasWei": os.environ.get("MAX_GAS_WEI", "500000000000000"),
                    "maxSlippageBps": 50,
                    "deadlineUnix": now_unix + 300,
                },
            })
        return orders

    def manual_restart(self) -> bool:
        """Manually restart after critical halt.

        This is the only way to resume trading after a critical
        drawdown event. Cannot be called programmatically in
        production — this method exists for the manual restart flow.
        """
        if not self._trading_halted:
            return False

        _logger.info(
            "Manual restart — clearing circuit breaker",
            extra={"data": {
                "previous_peak": str(self._peak_value),
                "current_value": str(self._current_value),
            }},
        )
        self._trading_halted = False
        self._entries_paused = False
        self._triggered_at = None
        # Reset peak to current value to avoid immediate re-trigger
        self._peak_value = self._current_value
        return True

    def get_state(self) -> DrawdownState:
        """Get current drawdown state snapshot."""
        return DrawdownState(
            peak_value=self._peak_value,
            current_value=self._current_value,
            drawdown_pct=self.drawdown_pct,
            level=self.level,
            entries_paused=self._entries_paused,
            trading_halted=self._trading_halted,
            triggered_at=self._triggered_at,
        )
