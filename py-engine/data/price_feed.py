"""Real-time price feed — multi-source ingestion, caching, and oracle manipulation guards."""

from __future__ import annotations

import json
import time
import urllib.request
from datetime import UTC, datetime
from typing import Any

from data.redis_client import RedisManager

SERVICE_NAME = "py-engine"

# Tokens tracked: ETH, WBTC, major stablecoins, Aave-supported
SUPPORTED_TOKENS: dict[str, str] = {
    "ETH": "ethereum",
    "WBTC": "wrapped-bitcoin",
    "USDC": "usd-coin",
    "USDT": "tether",
    "DAI": "dai",
    "AAVE": "aave",
    "LINK": "chainlink",
    "UNI": "uniswap",
}

# DeFi Llama uses contract addresses on Ethereum mainnet
DEFILLAMA_TOKEN_ADDRESSES: dict[str, str] = {
    "ETH": "coingecko:ethereum",
    "WBTC": "coingecko:wrapped-bitcoin",
    "USDC": "coingecko:usd-coin",
    "USDT": "coingecko:tether",
    "DAI": "coingecko:dai",
    "AAVE": "coingecko:aave",
    "LINK": "coingecko:chainlink",
    "UNI": "coingecko:uniswap",
}

# L2-specific token mappings: token -> {chain, contract, coingecko_id}
L2_TOKEN_MAPPINGS: dict[str, dict[str, str]] = {
    "ARB": {
        "chain": "arbitrum",
        "contract": "0x912CE59144191C1204E64559FE8253a0e49E6548",
        "coingecko_id": "arbitrum",
    },
    "GMX": {
        "chain": "arbitrum",
        "contract": "0xfc5A1A6EB076a2C7aD06eD22C90d7E710E35ad0a",
        "coingecko_id": "gmx",
    },
    "AERO": {
        "chain": "base",
        "contract": "0x940181a94A35A4569E4529A3CDfB74e38FD98631",
        "coingecko_id": "aerodrome-finance",
    },
    "OP": {
        "chain": "optimism",
        "contract": "0x4200000000000000000000000000000000000042",
        "coingecko_id": "optimism",
    },
}

# Defaults
DEFAULT_PRICE_TTL_SECONDS = 30
DEFAULT_DEVIATION_THRESHOLD = 0.02  # 2%
TWAP_WINDOWS = {"5m": 300, "1h": 3600, "24h": 86400}
TWAP_HISTORY_KEY_PREFIX = "price:history:"
PRICE_CACHE_KEY_PREFIX = "price:"


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


class PriceResult:
    """A single token price from a source."""

    __slots__ = ("token", "price_usd", "source", "timestamp")

    def __init__(self, token: str, price_usd: float, source: str, timestamp: str) -> None:
        self.token = token
        self.price_usd = price_usd
        self.source = source
        self.timestamp = timestamp

    def to_dict(self) -> dict[str, Any]:
        """Return dictionary representation."""
        return {
            "token": self.token,
            "price_usd": self.price_usd,
            "source": self.source,
            "timestamp": self.timestamp,
        }


