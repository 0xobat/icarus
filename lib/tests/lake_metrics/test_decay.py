"""Tests for the Page-Hinkley per-candidate decay detector."""

from __future__ import annotations

import numpy as np
import pytest
from icarus.lake_metrics import DetectorState, PageHinkleyDetector

# Synthetic-stream parameters chosen to mimic 7-day rolling-Sharpe noise
# around a paper-trade baseline. `sharpe_sigma=0.3` is loose enough to
# stress the detector while still being realistic for daily rolling
# Sharpe noise in crypto-yield templates.
BASELINE_SHARPE = 1.2
SHARPE_SIGMA = 0.3

# Tuned per blueprint: delta tolerates ~1 sigma of natural noise; lambda picked
# from a paper-trade variance estimate so the false-positive rate on a
# stationary stream of ~100 observations stays well under 5%.
DELTA = 0.30
LAMBDA = 4.0


def _make_detector() -> PageHinkleyDetector:
    return PageHinkleyDetector(
        baseline_sharpe=BASELINE_SHARPE,
        delta=DELTA,
        lambda_threshold=LAMBDA,
    )


def test_does_not_fire_on_stationary_stream() -> None:
    """(a) detector stays silent on a stationary baseline-mean stream."""
    rng = np.random.default_rng(seed=42)
    detector = _make_detector()
    observations = rng.normal(loc=BASELINE_SHARPE, scale=SHARPE_SIGMA, size=100)

    fired_any = any(detector.update(float(x)) for x in observations)

    assert fired_any is False
    assert detector.fired is False
    assert detector.state.n_observations == 100


def test_fires_within_seven_days_of_step_change() -> None:
    """(b) detector fires within 7 daily observations of a step-change drop.

    Mirrors the blueprint's detection-latency target ("typically 3-7
    days" on rolling 7-day Sharpe). After 30 days of stationary
    baseline, the candidate's true mean collapses to baseline - 1.0;
    the detector should fire within 7 post-step observations.
    """
    rng = np.random.default_rng(seed=42)
    detector = _make_detector()

    pre_step = rng.normal(loc=BASELINE_SHARPE, scale=SHARPE_SIGMA, size=30)
    post_step_mean = BASELINE_SHARPE - 1.0
    post_step = rng.normal(loc=post_step_mean, scale=SHARPE_SIGMA, size=20)

    for x in pre_step:
        assert detector.update(float(x)) is False

    fire_index: int | None = None
    for i, x in enumerate(post_step, start=1):
        if detector.update(float(x)):
            fire_index = i
            break

    assert fire_index is not None, "detector failed to fire after step-change"
    assert fire_index <= 7, (
        f"detector took {fire_index} post-step observations; "
        "blueprint target is <= 7 days"
    )


def test_state_round_trips_through_json_without_losing_detection() -> None:
    """(c) DetectorState → JSON → DetectorState preserves detection ability."""
    rng = np.random.default_rng(seed=42)
    detector = _make_detector()

    pre_step = rng.normal(loc=BASELINE_SHARPE, scale=SHARPE_SIGMA, size=20)
    for x in pre_step:
        detector.update(float(x))

    # Serialise mid-stream, simulating a lake-governor restart.
    snapshot_json = detector.state.to_json()
    rehydrated_state = DetectorState.from_json(snapshot_json)
    rehydrated = PageHinkleyDetector(state=rehydrated_state)

    # The rehydrated detector must equal the original by value.
    assert rehydrated.state == detector.state
    assert rehydrated.fired is False

    # Feed the same post-step stream through both detectors; they must
    # fire at the same observation index.
    post_step = rng.normal(loc=BASELINE_SHARPE - 1.0, scale=SHARPE_SIGMA, size=20)

    original_fires: list[bool] = []
    rehydrated_fires: list[bool] = []
    for x in post_step:
        original_fires.append(detector.update(float(x)))
        rehydrated_fires.append(rehydrated.update(float(x)))

    assert original_fires == rehydrated_fires
    assert any(original_fires), "step-change should have fired the detector"


def test_false_positive_rate_under_five_percent_on_stationary_streams() -> None:
    """(d) tuned threshold yields < 5% false-positive rate on 100 streams."""
    parent_rng = np.random.default_rng(seed=42)
    false_positives = 0
    n_streams = 100
    stream_length = 100  # ~100 daily observations per stream

    for _ in range(n_streams):
        # Each stream gets its own independent child RNG.
        child_seed = int(parent_rng.integers(0, 2**32 - 1))
        rng = np.random.default_rng(child_seed)
        detector = _make_detector()
        observations = rng.normal(
            loc=BASELINE_SHARPE, scale=SHARPE_SIGMA, size=stream_length
        )
        fired = any(detector.update(float(x)) for x in observations)
        if fired:
            false_positives += 1

    fp_rate = false_positives / n_streams
    assert fp_rate < 0.05, (
        f"false-positive rate {fp_rate:.2%} >= 5% on {n_streams} stationary streams"
    )


def test_constructor_rejects_mixed_fresh_and_state_kwargs() -> None:
    """Defensive: passing both `state` and fresh kwargs is a programming error."""
    state = DetectorState(baseline=1.0, delta=0.1, lambda_threshold=2.0)
    with pytest.raises(ValueError, match="either `state` or"):
        PageHinkleyDetector(
            baseline_sharpe=1.0,
            delta=0.1,
            lambda_threshold=2.0,
            state=state,
        )


def test_constructor_requires_complete_fresh_parameters() -> None:
    """Defensive: partial fresh-parameter sets must be rejected."""
    with pytest.raises(ValueError, match="fresh construction requires"):
        PageHinkleyDetector(baseline_sharpe=1.0, delta=0.1)


def test_constructor_rejects_invalid_parameters() -> None:
    """Defensive: negative delta and non-positive lambda are rejected."""
    with pytest.raises(ValueError, match="delta must be non-negative"):
        PageHinkleyDetector(
            baseline_sharpe=1.0, delta=-0.1, lambda_threshold=2.0
        )
    with pytest.raises(ValueError, match="lambda_threshold must be positive"):
        PageHinkleyDetector(
            baseline_sharpe=1.0, delta=0.1, lambda_threshold=0.0
        )


def test_detector_latches_after_firing() -> None:
    """Once fired, subsequent updates also return True (no missed edge)."""
    detector = PageHinkleyDetector(
        baseline_sharpe=1.0, delta=0.0, lambda_threshold=0.5
    )
    # Hard drop: a single observation well below baseline must fire because
    # delta=0 and lambda is tiny.
    for _ in range(3):
        detector.update(-5.0)
    assert detector.fired is True
    # Now feed baseline-recovery observations; the latch must persist.
    for _ in range(5):
        assert detector.update(1.0) is True
