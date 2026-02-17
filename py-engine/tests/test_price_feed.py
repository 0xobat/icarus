"""Tests for real-time price feed — DATA-001."""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import MagicMock

import pytest

from data.price_feed import (
    DEFILLAMA_TOKEN_ADDRESSES,
    PRICE_CACHE_KEY_PREFIX,
    SUPPORTED_TOKENS,
    PriceFeedManager,
    PriceResult,
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

    # Mock sorted set operations on the underlying client
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
            if (min_score == "-inf" or score >= float(min_score))
            and (max_score == "+inf" or score <= float(max_score))
        ]

    def zremrangebyscore(key: str, min_score: str, max_score: float) -> None:
        if key not in redis._zsets:
            return
        redis._zsets[key] = [
            (s, m)
            for s, m in redis._zsets[key]
            if not (s <= float(max_score))
        ]

    redis.client = MagicMock()
    redis.client.zadd = MagicMock(side_effect=zadd)
    redis.client.zrangebyscore = MagicMock(side_effect=zrangebyscore)
    redis.client.zremrangebyscore = MagicMock(side_effect=zremrangebyscore)

    return redis


def _make_coingecko_response(prices: dict[str, float]) -> dict[str, Any]:
    """Build a mock CoinGecko response."""
    token_to_id = SUPPORTED_TOKENS
    result: dict[str, Any] = {}
    for token, price in prices.items():
        cg_id = token_to_id.get(token)
        if cg_id:
            result[cg_id] = {"usd": price}
    return result


def _make_defillama_response(prices: dict[str, float]) -> dict[str, Any]:
    """Build a mock DeFi Llama response."""
    coins: dict[str, Any] = {}
    for token, price in prices.items():
        addr = DEFILLAMA_TOKEN_ADDRESSES.get(token)
        if addr:
            coins[addr] = {"price": price, "symbol": token}
    return {"coins": coins}


# ── Tests ─────────────────────────────────────────────


class TestPriceResult:
    def test_to_dict(self) -> None:
        pr = PriceResult("ETH", 2500.0, "coingecko", "2026-01-01T00:00:00+00:00")
        d = pr.to_dict()
        assert d["token"] == "ETH"
        assert d["price_usd"] == 2500.0
        assert d["source"] == "coingecko"


class TestOracleManipulationGuard:
    def test_accepts_matching_prices(self) -> None:
        redis = _make_mock_redis()
        mgr = PriceFeedManager(redis)
        ok, dev = mgr._check_deviation("ETH", 2500.0, 2510.0)
        assert ok
        assert dev < 0.02

    def test_rejects_deviated_prices(self) -> None:
        redis = _make_mock_redis()
        mgr = PriceFeedManager(redis)
        # 5% deviation
        ok, dev = mgr._check_deviation("ETH", 2500.0, 2625.0)
        assert not ok
        assert dev > 0.02

    def test_exactly_at_threshold(self) -> None:
        redis = _make_mock_redis()
        mgr = PriceFeedManager(redis)
        # 2% deviation: midpoint = 2525, diff = 50, 50/2525 ≈ 0.0198 — just under
        ok, _dev = mgr._check_deviation("ETH", 2500.0, 2550.0)
        assert ok

    def test_zero_prices(self) -> None:
        redis = _make_mock_redis()
        mgr = PriceFeedManager(redis)
        ok, dev = mgr._check_deviation("ETH", 0.0, 0.0)
        assert ok
        assert dev == 0.0


class TestMultiSourceAggregation:
    def test_both_sources_agree(self) -> None:
        """When both sources agree within threshold, average is cached."""
        redis = _make_mock_redis()
        call_count = 0

        def mock_fetch(url: str, timeout: int = 10) -> Any:
            nonlocal call_count
            call_count += 1
            if "api.coingecko.com" in url:
                return _make_coingecko_response({"ETH": 2500.0, "WBTC": 42000.0})
            return _make_defillama_response({"ETH": 2505.0, "WBTC": 42050.0})

        mgr = PriceFeedManager(redis, fetch_fn=mock_fetch)
        result = mgr.fetch_prices()

        assert "ETH" in result
        assert result["ETH"]["price_usd"] == pytest.approx(2502.5)
        assert len(result["ETH"]["sources"]) == 2
        assert "WBTC" in result
        assert redis.cache_set.call_count >= 2

    def test_deviation_rejects_token(self) -> None:
        """Tokens with >2% deviation are rejected entirely."""
        redis = _make_mock_redis()

        def mock_fetch(url: str, timeout: int = 10) -> Any:
            if "api.coingecko.com" in url:
                return _make_coingecko_response({"ETH": 2500.0})
            return _make_defillama_response({"ETH": 3000.0})  # 18% deviation

        mgr = PriceFeedManager(redis, fetch_fn=mock_fetch)
        result = mgr.fetch_prices()

        assert "ETH" not in result

    def test_single_source_fallback(self) -> None:
        """When one source fails, use the remaining source."""
        redis = _make_mock_redis()

        def mock_fetch(url: str, timeout: int = 10) -> Any:
            if "api.coingecko.com" in url:
                return _make_coingecko_response({"ETH": 2500.0})
            raise ConnectionError("DeFi Llama down")

        mgr = PriceFeedManager(redis, fetch_fn=mock_fetch)
        result = mgr.fetch_prices()

        assert "ETH" in result
        assert result["ETH"]["price_usd"] == 2500.0
        assert result["ETH"]["sources"] == ["coingecko"]

    def test_both_sources_fail(self) -> None:
        """When both sources fail, return empty results."""
        redis = _make_mock_redis()

        def mock_fetch(url: str, timeout: int = 10) -> Any:
            raise ConnectionError("Network down")

        mgr = PriceFeedManager(redis, fetch_fn=mock_fetch)
        result = mgr.fetch_prices()

        assert result == {}


