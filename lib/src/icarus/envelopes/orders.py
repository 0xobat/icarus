"""Execution order envelope — `execution:orders:{base|solana}` Redis channel."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from icarus.types.market import Chain

OrderAction = Literal[
    # Common
    "swap",
    "supply",
    "withdraw",
    # EVM-flavoured
    "mint_lp",
    "burn_lp",
    "stake",
    "unstake",
    "collect_fees",
    "flash_loan",
    # Solana-flavoured
    "open_perp",
    "close_perp",
    "deposit",
    "borrow",
    "repay",
]

OrderPriority = Literal["urgent", "normal", "low"]
"""urgent = stop-loss / emergency unwind; bypasses gas/priority-fee throttles."""


class _StrictBase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class OrderParams(_StrictBase):
    """Action-specific parameters. Kept loose intentionally — the executor
    validates per-action shape against the protocol adapter. Amounts are
    Decimal strings on the wire to preserve precision."""

    token_in: str | None = None
    token_out: str | None = None
    amount: Decimal | None = None  # Smallest unit (wei or lamports), stringified on wire
    recipient: str | None = None
    pool_id: str | None = None  # e.g. "base:aerodrome:usdc-usdbc"
    venue: str | None = None  # e.g. "drift", "kamino"
    extra: dict[str, str | int | Decimal | None] = Field(default_factory=dict)


class OrderLimits(_StrictBase):
    """Pre-trade limits enforced by the executor before broadcast.

    The risk pre-trade gate fills these in based on the allocator's sizing
    + the candidate's manifest. An order that violates its own limits is
    auto-rejected at the executor with a `failed` ExecutionResult.

    Solana orders use `max_priority_fee_lamports` instead of `max_gas_wei`;
    both are stringified Decimals on the wire."""

    max_gas_wei: Decimal | None = None
    max_priority_fee_lamports: Decimal | None = None
    max_slippage_bps: int = Field(ge=0, le=1000, description="0-1000 basis points (10%)")
    deadline_unix: int = Field(ge=0)


class SolanaSpecificOrder(_StrictBase):
    """SVM-side per-order detail.

    `compute_unit_price` is the priority fee in micro-lamports per CU (the
    SVM analogue of EVM's `maxPriorityFeePerGas`); the solana-executor
    multiplies it by `compute_unit_limit` to bound total priority spend.
    `lookup_tables` are optional address-lookup-table addresses (v0 tx
    compression) — empty list means no ALTs.

    Only attached to ExecutionOrders with chain="solana"."""

    compute_unit_price: int | None = Field(
        default=None,
        ge=0,
        description="Priority fee in micro-lamports per compute unit.",
    )
    compute_unit_limit: int | None = Field(
        default=None,
        ge=0,
        description="Max compute units the tx may consume (SVM gas budget).",
    )
    lookup_tables: list[str] = Field(
        default_factory=list,
        description="Address-lookup-table addresses for v0 tx compression.",
    )


class ExecutionOrder(_StrictBase):
    """One order, one chain, one candidate.

    `template_id` and `candidate_id` identify which lake row asked for this
    order. The result envelope echoes both back so the decision-engine can
    attribute fills to the right candidate's PnL.

    `strategy` is a v4.2-compatibility prefix used by circuit-breaker
    emissions (`"CB:drawdown"`, etc.) that fire outside the normal
    allocator path. For allocator-issued orders, `strategy` equals
    `f"{template_id}:{candidate_id}"`.
    """

    version: Literal["1.0.0"] = "1.0.0"
    order_id: str = Field(min_length=8, description="UUID")
    correlation_id: str
    timestamp: datetime
    chain: Chain
    protocol: str
    action: OrderAction
    strategy: str = Field(description="`<template_id>:<candidate_id>` or `CB:<breaker>`")
    template_id: str | None = None
    candidate_id: str | None = None
    priority: OrderPriority = "normal"
    params: OrderParams
    limits: OrderLimits

    # Chain-specific addressing. Base-side orders carry nothing here yet
    # (everything they need is in `limits`); Solana-side orders carry
    # compute_unit_price/compute_unit_limit/lookup_tables. Matches the
    # MarketEvent / ExecutionResult chain-discriminator pattern.
    solana_specific: SolanaSpecificOrder | None = None

    @model_validator(mode="after")
    def _strategy_matches_template_candidate(self) -> ExecutionOrder:
        """If both template_id and candidate_id are set, `strategy` must equal
        `f"{template_id}:{candidate_id}"`. Circuit-breaker orders skip this
        because they leave template_id/candidate_id None and use `CB:*`."""

        if self.template_id and self.candidate_id:
            expected = f"{self.template_id}:{self.candidate_id}"
            if self.strategy != expected:
                raise ValueError(
                    f"strategy ({self.strategy!r}) must equal '{expected}' when both "
                    "template_id and candidate_id are set"
                )
        elif self.strategy.startswith("CB:"):
            # Circuit-breaker emission — fine without template/candidate
            pass
        elif self.template_id or self.candidate_id:
            raise ValueError("template_id and candidate_id must be set together or both None")
        return self

    @model_validator(mode="after")
    def _chain_specific_matches_chain(self) -> ExecutionOrder:
        """Mirror the JSON Schema's allOf/if-then conditional for chain-side
        blocks. solana_specific is only valid when chain="solana"; chain=
        "solana" requires it to be present. Same pattern as MarketEvent.

        Base side is intentionally schema-flat for now — Base orders don't
        need a chain-specific block beyond what `limits` already carries.
        """

        if self.chain == "solana" and self.solana_specific is None:
            raise ValueError("chain='solana' requires solana_specific to be set")
        if self.chain == "base" and self.solana_specific is not None:
            raise ValueError("chain='base' must leave solana_specific unset")
        return self
