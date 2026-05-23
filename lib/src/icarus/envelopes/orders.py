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
