"""Live end-to-end test — calls the real Dune Analytics API.

Dune queries cost execution credits (charged against the Analyst plan
budget per Q7), so this test stays deliberately minimal: one
`fetch_cached_results` call against a well-known public query.

Gated by DUNE_API_KEY to keep developer-machine `pytest` runs offline by
default. CI sets DUNE_API_KEY on the integration job.

We pick Dune's first-ever community query, query_id=4 ("dummy" select),
which is stable, public, and tiny. Asserting only that the call returns
a result envelope keeps this test independent of the underlying query's
row count drifting over time.
"""

from __future__ import annotations

import os

import pytest
from icarus.data_adapters import DuneAdapter

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not os.environ.get("DUNE_API_KEY"),
        reason="DUNE_API_KEY not set; live Dune test skipped",
    ),
]

# A stable public Dune query. Query 4 is one of the earliest community
# queries and is kept alive for sandbox use.
_PROBE_QUERY_ID = 4


@pytest.mark.asyncio
async def test_fetch_cached_results_round_trips_real_api() -> None:
    """At minimum, Dune should return a valid envelope shape.

    Failure modes this catches:
      (a) Lost network / DNS.
      (b) DUNE_API_KEY rotated or revoked.
      (c) Dune renamed `result.rows` or moved the cached endpoint.
    """
    async with DuneAdapter({"base": _PROBE_QUERY_ID}) as adapter:
        rows = await adapter.fetch_cached_results(_PROBE_QUERY_ID)

    # Rows may legitimately be empty (Dune cache evicted) — we only need
    # the call to complete cleanly. Type is enough.
    assert isinstance(rows, list)
