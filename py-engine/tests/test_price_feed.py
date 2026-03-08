"""Tests for real-time price feed — DATA-001."""

from __future__ import annotations

import time
from datetime import datetime
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
from strategies.base import TokenPrice

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
                "prices": [
                    {
                        "currency": "usd",
                        "value": str(price),
                        "lastUpdatedAt": "2026-01-01T00:00:00Z",
                    },
                ],
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
            return _make_alchemy_response(
                {"USDC": 1.0001, "USDT": 1.0, "DAI": 0.9998, "AERO": 1.25},
            )

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
                return _make_alchemy_response(
                    {"USDC": 1.0001, "USDT": 1.0, "DAI": 1.0, "AERO": 1.5},
                )
            if "coins.llama.fi" in url:
                # DefiLlama always called for cross-validation
                return _make_defillama_response(
                    {"USDC": 1.0001, "USDT": 1.0, "DAI": 1.0, "AERO": 1.5},
                )
            raise ConnectionError("Unknown URL")

        mgr = PriceFeedManager(redis, fetch_fn=mock_fetch, alchemy_api_key="test-key")
        result = mgr.fetch_prices()

        assert "USDC" in result
        assert result["USDC"]["price_usd"] == pytest.approx(1.0001)
        assert "alchemy" in result["USDC"]["sources"]
        assert redis.cache_set.call_count >= 4

    def test_partial_alchemy_supplements_from_defillama(self) -> None:
        """When Alchemy returns some tokens, DefiLlama fills in the rest."""
        redis = _make_mock_redis()

        def mock_fetch(url: str, timeout: int = 10) -> Any:
            if "api.g.alchemy.com" in url:
                return _make_alchemy_response({"USDC": 1.0001, "USDT": 1.0})
            if "coins.llama.fi" in url:
                return _make_defillama_response({"DAI": 0.999, "AERO": 1.25, "USDC": 1.001})
            raise ConnectionError("Unknown URL")

        mgr = PriceFeedManager(redis, fetch_fn=mock_fetch, alchemy_api_key="test-key")
        result = mgr.fetch_prices()

        # USDC cross-validated: primary source alchemy, both sources listed
        assert result["USDC"]["sources"][0] == "alchemy"
        assert result["USDC"]["price_usd"] == pytest.approx(1.0001)
        assert result["USDT"]["sources"] == ["alchemy"]
        # Missing tokens filled from DefiLlama
        assert result["DAI"]["sources"] == ["defillama"]
        assert result["AERO"]["sources"] == ["defillama"]
        assert result["AERO"]["price_usd"] == pytest.approx(1.25)

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
        mgr = PriceFeedManager(redis, staleness_threshold_seconds=60)

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
        mgr = PriceFeedManager(redis, staleness_threshold_seconds=30)

        # Cache a price from 60 seconds ago (>30s threshold)
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
            if "api.g.alchemy.com" in url:
                return _make_alchemy_response({"USDC": 1.000})
            if "coins.llama.fi" in url:
                return _make_defillama_response({"USDC": 1.000})
            raise ConnectionError("Unknown URL")

        mgr = PriceFeedManager(redis, fetch_fn=mock_fetch, alchemy_api_key="test-key")
        result = mgr.fetch_prices()

        assert "USDC" in result
        ts = result["USDC"]["timestamp"]
        assert "+00:00" in ts


class TestCustomTokenList:
    def test_alchemy_fetches_all_configured_symbols(self) -> None:
        redis = _make_mock_redis()

        def mock_fetch(url: str, timeout: int = 10) -> Any:
            if "api.g.alchemy.com" in url:
                return _make_alchemy_response({"USDC": 1.0, "AERO": 1.5})
            if "coins.llama.fi" in url:
                return _make_defillama_response({"USDC": 1.0, "AERO": 1.5})
            raise ConnectionError("Unknown URL")

        mgr = PriceFeedManager(redis, fetch_fn=mock_fetch, alchemy_api_key="test-key")
        result = mgr.fetch_prices()

        assert "USDC" in result
        assert "AERO" in result


# ── Cross-source validation tests ─────────────────────


