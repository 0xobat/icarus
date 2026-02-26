"""Anomaly detection — automated detection of unusual system behaviour (MON-004).

Detects unexpected wallet balance changes, gas spend anomalies, strategy
performance degradation, protocol health issues, and execution deviations.
Each check returns an ``Anomaly`` dataclass with type, severity, message, and
data.  Cooldown tracking prevents alert fatigue by suppressing duplicate
anomalies within a configurable window.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Any

from monitoring.logger import get_logger

_logger = get_logger("anomaly-detection", enable_file=False)


# ---------------------------------------------------------------------------
# Types & data classes
# ---------------------------------------------------------------------------
class AnomalyType(StrEnum):
    """Categories of detectable anomalies."""

    BALANCE_ANOMALY = "balance_anomaly"
    GAS_ANOMALY = "gas_anomaly"
    EXECUTION_DEVIATION = "execution_deviation"
    PERFORMANCE_DEGRADATION = "performance_degradation"
    PROTOCOL_HEALTH = "protocol_health"


class AnomalySeverity(StrEnum):
    """Severity classification for anomalies."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass
class Anomaly:
    """Result of a single anomaly check."""

    anomaly_type: str
    severity: str
    message: str
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: float = 0.0

    def __post_init__(self) -> None:
        if self.timestamp == 0.0:
            self.timestamp = time.time()


