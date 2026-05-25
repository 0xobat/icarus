"""RosterListener cache tests — payload-driven cache updates.

We don't spin up Postgres in unit tests; instead we exercise the cache's
event-apply path with hand-built `LakeRosterChangedPayload`s, and we
stub the `db.get_session()` row reader so apply_event materialises a
known row. This mirrors the cluster-seam contract: notify → re-select.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from decision_engine.roster_listener import RosterCache, RosterEntry, RosterListener
from icarus.db.notify import LakeRosterChangedPayload


class _FakeDB:
    """In-memory stand-in for DatabaseManager — returns rows from a dict."""

    def __init__(self, rows: dict[str, dict]) -> None:
        self._rows = rows

    def get_session(self):
        return _FakeSession(self._rows)


class _FakeSession:
    def __init__(self, rows: dict[str, dict]) -> None:
        self._rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, stmt):
        # Inspect WHERE clauses on the stmt to figure out if a single
        # candidate is requested; fall back to all-rows otherwise.
        candidate_id = None
        try:
            whereclauses = list(stmt.whereclause.get_children())
            for clause in whereclauses:
                # right side is typically a BindParameter on a string equality
                if hasattr(clause, "value"):
                    candidate_id = clause.value
        except Exception:
            pass

        if candidate_id is not None:
            row = self._rows.get(candidate_id)
            rows = [row] if row is not None else []
        else:
            rows = list(self._rows.values())
        return _FakeResult([_dict_to_row(r) for r in rows])


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None


class _Row:
    pass


def _dict_to_row(d: dict) -> _Row:
    r = _Row()
    for k, v in d.items():
        setattr(r, k, v)
    return r


def _row_dict(candidate_id: str, state: str, *, breaker_tripped: bool = False) -> dict:
    return {
        "candidate_id": candidate_id,
        "template_id": "LEND-001",
        "state": state,
        "allocation_usd": Decimal("0"),
        "allocation_max_pct": Decimal("0.1"),
        "breaker_tripped": breaker_tripped,
    }


def _payload(
    candidate_id: str,
    previous_state: str,
    new_state: str,
) -> LakeRosterChangedPayload:
    return LakeRosterChangedPayload(
        candidate_id=candidate_id,
        template_id="LEND-001",
        previous_state=previous_state,  # type: ignore[arg-type]
        new_state=new_state,  # type: ignore[arg-type]
        transition_reason="test",
        correlation_id="corr-1",
        emitted_at=datetime.now(UTC),
    )


def test_cache_filters_to_live_states_only():
    cache = RosterCache()
    cache.replace(
        [
            RosterEntry("A", "T", "live_capped", Decimal("0"), Decimal("0.1"), False),
            RosterEntry("B", "T", "paper_trade", Decimal("0"), Decimal("0.1"), False),
            RosterEntry("C", "T", "live_mature", Decimal("0"), Decimal("0.1"), False),
            RosterEntry("D", "T", "live_capped", Decimal("0"), Decimal("0.1"), True),
        ]
    )
    live_ids = {e.candidate_id for e in cache.live()}
    assert live_ids == {"A", "C"}  # B is paper_trade; D has breaker_tripped


def test_cache_by_id_returns_entry_or_none():
    cache = RosterCache()
    cache.replace([RosterEntry("X", "T", "live_capped", Decimal("0"), Decimal("0.1"), False)])
    assert cache.by_id("X") is not None
    assert cache.by_id("Y") is None


def test_apply_event_inserts_new_row_into_cache():
    db = _FakeDB(rows={"CAND-1": _row_dict("CAND-1", "live_capped")})
    listener = RosterListener(db=db)  # type: ignore[arg-type]

    payload = _payload("CAND-1", "paper_trade", "live_capped")
    listener.apply_event(payload)

    assert len(listener.cache) == 1
    entry = listener.cache.by_id("CAND-1")
    assert entry is not None
    assert entry.state == "live_capped"


def test_apply_event_updates_existing_row():
    db = _FakeDB(rows={"CAND-1": _row_dict("CAND-1", "live_mature")})
    listener = RosterListener(db=db)  # type: ignore[arg-type]
    listener.cache.replace(
        [RosterEntry("CAND-1", "LEND-001", "live_capped", Decimal("0"), Decimal("0.1"), False)]
    )

    payload = _payload("CAND-1", "live_capped", "live_mature")
    listener.apply_event(payload)

    entry = listener.cache.by_id("CAND-1")
    assert entry is not None
    assert entry.state == "live_mature"


def test_apply_event_removes_archived_row_from_cache():
    # Simulate: DB no longer returns the row (or returns it in archived
    # state — caller decides). We simulate by removing it from the FakeDB.
    db = _FakeDB(rows={})  # row deleted upstream
    listener = RosterListener(db=db)  # type: ignore[arg-type]
    listener.cache.replace(
        [RosterEntry("CAND-1", "LEND-001", "live_capped", Decimal("0"), Decimal("0.1"), False)]
    )

    payload = _payload("CAND-1", "live_capped", "archived")
    listener.apply_event(payload)

    assert listener.cache.by_id("CAND-1") is None


def test_bootstrap_loads_all_rows():
    import asyncio

    db = _FakeDB(
        rows={
            "A": _row_dict("A", "live_capped"),
            "B": _row_dict("B", "live_mature"),
        }
    )
    listener = RosterListener(db=db)  # type: ignore[arg-type]
    asyncio.run(listener.bootstrap())
    ids = {e.candidate_id for e in listener.cache.all()}
    assert ids == {"A", "B"}
