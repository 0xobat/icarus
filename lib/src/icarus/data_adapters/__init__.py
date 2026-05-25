"""Concrete `DataAdapter` implementations.

Each adapter satisfies `icarus.protocols.data.DataAdapter`:
  - `name: str` for log breadcrumbs + config lookup
  - `historical_supported: bool` (backtest engine refuses live-only)
  - `async fetch_live(chain)` → MarketSnapshot
  - `fetch_historical(chain, start, end)` → AsyncIterator[MarketSnapshot]

W3 shipped two adapters; W4 adds Dune:
  - `DefiLlamaAdapter` — public yields API, historical + live, APY/TVL focus.
  - `RpcAdapter` — EVM archive node (Alchemy on Base), historical + live,
    gas/price focus via Chainlink aggregators.
  - `DuneAdapter` — Dune Analytics SQL warehouse, historical + live,
    per-template query authoring (queries declared by callers).

Future adapters (websocket feeds, SolanaRpcAdapter) land here too;
downstream code imports from this module, never from individual files.
"""

from icarus.data_adapters.defillama import DefiLlamaAdapter
from icarus.data_adapters.dune import DuneAdapter
from icarus.data_adapters.rpc import RpcAdapter

__all__ = ["DefiLlamaAdapter", "DuneAdapter", "RpcAdapter"]
