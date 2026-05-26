"""Execution result envelope — `execution:results:{base|solana}` Redis channel."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

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


class SolanaSpecificResult(_StrictBase):
    """SVM-side per-result detail.

    `signature` is the base58-encoded 64-byte tx signature (Solana's
    `tx_hash` analogue). `slot` is the block-number analogue.
    `compute_units_consumed` is the actual CU spend (the SVM `gas_used`).
    `priority_fee_lamports` is the total priority fee paid (priority_fee
    per CU times CUs consumed).

    Only attached to ExecutionResults with chain="solana"."""

    signature: str | None = Field(
        default=None,
        description="Base58 tx signature (64 bytes).",
    )
    slot: int | None = Field(default=None, ge=0)
    compute_units_consumed: int | None = Field(
        default=None,
        ge=0,
        description="Actual compute-unit spend reported by the cluster.",
    )
    priority_fee_lamports: int | None = Field(
        default=None,
        ge=0,
        description="Total priority fee paid in lamports.",
    )


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

    # On-chain identifiers (Base side flat for ts-executor compat).
    tx_hash: str | None = None
    block_number: int | None = Field(default=None, ge=0)

    # Economics — stringified Decimals on the wire (Base flat fields).
    gas_used_wei: Decimal | None = None
    effective_gas_price_wei: Decimal | None = None
    fill_price: Decimal | None = None
    amount_out: Decimal | None = None

    # Chain-specific addressing — Solana's signature/slot/CU/priority-fee
    # live here so the EVM half of the bus does not pay a schema cost.
    # Validator below enforces presence/absence against `chain`.
    solana_specific: SolanaSpecificResult | None = None

    # Failure detail
    revert_reason: str | None = None
    error: str | None = None
    retry_count: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _chain_specific_matches_chain(self) -> ExecutionResult:
        """Mirror the JSON Schema's allOf/if-then conditional. solana_specific
        is only valid when chain="solana"; chain="solana" requires it. Same
        pattern as MarketEvent and ExecutionOrder.

        Base side stays schema-flat (`tx_hash`, `block_number`, `gas_used_wei`,
        `effective_gas_price_wei` are top-level) to preserve the ts-executor
        v2 wire shape; only the Solana extension formally requires a block.
        """

        if self.chain == "solana" and self.solana_specific is None:
            raise ValueError("chain='solana' requires solana_specific to be set")
        if self.chain == "base" and self.solana_specific is not None:
            raise ValueError("chain='base' must leave solana_specific unset")
        return self
