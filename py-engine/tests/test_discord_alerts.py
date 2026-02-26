"""Tests for Discord alert system — MON-002."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock, patch

from monitoring.discord_alerts import (
    AlertResult,
    AlertSeverity,
    AlertType,
    DiscordAlertManager,
    DiscordEmbed,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _make_manager(**kwargs) -> DiscordAlertManager:
    return DiscordAlertManager(**kwargs)


def _make_manager_with_webhook(**kwargs) -> DiscordAlertManager:
    return DiscordAlertManager(webhook_url="https://discord.com/api/webhooks/test", **kwargs)


# ---------------------------------------------------------------------------
# DiscordEmbed
# ---------------------------------------------------------------------------
class TestDiscordEmbed:

    def test_to_dict_minimal(self) -> None:
        embed = DiscordEmbed(title="Test", description="Desc", color=0xFF0000)
        d = embed.to_dict()
        assert d["title"] == "Test"
        assert d["description"] == "Desc"
        assert d["color"] == 0xFF0000
        assert "fields" not in d
        assert "footer" not in d

    def test_to_dict_with_fields(self) -> None:
        embed = DiscordEmbed(
            title="T",
            description="D",
            fields=[{"name": "F1", "value": "V1", "inline": True}],
        )
        d = embed.to_dict()
        assert len(d["fields"]) == 1
        assert d["fields"][0]["name"] == "F1"

    def test_to_dict_with_footer(self) -> None:
        embed = DiscordEmbed(title="T", description="D", footer="foot")
        d = embed.to_dict()
        assert d["footer"]["text"] == "foot"

    def test_to_dict_with_timestamp(self) -> None:
        embed = DiscordEmbed(title="T", description="D", timestamp="2026-01-01T00:00:00Z")
        d = embed.to_dict()
        assert d["timestamp"] == "2026-01-01T00:00:00Z"


# ---------------------------------------------------------------------------
# AlertResult
# ---------------------------------------------------------------------------
class TestAlertResult:

    def test_auto_timestamp(self) -> None:
        r = AlertResult(sent=True, alert_type="test", severity="info", message="msg")
        assert r.timestamp != ""

    def test_explicit_timestamp(self) -> None:
        r = AlertResult(
            sent=True, alert_type="test", severity="info",
            message="msg", timestamp="fixed",
        )
        assert r.timestamp == "fixed"


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
class TestConfiguration:

    def test_no_webhook_configured(self) -> None:
        mgr = _make_manager()
        assert not mgr.webhook_configured

    def test_webhook_via_constructor(self) -> None:
        mgr = _make_manager_with_webhook()
        assert mgr.webhook_configured

    def test_webhook_via_env(self) -> None:
        with patch.dict("os.environ", {"DISCORD_WEBHOOK_URL": "https://example.com/webhook"}):
            mgr = _make_manager()
            assert mgr.webhook_configured

    def test_empty_history_at_start(self) -> None:
        mgr = _make_manager()
        assert mgr.history == []


# ---------------------------------------------------------------------------
# Severity filtering
# ---------------------------------------------------------------------------
class TestSeverityFiltering:

    def test_info_passes_info_threshold(self) -> None:
        mgr = _make_manager(min_severity=AlertSeverity.INFO)
        assert mgr._should_send(AlertSeverity.INFO)

    def test_info_blocked_by_warning_threshold(self) -> None:
        mgr = _make_manager(min_severity=AlertSeverity.WARNING)
        assert not mgr._should_send(AlertSeverity.INFO)

    def test_critical_passes_warning_threshold(self) -> None:
        mgr = _make_manager(min_severity=AlertSeverity.WARNING)
        assert mgr._should_send(AlertSeverity.CRITICAL)

    def test_warning_blocked_by_critical_threshold(self) -> None:
        mgr = _make_manager(min_severity=AlertSeverity.CRITICAL)
        assert not mgr._should_send(AlertSeverity.WARNING)


# ---------------------------------------------------------------------------
# Circuit breaker alerts
# ---------------------------------------------------------------------------
class TestCircuitBreakerAlert:

    def test_sends_with_correct_type(self) -> None:
        mgr = _make_manager()
        result = mgr.send_circuit_breaker_alert(
            trigger_type="drawdown",
            threshold="20%",
            action_taken="halt_all",
        )
        assert result.alert_type == AlertType.CIRCUIT_BREAKER
        assert result.severity == AlertSeverity.CRITICAL

    def test_alert_logged_without_webhook(self) -> None:
        mgr = _make_manager()
        result = mgr.send_circuit_breaker_alert(
            trigger_type="drawdown",
            threshold="20%",
            action_taken="halt_all",
        )
        assert not result.sent
        assert len(mgr.history) == 1

    @patch("monitoring.discord_alerts.urllib.request.urlopen")
    def test_sends_via_webhook(self, mock_urlopen) -> None:
        mock_response = MagicMock()
        mock_response.status = 204
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        mgr = _make_manager_with_webhook()
        result = mgr.send_circuit_breaker_alert(
            trigger_type="drawdown",
            threshold="20%",
            action_taken="halt_all",
        )
        assert result.sent
        mock_urlopen.assert_called_once()

    def test_with_extra_details(self) -> None:
        mgr = _make_manager()
        result = mgr.send_circuit_breaker_alert(
            trigger_type="gas_spike",
            threshold="3x",
            action_taken="pause_non_urgent",
            details={"current_gas": "150 gwei"},
        )
        assert "gas_spike" in result.message

    def test_filtered_by_severity(self) -> None:
        # Circuit breaker is CRITICAL severity; should still be sent even with CRITICAL filter
        mgr = _make_manager(min_severity=AlertSeverity.CRITICAL)
        result = mgr.send_circuit_breaker_alert(
            trigger_type="test",
            threshold="x",
            action_taken="y",
        )
        # Not sent because no webhook, but it passed severity filter
        assert result.error == "Webhook not configured or delivery failed"


# ---------------------------------------------------------------------------
# Position change alerts
# ---------------------------------------------------------------------------
class TestPositionChangeAlert:

    def test_sends_with_correct_type(self) -> None:
        mgr = _make_manager()
        result = mgr.send_position_change_alert(
            strategy="aave_lending",
            before_value=Decimal("10000"),
            after_value=Decimal("8000"),
            reasoning="Rebalancing due to utilization spike",
        )
        assert result.alert_type == AlertType.POSITION_CHANGE
        assert result.severity == AlertSeverity.WARNING

    def test_message_contains_strategy(self) -> None:
        mgr = _make_manager()
        result = mgr.send_position_change_alert(
            strategy="lido_staking",
            before_value="5000",
            after_value="3000",
            reasoning="risk reduction",
        )
        assert "lido_staking" in result.message


# ---------------------------------------------------------------------------
# Daily summary
# ---------------------------------------------------------------------------
class TestDailySummary:

    def test_sends_with_correct_type(self) -> None:
        mgr = _make_manager()
        result = mgr.send_daily_summary(
            portfolio_value=Decimal("100000"),
            daily_pnl=Decimal("500"),
            active_strategies=["aave_lending", "lido_staking"],
        )
        assert result.alert_type == AlertType.DAILY_SUMMARY
        assert result.severity == AlertSeverity.INFO

    def test_with_performers(self) -> None:
        mgr = _make_manager()
        result = mgr.send_daily_summary(
            portfolio_value=Decimal("100000"),
            daily_pnl=Decimal("-200"),
            active_strategies=["a", "b"],
            top_performer="a",
            bottom_performer="b",
        )
        assert result.alert_type == AlertType.DAILY_SUMMARY

    def test_empty_strategies(self) -> None:
        mgr = _make_manager()
        result = mgr.send_daily_summary(
            portfolio_value=Decimal("50000"),
            daily_pnl=Decimal("0"),
            active_strategies=[],
        )
        assert result.alert_type == AlertType.DAILY_SUMMARY

    def test_filtered_when_severity_too_low(self) -> None:
        mgr = _make_manager(min_severity=AlertSeverity.WARNING)
        result = mgr.send_daily_summary(
            portfolio_value=Decimal("100000"),
            daily_pnl=Decimal("500"),
            active_strategies=["a"],
        )
        assert not result.sent
        assert result.error == "Below minimum severity threshold"


# ---------------------------------------------------------------------------
# Anomaly alerts
# ---------------------------------------------------------------------------
class TestAnomalyAlert:

    def test_sends_with_correct_type(self) -> None:
        mgr = _make_manager()
        result = mgr.send_anomaly_alert(
            anomaly_type="balance_deviation",
            message="Unexpected balance change detected",
        )
        assert result.alert_type == AlertType.ANOMALY

    def test_custom_severity(self) -> None:
        mgr = _make_manager()
        result = mgr.send_anomaly_alert(
            anomaly_type="gas_spike",
            severity=AlertSeverity.CRITICAL,
            message="Gas 5x normal",
        )
        assert result.severity == AlertSeverity.CRITICAL

    def test_with_data(self) -> None:
        mgr = _make_manager()
        result = mgr.send_anomaly_alert(
            anomaly_type="protocol_tvl_drop",
            message="Aave TVL dropped 35%",
            data={"protocol": "aave", "drop_pct": "35"},
        )
        assert "protocol_tvl_drop" in result.message


# ---------------------------------------------------------------------------
# Approval request alerts
# ---------------------------------------------------------------------------
class TestApprovalRequestAlert:

    def test_sends_with_correct_type(self) -> None:
        mgr = _make_manager()
        result = mgr.send_approval_request(
            action_description="Deploy to new protocol: Compound V3",
            amounts="$15,000 USDC",
            risk_context="New protocol, unaudited adapter",
            estimated_impact="15% portfolio allocation",
            approval_id="abc123",
        )
        assert result.alert_type == AlertType.APPROVAL_REQUEST
        assert "abc123" in result.message

    def test_approval_id_in_message(self) -> None:
        mgr = _make_manager()
        result = mgr.send_approval_request(
            action_description="Large trade",
            amounts="$20,000",
            risk_context="High exposure",
            estimated_impact="20% of portfolio",
            approval_id="xyz789",
        )
        assert "xyz789" in result.message


# ---------------------------------------------------------------------------
# Webhook error handling
# ---------------------------------------------------------------------------
class TestWebhookErrors:

    @patch("monitoring.discord_alerts.urllib.request.urlopen")
    def test_http_error_handled(self, mock_urlopen) -> None:
        import urllib.error

        mock_urlopen.side_effect = urllib.error.HTTPError(
            url="https://example.com", code=500, msg="Error",
            hdrs=None, fp=None,  # type: ignore[arg-type]
        )
        mgr = _make_manager_with_webhook()
        result = mgr.send_circuit_breaker_alert(
            trigger_type="test",
            threshold="x",
            action_taken="y",
        )
        assert not result.sent

    @patch("monitoring.discord_alerts.urllib.request.urlopen")
    def test_url_error_handled(self, mock_urlopen) -> None:
        import urllib.error

        mock_urlopen.side_effect = urllib.error.URLError("Connection refused")
        mgr = _make_manager_with_webhook()
        result = mgr.send_circuit_breaker_alert(
            trigger_type="test",
            threshold="x",
            action_taken="y",
        )
        assert not result.sent

    @patch("monitoring.discord_alerts.urllib.request.urlopen")
    def test_os_error_handled(self, mock_urlopen) -> None:
        mock_urlopen.side_effect = OSError("Network unreachable")
        mgr = _make_manager_with_webhook()
        result = mgr.send_circuit_breaker_alert(
            trigger_type="test",
            threshold="x",
            action_taken="y",
        )
        assert not result.sent


# ---------------------------------------------------------------------------
# History tracking
# ---------------------------------------------------------------------------
class TestHistory:

    def test_history_accumulates(self) -> None:
        mgr = _make_manager()
        mgr.send_circuit_breaker_alert(trigger_type="a", threshold="b", action_taken="c")
        mgr.send_anomaly_alert(anomaly_type="x", message="y")
        assert len(mgr.history) == 2

    def test_history_is_copy(self) -> None:
        mgr = _make_manager()
        mgr.send_circuit_breaker_alert(trigger_type="a", threshold="b", action_taken="c")
        h = mgr.history
        h.clear()
        assert len(mgr.history) == 1


# ---------------------------------------------------------------------------
# Graceful degradation
# ---------------------------------------------------------------------------
class TestGracefulDegradation:

    def test_no_webhook_still_logs(self) -> None:
        mgr = _make_manager()
        result = mgr.send_circuit_breaker_alert(
            trigger_type="drawdown",
            threshold="20%",
            action_taken="halt_all",
        )
        # Alert is not sent but is recorded in history
        assert not result.sent
        assert len(mgr.history) == 1
        assert result.alert_type == AlertType.CIRCUIT_BREAKER

    def test_all_alert_types_work_without_webhook(self) -> None:
        mgr = _make_manager()
        r1 = mgr.send_circuit_breaker_alert(
            trigger_type="t", threshold="th", action_taken="a",
        )
        r2 = mgr.send_position_change_alert(
            strategy="s", before_value="1", after_value="2", reasoning="r",
        )
        r3 = mgr.send_daily_summary(
            portfolio_value=Decimal("100"), daily_pnl=Decimal("10"),
            active_strategies=["x"],
        )
        r4 = mgr.send_anomaly_alert(anomaly_type="at", message="m")
        r5 = mgr.send_approval_request(
            action_description="ad", amounts="$1",
            risk_context="rc", estimated_impact="ei", approval_id="id1",
        )
        for r in [r1, r2, r3, r4, r5]:
            assert not r.sent
        assert len(mgr.history) == 5
