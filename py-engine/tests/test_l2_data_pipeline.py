"""Tests for L2 data pipeline — DATA-005."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from data.defi_metrics import (
    L2_PROTOCOL_CHAINS,
    AerodromeMetrics,
    DeFiMetricsCollector,
    GmxMetrics,
)
from data.gas_monitor import (
    L2_GAS_PARAMS,
    SUPPORTED_L2_CHAINS,
    GasMonitor,
    L2GasEstimate,
)
from data.price_feed import (
    L2_TOKEN_MAPPINGS,
    PriceFeedManager,
)

# ── Fixtures ──────────────────────────────────────────


def _make_mock_redis() -> MagicMock:
    """Create a mock RedisManager with cache and sorted-set operations."""
    redis_mock = MagicMock()
    redis_mock._cache: dict[str, Any] = {}
    redis_mock._zsets: dict[str, list[tuple[float, str]]] = {}

    def cache_set(key: str, value: Any, ttl: int) -> None:
        redis_mock._cache[key] = value

    def cache_get(key: str) -> Any | None:
        return redis_mock._cache.get(key)

    redis_mock.cache_set = MagicMock(side_effect=cache_set)
    redis_mock.cache_get = MagicMock(side_effect=cache_get)

    def zadd(key: str, mapping: dict[str, float]) -> None:
        if key not in redis_mock._zsets:
            redis_mock._zsets[key] = []
        for member, score in mapping.items():
            redis_mock._zsets[key].append((score, member))

    def zrangebyscore(key: str, min_score: float, max_score: float) -> list[str]:
        if key not in redis_mock._zsets:
            return []
        return [
            member
            for score, member in redis_mock._zsets[key]
            if (str(min_score) == "-inf" or score >= float(min_score))
            and (str(max_score) == "+inf" or score <= float(max_score))
        ]

    def zremrangebyscore(key: str, min_score: str, max_score: float) -> None:
        if key not in redis_mock._zsets:
            return
        redis_mock._zsets[key] = [
            (s, m) for s, m in redis_mock._zsets[key] if not (s <= float(max_score))
        ]

    redis_mock.client = MagicMock()
    redis_mock.client.zadd = MagicMock(side_effect=zadd)
    redis_mock.client.zrangebyscore = MagicMock(side_effect=zrangebyscore)
    redis_mock.client.zremrangebyscore = MagicMock(side_effect=zremrangebyscore)

    return redis_mock


# ══════════════════════════════════════════════════════
# Part 1 — PriceFeedManager L2 token mappings
# ══════════════════════════════════════════════════════


class TestL2TokenMappings:
    def test_l2_token_mappings_has_expected_tokens(self) -> None:
        assert "ARB" in L2_TOKEN_MAPPINGS
        assert "GMX" in L2_TOKEN_MAPPINGS
        assert "AERO" in L2_TOKEN_MAPPINGS
        assert "OP" in L2_TOKEN_MAPPINGS

    def test_each_mapping_has_required_fields(self) -> None:
        for token, info in L2_TOKEN_MAPPINGS.items():
            assert "chain" in info, f"{token} missing chain"
            assert "contract" in info, f"{token} missing contract"
            assert "coingecko_id" in info, f"{token} missing coingecko_id"

    def test_arb_is_on_arbitrum(self) -> None:
        assert L2_TOKEN_MAPPINGS["ARB"]["chain"] == "arbitrum"

    def test_aero_is_on_base(self) -> None:
        assert L2_TOKEN_MAPPINGS["AERO"]["chain"] == "base"


class TestGetL2Tokens:
    def test_arbitrum_tokens(self) -> None:
        redis_mock = _make_mock_redis()
        mgr = PriceFeedManager(redis_mock)
        tokens = mgr.get_l2_tokens("arbitrum")
        assert "ARB" in tokens
        assert "GMX" in tokens
        assert "AERO" not in tokens

    def test_base_tokens(self) -> None:
        redis_mock = _make_mock_redis()
        mgr = PriceFeedManager(redis_mock)
        tokens = mgr.get_l2_tokens("base")
        assert "AERO" in tokens
        assert "ARB" not in tokens

    def test_unknown_chain_returns_empty(self) -> None:
        redis_mock = _make_mock_redis()
        mgr = PriceFeedManager(redis_mock)
        tokens = mgr.get_l2_tokens("solana")
        assert tokens == []

    def test_case_insensitive(self) -> None:
        redis_mock = _make_mock_redis()
        mgr = PriceFeedManager(redis_mock)
        assert mgr.get_l2_tokens("Arbitrum") == mgr.get_l2_tokens("arbitrum")


class TestIsL2Token:
    def test_arb_is_l2(self) -> None:
        redis_mock = _make_mock_redis()
        mgr = PriceFeedManager(redis_mock)
        assert mgr.is_l2_token("ARB") is True

    def test_eth_is_not_l2(self) -> None:
        redis_mock = _make_mock_redis()
        mgr = PriceFeedManager(redis_mock)
        assert mgr.is_l2_token("ETH") is False

    def test_case_insensitive(self) -> None:
        redis_mock = _make_mock_redis()
        mgr = PriceFeedManager(redis_mock)
        assert mgr.is_l2_token("arb") is True
        assert mgr.is_l2_token("Gmx") is True


# ══════════════════════════════════════════════════════
# Part 2 — GasMonitor L2 gas estimation
# ══════════════════════════════════════════════════════


class TestL2GasEstimateDataclass:
    def test_fields(self) -> None:
        est = L2GasEstimate(
            l2_gas=2100.0, l1_data_cost=10500.0,
            total_cost_wei=12600000000000, chain="arbitrum",
        )
        assert est.l2_gas == 2100.0
        assert est.l1_data_cost == 10500.0
        assert est.total_cost_wei == 12600000000000
        assert est.chain == "arbitrum"


class TestEstimateL2Gas:
    def test_arbitrum_estimate(self) -> None:
        redis_mock = _make_mock_redis()
        mon = GasMonitor(redis_mock)
        est = mon.estimate_l2_gas("arbitrum", gas_units=21000)

        assert isinstance(est, L2GasEstimate)
        assert est.chain == "arbitrum"
        expected_l2 = L2_GAS_PARAMS["arbitrum"]["base_l2_gas_gwei"] * 21000
        assert est.l2_gas == pytest.approx(expected_l2)

    def test_base_estimate(self) -> None:
        redis_mock = _make_mock_redis()
        mon = GasMonitor(redis_mock)
        est = mon.estimate_l2_gas("base", gas_units=21000)

        assert est.chain == "base"
        expected_l2 = L2_GAS_PARAMS["base"]["base_l2_gas_gwei"] * 21000
        assert est.l2_gas == pytest.approx(expected_l2)

    def test_total_cost_includes_l1_data(self) -> None:
        redis_mock = _make_mock_redis()
        mon = GasMonitor(redis_mock)
        est = mon.estimate_l2_gas("arbitrum", gas_units=21000)

        params = L2_GAS_PARAMS["arbitrum"]
        expected_total_gwei = (
            params["base_l2_gas_gwei"] * 21000
            + params["l1_data_cost_gwei"] * 21000
        )
        expected_wei = int(expected_total_gwei * 1e9)
        assert est.total_cost_wei == expected_wei

    def test_unsupported_chain_raises(self) -> None:
        redis_mock = _make_mock_redis()
        mon = GasMonitor(redis_mock)
        with pytest.raises(ValueError, match="Unsupported L2 chain"):
            mon.estimate_l2_gas("polygon")

    def test_case_insensitive(self) -> None:
        redis_mock = _make_mock_redis()
        mon = GasMonitor(redis_mock)
        est = mon.estimate_l2_gas("Arbitrum", gas_units=21000)
        assert est.chain == "arbitrum"

    def test_default_gas_units(self) -> None:
        redis_mock = _make_mock_redis()
        mon = GasMonitor(redis_mock)
        est = mon.estimate_l2_gas("arbitrum")
        # Default is 21000 gas units
        expected_l2 = L2_GAS_PARAMS["arbitrum"]["base_l2_gas_gwei"] * 21000
        assert est.l2_gas == pytest.approx(expected_l2)


class TestGetL2Overhead:
    def test_arbitrum_overhead(self) -> None:
        redis_mock = _make_mock_redis()
        mon = GasMonitor(redis_mock)
        overhead = mon.get_l2_overhead("arbitrum")
        assert overhead == L2_GAS_PARAMS["arbitrum"]["l1_overhead_factor"]

    def test_base_overhead(self) -> None:
        redis_mock = _make_mock_redis()
        mon = GasMonitor(redis_mock)
        overhead = mon.get_l2_overhead("base")
        assert overhead == L2_GAS_PARAMS["base"]["l1_overhead_factor"]

    def test_unsupported_chain_raises(self) -> None:
        redis_mock = _make_mock_redis()
        mon = GasMonitor(redis_mock)
        with pytest.raises(ValueError, match="Unsupported L2 chain"):
            mon.get_l2_overhead("polygon")

    def test_supported_l2_chains_list(self) -> None:
        assert "arbitrum" in SUPPORTED_L2_CHAINS
        assert "base" in SUPPORTED_L2_CHAINS
        assert "optimism" in SUPPORTED_L2_CHAINS


# ══════════════════════════════════════════════════════
# Part 3 — DeFiMetricsCollector GMX + Aerodrome
# ══════════════════════════════════════════════════════


class TestGmxMetrics:
    def test_collect_gmx_metrics(self) -> None:
        redis_mock = _make_mock_redis()
        call_urls: list[str] = []

        def mock_fetch(url: str, timeout: int = 10) -> Any:
            call_urls.append(url)
            if "tvl/gmx" in url:
                return 500_000_000.0
            if "yields" in url:
                return {"data": [
                    {
                        "project": "gmx",
                        "chain": "Arbitrum",
                        "symbol": "GLP",
                        "tvlUsd": 300_000_000.0,
                        "volumeUsd1d": 50_000_000.0,
                    },
                ]}
            return {}

        collector = DeFiMetricsCollector(redis_mock, fetch_fn=mock_fetch)
        result = collector.collect_gmx_metrics()

        assert result is not None
        assert result.tvl_usd == 500_000_000.0
        assert result.volume_24h == 50_000_000.0
        assert result.open_interest_usd == 300_000_000.0
        assert result.chain == "arbitrum"
        assert redis_mock.cache_set.call_count == 1

    def test_gmx_fetch_failure_returns_none(self) -> None:
        redis_mock = _make_mock_redis()

        def mock_fetch(url: str, timeout: int = 10) -> Any:
            raise ConnectionError("API down")

        collector = DeFiMetricsCollector(redis_mock, fetch_fn=mock_fetch)
        assert collector.collect_gmx_metrics() is None

    def test_gmx_uses_cache_on_failure(self) -> None:
        redis_mock = _make_mock_redis()
        call_count = 0

        def mock_fetch(url: str, timeout: int = 10) -> Any:
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                if "tvl/gmx" in url:
                    return 500_000_000.0
                return {"data": [
                    {
                        "project": "gmx",
                        "chain": "Arbitrum",
                        "symbol": "GLP",
                        "tvlUsd": 300_000_000.0,
                        "volumeUsd1d": 50_000_000.0,
                    },
                ]}
            raise ConnectionError("API down")

        collector = DeFiMetricsCollector(redis_mock, fetch_fn=mock_fetch)
        result1 = collector.collect_gmx_metrics()
        assert result1 is not None

        result2 = collector.collect_gmx_metrics()
        assert result2 is not None
        assert result2.tvl_usd == 500_000_000.0


class TestAerodromeMetrics:
    def test_collect_aerodrome_metrics(self) -> None:
        redis_mock = _make_mock_redis()

        def mock_fetch(url: str, timeout: int = 10) -> Any:
            if "tvl/aerodrome" in url:
                return 200_000_000.0
            if "yields" in url:
                return {"data": [
                    {
                        "project": "aerodrome",
                        "chain": "Base",
                        "symbol": "USDC-ETH",
                        "tvlUsd": 50_000_000.0,
                        "volumeUsd1d": 10_000_000.0,
                        "apy": 15.5,
                    },
                    {
                        "project": "aerodrome",
                        "chain": "Base",
                        "symbol": "AERO-USDC",
                        "tvlUsd": 30_000_000.0,
                        "volumeUsd1d": 5_000_000.0,
                        "apy": 25.0,
                    },
                ]}
            return {}

        collector = DeFiMetricsCollector(redis_mock, fetch_fn=mock_fetch)
        result = collector.collect_aerodrome_metrics()

        assert result is not None
        assert result.tvl_usd == 200_000_000.0
        assert result.volume_24h == 15_000_000.0
        assert len(result.pools) == 2
        assert result.chain == "base"

    def test_aerodrome_fetch_failure_returns_none(self) -> None:
        redis_mock = _make_mock_redis()

        def mock_fetch(url: str, timeout: int = 10) -> Any:
            raise ConnectionError("API down")

        collector = DeFiMetricsCollector(redis_mock, fetch_fn=mock_fetch)
        assert collector.collect_aerodrome_metrics() is None

    def test_aerodrome_pool_data_structure(self) -> None:
        redis_mock = _make_mock_redis()

        def mock_fetch(url: str, timeout: int = 10) -> Any:
            if "tvl/aerodrome" in url:
                return 100_000_000.0
            return {"data": [
                {
                    "project": "aerodrome",
                    "chain": "Base",
                    "symbol": "USDC-ETH",
                    "tvlUsd": 50_000_000.0,
                    "volumeUsd1d": 10_000_000.0,
                    "apy": 15.5,
                },
            ]}

        collector = DeFiMetricsCollector(redis_mock, fetch_fn=mock_fetch)
        result = collector.collect_aerodrome_metrics()

        assert result is not None
        pool = result.pools[0]
        assert pool["symbol"] == "USDC-ETH"
        assert pool["tvl_usd"] == 50_000_000.0
        assert pool["volume_24h"] == 10_000_000.0
        assert pool["apy"] == 15.5


class TestGetL2ProtocolMetrics:
    def test_gmx_on_arbitrum(self) -> None:
        redis_mock = _make_mock_redis()

        def mock_fetch(url: str, timeout: int = 10) -> Any:
            if "tvl/gmx" in url:
                return 500_000_000.0
            return {"data": []}

        collector = DeFiMetricsCollector(redis_mock, fetch_fn=mock_fetch)
        result = collector.get_l2_protocol_metrics("gmx", "arbitrum")

        assert result is not None
        assert result["protocol"] == "gmx"
        assert result["chain"] == "arbitrum"

    def test_aerodrome_on_base(self) -> None:
        redis_mock = _make_mock_redis()

        def mock_fetch(url: str, timeout: int = 10) -> Any:
            if "tvl/aerodrome" in url:
                return 200_000_000.0
            return {"data": []}

        collector = DeFiMetricsCollector(redis_mock, fetch_fn=mock_fetch)
        result = collector.get_l2_protocol_metrics("aerodrome", "base")

        assert result is not None
        assert result["protocol"] == "aerodrome"

    def test_chain_mismatch_returns_none(self) -> None:
        redis_mock = _make_mock_redis()
        collector = DeFiMetricsCollector(redis_mock)
        result = collector.get_l2_protocol_metrics("gmx", "base")
        assert result is None

    def test_unknown_protocol_returns_none(self) -> None:
        redis_mock = _make_mock_redis()
        collector = DeFiMetricsCollector(redis_mock)
        result = collector.get_l2_protocol_metrics("unknown_proto", "arbitrum")
        assert result is None

    def test_l2_protocol_chains_mapping(self) -> None:
        assert L2_PROTOCOL_CHAINS["gmx"] == "arbitrum"
        assert L2_PROTOCOL_CHAINS["aerodrome"] == "base"


class TestUnifiedInterfaceL2:
    def test_get_metrics_gmx(self) -> None:
        redis_mock = _make_mock_redis()

        def mock_fetch(url: str, timeout: int = 10) -> Any:
            if "tvl/gmx" in url:
                return 500_000_000.0
            return {"data": []}

        collector = DeFiMetricsCollector(redis_mock, fetch_fn=mock_fetch)
        result = collector.get_metrics("gmx")

        assert result is not None
        assert result["protocol"] == "gmx"

    def test_get_metrics_aerodrome(self) -> None:
        redis_mock = _make_mock_redis()

        def mock_fetch(url: str, timeout: int = 10) -> Any:
            if "tvl/aerodrome" in url:
                return 200_000_000.0
            return {"data": []}

        collector = DeFiMetricsCollector(redis_mock, fetch_fn=mock_fetch)
        result = collector.get_metrics("aerodrome")

        assert result is not None
        assert result["protocol"] == "aerodrome"


class TestGmxMetricsDataclass:
    def test_to_dict(self) -> None:
        m = GmxMetrics(tvl_usd=500e6, volume_24h=50e6, open_interest_usd=300e6)
        d = m.to_dict()
        assert d["protocol"] == "gmx"
        assert d["chain"] == "arbitrum"
        assert d["tvl_usd"] == 500e6

    def test_auto_timestamp(self) -> None:
        m = GmxMetrics(tvl_usd=0, volume_24h=0, open_interest_usd=0)
        assert m.timestamp
        assert "+00:00" in m.timestamp


class TestAerodromeMetricsDataclass:
    def test_to_dict(self) -> None:
        m = AerodromeMetrics(tvl_usd=200e6, volume_24h=15e6, pools=[{"symbol": "USDC-ETH"}])
        d = m.to_dict()
        assert d["protocol"] == "aerodrome"
        assert d["chain"] == "base"
        assert len(d["pools"]) == 1

    def test_auto_timestamp(self) -> None:
        m = AerodromeMetrics(tvl_usd=0, volume_24h=0)
        assert m.timestamp
        assert "+00:00" in m.timestamp
