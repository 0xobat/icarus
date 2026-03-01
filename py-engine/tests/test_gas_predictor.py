"""Tests for ML gas prediction — TEST-003."""

from __future__ import annotations

import time
from decimal import Decimal

import pytest

from ml.feature_engineering import GasFeatures, extract_features, features_to_array
from ml.gas_predictor import (
    GasPrediction,
    GasPredictor,
    GasPredictorConfig,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _make_history(
    n: int,
    *,
    base_gwei: float = 30.0,
    interval_seconds: int = 60,
    start_ts: float | None = None,
    drift: float = 0.0,
) -> list[dict]:
    """Generate *n* synthetic gas snapshots.

    Args:
        n: Number of data points.
        base_gwei: Starting gas price.
        interval_seconds: Seconds between each snapshot.
        start_ts: Starting unix timestamp (defaults to now - n*interval).
        drift: Per-step additive drift in gwei.
    """
    ts = start_ts or (time.time() - n * interval_seconds)
    history: list[dict] = []
    for i in range(n):
        gwei = base_gwei + drift * i + (i % 5) * 0.5  # small cyclic noise
        history.append({"gwei": gwei, "ts": ts + i * interval_seconds})
    return history


def _make_predictable_history(n: int = 864) -> list[dict]:
    """Generate a history with a strong hour-of-day pattern the ML model can learn.

    Gas prices follow a smooth sinusoidal curve driven by hour-of-day.
    Default 864 points at 15-min intervals = 9 days, giving the model
    multiple full diurnal cycles in both train and test splits.

    Args:
        n: Number of data points (default 864 = 9 days at 15-min intervals).
    """
    import math
    from datetime import UTC, datetime

    interval = 900  # 15-minute intervals
    base_ts = time.time() - n * interval
    history: list[dict] = []
    for i in range(n):
        ts = base_ts + i * interval
        dt = datetime.fromtimestamp(ts, tz=UTC)
        hour = dt.hour + dt.minute / 60.0
        # Sine wave peaking at hour 14 UTC — no drift so train/test match
        gwei = 30.0 + 10.0 * math.sin(2 * math.pi * (hour - 6) / 24)
        history.append({"gwei": round(gwei, 2), "ts": ts})
    return history


# ---------------------------------------------------------------------------
# Feature engineering
# ---------------------------------------------------------------------------
class TestGasFeatures:

    def test_dataclass_fields(self) -> None:
        f = GasFeatures(
            hour_of_day=14,
            day_of_week=2,
            gas_mean_1h=30.0,
            gas_std_1h=2.0,
            gas_mean_4h=28.0,
            gas_mean_24h=25.0,
            gas_trend=0.1,
            block_utilization=0.85,
        )
        assert f.hour_of_day == 14
        assert f.block_utilization == 0.85

    def test_extract_features_empty_history(self) -> None:
        assert extract_features([]) == []

    def test_extract_features_needs_one_hour(self) -> None:
        # Only 30 min of data — no features produced
        history = _make_history(30, interval_seconds=60)
        assert extract_features(history) == []

    def test_extract_features_produces_output(self) -> None:
        # 2 hours of per-minute data → features from t=1h onward
        history = _make_history(120, interval_seconds=60)
        feats = extract_features(history)
        assert len(feats) > 0
        assert all(isinstance(f, GasFeatures) for f in feats)

    def test_extract_features_hour_and_day(self) -> None:
        history = _make_history(120, interval_seconds=60)
        feats = extract_features(history)
        for f in feats:
            assert 0 <= f.hour_of_day <= 23
            assert 0 <= f.day_of_week <= 6

    def test_extract_features_block_utilization_default(self) -> None:
        history = _make_history(120, interval_seconds=60)
        feats = extract_features(history)
        # Default block_utilization is 0.5
        assert all(f.block_utilization == 0.5 for f in feats)

    def test_extract_features_custom_block_utilization(self) -> None:
        history = _make_history(120, interval_seconds=60)
        for entry in history:
            entry["block_utilization"] = 0.9
        feats = extract_features(history)
        assert all(f.block_utilization == 0.9 for f in feats)


class TestFeaturesToArray:

    def test_empty(self) -> None:
        assert features_to_array([]) == []

    def test_correct_dimensions(self) -> None:
        history = _make_history(120, interval_seconds=60)
        feats = extract_features(history)
        arr = features_to_array(feats)
        assert len(arr) == len(feats)
        assert all(len(row) == 8 for row in arr)

    def test_values_are_floats(self) -> None:
        f = GasFeatures(
            hour_of_day=14,
            day_of_week=2,
            gas_mean_1h=30.0,
            gas_std_1h=2.0,
            gas_mean_4h=28.0,
            gas_mean_24h=25.0,
            gas_trend=0.1,
            block_utilization=0.85,
        )
        arr = features_to_array([f])
        assert arr == [[14.0, 2.0, 30.0, 2.0, 28.0, 25.0, 0.1, 0.85]]


# ---------------------------------------------------------------------------
# GasPrediction dataclass
# ---------------------------------------------------------------------------
class TestGasPrediction:

    def test_fields(self) -> None:
        p = GasPrediction(
            predicted_gas=Decimal("30.5"),
            confidence=0.85,
            horizon="1h",
            method="ml",
        )
        assert p.predicted_gas == Decimal("30.5")
        assert p.method == "ml"
        assert p.horizon == "1h"
        assert p.confidence == 0.85


# ---------------------------------------------------------------------------
# GasPredictorConfig
# ---------------------------------------------------------------------------
class TestGasPredictorConfig:

    def test_defaults(self) -> None:
        cfg = GasPredictorConfig()
        assert cfg.min_training_samples == 100
        assert cfg.retrain_interval_hours == 6
        assert cfg.confidence_threshold == 0.6

    def test_custom(self) -> None:
        cfg = GasPredictorConfig(
            min_training_samples=50, retrain_interval_hours=12, confidence_threshold=0.8,
        )
        assert cfg.min_training_samples == 50
        assert cfg.retrain_interval_hours == 12
        assert cfg.confidence_threshold == 0.8


# ---------------------------------------------------------------------------
# Heuristic prediction (always available)
# ---------------------------------------------------------------------------
class TestHeuristicPrediction:

    def test_basic_heuristic(self) -> None:
        predictor = GasPredictor()
        history = _make_history(60, interval_seconds=60, base_gwei=30.0)
        predictor.train(history)  # Not enough for ML, will use heuristic
        # Use very low min_training_samples to trigger ML only when we want
        predictor_heur = GasPredictor(GasPredictorConfig(min_training_samples=999999))
        predictor_heur.train(history)
        pred = predictor_heur.predict("1h")
        assert pred.method == "heuristic"
        assert pred.predicted_gas > 0
        assert pred.horizon == "1h"

    def test_heuristic_confidence_below_one(self) -> None:
        predictor = GasPredictor(GasPredictorConfig(min_training_samples=999999))
        history = _make_history(60, interval_seconds=60)
        predictor.train(history)
        pred = predictor.predict("1h")
        assert 0.0 <= pred.confidence <= 1.0

    def test_heuristic_horizons(self) -> None:
        predictor = GasPredictor(GasPredictorConfig(min_training_samples=999999))
        history = _make_history(200, interval_seconds=60)
        predictor.train(history)
        for h in ("1h", "4h", "24h"):
            pred = predictor.predict(h)
            assert pred.horizon == h
            assert pred.method == "heuristic"

    def test_heuristic_invalid_horizon(self) -> None:
        predictor = GasPredictor()
        predictor.add_observation({"gwei": 30.0, "ts": time.time()})
        with pytest.raises(ValueError, match="Unsupported horizon"):
            predictor.predict("2h")


# ---------------------------------------------------------------------------
# ML training & prediction
# ---------------------------------------------------------------------------
class TestMLTraining:

    def test_train_insufficient_data(self) -> None:
        predictor = GasPredictor(GasPredictorConfig(min_training_samples=100))
        history = _make_history(30, interval_seconds=60)
        result = predictor.train(history)
        assert result is False
        assert not predictor.is_model_trained()

    def test_train_with_enough_data(self) -> None:
        cfg = GasPredictorConfig(min_training_samples=50, confidence_threshold=0.0)
        predictor = GasPredictor(cfg)
        history = _make_predictable_history(500)
        result = predictor.train(history)
        assert result is True
        assert predictor.is_model_trained()

    def test_predict_with_trained_model(self) -> None:
        cfg = GasPredictorConfig(min_training_samples=50, confidence_threshold=0.0)
        predictor = GasPredictor(cfg)
        history = _make_predictable_history(500)
        predictor.train(history)
        pred = predictor.predict("1h")
        assert pred.method == "ml"
        assert pred.predicted_gas > 0

    def test_prediction_fallback_when_untrained(self) -> None:
        predictor = GasPredictor()
        history = _make_history(60, interval_seconds=60)
        predictor.train(history)  # Not enough for ML
        pred = predictor.predict("1h")
        assert pred.method == "heuristic"


# ---------------------------------------------------------------------------
# Model stats
# ---------------------------------------------------------------------------
class TestModelStats:

    def test_stats_untrained(self) -> None:
        predictor = GasPredictor()
        stats = predictor.get_model_stats()
        assert stats["trained"] is False
        assert stats["samples"] == 0
        assert stats["last_trained"] is None
        assert stats["sklearn_available"] is True

    def test_stats_after_training(self) -> None:
        cfg = GasPredictorConfig(min_training_samples=50, confidence_threshold=0.0)
        predictor = GasPredictor(cfg)
        history = _make_predictable_history(500)
        predictor.train(history)
        stats = predictor.get_model_stats()
        assert stats["trained"] is True
        assert stats["samples"] > 0
        assert stats["last_trained"] is not None
        assert isinstance(stats["score"], float)


# ---------------------------------------------------------------------------
# add_observation
# ---------------------------------------------------------------------------
class TestAddObservation:

    def test_adds_to_history(self) -> None:
        predictor = GasPredictor()
        predictor.add_observation({"gwei": 30.0, "ts": time.time()})
        predictor.add_observation({"gwei": 32.0, "ts": time.time()})
        # Internal history should have 2 entries
        assert len(predictor._gas_history) == 2

    def test_missing_keys_raises(self) -> None:
        predictor = GasPredictor()
        with pytest.raises(ValueError, match="gwei"):
            predictor.add_observation({"price": 30.0})

    def test_predict_after_observations(self) -> None:
        predictor = GasPredictor()
        now = time.time()
        for i in range(120):
            predictor.add_observation({"gwei": 30.0 + i * 0.1, "ts": now - (120 - i) * 60})
        pred = predictor.predict("1h")
        assert pred.method == "heuristic"
        assert pred.predicted_gas > 0

    def test_no_history_predict_raises(self) -> None:
        predictor = GasPredictor()
        with pytest.raises(RuntimeError, match="No gas history"):
            predictor.predict("1h")
