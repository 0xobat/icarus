"""DataAdapter Protocol — pluggable market-data sources.

Concrete adapters: DefiLlama (week 1), on-chain RPC (week 1), Dune (week 4).
All three return the same `MarketSnapshot` shape so backtest and live
runtime can swap adapter without changing strategy code.

`fetch_historical` is an async iterator (not a list) because backtests
replay months of snapshots and materialising them all eats RAM — a
years-of-Solana-ticks backtest would not fit. Callers `async for` over
the result.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from typing import Protocol, runtime_checkable

from icarus.types import MarketSnapshot
from icarus.types.market import Chain


@runtime_checkable
class DataAdapter(Protocol):
    """Async source of `MarketSnapshot`s.

    `name` is used for log breadcrumbs and config lookup.
    `historical_supported` distinguishes replay-capable adapters (DefiLlama,
    Dune) from live-only ones (a websocket feed); the backtest engine refuses
    live-only adapters.
    """

    name: str
    historical_supported: bool

    async def fetch_live(self, chain: Chain) -> MarketSnapshot:
        """Return the most recent snapshot for `chain`."""
        ...

    def fetch_historical(
        self, chain: Chain, start: datetime, end: datetime
    ) -> AsyncIterator[MarketSnapshot]:
        """Yield ordered snapshots covering [start, end].

        Concrete impls are async generators (`async def` + `yield`).
        Raises NotImplementedError on the *first* iteration if
        `historical_supported` is False.
        """
        ...
