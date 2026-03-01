"""ML gas prediction using GradientBoosting with heuristic fallback."""

from __future__ import annotations

import statistics
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from ml.feature_engineering import extract_features, features_to_array
from monitoring.logger import get_logger

logger = get_logger("gas_predictor", enable_file=False)

# Conditional sklearn import — fall back to heuristics when unavailable.
try:
    from sklearn.ensemble import GradientBoostingRegressor
    from sklearn.metrics import r2_score

    _HAS_SKLEARN = True
except ImportError:  # pragma: no cover
    _HAS_SKLEARN = False


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
@dataclass
class GasPrediction:
    """Result of a gas-price prediction.

    Attributes:
        predicted_gas: Predicted gas price in gwei.
        confidence: Model confidence in the prediction (0.0-1.0).
        horizon: Prediction horizon (``"1h"``, ``"4h"``, or ``"24h"``).
        method: Method used (``"ml"`` or ``"heuristic"``).
    """

    predicted_gas: Decimal
    confidence: float
    horizon: str
    method: str


@dataclass
class GasPredictorConfig:
    """Configuration for the gas predictor.

    Attributes:
        min_training_samples: Minimum feature rows required before ML training.
        retrain_interval_hours: Hours between automatic retraining.
        confidence_threshold: Minimum cross-val R^2 to accept the ML model.
    """

    min_training_samples: int = 100
    retrain_interval_hours: int = 6
    confidence_threshold: float = 0.6


# ---------------------------------------------------------------------------
# Horizon helpers
# ---------------------------------------------------------------------------
_HORIZON_SECONDS: dict[str, int] = {
    "1h": 3600,
    "4h": 4 * 3600,
    "24h": 24 * 3600,
}

_VALID_HORIZONS = frozenset(_HORIZON_SECONDS.keys())


def _validate_horizon(horizon: str) -> None:
    """Raise ValueError for unsupported horizons."""
    if horizon not in _VALID_HORIZONS:
        raise ValueError(f"Unsupported horizon {horizon!r}. Valid: {sorted(_VALID_HORIZONS)}")


