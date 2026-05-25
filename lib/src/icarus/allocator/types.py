"""Shared allocator inputs.

The `Allocator` Protocol (``icarus.protocols.allocator``) takes
``Mapping[str, Decision]``, ``PortfolioSnapshot``, and ``Regime`` because
those are the contract the decision-engine assembles each cycle. But
sizing also needs per-candidate metadata that lives on ``LakeRoster``
(``template_id``, ``allocation_max_pct``) and, for risk-parity, a recent
return series.

Rather than fold those into the Protocol — and break v4.2 strategies that
consume ``Decision`` directly — each concrete allocator takes the roster
lookup at construction time and (for risk-parity) reads recent returns
from an injected callable. This keeps the Protocol stable while still
letting allocators do their job.

`CandidateInput` is the *pure-compute* shape consumed by the inner
functional cores in ``equal_weight.py`` / ``risk_parity.py``. The Protocol
wrappers build a sequence of `CandidateInput` from the roster + decisions
mapping, then delegate.

Pure dataclasses. No I/O.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

import numpy as np


@dataclass(frozen=True, slots=True)
class RosterEntry:
    """Per-candidate roster metadata the allocator needs.

    Mirrors the columns the lake-governor writes to ``LakeRoster``:
    ``template_id`` for the lake-level template cap, and
    ``allocation_max_pct`` for the per-candidate cap. The decision-engine
    builds a ``Mapping[candidate_id -> RosterEntry]`` once per cycle from
    the active roster query and passes it into the allocator.

    Attributes:
        template_id: soft FK to ``templates.template_id``. Used to
            aggregate per-template caps.
        allocation_max_pct: per-candidate NAV-fraction ceiling, in
            [0.0, 1.0]. ``0.05`` means "at most 5% of NAV ever in this
            candidate".
    """

    template_id: str
    allocation_max_pct: Decimal


@dataclass(frozen=True, slots=True)
class CandidateInput:
    """One row of pure-compute allocator input.

    The functional cores consume `Sequence[CandidateInput]` so unit tests
    can drive them directly without standing up the full Protocol shape.

    Attributes:
        candidate_id: stable id (matches `LakeRoster.candidate_id`).
        template_id: soft FK for lake-level template-cap aggregation.
        confidence: candidate's per-cycle confidence in [0, 1]. Risk-parity
            weights are scaled by confidence so a "low-conviction"
            candidate gets proportionally less size even if its volatility
            says otherwise.
        allocation_max_pct: per-candidate ceiling, in [0.0, 1.0].
        recent_returns: per-period returns ndarray. May be empty (cold-start
            cohort). Risk-parity uses ``len(recent_returns)`` against the
            composed-allocator observation window to decide cold-start vs
            steady-state per candidate.
    """

    candidate_id: str
    template_id: str
    confidence: Decimal
    allocation_max_pct: Decimal
    recent_returns: np.ndarray  # 1-D float64

    @staticmethod
    def coerce_returns(returns: Sequence[float] | np.ndarray | None) -> np.ndarray:
        """Normalise a returns input into a 1-D float64 ndarray.

        Pure helper so call sites can pass lists, generators, or arrays
        without each one re-implementing the dtype coercion.
        """
        if returns is None:
            return np.array([], dtype=np.float64)
        arr = np.asarray(returns, dtype=np.float64)
        if arr.ndim != 1:
            raise ValueError(
                f"recent_returns must be 1-D, got shape {arr.shape}"
            )
        return arr
