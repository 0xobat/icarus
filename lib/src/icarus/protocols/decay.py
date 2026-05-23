"""DecayDetector Protocol — per-candidate performance drift detector.

Default impl: Page-Hinkley test on rolling 7-day Sharpe vs. paper-trade
baseline, threshold tuned per-template from initial paper-trade variance.

Detector is stateful (per-candidate state machine ticks each cycle as new
PnL samples arrive). The lake-governor service owns the state and persists
it in Postgres; the Protocol surface here is pure compute against an
in-memory state snapshot.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class DecayEvent:
    """Emitted when the detector trips.

    The lake-governor consumes this and transitions the candidate to
    `demoted_paper`. The LLM hypothesis comes later from a separate
    post-action advisor — `hypothesis` here is empty at trip-time.
    """

    candidate_id: str
    tripped_at: datetime
    rolling_sharpe: Decimal
    baseline_sharpe: Decimal
    cusum_statistic: Decimal
    hypothesis: str = ""


@runtime_checkable
class DecayDetector(Protocol):
    """Stateful per-candidate detector.

    `step` is called once per cycle with the latest realised return for the
    candidate. Returns a `DecayEvent` when the detector trips; otherwise
    None. The impl owns its internal CUSUM / Page-Hinkley state.
    """

    name: str

    def step(
        self,
        candidate_id: str,
        realized_return: Decimal,
        as_of: datetime,
    ) -> DecayEvent | None:
        """Advance the detector. Idempotent across same (candidate_id, as_of)."""
        ...

    def reset(self, candidate_id: str) -> None:
        """Clear state for a candidate (used on promotion back from demotion)."""
        ...
