"""DefiLlama yields adapter — public APY/TVL feed for Base + Solana pools.

DefiLlama exposes two relevant endpoints, both auth-free:

  GET https://yields.llama.fi/pools
    → snapshot of every tracked pool (one row per pool, current values).
    Used by `fetch_live`.

  GET https://yields.llama.fi/chart/{pool_id}
    → time series of {timestamp, tvlUsd, apy} for one pool.
    Used by `fetch_historical` — we fan out across the pools matching
    `chain`, then merge by timestamp so callers get one MarketSnapshot
    per unique timestamp across pools.

Chain mapping: DefiLlama uses TitleCase ("Base", "Solana"); our `Chain`
literal is lowercase. `_CHAIN_TO_LLAMA` is the single source of truth.

Pool ID scheme: we synthesise `<chain>:<project>:<symbol>` (lowercased,
non-alnum → `_`) so a backtest reading a CSV and a live engine reading
this adapter address the same pool with the same string. The original
DefiLlama UUID is preserved in `metadata["llama_pool_uuid"]` for debug.

Rate-limit behaviour: DefiLlama is generous but not infinite. We don't
implement a token-bucket here — the backtest engine is expected to cache
results in Postgres (W4). For now, callers must be sane about iteration.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import datetime
from decimal import Decimal
from typing import Any

import httpx
import structlog
from pydantic import BaseModel, ConfigDict, Field

from icarus.types import MarketSnapshot
from icarus.types.market import Chain, PoolState

_logger = structlog.get_logger(service="data_adapters.defillama")

_BASE_URL = "https://yields.llama.fi"
_DEFAULT_TIMEOUT = 30.0

# DefiLlama uses TitleCase chain names. Keep this table small and explicit;
# adding a chain means adding it to the `Chain` Literal in icarus.types.market
# first, which forces the type checker to flag every consumer that has not
# adopted it.
_CHAIN_TO_LLAMA: dict[Chain, str] = {
    "base": "Base",
    "solana": "Solana",
}

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _pool_id(chain: Chain, project: str, symbol: str) -> str:
    """Deterministic chain-qualified pool ID.

    `base:aave-v3:USDC` → `base:aave_v3:usdc`. Stable across runs so
    backtest-vs-live diffs are real diffs, not casing artefacts.
    """

    def _slug(s: str) -> str:
        return _NON_ALNUM.sub("_", s.lower()).strip("_")

    return f"{chain}:{_slug(project)}:{_slug(symbol)}"


class _LlamaPoolRow(BaseModel):
    """One row from `/pools`. Subset of fields we actually consume.

    `model_config` allows extra fields because DefiLlama adds new metrics
    (reward APY breakdowns, IL risk, predictions) over time and we don't
    want a new field to break the adapter.
    """

    model_config = ConfigDict(extra="ignore")

    chain: str
    project: str
    symbol: str
    pool: str = Field(description="DefiLlama UUID")
    tvl_usd: Decimal = Field(alias="tvlUsd")
    apy: Decimal | None = None
    apy_base: Decimal | None = Field(default=None, alias="apyBase")
    apy_reward: Decimal | None = Field(default=None, alias="apyReward")


class _LlamaChartPoint(BaseModel):
    """One point from `/chart/{pool}`."""

    model_config = ConfigDict(extra="ignore")

    timestamp: datetime
    tvl_usd: Decimal = Field(alias="tvlUsd")
    apy: Decimal | None = None


def _row_to_snapshot(
    rows: list[_LlamaPoolRow], chain: Chain, timestamp: datetime
) -> MarketSnapshot:
    """Collapse N rows (same chain, same timestamp) into one MarketSnapshot."""
    apys: dict[str, Decimal] = {}
    pools: dict[str, PoolState] = {}

    for row in rows:
        pid = _pool_id(chain, row.project, row.symbol)
        # Prefer total APY; fall back to base+reward; finally 0.
        if row.apy is not None:
            apys[pid] = row.apy
        elif row.apy_base is not None or row.apy_reward is not None:
            apys[pid] = (row.apy_base or Decimal(0)) + (row.apy_reward or Decimal(0))
        else:
            apys[pid] = Decimal(0)

        pools[pid] = PoolState(
            pool_id=pid,
            tvl=row.tvl_usd,
            # DefiLlama yields API does not expose depth/fees_24h directly.
            # Templates that need them must combine with another adapter
            # (e.g. RPC pool-state reader). Zero is the honest sentinel here.
            depth=Decimal(0),
            fees_24h=Decimal(0),
        )

    return MarketSnapshot(
        timestamp=timestamp,
        chain=chain,
        prices={},  # Yields API has no spot prices; use RpcAdapter for those.
        apys=apys,
        pool_state=pools,
        gas_gwei=Decimal(0),  # Same — RpcAdapter is the source of truth for gas.
        metadata={"source": "defillama", "pool_count": len(rows)},
    )


class DefiLlamaAdapter:
    """Public-API yields adapter satisfying `DataAdapter`.

    Stateless apart from the HTTP client; can be instantiated once per
    process and shared across cycles. The injected `client` makes unit
    tests trivial (pass an `httpx.AsyncClient` wired to
    `httpx.MockTransport`).
    """

    name = "defillama"
    historical_supported = True

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        *,
        base_url: str = _BASE_URL,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=timeout)

    async def aclose(self) -> None:
        """Close the underlying HTTP client if we own it."""
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> DefiLlamaAdapter:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()

    # ------------------------------------------------------------------
    # DataAdapter Protocol
    # ------------------------------------------------------------------
    async def fetch_live(self, chain: Chain) -> MarketSnapshot:
        """Snapshot every pool on `chain` at the current time.

        Pulls `/pools` (one round-trip), filters to `chain`, and returns
        one MarketSnapshot stamped with the current UTC time.
        """
        llama_chain = _CHAIN_TO_LLAMA[chain]
        url = f"{self._base_url}/pools"

        _logger.info("defillama_fetch_live_start", chain=chain, url=url)
        resp = await self._client.get(url)
        resp.raise_for_status()
        payload = resp.json()

        raw_rows: list[dict[str, Any]] = payload.get("data", [])
        rows = [
            _LlamaPoolRow.model_validate(r)
            for r in raw_rows
            if r.get("chain") == llama_chain
        ]

        now = datetime.now().astimezone()
        snapshot = _row_to_snapshot(rows, chain, now)
        _logger.info(
            "defillama_fetch_live_ok",
            chain=chain,
            pool_count=len(rows),
            timestamp=now.isoformat(),
        )
        return snapshot

    async def fetch_historical(
        self, chain: Chain, start: datetime, end: datetime
    ) -> AsyncIterator[MarketSnapshot]:
        """Yield one snapshot per unique timestamp in [start, end].

        Strategy: list all pools for `chain` once, fan out `/chart/{pool}`
        requests sequentially (DefiLlama rate-limits aggressive parallelism),
        bucket points by timestamp, yield in chronological order.

        Caller pattern:
            async for snap in adapter.fetch_historical("base", t0, t1):
                ...
        """
        llama_chain = _CHAIN_TO_LLAMA[chain]

        # 1. Discover pools on this chain.
        pools_url = f"{self._base_url}/pools"
        _logger.info(
            "defillama_fetch_historical_start",
            chain=chain,
            start=start.isoformat(),
            end=end.isoformat(),
        )
        pools_resp = await self._client.get(pools_url)
        pools_resp.raise_for_status()
        pools_payload = pools_resp.json()
        pools = [
            _LlamaPoolRow.model_validate(r)
            for r in pools_payload.get("data", [])
            if r.get("chain") == llama_chain
        ]

        # 2. Bucket: timestamp → list[(row_template, point)].
        # We keep the row metadata (project, symbol) alongside each point so
        # _row_to_snapshot can re-emit pool IDs without re-fetching `/pools`.
        buckets: dict[datetime, list[_LlamaPoolRow]] = {}

        for pool in pools:
            chart_url = f"{self._base_url}/chart/{pool.pool}"
            chart_resp = await self._client.get(chart_url)
            if chart_resp.status_code != 200:
                _logger.warning(
                    "defillama_chart_skip",
                    pool=pool.pool,
                    status=chart_resp.status_code,
                )
                continue

            chart_data = chart_resp.json().get("data", [])
            for raw_point in chart_data:
                try:
                    point = _LlamaChartPoint.model_validate(raw_point)
                except (ValueError, KeyError):
                    continue
                # Compare as tz-aware: DefiLlama timestamps are UTC ISO.
                ts = point.timestamp
                if ts < start or ts > end:
                    continue
                # Re-materialise a row stamped with the point's values so
                # _row_to_snapshot treats them uniformly.
                stamped = pool.model_copy(
                    update={"tvl_usd": point.tvl_usd, "apy": point.apy}
                )
                buckets.setdefault(ts, []).append(stamped)

        # 3. Yield in chronological order.
        for ts in sorted(buckets):
            yield _row_to_snapshot(buckets[ts], chain, ts)

        _logger.info(
            "defillama_fetch_historical_done",
            chain=chain,
            distinct_timestamps=len(buckets),
        )


__all__ = ["DefiLlamaAdapter"]
