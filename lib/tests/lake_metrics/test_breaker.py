"""Tests for the per-template aggregate-performance breaker."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest
from icarus.lake_metrics import TemplateBreaker

EXPECTED_SHARPE = 1.5
SIGMA = 0.4
BASE_TS = datetime(2026, 5, 1, tzinfo=UTC)


def _make_breaker(
    *,
    sigma_threshold: float = 2.0,
    window: timedelta = timedelta(days=14),
    min_observations: int = 3,
) -> TemplateBreaker:
    return TemplateBreaker(
        expected_sharpe=EXPECTED_SHARPE,
        sigma=SIGMA,
        sigma_threshold=sigma_threshold,
        window=window,
        min_observations=min_observations,
    )


def test_does_not_fire_within_one_sigma_of_expected() -> None:
    """(a) breaker is silent when realised stays within +/- 1 sigma of expected."""
    rng = np.random.default_rng(seed=42)
    breaker = _make_breaker()

    # 14 daily observations, drawn from N(expected, sigma) — never wanders
    # more than ~1 sigma from the mean of the window.
    observations = rng.normal(loc=EXPECTED_SHARPE, scale=SIGMA, size=14)

    fired_any = False
    for i, x in enumerate(observations):
        ts = BASE_TS + timedelta(days=i)
        fired_any = breaker.update(ts, float(x)) or fired_any

    assert fired_any is False
    assert breaker.tripped is False


def test_fires_when_realized_drops_to_minus_two_sigma_over_window() -> None:
    """(b) breaker trips when 14-day mean realised drops to expected - 2 sigma."""
    breaker = _make_breaker()

    # Feed 14 daily observations all sitting at the -2 sigma floor; the rolling
    # mean equals exactly the threshold, so we push slightly below it to
    # guarantee a trip.
    drop_value = EXPECTED_SHARPE - 2.0 * SIGMA - 0.01

    fired_at: int | None = None
    for i in range(14):
        ts = BASE_TS + timedelta(days=i)
        if breaker.update(ts, drop_value):
            fired_at = i
            break

    assert fired_at is not None, "breaker failed to fire on persistent -2 sigma drop"
    assert breaker.tripped is True


def test_fourteen_day_window_discards_stale_observations() -> None:
    """(c) observations older than 14d relative to newest are evicted.

    Seed the breaker with a handful of catastrophically negative samples
    that fall just short of tripping it (min_observations is set so the
    early samples can't trip on their own), then jump >14 days forward
    and feed healthy samples. If the rolling window correctly evicts
    stale samples, the breaker stays silent and `.window_size` reflects
    only the fresh samples.
    """
    # Pick min_observations > our seed count so the stale catastrophic
    # samples cannot fire on their own.
    breaker = _make_breaker(min_observations=10)

    seed_count = 5
    for i in range(seed_count):
        fired = breaker.update(
            BASE_TS + timedelta(days=i),
            EXPECTED_SHARPE - 5.0 * SIGMA,
        )
        assert fired is False
    assert breaker.tripped is False

    # Roll the clock forward by > 14 days and feed healthy observations.
    # The stale negative samples must be evicted; the breaker must not fire.
    healthy_start = BASE_TS + timedelta(days=30)
    for i in range(14):
        fired = breaker.update(
            healthy_start + timedelta(days=i),
            EXPECTED_SHARPE,  # exactly on expected
        )
        assert fired is False

    assert breaker.tripped is False
    # And the window now contains only the 14 fresh samples — the
    # `seed_count` stale ones must have been evicted.
    assert breaker.window_size == 14


def test_min_observations_blocks_early_trip() -> None:
    """A single catastrophic observation cannot trip before min_observations."""
    breaker = _make_breaker(min_observations=5)

    # One enormous drop, but only one observation — must not fire.
    fired = breaker.update(BASE_TS, EXPECTED_SHARPE - 10.0 * SIGMA)
    assert fired is False
    assert breaker.tripped is False


def test_breaker_latches_after_tripping() -> None:
    """Once tripped, .update keeps returning True; recovery does not re-arm."""
    breaker = _make_breaker(min_observations=1)
    floor = EXPECTED_SHARPE - 3.0 * SIGMA

    # Force a trip.
    for i in range(3):
        breaker.update(BASE_TS + timedelta(days=i), floor)
    assert breaker.tripped is True

    # Feed healthy recovery samples — the latch must persist.
    for i in range(14):
        assert (
            breaker.update(
                BASE_TS + timedelta(days=30 + i), EXPECTED_SHARPE
            )
            is True
        )


def test_rejects_out_of_order_timestamps() -> None:
    """Defensive: caller must feed timestamps in non-decreasing order."""
    breaker = _make_breaker()
    breaker.update(BASE_TS + timedelta(days=2), EXPECTED_SHARPE)
    with pytest.raises(ValueError, match="non-decreasing order"):
        breaker.update(BASE_TS, EXPECTED_SHARPE)


def test_constructor_rejects_invalid_parameters() -> None:
    """Defensive: zero/negative sigma, threshold, window must be rejected."""
    with pytest.raises(ValueError, match="sigma must be positive"):
        TemplateBreaker(expected_sharpe=1.0, sigma=0.0)
    with pytest.raises(ValueError, match="sigma_threshold must be positive"):
        TemplateBreaker(expected_sharpe=1.0, sigma=0.1, sigma_threshold=-1.0)
    with pytest.raises(ValueError, match="window must be positive"):
        TemplateBreaker(
            expected_sharpe=1.0, sigma=0.1, window=timedelta(0)
        )
    with pytest.raises(ValueError, match="min_observations must be >= 1"):
        TemplateBreaker(expected_sharpe=1.0, sigma=0.1, min_observations=0)


def test_threshold_matches_expected_minus_two_sigma() -> None:
    """The exposed `.threshold` matches the blueprint's 2-sigma floor."""
    breaker = _make_breaker()
    assert breaker.threshold == pytest.approx(EXPECTED_SHARPE - 2.0 * SIGMA)
