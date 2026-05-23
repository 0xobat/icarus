"""Tests for DeFi protocol metrics collector — DATA-003."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from data.defi_metrics import (
    AaveMarketMetrics,
    AaveMetrics,
    DeFiMetricsCollector,
    ProtocolTVL,
)

# ── Fixtures ──────────────────────────────────────────


def _make_mock_redis() -> MagicMock:
    """Create a mock RedisManager with cache operations."""
    redis = MagicMock()
    redis._cache: dict[str, Any] = {}

    def cache_set(key: str, value: Any, ttl: int) -> None:
        redis._cache[key] = value

    def cache_get(key: str) -> Any | None:
        return redis._cache.get(key)

    redis.cache_set = MagicMock(side_effect=cache_set)
    redis.cache_get = MagicMock(side_effect=cache_get)
    return redis


def _make_yields_response(
    aave_markets: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a mock DeFi Llama yields API response."""
    data: list[dict[str, Any]] = []
    if aave_markets:
        for m in aave_markets:
            data.append({
                "project": "aave-v3",
                "chain": "Ethereum",
                "symbol": m.get("symbol", "ETH"),
                "utilization": m.get("utilization", 50.0),
                "apy": m.get("apy", 3.5),
                "apyBorrow": m.get("apyBorrow", 5.0),
                "tvlUsd": m.get("tvlUsd", 1000000.0),
            })
    return {"data": data}


# ── Tests: Dataclasses ───────────────────────────────


class TestDataclasses:
    def test_aave_market_metrics(self) -> None:
        m = AaveMarketMetrics(
            symbol="ETH",
            utilization_rate=80.0,
            supply_apy=3.5,
            borrow_apy=5.0,
            available_liquidity=1000000.0,
        )
        assert m.symbol == "ETH"
        assert m.timestamp  # auto-populated

    def test_aave_metrics_to_dict(self) -> None:
        market = AaveMarketMetrics("ETH", 80.0, 3.5, 5.0, 1e6)
        metrics = AaveMetrics(markets=[market])
        d = metrics.to_dict()
        assert d["protocol"] == "aave"
        assert len(d["markets"]) == 1
        assert d["markets"][0]["symbol"] == "ETH"

    def test_protocol_tvl_to_dict(self) -> None:
        tvl = ProtocolTVL(protocol="aave", tvl_usd=5e9)
        d = tvl.to_dict()
        assert d["protocol"] == "aave"
        assert d["tvl_usd"] == 5e9
        assert d["chain"] == "ethereum"


# ── Tests: Aave ──────────────────────────────────────


class TestAaveMetrics:
    def test_fetches_and_caches(self) -> None:
        redis = _make_mock_redis()

        def mock_fetch(url: str, timeout: int = 10) -> Any:
            return _make_yields_response(aave_markets=[
                {"symbol": "ETH", "utilization": 80.0, "apy": 3.5, "apyBorrow": 5.0, "tvlUsd": 1e6},
                {"symbol": "USDC", "utilization": 90.0, "apy": 4.0,
                 "apyBorrow": 6.0, "tvlUsd": 2e6},
            ])

        collector = DeFiMetricsCollector(redis, fetch_fn=mock_fetch)
        result = collector.fetch_aave_metrics()

        assert result is not None
        assert len(result.markets) == 2
        assert result.markets[0].symbol == "ETH"
        assert result.markets[0].supply_apy == 3.5
        assert result.markets[1].utilization_rate == 90.0
        assert redis.cache_set.call_count == 1

    def test_handles_empty_response(self) -> None:
        redis = _make_mock_redis()

        def mock_fetch(url: str, timeout: int = 10) -> Any:
            return {"data": []}

        collector = DeFiMetricsCollector(redis, fetch_fn=mock_fetch)
        result = collector.fetch_aave_metrics()

        assert result is not None
        assert len(result.markets) == 0


# ── Tests: TVL ───────────────────────────────────────


