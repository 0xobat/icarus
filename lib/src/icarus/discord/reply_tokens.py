"""Reply-token store for Discord operator round-trips.

A *reply token* is a Postgres row recording one outstanding operator
question (e.g. "should we promote candidate c-7af3 to live_capped?").
When the operator types ``APPROVE c-7af3`` or ``REJECT c-7af3 <reason>``
back into the Discord channel, the message is fed through
:meth:`ReplyTokenStore.match_reply`, which:

1. Parses the message with a strict regex (see ``_REPLY_PATTERN``).
2. Looks up the most recent ``pending`` token whose ``candidate_id``
   matches the message's candidate slug.
3. Atomically flips that row to ``approved`` / ``rejected`` and stamps
   ``replied_at`` + ``reply_message`` + ``reply_verdict``.

Tokens older than ``ttl_hours`` are swept to ``expired`` by
:meth:`ReplyTokenStore.expire_stale`; the lake-governor's tick loop calls
this so a forgotten request never blocks the next promotion attempt.

All DB writes wrap sync SQLAlchemy sessions inside :func:`asyncio.to_thread`
to keep the lake-governor event loop responsive — same pattern as
``lake_governor.state_machine``.

Reply syntax (case-insensitive on the verb)::

    APPROVE tok-42
    REJECT tok-42 returns concentrated in 4-day window
    approve tok-42

The ``tok-<id>`` slug is the integer ``DiscordReplyToken.id`` printed
in the outbound promotion-request message. Earlier the reply syntax
was ``APPROVE <candidate_id>``, but the bot itself broadcast the
candidate id in the same message — any reader of the channel could
echo it, satisfying the regex without ever holding a per-request
secret. The token-id form requires the reply to reference the
SPECIFIC pending request, not just the candidate (see W12 review).

Anything else returns ``None`` from ``match_reply``.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final, Literal

import structlog
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from icarus.db.database import DatabaseManager
from icarus.db.models import (
    DISCORD_REPLY_TOKEN_KINDS,
    DiscordReplyToken,
)

logger = structlog.get_logger(service="discord", component="reply_tokens")


# ─── Public dataclasses ─────────────────────────────────────────────────────


Verdict = Literal["approved", "rejected"]


@dataclass(frozen=True, slots=True)
class ReplyToken:
    """Snapshot of a ``discord_reply_tokens`` row at creation/read time."""

    id: int
    candidate_id: str
    template_id: str
    kind: str
    status: str
    created_at: datetime
    expires_at: datetime
    replied_at: datetime | None = None
    reply_message: str | None = None
    reply_verdict: str | None = None


@dataclass(frozen=True, slots=True)
class ReplyTokenMatch:
    """Result of a successful :meth:`ReplyTokenStore.match_reply` call."""

    token: ReplyToken
    verdict: Verdict
    reason: str | None


# ─── Reply parsing ──────────────────────────────────────────────────────────

# ``^(APPROVE|REJECT) tok-<id> [reason...]$``
# - Verb is case-insensitive (we ``.upper()`` after parsing).
# - Token id is the durable per-request identifier (``DiscordReplyToken.id``
#   prefixed with ``tok-``). Earlier shape was ``APPROVE <candidate_id>``,
#   which let any reader of the broadcast message satisfy the regex with
#   no per-request secret — flagged in the W12 review as auth bypass.
#   Using ``tok-<id>`` means the reply must reference the SPECIFIC pending
#   request, not just the candidate.
# - Reason is optional; APPROVE typically has none, REJECT typically has one.
TOKEN_SLUG_PREFIX: Final[str] = "tok-"


def format_token_slug(token_id: int) -> str:
    """Render the operator-facing token slug for ``DiscordReplyToken.id``."""
    return f"{TOKEN_SLUG_PREFIX}{token_id}"


_REPLY_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^(?P<verb>APPROVE|REJECT)\s+tok-(?P<token_id>\d+)(?:\s+(?P<reason>.+))?$",
    re.IGNORECASE | re.DOTALL,
)


def _parse_reply(message: str) -> tuple[Verdict, int, str | None] | None:
    """Parse an operator reply.

    Returns ``(verdict, token_id, reason)`` if the message matches the
    accepted shape, else ``None``. ``verdict`` is normalized to
    ``"approved"`` / ``"rejected"`` to match the persisted ``reply_verdict``
    column values. ``token_id`` is the integer ``DiscordReplyToken.id``
    parsed from the ``tok-<n>`` slug.
    """
    stripped = message.strip()
    if not stripped:
        return None
    match = _REPLY_PATTERN.match(stripped)
    if match is None:
        return None
    verb = match.group("verb").upper()
    try:
        token_id = int(match.group("token_id"))
    except ValueError:
        return None
    reason = match.group("reason")
    verdict: Verdict = "approved" if verb == "APPROVE" else "rejected"
    if reason is not None:
        reason = reason.strip() or None
    return verdict, token_id, reason


# ─── Store ──────────────────────────────────────────────────────────────────


class ReplyTokenStore:
    """Async wrapper around the ``discord_reply_tokens`` table.

    Cheap to construct — holds only a reference to the
    :class:`DatabaseManager`. Safe to call concurrently from multiple
    awaits; each method opens a fresh sync session inside
    :func:`asyncio.to_thread`.
    """

    def __init__(self, db: DatabaseManager, *, default_ttl_hours: int = 24) -> None:
        self._db = db
        self._default_ttl_hours = default_ttl_hours

    async def create_pending(
        self,
        *,
        candidate_id: str,
        template_id: str,
        kind: str,
        ttl_hours: int | None = None,
    ) -> ReplyToken:
        """Insert a fresh ``pending`` token row, return the snapshot.

        ``kind`` must be one of :data:`DISCORD_REPLY_TOKEN_KINDS` — guarded
        in Python rather than via DB CHECK constraint (portability).
        """
        if kind not in DISCORD_REPLY_TOKEN_KINDS:
            raise ValueError(
                f"unknown reply-token kind: {kind!r}; "
                f"expected one of {DISCORD_REPLY_TOKEN_KINDS}"
            )
        ttl = ttl_hours if ttl_hours is not None else self._default_ttl_hours
        return await asyncio.to_thread(
            self._sync_create_pending, candidate_id, template_id, kind, ttl
        )

    async def match_reply(self, *, message: str) -> ReplyTokenMatch | None:
        """Parse ``message`` and resolve it against the pending token by id.

        Returns the match (with the row updated to its terminal state) on
        success; ``None`` if the message doesn't parse or no pending
        token with the referenced ``tok-<id>`` exists.

        The token-id lookup (vs. the prior candidate-id lookup) is what
        makes the reply unforgeable by a non-allowlist reader of the
        broadcast message — see W12 review §"Discord auth bypass".
        """
        parsed = _parse_reply(message)
        if parsed is None:
            logger.debug("reply_tokens.match.no_parse", message_prefix=message[:80])
            return None
        verdict, token_id, reason = parsed
        return await asyncio.to_thread(
            self._sync_match_reply, token_id, verdict, reason, message
        )

    async def expire_stale(
        self, *, now: datetime, ttl_hours: int | None = None
    ) -> int:
        """Mark all pending tokens past their ``expires_at`` as ``expired``.

        The ``ttl_hours`` argument is accepted for API symmetry but the
        canonical truth is the row's ``expires_at`` column (set when the
        token was created). Returns the number of rows flipped.
        """
        # The ttl_hours argument here is informational; expires_at on the
        # row is the source of truth. Logged for audit.
        return await asyncio.to_thread(self._sync_expire_stale, now, ttl_hours)

    # ─── Sync workers (run inside asyncio.to_thread) ────────────────────────

    def _sync_create_pending(
        self, candidate_id: str, template_id: str, kind: str, ttl_hours: int
    ) -> ReplyToken:
        now = datetime.now(UTC)
        expires_at = now + timedelta(hours=ttl_hours)
        with self._db.get_session() as session:
            row = DiscordReplyToken(
                candidate_id=candidate_id,
                template_id=template_id,
                kind=kind,
                status="pending",
                created_at=now,
                expires_at=expires_at,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            snap = _row_to_snapshot(row)
        logger.info(
            "reply_tokens.created",
            token_id=snap.id,
            candidate_id=candidate_id,
            template_id=template_id,
            kind=kind,
            expires_at=expires_at.isoformat(),
        )
        return snap

    def _sync_match_reply(
        self,
        token_id: int,
        verdict: Verdict,
        reason: str | None,
        raw_message: str,
    ) -> ReplyTokenMatch | None:
        now = datetime.now(UTC)
        with self._db.get_session() as session:
            row = session.get(DiscordReplyToken, token_id)
            if row is None or row.status != "pending":
                logger.info(
                    "reply_tokens.match.no_pending_token",
                    token_id=token_id,
                    verdict=verdict,
                    actual_status=row.status if row is not None else None,
                )
                return None
            row.status = verdict
            row.replied_at = now
            row.reply_message = raw_message
            row.reply_verdict = verdict
            session.commit()
            session.refresh(row)
            snap = _row_to_snapshot(row)
        logger.info(
            "reply_tokens.matched",
            token_id=snap.id,
            candidate_id=snap.candidate_id,
            verdict=verdict,
            has_reason=reason is not None,
        )
        return ReplyTokenMatch(token=snap, verdict=verdict, reason=reason)

    def _sync_expire_stale(self, now: datetime, ttl_hours_arg: int | None) -> int:
        with self._db.get_session() as session:
            stmt = (
                update(DiscordReplyToken)
                .where(
                    DiscordReplyToken.status == "pending",
                    DiscordReplyToken.expires_at <= now,
                )
                .values(status="expired")
            )
            result = session.execute(stmt)
            session.commit()
            count = result.rowcount or 0
        if count:
            logger.info(
                "reply_tokens.expired_sweep",
                count=count,
                cutoff=now.isoformat(),
                ttl_hours_hint=ttl_hours_arg,
            )
        return count

    # ─── Internal helpers ───────────────────────────────────────────────────

    @staticmethod
    def _most_recent_pending(
        session: Session, candidate_id: str
    ) -> DiscordReplyToken | None:
        stmt = (
            select(DiscordReplyToken)
            .where(
                DiscordReplyToken.candidate_id == candidate_id,
                DiscordReplyToken.status == "pending",
            )
            .order_by(DiscordReplyToken.created_at.desc())
            .limit(1)
        )
        return session.scalar(stmt)


def _row_to_snapshot(row: DiscordReplyToken) -> ReplyToken:
    return ReplyToken(
        id=row.id,
        candidate_id=row.candidate_id,
        template_id=row.template_id,
        kind=row.kind,
        status=row.status,
        created_at=row.created_at,
        expires_at=row.expires_at,
        replied_at=row.replied_at,
        reply_message=row.reply_message,
        reply_verdict=row.reply_verdict,
    )
