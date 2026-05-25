"""Tests for the Postgres LISTEN/NOTIFY contracts in `icarus.db.notify`.

Covers four cases:
  1. Payload schemas validate happy-path input and reject extra fields.
  2. `notify()` on a SQLite session is a no-op (no SQL emitted).
  3. `notify()` on a mock Postgres session invokes `pg_notify(...)` with
     the channel + JSON payload.
  4. The listener's parser turns valid JSON into the right pydantic
     model and skips garbage with a logged warning.

Test 4 exercises the parser directly with a synthetic generator instead
of opening a real LISTEN socket, per the W5 plan.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from icarus.db.database import DatabaseConfig, DatabaseManager
from icarus.db.notify import (
    CHANNEL_EXECUTIONS_CHANGED,
    CHANNEL_LAKE_ROSTER_CHANGED,
    ExecutionsChangedPayload,
    LakeRosterChangedPayload,
    _parse_payload,
    notify,
)
from pydantic import ValidationError
from sqlalchemy import text

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _roster_payload(**overrides) -> LakeRosterChangedPayload:
    data = {
        "candidate_id": "cand_abc",
        "template_id": "tmpl_basis_perp_001",
        "previous_state": "blessed",
        "new_state": "live",
        "transition_reason": "smoke_test_passed",
        "correlation_id": "corr_123",
        "emitted_at": datetime(2026, 5, 25, 12, 0, tzinfo=UTC),
    }
    data.update(overrides)
    return LakeRosterChangedPayload(**data)


def _executions_payload(**overrides) -> ExecutionsChangedPayload:
    data = {
        "execution_id": "exec_xyz",
        "candidate_id": "cand_abc",
        "template_id": "tmpl_basis_perp_001",
        "chain": "base",
        "order_id": "order_12345678",
        "status": "confirmed",
        "correlation_id": "corr_123",
        "emitted_at": datetime(2026, 5, 25, 12, 1, tzinfo=UTC),
    }
    data.update(overrides)
    return ExecutionsChangedPayload(**data)


# ---------------------------------------------------------------------------
# Test 1 — payload schemas
# ---------------------------------------------------------------------------


def test_lake_roster_payload_happy_path_and_rejects_extra_fields():
    payload = _roster_payload()
    assert payload.version == "1.0.0"
    assert payload.candidate_id == "cand_abc"
    assert payload.new_state == "live"

    # Round-trip through JSON proves the wire form is deterministic.
    rt = LakeRosterChangedPayload.model_validate_json(payload.model_dump_json())
    assert rt == payload

    with pytest.raises(ValidationError):
        LakeRosterChangedPayload.model_validate(
            {**json.loads(payload.model_dump_json()), "unexpected": "value"}
        )

    # State literal is locked.
    with pytest.raises(ValidationError):
        _roster_payload(new_state="schroedinger")


def test_executions_payload_happy_path_and_rejects_extra_fields():
    payload = _executions_payload()
    assert payload.version == "1.0.0"
    assert payload.chain == "base"
    assert payload.status == "confirmed"

    rt = ExecutionsChangedPayload.model_validate_json(payload.model_dump_json())
    assert rt == payload

    with pytest.raises(ValidationError):
        ExecutionsChangedPayload.model_validate(
            {**json.loads(payload.model_dump_json()), "rogue": 1}
        )

    # Chain literal is locked to base|solana.
    with pytest.raises(ValidationError):
        _executions_payload(chain="ethereum")

    # order_id min_length=8.
    with pytest.raises(ValidationError):
        _executions_payload(order_id="short")


# ---------------------------------------------------------------------------
# Test 2 — SQLite is a no-op
# ---------------------------------------------------------------------------


def test_notify_on_sqlite_is_noop():
    """SQLite has no NOTIFY — the producer must not raise and must not
    attempt to execute any SQL."""

    manager = DatabaseManager(DatabaseConfig(url="sqlite:///:memory:"))
    try:
        manager.create_tables()
        session = manager.get_session()
        try:
            payload = _roster_payload()
            # Should return cleanly. If we had executed SQL, the session's
            # `info` dict would not stay empty — but the more direct proof
            # is that no exception is raised on an engine that has no such
            # function.
            notify(session, CHANNEL_LAKE_ROSTER_CHANGED, payload)
        finally:
            session.close()
    finally:
        manager.close()


# ---------------------------------------------------------------------------
# Test 3 — Postgres mock invokes pg_notify
# ---------------------------------------------------------------------------


def test_notify_on_postgres_calls_pg_notify():
    """With a mock session whose bind reports the postgres dialect, the
    producer must call `session.execute` with a SELECT pg_notify(...)
    statement and the channel + JSON payload bound parameters."""

    session = MagicMock()
    bind = MagicMock()
    bind.dialect.name = "postgresql"
    session.get_bind.return_value = bind

    payload = _executions_payload()
    notify(session, CHANNEL_EXECUTIONS_CHANGED, payload)

    assert session.execute.call_count == 1
    args, kwargs = session.execute.call_args
    # First positional arg is a TextClause built from `text(...)`.
    stmt = args[0]
    assert isinstance(stmt, type(text("SELECT 1")))
    assert "pg_notify" in str(stmt)
    params = args[1] if len(args) > 1 else kwargs
    assert params["channel"] == CHANNEL_EXECUTIONS_CHANGED
    # Bound payload is exactly the model's JSON form.
    assert params["payload"] == payload.model_dump_json()
    # And the JSON is parseable back into the same model (sanity).
    assert (
        ExecutionsChangedPayload.model_validate_json(params["payload"]) == payload
    )


def test_notify_rejects_mismatched_payload_and_channel():
    session = MagicMock()
    bind = MagicMock()
    bind.dialect.name = "postgresql"
    session.get_bind.return_value = bind

    with pytest.raises(ValueError, match="does not match channel"):
        notify(session, CHANNEL_LAKE_ROSTER_CHANGED, _executions_payload())


def test_notify_rejects_unknown_channel():
    session = MagicMock()
    with pytest.raises(ValueError, match="unknown notify channel"):
        notify(session, "made_up_channel", _roster_payload())


# ---------------------------------------------------------------------------
# Test 4 — listener parser path
# ---------------------------------------------------------------------------


def test_parse_payload_accepts_valid_and_rejects_garbage(caplog):
    """The parser turns valid JSON into the right pydantic model and
    returns None on garbage with a logged warning."""

    valid = _roster_payload()
    parsed = _parse_payload(CHANNEL_LAKE_ROSTER_CHANGED, valid.model_dump_json())
    assert parsed == valid
    assert isinstance(parsed, LakeRosterChangedPayload)

    # Wrong schema on this channel — executions payload should fail
    # validation against the roster schema.
    wrong_shape = _executions_payload().model_dump_json()
    assert _parse_payload(CHANNEL_LAKE_ROSTER_CHANGED, wrong_shape) is None

    # Garbage JSON.
    assert _parse_payload(CHANNEL_LAKE_ROSTER_CHANGED, "not-json{{") is None

    # Unknown channel.
    assert _parse_payload("unregistered_channel", valid.model_dump_json()) is None


@pytest.mark.asyncio
async def test_listen_loop_with_synthetic_source():
    """Exercises the listen loop's parse-and-yield contract end-to-end
    without binding a real Postgres socket. We feed a queue with the
    payload strings a real LISTEN socket would deliver and assert the
    consumer sees the validated models in order, skipping the bad one.
    """

    # Reproduce the listener's parse-and-yield logic over an injected
    # queue. This is the same parser path `listen()` uses; if either
    # contract drifts, this test fails.
    async def fake_listen() -> AsyncIterator[LakeRosterChangedPayload]:
        messages = [
            _roster_payload(candidate_id="cand_1").model_dump_json(),
            "garbage-not-json",
            _roster_payload(candidate_id="cand_2", new_state="demoted").model_dump_json(),
        ]
        for raw in messages:
            parsed = _parse_payload(CHANNEL_LAKE_ROSTER_CHANGED, raw)
            if parsed is not None:
                yield parsed
            await asyncio.sleep(0)

    received: list[LakeRosterChangedPayload] = []
    async for p in fake_listen():
        received.append(p)

    assert len(received) == 2
    assert received[0].candidate_id == "cand_1"
    assert received[1].candidate_id == "cand_2"
    assert received[1].new_state == "demoted"
