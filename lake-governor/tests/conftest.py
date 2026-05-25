"""Test fixtures for lake-governor — ephemeral SQLite + seeded candidates.

We use an on-disk tmp_path SQLite file (not ``:memory:``) because the
state machine opens a fresh sync ``Session`` per transition; an
in-memory DB would give each session a blank slate. The same pattern is
used in backtest-worker/tests/conftest.py.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from icarus.db.database import DatabaseConfig, DatabaseManager
from icarus.db.models import Candidate, LakeRoster


@pytest.fixture
def db(tmp_path):
    db_path = tmp_path / "lake.db"
    manager = DatabaseManager(DatabaseConfig(url=f"sqlite:///{db_path}"))
    manager.create_tables()
    try:
        yield manager
    finally:
        manager.close()


def _seed_candidate(
    manager: DatabaseManager,
    candidate_id: str,
    template_id: str = "TEMPLATE-001",
    state: str = "backtest",
    *,
    create_roster: bool = False,
    allocation_max_pct: Decimal = Decimal("0.10"),
    breaker_tripped: bool = False,
) -> None:
    """Insert a Candidate row and (optionally) a matching LakeRoster row."""
    with manager.get_session() as session:
        session.add(
            Candidate(
                candidate_id=candidate_id,
                template_id=template_id,
                template_version="1.0.0",
                params_json="{}",
                state=state,
                entered_state_at=datetime.now(UTC),
            )
        )
        if create_roster:
            session.add(
                LakeRoster(
                    candidate_id=candidate_id,
                    template_id=template_id,
                    state=state,
                    allocation_usd=Decimal("0"),
                    allocation_max_pct=allocation_max_pct,
                    last_transition_at=datetime.now(UTC),
                    breaker_tripped=breaker_tripped,
                )
            )
        session.commit()


@pytest.fixture
def seed_candidate():
    return _seed_candidate
