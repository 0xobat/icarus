"""Live end-to-end test — calls the real DefiLlama public API.

DefiLlama yields are free + auth-less, so this is the only adapter test
that we can afford to run on every CI sweep against the real upstream.
The whole point is to fail loudly when DefiLlama changes their JSON shape
under us; mocks cannot tell us that.

Gated by LIVE_NETWORK to keep developer-machine `pytest` runs offline by
default. CI sets LIVE_NETWORK=1 on the integration job (separate from
the unit job).

Cost: $0. Latency: ~1-3s for /pools. We do NOT call /chart here because
that fans out per-pool and would push runtime above the budget.
"""

from __future__ import annotations

import os
from decimal import Decimal

import pytest
from icarus.data_adapters import DefiLlamaAdapter
from icarus.types import MarketSnapshot

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not os.environ.get("LIVE_NETWORK"),
        reason="LIVE_NETWORK not set; live DefiLlama test skipped",
    ),
]


@pytest.mark.asyncio
async def test_fetch_live_base_returns_real_pools() -> None:
    """At minimum, Base should always have *some* tracked pools.

    If this returns zero pools we either:
      (a) lost network access,
      (b) DefiLlama renamed the chain key,
      (c) the adapter's chain mapping drifted.
    All three deserve a loud failure.
    """
    async with DefiLlamaAdapter() as adapter:
        snap = await adapter.fetch_live("base")

    assert isinstance(snap, MarketSnapshot)
    assert snap.chain == "base"
    assert len(snap.apys) > 0, "DefiLlama returned no Base pools — investigate"
    # Sanity on the shape: every key in apys has a matching pool_state entry.
    assert set(snap.apys.keys()) == set(snap.pool_state.keys())
    # APYs come back as Decimals, not floats — protects strategy math.
    for pid, apy in snap.apys.items():
        assert isinstance(apy, Decimal), f"{pid} apy is {type(apy)}, expected Decimal"
        # Negative APYs do exist (Pendle YT decay etc.); just bound the universe.
        assert Decimal(-100) < apy < Decimal(10_000)
