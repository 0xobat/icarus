"""Tests for the dashboard state publisher (CONN-002)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock

from monitoring.state_publisher import publish_dashboard_state

# ---------------------------------------------------------------------------
# Mock Redis client
# ---------------------------------------------------------------------------

class MockRedisClient:
    """In-memory Redis mock that tracks SET calls with TTL."""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}
        self._ttls: dict[str, int] = {}

    def set(self, key: str, value: str, *, ex: int | None = None) -> None:
        self._store[key] = value
        if ex is not None:
            self._ttls[key] = ex

    def ping(self) -> bool:
        return True

    def get(self, key: str) -> str | None:
        return self._store.get(key)

    def get_json(self, key: str) -> dict | list | None:
        raw = self._store.get(key)
        if raw is None:
            return None
        return json.loads(raw)


class MockRedisManager:
    """Mimics RedisManager with a .client property."""

    def __init__(self) -> None:
        self._client = MockRedisClient()

    @property
    def client(self) -> MockRedisClient:
        return self._client


# ---------------------------------------------------------------------------
# Mock snapshot for db_repo
# ---------------------------------------------------------------------------

class MockSnapshot:
    def __init__(self, total_value_usd: Decimal, timestamp: datetime) -> None:
        self.total_value_usd = total_value_usd
        self.timestamp = timestamp
        self.drawdown_from_peak = Decimal("0.05")
        self.peak_value_usd = total_value_usd * Decimal("1.05")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_tracker() -> MagicMock:
    tracker = MagicMock()
    tracker.get_summary.return_value = {
        "open_count": 2,
        "closed_count": 1,
        "total_value": "10000.50",
        "total_unrealized_pnl": "500.25",
        "total_realized_pnl": "200.00",
    }
    return tracker


def _make_drawdown_breaker() -> MagicMock:
    breaker = MagicMock()
    breaker.drawdown_pct = Decimal("0.042")
    breaker._critical_threshold = Decimal("0.20")

    state = MagicMock()
    state.drawdown_pct = Decimal("0.042")
    state.peak_value = Decimal("10500")
    state.current_value = Decimal("10059")
    state.level = "normal"
    state.triggered_at = None
    breaker.get_state.return_value = state
    return breaker


def _make_circuit_breakers() -> dict[str, MagicMock]:
    gas_spike = MagicMock()
    gs_state = MagicMock()
    gs_state.is_active = False
    gs_state.current_gas = Decimal("30")
    gs_state.threshold = Decimal("90")
    gs_state.activated_at = None
    gas_spike.get_state.return_value = gs_state

    tx_failures = MagicMock()
    tx_state = MagicMock()
    tx_state.is_paused = False
    tx_state.failures_in_window = 1
    tx_state.threshold = 3
    tx_state.last_failure = None
    tx_failures.get_state.return_value = tx_state

    position_loss = MagicMock()
    tvl_monitor = MagicMock()

    hold_mode = MagicMock()
    hold_mode.is_active.return_value = False
    hold_mode.reason = None
    hold_mode.entry_time = None

    return {
        "gas_spike": gas_spike,
        "tx_failures": tx_failures,
        "position_loss": position_loss,
        "tvl_monitor": tvl_monitor,
        "hold_mode": hold_mode,
    }


def _make_exposure_limiter() -> MagicMock:
    limiter = MagicMock()
    exposure = MagicMock()
    exposure.total_capital = "10000"
    exposure.total_deployed = "7000"
    exposure.by_protocol = {"aave_v3": "5000", "aerodrome": "2000"}
    exposure.by_asset = {"USDC": "5000", "AERO": "2000"}
    exposure.stablecoin_reserve_pct = "0.30"
    exposure.protocol_pcts = {"aave_v3": "0.50", "aerodrome": "0.20"}
    exposure.asset_pcts = {"USDC": "0.50", "AERO": "0.20"}
    limiter.get_exposure.return_value = exposure

    config = MagicMock()
    config.max_protocol_pct = Decimal("0.40")
    config.max_asset_pct = Decimal("0.60")
    config.min_stablecoin_pct = Decimal("0.15")
    limiter.config = config

    return limiter


def _make_strategy_manager() -> MagicMock:
    manager = MagicMock()
    manager._state.get_strategy_statuses.return_value = {
        "LEND-001": "active",
        "LP-001": "active",
    }
    return manager


def _make_db_repo(with_snapshots: bool = True) -> MagicMock:
    repo = MagicMock()
    if with_snapshots:
        now = datetime.now(UTC)
        snapshots = [
            MockSnapshot(Decimal("10000"), now),
            MockSnapshot(Decimal("9800"), now - timedelta(hours=12)),
            MockSnapshot(Decimal("9500"), now - timedelta(hours=24)),
        ]
        repo.get_snapshots.return_value = snapshots
    else:
        repo.get_snapshots.return_value = []

    repo.get_alerts.return_value = []
    repo.get_latest_snapshot.return_value = None
    return repo


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestPublishDashboardState:
    """Core tests for publish_dashboard_state."""

    def _publish(self, **overrides) -> MockRedisManager:
        redis = MockRedisManager()
        tracker = overrides.get("tracker", _make_tracker())
        drawdown = overrides.get("drawdown", _make_drawdown_breaker())
        breakers = overrides.get("breakers", _make_circuit_breakers())
        exposure = overrides.get("exposure", _make_exposure_limiter())
        strategy = overrides.get("strategy", _make_strategy_manager())
        position = overrides.get("position", tracker)
        db_repo = overrides.get("db_repo", _make_db_repo())

        publish_dashboard_state(
            redis_client=redis,
            tracker=tracker,
            drawdown_breaker=drawdown,
            circuit_breakers=breakers,
            exposure_limiter=exposure,
            strategy_manager=strategy,
            position_tracker=position,
            db_repo=db_repo,
        )
        return redis

    def test_all_keys_written(self) -> None:
        """All 8 dashboard keys are written to Redis."""
        redis = self._publish()
        expected_keys = [
            "dashboard:metrics",
            "dashboard:strategies",
            "dashboard:breakers",
            "dashboard:drawdown",
            "dashboard:exposure",
            "dashboard:reserve",
            "dashboard:hold_mode",
            "dashboard:health",
        ]
        for key in expected_keys:
            assert redis.client.get(key) is not None, f"Missing key: {key}"

    def test_ttl_set_to_120(self) -> None:
        """All keys should have TTL of 120 seconds."""
        redis = self._publish()
        for key, ttl in redis.client._ttls.items():
            assert ttl == 120, f"Key {key} has TTL {ttl}, expected 120"

    def test_metrics_shape(self) -> None:
        """dashboard:metrics has the full MetricsData shape."""
        redis = self._publish()
        metrics = redis.client.get_json("dashboard:metrics")
        assert isinstance(metrics, dict)
        required_fields = [
            "portfolio_value",
            "portfolio_change_24h_pct",
            "portfolio_change_24h_abs",
            "portfolio_sparkline",
            "drawdown_current",
            "drawdown_limit",
            "pnl_today",
            "pnl_today_pct",
            "pnl_sparkline",
            "tx_success_rate",
            "tx_success_count",
            "tx_total_count",
        ]
        for field in required_fields:
            assert field in metrics, f"Missing field: {field}"

    def test_metrics_values(self) -> None:
        """Metrics contain expected portfolio value."""
        redis = self._publish()
        metrics = redis.client.get_json("dashboard:metrics")
        assert metrics["portfolio_value"] == 10000.50

    def test_strategies_shape(self) -> None:
        """dashboard:strategies has strategies, reserve, and total_value."""
        redis = self._publish()
        data = redis.client.get_json("dashboard:strategies")
        assert "strategies" in data
        assert "reserve" in data
        assert "total_value" in data
        assert isinstance(data["strategies"], list)

    def test_breakers_shape(self) -> None:
        """dashboard:breakers is an array of breaker objects."""
        redis = self._publish()
        data = redis.client.get_json("dashboard:breakers")
        assert isinstance(data, list)
        assert len(data) >= 1
        breaker = data[0]
        for field in ("name", "current", "limit", "unit", "status", "last_triggered"):
            assert field in breaker, f"Missing field: {field}"

    def test_drawdown_shape(self) -> None:
        """dashboard:drawdown has expected fields."""
        redis = self._publish()
        data = redis.client.get_json("dashboard:drawdown")
        for field in ("current_pct", "peak_value", "current_value", "level", "limit"):
            assert field in data, f"Missing field: {field}"

    def test_exposure_shape(self) -> None:
        """dashboard:exposure is an array with scope, name, etc."""
        redis = self._publish()
        data = redis.client.get_json("dashboard:exposure")
        assert isinstance(data, list)
        if data:
            entry = data[0]
            fields = ("scope", "name", "current_allocation", "current_pct", "limit_pct", "headroom")
            for field in fields:
                assert field in entry, f"Missing field: {field}"

    def test_reserve_shape(self) -> None:
        """dashboard:reserve has liquid_reserve, min_reserve_requirement, reserve_pct."""
        redis = self._publish()
        data = redis.client.get_json("dashboard:reserve")
        for field in ("liquid_reserve", "min_reserve_requirement", "reserve_pct"):
            assert field in data, f"Missing field: {field}"

    def test_hold_mode_inactive(self) -> None:
        """dashboard:hold_mode is inactive by default."""
        redis = self._publish()
        data = redis.client.get_json("dashboard:hold_mode")
        assert data["active"] is False
        assert data["reason"] == ""
        assert data["since"] == ""

    def test_hold_mode_active(self) -> None:
        """dashboard:hold_mode reflects active hold mode."""
        breakers = _make_circuit_breakers()
        breakers["hold_mode"].is_active.return_value = True
        breakers["hold_mode"].reason = "Claude API unavailable"
        breakers["hold_mode"].entry_time = "2026-03-11T09:42:00Z"

        redis = self._publish(breakers=breakers)
        data = redis.client.get_json("dashboard:hold_mode")
        assert data["active"] is True
        assert data["reason"] == "Claude API unavailable"
        assert data["since"] == "2026-03-11T09:42:00Z"

    def test_health_shape(self) -> None:
        """dashboard:health is an array of service health objects."""
        redis = self._publish()
        data = redis.client.get_json("dashboard:health")
        assert isinstance(data, list)
        assert len(data) >= 1
        svc = data[0]
        for field in ("name", "status", "latency_ms", "last_heartbeat", "error_count_24h"):
            assert field in svc, f"Missing field: {field}"


class TestDecimalConversion:
    """Verify Decimal→float conversion in JSON output."""

    def test_decimal_values_serialized_as_float(self) -> None:
        """Decimal values should appear as floats in JSON, not strings."""
        redis = MockRedisManager()
        tracker = _make_tracker()
        drawdown = _make_drawdown_breaker()
        breakers = _make_circuit_breakers()
        exposure = _make_exposure_limiter()
        strategy = _make_strategy_manager()
        db_repo = _make_db_repo()

        publish_dashboard_state(
            redis_client=redis,
            tracker=tracker,
            drawdown_breaker=drawdown,
            circuit_breakers=breakers,
            exposure_limiter=exposure,
            strategy_manager=strategy,
            position_tracker=tracker,
            db_repo=db_repo,
        )

        # Check drawdown — drawdown_pct is Decimal in the breaker
        dd = redis.client.get_json("dashboard:drawdown")
        assert isinstance(dd["current_pct"], float)
        assert isinstance(dd["peak_value"], float)


class TestEmptyDbRepo:
    """Graceful handling when db_repo returns empty results."""

    def test_empty_snapshots(self) -> None:
        """Publish succeeds when no snapshots exist."""
        redis = MockRedisManager()
        tracker = _make_tracker()
        drawdown = _make_drawdown_breaker()
        breakers = _make_circuit_breakers()
        exposure = _make_exposure_limiter()
        strategy = _make_strategy_manager()
        db_repo = _make_db_repo(with_snapshots=False)

        publish_dashboard_state(
            redis_client=redis,
            tracker=tracker,
            drawdown_breaker=drawdown,
            circuit_breakers=breakers,
            exposure_limiter=exposure,
            strategy_manager=strategy,
            position_tracker=tracker,
            db_repo=db_repo,
        )

        metrics = redis.client.get_json("dashboard:metrics")
        assert metrics is not None
        assert metrics["portfolio_change_24h_pct"] == 0.0
        assert metrics["portfolio_sparkline"] == []

    def test_db_repo_exception(self) -> None:
        """Publish does not crash when db_repo raises exceptions."""
        redis = MockRedisManager()
        tracker = _make_tracker()
        drawdown = _make_drawdown_breaker()
        breakers = _make_circuit_breakers()
        exposure = _make_exposure_limiter()
        strategy = _make_strategy_manager()
        db_repo = MagicMock()
        db_repo.get_snapshots.side_effect = RuntimeError("DB down")
        db_repo.get_alerts.side_effect = RuntimeError("DB down")
        db_repo.get_latest_snapshot.side_effect = RuntimeError("DB down")

        # Should not raise
        publish_dashboard_state(
            redis_client=redis,
            tracker=tracker,
            drawdown_breaker=drawdown,
            circuit_breakers=breakers,
            exposure_limiter=exposure,
            strategy_manager=strategy,
            position_tracker=tracker,
            db_repo=db_repo,
        )

        # Some keys should still be written (the ones that don't depend on db)
        breakers_data = redis.client.get_json("dashboard:breakers")
        assert breakers_data is not None


class TestSparklineDerivation:
    """Test portfolio_sparkline and pnl_sparkline derivation."""

    def test_sparkline_from_snapshots(self) -> None:
        """portfolio_sparkline contains chronological values from snapshots."""
        redis = MockRedisManager()
        tracker = _make_tracker()
        drawdown = _make_drawdown_breaker()
        breakers = _make_circuit_breakers()
        exposure = _make_exposure_limiter()
        strategy = _make_strategy_manager()
        db_repo = _make_db_repo(with_snapshots=True)

        publish_dashboard_state(
            redis_client=redis,
            tracker=tracker,
            drawdown_breaker=drawdown,
            circuit_breakers=breakers,
            exposure_limiter=exposure,
            strategy_manager=strategy,
            position_tracker=tracker,
            db_repo=db_repo,
        )

        metrics = redis.client.get_json("dashboard:metrics")
        # Snapshots are desc-ordered: 10000, 9800, 9500
        # Reversed chronological: 9500, 9800, 10000
        assert metrics["portfolio_sparkline"] == [9500.0, 9800.0, 10000.0]

    def test_pnl_sparkline_deltas(self) -> None:
        """pnl_sparkline contains sequential deltas."""
        redis = MockRedisManager()
        tracker = _make_tracker()
        drawdown = _make_drawdown_breaker()
        breakers = _make_circuit_breakers()
        exposure = _make_exposure_limiter()
        strategy = _make_strategy_manager()
        db_repo = _make_db_repo(with_snapshots=True)

        publish_dashboard_state(
            redis_client=redis,
            tracker=tracker,
            drawdown_breaker=drawdown,
            circuit_breakers=breakers,
            exposure_limiter=exposure,
            strategy_manager=strategy,
            position_tracker=tracker,
            db_repo=db_repo,
        )

        metrics = redis.client.get_json("dashboard:metrics")
        # Chronological: 9500, 9800, 10000 → deltas: 300, 200
        assert metrics["pnl_sparkline"] == [300.0, 200.0]

    def test_24h_change_calculation(self) -> None:
        """portfolio_change_24h_pct/abs derived from oldest snapshot."""
        redis = MockRedisManager()
        tracker = _make_tracker()
        # tracker total_value is 10000.50
        drawdown = _make_drawdown_breaker()
        breakers = _make_circuit_breakers()
        exposure = _make_exposure_limiter()
        strategy = _make_strategy_manager()
        db_repo = _make_db_repo(with_snapshots=True)

        publish_dashboard_state(
            redis_client=redis,
            tracker=tracker,
            drawdown_breaker=drawdown,
            circuit_breakers=breakers,
            exposure_limiter=exposure,
            strategy_manager=strategy,
            position_tracker=tracker,
            db_repo=db_repo,
        )

        metrics = redis.client.get_json("dashboard:metrics")
        # Oldest snapshot value is 9500, current portfolio value is 10000.50
        expected_abs = 10000.50 - 9500.0
        expected_pct = (expected_abs / 9500.0) * 100
        assert abs(metrics["portfolio_change_24h_abs"] - expected_abs) < 0.01
        assert abs(metrics["portfolio_change_24h_pct"] - expected_pct) < 0.01


class TestErrorResilience:
    """Verify the function never raises exceptions."""

    def test_redis_client_error(self) -> None:
        """Graceful handling when Redis client raises."""
        redis = MagicMock()
        redis.client.set.side_effect = RuntimeError("Connection refused")

        # Should not raise
        publish_dashboard_state(
            redis_client=redis,
            tracker=_make_tracker(),
            drawdown_breaker=_make_drawdown_breaker(),
            circuit_breakers=_make_circuit_breakers(),
            exposure_limiter=_make_exposure_limiter(),
            strategy_manager=_make_strategy_manager(),
            position_tracker=_make_tracker(),
            db_repo=_make_db_repo(),
        )
