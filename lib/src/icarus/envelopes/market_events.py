"""Market event envelope — `market:events:{base|solana}` Redis channel."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from icarus.types.market import Chain

MarketEventType = Literal[
    "new_block",
    "new_slot",
    "swap",
    "rate_change",
    "liquidity_change",
    "large_transfer",
    "price_update",
    "oracle_update",
]


class _StrictBase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class BaseChainSpecific(_StrictBase):
    """EVM-side per-event detail.

    `block_number`, `tx_hash`, `gas_used_wei`, etc. are EVM-flavoured;
    only attached to MarketEvents with chain="base".
    """

    block_number: int = Field(ge=0)
    tx_hash: str | None = None
    log_index: int | None = Field(default=None, ge=0)
    gas_price_wei: int | None = Field(default=None, ge=0)


class SolanaChainSpecific(_StrictBase):
    """Solana-side per-event detail.

    Slot / signature / lamports instead of block_number / tx_hash / wei.
    Only attached to MarketEvents with chain="solana".
    """

    slot: int = Field(ge=0)
    signature: str | None = None
    priority_fee_lamports: int | None = Field(default=None, ge=0)


class MarketEvent(_StrictBase):
    """Single normalised event published by a chain executor.

    The chain executor produces these by watching the chain's RPC + Helius
    streams (Solana) or Alchemy WS (Base) and normalising to this shape.
    The decision-engine consumes them, updates its in-memory market cache,
    and (once-per-cycle) builds a `MarketSnapshot` from the cache.

    Sequence numbers are per-chain monotonic; a gap means the WS dropped
    events and the decision-engine flags `data_stale` to the risk gate.
    """

    version: Literal["1.0.0"] = "1.0.0"
    timestamp: datetime
    chain: Chain
    sequence: int = Field(ge=0)
    event_type: MarketEventType
    protocol: str = Field(description="aave_v3, aerodrome, kamino, drift, jupiter, system, ...")
    correlation_id: str

    # Symbol the event pertains to, when applicable (e.g. "USDC", "SOL/USDC LP")
    symbol: str | None = None

    # Price / rate / volume payload — kept loose because event types differ
    payload: dict[str, Decimal | str | int | None] = Field(default_factory=dict)

    # Chain-specific addressing (exactly one of these is set, matching `chain`)
    base_specific: BaseChainSpecific | None = None
    solana_specific: SolanaChainSpecific | None = None
