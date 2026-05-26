"""Unit tests for :class:`ReplyTokenStore`.

Covers the create / match / expire trinity plus the regex-parser
rejection cases. SQLite in-memory backs all tests.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from icarus.db.database import DatabaseConfig, DatabaseManager
from icarus.db.models import DiscordReplyToken
from icarus.discord import ReplyTokenStore
from sqlalchemy import select


@pytest.fixture
def db_manager(tmp_path):
    # File-backed SQLite (not ``:memory:``) because reply_tokens uses
    # ``asyncio.to_thread`` which opens a fresh session per call; an
    # in-memory connection-scoped DB would give each session a blank
    # slate. Same pattern as lake-governor/tests/conftest.py.
    db_path = tmp_path / "discord.db"
    mgr = DatabaseManager(DatabaseConfig(url=f"sqlite:///{db_path}", echo=False))
    mgr.create_tables()
    yield mgr
    mgr.close()


@pytest.fixture
def store(db_manager):
    return ReplyTokenStore(db_manager)


# ─── create_pending ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_pending_inserts_pending_row(db_manager, store):
    token = await store.create_pending(
        candidate_id="c-abc",
        template_id="LEND-001",
        kind="promotion_request",
    )
    assert token.status == "pending"
    assert token.candidate_id == "c-abc"
    assert token.template_id == "LEND-001"
    assert token.kind == "promotion_request"
    assert token.expires_at > token.created_at
    # Default TTL is 24h.
    delta = token.expires_at - token.created_at
    assert 23.9 <= delta.total_seconds() / 3600 <= 24.1

    # Round-trip the row from the DB to confirm persistence.
    with db_manager.get_session() as session:
        row = session.scalar(
            select(DiscordReplyToken).where(DiscordReplyToken.id == token.id)
        )
        assert row is not None
        assert row.status == "pending"
        assert row.candidate_id == "c-abc"


@pytest.mark.asyncio
async def test_create_pending_rejects_unknown_kind(store):
    with pytest.raises(ValueError, match="unknown reply-token kind"):
        await store.create_pending(
            candidate_id="c-abc",
            template_id="LEND-001",
            kind="nonsense_kind",
        )


# ─── match_reply: positive cases ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_match_reply_approves_pending_token(db_manager, store):
    token = await store.create_pending(
        candidate_id="c-abc",
        template_id="LEND-001",
        kind="promotion_request",
    )

    match = await store.match_reply(message="APPROVE c-abc")

    assert match is not None
    assert match.verdict == "approved"
    assert match.reason is None
    assert match.token.id == token.id
    assert match.token.status == "approved"
    assert match.token.reply_message == "APPROVE c-abc"
    assert match.token.reply_verdict == "approved"
    assert match.token.replied_at is not None

    # DB confirms the flip is durable.
    with db_manager.get_session() as session:
        row = session.scalar(
            select(DiscordReplyToken).where(DiscordReplyToken.id == token.id)
        )
        assert row is not None
        assert row.status == "approved"
        assert row.reply_verdict == "approved"


@pytest.mark.asyncio
async def test_match_reply_rejects_with_reason(db_manager, store):
    token = await store.create_pending(
        candidate_id="c-abc",
        template_id="LEND-001",
        kind="promotion_request",
    )

    reason_text = "returns concentrated in 4-day window"
    match = await store.match_reply(message=f"REJECT c-abc {reason_text}")

    assert match is not None
    assert match.verdict == "rejected"
    assert match.reason == reason_text
    assert match.token.id == token.id
    assert match.token.status == "rejected"
    assert match.token.reply_verdict == "rejected"
    assert reason_text in (match.token.reply_message or "")


@pytest.mark.asyncio
async def test_match_reply_case_insensitive_verb(store):
    await store.create_pending(
        candidate_id="c-xyz",
        template_id="LEND-001",
        kind="promotion_request",
    )
    match = await store.match_reply(message="approve c-xyz")
    assert match is not None
    assert match.verdict == "approved"


@pytest.mark.asyncio
async def test_match_reply_matches_most_recent_pending(db_manager, store):
    """Two pending tokens for the same candidate → most recent wins."""
    older = await store.create_pending(
        candidate_id="c-abc",
        template_id="LEND-001",
        kind="promotion_request",
    )
    # Backdate the older token by 1h so order-by-created_at-desc is unambiguous.
    with db_manager.get_session() as session:
        row = session.scalar(
            select(DiscordReplyToken).where(DiscordReplyToken.id == older.id)
        )
        assert row is not None
        row.created_at = row.created_at - timedelta(hours=1)
        session.commit()

    newer = await store.create_pending(
        candidate_id="c-abc",
        template_id="LEND-001",
        kind="promotion_request",
    )

    match = await store.match_reply(message="APPROVE c-abc")
    assert match is not None
    assert match.token.id == newer.id

    # Older token remains pending.
    with db_manager.get_session() as session:
        row = session.scalar(
            select(DiscordReplyToken).where(DiscordReplyToken.id == older.id)
        )
        assert row is not None
        assert row.status == "pending"


# ─── match_reply: negative cases ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_match_reply_returns_none_for_junk_message(store):
    await store.create_pending(
        candidate_id="c-abc",
        template_id="LEND-001",
        kind="promotion_request",
    )
    assert await store.match_reply(message="lol") is None
    assert await store.match_reply(message="") is None
    assert await store.match_reply(message="approve") is None  # no candidate id
    assert await store.match_reply(message="MAYBE c-abc") is None  # bad verb


@pytest.mark.asyncio
async def test_match_reply_returns_none_when_candidate_has_no_pending_token(store):
    await store.create_pending(
        candidate_id="c-abc",
        template_id="LEND-001",
        kind="promotion_request",
    )
    # Different candidate id → no pending token.
    assert await store.match_reply(message="APPROVE x-no-such-token") is None


@pytest.mark.asyncio
async def test_match_reply_returns_none_after_token_already_terminal(
    db_manager, store
):
    """Once approved/rejected, the same candidate's token won't re-match."""
    await store.create_pending(
        candidate_id="c-abc",
        template_id="LEND-001",
        kind="promotion_request",
    )
    first = await store.match_reply(message="APPROVE c-abc")
    assert first is not None
    # Now there are no more pending tokens for c-abc.
    second = await store.match_reply(message="APPROVE c-abc")
    assert second is None


# ─── expire_stale ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_expire_stale_flips_overdue_pending_tokens(db_manager, store):
    fresh = await store.create_pending(
        candidate_id="c-fresh",
        template_id="LEND-001",
        kind="promotion_request",
    )
    stale = await store.create_pending(
        candidate_id="c-stale",
        template_id="LEND-001",
        kind="promotion_request",
    )

    # Backdate the stale token: created 30h ago, expires 6h ago.
    with db_manager.get_session() as session:
        row = session.scalar(
            select(DiscordReplyToken).where(DiscordReplyToken.id == stale.id)
        )
        assert row is not None
        row.created_at = row.created_at - timedelta(hours=30)
        row.expires_at = row.expires_at - timedelta(hours=30)
        session.commit()

    now = datetime.now(UTC)
    expired_count = await store.expire_stale(now=now, ttl_hours=24)
    assert expired_count == 1

    with db_manager.get_session() as session:
        stale_row = session.scalar(
            select(DiscordReplyToken).where(DiscordReplyToken.id == stale.id)
        )
        fresh_row = session.scalar(
            select(DiscordReplyToken).where(DiscordReplyToken.id == fresh.id)
        )
        assert stale_row is not None and stale_row.status == "expired"
        assert fresh_row is not None and fresh_row.status == "pending"


@pytest.mark.asyncio
async def test_expire_stale_idempotent_when_no_stale_tokens(store):
    await store.create_pending(
        candidate_id="c-abc",
        template_id="LEND-001",
        kind="promotion_request",
    )
    count = await store.expire_stale(now=datetime.now(UTC))
    assert count == 0


@pytest.mark.asyncio
async def test_expire_stale_does_not_touch_already_terminal_rows(db_manager, store):
    """Approved tokens stay approved; only pending rows can be swept."""
    await store.create_pending(
        candidate_id="c-abc",
        template_id="LEND-001",
        kind="promotion_request",
    )
    await store.match_reply(message="APPROVE c-abc")

    # Even with a far-future cutoff, the approved row is untouched.
    far_future = datetime.now(UTC) + timedelta(days=365)
    count = await store.expire_stale(now=far_future)
    assert count == 0

    with db_manager.get_session() as session:
        rows = list(session.scalars(select(DiscordReplyToken)))
        assert len(rows) == 1
        assert rows[0].status == "approved"
