"""Portfolio snapshot — second input to every `evaluate()` call."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True)
class Position:
    """Open position attributable to a single candidate."""

    candidate_id: str
    template_id: str
    asset: str
    size_usd: Decimal
    entry_price: Decimal
    entry_time: datetime


@dataclass(frozen=True)
class PortfolioSnapshot:
    """Portfolio state at decision time.

    `positions` is keyed by `candidate_id` (not asset) because the lake
    architecture allocates per candidate, and two candidates from different
    templates can hold the same underlying asset.
    """

    nav_usd: Decimal
    positions: Mapping[str, Position]
    cash_usd: Decimal
    drawdown_from_peak: Decimal
    last_rebalance: datetime
