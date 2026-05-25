"""Concrete `DataAdapter` implementations.

Each adapter satisfies `icarus.protocols.data.DataAdapter`:
  - `name: str` for log breadcrumbs + config lookup
  - `historical_supported: bool` (backtest engine refuses live-only)
  - `async fetch_live(chain)` → MarketSnapshot
  - `fetch_historical(chain, start, end)` → AsyncIterator[MarketSnapshot]

W3 ships two adapters:
  - `DefiLlamaAdapter` — public yields API, historical + live, APY/TVL focus.
  - `RpcAdapter` — EVM archive node (Alchemy on Base), historical + live,
    gas/price focus via Chainlink aggregators.

Future adapters (Dune, websocket feeds) land here too; downstream code
imports from this module, never from individual files.
"""

from icarus.data_adapters.defillama import DefiLlamaAdapter
from icarus.data_adapters.rpc import RpcAdapter

__all__ = ["DefiLlamaAdapter", "RpcAdapter"]
