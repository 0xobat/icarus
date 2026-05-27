"""LakeRoster listener cache — Postgres LISTEN/NOTIFY → in-memory snapshot.

Cluster-seam invariant (per CLAUDE.md and blueprint):

    Cross-cluster reads via Postgres (with LISTEN/NOTIFY for eventness),
    never service-to-service direct calls.

The decision-engine needs the *live* set of candidates every cycle but
must not (a) HTTP-poll lake-governor or (b) re-SELECT from `lake_roster`
on every tick. The compromise this listener implements:

  1. On startup, do ONE materialising SELECT to populate the cache.
  2. Subscribe to the `lake_roster_changed` notify channel and, on each
     event, re-SELECT only the row that changed (or, on receiving the
     event, mark the cache stale and lazily reload on next read — both
     are correct; we use option 1: synchronous targeted reload because
     it keeps the per-cycle read cheap and lock-free).
  3. The cycle reads the cache synchronously per tick — O(N candidates),
     no I/O.

The listener is *resilient*: connection drops are handled by
`icarus.db.notify.listen`'s reconnect-with-backoff loop. On reconnect
we re-bootstrap the cache from a full SELECT to recover any
state changes missed during the gap.

If `decay_state_json` or `breaker_tripped` change, that's still a
`lake_roster_changed` event — the lake-governor is the sole writer.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING

import structlog
from icarus.db.models import LakeRoster
from icarus.db.notify import (
    CHANNEL_LAKE_ROSTER_CHANGED,
    LakeRosterChangedPayload,
    LakeRosterState,
    listen,
)
from sqlalchemy import select

# Per-state cap multiplier — see blueprint §"Capital allocation":
# live_capped is 0.5x the baseline cap stored on the row;
# live_mature gets the full baseline. All other states never reach the
# allocator (only LIVE_STATES are read), so their multiplier is academic.
_STATE_CAP_MULTIPLIER: dict[str, Decimal] = {
    "live_capped": Decimal("0.5"),
    "live_mature": Decimal("1.0"),
}

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import AsyncIterator

    from icarus.db.database import DatabaseManager

logger = structlog.get_logger(service="decision-engine.roster_listener")

LIVE_STATES: frozenset[LakeRosterState] = frozenset({"live_capped", "live_mature"})
"""Candidates in these states get a per-tick `evaluate()` call.

