"""Gas price monitor — track, cache, and analyze Ethereum gas prices."""

from __future__ import annotations

import json
import time
import urllib.request
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from data.redis_client import RedisManager

SERVICE_NAME = "py-engine"

# Redis key prefixes
GAS_CACHE_KEY = "gas:current"
GAS_HISTORY_KEY = "gas:history"
GAS_HOURLY_KEY_PREFIX = "gas:hourly:"

# Defaults
DEFAULT_GAS_TTL_SECONDS = 12  # ~1 block
DEFAULT_SPIKE_MULTIPLIER = 3.0
DEFAULT_ALERT_THRESHOLD_GWEI = 100.0
ROLLING_WINDOW_SECONDS = 86400  # 24h


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


class GasPrices:
    """Gas price snapshot with fast/standard/slow tiers in gwei."""

    __slots__ = ("fast", "standard", "slow", "timestamp")

    def __init__(self, fast: float, standard: float, slow: float, timestamp: str) -> None:
        self.fast = fast
        self.standard = standard
        self.slow = slow
        self.timestamp = timestamp

    def to_dict(self) -> dict[str, Any]:
        """Return dictionary representation."""
        return {
            "fast": self.fast,
            "standard": self.standard,
            "slow": self.slow,
            "timestamp": self.timestamp,
        }

    def get_tier(self, priority: str) -> float:
        """Get gas price for a priority tier."""
        tiers = {"fast": self.fast, "standard": self.standard, "slow": self.slow}
        if priority not in tiers:
            raise ValueError(f"Unknown priority: {priority}. Valid: {list(tiers.keys())}")
        return tiers[priority]


