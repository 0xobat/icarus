"""Market data snapshot — input to every `evaluate()` call."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

Chain = Literal["base", "solana"]


@dataclass(frozen=True)
class PoolState:
    """Per-pool metrics.

    `pool_id` is a chain-qualified identifier (e.g. "base:aave_v3:usdc",
    "solana:kamino:sol-msol"). Adapters that produce PoolState must use a
    consistent scheme so templates can address pools deterministically.
    """

    pool_id: str
    tvl: Decimal
    depth: Decimal
    fees_24h: Decimal


@dataclass(frozen=True)
class MarketSnapshot:
    """Pre-sliced market data passed to `evaluate(params, market_data, portfolio_state)`.

    Built by the backtest engine (replay) or the runtime data assembler
    (live cycle). Same shape both places — that's what makes backtest-to-live
    fidelity a static check rather than a runtime hope.
    """

    timestamp: datetime
    chain: Chain
    prices: Mapping[str, Decimal]
    apys: Mapping[str, Decimal]
    pool_state: Mapping[str, PoolState]
    gas_gwei: Decimal
    metadata: Mapping[str, Any]
