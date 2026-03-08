"""Price feed — multi-source ingestion, caching, staleness, cross-source validation."""

from __future__ import annotations

import json
import os
import time
import urllib.request
from datetime import UTC, datetime
from typing import Any

from data.redis_client import RedisManager
from strategies.base import TokenPrice

SERVICE_NAME = "py-engine"

# Tokens tracked by Alchemy (symbol used directly in API call)
ALCHEMY_SYMBOLS: list[str] = ["USDC", "USDT", "DAI", "AERO"]

# DeFi Llama fallback addresses
DEFILLAMA_TOKEN_ADDRESSES: dict[str, str] = {
    "USDC": "coingecko:usd-coin",
    "USDT": "coingecko:tether",
    "DAI": "coingecko:dai",
    "AERO": "coingecko:aerodrome-finance",
}

# L2-specific token metadata (kept for get_l2_tokens / is_l2_token helpers)
L2_TOKEN_MAPPINGS: dict[str, dict[str, str]] = {
    "AERO": {
        "chain": "base",
        "contract": "0x940181a94A35A4569E4529A3CDfB74e38FD98631",
    },
}

# Defaults
DEFAULT_PRICE_CACHE_TTL_SECONDS = 30
DEFAULT_STALENESS_THRESHOLD_SECONDS = 60
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
    """Multi-source price feed with caching, staleness detection, and cross-source validation."""

    def __init__(
        self,
        redis: RedisManager,
        *,
        ttl_seconds: int | None = None,
        staleness_threshold_seconds: int | None = None,
        deviation_threshold: float | None = None,
        fetch_fn: Any = None,
        alchemy_api_key: str | None = None,
        fetch_interval_seconds: int | None = None,
    ) -> None:
        self._redis = redis
        self._ttl = ttl_seconds or int(
            os.environ.get("PRICE_CACHE_TTL", str(DEFAULT_PRICE_CACHE_TTL_SECONDS)),
        )
        self._staleness_threshold = staleness_threshold_seconds or int(
            os.environ.get(
                "PRICE_STALENESS_THRESHOLD_SECONDS",
                str(DEFAULT_STALENESS_THRESHOLD_SECONDS),
            ),
        )
        self._deviation_threshold = (
            deviation_threshold
            if deviation_threshold is not None
            else DEFAULT_DEVIATION_THRESHOLD
        )
        self._fetch_fn = fetch_fn or _fetch_url
        self._alchemy_api_key = (
            alchemy_api_key
            or os.environ.get("ALCHEMY_API_KEY")
            or os.environ.get("ALCHEMY_SEPOLIA_API_KEY")
        )
        self._fetch_interval = fetch_interval_seconds or int(
            os.environ.get("PRICE_FETCH_INTERVAL_SECONDS", "30"),
        )
        self._last_fetch_time: float = 0.0

    # ── L2 token helpers ──────────────────────────────────

    def get_l2_tokens(self, chain: str) -> list[str]:
        """Return tokens available on a given L2 chain.

        Args:
            chain: The L2 chain identifier (e.g. "base").

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
            token: The token symbol to check (e.g. "AERO").

        Returns:
            True if the token is an L2-specific token.
        """
        return token.upper() in L2_TOKEN_MAPPINGS

    # ── Source fetchers ──────────────────────────────────

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

    def _fetch_alchemy(self) -> dict[str, PriceResult]:
        """Fetch prices from Alchemy Token Prices API."""
        if not self._alchemy_api_key:
            raise ValueError("ALCHEMY_API_KEY is required for Alchemy price fetches")

        symbols = "&".join(f"symbols={s}" for s in ALCHEMY_SYMBOLS)
        url = f"https://api.g.alchemy.com/prices/v1/{self._alchemy_api_key}/tokens/by-symbol?{symbols}"
        now = datetime.now(UTC).isoformat()

        data = self._fetch_fn(url)
        results: dict[str, PriceResult] = {}

        for entry in data.get("data", []):
            symbol = entry.get("symbol", "").upper()
            prices = entry.get("prices", [])
            if symbol in ALCHEMY_SYMBOLS and prices:
                usd_price = next(
                    (p for p in prices if p.get("currency") == "usd"), None
                )
                if usd_price:
                    results[symbol] = PriceResult(
                        token=symbol,
                        price_usd=float(usd_price["value"]),
                        source="alchemy",
                        timestamp=now,
                    )

        return results

    # ── Caching ──────────────────────────────────────────

    def _cache_price(self, token: str, price: float, timestamp: str) -> None:
        """Cache a validated price in Redis with TTL."""
        data = {"price_usd": price, "timestamp": timestamp, "cached_at": time.time()}
        self._redis.cache_set(f"{PRICE_CACHE_KEY_PREFIX}{token}", data, self._ttl)

    def get_cached_price(self, token: str) -> dict[str, Any] | None:
        """Get cached price. Returns None if missing. Flags stale prices.

        Staleness is determined by PRICE_STALENESS_THRESHOLD_SECONDS (default 60s),
        which is separate from the cache TTL. A price can be cached but stale.
        """
        data = self._redis.cache_get(f"{PRICE_CACHE_KEY_PREFIX}{token}")
        if data is None:
            return None
        cached_at = data.get("cached_at", 0)
        age = time.time() - cached_at
        data["stale"] = age > self._staleness_threshold
        if data["stale"]:
            _log(
                "stale_price_detected",
                f"Stale price for {token}",
                token=token,
                age_seconds=round(age, 1),
                staleness_threshold=self._staleness_threshold,
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

    # ── Cross-source validation ────────────────────────

    def _validate_cross_source(
        self,
        alchemy_results: dict[str, PriceResult],
        defillama_results: dict[str, PriceResult],
    ) -> dict[str, PriceResult]:
        """Validate prices across sources, rejecting tokens with >2% deviation.

        When both sources provide a price for the same token, deviation is checked.
        If deviation exceeds the threshold, the token is rejected from results.
        Tokens available from only one source are accepted as-is.

        Returns:
            Validated price results (primary source preferred).
        """
        validated: dict[str, PriceResult] = {}

        all_tokens = set(alchemy_results) | set(defillama_results)
        for token in all_tokens:
            alchemy_pr = alchemy_results.get(token)
            defillama_pr = defillama_results.get(token)

            if alchemy_pr and defillama_pr:
                # Both sources — check deviation
                mid = (alchemy_pr.price_usd + defillama_pr.price_usd) / 2
                if mid == 0:
                    continue
                deviation = abs(alchemy_pr.price_usd - defillama_pr.price_usd) / mid
                if deviation > self._deviation_threshold:
                    _log(
                        "price_deviation_rejected",
                        f"Price deviation for {token} exceeds threshold",
                        token=token,
                        alchemy_price=alchemy_pr.price_usd,
                        defillama_price=defillama_pr.price_usd,
                        deviation=round(deviation, 6),
                        threshold=self._deviation_threshold,
                    )
                    continue
                # Use primary (Alchemy) when validated
                validated[token] = alchemy_pr
            elif alchemy_pr:
                validated[token] = alchemy_pr
            elif defillama_pr:
                validated[token] = defillama_pr

        return validated

    # ── Staleness check ──────────────────────────────────

    def is_any_stale(self) -> bool:
        """Check if any tracked token price is stale.

        Returns:
            True if any token has a stale or missing cached price.
        """
        for symbol in ALCHEMY_SYMBOLS:
            cached = self.get_cached_price(symbol)
            if cached is None or cached.get("stale", False):
                return True
        return False

    # ── TokenPrice conversion ────────────────────────────

    def get_token_prices(self) -> list[TokenPrice]:
        """Fetch prices and return as TokenPrice dataclasses for strategy consumption.

        Returns:
            List of TokenPrice dataclasses with token, price, source, and timestamp.
        """
        price_data = self.fetch_prices()
        result: list[TokenPrice] = []
        for token, data in price_data.items():
            source = data["sources"][0] if data["sources"] else "unknown"
            ts_str = data["timestamp"]
            try:
                ts = datetime.fromisoformat(ts_str)
            except (ValueError, TypeError):
                ts = datetime.now(UTC)
            result.append(
                TokenPrice(token=token, price=data["price_usd"], source=source, timestamp=ts),
            )
        return result

    # ── Main fetch cycle ─────────────────────────────────

    def fetch_prices(self) -> dict[str, dict[str, Any]]:
        """Fetch prices: Alchemy primary, DefiLlama fallback, with cross-source validation.

        Fetches from both sources when possible. If both return a price for the
        same token, validates that deviation is <2%. Rejected tokens are excluded.

        Returns:
            Dict of token -> {price_usd, timestamp, sources}.
        """
        now_epoch = time.time()

        # Cache-freshness check — if we fetched recently, return cached prices
        if now_epoch - self._last_fetch_time < self._fetch_interval:
            cached = self._get_all_cached_prices()
            if cached:
                return cached

        alchemy_results: dict[str, PriceResult] = {}
        defillama_results: dict[str, PriceResult] = {}

        # Primary: Alchemy
        if self._alchemy_api_key:
            try:
                alchemy_results = self._fetch_alchemy()
            except Exception as e:
                _log("price_source_error", f"Alchemy fetch failed: {e}", source="alchemy")

        # Always try DefiLlama for cross-validation (or fallback if Alchemy failed)
        try:
            defillama_results = self._fetch_defillama()
        except Exception as e:
            _log("price_source_error", f"DefiLlama fetch failed: {e}", source="defillama")

        # Cross-source validation
        validated = self._validate_cross_source(alchemy_results, defillama_results)

        # Cache and return
        results: dict[str, dict[str, Any]] = {}
        for token, pr in validated.items():
            self._cache_price(token, pr.price_usd, pr.timestamp)
            self._record_price_history(token, pr.price_usd, now_epoch)
            sources = [pr.source]
            # Note if cross-validated
            other = defillama_results if pr.source == "alchemy" else alchemy_results
            if token in other:
                sources.append(other[token].source)
            results[token] = {
                "price_usd": pr.price_usd,
                "timestamp": pr.timestamp,
                "sources": sources,
            }

        if results:
            self._last_fetch_time = now_epoch

        return results

    def _get_all_cached_prices(self) -> dict[str, dict[str, Any]]:
        """Return all cached prices for known tokens."""
        results: dict[str, dict[str, Any]] = {}
        for symbol in ALCHEMY_SYMBOLS:
            cached = self.get_cached_price(symbol)
            if cached and not cached.get("stale", False):
                results[symbol] = {
                    "price_usd": cached["price_usd"],
                    "timestamp": cached["timestamp"],
                    "sources": ["cached"],
                }
        return results
