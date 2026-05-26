"""Bayesian (Optuna TPE) search engine — sample-budget alternative to grid.

Where ``run_grid_search`` enumerates the full Cartesian product of
``param_ranges`` (cheap when each param has a handful of values, exponential
when they have many), this engine asks Optuna's TPE sampler to pick the
``n_trials`` most-promising parameter combinations under the same objective.

We deliberately reuse ``_simulate_one`` from ``backtest_worker.search`` so
both engines persist the *same* ``ParameterSearchResult`` shape into
``parameter_search_results``. The runner doesn't care which engine produced
a row — only the search_config envelope distinguishes them at enqueue time.

Objective parity with grid search:
    score = deflated_sharpe - turnover_lambda * turnover
TPE maximises this. Optuna minimises by default so we pass `direction="maximize"`.

Reproducibility:
    The TPESampler is seeded from ``config.seed`` (default 42). Same config
    + same snapshot stream + same evaluate_fn → byte-identical result list,
    same ordering, same compute_seconds-modulo timing. The persistence
    layer relies on this for re-run idempotency (the candidate_id is a
    hash of params, so repeat trials over the same combo are de-duplicated
    upstream in the runner via dict-keyed assignment).

Sync/async:
    Optuna's ``study.optimize`` is sync and blocks the event loop. We
    wrap the whole optimise call in ``asyncio.to_thread`` so the runner
    can ``await`` it without stalling the worker's redis poller.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any

import numpy as np
import optuna
import structlog
from icarus.protocols.backtest import (
    BayesianCategoricalRange,
    BayesianContinuousRange,
    BayesianGridRange,
    BayesianParamRange,
    BayesianSearchConfig,
)
from icarus.protocols.data import DataAdapter
from icarus.types import MarketSnapshot
from optuna.samplers import TPESampler

from backtest_worker.search import (
    EvaluateFn,
    ParameterSearchResult,
    _materialise_snapshots,
    _simulate_one,
)

logger = structlog.get_logger(service="backtest-worker.bayesian_search")

# Silence Optuna's default per-trial INFO chatter — our structlog binding
# below covers the same ground at the level we actually want.
optuna.logging.set_verbosity(optuna.logging.WARNING)


def _suggest_param(
    trial: optuna.Trial, name: str, spec: BayesianParamRange
) -> Any:
    """Dispatch one parameter onto the matching ``trial.suggest_*`` call.

    Continuous → ``suggest_float`` (with log-scale opt-in).
    Grid → ``suggest_categorical`` over the explicit value list.
    Categorical → ``suggest_categorical`` over the string choices.

    Centralised so the test suite can assert dispatch behaviour without
    poking at engine internals.
    """
    if isinstance(spec, BayesianContinuousRange):
        return trial.suggest_float(
            name,
            float(spec.low),
            float(spec.high),
            log=(spec.scale == "log"),
        )
    if isinstance(spec, BayesianGridRange):
        # Optuna requires hashable categorical values — Decimals are hashable
        # but stringify oddly across SDK versions. Cast to a JSON-friendly
        # primitive (int/float) so the persisted params_json round-trips.
        choices = [_coerce_grid_value(v) for v in spec.values]
        return trial.suggest_categorical(name, choices)
    if isinstance(spec, BayesianCategoricalRange):
        return trial.suggest_categorical(name, list(spec.choices))
    raise TypeError(f"unsupported BayesianParamRange: {type(spec).__name__}")


def _coerce_grid_value(value: Any) -> Any:
    """Make a grid value Optuna-categorical-safe + JSON-serialisable.

    Decimals → float when they fit, str when they don't (preserves exact
    precision across the round trip into params_json). ints and floats
    pass through unchanged.
    """
    if isinstance(value, Decimal):
        as_float = float(value)
        # Round-trip check: if str(Decimal) survives float coercion exactly,
        # keep it as a float. Otherwise fall back to str to preserve precision.
        if Decimal(str(as_float)) == value:
            return as_float
        return str(value)
    return value


async def run_bayesian_search(
    config: BayesianSearchConfig,
    *,
    evaluate_fn: EvaluateFn,
    adapter: DataAdapter,
) -> list[ParameterSearchResult]:
    """Run an Optuna TPE search and return one row per completed trial.

    Returns rows in *trial completion* order — Optuna assigns trial
    numbers 0..n_trials-1, and we preserve that order in the output list
    so the runner persists them in the same order they were explored.
    This makes a partial-completion checkpoint (W11 budget cap) yield a
    well-defined prefix, same contract as grid search.

    `n_trials` is honoured exactly — Optuna's `n_trials` parameter to
    `study.optimize`. If a trial raises, Optuna records it as a failed
    trial and continues; we filter failures from the returned list so
    downstream consumers never see a row with NaN metrics.
    """
    log = logger.bind(
        template_id=config.template_id,
        template_version=config.template_version,
        n_trials=config.n_trials,
        seed=config.seed,
    )
    log.info("bayesian_search_start")

    snapshots = await _materialise_snapshots(
        adapter, config.chain, config.backtest_start, config.backtest_end
    )
    log.info("snapshots_loaded", n_snapshots=len(snapshots))

    # Hold per-trial results keyed by trial.number so we can return them
    # in the deterministic order Optuna assigned (0..n-1), independent of
    # the order the sampler happened to score them.
    rows_by_trial: dict[int, ParameterSearchResult] = {}

    def _objective(trial: optuna.Trial) -> float:
        params = _sample_params(trial, config.param_ranges)
        result = _simulate_one(
            template_id=config.template_id,
            template_version=config.template_version,
            evaluate_fn=evaluate_fn,
            params=params,
            snapshots=snapshots,
            turnover_lambda=config.turnover_lambda,
            # n_trials feeds the deflated-sharpe correction; pass the
            # full Bayesian budget so deflation reflects the *intended*
            # search size, not just the trials seen so far.
            n_trials=config.n_trials,
        )
        rows_by_trial[trial.number] = result
        # Maximise: deflated_sharpe minus turnover cost. Identical to the
        # ranking grid search uses in ``select_top_k``.
        return result.deflated_sharpe - float(config.turnover_lambda) * result.turnover

    sampler = TPESampler(seed=config.seed)
    # numpy default_rng is *also* seeded so any auxiliary sampling stays
    # reproducible — the brief calls this out explicitly.
    _ = np.random.default_rng(seed=config.seed)
    study = optuna.create_study(direction="maximize", sampler=sampler)

    # Optuna is sync; off-load to a thread so the worker's event loop
    # can still service the redis poller / heartbeat while a long study runs.
    await asyncio.to_thread(
        study.optimize,
        _objective,
        n_trials=config.n_trials,
        show_progress_bar=False,
    )

    # Preserve trial-number order; drop trials that raised before
    # _simulate_one returned (rare — _simulate_one swallows evaluate
    # errors itself, so the only failure modes here are programmer bugs).
    results = [rows_by_trial[i] for i in sorted(rows_by_trial) if i in rows_by_trial]
    log.info(
        "bayesian_search_complete",
        n_results=len(results),
        n_failed=config.n_trials - len(results),
    )
    return results


def _sample_params(
    trial: optuna.Trial, param_ranges: Mapping[str, BayesianParamRange]
) -> dict[str, Any]:
    """Walk the param spec in stable key order and ask Optuna for a value each.

    Key order matters: Optuna's TPE uses the parameter *name* as the
    suggestion identifier, so the order of `suggest_*` calls doesn't
    affect sampling, but it *does* affect the stored params dict
    insertion order — which params_json round-trips into Postgres in the
    same order, which the webapp surfaces in the same order. Stability
    here means search results render identically across reruns.
    """
    return {name: _suggest_param(trial, name, spec) for name, spec in param_ranges.items()}


# ─────────── Snapshot-list helper export (test convenience) ───────────
# Re-exported so the unit test can build a stub adapter and snapshot list
# without importing from the grid module — keeps the bayesian_search
# tests self-contained against their own surface.

__all__ = [
    "MarketSnapshot",
    "Sequence",
    "_sample_params",
    "_suggest_param",
    "run_bayesian_search",
]