class TestCrossSourceValidation:
    """Tests for multi-source price validation (>2% deviation rejection)."""

    def test_prices_within_threshold_accepted(self) -> None:
        """Prices with <2% deviation are accepted, primary source preferred."""
        redis = _make_mock_redis()

        def mock_fetch(url: str, timeout: int = 10) -> Any:
            if "api.g.alchemy.com" in url:
                return _make_alchemy_response({"USDC": 1.001, "DAI": 0.999})
            if "coins.llama.fi" in url:
                return _make_defillama_response({"USDC": 1.002, "DAI": 1.000})
            raise ConnectionError("Unknown URL")

        mgr = PriceFeedManager(redis, fetch_fn=mock_fetch, alchemy_api_key="test-key")
        result = mgr.fetch_prices()

        assert "USDC" in result
        assert "DAI" in result
        # Primary (alchemy) price used when validated
        assert result["USDC"]["price_usd"] == pytest.approx(1.001)
        # Both sources recorded
        assert "alchemy" in result["USDC"]["sources"]
        assert "defillama" in result["USDC"]["sources"]

    def test_prices_exceeding_threshold_rejected(self) -> None:
        """Prices with >2% deviation are rejected entirely."""
        redis = _make_mock_redis()

        def mock_fetch(url: str, timeout: int = 10) -> Any:
            if "api.g.alchemy.com" in url:
                return _make_alchemy_response({"USDC": 1.00, "AERO": 2.00})
            if "coins.llama.fi" in url:
                # AERO has >2% deviation (2.00 vs 1.50 = ~28% deviation)
                return _make_defillama_response({"USDC": 1.005, "AERO": 1.50})
            raise ConnectionError("Unknown URL")

        mgr = PriceFeedManager(redis, fetch_fn=mock_fetch, alchemy_api_key="test-key")
        result = mgr.fetch_prices()

        assert "USDC" in result  # Within threshold
        assert "AERO" not in result  # Rejected due to deviation

    def test_single_source_accepted_without_validation(self) -> None:
        """Token from only one source is accepted without cross-validation."""
        redis = _make_mock_redis()

        def mock_fetch(url: str, timeout: int = 10) -> Any:
            if "api.g.alchemy.com" in url:
                return _make_alchemy_response({"USDC": 1.0, "USDT": 1.0})
            if "coins.llama.fi" in url:
                # Only returns USDC, not USDT
                return _make_defillama_response({"USDC": 1.0})
            raise ConnectionError("Unknown URL")

        mgr = PriceFeedManager(redis, fetch_fn=mock_fetch, alchemy_api_key="test-key")
        result = mgr.fetch_prices()

        assert "USDC" in result
        assert "USDT" in result  # Single source, accepted without cross-check

    def test_exact_boundary_deviation_rejected(self) -> None:
        """Prices at >2% deviation are rejected."""
        redis = _make_mock_redis()

        def mock_fetch(url: str, timeout: int = 10) -> Any:
            if "api.g.alchemy.com" in url:
                # ~2.08% deviation: 1.00 vs 1.021
                return _make_alchemy_response({"USDC": 1.00})
            if "coins.llama.fi" in url:
                return _make_defillama_response({"USDC": 1.021})
            raise ConnectionError("Unknown URL")

        mgr = PriceFeedManager(redis, fetch_fn=mock_fetch, alchemy_api_key="test-key")
        result = mgr.fetch_prices()

        assert "USDC" not in result  # >2% deviation rejected

    def test_custom_deviation_threshold(self) -> None:
        """Custom deviation threshold is respected."""
        redis = _make_mock_redis()

        def mock_fetch(url: str, timeout: int = 10) -> Any:
            if "api.g.alchemy.com" in url:
                return _make_alchemy_response({"USDC": 1.00})
            if "coins.llama.fi" in url:
                # ~1% deviation
                return _make_defillama_response({"USDC": 1.01})
            raise ConnectionError("Unknown URL")

        # Strict threshold: 0.5%
        mgr = PriceFeedManager(
            redis, fetch_fn=mock_fetch, alchemy_api_key="test-key",
            deviation_threshold=0.005,
        )
        result = mgr.fetch_prices()
        assert "USDC" not in result  # Rejected at 0.5% threshold

    def test_defillama_fail_uses_alchemy_only(self) -> None:
        """When DefiLlama fails, Alchemy results used without cross-validation."""
        redis = _make_mock_redis()

        def mock_fetch(url: str, timeout: int = 10) -> Any:
            if "api.g.alchemy.com" in url:
                return _make_alchemy_response({"USDC": 1.0, "USDT": 1.0})
            if "coins.llama.fi" in url:
                raise ConnectionError("DefiLlama down")
            raise ConnectionError("Unknown URL")

        mgr = PriceFeedManager(redis, fetch_fn=mock_fetch, alchemy_api_key="test-key")
        result = mgr.fetch_prices()

        assert "USDC" in result
        assert result["USDC"]["sources"] == ["alchemy"]


