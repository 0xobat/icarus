"""Tests for the regime-segmented OOS acceptance gate."""

from __future__ import annotations

import numpy as np
from icarus.backtest_metrics import RegimeStats, oos_gate


def _make_results(
    rng: np.random.Generator,
    *,
    regime: str,
    n: int,
    sharpe_loc: float,
    dd_loc: float,
) -> list[tuple[float, float, str | None]]:
    """Build n synthetic walk-forward result tuples for one regime."""
    sharpes = rng.normal(loc=sharpe_loc, scale=0.05, size=n)
    dds = np.clip(rng.normal(loc=dd_loc, scale=0.005, size=n), a_min=0.0, a_max=None)
    return [(float(s), float(d), regime) for s, d in zip(sharpes, dds, strict=True)]


def test_pass_when_all_regimes_clear_thresholds() -> None:
    """(a) PASS when every regime shows mean sharpe > 0.5 and max_dd < 0.10."""
    rng = np.random.default_rng(seed=42)
    results = (
        _make_results(rng, regime="bull", n=20, sharpe_loc=1.2, dd_loc=0.03)
        + _make_results(rng, regime="bear", n=20, sharpe_loc=0.9, dd_loc=0.05)
        + _make_results(rng, regime="chop", n=20, sharpe_loc=0.7, dd_loc=0.04)
    )

    decision = oos_gate(results)

    assert decision.passed is True
    assert "3 regime(s)" in decision.reason
    assert set(decision.per_regime_stats) == {"bull", "bear", "chop"}
    for stats in decision.per_regime_stats.values():
        assert stats.passed is True
        assert stats.n_windows == 20


def test_fail_with_reason_when_one_regime_fails() -> None:
    """(b) FAIL with a reason naming the offending regime + thresholds."""
    rng = np.random.default_rng(seed=42)
    results = (
        _make_results(rng, regime="bull", n=20, sharpe_loc=1.2, dd_loc=0.03)
        # 'bear' regime breaches both gates: low sharpe AND high drawdown.
        + _make_results(rng, regime="bear", n=20, sharpe_loc=0.2, dd_loc=0.15)
        + _make_results(rng, regime="chop", n=20, sharpe_loc=0.7, dd_loc=0.04)
    )

    decision = oos_gate(results)

    assert decision.passed is False
    assert "bear" in decision.reason
    assert "mean_test_sharpe" in decision.reason
    assert "worst_test_max_dd" in decision.reason
    assert decision.per_regime_stats["bear"].passed is False
    assert decision.per_regime_stats["bull"].passed is True
    assert decision.per_regime_stats["chop"].passed is True


def test_per_regime_stats_one_entry_per_distinct_label() -> None:
    """(c) per_regime_stats has exactly one entry per distinct regime_label."""
    rng = np.random.default_rng(seed=42)
    results = (
        _make_results(rng, regime="bull", n=5, sharpe_loc=1.0, dd_loc=0.03)
        + _make_results(rng, regime="bull", n=7, sharpe_loc=1.1, dd_loc=0.04)
        + _make_results(rng, regime="bear", n=3, sharpe_loc=0.8, dd_loc=0.05)
    )

    decision = oos_gate(results)

    assert len(decision.per_regime_stats) == 2
    assert decision.per_regime_stats["bull"].n_windows == 12
    assert decision.per_regime_stats["bear"].n_windows == 3
    assert isinstance(decision.per_regime_stats["bull"], RegimeStats)


def test_none_regime_label_bucketed_as_unknown() -> None:
    """Rows with regime_label=None fall into the 'unknown' bucket."""
    rng = np.random.default_rng(seed=42)
    typed = _make_results(rng, regime="bull", n=5, sharpe_loc=1.0, dd_loc=0.03)
    untyped: list[tuple[float, float, str | None]] = [
        (0.9, 0.04, None),
        (1.0, 0.03, None),
    ]
    decision = oos_gate(typed + untyped)

    assert "unknown" in decision.per_regime_stats
    assert decision.per_regime_stats["unknown"].n_windows == 2


def test_empty_input_fails_with_explicit_reason() -> None:
    """Empty input is a fail (vacuous truth would be a footgun for the lake)."""
    decision = oos_gate([])
    assert decision.passed is False
    assert "no walk-forward results" in decision.reason
    assert decision.per_regime_stats == {}
