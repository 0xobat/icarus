"""Tests for multi-test correction (Bonferroni + Benjamini-Hochberg)."""

from __future__ import annotations

import numpy as np
import pytest
from icarus.backtest_metrics import (
    CohortCorrectionResult,
    apply_cohort_correction,
    benjamini_hochberg,
    bonferroni_correct,
    deflated_sharpe_to_pvalue,
)


def test_bonferroni_only_smallest_p_survives() -> None:
    """(a) Bonferroni at alpha=0.05, N=3 → threshold 0.0167; only p=0.01 survives."""
    p = np.array([0.01, 0.04, 0.5])

    survives = bonferroni_correct(p, alpha=0.05)

    assert survives.dtype == bool
    assert survives.tolist() == [True, False, False]


def test_benjamini_hochberg_matches_classical_step_up() -> None:
    """(b) BH at alpha=0.05, N=3 on [0.01, 0.04, 0.5].

    Thresholds (k/N * alpha) for k=1,2,3 are 0.0167, 0.0333, 0.05.
    Sorted p-values 0.01, 0.04, 0.5 → only p_(1)=0.01 clears its threshold,
    so largest passing k=1 and only the first hypothesis is rejected.
    Matches `scipy.stats.false_discovery_control` (BH adjusted p-values
    are 0.03, 0.06, 0.5).
    """
    p = np.array([0.01, 0.04, 0.5])

    survives = benjamini_hochberg(p, alpha=0.05)

    assert survives.dtype == bool
    assert survives.tolist() == [True, False, False]


def test_benjamini_hochberg_more_permissive_than_bonferroni_when_they_differ() -> None:
    """BH admits a candidate Bonferroni rejects when the cohort is bigger.

    With N=5 and a moderate p like 0.02, Bonferroni's threshold (0.01) rejects
    it but BH's rank-2 threshold (0.02) admits it together with a smaller p.
    """
    p = np.array([0.005, 0.02, 0.5, 0.6, 0.7])

    bh = benjamini_hochberg(p, alpha=0.05)
    bonf = bonferroni_correct(p, alpha=0.05)

    # Bonferroni threshold = 0.01; only p=0.005 clears it.
    assert bonf.tolist() == [True, False, False, False, False]
    # BH at rank 2: threshold 0.02; both p=0.005 and p=0.02 survive.
    assert bh.tolist() == [True, True, False, False, False]


def test_benjamini_hochberg_preserves_input_order() -> None:
    """BH returns survival mask in original input order, not sorted order."""
    # Same cohort as the classical-step-up test, shuffled — only the original
    # position of the smallest p (now at index 1) survives.
    p = np.array([0.5, 0.01, 0.04])

    survives = benjamini_hochberg(p, alpha=0.05)

    assert survives.tolist() == [False, True, False]


def test_benjamini_hochberg_uses_largest_surviving_rank() -> None:
    """BH rejects all ranks <= k where k is the largest passing rank.

    A middle p-value that fails its own k/N threshold can still be rejected
    if a *later* p-value passes — this guards against a too-greedy variant
    that stops at the first failure.
    """
    # N=5, alpha=0.05 → thresholds 0.01, 0.02, 0.03, 0.04, 0.05.
    # p_(1)=0.005 PASS, p_(2)=0.025 FAIL, p_(3)=0.028 FAIL, p_(4)=0.035 PASS,
    # p_(5)=0.5 FAIL → largest passing rank is 4, reject ranks 1..4.
    p = np.array([0.005, 0.025, 0.028, 0.035, 0.5])

    survives = benjamini_hochberg(p, alpha=0.05)

    assert survives.tolist() == [True, True, True, True, False]


def test_deflated_sharpe_to_pvalue_boundaries() -> None:
    """(c) DSR → p-value: 0.95 → 0.05, 1.0 → 0, 0.0 → 1.0."""
    assert deflated_sharpe_to_pvalue(0.95) == pytest.approx(0.05)
    assert deflated_sharpe_to_pvalue(1.0) == pytest.approx(0.0)
    assert deflated_sharpe_to_pvalue(0.0) == pytest.approx(1.0)


def test_deflated_sharpe_to_pvalue_clips_out_of_range() -> None:
    """Numerical safety: clip p into [0, 1] for DSR values slightly outside."""
    assert deflated_sharpe_to_pvalue(1.0001) == 0.0
    assert deflated_sharpe_to_pvalue(-0.0001) == 1.0


def test_apply_cohort_correction_round_trip_bh() -> None:
    """(d) Round-trip: apply BH to a DSR cohort → first two survive."""
    # DSR=[0.99, 0.95, 0.5] → p=[0.01, 0.05, 0.5]
    # BH thresholds at alpha=0.05, N=3: 0.0167, 0.0333, 0.05
    # p_(1)=0.01 PASS, p_(2)=0.05 FAIL, p_(3)=0.5 FAIL — but k looks at largest
    # passing, so only p_(1) survives... that's BH math, not what spec (d)
    # claims. Use a DSR cohort that actually leaves two survivors.
    # DSR=[0.99, 0.97, 0.5] → p=[0.01, 0.03, 0.5]
    # thresholds 0.0167, 0.0333, 0.05 → p_(1) PASS, p_(2) PASS → k=2.
    dsrs = np.array([0.99, 0.97, 0.5])

    result = apply_cohort_correction(dsrs, method="benjamini_hochberg", alpha=0.05)

    assert isinstance(result, CohortCorrectionResult)
    assert result.method == "benjamini_hochberg"
    assert result.n_tested == 3
    assert result.n_survived == 2
    assert result.survives.tolist() == [True, True, False]
    # corrected_alpha reflects the rank-2 threshold that admitted survivors.
    assert result.corrected_alpha == pytest.approx(2 * 0.05 / 3)


