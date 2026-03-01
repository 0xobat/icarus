"""Feature extraction from gas history for ML gas prediction models."""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass
class GasFeatures:
    """Feature vector extracted from a gas history snapshot.

    Attributes:
        hour_of_day: Hour of the day (0-23) when the snapshot was taken.
        day_of_week: Day of the week (0=Monday, 6=Sunday).
        gas_mean_1h: Mean gas price over the preceding 1-hour window.
        gas_std_1h: Standard deviation of gas price over the preceding 1-hour window.
        gas_mean_4h: Mean gas price over the preceding 4-hour window.
        gas_mean_24h: Mean gas price over the preceding 24-hour window.
        gas_trend: Linear slope of gas prices over the preceding 1-hour window.
        block_utilization: Block utilization ratio (0.0-1.0) at snapshot time.
    """

    hour_of_day: int
    day_of_week: int
    gas_mean_1h: float
    gas_std_1h: float
    gas_mean_4h: float
    gas_mean_24h: float
    gas_trend: float
    block_utilization: float


def _compute_slope(values: list[float]) -> float:
    """Compute a simple linear slope over evenly-spaced values.

    Uses ordinary least-squares regression against an integer index.
    Returns 0.0 when fewer than two values are provided.
    """
    n = len(values)
    if n < 2:
        return 0.0
    x_mean = (n - 1) / 2.0
    y_mean = statistics.mean(values)
    numerator = sum((i - x_mean) * (v - y_mean) for i, v in enumerate(values))
    denominator = sum((i - x_mean) ** 2 for i in range(n))
    if denominator == 0:
        return 0.0
    return numerator / denominator


def _window_values(
    gas_history: list[dict],
    anchor_ts: float,
    window_seconds: float,
) -> list[float]:
    """Return gwei values from *gas_history* within a time window ending at *anchor_ts*."""
    start = anchor_ts - window_seconds
    return [
        float(entry["gwei"])
        for entry in gas_history
        if start <= float(entry["ts"]) <= anchor_ts
    ]


def extract_features(gas_history: list[dict]) -> list[GasFeatures]:
    """Convert raw gas snapshots to feature vectors.

    Each snapshot dict must contain ``"gwei"`` (gas price in gwei),
    ``"ts"`` (unix epoch timestamp), and optionally ``"block_utilization"``
    (float 0-1, defaults to 0.5 if missing).

    A feature vector is produced for every snapshot that has at least one
    hour of preceding history.

    Args:
        gas_history: Raw gas snapshots sorted by timestamp ascending.

    Returns:
        List of extracted feature vectors, one per eligible snapshot.
    """
    if not gas_history:
        return []

    sorted_history = sorted(gas_history, key=lambda e: float(e["ts"]))
    features: list[GasFeatures] = []

    first_ts = float(sorted_history[0]["ts"])

    for entry in sorted_history:
        ts = float(entry["ts"])
        # Need at least 1h of prior data
        if ts - first_ts < 3600:
            continue

        dt = datetime.fromtimestamp(ts, tz=UTC)

        vals_1h = _window_values(sorted_history, ts, 3600)
        vals_4h = _window_values(sorted_history, ts, 4 * 3600)
        vals_24h = _window_values(sorted_history, ts, 24 * 3600)

        if not vals_1h:
            continue

        gas_std = statistics.stdev(vals_1h) if len(vals_1h) >= 2 else 0.0

        features.append(
            GasFeatures(
                hour_of_day=dt.hour,
                day_of_week=dt.weekday(),
                gas_mean_1h=statistics.mean(vals_1h),
                gas_std_1h=gas_std,
                gas_mean_4h=statistics.mean(vals_4h) if vals_4h else statistics.mean(vals_1h),
                gas_mean_24h=statistics.mean(vals_24h) if vals_24h else statistics.mean(vals_1h),
                gas_trend=_compute_slope(vals_1h),
                block_utilization=float(entry.get("block_utilization", 0.5)),
            )
        )

    return features


def features_to_array(features: list[GasFeatures]) -> list[list[float]]:
    """Convert feature dataclasses to a 2-D numeric array for sklearn.

    Args:
        features: List of GasFeatures dataclass instances.

    Returns:
        List of lists where each inner list is a numeric feature row.
    """
    return [
        [
            float(f.hour_of_day),
            float(f.day_of_week),
            f.gas_mean_1h,
            f.gas_std_1h,
            f.gas_mean_4h,
            f.gas_mean_24h,
            f.gas_trend,
            f.block_utilization,
        ]
        for f in features
    ]
