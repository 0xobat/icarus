"""Postgres LISTEN/NOTIFY contracts for cross-cluster eventing.

Per blueprint and CLAUDE.md ("Cross-cluster reads via Postgres (with
LISTEN/NOTIFY for eventness), never service-to-service direct calls"),
the curation and execution clusters do not call each other directly.
They publish state changes through two named Postgres channels and
subscribers materialise their view from Postgres rows once notified.

Two channels:

  lake_roster_changed   lake-governor      -> decision-engine
                        (a candidate's LakeRoster state transitioned)
  executions_changed    decision-engine    -> lake-governor
                        (an executions row landed)

Both payloads carry a `version` field (currently "1.0.0") so consumers
can fail loudly on schema drift instead of silently dropping fields.

Producer side (`notify`) runs inside the existing sync SQLAlchemy
session/transaction so the NOTIFY is atomic with the row write that
caused it. On SQLite (dev), the call is a no-op + debug log.

Consumer side (`listen`) opens its own asyncpg connection because
sync SQLAlchemy sessions cannot park on a LISTEN socket. The async
generator reconnects on disconnect and validates every payload
against the channel's schema, skipping (with a logged warning)
anything malformed.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Literal

import asyncpg
import structlog
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import text
from sqlalchemy.orm import Session

from icarus.types.market import Chain

logger = structlog.get_logger(service="icarus.db.notify")

# ---------------------------------------------------------------------------
# Channel names — keep in sync with blueprint §"Cluster seam"
# ---------------------------------------------------------------------------

CHANNEL_LAKE_ROSTER_CHANGED = "lake_roster_changed"
CHANNEL_EXECUTIONS_CHANGED = "executions_changed"

LakeRosterState = Literal[
    "candidate",
    "blessed",
    "live",
    "demoted",
    "retired",
]

ExecutionsRowStatus = Literal[
    "confirmed",
    "failed",
    "reverted",
    "timeout",
    "rejected_by_guard",
]


# ---------------------------------------------------------------------------
# Payload schemas
# ---------------------------------------------------------------------------


class _StrictBase(BaseModel):
    """Frozen, extra=forbid base — matches `envelopes/*` convention."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class LakeRosterChangedPayload(_StrictBase):
    """Emitted when a LakeRoster row transitions between states.

    Subscribers (decision-engine) re-read the row from Postgres to learn
    the full new state; the payload only carries enough to scope the read.
    """

    version: Literal["1.0.0"] = "1.0.0"
    candidate_id: str = Field(min_length=1)
    template_id: str = Field(min_length=1)
    previous_state: LakeRosterState
    new_state: LakeRosterState
    transition_reason: str = Field(min_length=1)
    correlation_id: str = Field(min_length=1)
    emitted_at: datetime


class ExecutionsChangedPayload(_StrictBase):
    """Emitted when an executions row lands (success or terminal failure).

    Lake-governor subscribes and re-reads the executions row to update
    candidate performance / trigger demotions. The payload is intentionally
    thin — durable detail lives on the row."""

    version: Literal["1.0.0"] = "1.0.0"
    execution_id: str = Field(min_length=1)
    candidate_id: str | None = None
    template_id: str | None = None
    chain: Chain
    order_id: str = Field(min_length=8)
    status: ExecutionsRowStatus
    correlation_id: str = Field(min_length=1)
    emitted_at: datetime


# Channel -> payload schema registry (used by the listener for validation).
_CHANNEL_SCHEMAS: dict[str, type[_StrictBase]] = {
    CHANNEL_LAKE_ROSTER_CHANGED: LakeRosterChangedPayload,
    CHANNEL_EXECUTIONS_CHANGED: ExecutionsChangedPayload,
}


# ---------------------------------------------------------------------------
# Producer
# ---------------------------------------------------------------------------


def _is_postgres_session(session: Session) -> bool:
    """True iff the session's bind is a Postgres dialect."""
    bind = session.get_bind()
    if bind is None:
        return False
    name = getattr(bind.dialect, "name", "")
    return name.startswith("postgres")


