"""DeFi protocol metrics collector — Aave, Uniswap V3, Lido, TVL."""

from __future__ import annotations

import json
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from data.redis_client import RedisManager

SERVICE_NAME = "py-engine"

# TTLs
RATE_TTL_SECONDS = 300  # 5 minutes for APY/utilization data
TVL_TTL_SECONDS = 3600  # 1 hour for TVL data

# Redis key prefixes
METRICS_KEY_PREFIX = "metrics:"


def _log(event: str, message: str, **kwargs: Any) -> None:
    entry = {
        "timestamp": datetime.now(UTC).isoformat(),
        "service": SERVICE_NAME,
        "event": event,
        "message": message,
        **kwargs,
    }
    print(json.dumps(entry), flush=True)


def _fetch_url(url: str, timeout: int = 10) -> Any:
    """Fetch JSON from a URL using stdlib."""
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


# ── Dataclasses ──────────────────────────────────────


@dataclass
class AaveMarketMetrics:
    """Metrics for a single Aave V3 market."""

    symbol: str
    utilization_rate: float
    supply_apy: float
    borrow_apy: float
    available_liquidity: float
    timestamp: str = ""

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = datetime.now(UTC).isoformat()


@dataclass
class AaveMetrics:
    """Aggregated Aave V3 metrics across all markets."""

    markets: list[AaveMarketMetrics] = field(default_factory=list)
    protocol: str = "aave"
    timestamp: str = ""

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = datetime.now(UTC).isoformat()

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol": self.protocol,
            "markets": [asdict(m) for m in self.markets],
            "timestamp": self.timestamp,
        }


@dataclass
class UniswapPoolMetrics:
    """Metrics for a single Uniswap V3 pool."""

    pair: str
    reserves_token0: float
    reserves_token1: float
    current_tick: int
    fee_tier: int
    volume_24h: float
    timestamp: str = ""

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = datetime.now(UTC).isoformat()


@dataclass
class UniswapMetrics:
    """Aggregated Uniswap V3 pool metrics."""

    pools: list[UniswapPoolMetrics] = field(default_factory=list)
    protocol: str = "uniswap_v3"
    timestamp: str = ""

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = datetime.now(UTC).isoformat()

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol": self.protocol,
            "pools": [asdict(p) for p in self.pools],
            "timestamp": self.timestamp,
        }


@dataclass
class LidoMetrics:
    """Lido staking metrics."""

    steth_apy: float
    queue_status: str  # "open", "closed", "paused"
    steth_total_supply: float = 0.0
    protocol: str = "lido"
    timestamp: str = ""

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = datetime.now(UTC).isoformat()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ProtocolTVL:
    """Protocol total value locked."""

    protocol: str
    tvl_usd: float
    chain: str = "ethereum"
    timestamp: str = ""

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = datetime.now(UTC).isoformat()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ── Collector ────────────────────────────────────────


# DeFi Llama protocol slugs
DEFILLAMA_PROTOCOLS = {
    "aave": "aave",
    "uniswap_v3": "uniswap",
    "lido": "lido",
}

# Target Uniswap pools (pair label → pool address placeholder)
TARGET_UNISWAP_POOLS = [
    "ETH/USDC",
    "ETH/USDT",
    "WBTC/ETH",
]


