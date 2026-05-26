"""Runner end-to-end test.

Wires a stub adapter + toy template + in-memory SQLite, runs a
``SearchJob`` through ``run_one_job``, then re-opens the DB and asserts
the expected number of rows landed in ``parameter_search_results`` and
``walk_forward_results``.

We bypass the on-disk ``TemplateRegistry`` (which expects manifest +
evaluate.py on disk and AST-lints them) by injecting a minimal fake
registry with a single ``Template`` whose ``evaluate`` is our toy
function. The on-disk registry is exercised in `lib/tests/` against
real template directories — exercising it here too would test the same
code twice.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from backtest_worker.runner import (
    deserialise_grid_search_config,
    run_one_job,
)
from icarus.db.models import (
    ParameterSearchResult as ParameterSearchResultModel,
)
from icarus.db.models import (
    WalkForwardResult as WalkForwardResultModel,
)
from icarus.envelopes.research import SearchJob
from sqlalchemy import select


@dataclass(frozen=True)
class _FakeTemplate:
    id: str
    evaluate: Any


class _FakeRegistry:
    """Minimal registry interface — only ``by_id`` is called by the runner."""

    def __init__(self, template: _FakeTemplate) -> None:
        self._template = template

    def by_id(self, template_id: str) -> _FakeTemplate:
        if template_id != self._template.id:
            raise KeyError(template_id)
        return self._template


def _build_job(template_id: str) -> SearchJob:
    return SearchJob(
        version="1.0.0",
        job_id="testjob1",
        enqueued_at=datetime(2026, 1, 1, tzinfo=UTC),
        requested_by="pytest",
        correlation_id="corr-1",
        template_id=template_id,
        template_version="0.1.0",
        deadline_unix=int(datetime(2099, 1, 1, tzinfo=UTC).timestamp()),
        search_config={
            "kind": "grid",
            "template_id": template_id,
            "template_version": "0.1.0",
            "asset_universe": ["BASE"],
            "chain": "base",
            "backtest_start": "2026-01-01T00:00:00+00:00",
            "backtest_end": "2026-04-01T00:00:00+00:00",
            "walk_forward": [60, 15, 3],
            "turnover_lambda": "0.01",
            "top_k": 2,
            "param_ranges": {"period": [3, 5, 7], "threshold": [-1, 0, 1]},
        },
    )


def test_deserialise_grid_search_config_round_trip():
    raw = {
        "kind": "grid",
        "template_id": "TEST-001",
        "template_version": "0.1.0",
        "asset_universe": ["BASE"],
        "chain": "base",
        "backtest_start": "2026-01-01T00:00:00+00:00",
        "backtest_end": "2026-04-01T00:00:00+00:00",
        "walk_forward": [60, 15, 3],
        "turnover_lambda": "0.01",
        "top_k": 3,
        "param_ranges": {"period": [3, 5]},
    }
    cfg = deserialise_grid_search_config(raw)
    assert cfg.template_id == "TEST-001"
    assert cfg.chain == "base"
    assert cfg.walk_forward == (60, 15, 3)
    assert cfg.turnover_lambda == Decimal("0.01")
    assert dict(cfg.param_ranges) == {"period": [3, 5]}


def test_deserialise_grid_rejects_bayesian_envelope():
    """The grid-specific deserialiser still rejects non-grid envelopes —
    callers wanting kind dispatch must route through ``deserialise_search_config``."""
    with pytest.raises(ValueError, match="expected kind='grid'"):
        deserialise_grid_search_config({"kind": "bayesian"})


@pytest.mark.asyncio
async def test_run_one_job_persists_search_and_walk_forward(
    in_memory_db, stub_adapter, toy_evaluate
):
    registry = _FakeRegistry(_FakeTemplate(id="TEST-001", evaluate=toy_evaluate))
    job = _build_job("TEST-001")

    outcome = await run_one_job(
        job, registry=registry, adapter=stub_adapter, db=in_memory_db
    )

    # 3x3 = 9 search rows; top_k=2 -> 2 candidates; 6 windows per candidate = 12.
    assert outcome.n_search_rows == 9
    assert outcome.n_top_k == 2
    assert outcome.n_walk_forward_rows == 12

    # Re-open a session and verify rows are in the DB.
    session = in_memory_db.get_session()
    try:
        search_db_rows = session.scalars(select(ParameterSearchResultModel)).all()
        wf_db_rows = session.scalars(select(WalkForwardResultModel)).all()
    finally:
        session.close()

    assert len(search_db_rows) == 9
    assert len(wf_db_rows) == 12

    # is_top_k flag should be set on exactly 2 search rows.
    top_k_count = sum(1 for r in search_db_rows if r.is_top_k)
    assert top_k_count == 2

    # Candidate ids on walk-forward rows are deterministic — every
    # walk-forward row's candidate_id matches one of the top-K rows.
    candidate_ids = {r.candidate_id for r in wf_db_rows}
    assert len(candidate_ids) == 2
