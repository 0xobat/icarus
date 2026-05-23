"""Type contracts for the strategy DSL and runtime.

These dataclasses are the deterministic contract between extractor-emitted
`evaluate.py` files, the backtest engine, the paper-trade harness, and the
runtime decision-engine. The extractor prompt embeds these stubs verbatim
so the LLM emits `evaluate()` against the same shapes every implementation
sees.

Stability rule: changing a field on any frozen dataclass here breaks every
template, every backtest, and every cached evaluation result. Bump the
manifest semver field on any breaking change.
"""

from icarus.types.decision import Action, Decision
from icarus.types.market import MarketSnapshot, PoolState
from icarus.types.portfolio import PortfolioSnapshot, Position

__all__ = [
    "Action",
    "Decision",
    "MarketSnapshot",
    "PoolState",
    "PortfolioSnapshot",
    "Position",
]
