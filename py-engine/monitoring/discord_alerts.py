"""Discord alert system — webhook-based notifications for critical events (MON-002).

Sends alerts to Discord via webhooks for circuit breaker triggers, position changes,
daily performance summaries, anomaly detections, and human-in-the-loop approval
requests.  Uses ``urllib.request`` so no extra dependency is needed.

When ``DISCORD_WEBHOOK_URL`` is not set the alerts are logged but not sent
(graceful degradation).
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from monitoring.logger import get_logger

_logger = get_logger("discord-alerts", enable_file=False)


# ---------------------------------------------------------------------------
# Severity levels
# ---------------------------------------------------------------------------
class AlertSeverity(StrEnum):
    """Alert severity levels mapped to Discord embed colours."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


_SEVERITY_COLORS: dict[AlertSeverity, int] = {
    AlertSeverity.INFO: 0x3498DB,       # blue
    AlertSeverity.WARNING: 0xF39C12,    # orange
    AlertSeverity.CRITICAL: 0xE74C3C,   # red
}


# ---------------------------------------------------------------------------
# Alert types
# ---------------------------------------------------------------------------
class AlertType(StrEnum):
    """Enumeration of supported alert categories."""

    CIRCUIT_BREAKER = "circuit_breaker"
    POSITION_CHANGE = "position_change"
    DAILY_SUMMARY = "daily_summary"
    ANOMALY = "anomaly"
    APPROVAL_REQUEST = "approval_request"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
