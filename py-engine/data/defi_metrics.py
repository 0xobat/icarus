"""DeFi protocol metrics collector — Aave V3, Aerodrome, TVL."""

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
        """Return dictionary representation."""
        return {
            "protocol": self.protocol,
            "markets": [asdict(m) for m in self.markets],
            "timestamp": self.timestamp,
        }



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
        """Return dictionary representation."""
        return asdict(self)



@dataclass
class AerodromeMetrics:
    """Aerodrome DEX metrics on Base."""

    tvl_usd: float
    volume_24h: float
    pools: list[dict[str, Any]] = field(default_factory=list)
    reward_emissions_daily: float = 0.0
    liquidity_depth: list[dict[str, Any]] = field(default_factory=list)
    protocol: str = "aerodrome"
    chain: str = "base"
    timestamp: str = ""

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = datetime.now(UTC).isoformat()

    def to_dict(self) -> dict[str, Any]:
        """Return dictionary representation."""
        return asdict(self)


# ── Collector ────────────────────────────────────────


# DeFi Llama protocol slugs
DEFILLAMA_PROTOCOLS = {
    "aave": "aave",
    "aerodrome": "aerodrome",
}

# L2 protocol chain mappings
L2_PROTOCOL_CHAINS: dict[str, str] = {
    "aerodrome": "base",
}


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

    # ── Aerodrome (Base) ─────────────────────────────────

    def collect_aerodrome_metrics(self) -> AerodromeMetrics | None:
        """Collect Aerodrome DEX metrics from DeFi Llama.

        Returns:
            AerodromeMetrics with TVL, volume, and pool data, or None on failure.
        """
        try:
            # Fetch TVL
            tvl_data = self._fetch_fn("https://api.llama.fi/tvl/aerodrome")
            tvl_usd = float(tvl_data) if isinstance(tvl_data, (int, float)) else 0.0

            # Fetch pool data from yields API
            pools_data = self._fetch_fn("https://yields.llama.fi/pools")
            all_pools = pools_data.get("data", [])

            volume_24h = 0.0
            pool_summaries: list[dict[str, Any]] = []
            total_reward_emissions = 0.0
            liquidity_depth: list[dict[str, Any]] = []
            for pool in all_pools:
                if pool.get("project") == "aerodrome" and pool.get("chain") == "Base":
                    vol = float(pool.get("volumeUsd1d", 0) or 0)
                    tvl = float(pool.get("tvlUsd", 0) or 0)
                    apy = float(pool.get("apy", 0) or 0)
                    symbol = pool.get("symbol", "")

                    volume_24h += vol

                    # Estimate daily reward emissions from APY and TVL
                    daily_rewards = (apy / 100.0 / 365.0) * tvl if apy and tvl else 0.0
                    total_reward_emissions += daily_rewards

                    pool_summaries.append({
                        "symbol": symbol,
                        "tvl_usd": tvl,
                        "volume_24h": vol,
                        "apy": apy,
                        "reward_apr": float(pool.get("apyReward", 0) or 0),
                    })
                    liquidity_depth.append({
                        "pair": symbol,
                        "tvl_usd": tvl,
                        "volume_tvl_ratio": vol / tvl if tvl > 0 else 0.0,
                    })

            result = AerodromeMetrics(
                tvl_usd=tvl_usd,
                volume_24h=volume_24h,
                pools=pool_summaries,
                reward_emissions_daily=total_reward_emissions,
                liquidity_depth=liquidity_depth,
            )
            self._cache_set("aerodrome", "metrics", result.to_dict(), self._rate_ttl)
            return result

        except Exception as e:
            _log(
                "metrics_fetch_error",
                f"Aerodrome metrics fetch failed: {e}",
                protocol="aerodrome",
            )
            cached = self._cache_get("aerodrome", "metrics")
            if cached is not None:
                _log(
                    "metrics_using_cached",
                    "Using cached metrics for aerodrome after fetch failure",
                    protocol="aerodrome",
                )
                return AerodromeMetrics(
                    tvl_usd=cached.get("tvl_usd", 0),
                    volume_24h=cached.get("volume_24h", 0),
                    pools=cached.get("pools", []),
                    reward_emissions_daily=cached.get("reward_emissions_daily", 0),
                    liquidity_depth=cached.get("liquidity_depth", []),
                    timestamp=cached.get("timestamp", ""),
                )
            return None

    # ── L2 protocol metrics unified interface ────────────

    def get_l2_protocol_metrics(
        self, protocol: str, chain: str
    ) -> dict[str, Any] | None:
        """Fetch metrics for an L2 protocol on a given chain.

        Args:
            protocol: Protocol identifier (e.g. "gmx", "aerodrome").
            chain: Chain identifier (e.g. "base").

        Returns:
            Normalized dict of protocol metrics, or None if unavailable.
        """
        expected_chain = L2_PROTOCOL_CHAINS.get(protocol)
        if expected_chain is None:
            _log(
                "l2_metrics_unknown_protocol",
                f"Unknown L2 protocol: {protocol}",
                protocol=protocol,
                chain=chain,
            )
            return None

        if expected_chain != chain.lower():
            _log(
                "l2_metrics_chain_mismatch",
                f"Protocol {protocol} is on {expected_chain}, not {chain}",
                protocol=protocol,
                expected_chain=expected_chain,
                requested_chain=chain,
            )
            return None

        fetchers: dict[str, Any] = {
            "aerodrome": self.collect_aerodrome_metrics,
        }

        fetcher = fetchers.get(protocol)
        if fetcher is None:
            return None

        result = fetcher()
        if result is None:
            return None
        return result.to_dict()

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
            "aerodrome": self.collect_aerodrome_metrics,
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