# ---------------------------------------------------------------------------
# GasPredictor
# ---------------------------------------------------------------------------
class GasPredictor:
    """Predict future gas prices using GradientBoosting or rolling-average heuristics.

    When scikit-learn is available and enough training data has been collected
    the predictor trains a ``GradientBoostingRegressor``.  Otherwise (or when
    the model performs poorly) it falls back to simple rolling-average
    heuristics.
    """

    def __init__(self, config: GasPredictorConfig | None = None) -> None:
        self._config = config or GasPredictorConfig()
        self._gas_history: list[dict] = []
        self._model: Any = None
        self._last_trained: float | None = None
        self._training_samples: int = 0
        self._model_score: float = 0.0

    # -- public API --------------------------------------------------------

    def train(self, gas_history: list[dict]) -> bool:
        """Train or retrain the ML model on *gas_history*.

        Args:
            gas_history: Raw gas snapshots (each with ``"gwei"`` and ``"ts"``).

        Returns:
            ``True`` if an ML model was successfully trained, ``False`` if
            training was skipped or scikit-learn is unavailable.
        """
        self._gas_history = list(gas_history)

        if not _HAS_SKLEARN:
            logger.info("scikit-learn unavailable — using heuristic fallback")
            return False

        feats = extract_features(gas_history)
        if len(feats) < self._config.min_training_samples:
            logger.info(
                "Insufficient training samples (%d < %d)",
                len(feats),
                self._config.min_training_samples,
            )
            return False

        x = features_to_array(feats)
        y = [float(f.gas_mean_1h) for f in feats]

        # Time-series-aware holdout: train on first 80%, evaluate on last 20%
        split = int(len(x) * 0.8)
        x_train, x_test = x[:split], x[split:]
        y_train, y_test = y[:split], y[split:]

        model = GradientBoostingRegressor(
            n_estimators=100,
            max_depth=4,
            learning_rate=0.1,
            random_state=42,
        )

        model.fit(x_train, y_train)
        y_pred = model.predict(x_test)
        avg_score = float(r2_score(y_test, y_pred))

        if avg_score < self._config.confidence_threshold:
            threshold = self._config.confidence_threshold
            logger.info("Model score %.3f below threshold %.3f", avg_score, threshold)
            self._model = None
            self._model_score = avg_score
            return False

        # Refit on the full dataset now that quality is confirmed
        model.fit(x, y)
        self._model = model
        self._last_trained = time.time()
        self._training_samples = len(feats)
        self._model_score = avg_score

        logger.info(
            "Model trained — samples=%d score=%.3f",
            self._training_samples,
            self._model_score,
        )
        return True

    def predict(self, horizon: str = "1h") -> GasPrediction:
        """Predict the gas price for the given *horizon*.

        Falls back to heuristics when the ML model is unavailable or
        untrained.

        Args:
            horizon: One of ``"1h"``, ``"4h"``, or ``"24h"``.

        Returns:
            A ``GasPrediction`` with the predicted gas price.

        Raises:
            ValueError: If *horizon* is not a recognised value.
            RuntimeError: If there is no gas history at all.
        """
        _validate_horizon(horizon)

        if not self._gas_history:
            raise RuntimeError("No gas history available — call train() or add_observation() first")

        if self._model is not None and _HAS_SKLEARN:
            return self._ml_predict(horizon)

        return self._heuristic_predict(self._gas_history, horizon)

    def is_model_trained(self) -> bool:
        """Return whether an ML model is currently available."""
        return self._model is not None

    def get_model_stats(self) -> dict:
        """Return diagnostic statistics about the current model state.

        Returns:
            Dict with keys ``trained``, ``samples``, ``last_trained``,
            ``score``, and ``sklearn_available``.
        """
        return {
            "trained": self.is_model_trained(),
            "samples": self._training_samples,
            "last_trained": (
                datetime.fromtimestamp(self._last_trained, tz=UTC).isoformat()
                if self._last_trained
                else None
            ),
            "score": self._model_score,
            "sklearn_available": _HAS_SKLEARN,
        }

    def add_observation(self, gas_snapshot: dict) -> None:
        """Append a single observation to the internal gas history.

        Args:
            gas_snapshot: Dict with at least ``"gwei"`` and ``"ts"`` keys.

        Raises:
            ValueError: If required keys are missing.
        """
        if "gwei" not in gas_snapshot or "ts" not in gas_snapshot:
            raise ValueError("gas_snapshot must contain 'gwei' and 'ts' keys")
        self._gas_history.append(gas_snapshot)

    # -- internal ----------------------------------------------------------

    def _ml_predict(self, horizon: str) -> GasPrediction:
        """Produce a prediction using the trained ML model."""
        feats = extract_features(self._gas_history)
        if not feats:
            return self._heuristic_predict(self._gas_history, horizon)

        last_feat = feats[-1]
        row = features_to_array([last_feat])
        predicted = float(self._model.predict(row)[0])

        # Scale the raw 1h prediction proportionally for longer horizons
        horizon_secs = _HORIZON_SECONDS[horizon]
        scale = horizon_secs / 3600.0
        # Blend towards the long-term mean for longer horizons
        long_mean = last_feat.gas_mean_24h
        if scale > 1.0:
            blended = predicted * (1.0 / scale) + long_mean * (1.0 - 1.0 / scale)
        else:
            blended = predicted

        confidence = min(max(self._model_score, 0.0), 1.0)

        return GasPrediction(
            predicted_gas=Decimal(str(round(blended, 4))),
            confidence=confidence,
            horizon=horizon,
            method="ml",
        )

    def _heuristic_predict(self, gas_history: list[dict], horizon: str) -> GasPrediction:
        """Produce a prediction using rolling-average heuristics.

        Args:
            gas_history: Raw gas snapshots.
            horizon: Prediction horizon.

        Returns:
            A ``GasPrediction`` with method ``"heuristic"``.
        """
        _validate_horizon(horizon)

        if not gas_history:
            raise RuntimeError("No gas history available for heuristic prediction")

        sorted_hist = sorted(gas_history, key=lambda e: float(e["ts"]))
        latest_ts = float(sorted_hist[-1]["ts"])
        horizon_secs = _HORIZON_SECONDS[horizon]

        # Gather values in the window matching the requested horizon
        window_vals = [
            float(e["gwei"])
            for e in sorted_hist
            if latest_ts - float(e["ts"]) <= horizon_secs
        ]

        if not window_vals:
            window_vals = [float(sorted_hist[-1]["gwei"])]

        mean_val = statistics.mean(window_vals)

        # Confidence decreases with fewer data points and longer horizons
        data_factor = min(len(window_vals) / 50.0, 1.0)
        horizon_penalty = {"1h": 1.0, "4h": 0.85, "24h": 0.7}[horizon]
        confidence = round(data_factor * horizon_penalty * 0.5, 4)

        return GasPrediction(
            predicted_gas=Decimal(str(round(mean_val, 4))),
            confidence=confidence,
            horizon=horizon,
            method="heuristic",
        )
