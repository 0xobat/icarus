"""Unit tests for DefiLlamaAdapter — JSON → MarketSnapshot transformation.

Pure tests: no network, no sleep. An httpx.MockTransport serves canned
JSON responses so we exercise the real request-routing + parsing path
without touching DefiLlama's servers. If the adapter regresses on field
mapping, casing, or pool ID synthesis, these fail.

The Protocol-conformance check (`isinstance(..., DataAdapter)`) lives here
too — if someone removes `historical_supported` or renames `fetch_live`,
this is the test that fails first.
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

import httpx
import pytest
from icarus.data_adapters import DefiLlamaAdapter
from icarus.protocols.data import DataAdapter
from icarus.types import MarketSnapshot

# A minimal `/pools` payload mixing Base + Solana + another chain so the
# chain filter is actually exercised. Field names match DefiLlama exactly
# (camelCase) — the adapter's pydantic aliases are responsible for the
# mapping.
_POOLS_PAYLOAD: dict[str, Any] = {
    "status": "success",
    "data": [
        {
            "chain": "Base",
            "project": "aave-v3",
            "symbol": "USDC",
            "pool": "uuid-aave-usdc",
            "tvlUsd": 12_345_678.90,
            "apy": 4.21,
            "apyBase": 3.5,
            "apyReward": 0.71,
        },
        {
            "chain": "Base",
            "project": "moonwell",
            "symbol": "WETH",
            "pool": "uuid-moonwell-weth",
            "tvlUsd": 9_876_543.21,
            # Force the "no top-level apy → sum of base + reward" branch.
            "apy": None,
            "apyBase": 1.2,
            "apyReward": 0.8,
        },
        {
            "chain": "Solana",
            "project": "kamino",
            "symbol": "SOL-USDC",
            "pool": "uuid-kamino-sol-usdc",
            "tvlUsd": 1_000_000.0,
            "apy": 7.5,
        },
        # Different chain — must be filtered out.
        {
            "chain": "Arbitrum",
            "project": "aave-v3",
            "symbol": "USDC",
            "pool": "uuid-arb-aave",
            "tvlUsd": 999.0,
            "apy": 9.9,
        },
    ],
}

_CHART_AAVE: dict[str, Any] = {
    "status": "success",
    "data": [
        {"timestamp": "2026-01-01T00:00:00.000Z", "tvlUsd": 1000.0, "apy": 3.0},
        {"timestamp": "2026-01-02T00:00:00.000Z", "tvlUsd": 1100.0, "apy": 3.1},
        # Out-of-range — must be filtered.
        {"timestamp": "2025-01-01T00:00:00.000Z", "tvlUsd": 500.0, "apy": 2.0},
    ],
}

_CHART_MOONWELL: dict[str, Any] = {
    "status": "success",
    "data": [
        {"timestamp": "2026-01-01T00:00:00.000Z", "tvlUsd": 2000.0, "apy": 1.5},
    ],
}


def _make_handler(extra_pools: dict[str, dict[str, Any]] | None = None):
    """Build an httpx MockTransport handler that routes by URL path."""
    charts = {
        "uuid-aave-usdc": _CHART_AAVE,
        "uuid-moonwell-weth": _CHART_MOONWELL,
    }
    if extra_pools:
        charts.update(extra_pools)

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/pools":
            return httpx.Response(200, json=_POOLS_PAYLOAD)
        if path.startswith("/chart/"):
            pool_id = path.removeprefix("/chart/")
            payload = charts.get(pool_id)
            if payload is None:
                return httpx.Response(404, json={"status": "error"})
            return httpx.Response(200, json=payload)
        return httpx.Response(404, text=f"unexpected path {path}")

    return handler


def test_adapter_satisfies_protocol() -> None:
    """If this fails, callers using DataAdapter typing won't accept us."""
    transport = httpx.MockTransport(_make_handler())
    client = httpx.AsyncClient(transport=transport)
    adapter = DefiLlamaAdapter(client=client)
    assert isinstance(adapter, DataAdapter)
    assert adapter.name == "defillama"
    assert adapter.historical_supported is True


def test_pool_id_synthesis_is_deterministic_and_lowercased() -> None:
    """`base:aave-v3:USDC` must collapse to a stable lowercase slug.

    Indirect test — we look at the rendered MarketSnapshot.
    """
    from icarus.data_adapters.defillama import _pool_id

    assert _pool_id("base", "aave-v3", "USDC") == "base:aave_v3:usdc"
    assert _pool_id("solana", "Kamino V2", "SOL-mSOL") == "solana:kamino_v2:sol_msol"
    # Idempotent under repeated calls.
    assert _pool_id("base", "aave-v3", "USDC") == _pool_id("base", "aave-v3", "USDC")


@pytest.mark.asyncio
async def test_fetch_live_base_filters_and_maps_pools() -> None:
    transport = httpx.MockTransport(_make_handler())
    async with httpx.AsyncClient(transport=transport) as client:
        adapter = DefiLlamaAdapter(client=client)
        snap = await adapter.fetch_live("base")

    assert isinstance(snap, MarketSnapshot)
    assert snap.chain == "base"
    # Only Base pools made it through.
    assert set(snap.apys.keys()) == {
        "base:aave_v3:usdc",
        "base:moonwell:weth",
    }
    # Top-level apy wins when present.
    assert snap.apys["base:aave_v3:usdc"] == Decimal("4.21")
    # base + reward sum when top-level apy is absent.
    assert snap.apys["base:moonwell:weth"] == Decimal("1.2") + Decimal("0.8")
    # TVL faithfully copied.
    aave = snap.pool_state["base:aave_v3:usdc"]
    assert aave.tvl == Decimal("12345678.90")
    assert aave.depth == Decimal(0)  # honest sentinel
    # gas + prices are intentionally empty — RpcAdapter is the source.
    assert snap.gas_gwei == Decimal(0)
    assert snap.prices == {}
    assert snap.metadata["source"] == "defillama"
    assert snap.metadata["pool_count"] == 2