# ── Staleness threshold tests ────────────────────────


class TestStalenessThreshold:
    """Tests for configurable staleness detection."""

    def test_default_threshold_is_60s(self) -> None:
        redis = _make_mock_redis()
        mgr = PriceFeedManager(redis)
        assert mgr._staleness_threshold == 60

    def test_custom_threshold_via_constructor(self) -> None:
        redis = _make_mock_redis()
        mgr = PriceFeedManager(redis, staleness_threshold_seconds=120)
        assert mgr._staleness_threshold == 120

    def test_threshold_from_env_var(self) -> None:
        import os
        redis = _make_mock_redis()
        old = os.environ.get("PRICE_STALENESS_THRESHOLD_SECONDS")
        try:
            os.environ["PRICE_STALENESS_THRESHOLD_SECONDS"] = "45"
            mgr = PriceFeedManager(redis)
            assert mgr._staleness_threshold == 45
        finally:
            if old is not None:
                os.environ["PRICE_STALENESS_THRESHOLD_SECONDS"] = old
            else:
                os.environ.pop("PRICE_STALENESS_THRESHOLD_SECONDS", None)

    def test_price_within_threshold_not_stale(self) -> None:
        redis = _make_mock_redis()
        mgr = PriceFeedManager(redis, staleness_threshold_seconds=60)

        redis._cache[f"{PRICE_CACHE_KEY_PREFIX}USDC"] = {
            "price_usd": 1.0,
            "timestamp": "2026-01-01T00:00:00+00:00",
            "cached_at": time.time() - 30,  # 30s old, threshold 60s
        }

        result = mgr.get_cached_price("USDC")
        assert result is not None
        assert not result["stale"]

    def test_price_beyond_threshold_is_stale(self) -> None:
        redis = _make_mock_redis()
        mgr = PriceFeedManager(redis, staleness_threshold_seconds=60)

        redis._cache[f"{PRICE_CACHE_KEY_PREFIX}USDC"] = {
            "price_usd": 1.0,
            "timestamp": "2026-01-01T00:00:00+00:00",
            "cached_at": time.time() - 90,  # 90s old, threshold 60s
        }

        result = mgr.get_cached_price("USDC")
        assert result is not None
        assert result["stale"]

    def test_is_any_stale_all_fresh(self) -> None:
        redis = _make_mock_redis()
        mgr = PriceFeedManager(redis, staleness_threshold_seconds=60)

        now = time.time()
        for symbol in ALCHEMY_SYMBOLS:
            redis._cache[f"{PRICE_CACHE_KEY_PREFIX}{symbol}"] = {
                "price_usd": 1.0,
                "timestamp": "2026-01-01T00:00:00+00:00",
                "cached_at": now,
            }

        assert not mgr.is_any_stale()

    def test_is_any_stale_one_stale(self) -> None:
        redis = _make_mock_redis()
        mgr = PriceFeedManager(redis, staleness_threshold_seconds=60)

        now = time.time()
        for symbol in ALCHEMY_SYMBOLS:
            redis._cache[f"{PRICE_CACHE_KEY_PREFIX}{symbol}"] = {
                "price_usd": 1.0,
                "timestamp": "2026-01-01T00:00:00+00:00",
                "cached_at": now,
            }
        # Make one stale
        redis._cache[f"{PRICE_CACHE_KEY_PREFIX}AERO"]["cached_at"] = now - 120

        assert mgr.is_any_stale()

    def test_is_any_stale_missing_price(self) -> None:
        redis = _make_mock_redis()
        mgr = PriceFeedManager(redis, staleness_threshold_seconds=60)
        # No prices cached at all
        assert mgr.is_any_stale()


