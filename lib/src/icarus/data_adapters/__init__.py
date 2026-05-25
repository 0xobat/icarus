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
from icarus.protocols.data import DataAdapter


def build_default_adapter() -> DataAdapter:
    """Pick a default `DataAdapter` for service boot.

    Surfaced by the W4 review: backtest-worker's `_resolve_adapter_from_env`
    (`backtest-worker/src/backtest_worker/__main__.py:139`) calls this factory
    at worker startup. v1 default = `DefiLlamaAdapter` because it requires
    no API key (public yields API) and covers APY/TVL — the data shape the
    first two reference templates (LEND-001, BASIS-PERP-001) actually need.

    Per-template adapter routing (e.g. send Dune-backed templates to
    `DuneAdapter`, Chainlink-priced templates to `RpcAdapter`) is W5+ work
    once the registry knows each template's data dependencies.
    """
    return DefiLlamaAdapter()


__all__ = [
    "DataAdapter",
    "DefiLlamaAdapter",
    "DuneAdapter",
    "RpcAdapter",
    "build_default_adapter",
]
