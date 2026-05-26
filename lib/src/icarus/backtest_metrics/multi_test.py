"""Multi-test correction — Bonferroni and Benjamini-Hochberg (FDR).

When the lake searches many parameter combinations in parallel, the
probability that *at least one* yields a false-positive Sharpe rises with
the cohort size N. The deflated Sharpe ratio (DSR) corrects each candidate's
own Sharpe for the n_trials that produced it, but it does not enforce a
cohort-level family-wise or false-discovery bound across the *survivors*
that the promotion pipeline considers together.

This module supplies that second layer:

- `bonferroni_correct` — family-wise error rate (FWER) control by dividing
  the per-test alpha by N. Conservative; appropriate when even one false
  positive is unacceptable.
- `benjamini_hochberg` — false-discovery rate (FDR) control by accepting the
  largest k such that p_(k) <= k/N * alpha (Benjamini & Hochberg, 1995).
  Less conservative than Bonferroni; better statistical power.
- `deflated_sharpe_to_pvalue` — DSR is the survival probability under H0,
  so the corresponding p-value is simply 1 - DSR. Wrapped as a function for
  call-site clarity and to give the conversion a named home in tests.
- `apply_cohort_correction` — full pipeline: DSR → p → correction → result.

All functions are pure numpy; no I/O, no logging, no ORM. The intended
caller is the promotion gate inside `lake-governor` (and any backtest-worker
post-processing step that needs cohort-level correction).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np


@dataclass(frozen=True, slots=True)
class CohortCorrectionResult:
    """Outcome of applying a multi-test correction to a candidate cohort."""

    survives: np.ndarray  # bool array, shape (n_tested,)
    corrected_alpha: float
    n_survived: int
    n_tested: int
    method: str


def bonferroni_correct(
    p_values: np.ndarray,
    alpha: float = 0.05,
) -> np.ndarray:
    """Bonferroni family-wise-error-rate correction.

    A p-value survives iff `p <= alpha / N`, where N is the cohort size.
    Returns a boolean array the same shape as `p_values` indicating which
    entries clear the corrected threshold.

    Args:
        p_values: 1-D array of p-values in [0, 1].
        alpha: family-wise significance level (default 0.05).

    Returns:
        Boolean numpy array, True where the candidate survives correction.
    """
    arr = np.asarray(p_values, dtype=float).ravel()
    n = arr.size
    if n == 0:
        return np.zeros(0, dtype=bool)
    corrected = alpha / n
    return arr <= corrected


def benjamini_hochberg(
    p_values: np.ndarray,
    alpha: float = 0.05,
) -> np.ndarray:
    """Benjamini-Hochberg false-discovery-rate correction.

    Sorts p-values in ascending order, finds the largest rank k (1-indexed)
    such that `p_(k) <= k/N * alpha`, and rejects every hypothesis whose
    p-value is at rank <= k. Returns a boolean array in the *original* input
    order indicating which entries survive.

    Args:
        p_values: 1-D array of p-values in [0, 1].
        alpha: target false-discovery rate (default 0.05).

    Returns:
        Boolean numpy array, True where the candidate survives correction.
    """
    arr = np.asarray(p_values, dtype=float).ravel()
    n = arr.size
    if n == 0:
        return np.zeros(0, dtype=bool)

    order = np.argsort(arr, kind="mergesort")
    sorted_p = arr[order]
    ranks = np.arange(1, n + 1, dtype=float)
    thresholds = ranks * alpha / n
    below = sorted_p <= thresholds
    if not below.any():
        return np.zeros(n, dtype=bool)
    # Largest k (1-indexed) with p_(k) <= k/N * alpha; reject ranks <= k.
    k = int(np.max(np.where(below)[0])) + 1
    survives_sorted = np.zeros(n, dtype=bool)
    survives_sorted[:k] = True
    # Map back to original positions.
    survives = np.empty(n, dtype=bool)
    survives[order] = survives_sorted
    return survives


def deflated_sharpe_to_pvalue(dsr: float) -> float:
    """Convert a deflated Sharpe ratio (DSR) to a p-value.

    DSR is the probability — under the null of true Sharpe = 0 — that the
    observed Sharpe survives all of Bailey & López de Prado's deflations.
    A DSR close to 1 means the candidate is unlikely to be an artifact, so
    the corresponding p-value is `1 - DSR`.

    Args:
        dsr: deflated Sharpe ratio in [0, 1].

    Returns:
        p-value = 1 - dsr, clipped to [0, 1] for numerical safety.
    """
    p = 1.0 - float(dsr)
    if p < 0.0:
        return 0.0
    if p > 1.0:
        return 1.0
    return p


def apply_cohort_correction(
    deflated_sharpes: np.ndarray,
    *,
    method: Literal["bonferroni", "benjamini_hochberg"] = "benjamini_hochberg",
    alpha: float = 0.05,
) -> CohortCorrectionResult:
    """Apply a multi-test correction to a cohort of deflated Sharpe ratios.

    Converts each DSR to a p-value (1 - DSR), runs the chosen correction,
    and packages the outcome with bookkeeping metadata so the caller can
    log which candidates survived and why.

    Args:
        deflated_sharpes: 1-D array of DSR values in [0, 1], one per
            candidate in the cohort.
        method: which correction to apply. "bonferroni" controls FWER;
            "benjamini_hochberg" controls FDR (default, more powerful).
        alpha: significance level / target FDR (default 0.05).

    Returns:
        CohortCorrectionResult with the boolean survival mask, the
        corrected alpha (alpha/N for Bonferroni, the alpha passed through
        for BH), counts, and the method name.

    Raises:
        ValueError: if `method` is not one of the supported correction names.
    """
    arr = np.asarray(deflated_sharpes, dtype=float).ravel()
    n_tested = int(arr.size)

    if n_tested == 0:
        return CohortCorrectionResult(
            survives=np.zeros(0, dtype=bool),
            corrected_alpha=alpha,
            n_survived=0,
            n_tested=0,
            method=method,
        )

    p_values = np.array([deflated_sharpe_to_pvalue(float(d)) for d in arr], dtype=float)

    if method == "bonferroni":
        survives = bonferroni_correct(p_values, alpha=alpha)
        corrected_alpha = alpha / n_tested
    elif method == "benjamini_hochberg":
        survives = benjamini_hochberg(p_values, alpha=alpha)
        # For BH the "corrected alpha" is the largest threshold that admitted
        # a candidate; if nothing survives we report the smallest tier
        # (alpha/N) for symmetry with Bonferroni.
        if survives.any():
            k = int(survives.sum())
            corrected_alpha = k * alpha / n_tested
        else:
            corrected_alpha = alpha / n_tested
    else:
        raise ValueError(
            f"unknown method {method!r}; expected 'bonferroni' or 'benjamini_hochberg'"
        )

    return CohortCorrectionResult(
        survives=survives,
        corrected_alpha=float(corrected_alpha),
        n_survived=int(survives.sum()),
        n_tested=n_tested,
        method=method,
    )
