"""Tests for gas price monitor — DATA-002."""

from __future__ import annotations

import json
import time
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock

import pytest

from data.gas_monitor import (
    GAS_CACHE_KEY,
    GAS_HISTORY_KEY,
    GAS_HOURLY_KEY_PREFIX,
    GasMonitor,
    GasPrices,
)

# ── Fixtures ──────────────────────────────────────────


def _make_mock_redis() -> MagicMock:
    """Create a mock RedisManager with cache and sorted-set operations."""
    redis = MagicMock()
    redis._cache: dict[str, Any] = {}
    redis._zsets: dict[str, list[tuple[float, str]]] = {}

    def cache_set(key: str, value: Any, ttl: int) -> None:
        redis._cache[key] = value

    def cache_get(key: str) -> Any | None:
        return redis._cache.get(key)

    redis.cache_set = MagicMock(side_effect=cache_set)
    redis.cache_get = MagicMock(side_effect=cache_get)

    def zadd(key: str, mapping: dict[str, float]) -> None:
        if key not in redis._zsets:
            redis._zsets[key] = []
        for member, score in mapping.items():
            redis._zsets[key].append((score, member))

    def zrangebyscore(key: str, min_score: float, max_score: float) -> list[str]:
        if key not in redis._zsets:
            return []
        return [
            member
            for score, member in redis._zsets[key]
            if (str(min_score) == "-inf" or score >= float(min_score))
            and (str(max_score) == "+inf" or score <= float(max_score))
        ]

    def zremrangebyscore(key: str, min_score: str, max_score: float) -> None:
        if key not in redis._zsets:
            return
        redis._zsets[key] = [
            (s, m) for s, m in redis._zsets[key] if not (s <= float(max_score))
        ]

    redis.client = MagicMock()
    redis.client.zadd = MagicMock(side_effect=zadd)
    redis.client.zrangebyscore = MagicMock(side_effect=zrangebyscore)
    redis.client.zremrangebyscore = MagicMock(side_effect=zremrangebyscore)

    return redis


def _mock_etherscan_response(fast: float, standard: float, slow: float) -> dict[str, Any]:
    """Build a mock Etherscan gas oracle response."""
    return {
        "status": "1",
        "result": {
            "FastGasPrice": str(fast),
            "ProposeGasPrice": str(standard),
            "SafeGasPrice": str(slow),
        },
    }


# ── Tests ─────────────────────────────────────────────


class TestGasPrices:
    def test_to_dict(self) -> None:
        gp = GasPrices(50.0, 30.0, 20.0, "2026-01-01T00:00:00+00:00")
        d = gp.to_dict()
        assert d["fast"] == 50.0
        assert d["standard"] == 30.0
        assert d["slow"] == 20.0

    def test_get_tier(self) -> None:
        gp = GasPrices(50.0, 30.0, 20.0, "2026-01-01T00:00:00+00:00")
        assert gp.get_tier("fast") == 50.0
        assert gp.get_tier("standard") == 30.0
        assert gp.get_tier("slow") == 20.0

    def test_invalid_tier(self) -> None:
        gp = GasPrices(50.0, 30.0, 20.0, "2026-01-01T00:00:00+00:00")
        with pytest.raises(ValueError, match="Unknown priority"):
            gp.get_tier("turbo")


class TestUpdate:
    def test_fetches_and_caches(self) -> None:
        redis = _make_mock_redis()

        def mock_fetch(url: str, timeout: int = 10) -> Any:
            return _mock_etherscan_response(50.0, 30.0, 20.0)

        mon = GasMonitor(redis, fetch_fn=mock_fetch)
        prices = mon.update()

        assert prices is not None
        assert prices.fast == 50.0
        assert prices.standard == 30.0
        assert prices.slow == 20.0
        assert redis.cache_set.call_count == 1

    def test_fetch_failure_returns_none(self) -> None:
        redis = _make_mock_redis()

        def mock_fetch(url: str, timeout: int = 10) -> Any:
            raise ConnectionError("Network down")

        mon = GasMonitor(redis, fetch_fn=mock_fetch)
        prices = mon.update()

        assert prices is None

    def test_records_history(self) -> None:
        redis = _make_mock_redis()

        def mock_fetch(url: str, timeout: int = 10) -> Any:
            return _mock_etherscan_response(50.0, 30.0, 20.0)

        mon = GasMonitor(redis, fetch_fn=mock_fetch)
        mon.update()

        # Should have recorded to history sorted set
        assert GAS_HISTORY_KEY in redis._zsets
        assert len(redis._zsets[GAS_HISTORY_KEY]) == 1


