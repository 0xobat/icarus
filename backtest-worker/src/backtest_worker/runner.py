"""Runner — orchestrate one SearchJob end to end.

Given a ``SearchJob`` envelope, the runner:
  1. Deserialises the embedded ``search_config`` dict into a
     ``GridSearchConfig`` (``kind="grid"``) or ``BayesianSearchConfig``
     (``kind="bayesian"``, added in W10).
  2. Resolves the template's ``evaluate`` callable via ``TemplateRegistry``.
  3. Runs ``run_grid_search`` or ``run_bayesian_search`` over a shared
     snapshot stream — both return the same ``ParameterSearchResult`` shape.
  4. Selects top-K candidates (``select_top_k``) and runs
     ``run_walk_forward`` on each.
  5. Writes ``parameter_search_results`` + ``walk_forward_results`` rows
     to Postgres in one transaction per job.

The ``DataAdapter`` is injected — stream A is building real adapters
in parallel; the runner is adapter-agnostic. Tests pass a stub adapter
that yields synthetic ``MarketSnapshot``s.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import structlog
from icarus.db.database import DatabaseManager
from icarus.db.models import (
    ParameterSearchResult as ParameterSearchResultModel,
)
from icarus.db.models import (
    WalkForwardResult as WalkForwardResultModel,
)
from icarus.dsl import TemplateRegistry
from icarus.envelopes.research import SearchJob
from icarus.protocols.backtest import (
    BayesianCategoricalRange,
    BayesianContinuousRange,
    BayesianGridRange,
    BayesianParamRange,
    BayesianSearchConfig,
    GridSearchConfig,
    SearchConfig,
)
from icarus.protocols.data import DataAdapter
from icarus.types.market import Chain

from backtest_worker.bayesian_search import run_bayesian_search
from backtest_worker.search import (
    ParameterSearchResult,
    _materialise_snapshots,
    run_grid_search,
    select_top_k,
)
from backtest_worker.walkforward import WalkForwardResult, run_walk_forward

logger = structlog.get_logger(service="backtest-worker.runner")


@dataclass(frozen=True)
class RunOutcome:
    """In-memory summary of one job's work — useful for tests and logs."""

    job_id: str
    template_id: str
    n_search_rows: int
    n_top_k: int
    n_walk_forward_rows: int


def _coerce_chain(value: Any) -> Chain:
    """Narrow a serialised chain string to the ``Chain`` literal type."""
    if value not in ("base", "solana"):
        raise ValueError(f"invalid chain: {value!r}")
    return value  # type: ignore[return-value]


def _coerce_datetime(value: Any) -> datetime:
    """Accept ISO strings or already-parsed datetimes."""
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def _base_kwargs(raw: dict[str, Any]) -> dict[str, Any]:
    """Pull the 9 fields shared by grid + bayesian configs from the raw dict.

    Both ``GridSearchConfig`` and ``BayesianSearchConfig`` extend the same
    ``_BaseSearchConfig`` so extracting these once and splatting them into
    each constructor keeps the two deserialisers symmetrical.
    """
    return {
        "template_id": str(raw["template_id"]),
        "template_version": str(raw["template_version"]),
        "asset_universe": tuple(raw["asset_universe"]),
        "chain": _coerce_chain(raw["chain"]),
        "backtest_start": _coerce_datetime(raw["backtest_start"]),
        "backtest_end": _coerce_datetime(raw["backtest_end"]),
        "walk_forward": tuple(raw["walk_forward"]),
        "turnover_lambda": Decimal(str(raw["turnover_lambda"])),
        "top_k": int(raw["top_k"]),
    }


def deserialise_grid_search_config(raw: dict[str, Any]) -> GridSearchConfig:
    """Rebuild a ``GridSearchConfig`` from the ``SearchJob.search_config`` dict.

    The envelope intentionally stores the config as a plain dict to
    avoid a circular import between envelopes and protocols. This
    function is the inverse — it knows the GridSearchConfig field shape
    and types and bridges JSON-friendly values (ISO datetimes, string
    Decimals) back to their concrete types.

    Raises ``ValueError`` if ``kind`` is not ``"grid"`` — callers
    targeting Bayesian should route through ``deserialise_search_config``.
    """
    kind = raw.get("kind", "grid")
    if kind != "grid":
        raise ValueError(
            f"deserialise_grid_search_config: expected kind='grid', got {kind!r}"
        )
    return GridSearchConfig(
        **_base_kwargs(raw),
        param_ranges={k: list(v) for k, v in raw["param_ranges"].items()},
    )