def notify(session: Session, channel: str, payload: _StrictBase) -> None:
    """Publish `payload` on Postgres channel `channel` inside `session`.

    Runs as a `NOTIFY <channel>, <json>` statement in the session's
    current transaction so the notify commits with the row(s) that
    triggered it (or rolls back together — no orphan events).

    On SQLite (dev fallback) this is a no-op + debug log so that
    integration tests and local runs do not need a Postgres on the
    side. Production deployments are Postgres per the blueprint.

    Args:
        session: An open sync SQLAlchemy Session.
        channel: One of the `CHANNEL_*` constants in this module.
        payload: A pydantic payload model whose schema matches `channel`.

    Raises:
        ValueError: if `channel` is unknown or `payload` schema mismatches.
    """

    expected_schema = _CHANNEL_SCHEMAS.get(channel)
    if expected_schema is None:
        raise ValueError(f"unknown notify channel: {channel!r}")
    if not isinstance(payload, expected_schema):
        raise ValueError(
            f"payload type {type(payload).__name__} does not match channel "
            f"{channel!r} (expected {expected_schema.__name__})"
        )

    payload_json = payload.model_dump_json()

    if not _is_postgres_session(session):
        logger.debug(
            "notify.skipped_non_postgres",
            channel=channel,
            payload_bytes=len(payload_json),
        )
        return

    # Pass the JSON as a bound parameter so we never have to think about
    # quoting payload contents. Postgres lets us wrap it with pg_notify().
    session.execute(
        text("SELECT pg_notify(:channel, :payload)"),
        {"channel": channel, "payload": payload_json},
    )
    logger.debug(
        "notify.published",
        channel=channel,
        payload_bytes=len(payload_json),
    )


# ---------------------------------------------------------------------------
# Consumer
# ---------------------------------------------------------------------------


def _parse_payload(channel: str, raw: str) -> _StrictBase | None:
    """Validate a raw JSON payload string against `channel`'s schema.

    Returns the parsed pydantic model, or None if invalid (and logs a
    warning — never raises, so a single bad message cannot kill the
    consumer loop).
    """

    schema = _CHANNEL_SCHEMAS.get(channel)
    if schema is None:
        logger.warning("notify.unknown_channel", channel=channel)
        return None

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning(
            "notify.invalid_json",
            channel=channel,
            error=str(exc),
            raw_preview=raw[:200],
        )
        return None

    try:
        return schema.model_validate(data)
    except ValidationError as exc:
        logger.warning(
            "notify.schema_mismatch",
            channel=channel,
            schema=schema.__name__,
            error=str(exc),
        )
        return None


async def listen(
    connection_url: str,
    channel: str,
    *,
    reconnect_delay_seconds: float = 1.0,
) -> AsyncIterator[_StrictBase]:
    """Async generator yielding parsed payloads from a Postgres LISTEN socket.

    Opens its own asyncpg connection (sync SQLAlchemy can't park on
    LISTEN). Reconnects with backoff on disconnect. Invalid payloads
    are logged and skipped — the iterator never raises on a single
    bad message.

    The `connection_url` must be in asyncpg DSN form, e.g.
    ``postgresql://user:pass@host:5432/dbname``. SQLAlchemy-style
    ``postgresql+psycopg2://`` URLs are normalised by stripping the
    driver suffix.

    Args:
        connection_url: Postgres DSN.
        channel: One of the `CHANNEL_*` constants in this module.
        reconnect_delay_seconds: Initial backoff after a disconnect.
            Exponentially doubles up to 30s.

    Yields:
        Validated pydantic payload models for `channel`.
    """

    if channel not in _CHANNEL_SCHEMAS:
        raise ValueError(f"unknown listen channel: {channel!r}")

    dsn = _normalise_dsn(connection_url)
    queue: asyncio.Queue[str] = asyncio.Queue()
    backoff = reconnect_delay_seconds

    def _on_notify(
        _conn: asyncpg.Connection,
        _pid: int,
        _channel: str,
        payload: str,
    ) -> None:
        queue.put_nowait(payload)

    while True:
        conn: asyncpg.Connection | None = None
        try:
            conn = await asyncpg.connect(dsn)
            await conn.add_listener(channel, _on_notify)
            logger.info("notify.listening", channel=channel)
            backoff = reconnect_delay_seconds  # reset after successful connect

            while True:
                raw = await queue.get()
                parsed = _parse_payload(channel, raw)
                if parsed is not None:
                    yield parsed
        except (
            asyncpg.PostgresConnectionError,
            ConnectionError,
            OSError,
        ) as exc:
            logger.warning(
                "notify.disconnected",
                channel=channel,
                error=str(exc),
                retry_in_seconds=backoff,
            )
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)
        finally:
            if conn is not None:
                try:
                    await conn.remove_listener(channel, _on_notify)
                except Exception:
                    pass
                try:
                    await conn.close()
                except Exception:
                    pass


def _normalise_dsn(url: str) -> str:
    """Drop a SQLAlchemy driver suffix so asyncpg accepts the URL."""

    if url.startswith("postgresql+"):
        # e.g. postgresql+psycopg2://... -> postgresql://...
        scheme, _, rest = url.partition("://")
        base = scheme.split("+", 1)[0]
        return f"{base}://{rest}"
    return url


__all__ = [
    "CHANNEL_EXECUTIONS_CHANGED",
    "CHANNEL_LAKE_ROSTER_CHANGED",
    "ExecutionsChangedPayload",
    "ExecutionsRowStatus",
    "LakeRosterChangedPayload",
    "LakeRosterState",
    "listen",
    "notify",
]