@pytest.mark.asyncio
async def test_fetch_live_solana_filters_to_solana_only() -> None:
    transport = httpx.MockTransport(_make_handler())
    async with httpx.AsyncClient(transport=transport) as client:
        adapter = DefiLlamaAdapter(client=client)
        snap = await adapter.fetch_live("solana")

    assert snap.chain == "solana"
    assert list(snap.apys.keys()) == ["solana:kamino:sol_usdc"]
    assert snap.apys["solana:kamino:sol_usdc"] == Decimal("7.5")


@pytest.mark.asyncio
async def test_fetch_historical_buckets_by_timestamp_and_filters_window() -> None:
    """Chart points outside [start, end] must be dropped; in-range points
    bucket into per-timestamp MarketSnapshots, ordered ascending."""
    from datetime import datetime

    transport = httpx.MockTransport(_make_handler())
    async with httpx.AsyncClient(transport=transport) as client:
        adapter = DefiLlamaAdapter(client=client)
        start = datetime.fromisoformat("2025-12-31T00:00:00+00:00")
        end = datetime.fromisoformat("2026-01-31T00:00:00+00:00")
        snaps = [s async for s in adapter.fetch_historical("base", start, end)]

    # Two distinct timestamps in range: 2026-01-01 and 2026-01-02.
    # 2025-01-01 is out of range, must not appear.
    assert len(snaps) == 2
    assert snaps[0].timestamp < snaps[1].timestamp

    # First bucket: both pools have a point at 2026-01-01.
    first = snaps[0]
    assert first.timestamp == datetime.fromisoformat("2026-01-01T00:00:00+00:00")
    assert set(first.apys.keys()) == {
        "base:aave_v3:usdc",
        "base:moonwell:weth",
    }
    assert first.apys["base:aave_v3:usdc"] == Decimal("3.0")
    assert first.apys["base:moonwell:weth"] == Decimal("1.5")
    assert first.pool_state["base:moonwell:weth"].tvl == Decimal("2000.0")

    # Second bucket: only aave has a 2026-01-02 point.
    second = snaps[1]
    assert second.timestamp == datetime.fromisoformat("2026-01-02T00:00:00+00:00")
    assert list(second.apys.keys()) == ["base:aave_v3:usdc"]
    assert second.apys["base:aave_v3:usdc"] == Decimal("3.1")
    assert second.pool_state["base:aave_v3:usdc"].tvl == Decimal("1100.0")


@pytest.mark.asyncio
async def test_fetch_historical_skips_pools_with_failed_chart() -> None:
    """If `/chart/{pool}` 404s, the adapter logs and continues — does not raise."""
    from datetime import datetime

    # The handler returns 404 for any unknown pool; add a pool to /pools
    # that has no chart entry to force the 404 branch.
    extra_pool: dict[str, Any] = {
        "chain": "Base",
        "project": "ghostprotocol",
        "symbol": "XYZ",
        "pool": "uuid-no-chart",
        "tvlUsd": 1.0,
        "apy": 0.0,
    }
    pools_payload_with_extra = {
        "status": "success",
        "data": [*_POOLS_PAYLOAD["data"], extra_pool],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/pools":
            return httpx.Response(200, json=pools_payload_with_extra)
        if request.url.path == "/chart/uuid-no-chart":
            return httpx.Response(404, json={"status": "error"})
        if request.url.path.startswith("/chart/"):
            pid = request.url.path.removeprefix("/chart/")
            charts = {"uuid-aave-usdc": _CHART_AAVE, "uuid-moonwell-weth": _CHART_MOONWELL}
            payload = charts.get(pid)
            return httpx.Response(200, json=payload) if payload else httpx.Response(404)
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        adapter = DefiLlamaAdapter(client=client)
        start = datetime.fromisoformat("2025-12-31T00:00:00+00:00")
        end = datetime.fromisoformat("2026-01-31T00:00:00+00:00")
        snaps = [s async for s in adapter.fetch_historical("base", start, end)]

    # We still got the buckets from the two real pools; the ghost pool was skipped.
    assert len(snaps) == 2
    for snap in snaps:
        assert "base:ghostprotocol:xyz" not in snap.apys


@pytest.mark.asyncio
async def test_aclose_only_closes_owned_client() -> None:
    """If the caller injected a client, the adapter must NOT close it."""
    transport = httpx.MockTransport(_make_handler())
    injected = httpx.AsyncClient(transport=transport)
    adapter = DefiLlamaAdapter(client=injected)
    await adapter.aclose()
    # Injected client must still be usable.
    assert not injected.is_closed
    await injected.aclose()


def test_pool_payload_is_valid_json_fixture() -> None:
    """Smoke check that our fixture round-trips through JSON cleanly,
    so a typo in the fixture surfaces here instead of inside a test."""
    s = json.dumps(_POOLS_PAYLOAD)
    assert json.loads(s) == _POOLS_PAYLOAD
