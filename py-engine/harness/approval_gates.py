"""Human-in-the-loop approval gates — high-impact actions require owner approval (HARNESS-003).

Approval required for new protocol deployment, trades >15% of portfolio, and
new strategy tier activation.  Requests are sent via Discord (if configured)
and tracked in memory with configurable timeout.  The agent continues other
operations while awaiting approval — the gate never blocks.

Emergency override commands (pause_all, force_unwind, withdraw_all) are
processed immediately.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Any

from monitoring.logger import get_logger

_logger = get_logger("approval-gates", enable_file=False)

# Default approval timeout: 24 hours
_DEFAULT_TIMEOUT_HOURS = 24


# ---------------------------------------------------------------------------
# Enums & data classes
# ---------------------------------------------------------------------------
class ApprovalStatus(StrEnum):
    """Status of a pending approval request."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class ApprovalActionType(StrEnum):
    """Categories of actions that require approval."""

    NEW_PROTOCOL = "new_protocol"
    LARGE_TRADE = "large_trade"
    NEW_STRATEGY_TIER = "new_strategy_tier"


class EmergencyCommand(StrEnum):
    """Emergency override commands from the owner."""

    PAUSE_ALL = "pause_all"
    FORCE_UNWIND = "force_unwind"
    WITHDRAW_ALL = "withdraw_all"


@dataclass
class PendingApproval:
    """A pending approval request awaiting human decision."""

    approval_id: str
    action_type: str
    description: str
    amounts: str
    risk_context: str
    estimated_impact: str
    status: str = ApprovalStatus.PENDING
    created_at: str = ""
    expires_at: str = ""
    resolved_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = datetime.now(UTC).isoformat()


@dataclass
class EmergencyAction:
    """Result of processing an emergency override command."""

    command: str
    executed: bool
    timestamp: str = ""
    message: str = ""

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = datetime.now(UTC).isoformat()


@dataclass
class ApprovalLog:
    """Audit log entry for an approval or rejection."""

    approval_id: str
    action: str
    status: str
    timestamp: str = ""

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = datetime.now(UTC).isoformat()


