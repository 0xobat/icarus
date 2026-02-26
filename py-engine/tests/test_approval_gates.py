"""Tests for human-in-the-loop approval gates — HARNESS-003."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

from harness.approval_gates import (
    ApprovalActionType,
    ApprovalGateManager,
    ApprovalLog,
    ApprovalStatus,
    EmergencyAction,
    EmergencyCommand,
    PendingApproval,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _make_manager(**kwargs) -> ApprovalGateManager:
    return ApprovalGateManager(**kwargs)


def _request_sample(mgr: ApprovalGateManager, **overrides) -> PendingApproval:
    defaults = {
        "action_type": ApprovalActionType.NEW_PROTOCOL,
        "description": "Deploy to Compound V3",
        "amounts": "$15,000 USDC",
        "risk_context": "New protocol, unaudited adapter",
        "estimated_impact": "15% portfolio allocation",
    }
    defaults.update(overrides)
    return mgr.request_approval(**defaults)


# ---------------------------------------------------------------------------
# PendingApproval dataclass
# ---------------------------------------------------------------------------
class TestPendingApproval:

    def test_auto_timestamp(self) -> None:
        p = PendingApproval(
            approval_id="test",
            action_type="new_protocol",
            description="desc",
            amounts="$1",
            risk_context="low",
            estimated_impact="minimal",
        )
        assert p.created_at != ""

    def test_default_status_pending(self) -> None:
        p = PendingApproval(
            approval_id="test",
            action_type="new_protocol",
            description="desc",
            amounts="$1",
            risk_context="low",
            estimated_impact="minimal",
        )
        assert p.status == ApprovalStatus.PENDING


# ---------------------------------------------------------------------------
# EmergencyAction dataclass
# ---------------------------------------------------------------------------
class TestEmergencyAction:

    def test_auto_timestamp(self) -> None:
        a = EmergencyAction(command="pause_all", executed=True, message="done")
        assert a.timestamp != ""


# ---------------------------------------------------------------------------
# ApprovalLog dataclass
# ---------------------------------------------------------------------------
class TestApprovalLog:

    def test_auto_timestamp(self) -> None:
        log = ApprovalLog(approval_id="a1", action="requested", status="pending")
        assert log.timestamp != ""


# ---------------------------------------------------------------------------
# Requires approval
# ---------------------------------------------------------------------------
class TestRequiresApproval:

    def test_new_protocol_requires(self) -> None:
        mgr = _make_manager()
        assert mgr.requires_approval(ApprovalActionType.NEW_PROTOCOL)

    def test_new_strategy_tier_requires(self) -> None:
        mgr = _make_manager()
        assert mgr.requires_approval(ApprovalActionType.NEW_STRATEGY_TIER)

    def test_large_trade_above_15pct(self) -> None:
        mgr = _make_manager()
        assert mgr.requires_approval(
            ApprovalActionType.LARGE_TRADE, trade_pct=Decimal("16"),
        )

    def test_trade_at_15pct_does_not_require(self) -> None:
        mgr = _make_manager()
        assert not mgr.requires_approval(
            ApprovalActionType.LARGE_TRADE, trade_pct=Decimal("15"),
        )

    def test_small_trade_does_not_require(self) -> None:
        mgr = _make_manager()
        assert not mgr.requires_approval(
            ApprovalActionType.LARGE_TRADE, trade_pct=Decimal("5"),
        )

    def test_unknown_action_does_not_require(self) -> None:
        mgr = _make_manager()
        assert not mgr.requires_approval("routine_operation")


# ---------------------------------------------------------------------------
# Request approval
# ---------------------------------------------------------------------------
class TestRequestApproval:

    def test_returns_pending_approval(self) -> None:
        mgr = _make_manager()
        approval = _request_sample(mgr)
        assert approval.status == ApprovalStatus.PENDING
        assert approval.approval_id != ""
        assert approval.description == "Deploy to Compound V3"

    def test_has_expiration(self) -> None:
        mgr = _make_manager(timeout_hours=24)
        approval = _request_sample(mgr)
        assert approval.expires_at != ""

    def test_pending_count_increments(self) -> None:
        mgr = _make_manager()
        assert mgr.pending_count == 0
        _request_sample(mgr)
        assert mgr.pending_count == 1
        _request_sample(mgr)
        assert mgr.pending_count == 2

    def test_audit_log_records_request(self) -> None:
        mgr = _make_manager()
        _request_sample(mgr)
        assert len(mgr.audit_log) == 1
        assert mgr.audit_log[0].action == "requested"

    def test_metadata_stored(self) -> None:
        mgr = _make_manager()
        approval = mgr.request_approval(
            action_type=ApprovalActionType.NEW_PROTOCOL,
            description="test",
            metadata={"chain": "ethereum"},
        )
        assert approval.metadata["chain"] == "ethereum"

    def test_discord_notification_sent(self) -> None:
        mock_discord = MagicMock()
        mgr = _make_manager(discord_alert_manager=mock_discord)
        _request_sample(mgr)
        mock_discord.send_approval_request.assert_called_once()

    def test_discord_failure_does_not_crash(self) -> None:
        mock_discord = MagicMock()
        mock_discord.send_approval_request.side_effect = RuntimeError("boom")
        mgr = _make_manager(discord_alert_manager=mock_discord)
        approval = _request_sample(mgr)
        assert approval.status == ApprovalStatus.PENDING


# ---------------------------------------------------------------------------
# Check approval
# ---------------------------------------------------------------------------
class TestCheckApproval:

    def test_pending_returns_pending(self) -> None:
        mgr = _make_manager()
        approval = _request_sample(mgr)
        assert mgr.check_approval(approval.approval_id) == ApprovalStatus.PENDING

    def test_unknown_id_returns_expired(self) -> None:
        mgr = _make_manager()
        assert mgr.check_approval("nonexistent") == ApprovalStatus.EXPIRED

    def test_expired_after_timeout(self) -> None:
        mgr = _make_manager(timeout_hours=0.001)  # ~3.6 seconds
        approval = _request_sample(mgr)
        # Force expiration
        past = datetime.now(UTC) - timedelta(hours=1)
        mgr._pending[approval.approval_id].expires_at = past.isoformat()
        status = mgr.check_approval(approval.approval_id)
        assert status == ApprovalStatus.EXPIRED

    def test_expiration_logged(self) -> None:
        mgr = _make_manager()
        approval = _request_sample(mgr)
        past = datetime.now(UTC) - timedelta(hours=1)
        mgr._pending[approval.approval_id].expires_at = past.isoformat()
        mgr.check_approval(approval.approval_id)
        expired_logs = [
            log for log in mgr.audit_log if log.action == "expired"
        ]
        assert len(expired_logs) == 1


# ---------------------------------------------------------------------------
# Approve and reject
# ---------------------------------------------------------------------------
class TestApproveReject:

    def test_approve_success(self) -> None:
        mgr = _make_manager()
        approval = _request_sample(mgr)
        assert mgr.approve(approval.approval_id)
        assert mgr.check_approval(approval.approval_id) == ApprovalStatus.APPROVED

    def test_approve_sets_resolved_at(self) -> None:
        mgr = _make_manager()
        approval = _request_sample(mgr)
        mgr.approve(approval.approval_id)
        assert mgr._pending[approval.approval_id].resolved_at is not None

    def test_reject_success(self) -> None:
        mgr = _make_manager()
        approval = _request_sample(mgr)
        assert mgr.reject(approval.approval_id)
        assert mgr.check_approval(approval.approval_id) == ApprovalStatus.REJECTED

    def test_reject_sets_resolved_at(self) -> None:
        mgr = _make_manager()
        approval = _request_sample(mgr)
        mgr.reject(approval.approval_id)
        assert mgr._pending[approval.approval_id].resolved_at is not None

    def test_cannot_approve_nonexistent(self) -> None:
        mgr = _make_manager()
        assert not mgr.approve("nonexistent")

    def test_cannot_reject_nonexistent(self) -> None:
        mgr = _make_manager()
        assert not mgr.reject("nonexistent")

    def test_cannot_approve_already_approved(self) -> None:
        mgr = _make_manager()
        approval = _request_sample(mgr)
        mgr.approve(approval.approval_id)
        assert not mgr.approve(approval.approval_id)

    def test_cannot_reject_already_rejected(self) -> None:
        mgr = _make_manager()
        approval = _request_sample(mgr)
        mgr.reject(approval.approval_id)
        assert not mgr.reject(approval.approval_id)

    def test_approve_logged(self) -> None:
        mgr = _make_manager()
        approval = _request_sample(mgr)
        mgr.approve(approval.approval_id)
        approved_logs = [
            log for log in mgr.audit_log if log.action == "approved"
        ]
        assert len(approved_logs) == 1

    def test_reject_logged(self) -> None:
        mgr = _make_manager()
        approval = _request_sample(mgr)
        mgr.reject(approval.approval_id)
        rejected_logs = [
            log for log in mgr.audit_log if log.action == "rejected"
        ]
        assert len(rejected_logs) == 1

    def test_pending_count_decreases_on_approve(self) -> None:
        mgr = _make_manager()
        approval = _request_sample(mgr)
        assert mgr.pending_count == 1
        mgr.approve(approval.approval_id)
        assert mgr.pending_count == 0

    def test_pending_count_decreases_on_reject(self) -> None:
        mgr = _make_manager()
        approval = _request_sample(mgr)
        assert mgr.pending_count == 1
        mgr.reject(approval.approval_id)
        assert mgr.pending_count == 0


# ---------------------------------------------------------------------------
# Non-blocking behavior
# ---------------------------------------------------------------------------
class TestNonBlocking:

    def test_request_returns_immediately(self) -> None:
        mgr = _make_manager()
        approval = _request_sample(mgr)
        assert approval.status == ApprovalStatus.PENDING
        # No blocking — can continue other operations immediately

    def test_multiple_pending_simultaneously(self) -> None:
        mgr = _make_manager()
        a1 = _request_sample(mgr, description="Action 1")
        a2 = _request_sample(mgr, description="Action 2")
        _request_sample(mgr, description="Action 3")
        assert mgr.pending_count == 3
        mgr.approve(a1.approval_id)
        assert mgr.pending_count == 2
        mgr.reject(a2.approval_id)
        assert mgr.pending_count == 1


# ---------------------------------------------------------------------------
# Emergency commands
# ---------------------------------------------------------------------------
class TestEmergencyCommands:

    def test_pause_all(self) -> None:
        mgr = _make_manager()
        action = mgr.process_emergency_command(EmergencyCommand.PAUSE_ALL)
        assert action.executed
        assert mgr.paused
        assert "paused" in action.message.lower()

    def test_force_unwind(self) -> None:
        mgr = _make_manager()
        action = mgr.process_emergency_command(EmergencyCommand.FORCE_UNWIND)
        assert action.executed
        assert mgr.paused

    def test_withdraw_all(self) -> None:
        mgr = _make_manager()
        action = mgr.process_emergency_command(EmergencyCommand.WITHDRAW_ALL)
        assert action.executed
        assert mgr.paused

    def test_unknown_command(self) -> None:
        mgr = _make_manager()
        action = mgr.process_emergency_command("self_destruct")
        assert not action.executed
        assert "unknown" in action.message.lower()

    def test_pause_cancels_pending(self) -> None:
        mgr = _make_manager()
        a1 = _request_sample(mgr)
        a2 = _request_sample(mgr)
        mgr.process_emergency_command(EmergencyCommand.PAUSE_ALL)
        assert mgr.pending_count == 0
        assert mgr._pending[a1.approval_id].status == ApprovalStatus.CANCELLED
        assert mgr._pending[a2.approval_id].status == ApprovalStatus.CANCELLED

    def test_emergency_logged(self) -> None:
        mgr = _make_manager()
        mgr.process_emergency_command(EmergencyCommand.PAUSE_ALL)
        emergency_logs = [
            log for log in mgr.audit_log if "emergency" in log.action
        ]
        assert len(emergency_logs) >= 1


# ---------------------------------------------------------------------------
# Timeout configuration
# ---------------------------------------------------------------------------
class TestTimeoutConfiguration:

    def test_default_timeout_24h(self) -> None:
        mgr = _make_manager()
        assert mgr._timeout_hours == 24

    def test_custom_timeout(self) -> None:
        mgr = _make_manager(timeout_hours=48)
        assert mgr._timeout_hours == 48

    def test_timeout_from_env(self) -> None:
        with patch.dict("os.environ", {"APPROVAL_TIMEOUT_HOURS": "12"}):
            mgr = _make_manager()
            assert mgr._timeout_hours == 12.0

    def test_constructor_overrides_env(self) -> None:
        with patch.dict("os.environ", {"APPROVAL_TIMEOUT_HOURS": "12"}):
            mgr = _make_manager(timeout_hours=6)
            assert mgr._timeout_hours == 6


# ---------------------------------------------------------------------------
# Get pending approvals
# ---------------------------------------------------------------------------
class TestGetPendingApprovals:

    def test_returns_only_pending(self) -> None:
        mgr = _make_manager()
        a1 = _request_sample(mgr, description="Pending")
        a2 = _request_sample(mgr, description="Approved")
        mgr.approve(a2.approval_id)
        pending = mgr.get_pending_approvals()
        assert len(pending) == 1
        assert pending[0].approval_id == a1.approval_id

    def test_auto_expires_stale(self) -> None:
        mgr = _make_manager()
        approval = _request_sample(mgr)
        past = datetime.now(UTC) - timedelta(hours=1)
        mgr._pending[approval.approval_id].expires_at = past.isoformat()
        pending = mgr.get_pending_approvals()
        assert len(pending) == 0


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------
class TestAuditLog:

    def test_full_lifecycle_logged(self) -> None:
        mgr = _make_manager()
        approval = _request_sample(mgr)
        mgr.approve(approval.approval_id)
        log = mgr.audit_log
        actions = [entry.action for entry in log]
        assert "requested" in actions
        assert "approved" in actions

    def test_audit_log_is_copy(self) -> None:
        mgr = _make_manager()
        _request_sample(mgr)
        log = mgr.audit_log
        log.clear()
        assert len(mgr.audit_log) == 1

    def test_all_entries_have_timestamps(self) -> None:
        mgr = _make_manager()
        approval = _request_sample(mgr)
        mgr.approve(approval.approval_id)
        for entry in mgr.audit_log:
            assert entry.timestamp != ""