class TestProtocolTVL:
    def test_fetches_tvl(self) -> None:
        redis = _make_mock_redis()

        def mock_fetch(url: str, timeout: int = 10) -> Any:
            return 5000000000.0  # DeFi Llama returns raw number

        collector = DeFiMetricsCollector(redis, fetch_fn=mock_fetch)
        result = collector.fetch_tvl("aave")

        assert result is not None
        assert result.tvl_usd == 5e9
        assert result.protocol == "aave"
        assert redis.cache_set.call_count == 1

    def test_fetches_all_tvl(self) -> None:
        redis = _make_mock_redis()

        def mock_fetch(url: str, timeout: int = 10) -> Any:
            if "aave" in url:
                return 5e9
            if "aerodrome" in url:
                return 0.5e9
            return 0

        collector = DeFiMetricsCollector(redis, fetch_fn=mock_fetch)
        results = collector.fetch_all_tvl()

        assert len(results) == 2
        assert results["aave"].tvl_usd == 5e9
        assert results["aerodrome"].tvl_usd == 0.5e9


# ── Tests: Unified interface ─────────────────────────


class TestUnifiedInterface:
    def test_get_aave_metrics(self) -> None:
        redis = _make_mock_redis()

        def mock_fetch(url: str, timeout: int = 10) -> Any:
            return _make_yields_response(aave_markets=[
                {"symbol": "ETH", "apy": 3.5},
            ])

        collector = DeFiMetricsCollector(redis, fetch_fn=mock_fetch)
        result = collector.get_metrics("aave")

        assert result is not None
        assert result["protocol"] == "aave"

    def test_unknown_protocol(self) -> None:
        redis = _make_mock_redis()
        collector = DeFiMetricsCollector(redis)
        result = collector.get_metrics("unknown_proto")
        assert result is None


# ── Tests: Graceful degradation ──────────────────────


class TestGracefulDegradation:
    def test_aave_uses_cache_on_failure(self) -> None:
        redis = _make_mock_redis()
        call_count = 0

        def mock_fetch(url: str, timeout: int = 10) -> Any:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First call succeeds
                return _make_yields_response(aave_markets=[
                    {"symbol": "ETH", "apy": 3.5},
                ])
            # Second call fails
            raise ConnectionError("API down")

        collector = DeFiMetricsCollector(redis, fetch_fn=mock_fetch)

        # First call populates cache
        result1 = collector.fetch_aave_metrics()
        assert result1 is not None
        assert len(result1.markets) == 1

        # Second call fails but uses cache
        result2 = collector.fetch_aave_metrics()
        assert result2 is not None
        assert len(result2.markets) == 1
        assert result2.markets[0].symbol == "ETH"

    def test_tvl_uses_cache_on_failure(self) -> None:
        redis = _make_mock_redis()
        call_count = 0

        def mock_fetch(url: str, timeout: int = 10) -> Any:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return 5e9
            raise ConnectionError("API down")

        collector = DeFiMetricsCollector(redis, fetch_fn=mock_fetch)

        result1 = collector.fetch_tvl("aave")
        assert result1 is not None

        result2 = collector.fetch_tvl("aave")
        assert result2 is not None
        assert result2.tvl_usd == 5e9

    def test_returns_none_with_no_cache(self) -> None:
        redis = _make_mock_redis()

        def mock_fetch(url: str, timeout: int = 10) -> Any:
            raise ConnectionError("API down")

        collector = DeFiMetricsCollector(redis, fetch_fn=mock_fetch)

        assert collector.fetch_aave_metrics() is None
        assert collector.fetch_tvl("aave") is None


# ── Tests: Timestamps ────────────────────────────────


class TestTimestamps:
    def test_all_metrics_have_utc_timestamps(self) -> None:
        redis = _make_mock_redis()

        def mock_fetch(url: str, timeout: int = 10) -> Any:
            return _make_yields_response(aave_markets=[{"symbol": "ETH"}])

        collector = DeFiMetricsCollector(redis, fetch_fn=mock_fetch)

        aave = collector.fetch_aave_metrics()
        assert aave is not None
        assert "+00:00" in aave.timestamp