class TestRollingAverage:
    def test_single_entry(self) -> None:
        redis = _make_mock_redis()
        mon = GasMonitor(redis)

        now = time.time()
        mon._record_history(30.0, now)

        avg = mon.get_rolling_average(window_hours=24)
        assert avg == Decimal("30")

    def test_multiple_entries(self) -> None:
        redis = _make_mock_redis()
        mon = GasMonitor(redis)

        now = time.time()
        mon._record_history(20.0, now - 3600)
        mon._record_history(30.0, now - 1800)
        mon._record_history(40.0, now)

        avg = mon.get_rolling_average(window_hours=24)
        assert avg == Decimal("30")

    def test_respects_window(self) -> None:
        redis = _make_mock_redis()
        mon = GasMonitor(redis)

        now = time.time()
        # 25h ago — outside 24h window
        mon._record_history(1000.0, now - 90000)
        # Inside window
        mon._record_history(30.0, now - 1800)
        mon._record_history(50.0, now)

        avg = mon.get_rolling_average(window_hours=24)
        assert avg == Decimal("40")

    def test_no_data_returns_none(self) -> None:
        redis = _make_mock_redis()
        mon = GasMonitor(redis)

        avg = mon.get_rolling_average()
        assert avg is None

    def test_shorter_window(self) -> None:
        redis = _make_mock_redis()
        mon = GasMonitor(redis)

        now = time.time()
        # 2h ago — outside 1h window
        mon._record_history(100.0, now - 7200)
        # Inside 1h window
        mon._record_history(30.0, now - 1800)
        mon._record_history(50.0, now)

        avg = mon.get_rolling_average(window_hours=1)
        assert avg == Decimal("40")


class TestSpikeDetection:
    def test_no_spike(self) -> None:
        redis = _make_mock_redis()
        mon = GasMonitor(redis)

        # Set up: avg of 30, current of 30
        now = time.time()
        mon._record_history(30.0, now - 3600)
        mon._record_history(30.0, now)

        # Cache current prices
        redis._cache[GAS_CACHE_KEY] = GasPrices(50.0, 30.0, 20.0, "now").to_dict()

        assert mon.is_spike() is False

    def test_spike_detected(self) -> None:
        redis = _make_mock_redis()
        mon = GasMonitor(redis)

        # Set up: avg of 30, current of 100 (>3x)
        now = time.time()
        mon._record_history(30.0, now - 3600)
        mon._record_history(30.0, now)

        redis._cache[GAS_CACHE_KEY] = GasPrices(150.0, 100.0, 80.0, "now").to_dict()

        assert mon.is_spike() is True

    def test_custom_multiplier(self) -> None:
        redis = _make_mock_redis()
        mon = GasMonitor(redis)

        now = time.time()
        mon._record_history(30.0, now)

        # Current is 2.5x avg — not a spike at 3x, but is at 2x
        redis._cache[GAS_CACHE_KEY] = GasPrices(100.0, 75.0, 50.0, "now").to_dict()

        assert mon.is_spike(multiplier=3.0) is False
        assert mon.is_spike(multiplier=2.0) is True

    def test_no_data_returns_none(self) -> None:
        redis = _make_mock_redis()
        mon = GasMonitor(redis)

        assert mon.is_spike() is None

    def test_no_cached_prices_returns_none(self) -> None:
        redis = _make_mock_redis()
        mon = GasMonitor(redis)

        now = time.time()
        mon._record_history(30.0, now)

        # No cached prices
        assert mon.is_spike() is None