class DeFiMetricsCollector:
    """Collects, caches, and serves DeFi protocol metrics."""

    def __init__(
        self,
        redis: RedisManager,
        *,
        rate_ttl: int = RATE_TTL_SECONDS,
        tvl_ttl: int = TVL_TTL_SECONDS,
        fetch_fn: Any = None,
    ) -> None:
        self._redis = redis
        self._rate_ttl = rate_ttl
        self._tvl_ttl = tvl_ttl
        self._fetch_fn = fetch_fn or _fetch_url

    # ── Cache helpers ────────────────────────────────────

    def _cache_key(self, protocol: str, metric_type: str) -> str:
        return f"{METRICS_KEY_PREFIX}{protocol}:{metric_type}"

    def _cache_set(self, protocol: str, metric_type: str, data: dict[str, Any], ttl: int) -> None:
        key = self._cache_key(protocol, metric_type)
        self._redis.cache_set(key, data, ttl)

    def _cache_get(self, protocol: str, metric_type: str) -> dict[str, Any] | None:
        key = self._cache_key(protocol, metric_type)
        return self._redis.cache_get(key)

    # ── Aave ─────────────────────────────────────────────

    def fetch_aave_metrics(self) -> AaveMetrics | None:
        """Fetch Aave V3 market metrics from DeFi Llama yields API."""
        try:
            data = self._fetch_fn("https://yields.llama.fi/pools")
            pools = data.get("data", [])

            markets: list[AaveMarketMetrics] = []
            for pool in pools:
                if pool.get("project") != "aave-v3" or pool.get("chain") != "Ethereum":
                    continue
                markets.append(
                    AaveMarketMetrics(
                        symbol=pool.get("symbol", ""),
                        utilization_rate=float(pool.get("utilization", 0) or 0),
                        supply_apy=float(pool.get("apy", 0) or 0),
                        borrow_apy=float(pool.get("apyBorrow", 0) or 0),
                        available_liquidity=float(pool.get("tvlUsd", 0) or 0),
                    )
                )

            result = AaveMetrics(markets=markets)
            self._cache_set("aave", "rates", result.to_dict(), self._rate_ttl)
            return result

        except Exception as e:
            _log("metrics_fetch_error", f"Aave metrics fetch failed: {e}", protocol="aave")
            return self._get_cached_or_none("aave", "rates", AaveMetrics)

    # ── Uniswap V3 ───────────────────────────────────────

    def fetch_uniswap_metrics(self) -> UniswapMetrics | None:
        """Fetch Uniswap V3 pool metrics."""
        try:
            data = self._fetch_fn("https://yields.llama.fi/pools")
            pools_data = data.get("data", [])

            pools: list[UniswapPoolMetrics] = []
            for pool in pools_data:
                if pool.get("project") != "uniswap-v3" or pool.get("chain") != "Ethereum":
                    continue
                symbol = pool.get("symbol", "")
                if not any(target.replace("/", "-") in symbol for target in TARGET_UNISWAP_POOLS):
                    continue
                pools.append(
                    UniswapPoolMetrics(
                        pair=symbol,
                        reserves_token0=float(pool.get("tvlUsd", 0) or 0) / 2,
                        reserves_token1=float(pool.get("tvlUsd", 0) or 0) / 2,
                        current_tick=0,  # Not available from yields API
                        fee_tier=int(pool.get("feeTier", 0) or 0),
                        volume_24h=float(pool.get("volumeUsd1d", 0) or 0),
                    )
                )

            result = UniswapMetrics(pools=pools)
            self._cache_set("uniswap_v3", "pools", result.to_dict(), self._rate_ttl)
            return result

        except Exception as e:
            _log(
                "metrics_fetch_error",
                f"Uniswap metrics fetch failed: {e}",
                protocol="uniswap_v3",
            )
            return self._get_cached_or_none("uniswap_v3", "pools", UniswapMetrics)

    # ── Lido ─────────────────────────────────────────────

    def fetch_lido_metrics(self) -> LidoMetrics | None:
        """Fetch Lido staking metrics."""
        try:
            data = self._fetch_fn("https://eth-api.lido.fi/v1/protocol/steth/apr/sma")
            sma_data = data.get("data", {})
            apy = float(sma_data.get("smaApr", 0) or 0)

            result = LidoMetrics(
                steth_apy=apy,
                queue_status="open",  # Simplified; real impl queries withdrawal queue
            )
            self._cache_set("lido", "staking", result.to_dict(), self._rate_ttl)
            return result

        except Exception as e:
            _log("metrics_fetch_error", f"Lido metrics fetch failed: {e}", protocol="lido")
            return self._get_cached_or_none("lido", "staking", LidoMetrics)

    # ── TVL ───────────────────────────────────────────────

    def fetch_tvl(self, protocol: str) -> ProtocolTVL | None:
        """Fetch protocol TVL from DeFi Llama."""
        slug = DEFILLAMA_PROTOCOLS.get(protocol, protocol)
        try:
            data = self._fetch_fn(f"https://api.llama.fi/tvl/{slug}")
            tvl_usd = float(data) if isinstance(data, (int, float)) else 0.0

            result = ProtocolTVL(protocol=protocol, tvl_usd=tvl_usd)
            self._cache_set(protocol, "tvl", result.to_dict(), self._tvl_ttl)
            return result

        except Exception as e:
            _log(
                "metrics_fetch_error",
                f"TVL fetch failed for {protocol}: {e}",
                protocol=protocol,
            )
            return self._get_cached_tvl_or_none(protocol)

    def fetch_all_tvl(self) -> dict[str, ProtocolTVL]:
        """Fetch TVL for all tracked protocols."""
        results: dict[str, ProtocolTVL] = {}
        for protocol in DEFILLAMA_PROTOCOLS:
            tvl = self.fetch_tvl(protocol)
            if tvl is not None:
                results[protocol] = tvl
        return results

    # ── Unified interface ────────────────────────────────

    def get_metrics(self, protocol: str) -> dict[str, Any] | None:
        """Unified interface: fetch metrics for any supported protocol.

        Returns normalized dict or None if unavailable.
        """
        fetchers: dict[str, Any] = {
            "aave": self.fetch_aave_metrics,
            "uniswap_v3": self.fetch_uniswap_metrics,
            "lido": self.fetch_lido_metrics,
        }

        fetcher = fetchers.get(protocol)
        if fetcher is None:
            _log("metrics_unknown_protocol", f"Unknown protocol: {protocol}", protocol=protocol)
            return None

        result = fetcher()
        if result is None:
            return None
        return result.to_dict()

    # ── Graceful degradation helpers ─────────────────────

    def _get_cached_or_none(
        self, protocol: str, metric_type: str, cls: type
    ) -> Any | None:
        """Return cached metrics on fetch failure, with alert."""
        cached = self._cache_get(protocol, metric_type)
        if cached is not None:
            _log(
                "metrics_using_cached",
                f"Using cached {metric_type} for {protocol} after fetch failure",
                protocol=protocol,
                metric_type=metric_type,
            )
            # Reconstruct the dataclass from cached dict
            if cls is AaveMetrics:
                markets = [AaveMarketMetrics(**m) for m in cached.get("markets", [])]
                return AaveMetrics(markets=markets, timestamp=cached.get("timestamp", ""))
            if cls is UniswapMetrics:
                pools = [UniswapPoolMetrics(**p) for p in cached.get("pools", [])]
                return UniswapMetrics(pools=pools, timestamp=cached.get("timestamp", ""))
            if cls is LidoMetrics:
                return LidoMetrics(
                    steth_apy=cached.get("steth_apy", 0),
                    queue_status=cached.get("queue_status", "unknown"),
                    steth_total_supply=cached.get("steth_total_supply", 0),
                    timestamp=cached.get("timestamp", ""),
                )
        return None

    def _get_cached_tvl_or_none(self, protocol: str) -> ProtocolTVL | None:
        """Return cached TVL on fetch failure."""
        cached = self._cache_get(protocol, "tvl")
        if cached is not None:
            _log(
                "metrics_using_cached",
                f"Using cached TVL for {protocol} after fetch failure",
                protocol=protocol,
            )
            return ProtocolTVL(
                protocol=cached.get("protocol", protocol),
                tvl_usd=cached.get("tvl_usd", 0),
                chain=cached.get("chain", "ethereum"),
                timestamp=cached.get("timestamp", ""),
            )
        return None
