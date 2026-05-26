"""BacktestEngine Protocol — grid (v1) and Bayesian (week-10 add-on) search.

Per-template compute budget: max 2 CPU-hours per search run (hard kill,
enforced by the backtest-worker, not the engine impl).

Walk-forward via `vectorbt`'s rolling train/test scaffolding; deflated
Sharpe and regime-segmented OOS computed in Python on top.

`SearchConfig` is a discriminated union of `GridSearchConfig` and
`BayesianSearchConfig`. They share 9 fields via `_BaseSearchConfig` and
each carries the param-range shape appropriate to its `kind`. Static
type-check (mypy / pyright) narrows on `kind` so the impl dispatches
safely.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal, Protocol, runtime_checkable

from icarus.types.market import Chain


@dataclass(frozen=True, kw_only=True)
class _BaseSearchConfig:
    """Shared fields for grid and Bayesian search jobs.

    Not used directly — see GridSearchConfig or BayesianSearchConfig.
    """

    template_id: str
    template_version: str
    asset_universe: Sequence[str]
    chain: Chain
    backtest_start: datetime
    backtest_end: datetime
    walk_forward: tuple[int, int, int]  # (train_days, test_days, step_days); Q3 default 60/15/3
    turnover_lambda: Decimal
    top_k: int


@dataclass(frozen=True, kw_only=True)
class GridSearchConfig(_BaseSearchConfig):
    """Grid search — exhaustive over `param_ranges`.

    `param_ranges` maps each param name to the sequence of candidate values
    to try. Total search size = product of all sequence lengths. The impl
    enforces the per-template 2-CPU-hour budget against the total.
    """

    kind: Literal["grid"] = "grid"
    param_ranges: Mapping[str, Sequence[Any]]


@dataclass(frozen=True, kw_only=True)
class BayesianContinuousRange:
    """Continuous interval — Optuna samples with `suggest_float`.

    `scale="log"` flips on log-uniform sampling (use for span-of-orders
    knobs like learning rates or thresholds where 1e-4 vs 1e-3 matters
    as much as 0.1 vs 0.2).
    """

    kind: Literal["continuous"] = "continuous"
    low: Decimal
    high: Decimal
    scale: Literal["linear", "log"] = "linear"


@dataclass(frozen=True, kw_only=True)
class BayesianGridRange:
    """Discrete enumeration — Optuna samples with `suggest_categorical`.

    Mirrors the manifest's `GridParam` shape so a template manifest with
    a `grid`-kind param can be lowered directly into this range type.
    """

    kind: Literal["grid"] = "grid"
    values: Sequence[Any]


@dataclass(frozen=True, kw_only=True)
class BayesianCategoricalRange:
    """String choice — Optuna samples with `suggest_categorical`.

    Distinct from `BayesianGridRange` purely for symmetry with the
    manifest's discriminated union (numeric grid vs string categorical).
    """

    kind: Literal["categorical"] = "categorical"
    choices: Sequence[str]


BayesianParamRange = (
    BayesianContinuousRange | BayesianGridRange | BayesianCategoricalRange
)


@dataclass(frozen=True, kw_only=True)
class BayesianSearchConfig(_BaseSearchConfig):
    """Bayesian search (Optuna) — sampled, week-10 add-on.

    `param_ranges` maps each param name to a `BayesianParamRange` so the
    engine can dispatch to the right `trial.suggest_*` call. `n_trials`
    is the sample budget Optuna consumes (typically `top_k * 8` or
    whatever the 2-CPU-hour cap allows, whichever binds first). `seed`
    pins the TPESampler RNG so reruns produce identical surfaces — the
    same reproducibility contract grid search gets for free.
    """

    kind: Literal["bayesian"] = "bayesian"
    param_ranges: Mapping[str, BayesianParamRange]
    n_trials: int = 32
    seed: int = 42


SearchConfig = GridSearchConfig | BayesianSearchConfig


@dataclass(frozen=True)
class ResultSurface:
    """Per-template search output written to `parameter_search_results`.

    `surface` is a list of (params, metrics) rows. Metrics include
    sharpe, deflated_sharpe, max_dd, turnover, oos_sharpe, regime_segment.

    `top_k_candidate_ids` are the candidate-ids promoted to paper-trade.
    """

    template_id: str
    template_version: str
    surface: Sequence[Mapping[str, Any]]
    top_k_candidate_ids: Sequence[str]
    compute_seconds_used: Decimal


@runtime_checkable
class BacktestEngine(Protocol):
    """Async engine — one SearchConfig at a time, may run for hours."""

    name: str

    async def search(self, config: SearchConfig) -> ResultSurface:
        """Run the configured search and return the result surface.

        Impl dispatches on `config.kind` (mypy narrows `config` to the
        right concrete type inside each branch).

        Hard-killed at the worker level if the per-template compute budget
        is exceeded; the impl should checkpoint partial progress to Postgres
        so a kill produces a usable (if incomplete) ResultSurface.
        """
        ...