class GasMonitor:
    """Monitors gas prices, maintains rolling averages, and detects spikes."""

    def __init__(
        self,
        redis: RedisManager,
        *,
        ttl_seconds: int = DEFAULT_GAS_TTL_SECONDS,
        spike_multiplier: float = DEFAULT_SPIKE_MULTIPLIER,
        alert_threshold_gwei: float = DEFAULT_ALERT_THRESHOLD_GWEI,
        fetch_fn: Any = None,
    ) -> None:
        self._redis = redis
        self._ttl = ttl_seconds
        self._spike_multiplier = spike_multiplier
        self._alert_threshold_gwei = alert_threshold_gwei
        self._fetch_fn = fetch_fn or _fetch_url

    # ── Source fetching ──────────────────────────────────

    def _fetch_gas_prices(self) -> GasPrices:
        """Fetch current gas prices from external source."""
        url = "https://api.etherscan.io/api?module=gastracker&action=gasoracle"
        now = datetime.now(UTC).isoformat()

        data = self._fetch_fn(url)
        result = data.get("result", {})

        return GasPrices(
            fast=float(result.get("FastGasPrice", 0)),
            standard=float(result.get("ProposeGasPrice", 0)),
            slow=float(result.get("SafeGasPrice", 0)),
            timestamp=now,
        )

    # ── Caching ──────────────────────────────────────────

    def _cache_gas_prices(self, prices: GasPrices) -> None:
        """Cache current gas prices in Redis."""
        self._redis.cache_set(GAS_CACHE_KEY, prices.to_dict(), self._ttl)

    def get_cached_prices(self) -> GasPrices | None:
        """Get cached gas prices. Returns None if expired/missing."""
        data = self._redis.cache_get(GAS_CACHE_KEY)
        if data is None:
            return None
        return GasPrices(
            fast=data["fast"],
            standard=data["standard"],
            slow=data["slow"],
            timestamp=data["timestamp"],
        )

    # ── Rolling average ──────────────────────────────────

    def _record_history(self, standard_gwei: float, timestamp_epoch: float) -> None:
        """Store gas price in sorted set for rolling average and pattern analysis."""
        member = json.dumps({"gwei": standard_gwei, "ts": timestamp_epoch})
        self._redis.client.zadd(GAS_HISTORY_KEY, {member: timestamp_epoch})
        # Prune entries older than 24h
        cutoff = timestamp_epoch - ROLLING_WINDOW_SECONDS
        self._redis.client.zremrangebyscore(GAS_HISTORY_KEY, "-inf", cutoff)

        # Also record in hourly bucket for pattern analysis
        hour = datetime.fromtimestamp(timestamp_epoch, tz=UTC).hour
        hourly_key = f"{GAS_HOURLY_KEY_PREFIX}{hour}"
        self._redis.client.zadd(hourly_key, {member: timestamp_epoch})

    def get_rolling_average(self, window_hours: int = 24) -> Decimal | None:
        """Calculate rolling average gas price over a time window.

        Returns None if no data available.
        """
        now = time.time()
        window_seconds = window_hours * 3600
        entries = self._redis.client.zrangebyscore(
            GAS_HISTORY_KEY, now - window_seconds, now
        )

        if not entries:
            return None

        total = Decimal(0)
        for entry in entries:
            data = json.loads(entry)
            total += Decimal(str(data["gwei"]))

        return total / len(entries)

    def get_hourly_pattern(self, hour: int) -> Decimal | None:
        """Get average gas price for a specific hour of day (0-23).

        Useful for time-of-day pattern analysis.
        """
        if not 0 <= hour <= 23:
            raise ValueError(f"Hour must be 0-23, got {hour}")

        hourly_key = f"{GAS_HOURLY_KEY_PREFIX}{hour}"
        entries = self._redis.client.zrangebyscore(hourly_key, "-inf", "+inf")

        if not entries:
            return None

        total = Decimal(0)
        for entry in entries:
            data = json.loads(entry)
            total += Decimal(str(data["gwei"]))

        return total / len(entries)

    # ── Gas cost estimation ──────────────────────────────

    def estimate_gas_cost(
        self, gas_units: int, priority: str = "standard"
    ) -> Decimal | None:
        """Estimate gas cost in ETH for a given number of gas units.

        Returns None if no cached gas prices available.
        """
        prices = self.get_cached_prices()
        if prices is None:
            return None

        gwei = Decimal(str(prices.get_tier(priority)))
        return gwei * gas_units / Decimal("1e9")

    # ── Spike detection ──────────────────────────────────

    def is_spike(self, multiplier: float | None = None) -> bool | None:
        """Check if current gas is a spike relative to 24h average.

        Returns None if insufficient data. Returns True if
        current standard price > multiplier * rolling average.
        """
        mult = multiplier or self._spike_multiplier
        prices = self.get_cached_prices()
        if prices is None:
            return None

        avg = self.get_rolling_average(window_hours=24)
        if avg is None or avg == 0:
            return None

        current = Decimal(str(prices.standard))
        return current > avg * Decimal(str(mult))

    # ── Alert check ──────────────────────────────────────

    def _check_alert(self, prices: GasPrices) -> None:
        """Log alert if gas exceeds threshold."""
        if prices.standard > self._alert_threshold_gwei:
            _log(
                "gas_alert",
                f"Gas price {prices.standard} gwei exceeds threshold "
                f"{self._alert_threshold_gwei} gwei",
                standard_gwei=prices.standard,
                threshold_gwei=self._alert_threshold_gwei,
                fast_gwei=prices.fast,
                slow_gwei=prices.slow,
            )

    # ── Main update cycle ────────────────────────────────

    def update(self) -> GasPrices | None:
        """Fetch, cache, record, and check gas prices. Returns prices or None on failure."""
        try:
            prices = self._fetch_gas_prices()
        except Exception as e:
            _log("gas_fetch_error", f"Failed to fetch gas prices: {e}")
            return None

        now_epoch = time.time()
        self._cache_gas_prices(prices)
        self._record_history(prices.standard, now_epoch)
        self._check_alert(prices)

        return prices