# ---------------------------------------------------------------------------
# Approval Gate Manager
# ---------------------------------------------------------------------------
class ApprovalGateManager:
    """Manages human-in-the-loop approval gates for high-impact actions.

    Approvals are non-blocking: ``request_approval`` returns immediately with
    a ``PendingApproval`` in PENDING status.  Callers poll via
    ``check_approval`` to discover whether the owner has approved, rejected,
    or the request has timed out.
    """

    def __init__(
        self,
        *,
        timeout_hours: float | None = None,
        discord_alert_manager: Any | None = None,
    ) -> None:
        env_timeout = os.environ.get("APPROVAL_TIMEOUT_HOURS", "")
        if timeout_hours is not None:
            self._timeout_hours = timeout_hours
        elif env_timeout:
            self._timeout_hours = float(env_timeout)
        else:
            self._timeout_hours = _DEFAULT_TIMEOUT_HOURS

        self._discord = discord_alert_manager
        self._pending: dict[str, PendingApproval] = {}
        self._audit_log: list[ApprovalLog] = []
        self._paused = False

    @property
    def paused(self) -> bool:
        """Return whether all operations are paused via emergency command."""
        return self._paused

    @property
    def audit_log(self) -> list[ApprovalLog]:
        """Return a copy of the audit log."""
        return list(self._audit_log)

    @property
    def pending_count(self) -> int:
        """Return the number of currently pending approvals."""
        return sum(
            1 for a in self._pending.values() if a.status == ApprovalStatus.PENDING
        )

    # -- approval lifecycle -------------------------------------------------

    def requires_approval(
        self,
        action_type: str,
        *,
        trade_pct: Decimal | None = None,
    ) -> bool:
        """Determine if an action requires human approval.

        Args:
            action_type: The type of action being performed.
            trade_pct: For trades, the percentage of portfolio involved.

        Returns:
            True if approval is required.
        """
        if action_type == ApprovalActionType.NEW_PROTOCOL:
            return True
        if action_type == ApprovalActionType.NEW_STRATEGY_TIER:
            return True
        if action_type == ApprovalActionType.LARGE_TRADE:
            return trade_pct is not None and trade_pct > Decimal("15")
        return False

    def request_approval(
        self,
        *,
        action_type: str,
        description: str,
        amounts: str = "",
        risk_context: str = "",
        estimated_impact: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> PendingApproval:
        """Create a pending approval request.

        Sends a Discord notification (if configured) and returns immediately.
        The caller should poll ``check_approval`` to learn the outcome.

        Args:
            action_type: Category of action requiring approval.
            description: Human-readable description of the action.
            amounts: The amounts involved.
            risk_context: Risk assessment text.
            estimated_impact: Expected impact description.
            metadata: Optional extra context.

        Returns:
            A ``PendingApproval`` in PENDING status.
        """
        approval_id = uuid.uuid4().hex[:12]
        now = datetime.now(UTC)
        expires = now + timedelta(hours=self._timeout_hours)

        approval = PendingApproval(
            approval_id=approval_id,
            action_type=action_type,
            description=description,
            amounts=amounts,
            risk_context=risk_context,
            estimated_impact=estimated_impact,
            status=ApprovalStatus.PENDING,
            created_at=now.isoformat(),
            expires_at=expires.isoformat(),
            metadata=metadata or {},
        )
        self._pending[approval_id] = approval

        log_entry = ApprovalLog(
            approval_id=approval_id,
            action="requested",
            status=ApprovalStatus.PENDING,
        )
        self._audit_log.append(log_entry)

        _logger.info(
            f"Approval requested: {description}",
            extra={
                "data": {
                    "approval_id": approval_id,
                    "action_type": action_type,
                    "timeout_hours": self._timeout_hours,
                },
            },
        )

        # Send Discord notification if available
        if self._discord is not None:
            try:
                self._discord.send_approval_request(
                    action_description=description,
                    amounts=amounts,
                    risk_context=risk_context,
                    estimated_impact=estimated_impact,
                    approval_id=approval_id,
                )
            except Exception:
                _logger.warning(
                    "Failed to send Discord approval request",
                    extra={"data": {"approval_id": approval_id}},
                )

        return approval

    def check_approval(self, approval_id: str) -> ApprovalStatus:
        """Check the current status of an approval request.

        Automatically expires requests that have passed their timeout.

        Args:
            approval_id: The unique approval identifier.

        Returns:
            The current ``ApprovalStatus``.
        """
        approval = self._pending.get(approval_id)
        if approval is None:
            return ApprovalStatus.EXPIRED

        # Check expiration
        if approval.status == ApprovalStatus.PENDING and approval.expires_at:
            expires = datetime.fromisoformat(approval.expires_at)
            if datetime.now(UTC) >= expires:
                approval.status = ApprovalStatus.EXPIRED
                approval.resolved_at = datetime.now(UTC).isoformat()
                self._audit_log.append(
                    ApprovalLog(
                        approval_id=approval_id,
                        action="expired",
                        status=ApprovalStatus.EXPIRED,
                    ),
                )
                _logger.info(
                    f"Approval expired: {approval.description}",
                    extra={"data": {"approval_id": approval_id}},
                )

        return ApprovalStatus(approval.status)

    def approve(self, approval_id: str) -> bool:
        """Mark an approval as approved.

        Args:
            approval_id: The unique approval identifier.

        Returns:
            True if the approval was successfully marked, False if not found or
            not pending.
        """
        approval = self._pending.get(approval_id)
        if approval is None or approval.status != ApprovalStatus.PENDING:
            return False

        approval.status = ApprovalStatus.APPROVED
        approval.resolved_at = datetime.now(UTC).isoformat()
        self._audit_log.append(
            ApprovalLog(
                approval_id=approval_id,
                action="approved",
                status=ApprovalStatus.APPROVED,
            ),
        )
        _logger.info(
            f"Approval granted: {approval.description}",
            extra={"data": {"approval_id": approval_id}},
        )
        return True

    def reject(self, approval_id: str) -> bool:
        """Mark an approval as rejected.

        Args:
            approval_id: The unique approval identifier.

        Returns:
            True if the rejection was recorded, False if not found or not
            pending.
        """
        approval = self._pending.get(approval_id)
        if approval is None or approval.status != ApprovalStatus.PENDING:
            return False

        approval.status = ApprovalStatus.REJECTED
        approval.resolved_at = datetime.now(UTC).isoformat()
        self._audit_log.append(
            ApprovalLog(
                approval_id=approval_id,
                action="rejected",
                status=ApprovalStatus.REJECTED,
            ),
        )
        _logger.info(
            f"Approval rejected: {approval.description}",
            extra={"data": {"approval_id": approval_id}},
        )
        return True

    # -- emergency overrides ------------------------------------------------

    def process_emergency_command(self, command: str) -> EmergencyAction:
        """Process an emergency override command from the owner.

        Supported commands:
        - ``pause_all``: Halt all trading operations.
        - ``force_unwind``: Unwind all open positions.
        - ``withdraw_all``: Withdraw all funds to safety.

        Args:
            command: One of the ``EmergencyCommand`` values.

        Returns:
            An ``EmergencyAction`` describing what was done.
        """
        now = datetime.now(UTC).isoformat()

        if command == EmergencyCommand.PAUSE_ALL:
            self._paused = True
            # Cancel all pending approvals
            for approval in self._pending.values():
                if approval.status == ApprovalStatus.PENDING:
                    approval.status = ApprovalStatus.CANCELLED
                    approval.resolved_at = now
                    self._audit_log.append(
                        ApprovalLog(
                            approval_id=approval.approval_id,
                            action="cancelled_by_emergency",
                            status=ApprovalStatus.CANCELLED,
                        ),
                    )
            action = EmergencyAction(
                command=command,
                executed=True,
                message="All operations paused; pending approvals cancelled",
            )

        elif command == EmergencyCommand.FORCE_UNWIND:
            self._paused = True
            action = EmergencyAction(
                command=command,
                executed=True,
                message="Force unwind initiated; all positions queued for closure",
            )

        elif command == EmergencyCommand.WITHDRAW_ALL:
            self._paused = True
            action = EmergencyAction(
                command=command,
                executed=True,
                message="Withdraw all initiated; funds returning to safe wallet",
            )

        else:
            action = EmergencyAction(
                command=command,
                executed=False,
                message=f"Unknown emergency command: {command}",
            )

        self._audit_log.append(
            ApprovalLog(
                approval_id="emergency",
                action=f"emergency_{command}",
                status="executed" if action.executed else "failed",
            ),
        )

        _logger.warning(
            f"Emergency command: {command}",
            extra={
                "data": {
                    "command": command,
                    "executed": action.executed,
                    "message": action.message,
                },
            },
        )

        return action

    def get_pending_approvals(self) -> list[PendingApproval]:
        """Return all approvals currently in PENDING status.

        Expired requests are checked and updated before returning.
        """
        # Expire stale requests first
        for approval_id in list(self._pending):
            self.check_approval(approval_id)
        return [
            a for a in self._pending.values()
            if a.status == ApprovalStatus.PENDING
        ]
