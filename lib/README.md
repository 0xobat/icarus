# icarus (shared lib)

Shared library for Icarus v2 services. Workspace member of the root `uv` workspace.

## What lives here

| Package | Purpose |
|---|---|
| `icarus.types` | Frozen dataclasses: `MarketSnapshot`, `PortfolioSnapshot`, `Decision`, `PoolState`, `Position` |
| `icarus.protocols` | Protocol interfaces (DI seams): `Allocator`, `RegimeClassifier`, `DecayDetector`, `Extractor`, `BacktestEngine`, `DataAdapter` |
| `icarus.dsl` | Manifest schema validator, AST linter, template registry loader |
| `icarus.envelopes` | Redis bus envelope schemas (`market:events`, `execution:orders`, `execution:results`) — chain-aware |
| `icarus.db` | (added during week 1 validated copy-back from `.archive/py-engine/db/`) |
| `icarus.risk` | (added during week 1 validated copy-back from `.archive/py-engine/risk/`) |
| `icarus.data_adapters` | DefiLlama (W1), RPC (W1), Dune (W4) |

## Stability rule

Anything exported from `icarus.protocols` is a **load-bearing seam**. Changing its shape requires updating every implementer and is the migration path to v3's MCP/agents architecture. Add new Protocols freely; modify existing ones with care.
