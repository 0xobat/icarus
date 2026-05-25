"""Deflated Sharpe ratio — Bailey & López de Prado (2014).

The deflated Sharpe ratio (DSR) corrects an observed Sharpe ratio for:
  1. Non-normality (skew g3 and kurtosis g4 of the return distribution).
  2. Sample length T (finite-sample bias).
  3. Multiple testing (n_trials: how many parameter combinations were
     searched before this candidate was picked).

It returns the probability — under the null of true Sharpe = 0 — that the
observed Sharpe survives all three deflations. A DSR close to 1 means the
candidate is unlikely to be a backtest-overfitting artifact.

Reference:
  Bailey, D. H. & López de Prado, M. (2014).
  "The Deflated Sharpe Ratio: Correcting for Selection Bias, Backtest
  Overfitting, and Non-Normality."
  Journal of Portfolio Management 40 (5), pp. 94-107.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np
from scipy import stats

# Euler-Mascheroni constant, used in the expected-maximum-of-N-Sharpes term.
_EULER_MASCHERONI = 0.5772156649015328606


def _sharpe_ratio(returns: np.ndarray) -> float:
    """Per-period Sharpe ratio (no annualization, no risk-free rate).

    Matches the form used in the original Bailey & López de Prado derivation,
    which works in the same time unit as the input returns.
    """
    std = float(returns.std(ddof=1))
    if std == 0.0:
        return 0.0
    return float(returns.mean()) / std


def _expected_max_sharpe(n_trials: int, sr_variance: float) -> float:
    """Expected maximum of N i.i.d. Sharpe ratios under H0 (true SR = 0).

    Eq. (5) in Bailey & López de Prado (2014):

        E[max_N SR] ~= sqrt(V[SR]) * (
            (1 - g) * Z^{-1}(1 - 1/N)
            +     g * Z^{-1}(1 - 1/(N*e))
        )

    where g is the Euler-Mascheroni constant and Z^{-1} is the inverse
    standard-normal CDF.
    """
    if n_trials <= 1:
        return 0.0
    sqrt_v = math.sqrt(sr_variance)
    a = stats.norm.ppf(1.0 - 1.0 / n_trials)
    b = stats.norm.ppf(1.0 - 1.0 / (n_trials * math.e))
    return sqrt_v * ((1.0 - _EULER_MASCHERONI) * a + _EULER_MASCHERONI * b)


def deflated_sharpe(
    returns: Sequence[float] | np.ndarray,
    n_trials: int,
    *,
    skew: float | None = None,
    kurtosis: float | None = None,
    sr_variance: float | None = None,
) -> float:
    """Deflated Sharpe ratio (Bailey & López de Prado, 2014).

    Args:
        returns: per-period strategy returns (length T >= 2).
        n_trials: number of independent parameter combinations searched
            before this candidate was selected (the multiple-testing N).
        skew: override for sample skewness g3. If None, computed from returns.
        kurtosis: override for sample kurtosis g4 (NOT excess; normal = 3).
            If None, computed from returns (non-excess).
        sr_variance: override for V[SR] across the n_trials. If None,
            approximated from this candidate's own returns via the
            non-normal-adjusted asymptotic variance, 1/(T-1).

    Returns:
        DSR in [0, 1] — the probability that the true Sharpe exceeds the
        expected-max-of-N benchmark under the null hypothesis.
    """
    arr = np.asarray(returns, dtype=float)
    if arr.ndim != 1:
        raise ValueError("returns must be 1-D")
    n_obs = arr.size
    if n_obs < 2:
        raise ValueError("returns must have length >= 2")
    if n_trials < 1:
        raise ValueError("n_trials must be >= 1")

    sr_hat = _sharpe_ratio(arr)
    # Degenerate input (zero-variance returns) → sr_hat=0 from _sharpe_ratio
    # AND skew/kurtosis are mathematically undefined (would propagate as nan).
    # Coherent semantic: no detected skill ⇒ DSR = 0.0 (no probability of
    # skill above the expected-max-N benchmark). Matches the _sharpe_ratio
    # zero-std convention; spares every caller a nan-guard.
    if float(arr.std(ddof=1)) == 0.0:
        return 0.0
    g3 = float(stats.skew(arr, bias=False)) if skew is None else float(skew)
    # scipy.stats.kurtosis is excess (Fisher) by default; the formula expects
    # the non-excess (Pearson) g4 where a normal distribution has g4 = 3.
    g4 = (
        float(stats.kurtosis(arr, fisher=False, bias=False))
        if kurtosis is None
        else float(kurtosis)
    )
    v_sr = 1.0 / (n_obs - 1) if sr_variance is None else float(sr_variance)

    sr0 = _expected_max_sharpe(n_trials, v_sr)

    # Denominator of Eq. (9): standard error of SR under non-normality.
    denom_var = 1.0 - g3 * sr_hat + ((g4 - 1.0) / 4.0) * (sr_hat**2)
    if denom_var <= 0.0:
        # Pathological sample (heavy left tail w/ high SR) — DSR undefined.
        return float("nan")
    z = (sr_hat - sr0) * math.sqrt(n_obs - 1) / math.sqrt(denom_var)
    return float(stats.norm.cdf(z))
