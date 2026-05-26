"""Bayesian-search unit tests.

Covers the four scope items from the W10 brief:
  (a) 3-param config + n_trials=10 → exactly 10 ParameterSearchResult rows
  (b) Same seed twice → byte-identical results (TPE reproducibility)
  (c) Param spec types respected: continuous → suggest_float,
      grid/categorical → suggest_categorical
  (d) ``run_one_job`` with ``kind="bayesian"`` dispatches to the bayesian
      engine and persists rows correctly (in-memory SQLite + stub adapter)

The conftest stub adapter + toy evaluate function are reused unchanged
from the grid tests — both engines drive the same simulator code path,
so any divergence in metrics here would point at the bayesian engine
itself, not the simulator. That's the test isolation we want.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock

import pytest
from backtest_worker.bayesian_search import (
    _suggest_param,
    run_bayesian_search,
)
from backtest_worker.runner import (
    deserialise_bayesian_search_config,
    deserialise_search_config,
    run_one_job,
)
from icarus.db.models import (
    ParameterSearchResult as ParameterSearchResultModel,
)
from icarus.envelopes.research import SearchJob
from icarus.protocols.backtest import (
    BayesianCategoricalRange,
    BayesianContinuousRange,
    BayesianGridRange,
    BayesianSearchConfig,
)
from sqlalchemy import select

# ─────────── Shared fakes (mirror test_runner_unit.py) ───────────


@dataclass(frozen=True)
class _FakeTemplate:
    id: str
    evaluate: Any


class _FakeRegistry:
    def __init__(self, template: _FakeTemplate) -> None:
        self._template = template

    def by_id(self, template_id: str) -> _FakeTemplate:
        if template_id != self._template.id:
            raise KeyError(template_id)
        return self._template


def _build_bayesian_config(
    *, n_trials: int = 10, seed: int = 42
) -> BayesianSearchConfig:
    """3-param Bayesian config covering all three range types.

    The toy template only reads ``period`` and ``threshold``; ``venue``
    is ignored by the simulator but exercises the categorical suggest
    path through Optuna.
    """
    return BayesianSearchConfig(
        template_id="TEST-001",
        template_version="0.1.0",
        asset_universe=("BASE",),
        chain="base",
        backtest_start=datetime(2026, 1, 1, tzinfo=UTC),
        backtest_end=datetime(2026, 4, 1, tzinfo=UTC),
        walk_forward=(60, 15, 3),
        turnover_lambda=Decimal("0.01"),
        top_k=2,
        param_ranges={
            "period": BayesianContinuousRange(
                low=Decimal("2"), high=Decimal("10")
            ),
            "threshold": BayesianGridRange(values=[-1, 0, 1]),
            "venue": BayesianCategoricalRange(choices=["aave", "morpho"]),
        },
        n_trials=n_trials,
        seed=seed,
    )


# ─────────── (a) n_trials honoured ───────────


@pytest.mark.asyncio
async def test_run_bayesian_search_produces_n_trials_rows(
    stub_adapter, toy_evaluate
):
    """A 3-param config + n_trials=10 produces exactly 10 result rows."""
    config = _build_bayesian_config(n_trials=10)
    rows = await run_bayesian_search(
        config, evaluate_fn=toy_evaluate, adapter=stub_adapter
    )
    assert len(rows) == 10
    # Every row carries the template identity and a populated params dict.
    for r in rows:
        assert r.template_id == "TEST-001"
        assert r.template_version == "0.1.0"
        assert set(r.params.keys()) == {"period", "threshold", "venue"}
        assert r.sharpe == r.sharpe  # not NaN
        assert r.max_dd >= 0.0
        assert r.compute_seconds >= 0.0


# ─────────── (b) Reproducibility ───────────


@pytest.mark.asyncio
async def test_run_bayesian_search_seeded_is_reproducible(
    stub_adapter, toy_evaluate
):
    """Same seed → identical params sequence and identical metrics.

    Compute time naturally jitters, so we only assert on the deterministic
    quantities (params + metrics derived from the simulator).
    """
    config_a = _build_bayesian_config(n_trials=8, seed=1234)
    config_b = _build_bayesian_config(n_trials=8, seed=1234)

    rows_a = await run_bayesian_search(
        config_a, evaluate_fn=toy_evaluate, adapter=stub_adapter
    )
    rows_b = await run_bayesian_search(
        config_b, evaluate_fn=toy_evaluate, adapter=stub_adapter
    )

    assert len(rows_a) == len(rows_b) == 8
    for a, b in zip(rows_a, rows_b, strict=True):
        assert a.params == b.params
        assert a.sharpe == b.sharpe
        assert a.deflated_sharpe == b.deflated_sharpe
        assert a.max_dd == b.max_dd
        assert a.turnover == b.turnover

    # Different seed → different exploration path (sanity that the seed
    # actually moves the sampler — guards against an inadvertent global
    # state leak that would mask the reproducibility property).
    config_c = _build_bayesian_config(n_trials=8, seed=9999)
    rows_c = await run_bayesian_search(
        config_c, evaluate_fn=toy_evaluate, adapter=stub_adapter
    )
    assert [r.params for r in rows_a] != [r.params for r in rows_c]


# ─────────── (c) Param spec dispatch ───────────


def test_suggest_param_continuous_calls_suggest_float():
    """Continuous range routes to ``trial.suggest_float`` with the right bounds."""
    trial = MagicMock()
    trial.suggest_float.return_value = 4.2
    spec = BayesianContinuousRange(low=Decimal("2"), high=Decimal("10"))
    out = _suggest_param(trial, "period", spec)
    trial.suggest_float.assert_called_once_with("period", 2.0, 10.0, log=False)
    assert out == 4.2


def test_suggest_param_continuous_log_scale_propagates():
    """``scale="log"`` flips on Optuna's log-uniform sampling."""
    trial = MagicMock()
    trial.suggest_float.return_value = 0.001
    spec = BayesianContinuousRange(
        low=Decimal("0.0001"), high=Decimal("1.0"), scale="log"
    )
    _suggest_param(trial, "lr", spec)
    trial.suggest_float.assert_called_once_with("lr", 0.0001, 1.0, log=True)


