"""Tests for the Bailey & López de Prado (2014) deflated Sharpe ratio.

The four properties below collectively pin the implementation to the
published formula: the multiple-testing penalty (a), the no-deflation
identity (b), the moment-correction direction (c), and a numerical
regression at a known operating point (d).
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from icarus.backtest_metrics import deflated_sharpe
from scipy import stats


def _raw_sharpe_dsr(returns: np.ndarray) -> float:
    """Probabilistic Sharpe ratio at n_trials=1, g3=0, g4=3 (the no-deflation
    case from Bailey-Lopez de Prado).

    With multiple-testing turned off (sr0=0) and Gaussian moments, the formula
    reduces to Phi(SR * sqrt(T-1) / sqrt(1 + 0.5 * SR**2)).
    """
    std = float(returns.std(ddof=1))
    sr = float(returns.mean()) / std
    denom = math.sqrt(1.0 + 0.5 * sr**2)
    return float(stats.norm.cdf(sr * math.sqrt(returns.size - 1) / denom))


def test_deflated_lt_raw_when_many_trials() -> None:
    """(a) Multiple testing penalty: deflated < raw Sharpe when n_trials > 1."""
    rng = np.random.default_rng(seed=42)
    returns = rng.normal(loc=0.001, scale=0.01, size=500)

    dsr_one = deflated_sharpe(returns, n_trials=1)
    dsr_many = deflated_sharpe(returns, n_trials=1000)

    assert dsr_many < dsr_one
    # Sanity: a positive-mean draw should still produce a meaningful DSR
    # at n_trials=1 (above the chance level of 0.5).
    assert dsr_one > 0.5


def test_deflated_equals_raw_when_n_trials_one_and_normal() -> None:
    """(b) Identity: DSR collapses to the probabilistic Sharpe ratio when
    n_trials=1, g3=0, g4=3 (no multiple-testing penalty; Gaussian moments)."""
    rng = np.random.default_rng(seed=42)
    returns = rng.normal(loc=0.001, scale=0.01, size=500)

    dsr = deflated_sharpe(returns, n_trials=1, skew=0.0, kurtosis=3.0)
    raw = _raw_sharpe_dsr(returns)

    assert dsr == pytest.approx(raw, rel=1e-10, abs=1e-10)


def test_higher_skew_lower_kurtosis_raises_dsr() -> None:
    """(c) Positive skew + tame kurtosis -> larger denominator-friendly term.

    The deflated SR denominator is sqrt(1 - g3*SR + ((g4-1)/4)*SR^2). For a
    positive observed SR, raising g3 and lowering g4 both shrink the
    denominator, raising the z-score and therefore the DSR.
    """
    rng = np.random.default_rng(seed=42)
    returns = rng.normal(loc=0.001, scale=0.01, size=500)

    baseline = deflated_sharpe(returns, n_trials=10, skew=0.0, kurtosis=5.0)
    friendly = deflated_sharpe(returns, n_trials=10, skew=1.5, kurtosis=2.5)

    assert friendly > baseline


def test_regression_known_operating_point() -> None:
    """(d) Regression test at a hand-computable operating point.

    With:
      - sr_hat       = 0.1 (per-period)
      - T            = 101 => sqrt(T-1) = 10
      - n_trials     = 1   => sr0 = 0
      - skew g3      = 0
      - kurtosis g4  = 3
    the denominator collapses to sqrt(1 + 0.5 * 0.01) = sqrt(1.005) ~= 1.0025,
    so z = 0.1 * 10 / 1.0025 ~= 0.99751 and DSR = Phi(0.99751) ~= 0.84074.

    We construct returns whose exact sample mean/std hit SR = 0.1 by
    overriding skew/kurtosis (so the test is independent of higher moments
    of the synthetic draw).
    """
    rng = np.random.default_rng(seed=42)
    raw = rng.normal(size=101)
    # Standardise then rescale so std=1 (ddof=1) and mean=0.1 exactly.
    raw = (raw - raw.mean()) / raw.std(ddof=1)
    returns = raw + 0.1
    assert returns.mean() == pytest.approx(0.1, abs=1e-12)
    assert returns.std(ddof=1) == pytest.approx(1.0, abs=1e-12)

    dsr = deflated_sharpe(returns, n_trials=1, skew=0.0, kurtosis=3.0)

    expected_z = 0.1 * 10.0 / math.sqrt(1.0 + 0.5 * 0.01)
    expected = float(stats.norm.cdf(expected_z))
    assert dsr == pytest.approx(expected, rel=1e-10, abs=1e-10)
    # Tie to the human-readable number from the docstring.
    assert dsr == pytest.approx(0.84074, abs=1e-4)


def test_input_validation() -> None:
    """Boundary checks: bad inputs raise instead of silently mis-computing."""
    with pytest.raises(ValueError):
        deflated_sharpe(np.array([0.1]), n_trials=1)
    with pytest.raises(ValueError):
        deflated_sharpe(np.zeros((2, 2)), n_trials=1)
    with pytest.raises(ValueError):
        deflated_sharpe(np.zeros(10), n_trials=0)