def test_apply_cohort_correction_empty() -> None:
    """(e) Empty cohort → empty result, no exceptions."""
    result = apply_cohort_correction(np.array([]), method="benjamini_hochberg")

    assert isinstance(result, CohortCorrectionResult)
    assert result.n_tested == 0
    assert result.n_survived == 0
    assert result.survives.shape == (0,)
    assert result.survives.dtype == bool
    assert result.method == "benjamini_hochberg"


def test_apply_cohort_correction_empty_bonferroni() -> None:
    """Empty cohort under Bonferroni also returns clean empty result."""
    result = apply_cohort_correction(np.array([]), method="bonferroni")

    assert result.n_tested == 0
    assert result.n_survived == 0
    assert result.survives.shape == (0,)


def test_single_candidate_bh_equals_bonferroni_equals_raw_alpha() -> None:
    """(f) N=1: BH == Bonferroni == raw p-value comparison against alpha."""
    # DSR=0.97 → p=0.03; under any correction at alpha=0.05, N=1 the threshold
    # is exactly alpha, so 0.03 <= 0.05 survives.
    dsrs = np.array([0.97])
    expected_p_survives = 0.03 <= 0.05

    bh = apply_cohort_correction(dsrs, method="benjamini_hochberg", alpha=0.05)
    bonf = apply_cohort_correction(dsrs, method="bonferroni", alpha=0.05)
    raw_bonf = bonferroni_correct(np.array([0.03]), alpha=0.05)
    raw_bh = benjamini_hochberg(np.array([0.03]), alpha=0.05)

    assert bh.survives.tolist() == [expected_p_survives]
    assert bonf.survives.tolist() == [expected_p_survives]
    assert raw_bonf.tolist() == [expected_p_survives]
    assert raw_bh.tolist() == [expected_p_survives]
    assert bh.corrected_alpha == pytest.approx(0.05)
    assert bonf.corrected_alpha == pytest.approx(0.05)


def test_single_candidate_failing_p() -> None:
    """N=1 with p > alpha: both methods reject, corrected_alpha = alpha/N."""
    dsrs = np.array([0.5])  # p = 0.5

    bh = apply_cohort_correction(dsrs, method="benjamini_hochberg", alpha=0.05)
    bonf = apply_cohort_correction(dsrs, method="bonferroni", alpha=0.05)

    assert bh.survives.tolist() == [False]
    assert bonf.survives.tolist() == [False]
    # No survivors → both report alpha/N as the corrected floor.
    assert bh.corrected_alpha == pytest.approx(0.05)
    assert bonf.corrected_alpha == pytest.approx(0.05)


def test_apply_cohort_correction_bonferroni_corrected_alpha() -> None:
    """Bonferroni reports corrected_alpha = alpha / N."""
    dsrs = np.array([0.99, 0.97, 0.5, 0.4])

    result = apply_cohort_correction(dsrs, method="bonferroni", alpha=0.05)

    assert result.corrected_alpha == pytest.approx(0.05 / 4)
    # p = [0.01, 0.03, 0.5, 0.6]; threshold 0.0125 → only p=0.01 survives.
    assert result.survives.tolist() == [True, False, False, False]
    assert result.n_survived == 1


def test_apply_cohort_correction_rejects_unknown_method() -> None:
    """Unknown method → ValueError mentioning the bad name."""
    with pytest.raises(ValueError, match="bogus"):
        apply_cohort_correction(np.array([0.99]), method="bogus")  # type: ignore[arg-type]


def test_bonferroni_empty_input() -> None:
    """Empty p-value array → empty bool array (no division-by-zero)."""
    result = bonferroni_correct(np.array([]), alpha=0.05)

    assert result.shape == (0,)
    assert result.dtype == bool


def test_benjamini_hochberg_empty_input() -> None:
    """Empty p-value array → empty bool array."""
    result = benjamini_hochberg(np.array([]), alpha=0.05)

    assert result.shape == (0,)
    assert result.dtype == bool


def test_benjamini_hochberg_all_fail() -> None:
    """When no p-value clears its rank threshold, nothing survives."""
    p = np.array([0.5, 0.6, 0.9])

    survives = benjamini_hochberg(p, alpha=0.05)

    assert survives.tolist() == [False, False, False]


def test_apply_cohort_correction_all_dsr_one() -> None:
    """Pathological perfect-DSR cohort → all p=0 → everyone survives."""
    dsrs = np.array([1.0, 1.0, 1.0])

    bh = apply_cohort_correction(dsrs, method="benjamini_hochberg")
    bonf = apply_cohort_correction(dsrs, method="bonferroni")

    assert bh.n_survived == 3
    assert bonf.n_survived == 3