`paper_trade` lives in the lake-governor's shadow harness, not here.
`backtest` / `demoted_paper` / `archived` never reach the cycle.
"""


@dataclass(frozen=True)
class RosterEntry:
    """One row of the cache — what the cycle needs to drive a tick.

    Intentionally narrower than the full ORM row; the cycle does not
    care about `decay_state_json` etc. (those belong to lake-governor).
    `allocation_max_pct` is forwarded to the allocator as a per-candidate
    cap.
    """

    candidate_id: str
    template_id: str
    state: LakeRosterState
    allocation_usd: Decimal
    allocation_max_pct: Decimal
    breaker_tripped: bool


class RosterCache:
    """Thread-/task-safe snapshot of the live lake roster.

    Reads are synchronous and lock-free (we mutate by swapping the
    underlying tuple — readers see a consistent snapshot via the
    Python attribute load even without a lock). Writes happen only
    on the listener task.
    """

    def __init__(self) -> None:
        # Tuple, not list, so readers receive an immutable snapshot.
        self._entries: tuple[RosterEntry, ...] = ()

    def replace(self, entries: list[RosterEntry]) -> None:
        """Atomically swap the cache contents (writer side)."""
        # Single-assignment swap; Python guarantees this is atomic at
        # the bytecode level (STORE_ATTR is a single op).
        self._entries = tuple(entries)

    def all(self) -> tuple[RosterEntry, ...]:
        """Every cached entry, regardless of state."""
        return self._entries

    def live(self) -> tuple[RosterEntry, ...]:
        """Only entries in `LIVE_STATES` and not breaker-tripped."""
        return tuple(e for e in self._entries if e.state in LIVE_STATES and not e.breaker_tripped)

    def by_id(self, candidate_id: str) -> RosterEntry | None:
        for e in self._entries:
            if e.candidate_id == candidate_id:
                return e
        return None

    def __len__(self) -> int:
        return len(self._entries)


@dataclass
class RosterListener:
    """Listens on `lake_roster_changed` and keeps a `RosterCache` fresh.

    Lifecycle:
      * `await listener.bootstrap()` — one-time full-table load.
      * `listener.start()`           — spawn the consume task.
      * `listener.stop()`            — cancel the consume task.

    The listener owns its asyncpg connection lifecycle through
    `icarus.db.notify.listen`. The DatabaseManager is used only for
    the sync SELECT bootstrap and per-event row re-read.
    """

    db: DatabaseManager
    cache: RosterCache = field(default_factory=RosterCache)
    connection_url: str | None = None
    _task: asyncio.Task[None] | None = field(default=None, init=False, repr=False)

    async def bootstrap(self) -> None:
        """Materialise the cache from a single SELECT.

        Idempotent — calling again replaces the cache, useful after a
        notify-loop reconnect to recover from any missed events.
        """
        entries = await asyncio.to_thread(self._load_all)
        self.cache.replace(entries)
        logger.info("roster_bootstrap_complete", row_count=len(entries))

    def _load_all(self) -> list[RosterEntry]:
        with self.db.get_session() as session:
            rows = session.execute(select(LakeRoster)).scalars().all()
            return [self._row_to_entry(r) for r in rows]

    def _load_one(self, candidate_id: str) -> RosterEntry | None:
        with self.db.get_session() as session:
            row = (
                session.execute(
                    select(LakeRoster).where(LakeRoster.candidate_id == candidate_id)
                )
                .scalars()
                .first()
            )
            if row is None:
                return None
            return self._row_to_entry(row)

    @staticmethod
    def _row_to_entry(row: LakeRoster) -> RosterEntry:
        # The DB stores the *baseline* allocation_max_pct (set when the
        # candidate first entered paper_trade). The state machine does
        # NOT mutate this field on promotion, so the listener projects
        # the state-dependent cap multiplier into the value the
        # allocator consumes. This is the blueprint's 0.5x capital-
        # protection invariant for live_capped — without scaling here
        # the allocator would size live_capped at the full cap.
        baseline_max_pct = Decimal(str(row.allocation_max_pct))
        multiplier = _STATE_CAP_MULTIPLIER.get(row.state, Decimal("1"))
        return RosterEntry(
            candidate_id=row.candidate_id,
            template_id=row.template_id,
            # The DB column is String(32); narrow to the Literal type via cast.
            # Lake-governor enforces the state machine, so we trust the value.
            state=row.state,  # type: ignore[arg-type]
            allocation_usd=Decimal(str(row.allocation_usd)),
            allocation_max_pct=baseline_max_pct * multiplier,
            breaker_tripped=bool(row.breaker_tripped),
        )

    def apply_event(self, payload: LakeRosterChangedPayload) -> None:
        """Re-read the affected row and patch the cache.

        Pure-sync (the SELECT is cheap and runs in the listener task's
        event loop via `to_thread` from the caller). Used by the
        consume loop and exposed for tests so they can drive the
        cache without spinning a real Postgres.
        """
        candidate_id = payload.candidate_id
        new_entry = self._load_one(candidate_id)
        existing = {e.candidate_id: e for e in self.cache.all()}
        if new_entry is None:
            existing.pop(candidate_id, None)
        else:
            existing[candidate_id] = new_entry
        self.cache.replace(list(existing.values()))
        logger.debug(
            "roster_cache_patched",
            candidate_id=candidate_id,
            new_state=payload.new_state,
            previous_state=payload.previous_state,
            cache_size=len(existing),
        )

    def apply_event_dict(self, raw: dict) -> None:
        """Test helper: accept a raw dict, validate, apply."""
        payload = LakeRosterChangedPayload.model_validate(raw)
        self.apply_event(payload)

    async def _consume(self, source: AsyncIterator[LakeRosterChangedPayload]) -> None:
        async for payload in source:
            # Cache writes are sync; SELECT-per-event runs in a thread.
            try:
                await asyncio.to_thread(self.apply_event, payload)
            except Exception:
                # Single-event failure must never kill the listener loop.
                logger.exception(
                    "roster_event_apply_failed",
                    candidate_id=payload.candidate_id,
                )

    def start(self) -> None:
        """Spawn the consume task. Requires `connection_url` to be set."""
        if self._task is not None:
            raise RuntimeError("roster listener already started")
        if self.connection_url is None:
            raise RuntimeError(
                "RosterListener.connection_url must be set before start(); "
                "build a Postgres DSN and pass it in."
            )

        async def _runner() -> None:
            assert self.connection_url is not None  # for type checker
            source = self._listen_with_payload_type()
            await self._consume(source)

        self._task = asyncio.create_task(_runner(), name="roster-listener")

    async def _listen_with_payload_type(self) -> AsyncIterator[LakeRosterChangedPayload]:
        """Narrow the union returned by `notify.listen` to our payload type.

        Passes ``bootstrap`` as the ``on_connect`` callback so every
        reconnect re-materialises the cache — closing the window
        between asyncpg.connect and add_listener where a NOTIFY emitted
        in-between would be dropped.
        """
        assert self.connection_url is not None
        async for payload in listen(
            self.connection_url,
            CHANNEL_LAKE_ROSTER_CHANGED,
            on_connect=self.bootstrap,
        ):
            # `notify.listen` is typed as the union of all payload models
            # because it serves both channels; runtime guard for safety.
            if isinstance(payload, LakeRosterChangedPayload):
                yield payload

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except (asyncio.CancelledError, Exception):
            pass
        self._task = None


__all__ = [
    "LIVE_STATES",
    "RosterCache",
    "RosterEntry",
    "RosterListener",
]