class TestStalePriceDetection:
    def test_fresh_price_not_stale(self) -> None:
        redis = _make_mock_redis()
        mgr = PriceFeedManager(redis, ttl_seconds=30)

        # Manually cache a fresh price
        redis._cache[f"{PRICE_CACHE_KEY_PREFIX}ETH"] = {
            "price_usd": 2500.0,
            "timestamp": "2026-01-01T00:00:00+00:00",
            "cached_at": time.time(),
        }

        result = mgr.get_cached_price("ETH")
        assert result is not None
        assert not result["stale"]

    def test_old_price_is_stale(self) -> None:
        redis = _make_mock_redis()
        mgr = PriceFeedManager(redis, ttl_seconds=30)

        # Cache a price from 60 seconds ago
        redis._cache[f"{PRICE_CACHE_KEY_PREFIX}ETH"] = {
            "price_usd": 2500.0,
            "timestamp": "2026-01-01T00:00:00+00:00",
            "cached_at": time.time() - 60,
        }

        result = mgr.get_cached_price("ETH")
        assert result is not None
        assert result["stale"]

    def test_missing_price_returns_none(self) -> None:
        redis = _make_mock_redis()
        mgr = PriceFeedManager(redis)

        result = mgr.get_cached_price("NONEXISTENT")
        assert result is None


class TestTWAP:
    def test_twap_single_point(self) -> None:
        redis = _make_mock_redis()
        mgr = PriceFeedManager(redis)

        now = time.time()
        mgr._record_price_history("ETH", 2500.0, now)

        twap = mgr.get_twap("ETH", "5m")
        assert twap == 2500.0

    def test_twap_multiple_points(self) -> None:
        redis = _make_mock_redis()
        mgr = PriceFeedManager(redis)

        now = time.time()
        mgr._record_price_history("ETH", 2400.0, now - 120)
        mgr._record_price_history("ETH", 2500.0, now - 60)
        mgr._record_price_history("ETH", 2600.0, now)

        twap = mgr.get_twap("ETH", "5m")
        assert twap == pytest.approx(2500.0)

    def test_twap_respects_window(self) -> None:
        redis = _make_mock_redis()
        mgr = PriceFeedManager(redis)

        now = time.time()
        # Point outside 5m window
        mgr._record_price_history("ETH", 1000.0, now - 400)
        # Points inside 5m window
        mgr._record_price_history("ETH", 2500.0, now - 60)
        mgr._record_price_history("ETH", 2600.0, now)

        twap = mgr.get_twap("ETH", "5m")
        assert twap == pytest.approx(2550.0)

    def test_twap_no_data(self) -> None:
        redis = _make_mock_redis()
        mgr = PriceFeedManager(redis)

        twap = mgr.get_twap("ETH", "5m")
        assert twap is None

    def test_twap_invalid_window(self) -> None:
        redis = _make_mock_redis()
        mgr = PriceFeedManager(redis)

        with pytest.raises(ValueError, match="Unknown TWAP window"):
            mgr.get_twap("ETH", "10m")

    def test_twap_1h_window(self) -> None:
        redis = _make_mock_redis()
        mgr = PriceFeedManager(redis)

        now = time.time()
        mgr._record_price_history("ETH", 2400.0, now - 1800)
        mgr._record_price_history("ETH", 2600.0, now)

        twap = mgr.get_twap("ETH", "1h")
        assert twap == pytest.approx(2500.0)

    def test_twap_24h_window(self) -> None:
        redis = _make_mock_redis()
        mgr = PriceFeedManager(redis)

        now = time.time()
        mgr._record_price_history("ETH", 2300.0, now - 43200)
        mgr._record_price_history("ETH", 2700.0, now)

        twap = mgr.get_twap("ETH", "24h")
        assert twap == pytest.approx(2500.0)


class TestTimestampsUTC:
    def test_prices_have_utc_timestamps(self) -> None:
        redis = _make_mock_redis()

        def mock_fetch(url: str, timeout: int = 10) -> Any:
            if "api.coingecko.com" in url:
                return _make_coingecko_response({"ETH": 2500.0})
            return _make_defillama_response({"ETH": 2505.0})

        mgr = PriceFeedManager(redis, fetch_fn=mock_fetch)
        result = mgr.fetch_prices()

        assert "ETH" in result
        ts = result["ETH"]["timestamp"]
        # UTC timestamps from datetime.now(UTC) end with +00:00
        assert "+00:00" in ts


class TestCustomTokenList:
    def test_accepts_custom_tokens(self) -> None:
        redis = _make_mock_redis()
        custom_tokens = {"ETH": "ethereum"}

        def mock_fetch(url: str, timeout: int = 10) -> Any:
            if "api.coingecko.com" in url:
                return {"ethereum": {"usd": 2500.0}}
            return {"coins": {}}

        mgr = PriceFeedManager(redis, tokens=custom_tokens, fetch_fn=mock_fetch)
        result = mgr.fetch_prices()

        assert "ETH" in result
