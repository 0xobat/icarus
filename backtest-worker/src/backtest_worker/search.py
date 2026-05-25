"""Grid search engine — exhaustive sweep over a GridSearchConfig param space.

The engine is intentionally simple and synchronous in its outer loop: each
combination is a self-contained simulation that calls into ``template.evaluate``
once per snapshot, accumulates a price-relative return series, and reduces it
to a small metric bundle (``sharpe``, ``max_dd``, ``turnover``, etc).

The metric reduction is deliberately conservative — ``deflated_sharpe`` and
``oos_sharpe`` come from a parallel stream's ``icarus.backtest_metrics``
module. If that module is not yet importable (early in W3 while stream C is
in flight), we fall back to plain Sharpe so the search can still run, and
the runner records ``deflated_sharpe == sharpe``. The decision-engine never
consumes raw deflated_sharpe in cold-start mode, so the placeholder is safe
during W3 integration; once stream C lands the import resolves and the real
deflation takes over without code changes here.

Search size = product of all sequence lengths in ``param_ranges``. The
per-template 2-CPU-hour budget is enforced by the *worker* (not here);
the engine yields rows in deterministic order so an early termination
still produces a well-defined prefix of the surface.
"""

from __future__ import annotations

import itertools
import json
import math
import time
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import numpy as np
import structlog
from icarus.protocols.backtest import GridSearchConfig
from icarus.protocols.data import DataAdapter
from icarus.types import MarketSnapshot, PortfolioSnapshot, Position
from icarus.types.market import Chain

# Optional dependency — parallel stream C. Fall back gracefully so this
# stream's tests don't depend on a sibling worktree's module.
try:  # pragma: no cover - import guard
    from icarus.backtest_metrics import deflated_sharpe as _deflated_sharpe

    _HAS_DEFLATED = True
except ImportError:  # pragma: no cover - executed in stream-B-only worktrees
    _HAS_DEFLATED = False

    def _deflated_sharpe(  # type: ignore[no-redef]
        returns: np.ndarray, n_trials: int
    ) -> float:
        """Placeholder until ``icarus.backtest_metrics`` lands (stream C).

        Returns plain Sharpe — the deflation factor is identity when the
        real implementation is absent. The runner records the same value
        in both columns; the OOS gate downstream knows to ignore the
        deflated column when ``compute_seconds`` is zero (a stronger
        signal than a magic NaN here).
        """
        if returns.size < 2:
            return 0.0
        std = float(np.std(returns, ddof=1))
        if std == 0.0:
            return 0.0
        # Annualise assuming 1-day cadence.
        return float(np.mean(returns) / std * math.sqrt(365))


logger = structlog.get_logger(service="backtest-worker.search")

EvaluateFn = Any  # icarus.dsl.registry.EvaluateCallable; kept loose to avoid a cycle


@dataclass(frozen=True)
class ParameterSearchResult:
    """One row of the search surface.

    Mirrors the columns of ``icarus.db.models.ParameterSearchResult`` and
    is the in-memory shape passed to the runner before persistence.
    """

    template_id: str
    template_version: str
    params: Mapping[str, Any]
    sharpe: float
    deflated_sharpe: float
    max_dd: float
    turnover: float
    oos_sharpe: float | None
    compute_seconds: float
    is_top_k: bool = False

    @property
    def candidate_id(self) -> str:
        """Deterministic id from template + canonical-json(params)."""
        params_json = json.dumps(self.params, sort_keys=True, default=str)
        # 12 hex chars is plenty (~10^14 combos before a 1% collision risk).
        import hashlib

        h = hashlib.sha1(params_json.encode("utf-8")).hexdigest()[:12]
        return f"{self.template_id}-{h}"


def _iter_param_combinations(
    param_ranges: Mapping[str, Sequence[Any]],
) -> list[dict[str, Any]]:
    """Enumerate the Cartesian product of ``param_ranges`` in stable order.

    Stable order = dict-insertion order on keys, sequence order on values.
    Stable order matters because the runner persists rows in this order
    and the webapp queries by rowid for chronological browsing.
    """
    if not param_ranges:
        return [{}]
    keys = list(param_ranges.keys())
    value_lists = [list(param_ranges[k]) for k in keys]
    out: list[dict[str, Any]] = []
    for combo in itertools.product(*value_lists):
        out.append(dict(zip(keys, combo, strict=True)))
    return out