# ---------------------------------------------------------------------------
# Anomaly Detector
# ---------------------------------------------------------------------------
class AnomalyDetector:
    """Stateful anomaly detector with per-type cooldown tracking.

    Cooldowns prevent the same type of anomaly from being re-reported within
    ``cooldown_seconds`` (default 300 s / 5 min), keeping the false-positive
    rate manageable and avoiding alert fatigue.
    """

    def __init__(self, *, cooldown_seconds: float = 300.0) -> None:
        self._cooldown_seconds = cooldown_seconds
        self._last_fired: dict[str, float] = {}
        self._anomalies: list[Anomaly] = []

    @property
    def anomalies(self) -> list[Anomaly]:
        """Return a copy of all recorded anomalies."""
        return list(self._anomalies)

    def clear_history(self) -> None:
        """Clear the anomaly history and cooldown state."""
        self._anomalies.clear()
        self._last_fired.clear()

    # -- cooldown helpers ---------------------------------------------------

    def _in_cooldown(self, anomaly_type: str) -> bool:
        """Check if the given anomaly type is within its cooldown window."""
        last = self._last_fired.get(anomaly_type)
        if last is None:
            return False
        return (time.time() - last) < self._cooldown_seconds

    def _record(self, anomaly: Anomaly) -> Anomaly:
        """Record an anomaly and update cooldown timer."""
        self._last_fired[anomaly.anomaly_type] = anomaly.timestamp
        self._anomalies.append(anomaly)
        _logger.warning(
            anomaly.message,
            extra={
                "data": {
                    "anomaly_type": anomaly.anomaly_type,
                    "severity": anomaly.severity,
                    **anomaly.data,
                },
            },
        )
        return anomaly

    # -- detection methods --------------------------------------------------

    def check_balance_anomaly(
        self,
        expected: Decimal,
        actual: Decimal,
        *,
        threshold_pct: float = 5.0,
    ) -> Anomaly | None:
        """Detect unexpected wallet balance changes.

        Args:
            expected: The expected balance.
            actual: The observed balance.
            threshold_pct: Percentage deviation to flag (default 5%).

        Returns:
            An ``Anomaly`` if the deviation exceeds the threshold, else ``None``.
        """
        if self._in_cooldown(AnomalyType.BALANCE_ANOMALY):
            return None

        if expected == Decimal(0):
            if actual != Decimal(0):
                deviation_pct = Decimal(100)
            else:
                return None
        else:
            deviation_pct = abs(actual - expected) / abs(expected) * Decimal(100)

        if deviation_pct < Decimal(str(threshold_pct)):
            return None

        severity = (
            AnomalySeverity.CRITICAL
            if deviation_pct >= Decimal(20)
            else AnomalySeverity.WARNING
        )
        anomaly = Anomaly(
            anomaly_type=AnomalyType.BALANCE_ANOMALY,
            severity=severity,
            message=(
                f"Balance anomaly: expected {expected}, got {actual} "
                f"({deviation_pct:.1f}% deviation)"
            ),
            data={
                "expected": str(expected),
                "actual": str(actual),
                "deviation_pct": str(deviation_pct),
            },
        )
        return self._record(anomaly)

    def check_gas_anomaly(
        self,
        actual_gas: Decimal,
        estimated_gas: Decimal,
        *,
        threshold_pct: float = 50.0,
    ) -> Anomaly | None:
        """Detect gas spend anomalies.

        Args:
            actual_gas: The gas actually consumed.
            estimated_gas: The gas that was estimated.
            threshold_pct: Percentage over-spend to flag (default 50%).

        Returns:
            An ``Anomaly`` if gas is significantly higher than estimated, else ``None``.
        """
        if self._in_cooldown(AnomalyType.GAS_ANOMALY):
            return None

        if estimated_gas <= Decimal(0):
            return None

        overspend_pct = (actual_gas - estimated_gas) / estimated_gas * Decimal(100)

        if overspend_pct < Decimal(str(threshold_pct)):
            return None

        severity = (
            AnomalySeverity.CRITICAL
            if overspend_pct >= Decimal(200)
            else AnomalySeverity.WARNING
        )
        anomaly = Anomaly(
            anomaly_type=AnomalyType.GAS_ANOMALY,
            severity=severity,
            message=(
                f"Gas anomaly: estimated {estimated_gas}, actual {actual_gas} "
                f"({overspend_pct:.1f}% over)"
            ),
            data={
                "estimated_gas": str(estimated_gas),
                "actual_gas": str(actual_gas),
                "overspend_pct": str(overspend_pct),
            },
        )
        return self._record(anomaly)

    def check_execution_deviation(
        self,
        expected_price: Decimal,
        actual_price: Decimal,
        *,
        threshold_bps: int = 100,
    ) -> Anomaly | None:
        """Detect when actual execution deviates significantly from expected.

        Args:
            expected_price: The expected execution price.
            actual_price: The actual execution price.
            threshold_bps: Basis-point deviation to flag (default 100 bps = 1%).

        Returns:
            An ``Anomaly`` if deviation exceeds threshold, else ``None``.
        """
        if self._in_cooldown(AnomalyType.EXECUTION_DEVIATION):
            return None

        if expected_price <= Decimal(0):
            return None

        deviation_bps = (
            abs(actual_price - expected_price) / expected_price * Decimal(10000)
        )

        if deviation_bps < Decimal(str(threshold_bps)):
            return None

        severity = (
            AnomalySeverity.CRITICAL
            if deviation_bps >= Decimal(500)
            else AnomalySeverity.WARNING
        )
        anomaly = Anomaly(
            anomaly_type=AnomalyType.EXECUTION_DEVIATION,
            severity=severity,
            message=(
                f"Execution deviation: expected {expected_price}, got {actual_price} "
                f"({deviation_bps:.0f} bps)"
            ),
            data={
                "expected_price": str(expected_price),
                "actual_price": str(actual_price),
                "deviation_bps": str(deviation_bps),
            },
        )
        return self._record(anomaly)

    def check_performance_degradation(
        self,
        strategy: str,
        recent_returns: list[Decimal],
        *,
        lookback: int = 7,
    ) -> Anomaly | None:
        """Detect strategy performance degradation.

        Flags when a strategy's average recent return is negative and the
        majority of recent periods show losses.

        Args:
            strategy: Name of the strategy.
            recent_returns: List of recent period returns (newest last).
            lookback: Number of periods to evaluate (default 7).

        Returns:
            An ``Anomaly`` if the strategy shows degradation, else ``None``.
        """
        if self._in_cooldown(AnomalyType.PERFORMANCE_DEGRADATION):
            return None

        window = recent_returns[-lookback:] if len(recent_returns) > lookback else recent_returns
        if not window:
            return None

        avg_return = sum(window) / Decimal(len(window))
        loss_count = sum(1 for r in window if r < Decimal(0))
        loss_ratio = Decimal(loss_count) / Decimal(len(window))

        # Flag when average is negative AND majority of periods are losses
        if avg_return >= Decimal(0) or loss_ratio < Decimal("0.5"):
            return None

        severity = (
            AnomalySeverity.CRITICAL
            if loss_ratio >= Decimal("0.8")
            else AnomalySeverity.WARNING
        )
        anomaly = Anomaly(
            anomaly_type=AnomalyType.PERFORMANCE_DEGRADATION,
            severity=severity,
            message=(
                f"Performance degradation: {strategy} avg return {avg_return:.4f} "
                f"over {len(window)} periods ({loss_count}/{len(window)} losses)"
            ),
            data={
                "strategy": strategy,
                "avg_return": str(avg_return),
                "loss_count": loss_count,
                "window_size": len(window),
                "loss_ratio": str(loss_ratio),
            },
        )
        return self._record(anomaly)

    def check_protocol_health(
        self,
        protocol_metrics: dict[str, Any],
    ) -> Anomaly | None:
        """Monitor protocol health signals.

        Checks for TVL drops and utilization spikes based on the metrics
        provided.

        Args:
            protocol_metrics: Dict with keys like ``protocol``, ``tvl_change_pct``,
                ``utilization_pct``, and optional ``governance_proposals``.

        Returns:
            An ``Anomaly`` if protocol health signals are concerning, else ``None``.
        """
        if self._in_cooldown(AnomalyType.PROTOCOL_HEALTH):
            return None

        protocol = protocol_metrics.get("protocol", "unknown")
        tvl_change = Decimal(str(protocol_metrics.get("tvl_change_pct", 0)))
        utilization = Decimal(str(protocol_metrics.get("utilization_pct", 0)))
        governance = protocol_metrics.get("governance_proposals", 0)

        issues: list[str] = []

        # TVL drop > 30% is a red flag (per PRD circuit breaker spec)
        if tvl_change < Decimal("-30"):
            issues.append(f"TVL drop {tvl_change}%")

        # Utilization > 90% signals potential liquidity crunch
        if utilization > Decimal("90"):
            issues.append(f"utilization at {utilization}%")

        # Active governance proposals may signal protocol changes
        if governance and int(governance) > 0:
            issues.append(f"{governance} active governance proposal(s)")

        if not issues:
            return None

        # TVL drop alone is critical; otherwise warning
        severity = (
            AnomalySeverity.CRITICAL
            if tvl_change < Decimal("-30")
            else AnomalySeverity.WARNING
        )
        anomaly = Anomaly(
            anomaly_type=AnomalyType.PROTOCOL_HEALTH,
            severity=severity,
            message=f"Protocol health concern for {protocol}: {'; '.join(issues)}",
            data={
                "protocol": protocol,
                "tvl_change_pct": str(tvl_change),
                "utilization_pct": str(utilization),
                "governance_proposals": governance,
                "issues": issues,
            },
        )
        return self._record(anomaly)