# ── Cache TTL tests ──────────────────────────────────


class TestCacheTTL:
    """Tests for PRICE_CACHE_TTL env var."""

    def test_default_cache_ttl(self) -> None:
        redis = _make_mock_redis()
        mgr = PriceFeedManager(redis)
        assert mgr._ttl == 30

    def test_custom_cache_ttl_via_constructor(self) -> None:
        redis = _make_mock_redis()
        mgr = PriceFeedManager(redis, ttl_seconds=120)
        assert mgr._ttl == 120

    def test_cache_ttl_from_env_var(self) -> None:
        import os
        redis = _make_mock_redis()
        old = os.environ.get("PRICE_CACHE_TTL")
        try:
            os.environ["PRICE_CACHE_TTL"] = "90"
            mgr = PriceFeedManager(redis)
            assert mgr._ttl == 90
        finally:
            if old is not None:
                os.environ["PRICE_CACHE_TTL"] = old
            else:
                os.environ.pop("PRICE_CACHE_TTL", None)

    def test_cache_set_uses_ttl(self) -> None:
        redis = _make_mock_redis()
        mgr = PriceFeedManager(redis, ttl_seconds=45)

        mgr._cache_price("USDC", 1.0, "2026-01-01T00:00:00+00:00")

        redis.cache_set.assert_called_once()
        call_args = redis.cache_set.call_args
        assert call_args[0][2] == 45  # TTL argument


# ── TokenPrice dataclass tests ───────────────────────


class TestGetTokenPrices:
    """Tests for get_token_prices() returning TokenPrice dataclasses."""

    def test_returns_token_price_dataclasses(self) -> None:
        redis = _make_mock_redis()

        def mock_fetch(url: str, timeout: int = 10) -> Any:
            if "api.g.alchemy.com" in url:
                return _make_alchemy_response(
                    {"USDC": 1.0001, "USDT": 1.0, "DAI": 0.999, "AERO": 1.25},
                )
            if "coins.llama.fi" in url:
                return _make_defillama_response(
                    {"USDC": 1.0001, "USDT": 1.0, "DAI": 0.999, "AERO": 1.25},
                )
            raise ConnectionError("Unknown URL")

        mgr = PriceFeedManager(redis, fetch_fn=mock_fetch, alchemy_api_key="test-key")
        prices = mgr.get_token_prices()

        assert len(prices) == 4
        assert all(isinstance(p, TokenPrice) for p in prices)

        usdc = next(p for p in prices if p.token == "USDC")
        assert usdc.price == pytest.approx(1.0001)
        assert usdc.source == "alchemy"
        assert isinstance(usdc.timestamp, datetime)

    def test_returns_empty_list_when_no_prices(self) -> None:
        redis = _make_mock_redis()

        def mock_fetch(url: str, timeout: int = 10) -> Any:
            raise ConnectionError("Network down")

        mgr = PriceFeedManager(redis, fetch_fn=mock_fetch, alchemy_api_key="test-key")
        prices = mgr.get_token_prices()

        assert prices == []

    def test_token_price_has_correct_fields(self) -> None:
        redis = _make_mock_redis()

        def mock_fetch(url: str, timeout: int = 10) -> Any:
            if "api.g.alchemy.com" in url:
                return _make_alchemy_response({"AERO": 1.5})
            if "coins.llama.fi" in url:
                return _make_defillama_response({"AERO": 1.5})
            raise ConnectionError("Unknown URL")

        mgr = PriceFeedManager(redis, fetch_fn=mock_fetch, alchemy_api_key="test-key")
        prices = mgr.get_token_prices()

        assert len(prices) == 1
        aero = prices[0]
        assert aero.token == "AERO"
        assert aero.price == pytest.approx(1.5)
        assert aero.source in ("alchemy", "defillama")
        assert isinstance(aero.timestamp, datetime)