def _deserialise_bayesian_range(raw: dict[str, Any]) -> BayesianParamRange:
    """Map one entry of the envelope's ``param_ranges`` dict to a range obj.

    Discriminated by the ``kind`` field on each entry — mirrors the
    manifest's ParamSpec union so a template manifest's param spec can
    be lowered into a search config without rewriting the encoding.
    """
    kind = raw.get("kind", "continuous")
    if kind == "continuous":
        return BayesianContinuousRange(
            low=Decimal(str(raw["low"])),
            high=Decimal(str(raw["high"])),
            scale=raw.get("scale", "linear"),
        )
    if kind == "grid":
        return BayesianGridRange(values=list(raw["values"]))
    if kind == "categorical":
        return BayesianCategoricalRange(choices=list(raw["choices"]))
    raise ValueError(f"unknown bayesian param range kind: {kind!r}")


def deserialise_bayesian_search_config(raw: dict[str, Any]) -> BayesianSearchConfig:
    """Rebuild a ``BayesianSearchConfig`` from the envelope dict.

    Layout (per param entry): ``{"kind": "continuous", "low": "0.1", "high": "0.9"}``
    or ``{"kind": "grid", "values": [3, 5, 7]}``
    or ``{"kind": "categorical", "choices": ["aave", "morpho"]}``.
    `n_trials` and `seed` are top-level fields with sensible defaults so
    existing callers that only know about grid don't break if they
    transition.
    """
    kind = raw.get("kind", "bayesian")
    if kind != "bayesian":
        raise ValueError(
            f"deserialise_bayesian_search_config: expected kind='bayesian', got {kind!r}"
        )
    return BayesianSearchConfig(
        **_base_kwargs(raw),
        param_ranges={
            k: _deserialise_bayesian_range(v) for k, v in raw["param_ranges"].items()
        },
        n_trials=int(raw.get("n_trials", 32)),
        seed=int(raw.get("seed", 42)),
    )


def deserialise_search_config(raw: dict[str, Any]) -> SearchConfig:
    """Dispatch one envelope dict to the matching deserialiser by ``kind``.

    Default ``kind`` is ``"grid"`` for backward compat with W3-era jobs
    that didn't bother stamping the field (every job that reached
    Postgres before W10 was implicitly grid).
    """
    kind = raw.get("kind", "grid")
    if kind == "grid":
        return deserialise_grid_search_config(raw)
    if kind == "bayesian":
        return deserialise_bayesian_search_config(raw)
    raise ValueError(f"unknown search_config kind: {kind!r}")


def _persist(
    db: DatabaseManager,
    *,
    search_rows: Sequence[ParameterSearchResult],
    walk_rows: Sequence[WalkForwardResult],
) -> None:
    """Write both result tables in one transaction.

    Single-transaction so the webapp never sees a half-written search
    (e.g. surface present but no walk-forward rows). A failure here
    raises and the caller surfaces an alert; the job stays in
    ``:inflight`` until the visibility reaper requeues it.
    """
    session = db.get_session()
    try:
        for r in search_rows:
            session.add(
                ParameterSearchResultModel(
                    template_id=r.template_id,
                    template_version=r.template_version,
                    params_json=json.dumps(r.params, sort_keys=True, default=str),
                    sharpe=r.sharpe,
                    deflated_sharpe=r.deflated_sharpe,
                    max_dd=r.max_dd,
                    turnover=r.turnover,
                    oos_sharpe=r.oos_sharpe,
                    compute_seconds=r.compute_seconds,
                    is_top_k=r.is_top_k,
                )
            )
        for w in walk_rows:
            session.add(
                WalkForwardResultModel(
                    candidate_id=w.candidate_id,
                    train_start=w.train_start,
                    train_end=w.train_end,
                    test_start=w.test_start,
                    test_end=w.test_end,
                    train_sharpe=w.train_sharpe,
                    test_sharpe=w.test_sharpe,
                    test_max_dd=w.test_max_dd,
                    regime_label=w.regime_label,
                )
            )
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


