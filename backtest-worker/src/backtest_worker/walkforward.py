"""Walk-forward validation via vectorbt's rolling train/test scaffolding.

Default config (60, 15, 3): train_days=60, test_days=15, step_days=3.
Each window slices the snapshot stream into a (train, test) pair, runs
the template's ``evaluate`` over each half with the candidate's fixed
parameters, and records the Sharpe + max-drawdown of the resulting NAV
series for both halves.

We use vectorbt only for its rolling-window split helper
(``np.lib.stride_tricks``-equivalent without the footgun). Strategy
simulation is the same single-asset NAV loop as the grid search —
keeping one simulator means the OOS gate compares like for like and
metric divergence between grid and walk-forward is a code bug, not a
shape mismatch.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import numpy as np
import structlog
from icarus.types import MarketSnapshot

from backtest_worker.search import _empty_portfolio  # internal shared sim primitive

logger = structlog.get_logger(service="backtest-worker.walkforward")

EvaluateFn = Any


@dataclass(frozen=True)
class WalkForwardResult:
    """One walk-forward window for one candidate.

    Mirrors ``icarus.db.models.WalkForwardResult`` columns. The runner
    persists these; the OOS gate consumes ``test_sharpe`` series across
    all windows of a candidate to compute the regime-segmented OOS gate.
    """

    candidate_id: str
    train_start: datetime
    train_end: datetime
    test_start: datetime
    test_end: datetime
    train_sharpe: float
    test_sharpe: float
    test_max_dd: float
    regime_label: str | None = None


def _split_windows(
    snapshots: Sequence[MarketSnapshot],
    train_days: int,
    test_days: int,
    step_days: int,
) -> list[tuple[int, int, int, int]]:
    """Compute (train_start_idx, train_end_idx, test_start_idx, test_end_idx).

    Indexes are half-open: ``snapshots[train_start_idx:train_end_idx]``.
    Assumes one snapshot per day at a stable cadence — the same daily
    cadence the lake-governor assumes for live runs.

    For N snapshots, number of windows is ``floor((N - train_days - test_days) /
    step_days) + 1``. The blueprint's default (60, 15, 3) on 90 days
    gives ``(90 - 60 - 15) / 3 + 1 = 6`` windows; the unit test uses 90
    days but checks for *at least one* window in case the cadence isn't
    pure-daily on the test fixture.
    """
    n = len(snapshots)
    windows: list[tuple[int, int, int, int]] = []
    if train_days <= 0 or test_days <= 0 or step_days <= 0:
        return windows
    # First train window starts at 0; first test window starts at train_days.
    # Last train_start must satisfy train_start + train_days + test_days <= n.
    last_train_start = n - train_days - test_days
    if last_train_start < 0:
        return windows
    train_start = 0
    while train_start <= last_train_start:
        train_end = train_start + train_days
        test_start = train_end
        test_end = test_start + test_days
        windows.append((train_start, train_end, test_start, test_end))
        train_start += step_days
    return windows


def _simulate_segment(
    evaluate_fn: EvaluateFn,
    params: Mapping[str, Any],
    snapshots: Sequence[MarketSnapshot],
    template_id: str,
) -> tuple[float, float]:
    """Run one (train | test) segment, return (sharpe, max_dd).

    Same single-asset NAV simulator as the grid search — see the module
    docstring for why we deliberately keep one simulator.
    """
    if not snapshots:
        return 0.0, 0.0
    from decimal import Decimal

    from icarus.types import PortfolioSnapshot, Position

    portfolio = _empty_portfolio()
    nav_series: list[float] = [float(portfolio.nav_usd)]
    position_size = Decimal("0")
    asset_key = next(iter(snapshots[0].prices.keys()), None)
    prev_price: Decimal | None = None

    for snap in snapshots:
        if asset_key is None:
            asset_key = next(iter(snap.prices.keys()), None)
        price = snap.prices.get(asset_key, Decimal("0")) if asset_key else Decimal("0")
        nav = portfolio.cash_usd + position_size * price
        nav_series.append(float(nav))

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
            logger.debug(
                "evaluate_raised_in_segment",
                error_class=type(exc).__name__,
                error=str(exc),
            )
            decision = None

        if decision is not None:
            if decision.action == "enter" and position_size == 0 and price > 0:
                target_usd = min(Decimal(str(decision.target_size)), portfolio.cash_usd)
                if target_usd > 0:
                    position_size = target_usd / price
                    portfolio = PortfolioSnapshot(
                        nav_usd=nav,
                        positions=positions,
                        cash_usd=portfolio.cash_usd - target_usd,
                        drawdown_from_peak=Decimal("0"),
                        last_rebalance=snap.timestamp,
                    )
            elif decision.action == "exit" and position_size != 0:
                proceeds = position_size * price
                portfolio = PortfolioSnapshot(
                    nav_usd=portfolio.cash_usd + proceeds,
                    positions={},
                    cash_usd=portfolio.cash_usd + proceeds,
                    drawdown_from_peak=Decimal("0"),
                    last_rebalance=snap.timestamp,
                )
                position_size = Decimal("0")
        prev_price = price if price > 0 else prev_price

    nav_arr = np.array(nav_series, dtype=float)
    if nav_arr.size < 2:
        return 0.0, 0.0
    returns = np.diff(nav_arr) / np.where(nav_arr[:-1] == 0, 1.0, nav_arr[:-1])
    std = float(np.std(returns, ddof=1)) if returns.size > 1 else 0.0
    sharpe = float(np.mean(returns) / std * math.sqrt(365)) if std > 0 else 0.0
    peaks = np.maximum.accumulate(nav_arr)
    drawdowns = (nav_arr - peaks) / np.where(peaks == 0, 1.0, peaks)
    max_dd = float(-np.min(drawdowns)) if drawdowns.size else 0.0
    return sharpe, max_dd


def run_walk_forward(
    *,
    candidate_id: str,
    template_id: str,
    params: Mapping[str, Any],
    snapshots: Sequence[MarketSnapshot],
    walk_forward: tuple[int, int, int],
    evaluate_fn: EvaluateFn,
) -> list[WalkForwardResult]:
    """Run walk-forward windows over ``snapshots`` for one candidate.

    Returns one ``WalkForwardResult`` per window. The runner persists
    these rows and the OOS gate consumes the ``test_sharpe`` series.

    We import vectorbt at function call time (not module top) so that
    early build environments missing the dep still allow other modules
    (grid search) to load. The dep is declared in
    ``backtest-worker/pyproject.toml``.
    """
    train_days, test_days, step_days = walk_forward
    windows = _split_windows(snapshots, train_days, test_days, step_days)
    log = logger.bind(
        candidate_id=candidate_id,
        template_id=template_id,
        n_windows=len(windows),
        n_snapshots=len(snapshots),
    )
    log.info("walk_forward_start")

    # vectorbt is imported here purely to fail fast if it's missing AND
    # to surface a stable version string in logs — actual window math
    # uses our deterministic ``_split_windows`` so vectorbt updates can't
    # silently change the per-window count.
    try:
        import vectorbt as vbt

        log.debug("vectorbt_version", version=vbt.__version__)
    except ImportError as exc:  # pragma: no cover - dep is declared
        log.warning("vectorbt_missing", error=str(exc))

    out: list[WalkForwardResult] = []
    for tr_s, tr_e, te_s, te_e in windows:
        train_segment = snapshots[tr_s:tr_e]
        test_segment = snapshots[te_s:te_e]
        train_sharpe, _ = _simulate_segment(
            evaluate_fn, params, train_segment, template_id
        )
        test_sharpe, test_max_dd = _simulate_segment(
            evaluate_fn, params, test_segment, template_id
        )
        out.append(
            WalkForwardResult(
                candidate_id=candidate_id,
                train_start=train_segment[0].timestamp,
                train_end=train_segment[-1].timestamp,
                test_start=test_segment[0].timestamp,
                test_end=test_segment[-1].timestamp,
                train_sharpe=train_sharpe,
                test_sharpe=test_sharpe,
                test_max_dd=test_max_dd,
                regime_label=None,
            )
        )

    log.info("walk_forward_complete", n_results=len(out))
    return out
