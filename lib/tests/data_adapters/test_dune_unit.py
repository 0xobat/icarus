"""Unit tests for DuneAdapter — JSON → MarketSnapshot transformation.

Pure tests: no network, no real sleeps in the success path. An
`httpx.MockTransport` serves canned Dune responses so we exercise the
real request-routing + parsing path without touching api.dune.com.

What we cover:
  - Protocol conformance (`isinstance(..., DataAdapter)`).
  - `fetch_live` round-trips the `/query/{id}/results` cached endpoint and
    folds rows into a MarketSnapshot keyed by the rows' max timestamp.
  - `fetch_historical` posts execute → polls status → fetches results,
    buckets rows by timestamp, drops out-of-window rows.
  - Transient 5xx triggers a retry; permanent 4xx does not.
  - Auth header is set on every call.
  - Terminal `QUERY_STATE_FAILED` raises DuneQueryError.
  - Missing `chain_queries` mapping is a loud error, not a silent KeyError.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx
import pytest
from icarus.data_adapters import DuneAdapter
from icarus.data_adapters.dune import DuneQueryError
from icarus.protocols.data import DataAdapter
from icarus.types import MarketSnapshot

# Two pools at two distinct timestamps; one row out-of-window for
# fetch_historical to drop.
_HISTORICAL_ROWS: list[dict[str, Any]] = [
    {
        "timestamp": "2026-01-01T00:00:00Z",
        "pool_id": "base:aave_v3:usdc",
        "tvl_usd": "1000.5",
        "apy": "3.0",
        "depth_usd": "500",
        "fees_24h_usd": "12.34",
    },
    {
        "timestamp": "2026-01-01T00:00:00Z",
        "pool_id": "base:moonwell:weth",
        "tvl_usd": "2000",
        "apy": "1.5",
    },
    {
        "timestamp": "2026-01-02T00:00:00Z",
        "pool_id": "base:aave_v3:usdc",
        "tvl_usd": "1100",
        "apy": "3.1",
    },
    # Out of window — must be dropped.
    {
        "timestamp": "2025-01-01T00:00:00Z",
        "pool_id": "base:aave_v3:usdc",
        "tvl_usd": "999",
        "apy": "2.0",
    },
]

_LIVE_ROWS: list[dict[str, Any]] = [
    {
        "timestamp": "2026-05-25T12:00:00Z",
        "pool_id": "base:aave_v3:usdc",
        "tvl_usd": 12_345_678.90,
        "apy": 4.21,
    },
    {
        "timestamp": "2026-05-25T12:00:00Z",
        "pool_id": "base:moonwell:weth",
        "tvl_usd": 9_876_543.21,
        "apy": None,  # Forces _coerce_decimal to default to 0.
    },
    # A scalar-only row (no pool_id) contributing ETH price + gas.
    {
        "timestamp": "2026-05-25T12:00:00Z",
        "eth_price_usd": "3500.25",
        "gas_gwei": "0.05",
    },
]


def _execute_response(execution_id: str = "01H0EXEC") -> dict[str, Any]:
    return {"execution_id": execution_id, "state": "QUERY_STATE_PENDING"}


def _status_response(execution_id: str, state: str) -> dict[str, Any]:
    return {"execution_id": execution_id, "state": state}


def _results_envelope(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "execution_id": "01H0EXEC",
        "state": "QUERY_STATE_COMPLETED",
        "result": {
            "rows": rows,
            "metadata": {"column_names": list(rows[0].keys()) if rows else []},
        },
    }


# ----------------------------------------------------------------------
# Handler builders
# ----------------------------------------------------------------------
def _make_handler_success():
    """Routes for the happy-path historical + live flows."""

    captured_headers: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured_headers.update(dict(request.headers))
        path = request.url.path
        # Cached results (fetch_live).
        if path == "/api/v1/query/100/results":
            return httpx.Response(200, json=_results_envelope(_LIVE_ROWS))
        # Execute (fetch_historical).
        if path == "/api/v1/query/200/execute" and request.method == "POST":
            return httpx.Response(200, json=_execute_response("01H0EXEC"))
        # Status poll.
        if path == "/api/v1/execution/01H0EXEC/status":
            return httpx.Response(
                200, json=_status_response("01H0EXEC", "QUERY_STATE_COMPLETED")
            )
        # Results fetch.
        if path == "/api/v1/execution/01H0EXEC/results":
            return httpx.Response(200, json=_results_envelope(_HISTORICAL_ROWS))
        return httpx.Response(404, text=f"unexpected path {path}")

    return handler, captured_headers


def _make_handler_5xx_then_ok(target_path: str):
    """Returns 503 on the first hit to `target_path`, then 200 on retry."""

    state = {"hits": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == target_path:
            state["hits"] += 1
            if state["hits"] == 1:
                return httpx.Response(503, text="bad gateway")
            return httpx.Response(200, json=_results_envelope(_LIVE_ROWS))
        return httpx.Response(404)

    return handler, state


def _make_handler_failed_execution():
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/execute"):
            return httpx.Response(200, json=_execute_response("01H0FAIL"))
        if path.endswith("/status"):
            return httpx.Response(
                200, json=_status_response("01H0FAIL", "QUERY_STATE_FAILED")
            )
        return httpx.Response(404)

    return handler


# ----------------------------------------------------------------------
# Tests
# ----------------------------------------------------------------------
def _make_adapter(handler, *, chain_queries=None) -> DuneAdapter:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    return DuneAdapter(
        chain_queries or {"base": 100},
        client=client,
        api_key="test-key",
        # Squash the poll interval so retries don't add real sleep cost.
        poll_interval_seconds=0.0,
        poll_timeout_seconds=5.0,
    )


def test_adapter_satisfies_protocol() -> None:
    """If this fails, callers using DataAdapter typing won't accept us."""
    handler, _ = _make_handler_success()
    adapter = _make_adapter(handler)
    assert isinstance(adapter, DataAdapter)
    assert adapter.name == "dune"
    assert adapter.historical_supported is True