def test_suggest_param_grid_calls_suggest_categorical():
    """Grid range routes to ``trial.suggest_categorical`` over its values."""
    trial = MagicMock()
    trial.suggest_categorical.return_value = 5
    spec = BayesianGridRange(values=[3, 5, 7])
    out = _suggest_param(trial, "period", spec)
    trial.suggest_categorical.assert_called_once_with("period", [3, 5, 7])
    assert out == 5


def test_suggest_param_categorical_calls_suggest_categorical():
    """Categorical range routes to ``suggest_categorical`` over its string choices."""
    trial = MagicMock()
    trial.suggest_categorical.return_value = "aave"
    spec = BayesianCategoricalRange(choices=["aave", "morpho"])
    out = _suggest_param(trial, "venue", spec)
    trial.suggest_categorical.assert_called_once_with("venue", ["aave", "morpho"])
    assert out == "aave"


def test_suggest_param_grid_decimal_values_coerced():
    """Decimal grid values are coerced to floats (Optuna-categorical-safe)
    while exact-precision values that don't survive float round-trip
    fall back to string to preserve the value in ``params_json``."""
    trial = MagicMock()
    trial.suggest_categorical.return_value = 0.5
    spec = BayesianGridRange(
        values=[Decimal("0.25"), Decimal("0.5"), Decimal("0.75")]
    )
    _suggest_param(trial, "alpha", spec)
    # Each of these survives float round-trip exactly.
    trial.suggest_categorical.assert_called_once_with("alpha", [0.25, 0.5, 0.75])


# ─────────── (d) Runner dispatch through bayesian ───────────


def _build_bayesian_job(template_id: str) -> SearchJob:
    """Envelope mirror of ``_build_bayesian_config`` for the runner test."""
    return SearchJob(
        version="1.0.0",
        job_id="bayes-job-1",
        enqueued_at=datetime(2026, 1, 1, tzinfo=UTC),
        requested_by="pytest",
        correlation_id="corr-bayes-1",
        template_id=template_id,
        template_version="0.1.0",
        deadline_unix=int(datetime(2099, 1, 1, tzinfo=UTC).timestamp()),
        search_config={
            "kind": "bayesian",
            "template_id": template_id,
            "template_version": "0.1.0",
            "asset_universe": ["BASE"],
            "chain": "base",
            "backtest_start": "2026-01-01T00:00:00+00:00",
            "backtest_end": "2026-04-01T00:00:00+00:00",
            "walk_forward": [60, 15, 3],
            "turnover_lambda": "0.01",
            "top_k": 2,
            "n_trials": 6,
            "seed": 7,
            "param_ranges": {
                "period": {"kind": "continuous", "low": "2", "high": "10"},
                "threshold": {"kind": "grid", "values": [-1, 0, 1]},
                "venue": {
                    "kind": "categorical",
                    "choices": ["aave", "morpho"],
                },
            },
        },
    )


def test_deserialise_search_config_dispatches_bayesian():
    raw = _build_bayesian_job("TEST-001").search_config
    cfg = deserialise_search_config(raw)
    assert isinstance(cfg, BayesianSearchConfig)
    assert cfg.n_trials == 6
    assert cfg.seed == 7
    assert isinstance(cfg.param_ranges["period"], BayesianContinuousRange)
    assert isinstance(cfg.param_ranges["threshold"], BayesianGridRange)
    assert isinstance(cfg.param_ranges["venue"], BayesianCategoricalRange)


def test_deserialise_bayesian_rejects_wrong_kind():
    with pytest.raises(ValueError, match="expected kind='bayesian'"):
        deserialise_bayesian_search_config({"kind": "grid"})


def test_deserialise_search_config_unknown_kind():
    with pytest.raises(ValueError, match="unknown search_config kind"):
        deserialise_search_config({"kind": "random-search"})


@pytest.mark.asyncio
async def test_run_one_job_bayesian_persists_rows(
    in_memory_db, stub_adapter, toy_evaluate
):
    """End-to-end runner test: bayesian envelope → 6 search rows in DB,
    top-K=2 candidates marked ``is_top_k=True``, walk-forward rows
    generated for both."""
    registry = _FakeRegistry(_FakeTemplate(id="TEST-001", evaluate=toy_evaluate))
    job = _build_bayesian_job("TEST-001")

    outcome = await run_one_job(
        job, registry=registry, adapter=stub_adapter, db=in_memory_db
    )

    assert outcome.n_search_rows == 6
    assert outcome.n_top_k == 2
    # Walk-forward window count is driven by the snapshot stream length
    # and the (60, 15, 3) tuple — same arithmetic as the grid runner test:
    # 90 snapshots, train=60/test=15/step=3 → 6 windows per candidate.
    assert outcome.n_walk_forward_rows == 12

    session = in_memory_db.get_session()
    try:
        db_rows = session.scalars(select(ParameterSearchResultModel)).all()
    finally:
        session.close()

    assert len(db_rows) == 6
    assert sum(1 for r in db_rows if r.is_top_k) == 2
    # Every row carries all three params we asked Optuna to sample.
    for r in db_rows:
        # params_json is a JSON string keyed by param name.
        import json

        params = json.loads(r.params_json)
        assert set(params) == {"period", "threshold", "venue"}