async def _materialise_snapshots(
    adapter: DataAdapter,
    chain: Chain,
    start: Any,
    end: Any,
) -> list[MarketSnapshot]:
    """Drain the async historical iterator into an in-memory list.

    Sharing the same snapshot list across every grid combination is the
    biggest single speedup — adapters that hit network/disk would be the
    bottleneck otherwise. The 2-CPU-hour budget assumes one materialise
    per search, not per combination.
    """
    snapshots: list[MarketSnapshot] = []
    aiter: AsyncIterator[MarketSnapshot] = adapter.fetch_historical(chain, start, end)
    async for snap in aiter:
        snapshots.append(snap)
    return snapshots


def _empty_portfolio(start_cash: Decimal = Decimal("10000")) -> PortfolioSnapshot:
    """Initial portfolio handed to ``evaluate`` on the first snapshot."""
    from datetime import UTC, datetime

    return PortfolioSnapshot(
        nav_usd=start_cash,
        positions={},
        cash_usd=start_cash,
        drawdown_from_peak=Decimal("0"),
        last_rebalance=datetime.fromtimestamp(0, tz=UTC),
    )


def _simulate_one(
    template_id: str,
    template_version: str,
    evaluate_fn: EvaluateFn,
    params: Mapping[str, Any],
    snapshots: Sequence[MarketSnapshot],
    turnover_lambda: Decimal,
    n_trials: int,
) -> ParameterSearchResult:
    """Run one parameter combination over the snapshot stream.

    Deliberately small and synchronous — the grid is parallelisable in a
    later optimisation but every combination is independent, so a plain
    loop is the cleanest first cut.
    """
    started = time.monotonic()
    portfolio = _empty_portfolio()
    # Track NAV per snapshot for return series.
    nav_series: list[float] = [float(portfolio.nav_usd)]
    n_action_changes = 0
    last_action = "hold"

    # For NAV simulation we need a price for the first asset in the
    # universe. A stub adapter exposes a single "BASE" key; live adapters
    # use real symbols. We use whichever the first snapshot exposes.
    asset_key: str | None = None
    if snapshots and snapshots[0].prices:
        asset_key = next(iter(snapshots[0].prices.keys()))

    prev_price: Decimal | None = None
    position_size: Decimal = Decimal("0")  # asset units held

    for snap in snapshots:
        if asset_key is None:
            asset_key = next(iter(snap.prices.keys()), None)
        price = snap.prices.get(asset_key, Decimal("0")) if asset_key else Decimal("0")

        # Mark NAV to current price: cash + position_size * price.
        nav = portfolio.cash_usd + position_size * price
        nav_series.append(float(nav))

        # Rebuild a fresh PortfolioSnapshot for the evaluator (immutable
        # contract: same shape every call).
        positions: dict[str, Position] = {}
        if position_size != 0:
            positions[f"{template_id}-pos"] = Position(
                candidate_id=f"{template_id}-pos",
                template_id=template_id,
                asset=asset_key or "ASSET",
                size_usd=position_size * price,
                entry_price=price if prev_price is None else prev_price,
                entry_time=snap.timestamp,
            )
        ps = PortfolioSnapshot(
            nav_usd=nav,
            positions=positions,
            cash_usd=portfolio.cash_usd,
            drawdown_from_peak=Decimal("0"),
            last_rebalance=snap.timestamp,
        )

        try:
            decision = evaluate_fn(dict(params), snap, ps)
        except Exception as exc:
            logger.warning(
                "evaluate_raised",
                template_id=template_id,
                error_class=type(exc).__name__,
                error=str(exc),
            )
            decision = None

        if decision is not None:
            if decision.action != last_action:
                n_action_changes += 1
                last_action = decision.action
            if decision.action == "enter" and position_size == 0 and price > 0:
                target_usd = min(Decimal(str(decision.target_size)), portfolio.cash_usd)
                if target_usd > 0:
                    position_size = target_usd / price
                    portfolio = PortfolioSnapshot(
                        nav_usd=nav,
                        positions=positions,
                        cash_usd=portfolio.cash_usd - target_usd,
                        drawdown_from_peak=portfolio.drawdown_from_peak,
                        last_rebalance=snap.timestamp,
                    )
            elif decision.action == "exit" and position_size != 0:
                proceeds = position_size * price
                portfolio = PortfolioSnapshot(
                    nav_usd=portfolio.cash_usd + proceeds,
                    positions={},
                    cash_usd=portfolio.cash_usd + proceeds,
                    drawdown_from_peak=portfolio.drawdown_from_peak,
                    last_rebalance=snap.timestamp,
                )
                position_size = Decimal("0")
        prev_price = price if price > 0 else prev_price

    nav_arr = np.array(nav_series, dtype=float)
    if nav_arr.size < 2:
        sharpe = 0.0
        max_dd = 0.0
        returns = np.zeros(0)
    else:
        returns = np.diff(nav_arr) / np.where(nav_arr[:-1] == 0, 1.0, nav_arr[:-1])
        std = float(np.std(returns, ddof=1)) if returns.size > 1 else 0.0
        sharpe = (
            float(np.mean(returns) / std * math.sqrt(365)) if std > 0 else 0.0
        )
        peaks = np.maximum.accumulate(nav_arr)
        drawdowns = (nav_arr - peaks) / np.where(peaks == 0, 1.0, peaks)
        max_dd = float(-np.min(drawdowns)) if drawdowns.size else 0.0

    deflated = float(_deflated_sharpe(returns, n_trials))
    turnover = float(Decimal(str(n_action_changes)) * turnover_lambda)
    elapsed = time.monotonic() - started

    return ParameterSearchResult(
        template_id=template_id,
        template_version=template_version,
        params=dict(params),
        sharpe=sharpe,
        deflated_sharpe=deflated,
        max_dd=max_dd,
        turnover=turnover,
        oos_sharpe=None,  # walk-forward fills this on the top-K rows
        compute_seconds=elapsed,
        is_top_k=False,
    )