async def run_one_job(
    job: SearchJob,
    *,
    registry: TemplateRegistry,
    adapter: DataAdapter,
    db: DatabaseManager,
) -> RunOutcome:
    """Execute a single ``SearchJob`` end to end and persist results.

    Returns a ``RunOutcome`` describing what was written. Side effects:
    rows in ``parameter_search_results`` + ``walk_forward_results``.
    """
    log = logger.bind(
        job_id=job.job_id,
        template_id=job.template_id,
        template_version=job.template_version,
        correlation_id=job.correlation_id,
    )
    log.info("job_start")

    config = deserialise_search_config(job.search_config)
    template = registry.by_id(job.template_id)
    evaluate_fn = template.evaluate

    # 1. Parameter search — grid or bayesian per ``config.kind``. Both
    # engines return the same ``ParameterSearchResult`` shape so every
    # step after this one (top-K, walk-forward, persistence) is identical.
    if isinstance(config, BayesianSearchConfig):
        log.info("search_engine_dispatch", engine="bayesian", n_trials=config.n_trials)
        search_rows = await run_bayesian_search(
            config, evaluate_fn=evaluate_fn, adapter=adapter
        )
    else:
        log.info("search_engine_dispatch", engine="grid")
        search_rows = await run_grid_search(
            config, evaluate_fn=evaluate_fn, adapter=adapter
        )
    # 2. Top-K (with is_top_k=True on those rows).
    top_k_rows = select_top_k(search_rows, config.top_k)
    # Replace the underlying search_rows with the top-K-aware copies so
    # what we persist matches what the walk-forward runs on.
    by_params: dict[str, ParameterSearchResult] = {
        r.candidate_id: r for r in search_rows
    }
    for t in top_k_rows:
        by_params[t.candidate_id] = t
    persisted_rows = [by_params[r.candidate_id] for r in search_rows]

    # 3. Walk-forward over the top-K candidates only — re-materialise
    # the snapshot stream once and share across candidates (same
    # rationale as grid_search).
    snapshots = await _materialise_snapshots(
        adapter, config.chain, config.backtest_start, config.backtest_end
    )
    walk_rows: list[WalkForwardResult] = []
    for top in top_k_rows:
        windows = run_walk_forward(
            candidate_id=top.candidate_id,
            template_id=top.template_id,
            params=top.params,
            snapshots=snapshots,
            walk_forward=config.walk_forward,
            evaluate_fn=evaluate_fn,
        )
        walk_rows.extend(windows)

    # 4. Persist both result sets in one transaction.
    _persist(db, search_rows=persisted_rows, walk_rows=walk_rows)

    # TODO(W5): wire icarus.backtest_metrics.oos_gate over `walk_rows` per
    # template_id and persist the resulting GateDecision. The decision feeds
    # lake-governor's backtest -> paper_trade state transition. Deferred
    # because the candidate state machine lives in lake-governor (W5);
    # the runner here only owns the *search* outputs (parameter_search_results
    # + walk_forward_results). Persisting a GateDecision row before
    # lake-governor exists would create an unused column.

    outcome = RunOutcome(
        job_id=job.job_id,
        template_id=job.template_id,
        n_search_rows=len(persisted_rows),
        n_top_k=len(top_k_rows),
        n_walk_forward_rows=len(walk_rows),
    )
    log.info(
        "job_complete",
        n_search_rows=outcome.n_search_rows,
        n_top_k=outcome.n_top_k,
        n_walk_forward_rows=outcome.n_walk_forward_rows,
    )
    return outcome


def build_default_registry(templates_root: Path | None = None) -> TemplateRegistry:
    """Construct the registry the worker uses by default.

    Looks in ``./templates`` relative to the repo root unless overridden.
    Smoke-test mode rides the registry default (``blocking`` as of W4 per
    the blueprint's "smoke test enforcement turned on in registry loader"
    milestone). A template that fails its smoke test is rejected outright.
    """
    root = templates_root or Path("templates")
    registry = TemplateRegistry(root)
    registry.load()
    return registry
