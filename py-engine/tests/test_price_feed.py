"""Tests for real-time price feed — DATA-001."""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import MagicMock

import pytest

from data.price_feed import (
    ALCHEMY_SYMBOLS,
    DEFILLAMA_TOKEN_ADDRESSES,
    PRICE_CACHE_KEY_PREFIX,
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


def _make_defillama_response(prices: dict[str, float]) -> dict[str, Any]:
    """Build a mock DeFi Llama response."""
    coins: dict[str, Any] = {}
    for token, price in prices.items():
        addr = DEFILLAMA_TOKEN_ADDRESSES.get(token)
        if addr:
            coins[addr] = {"price": price, "symbol": token}
    return {"coins": coins}


def _make_alchemy_response(prices: dict[str, float]) -> dict[str, Any]:
    """Build a mock Alchemy Token Prices API response."""
    return {
        "data": [
            {
                "symbol": symbol,
                "prices": [{"currency": "usd", "value": str(price), "lastUpdatedAt": "2026-01-01T00:00:00Z"}],
            }
            for symbol, price in prices.items()
        ]
    }


# ── Tests ─────────────────────────────────────────────


class TestPriceResult:
    def test_to_dict(self) -> None:
        pr = PriceResult("USDC", 1.0, "alchemy", "2026-01-01T00:00:00+00:00")
        d = pr.to_dict()
        assert d["token"] == "USDC"
        assert d["price_usd"] == 1.0
        assert d["source"] == "alchemy"


class TestAlchemyFetch:
    def test_fetches_all_tokens(self) -> None:
        redis = _make_mock_redis()

        def mock_fetch(url: str, timeout: int = 10) -> Any:
            assert "api.g.alchemy.com/prices" in url
            return _make_alchemy_response({"USDC": 1.0001, "USDT": 1.0, "DAI": 0.9998, "AERO": 1.25})

        mgr = PriceFeedManager(redis, fetch_fn=mock_fetch, alchemy_api_key="test-key")
        result = mgr._fetch_alchemy()

        assert len(result) == 4
        assert result["USDC"].price_usd == pytest.approx(1.0001)
        assert result["USDC"].source == "alchemy"
        assert result["AERO"].price_usd == pytest.approx(1.25)

    def test_handles_missing_token_gracefully(self) -> None:
        redis = _make_mock_redis()

        def mock_fetch(url: str, timeout: int = 10) -> Any:
            return _make_alchemy_response({"USDC": 1.0})

        mgr = PriceFeedManager(redis, fetch_fn=mock_fetch, alchemy_api_key="test-key")
        result = mgr._fetch_alchemy()

        assert "USDC" in result
        assert "USDT" not in result

    def test_no_api_key_raises(self) -> None:
        redis = _make_mock_redis()
        mgr = PriceFeedManager(redis)
        # Ensure env vars don't leak
        import os
        old_key = os.environ.pop("ALCHEMY_API_KEY", None)
        old_sep = os.environ.pop("ALCHEMY_SEPOLIA_API_KEY", None)
        try:
            mgr._alchemy_api_key = None
            with pytest.raises(ValueError, match="ALCHEMY_API_KEY"):
                mgr._fetch_alchemy()
        finally:
            if old_key:
                os.environ["ALCHEMY_API_KEY"] = old_key
            if old_sep:
                os.environ["ALCHEMY_SEPOLIA_API_KEY"] = old_sep


class TestFetchPricesFlow:
    def test_alchemy_success_caches_and_returns(self) -> None:
        """When Alchemy succeeds, prices are cached and returned."""
        redis = _make_mock_redis()

        def mock_fetch(url: str, timeout: int = 10) -> Any:
            if "api.g.alchemy.com" in url:
                return _make_alchemy_response({"USDC": 1.0001, "USDT": 1.0})
            raise ConnectionError("Should not call fallback")

        mgr = PriceFeedManager(redis, fetch_fn=mock_fetch, alchemy_api_key="test-key")
        result = mgr.fetch_prices()

        assert "USDC" in result
        assert result["USDC"]["price_usd"] == pytest.approx(1.0001)
        assert result["USDC"]["sources"] == ["alchemy"]
        assert redis.cache_set.call_count >= 2

    def test_alchemy_fail_falls_back_to_defillama(self) -> None:
        """When Alchemy fails, DefiLlama is tried."""
        redis = _make_mock_redis()
        call_log: list[str] = []

        def mock_fetch(url: str, timeout: int = 10) -> Any:
            if "api.g.alchemy.com" in url:
                call_log.append("alchemy")
                raise ConnectionError("Alchemy down")
            if "coins.llama.fi" in url:
                call_log.append("defillama")
                return _make_defillama_response({"USDC": 1.0, "DAI": 0.999})
            raise ConnectionError("Unknown URL")

        mgr = PriceFeedManager(redis, fetch_fn=mock_fetch, alchemy_api_key="test-key")
        result = mgr.fetch_prices()

        assert "alchemy" in call_log
        assert "defillama" in call_log
        assert "USDC" in result
        assert result["USDC"]["sources"] == ["defillama"]

    def test_both_fail_returns_empty(self) -> None:
        """When both sources fail, return empty dict."""
        redis = _make_mock_redis()

        def mock_fetch(url: str, timeout: int = 10) -> Any:
            raise ConnectionError("Network down")

        mgr = PriceFeedManager(redis, fetch_fn=mock_fetch, alchemy_api_key="test-key")
        result = mgr.fetch_prices()

        assert result == {}

    def test_cache_freshness_skips_api_calls(self) -> None:
        """When all prices are fresh (within fetch_interval), skip API calls."""
        redis = _make_mock_redis()
        api_called = False

        def mock_fetch(url: str, timeout: int = 10) -> Any:
            nonlocal api_called
            api_called = True
            return _make_alchemy_response({"USDC": 1.0})

        mgr = PriceFeedManager(
            redis, fetch_fn=mock_fetch, alchemy_api_key="test-key",
            fetch_interval_seconds=30,
        )

        # First call — should hit API
        mgr.fetch_prices()
        assert api_called

        # Second call — should return cached (within 30s)
        api_called = False
        result = mgr.fetch_prices()
        assert not api_called
        assert "USDC" in result

    def test_no_alchemy_key_tries_defillama_directly(self) -> None:
        """When no Alchemy key is set, skip Alchemy and try DefiLlama."""
        redis = _make_mock_redis()

        def mock_fetch(url: str, timeout: int = 10) -> Any:
            if "coins.llama.fi" in url:
                return _make_defillama_response({"USDC": 1.0})
            raise ConnectionError("Should not call Alchemy")

        # Ensure env vars don't provide a key
        import os
        old_key = os.environ.pop("ALCHEMY_API_KEY", None)
        old_sep = os.environ.pop("ALCHEMY_SEPOLIA_API_KEY", None)
        try:
            mgr = PriceFeedManager(redis, fetch_fn=mock_fetch, alchemy_api_key=None)
            mgr._alchemy_api_key = None  # force None in case env leaked
            result = mgr.fetch_prices()
            assert "USDC" in result
        finally:
            if old_key:
                os.environ["ALCHEMY_API_KEY"] = old_key
            if old_sep:
                os.environ["ALCHEMY_SEPOLIA_API_KEY"] = old_sep


class TestStalePriceDetection:
    def test_fresh_price_not_stale(self) -> None:
        redis = _make_mock_redis()
        mgr = PriceFeedManager(redis, ttl_seconds=30)

        # Manually cache a fresh price
        redis._cache[f"{PRICE_CACHE_KEY_PREFIX}USDC"] = {
            "price_usd": 1.0,
            "timestamp": "2026-01-01T00:00:00+00:00",
            "cached_at": time.time(),
        }

        result = mgr.get_cached_price("USDC")
        assert result is not None
        assert not result["stale"]

    def test_old_price_is_stale(self) -> None:
        redis = _make_mock_redis()
        mgr = PriceFeedManager(redis, ttl_seconds=30)

        # Cache a price from 60 seconds ago
        redis._cache[f"{PRICE_CACHE_KEY_PREFIX}USDC"] = {
            "price_usd": 1.0,
            "timestamp": "2026-01-01T00:00:00+00:00",
            "cached_at": time.time() - 60,
        }

        result = mgr.get_cached_price("USDC")
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
        mgr._record_price_history("USDC", 1.0, now)

        twap = mgr.get_twap("USDC", "5m")
        assert twap == 1.0

    def test_twap_multiple_points(self) -> None:
        redis = _make_mock_redis()
        mgr = PriceFeedManager(redis)

        now = time.time()
        mgr._record_price_history("USDC", 0.998, now - 120)
        mgr._record_price_history("USDC", 1.000, now - 60)
        mgr._record_price_history("USDC", 1.002, now)

        twap = mgr.get_twap("USDC", "5m")
        assert twap == pytest.approx(1.000)

    def test_twap_respects_window(self) -> None:
        redis = _make_mock_redis()
        mgr = PriceFeedManager(redis)

        now = time.time()
        # Point outside 5m window
        mgr._record_price_history("USDC", 0.5, now - 400)
        # Points inside 5m window
        mgr._record_price_history("USDC", 1.000, now - 60)
        mgr._record_price_history("USDC", 1.002, now)

        twap = mgr.get_twap("USDC", "5m")
        assert twap == pytest.approx(1.001)

    def test_twap_no_data(self) -> None:
        redis = _make_mock_redis()
        mgr = PriceFeedManager(redis)

        twap = mgr.get_twap("USDC", "5m")
        assert twap is None

    def test_twap_invalid_window(self) -> None:
        redis = _make_mock_redis()
        mgr = PriceFeedManager(redis)

        with pytest.raises(ValueError, match="Unknown TWAP window"):
            mgr.get_twap("USDC", "10m")

    def test_twap_1h_window(self) -> None:
        redis = _make_mock_redis()
        mgr = PriceFeedManager(redis)

        now = time.time()
        mgr._record_price_history("USDC", 0.998, now - 1800)
        mgr._record_price_history("USDC", 1.002, now)

        twap = mgr.get_twap("USDC", "1h")
        assert twap == pytest.approx(1.0)

    def test_twap_24h_window(self) -> None:
        redis = _make_mock_redis()
        mgr = PriceFeedManager(redis)

        now = time.time()
        mgr._record_price_history("USDC", 0.998, now - 43200)
        mgr._record_price_history("USDC", 1.002, now)

        twap = mgr.get_twap("USDC", "24h")
        assert twap == pytest.approx(1.0)


class TestTimestampsUTC:
    def test_prices_have_utc_timestamps(self) -> None:
        redis = _make_mock_redis()

        def mock_fetch(url: str, timeout: int = 10) -> Any:
            return _make_alchemy_response({"USDC": 1.000})

        mgr = PriceFeedManager(redis, fetch_fn=mock_fetch, alchemy_api_key="test-key")
        result = mgr.fetch_prices()

        assert "USDC" in result
        ts = result["USDC"]["timestamp"]
        assert "+00:00" in ts


class TestCustomTokenList:
    def test_alchemy_fetches_all_configured_symbols(self) -> None:
        redis = _make_mock_redis()

        def mock_fetch(url: str, timeout: int = 10) -> Any:
            return _make_alchemy_response({"USDC": 1.0, "AERO": 1.5})

        mgr = PriceFeedManager(redis, fetch_fn=mock_fetch, alchemy_api_key="test-key")
        result = mgr.fetch_prices()

        assert "USDC" in result
        assert "AERO" in result