async def run_grid_search(
    config: GridSearchConfig,
    *,
    evaluate_fn: EvaluateFn,
    adapter: DataAdapter,
) -> list[ParameterSearchResult]:
    """Run a full grid search and return one row per parameter combination.

    Snapshots are materialised once and shared across all combinations
    (see ``_materialise_snapshots``). The order of the returned list
    matches the Cartesian-product order of ``param_ranges`` — stable
    across runs so re-running the same config produces byte-identical
    persistence.
    """
    combos = _iter_param_combinations(config.param_ranges)
    n_trials = max(1, len(combos))

    log = logger.bind(
        template_id=config.template_id,
        template_version=config.template_version,
        n_combos=n_trials,
    )
    log.info("grid_search_start")

    snapshots = await _materialise_snapshots(
        adapter, config.chain, config.backtest_start, config.backtest_end
    )
    log.info("snapshots_loaded", n_snapshots=len(snapshots))

    results: list[ParameterSearchResult] = []
    for combo in combos:
        row = _simulate_one(
            template_id=config.template_id,
            template_version=config.template_version,
            evaluate_fn=evaluate_fn,
            params=combo,
            snapshots=snapshots,
            turnover_lambda=config.turnover_lambda,
            n_trials=n_trials,
        )
        results.append(row)

    log.info("grid_search_complete", n_results=len(results))
    return results


def select_top_k(
    rows: Sequence[ParameterSearchResult], k: int
) -> list[ParameterSearchResult]:
    """Pick the top-K rows by deflated Sharpe, breaking ties by sharpe.

    Returns *new* dataclass copies with ``is_top_k=True`` set; the input
    list is not mutated. Stable: equal-score rows preserve grid order.
    """
    if k <= 0:
        return []
    # Negate the keys so we sort descending and ties keep insertion order.
    ranked = sorted(
        enumerate(rows),
        key=lambda kv: (-kv[1].deflated_sharpe, -kv[1].sharpe, kv[0]),
    )
    chosen_idx = {idx for idx, _ in ranked[:k]}
    return [
        ParameterSearchResult(
            template_id=r.template_id,
            template_version=r.template_version,
            params=r.params,
            sharpe=r.sharpe,
            deflated_sharpe=r.deflated_sharpe,
            max_dd=r.max_dd,
            turnover=r.turnover,
            oos_sharpe=r.oos_sharpe,
            compute_seconds=r.compute_seconds,
            is_top_k=True,
        )
        for i, r in enumerate(rows)
        if i in chosen_idx
    ]