def test_constructor_requires_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """No env var, no constructor arg → loud error at construction."""
    monkeypatch.delenv("DUNE_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="DUNE_API_KEY"):
        DuneAdapter({"base": 1})


def test_constructor_picks_up_env_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DUNE_API_KEY", "env-key")
    adapter = DuneAdapter({"base": 1})
    # Internal attribute — exposed because the test contract for the env
    # path is "key was read from env", and there's no public reflector.
    assert adapter._api_key == "env-key"
    asyncio.run(adapter.aclose())


@pytest.mark.asyncio
async def test_fetch_live_returns_snapshot_with_max_row_timestamp() -> None:
    handler, captured = _make_handler_success()
    adapter = _make_adapter(handler, chain_queries={"base": 100})

    snap = await adapter.fetch_live("base")

    assert isinstance(snap, MarketSnapshot)
    assert snap.chain == "base"
    assert snap.timestamp == datetime(2026, 5, 25, 12, 0, tzinfo=UTC)
    assert set(snap.apys.keys()) == {"base:aave_v3:usdc", "base:moonwell:weth"}
    assert snap.apys["base:aave_v3:usdc"] == Decimal("4.21")
    # None → 0 via _coerce_decimal.
    assert snap.apys["base:moonwell:weth"] == Decimal(0)
    # Scalar columns reached the snapshot.
    assert snap.prices["ETH"] == Decimal("3500.25")
    assert snap.gas_gwei == Decimal("0.05")
    # TVL faithfully copied (Decimal, not float).
    assert snap.pool_state["base:aave_v3:usdc"].tvl == Decimal("12345678.90")
    assert snap.metadata["source"] == "dune"
    # Auth header was attached to the outgoing request.
    assert captured["x-dune-api-key"] == "test-key"

    await adapter.aclose()


@pytest.mark.asyncio
async def test_fetch_live_unconfigured_chain_raises_clearly() -> None:
    handler, _ = _make_handler_success()
    adapter = _make_adapter(handler, chain_queries={"base": 100})
    with pytest.raises(RuntimeError, match="no query_id configured"):
        await adapter.fetch_live("solana")
    await adapter.aclose()


@pytest.mark.asyncio
async def test_fetch_live_per_call_query_id_override_wins() -> None:
    """Per-call `query_id=` skips the chain_queries lookup."""
    handler, _ = _make_handler_success()
    # Don't configure 'solana' in chain_queries; pass query_id=100 explicitly,
    # which the handler knows about.
    adapter = _make_adapter(handler, chain_queries={"base": 100})
    snap = await adapter.fetch_live("solana", query_id=100)
    # The handler returned LIVE_ROWS; chain on the snapshot is the
    # requested chain, not derived from the rows.
    assert snap.chain == "solana"
    assert len(snap.apys) == 2
    await adapter.aclose()


@pytest.mark.asyncio
async def test_fetch_historical_executes_polls_buckets_and_filters_window() -> None:
    handler, _ = _make_handler_success()
    adapter = _make_adapter(handler, chain_queries={"base": 200})

    start = datetime(2025, 12, 31, tzinfo=UTC)
    end = datetime(2026, 1, 31, tzinfo=UTC)
    snaps = [s async for s in adapter.fetch_historical("base", start, end)]

    # Two in-window timestamps; one out-of-window row dropped.
    assert len(snaps) == 2
    assert snaps[0].timestamp < snaps[1].timestamp

    first = snaps[0]
    assert first.timestamp == datetime(2026, 1, 1, tzinfo=UTC)
    assert set(first.apys.keys()) == {"base:aave_v3:usdc", "base:moonwell:weth"}
    assert first.apys["base:aave_v3:usdc"] == Decimal("3.0")
    assert first.pool_state["base:aave_v3:usdc"].depth == Decimal("500")
    assert first.pool_state["base:aave_v3:usdc"].fees_24h == Decimal("12.34")

    second = snaps[1]
    assert second.timestamp == datetime(2026, 1, 2, tzinfo=UTC)
    assert list(second.apys.keys()) == ["base:aave_v3:usdc"]
    assert second.apys["base:aave_v3:usdc"] == Decimal("3.1")

    await adapter.aclose()


@pytest.mark.asyncio
async def test_transient_5xx_is_retried_then_succeeds() -> None:
    handler, state = _make_handler_5xx_then_ok("/api/v1/query/100/results")
    adapter = _make_adapter(handler, chain_queries={"base": 100})

    snap = await adapter.fetch_live("base")

    assert state["hits"] == 2  # One 503, one 200.
    assert isinstance(snap, MarketSnapshot)
    await adapter.aclose()


@pytest.mark.asyncio
async def test_4xx_is_raised_immediately_not_retried() -> None:
    """4xx is a caller bug (bad query_id, expired key). Retrying burns budget
    and hides the real error from the operator."""

    state = {"hits": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        state["hits"] += 1
        return httpx.Response(404, text="query not found")

    adapter = _make_adapter(handler, chain_queries={"base": 100})
    with pytest.raises(httpx.HTTPStatusError):
        await adapter.fetch_live("base")
    assert state["hits"] == 1
    await adapter.aclose()


@pytest.mark.asyncio
async def test_failed_execution_raises_dune_query_error() -> None:
    handler = _make_handler_failed_execution()
    adapter = _make_adapter(handler, chain_queries={"base": 200})
    with pytest.raises(DuneQueryError, match="QUERY_STATE_FAILED"):
        start = datetime(2026, 1, 1, tzinfo=UTC)
        end = datetime(2026, 2, 1, tzinfo=UTC)
        [s async for s in adapter.fetch_historical("base", start, end)]
    await adapter.aclose()


@pytest.mark.asyncio
async def test_aclose_only_closes_owned_client() -> None:
    """If the caller injected a client, the adapter must NOT close it."""
    handler, _ = _make_handler_success()
    transport = httpx.MockTransport(handler)
    injected = httpx.AsyncClient(transport=transport)
    adapter = DuneAdapter({"base": 1}, client=injected, api_key="k")
    await adapter.aclose()
    assert not injected.is_closed
    await injected.aclose()
