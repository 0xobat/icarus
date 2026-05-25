"""Regime-segmented out-of-sample acceptance gate.

A candidate strategy must perform acceptably *in every regime it was
walk-forward tested in* — not just on average. This gate enforces that by
bucketing `WalkForwardResult`-shaped tuples by `regime_label` and asking,
per regime, whether the mean test_sharpe clears the floor and the worst
test_max_dd stays inside the ceiling.

This is the implementation arm of the blueprint's W3 backtest acceptance:
"OOS gate: regime-segmented hold-out windows; reject candidates that fail
in any regime, not just on average."

Pure-compute: consumes tuples (test_sharpe, test_max_dd, regime_label),
not ORM rows — keeping this module ORM-free as a deliberate boundary.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

import numpy as np

# Default acceptance thresholds — chosen to match the docstrings in the
# blueprint's W3 backtest engine section. Callers may override per-call.
DEFAULT_MIN_TEST_SHARPE = 0.5
DEFAULT_MAX_TEST_DRAWDOWN = 0.10


@dataclass(frozen=True, slots=True)
class RegimeStats:
    """Aggregated walk-forward stats for a single regime label."""

    regime_label: str
    n_windows: int
    mean_test_sharpe: float
    median_test_sharpe: float
    worst_test_max_dd: float  # the largest (worst) drawdown observed
    passed: bool


@dataclass(frozen=True, slots=True)
class GateDecision:
    """Final acceptance decision for the OOS gate."""

    passed: bool
    reason: str
    per_regime_stats: dict[str, RegimeStats] = field(default_factory=dict)


def oos_gate(
    results: Iterable[tuple[float, float, str | None]],
    *,
    min_test_sharpe: float = DEFAULT_MIN_TEST_SHARPE,
    max_test_drawdown: float = DEFAULT_MAX_TEST_DRAWDOWN,
    unknown_regime_label: str = "unknown",
) -> GateDecision:
    """Apply the regime-segmented OOS acceptance gate.

    Args:
        results: iterable of (test_sharpe, test_max_dd, regime_label) tuples,
            shaped to match `db.models.WalkForwardResult` rows. A `None`
            regime_label is bucketed under `unknown_regime_label`.
        min_test_sharpe: per-regime floor on the mean test_sharpe.
        max_test_drawdown: per-regime ceiling on the worst test_max_dd
            (drawdowns are positive magnitudes; lower is better).
        unknown_regime_label: bucket name for rows with regime_label=None.

    Returns:
        GateDecision with passed/reason and per-regime aggregated stats.
        Passes iff every distinct regime clears both thresholds.
    """
    # Bucket the rows by regime, materialising tuples once.
    buckets: dict[str, list[tuple[float, float]]] = {}
    for sharpe, max_dd, raw_label in results:
        label = raw_label if raw_label is not None else unknown_regime_label
        buckets.setdefault(label, []).append((float(sharpe), float(max_dd)))

    if not buckets:
        return GateDecision(
            passed=False,
            reason="no walk-forward results supplied",
            per_regime_stats={},
        )

    per_regime: dict[str, RegimeStats] = {}
    failures: list[str] = []

    for label, rows in buckets.items():
        sharpes = np.asarray([r[0] for r in rows], dtype=float)
        dds = np.asarray([r[1] for r in rows], dtype=float)
        mean_sharpe = float(sharpes.mean())
        median_sharpe = float(np.median(sharpes))
        worst_dd = float(dds.max())
        regime_passed = (
            mean_sharpe > min_test_sharpe and worst_dd < max_test_drawdown
        )
        per_regime[label] = RegimeStats(
            regime_label=label,
            n_windows=len(rows),
            mean_test_sharpe=mean_sharpe,
            median_test_sharpe=median_sharpe,
            worst_test_max_dd=worst_dd,
            passed=regime_passed,
        )
        if not regime_passed:
            reasons: list[str] = []
            if mean_sharpe <= min_test_sharpe:
                reasons.append(
                    f"mean_test_sharpe={mean_sharpe:.3f} <= "
                    f"min_test_sharpe={min_test_sharpe:.3f}"
                )
            if worst_dd >= max_test_drawdown:
                reasons.append(
                    f"worst_test_max_dd={worst_dd:.3f} >= "
                    f"max_test_drawdown={max_test_drawdown:.3f}"
                )
            failures.append(f"regime '{label}': {'; '.join(reasons)}")

    if failures:
        return GateDecision(
            passed=False,
            reason="; ".join(failures),
            per_regime_stats=per_regime,
        )
    return GateDecision(
        passed=True,
        reason=(
            f"all {len(per_regime)} regime(s) cleared "
            f"sharpe > {min_test_sharpe} and max_dd < {max_test_drawdown}"
        ),
        per_regime_stats=per_regime,
    )