@dataclass
class DiscordEmbed:
    """Represents a single Discord embed object."""

    title: str
    description: str
    color: int = 0x3498DB
    fields: list[dict[str, str | bool]] = field(default_factory=list)
    footer: str = ""
    timestamp: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize to the Discord webhook embed format."""
        payload: dict[str, Any] = {
            "title": self.title,
            "description": self.description,
            "color": self.color,
        }
        if self.fields:
            payload["fields"] = self.fields
        if self.footer:
            payload["footer"] = {"text": self.footer}
        if self.timestamp:
            payload["timestamp"] = self.timestamp
        return payload


@dataclass
class AlertResult:
    """Outcome of sending a Discord alert."""

    sent: bool
    alert_type: str
    severity: str
    message: str
    timestamp: str = ""
    error: str | None = None

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = datetime.now(UTC).isoformat()


# ---------------------------------------------------------------------------
# Discord Alert Manager
# ---------------------------------------------------------------------------
class DiscordAlertManager:
    """Sends structured alerts to Discord via webhooks.

    If ``DISCORD_WEBHOOK_URL`` is not configured, alerts are still logged
    locally so no monitoring gap occurs.
    """

    def __init__(
        self,
        *,
        webhook_url: str | None = None,
        min_severity: AlertSeverity = AlertSeverity.INFO,
    ) -> None:
        self._webhook_url = webhook_url or os.environ.get("DISCORD_WEBHOOK_URL", "")
        self._min_severity = min_severity
        self._history: list[AlertResult] = []

    @property
    def webhook_configured(self) -> bool:
        """Return whether a webhook URL is available."""
        return bool(self._webhook_url)

    @property
    def history(self) -> list[AlertResult]:
        """Return a copy of the alert history."""
        return list(self._history)

    # -- severity filtering -------------------------------------------------

    _SEVERITY_ORDER: dict[AlertSeverity, int] = {
        AlertSeverity.INFO: 0,
        AlertSeverity.WARNING: 1,
        AlertSeverity.CRITICAL: 2,
    }

    def _should_send(self, severity: AlertSeverity) -> bool:
        """Check if the severity meets the minimum threshold."""
        return self._SEVERITY_ORDER.get(
            severity, 0,
        ) >= self._SEVERITY_ORDER.get(self._min_severity, 0)

    # -- HTTP transport -----------------------------------------------------

    def _post_webhook(self, payload: dict[str, Any]) -> bool:
        """POST a JSON payload to the Discord webhook.

        Returns True on success, False on failure.
        """
        if not self._webhook_url:
            _logger.info(
                "Discord webhook not configured — alert logged only",
                extra={"data": payload},
            )
            return False

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self._webhook_url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
                return 200 <= resp.status < 300
        except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
            _logger.warning(
                "Discord webhook delivery failed",
                extra={"data": {"error": str(exc)}},
            )
            return False

    def _build_payload(self, embed: DiscordEmbed) -> dict[str, Any]:
        """Wrap an embed in a full webhook payload."""
        return {"embeds": [embed.to_dict()]}

    # -- public alert methods -----------------------------------------------

    def send_circuit_breaker_alert(
        self,
        *,
        trigger_type: str,
        threshold: str,
        action_taken: str,
        details: dict[str, Any] | None = None,
    ) -> AlertResult:
        """Send a circuit breaker trigger notification.

        Args:
            trigger_type: The type of circuit breaker that fired.
            threshold: The threshold value that was exceeded.
            action_taken: The action performed in response.
            details: Optional extra context dict.
        """
        severity = AlertSeverity.CRITICAL
        embed = DiscordEmbed(
            title="Circuit Breaker Triggered",
            description=f"**{trigger_type}** exceeded threshold",
            color=_SEVERITY_COLORS[severity],
            fields=[
                {"name": "Trigger", "value": trigger_type, "inline": True},
                {"name": "Threshold", "value": threshold, "inline": True},
                {"name": "Action", "value": action_taken, "inline": False},
            ],
            timestamp=datetime.now(UTC).isoformat(),
        )
        if details:
            for key, value in details.items():
                embed.fields.append(
                    {"name": str(key), "value": str(value), "inline": True},
                )

        return self._send(
            embed=embed,
            alert_type=AlertType.CIRCUIT_BREAKER,
            severity=severity,
            message=f"Circuit breaker: {trigger_type} — {action_taken}",
        )

    def send_position_change_alert(
        self,
        *,
        strategy: str,
        before_value: Decimal | str,
        after_value: Decimal | str,
        reasoning: str,
    ) -> AlertResult:
        """Send a large position change notification.

        Args:
            strategy: Name of the strategy.
            before_value: Position value before the change.
            after_value: Position value after the change.
            reasoning: Why the change was made.
        """
        severity = AlertSeverity.WARNING
        embed = DiscordEmbed(
            title="Position Change",
            description=f"Strategy **{strategy}** adjusted position",
            color=_SEVERITY_COLORS[severity],
            fields=[
                {"name": "Before", "value": str(before_value), "inline": True},
                {"name": "After", "value": str(after_value), "inline": True},
                {"name": "Reasoning", "value": reasoning, "inline": False},
            ],
            timestamp=datetime.now(UTC).isoformat(),
        )
        return self._send(
            embed=embed,
            alert_type=AlertType.POSITION_CHANGE,
            severity=severity,
            message=f"Position change: {strategy} {before_value} -> {after_value}",
        )

    def send_daily_summary(
        self,
        *,
        portfolio_value: Decimal | str,
        daily_pnl: Decimal | str,
        active_strategies: list[str],
        top_performer: str = "",
        bottom_performer: str = "",
    ) -> AlertResult:
        """Send a daily performance summary.

        Args:
            portfolio_value: Current total portfolio value.
            daily_pnl: Profit/loss for the day.
            active_strategies: List of currently active strategy names.
            top_performer: Best performing strategy today.
            bottom_performer: Worst performing strategy today.
        """
        severity = AlertSeverity.INFO
        strategies_text = ", ".join(active_strategies) if active_strategies else "None"
        embed = DiscordEmbed(
            title="Daily Performance Summary",
            description="End-of-day portfolio report",
            color=_SEVERITY_COLORS[severity],
            fields=[
                {
                    "name": "Portfolio Value",
                    "value": str(portfolio_value),
                    "inline": True,
                },
                {"name": "Daily P&L", "value": str(daily_pnl), "inline": True},
                {
                    "name": "Active Strategies",
                    "value": strategies_text,
                    "inline": False,
                },
            ],
            timestamp=datetime.now(UTC).isoformat(),
        )
        if top_performer:
            embed.fields.append(
                {"name": "Top Performer", "value": top_performer, "inline": True},
            )
        if bottom_performer:
            embed.fields.append(
                {"name": "Bottom Performer", "value": bottom_performer, "inline": True},
            )

        return self._send(
            embed=embed,
            alert_type=AlertType.DAILY_SUMMARY,
            severity=severity,
            message=f"Daily summary: value={portfolio_value} pnl={daily_pnl}",
        )

    def send_anomaly_alert(
        self,
        *,
        anomaly_type: str,
        severity: AlertSeverity = AlertSeverity.WARNING,
        message: str,
        data: dict[str, Any] | None = None,
    ) -> AlertResult:
        """Send an anomaly detection alert.

        Args:
            anomaly_type: Category of anomaly detected.
            severity: How severe the anomaly is.
            message: Human-readable description.
            data: Additional context data.
        """
        embed = DiscordEmbed(
            title=f"Anomaly Detected: {anomaly_type}",
            description=message,
            color=_SEVERITY_COLORS[severity],
            timestamp=datetime.now(UTC).isoformat(),
        )
        if data:
            for key, value in data.items():
                embed.fields.append(
                    {"name": str(key), "value": str(value), "inline": True},
                )

        return self._send(
            embed=embed,
            alert_type=AlertType.ANOMALY,
            severity=severity,
            message=f"Anomaly: {anomaly_type} — {message}",
        )

    def send_approval_request(
        self,
        *,
        action_description: str,
        amounts: str,
        risk_context: str,
        estimated_impact: str,
        approval_id: str,
    ) -> AlertResult:
        """Send a human-in-the-loop approval request with action buttons.

        Args:
            action_description: What action requires approval.
            amounts: The amounts involved.
            risk_context: Risk assessment of the action.
            estimated_impact: Expected portfolio impact.
            approval_id: Unique identifier for tracking the approval.
        """
        severity = AlertSeverity.WARNING
        embed = DiscordEmbed(
            title="Approval Required",
            description=action_description,
            color=_SEVERITY_COLORS[severity],
            fields=[
                {"name": "Amounts", "value": amounts, "inline": True},
                {"name": "Risk Context", "value": risk_context, "inline": True},
                {"name": "Estimated Impact", "value": estimated_impact, "inline": False},
                {"name": "Approval ID", "value": approval_id, "inline": True},
                {
                    "name": "Actions",
                    "value": "React with :white_check_mark: to approve or :x: to reject",
                    "inline": False,
                },
            ],
            timestamp=datetime.now(UTC).isoformat(),
        )
        return self._send(
            embed=embed,
            alert_type=AlertType.APPROVAL_REQUEST,
            severity=severity,
            message=f"Approval request: {action_description} (id={approval_id})",
        )

    # -- internal helpers ---------------------------------------------------

    def _send(
        self,
        *,
        embed: DiscordEmbed,
        alert_type: AlertType,
        severity: AlertSeverity,
        message: str,
    ) -> AlertResult:
        """Build, filter, log, and send an alert.

        Returns:
            An ``AlertResult`` describing the outcome.
        """
        now = datetime.now(UTC).isoformat()

        # Always log regardless of send
        _logger.info(
            message,
            extra={
                "data": {
                    "alert_type": alert_type.value,
                    "severity": severity.value,
                },
            },
        )

        if not self._should_send(severity):
            result = AlertResult(
                sent=False,
                alert_type=alert_type.value,
                severity=severity.value,
                message=message,
                timestamp=now,
                error="Below minimum severity threshold",
            )
            self._history.append(result)
            return result

        payload = self._build_payload(embed)
        sent = self._post_webhook(payload)

        result = AlertResult(
            sent=sent,
            alert_type=alert_type.value,
            severity=severity.value,
            message=message,
            timestamp=now,
            error=None if sent else "Webhook not configured or delivery failed",
        )
        self._history.append(result)
        return result
