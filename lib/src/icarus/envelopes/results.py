"""Execution result envelope — `execution:results:{base|solana}` Redis channel."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from icarus.types.market import Chain

ExecutionStatus = Literal[
    "confirmed",
    "failed",
    "reverted",
    "timeout",
    "rejected_by_guard",  # application-level allowlist or risk gate rejected pre-broadcast
]


class _StrictBase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ExecutionResult(_StrictBase):
    """One result, one order.

    Echoes `template_id` + `candidate_id` from the originating order so the
    decision-engine can attribute fills to the right candidate's PnL without
    consulting the orders table. The `correlation_id` chain ties this back
    to the originating market event (or operator action) that triggered the
    cycle.

    `chain_specific` (intentionally untyped here, modeled per-chain by the
    executor) carries Solana-side fields like signature + priority fee, or
    EVM-side block_number + effective_gas_price. Consumers project the
    fields they care about; producers are required to fill at least
    block/slot identifier on success."""

    version: Literal["1.0.0"] = "1.0.0"
    order_id: str
    correlation_id: str
    timestamp: datetime
    chain: Chain
    status: ExecutionStatus
    template_id: str | None = None
    candidate_id: str | None = None

    # On-chain identifiers
    tx_hash: str | None = None
    signature: str | None = None
    block_number: int | None = Field(default=None, ge=0)
    slot: int | None = Field(default=None, ge=0)

    # Economics — stringified Decimals on the wire
    gas_used_wei: Decimal | None = None
    effective_gas_price_wei: Decimal | None = None
    priority_fee_lamports: Decimal | None = None
    fill_price: Decimal | None = None
    amount_out: Decimal | None = None

    # Failure detail
    revert_reason: str | None = None
    error: str | None = None
    retry_count: int = Field(default=0, ge=0)