class TestGasCostEstimation:
    def test_standard_estimate(self) -> None:
        redis = _make_mock_redis()
        mon = GasMonitor(redis)

        redis._cache[GAS_CACHE_KEY] = GasPrices(50.0, 30.0, 20.0, "now").to_dict()

        # 21000 gas units (simple transfer) at 30 gwei
        cost = mon.estimate_gas_cost(21000, priority="standard")
        assert cost is not None
        expected = Decimal("30") * 21000 / Decimal("1e9")
        assert cost == expected

    def test_fast_estimate(self) -> None:
        redis = _make_mock_redis()
        mon = GasMonitor(redis)

        redis._cache[GAS_CACHE_KEY] = GasPrices(50.0, 30.0, 20.0, "now").to_dict()

        cost = mon.estimate_gas_cost(21000, priority="fast")
        assert cost is not None
        expected = Decimal("50") * 21000 / Decimal("1e9")
        assert cost == expected

    def test_no_prices_returns_none(self) -> None:
        redis = _make_mock_redis()
        mon = GasMonitor(redis)

        cost = mon.estimate_gas_cost(21000)
        assert cost is None


class TestAlertTriggering:
    def test_alert_on_high_gas(self, capsys: pytest.CaptureFixture[str]) -> None:
        redis = _make_mock_redis()

        def mock_fetch(url: str, timeout: int = 10) -> Any:
            return _mock_etherscan_response(150.0, 120.0, 90.0)

        mon = GasMonitor(redis, alert_threshold_gwei=100.0, fetch_fn=mock_fetch)
        mon.update()

        captured = capsys.readouterr()
        assert "gas_alert" in captured.out
        assert "120" in captured.out

    def test_no_alert_under_threshold(self, capsys: pytest.CaptureFixture[str]) -> None:
        redis = _make_mock_redis()

        def mock_fetch(url: str, timeout: int = 10) -> Any:
            return _mock_etherscan_response(50.0, 30.0, 20.0)

        mon = GasMonitor(redis, alert_threshold_gwei=100.0, fetch_fn=mock_fetch)
        mon.update()

        captured = capsys.readouterr()
        assert "gas_alert" not in captured.out


class TestHourlyPattern:
    def test_records_hourly_data(self) -> None:
        redis = _make_mock_redis()
        mon = GasMonitor(redis)

        now = time.time()
        mon._record_history(30.0, now)

        # Check that hourly bucket was recorded
        from datetime import UTC, datetime

        hour = datetime.fromtimestamp(now, tz=UTC).hour
        hourly_key = f"{GAS_HOURLY_KEY_PREFIX}{hour}"
        assert hourly_key in redis._zsets

    def test_get_hourly_pattern(self) -> None:
        redis = _make_mock_redis()
        mon = GasMonitor(redis)

        # Manually add data to hour 14 bucket
        hourly_key = f"{GAS_HOURLY_KEY_PREFIX}14"
        member1 = json.dumps({"gwei": 30.0, "ts": 1000.0})
        member2 = json.dumps({"gwei": 50.0, "ts": 2000.0})
        redis._zsets[hourly_key] = [(1000.0, member1), (2000.0, member2)]

        avg = mon.get_hourly_pattern(14)
        assert avg == Decimal("40")

    def test_hourly_no_data(self) -> None:
        redis = _make_mock_redis()
        mon = GasMonitor(redis)

        avg = mon.get_hourly_pattern(3)
        assert avg is None

    def test_invalid_hour(self) -> None:
        redis = _make_mock_redis()
        mon = GasMonitor(redis)

        with pytest.raises(ValueError, match="Hour must be 0-23"):
            mon.get_hourly_pattern(25)


class TestTimestampsUTC:
    def test_update_uses_utc(self) -> None:
        redis = _make_mock_redis()

        def mock_fetch(url: str, timeout: int = 10) -> Any:
            return _mock_etherscan_response(50.0, 30.0, 20.0)

        mon = GasMonitor(redis, fetch_fn=mock_fetch)
        prices = mon.update()

        assert prices is not None
        assert "+00:00" in prices.timestamp