class PriceFeedManager:
    """Multi-source price feed with caching, deviation guards, and TWAP."""

    def __init__(
        self,
        redis: RedisManager,
        *,
        ttl_seconds: int = DEFAULT_PRICE_TTL_SECONDS,
        deviation_threshold: float = DEFAULT_DEVIATION_THRESHOLD,
        tokens: dict[str, str] | None = None,
        fetch_fn: Any = None,
    ) -> None:
        self._redis = redis
        self._ttl = ttl_seconds
        self._deviation_threshold = deviation_threshold
        self._tokens = tokens or SUPPORTED_TOKENS
        self._fetch_fn = fetch_fn or _fetch_url

    # ── L2 token helpers ──────────────────────────────────

    def get_l2_tokens(self, chain: str) -> list[str]:
        """Return tokens available on a given L2 chain.

        Args:
            chain: The L2 chain identifier (e.g. "arbitrum", "base").

        Returns:
            List of token symbols available on the specified chain.
        """
        return [
            token
            for token, info in L2_TOKEN_MAPPINGS.items()
            if info["chain"] == chain.lower()
        ]

    def is_l2_token(self, token: str) -> bool:
        """Check if a token is L2-specific.

        Args:
            token: The token symbol to check (e.g. "ARB", "GMX").

        Returns:
            True if the token is an L2-specific token.
        """
        return token.upper() in L2_TOKEN_MAPPINGS

    # ── L2 price fetching ────────────────────────────────

    def fetch_l2_prices(self) -> dict[str, dict[str, Any]]:
        """Fetch prices for L2-specific tokens via CoinGecko.

        Uses CoinGecko IDs from L2_TOKEN_MAPPINGS to fetch prices for
        tokens like ARB, GMX, AERO, OP. Returns the same normalized format
        as fetch_prices() with an additional 'chain' field.

        Returns:
            Dict of token -> {price_usd, timestamp, sources, chain, deviation}.
        """
        results: dict[str, dict[str, Any]] = {}
        now_epoch = time.time()

        l2_cg_ids = {token: info["coingecko_id"] for token, info in L2_TOKEN_MAPPINGS.items()}
        ids = ",".join(l2_cg_ids.values())
        url = f"https://api.coingecko.com/api/v3/simple/price?ids={ids}&vs_currencies=usd"

        try:
            data = self._fetch_fn(url)
            now = datetime.now(UTC).isoformat()
            id_to_token = {cg_id: token for token, cg_id in l2_cg_ids.items()}

            for cg_id, prices in data.items():
                token = id_to_token.get(cg_id)
                if token and "usd" in prices:
                    price_usd = float(prices["usd"])
                    self._cache_price(token, price_usd, now)
                    self._record_price_history(token, price_usd, now_epoch)
                    results[token] = {
                        "price_usd": price_usd,
                        "timestamp": now,
                        "sources": ["coingecko"],
                        "chain": L2_TOKEN_MAPPINGS[token]["chain"],
                        "deviation": 0.0,
                    }
        except Exception as e:
            _log("price_source_error", f"L2 price fetch failed: {e}", source="coingecko_l2")

        return results

    # ── Source fetchers ──────────────────────────────────

    def _fetch_coingecko(self) -> dict[str, PriceResult]:
        """Fetch prices from CoinGecko API."""
        ids = ",".join(self._tokens.values())
        url = f"https://api.coingecko.com/api/v3/simple/price?ids={ids}&vs_currencies=usd"
        now = datetime.now(UTC).isoformat()

        data = self._fetch_fn(url)
        results: dict[str, PriceResult] = {}
        id_to_token = {v: k for k, v in self._tokens.items()}

        for cg_id, prices in data.items():
            token = id_to_token.get(cg_id)
            if token and "usd" in prices:
                results[token] = PriceResult(
                    token=token,
                    price_usd=float(prices["usd"]),
                    source="coingecko",
                    timestamp=now,
                )
        return results

    def _fetch_defillama(self) -> dict[str, PriceResult]:
        """Fetch prices from DeFi Llama API."""
        coins = ",".join(DEFILLAMA_TOKEN_ADDRESSES.values())
        url = f"https://coins.llama.fi/prices/current/{coins}"
        now = datetime.now(UTC).isoformat()

        data = self._fetch_fn(url)
        coins_data = data.get("coins", {})
        results: dict[str, PriceResult] = {}
        addr_to_token = {v: k for k, v in DEFILLAMA_TOKEN_ADDRESSES.items()}

        for addr, info in coins_data.items():
            token = addr_to_token.get(addr)
            if token and "price" in info:
                results[token] = PriceResult(
                    token=token,
                    price_usd=float(info["price"]),
                    source="defillama",
                    timestamp=now,
                )
        return results

    # ── Oracle manipulation guard ────────────────────────

    def _check_deviation(
        self, token: str, price_a: float, price_b: float
    ) -> tuple[bool, float]:
        """Check if two prices deviate beyond threshold. Returns (ok, deviation_pct)."""
        if price_a == 0 and price_b == 0:
            return True, 0.0
        mid = (price_a + price_b) / 2
        if mid == 0:
            return False, 1.0
        deviation = abs(price_a - price_b) / mid
        ok = deviation <= self._deviation_threshold
        if not ok:
            _log(
                "price_deviation_rejected",
                f"Price deviation for {token} exceeds threshold",
                token=token,
                price_a=price_a,
                price_b=price_b,
                deviation=round(deviation, 6),
                threshold=self._deviation_threshold,
            )
        return ok, deviation

    # ── Caching ──────────────────────────────────────────

    def _cache_price(self, token: str, price: float, timestamp: str) -> None:
        """Cache a validated price in Redis with TTL."""
        data = {"price_usd": price, "timestamp": timestamp, "cached_at": time.time()}
        self._redis.cache_set(f"{PRICE_CACHE_KEY_PREFIX}{token}", data, self._ttl)

    def get_cached_price(self, token: str) -> dict[str, Any] | None:
        """Get cached price. Returns None if missing. Flags stale prices."""
        data = self._redis.cache_get(f"{PRICE_CACHE_KEY_PREFIX}{token}")
        if data is None:
            return None
        # Check staleness — if Redis TTL has expired, cache_get returns None,
        # but we also check the cached_at timestamp for extra safety.
        cached_at = data.get("cached_at", 0)
        age = time.time() - cached_at
        data["stale"] = age > self._ttl
        if data["stale"]:
            _log(
                "stale_price_detected",
                f"Stale price for {token}",
                token=token,
                age_seconds=round(age, 1),
                ttl=self._ttl,
            )
        return data

    # ── TWAP ─────────────────────────────────────────────

    def _record_price_history(self, token: str, price: float, timestamp_epoch: float) -> None:
        """Store price point in Redis sorted set for TWAP calculation."""
        key = f"{TWAP_HISTORY_KEY_PREFIX}{token}"
        member = json.dumps({"price": price, "ts": timestamp_epoch})
        self._redis.client.zadd(key, {member: timestamp_epoch})
        # Prune entries older than 24h
        cutoff = timestamp_epoch - TWAP_WINDOWS["24h"]
        self._redis.client.zremrangebyscore(key, "-inf", cutoff)

    def get_twap(self, token: str, window: str = "5m") -> float | None:
        """Calculate TWAP over a configurable window. Returns None if no data."""
        seconds = TWAP_WINDOWS.get(window)
        if seconds is None:
            raise ValueError(f"Unknown TWAP window: {window}. Valid: {list(TWAP_WINDOWS.keys())}")

        now = time.time()
        key = f"{TWAP_HISTORY_KEY_PREFIX}{token}"
        entries = self._redis.client.zrangebyscore(key, now - seconds, now)

        if not entries:
            return None

        prices = []
        for entry in entries:
            data = json.loads(entry)
            prices.append(data["price"])

        return sum(prices) / len(prices)

    # ── Main fetch cycle ─────────────────────────────────

    def fetch_prices(self) -> dict[str, dict[str, Any]]:
        """Fetch from all sources, validate, cache, and return validated prices.

        Returns a dict of token -> {price_usd, timestamp, sources} for validated prices.
        Prices with >2% deviation between sources are rejected.
        """
        results: dict[str, dict[str, Any]] = {}
        now_epoch = time.time()

        # Fetch from both sources
        source_a: dict[str, PriceResult] = {}
        source_b: dict[str, PriceResult] = {}

        try:
            source_a = self._fetch_coingecko()
        except Exception as e:
            _log("price_source_error", f"CoinGecko fetch failed: {e}", source="coingecko")

        try:
            source_b = self._fetch_defillama()
        except Exception as e:
            _log("price_source_error", f"DeFi Llama fetch failed: {e}", source="defillama")

        all_tokens = set(list(source_a.keys()) + list(source_b.keys()))

        for token in all_tokens:
            price_a_result = source_a.get(token)
            price_b_result = source_b.get(token)

            if price_a_result and price_b_result:
                # Both sources available — check deviation
                ok, deviation = self._check_deviation(
                    token, price_a_result.price_usd, price_b_result.price_usd
                )
                if not ok:
                    continue  # Reject this token's price

                # Use average of both sources
                avg_price = (price_a_result.price_usd + price_b_result.price_usd) / 2
                timestamp = price_a_result.timestamp

                self._cache_price(token, avg_price, timestamp)
                self._record_price_history(token, avg_price, now_epoch)

                results[token] = {
                    "price_usd": avg_price,
                    "timestamp": timestamp,
                    "sources": ["coingecko", "defillama"],
                    "deviation": round(deviation, 6),
                }

            elif price_a_result or price_b_result:
                # Only one source — use it but flag single-source
                single = price_a_result or price_b_result
                assert single is not None

                _log(
                    "single_source_price",
                    f"Only one source available for {token}",
                    token=token,
                    source=single.source,
                )

                self._cache_price(token, single.price_usd, single.timestamp)
                self._record_price_history(token, single.price_usd, now_epoch)

                results[token] = {
                    "price_usd": single.price_usd,
                    "timestamp": single.timestamp,
                    "sources": [single.source],
                    "deviation": 0.0,
                }

        return results
