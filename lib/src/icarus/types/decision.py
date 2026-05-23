"""Decision — the output of a candidate's `evaluate()` call."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

Action = Literal["enter", "exit", "rebalance", "hold"]


@dataclass(frozen=True)
class Decision:
    """A candidate's per-cycle recommendation.

    Note: this is the candidate's *intent*. The allocator decides whether
    to honour it (sizing, risk-parity cap, lake-level template cap), and
    the pre-trade risk gate decides whether to let any resulting order
    through to a chain executor. A `Decision` is advisory until the
    deterministic gates run.

    `confidence` ∈ [0, 1] feeds allocator weighting in cold-start mode.
    """

    action: Action
    target_size: Decimal
    confidence: Decimal
    reasoning: str
