"""Dune Analytics adapter — parameterised SQL queries against Dune's data warehouse.

Dune exposes a query-execution API at `https://api.dune.com/api/v1/`:

  POST /query/{query_id}/execute
    → kicks off a fresh execution, returns `execution_id`. Used by
      `fetch_historical` when callers need a specific [start, end] window
      that the cached snapshot may not cover.

  GET  /execution/{execution_id}/status
    → poll until `state == "QUERY_STATE_COMPLETED"` (or `FAILED`).

  GET  /execution/{execution_id}/results
    → row payload once complete.

  GET  /query/{query_id}/results
    → Dune-side cached most-recent execution. One round-trip. Used by
      `fetch_live` because the latency budget is "head of cache" not
      "freshly executed".

Authoring split (per blueprint W4):
  - This module is the generic HTTP + parsing layer (the "~3 days, generic"
    half). It does NOT know which `query_id` corresponds to which chain or
    template — that mapping is owned by per-template config and passed in
    at construction (`chain_queries=`) or per-call (`query_id=`).
  - The per-template query authoring is ongoing and lives in
    `templates/<TEMPLATE_ID>/dune/*.sql` (out of scope for this adapter).

Row → MarketSnapshot contract:
  Dune queries authored for this adapter MUST select at least these columns:
    - `timestamp` (ISO-8601 string or Dune `timestamp` type)
    - `pool_id`   (chain-qualified slug; same scheme DefiLlama emits)
    - `tvl_usd`   (numeric)
    - `apy`       (numeric, percent, optional — defaults to 0)
  Optional columns: `depth_usd`, `fees_24h_usd`, `eth_price_usd`,
  `gas_gwei`. Unknown columns are ignored.

Caching (W4 follow-up, deferred):
  Blueprint § "Dune adapter (week 4)" calls for a Postgres-backed cache
  keyed by `(query_id, params_hash)` to absorb repeat reads. Schema sketch:

    CREATE TABLE dune_cache (
      query_id        BIGINT      NOT NULL,
      params_hash     TEXT        NOT NULL,
      executed_at     TIMESTAMPTZ NOT NULL,
      rows            JSONB       NOT NULL,
      PRIMARY KEY (query_id, params_hash)
    );

  Not load-bearing at v1 scale (Analyst plan headroom — Q7); tracked as
  a follow-up rather than blocking this adapter. Today the adapter relies
  on Dune's own server-side cache (`/results` endpoint) for `fetch_live`.

Auth: reads `DUNE_API_KEY` from env (header `X-Dune-API-Key`). Constructor
arg `api_key=` overrides for tests.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Mapping
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx
import structlog
from pydantic import BaseModel, ConfigDict, Field

from icarus.types import MarketSnapshot
from icarus.types.market import Chain, PoolState

_logger = structlog.get_logger(service="data_adapters.dune")

_BASE_URL = "https://api.dune.com/api/v1"
_DEFAULT_TIMEOUT = 60.0
_API_KEY_ENV_VAR = "DUNE_API_KEY"

# Execution poll cadence. Dune executions for small queries finish in a few
# seconds; long queries can take minutes. We poll on a fixed cadence rather
# than expo backoff because Dune's billing is per-execution, not per-poll.
_POLL_INTERVAL_SECONDS = 2.0
_POLL_TIMEOUT_SECONDS = 600.0  # 10 min — Analyst-tier ceiling for our templates.

# Retry budget for transient upstream errors (5xx, network). 4 attempts
# with linear backoff (1s, 2s, 3s) — Dune's failure modes are usually
# either "instantly retriable" or "completely down"; long backoff doesn't
# help the second case and starves the first.
_RETRY_MAX_ATTEMPTS = 4
_RETRY_BASE_DELAY_S = 1.0

_COMPLETED_STATES = frozenset({"QUERY_STATE_COMPLETED"})
_FAILED_STATES = frozenset(
    {"QUERY_STATE_FAILED", "QUERY_STATE_CANCELLED", "QUERY_STATE_EXPIRED"}
)


class DuneQueryError(RuntimeError):
    """Raised when Dune returns a terminal failure for an execution."""


class _DuneExecutionStarted(BaseModel):
    model_config = ConfigDict(extra="ignore")

    execution_id: str


class _DuneExecutionStatus(BaseModel):
    model_config = ConfigDict(extra="ignore")

    execution_id: str
    state: str


class _DuneResultsEnvelope(BaseModel):
    """Subset of the `/results` payload. Dune wraps rows under `result.rows`."""

    model_config = ConfigDict(extra="ignore")

    result: dict[str, Any] = Field(default_factory=dict)
    state: str | None = None  # `/query/{id}/results` includes a state too.

    @property
    def rows(self) -> list[dict[str, Any]]:
        return list(self.result.get("rows", []))


def _coerce_decimal(value: Any) -> Decimal:
    """Best-effort numeric coercion. Dune emits numbers as JSON floats or strings."""
    if value is None or value == "":
        return Decimal(0)
    try:
        # Stringify first to avoid float→Decimal precision artefacts.
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal(0)


def _coerce_timestamp(value: Any) -> datetime:
    """Parse the various timestamp shapes Dune returns.

    Dune emits ISO-8601 strings (UTC, sometimes with trailing 'Z').
    """
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        # `fromisoformat` handles `Z` only on 3.11+; we're on 3.13.
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    raise ValueError(f"Unparseable timestamp value: {value!r}")


def _rows_to_snapshot(
    rows: list[dict[str, Any]], chain: Chain, timestamp: datetime
) -> MarketSnapshot:
    """Collapse rows sharing one timestamp into one MarketSnapshot.

    See module docstring for the column contract that authored Dune queries
    must satisfy.
    """
    apys: dict[str, Decimal] = {}
    pools: dict[str, PoolState] = {}
    prices: dict[str, Decimal] = {}
    gas_gwei = Decimal(0)

    for row in rows:
        pool_id = row.get("pool_id")
        if not pool_id:
            # Rows without a pool_id contribute scalar fields (price, gas)
            # but no per-pool entry. This lets one Dune query mix
            # market-wide and per-pool readings.
            pass
        else:
            apys[pool_id] = _coerce_decimal(row.get("apy"))
            pools[pool_id] = PoolState(
                pool_id=pool_id,
                tvl=_coerce_decimal(row.get("tvl_usd")),
                depth=_coerce_decimal(row.get("depth_usd")),
                fees_24h=_coerce_decimal(row.get("fees_24h_usd")),
            )

        # Scalar fields: last writer wins. Dune queries authored for this
        # adapter are expected to emit at most one scalar per snapshot.
        if "eth_price_usd" in row and row["eth_price_usd"] is not None:
            prices["ETH"] = _coerce_decimal(row["eth_price_usd"])
        if "gas_gwei" in row and row["gas_gwei"] is not None:
            gas_gwei = _coerce_decimal(row["gas_gwei"])

    return MarketSnapshot(
        timestamp=timestamp,
        chain=chain,
        prices=prices,
        apys=apys,
        pool_state=pools,
        gas_gwei=gas_gwei,
        metadata={"source": "dune", "row_count": len(rows)},
    )


class DuneAdapter:
    """Dune Analytics adapter satisfying `DataAdapter`.

    Construction:
        adapter = DuneAdapter(chain_queries={"base": 1234567, "solana": 7654321})
        # Reads DUNE_API_KEY from env. Pass `api_key=` to override.

    Per-call override:
        await adapter.fetch_live("base", query_id=999)

    Tests:
        adapter = DuneAdapter(
            chain_queries={"base": 1},
            client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
            api_key="test-key",
        )
    """

    name = "dune"
    historical_supported = True

    def __init__(
        self,
        chain_queries: Mapping[Chain, int] | None = None,
        *,
        client: httpx.AsyncClient | None = None,
        api_key: str | None = None,
        base_url: str = _BASE_URL,
        timeout: float = _DEFAULT_TIMEOUT,
        poll_interval_seconds: float = _POLL_INTERVAL_SECONDS,
        poll_timeout_seconds: float = _POLL_TIMEOUT_SECONDS,
    ) -> None:
        resolved_key = api_key if api_key is not None else os.environ.get(_API_KEY_ENV_VAR)
        if not resolved_key:
            raise RuntimeError(
                f"DuneAdapter requires either `api_key=` or the "
                f"{_API_KEY_ENV_VAR} env var to be set."
            )
        self._api_key = resolved_key
        self._chain_queries: dict[Chain, int] = dict(chain_queries or {})
        self._base_url = base_url.rstrip("/")
        self._poll_interval = poll_interval_seconds
        self._poll_timeout = poll_timeout_seconds

        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=timeout,
            headers={"X-Dune-API-Key": self._api_key},
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> DuneAdapter:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()

    # ------------------------------------------------------------------
    # Internal HTTP
    # ------------------------------------------------------------------
    def _headers(self) -> dict[str, str]:
        """Inject API key on every call.

        We pass it as a per-request header rather than rely on the client's
        default headers because tests inject an `AsyncClient` with a
        MockTransport and may not set the key in client defaults.
        """
        return {"X-Dune-API-Key": self._api_key}

    async def _request_with_retry(
        self,
        method: str,
        url: str,
        *,
        json: dict[str, Any] | None = None,
    ) -> httpx.Response:
        """Single HTTP call with retry on transient 5xx + network errors.

        Raises the final httpx.Response (via raise_for_status) on 4xx —
        those are caller errors (bad query_id, expired key) and retrying
        only burns Dune budget.
        """
        last_exc: Exception | None = None
        for attempt in range(1, _RETRY_MAX_ATTEMPTS + 1):
            try:
                resp = await self._client.request(
                    method, url, json=json, headers=self._headers()
                )
            except (httpx.NetworkError, httpx.TimeoutException) as exc:
                last_exc = exc
                _logger.warning(
                    "dune_network_retry",
                    attempt=attempt,
                    method=method,
                    url=url,
                    error=str(exc),
                )
                if attempt == _RETRY_MAX_ATTEMPTS:
                    raise
                await asyncio.sleep(_RETRY_BASE_DELAY_S * attempt)
                continue

            if 500 <= resp.status_code < 600 and attempt < _RETRY_MAX_ATTEMPTS:
                _logger.warning(
                    "dune_5xx_retry",
                    attempt=attempt,
                    status=resp.status_code,
                    url=url,
                )
                await asyncio.sleep(_RETRY_BASE_DELAY_S * attempt)
                continue

            resp.raise_for_status()
            return resp

        # Unreachable in practice: the loop either returns, raises via
        # raise_for_status, or re-raises the captured network error.
        raise RuntimeError("dune retry loop exited without response") from last_exc

    def _resolve_query_id(self, chain: Chain, query_id: int | None) -> int:
        if query_id is not None:
            return query_id
        if chain not in self._chain_queries:
            raise RuntimeError(
                f"DuneAdapter has no query_id configured for chain={chain!r}. "
                "Pass `chain_queries=` at construction or `query_id=` per call."
            )
        return self._chain_queries[chain]

    # ------------------------------------------------------------------
    # Public query execution helpers (also used by fetch_historical)
    # ------------------------------------------------------------------
    async def execute_and_wait(
        self,
        query_id: int,
        *,
        query_parameters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Kick off a fresh execution, poll to completion, return rows.

        Used when callers need parameter-controlled freshness (e.g. a
        historical window). For "give me whatever Dune has cached", call
        `fetch_cached_results` instead.
        """
        exec_url = f"{self._base_url}/query/{query_id}/execute"
        body: dict[str, Any] = {}
        if query_parameters:
            body["query_parameters"] = query_parameters

        _logger.info(
            "dune_execute_start", query_id=query_id, has_params=bool(query_parameters)
        )
        exec_resp = await self._request_with_retry("POST", exec_url, json=body)
        execution = _DuneExecutionStarted.model_validate(exec_resp.json())
        execution_id = execution.execution_id

        # Poll status until terminal.
        status_url = f"{self._base_url}/execution/{execution_id}/status"
        elapsed = 0.0
        while elapsed < self._poll_timeout:
            status_resp = await self._request_with_retry("GET", status_url)
            status = _DuneExecutionStatus.model_validate(status_resp.json())
            if status.state in _COMPLETED_STATES:
                break
            if status.state in _FAILED_STATES:
                raise DuneQueryError(
                    f"Dune execution {execution_id} ended in state {status.state}"
                )
            await asyncio.sleep(self._poll_interval)
            elapsed += self._poll_interval
        else:
            raise DuneQueryError(
                f"Dune execution {execution_id} did not complete within "
                f"{self._poll_timeout}s"
            )

        results_url = f"{self._base_url}/execution/{execution_id}/results"
        results_resp = await self._request_with_retry("GET", results_url)
        envelope = _DuneResultsEnvelope.model_validate(results_resp.json())
        _logger.info(
            "dune_execute_done",
            query_id=query_id,
            execution_id=execution_id,
            row_count=len(envelope.rows),
        )
        return envelope.rows

    async def fetch_cached_results(self, query_id: int) -> list[dict[str, Any]]:
        """Fetch Dune's most recent cached execution for `query_id`.

        One round-trip. No execution charged. Source of truth for `fetch_live`.
        """
        url = f"{self._base_url}/query/{query_id}/results"
        _logger.info("dune_fetch_cached_start", query_id=query_id)
        resp = await self._request_with_retry("GET", url)
        envelope = _DuneResultsEnvelope.model_validate(resp.json())
        _logger.info(
            "dune_fetch_cached_done",
            query_id=query_id,
            row_count=len(envelope.rows),
            state=envelope.state,
        )
        return envelope.rows

    # ------------------------------------------------------------------
    # DataAdapter Protocol
    # ------------------------------------------------------------------
    async def fetch_live(
        self, chain: Chain, *, query_id: int | None = None
    ) -> MarketSnapshot:
        """Return the latest Dune-cached snapshot for `chain`.

        Resolves `query_id` from `chain_queries` unless overridden. Rows are
        collapsed into a single MarketSnapshot stamped with the most recent
        row timestamp (or now() if no `timestamp` column is present).
        """
        qid = self._resolve_query_id(chain, query_id)
        rows = await self.fetch_cached_results(qid)

        # Pick the snapshot timestamp: max(row.timestamp) if present, else now.
        ts_candidates: list[datetime] = []
        for row in rows:
            raw = row.get("timestamp")
            if raw is None:
                continue
            try:
                ts_candidates.append(_coerce_timestamp(raw))
            except ValueError:
                continue
        snapshot_ts = max(ts_candidates) if ts_candidates else datetime.now().astimezone()

        return _rows_to_snapshot(rows, chain, snapshot_ts)

    async def fetch_historical(
        self,
        chain: Chain,
        start: datetime,
        end: datetime,
        *,
        query_id: int | None = None,
        extra_parameters: dict[str, Any] | None = None,
    ) -> AsyncIterator[MarketSnapshot]:
        """Yield one MarketSnapshot per unique `timestamp` in [start, end].

        Executes the resolved query with `start` and `end` parameters (Dune
        query authors must declare matching `{{start}}` and `{{end}}` text
        parameters). Rows are bucketed by their `timestamp` column and
        yielded in chronological order.

        Caller pattern:
            async for snap in adapter.fetch_historical("base", t0, t1):
                ...
        """
        qid = self._resolve_query_id(chain, query_id)

        params: dict[str, Any] = {
            "start": start.isoformat(),
            "end": end.isoformat(),
        }
        if extra_parameters:
            params.update(extra_parameters)

        rows = await self.execute_and_wait(qid, query_parameters=params)

        # Bucket by timestamp.
        buckets: dict[datetime, list[dict[str, Any]]] = {}
        for row in rows:
            raw = row.get("timestamp")
            if raw is None:
                continue
            try:
                ts = _coerce_timestamp(raw)
            except ValueError:
                continue
            if ts < start or ts > end:
                # The query SHOULD respect [start, end] in SQL, but defending
                # against off-by-one in user-authored SQL is cheap insurance.
                continue
            buckets.setdefault(ts, []).append(row)

        for ts in sorted(buckets):
            yield _rows_to_snapshot(buckets[ts], chain, ts)

        _logger.info(
            "dune_fetch_historical_done",
            chain=chain,
            query_id=qid,
            distinct_timestamps=len(buckets),
        )


__all__ = ["DuneAdapter", "DuneQueryError"]
