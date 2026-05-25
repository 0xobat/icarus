"""Switching allocator — equal-weight for short-history candidates,
risk-parity for the rest.

A single decision cycle can mix freshly-promoted candidates (a few days
of returns) with veterans (months). The blueprint W6 line — "cold-start
equal-weight → steady-state risk-parity" — reads like a global toggle,
but the natural unit of switching is *per candidate*, not per cycle:
the moment a single new candidate ships into the live cohort, a
global-toggle allocator would either drop it (no history) or pretend it
has steady-state vol (bad). Per-candidate switching keeps the lake
operationally usable while still using risk-parity on everyone with
enough data.

Mechanics:
  * `observation_window_days` (default 14): a candidate with fewer than
    this many return observations is "cold-start"; everyone else is
    "steady-state".
  * Cold-start cohort gets `compute_equal_weight_weights` over the
    cold-start subset's NAV-share.
  * Steady-state cohort gets `compute_risk_parity_weights` over the
    steady-state subset's NAV-share.
  * NAV-share between the two cohorts is proportional to the cohort
    size — each side gets ``len(cohort) / len(all)`` of NAV before its
    own internal weighting runs. This matches what a global allocator
    would have done in the absence of any history (1/N per candidate)
    while still letting the steady-state side risk-weight internally.
  * Final two-stage caps (per-candidate, per-template) apply once at
    the end to the merged weight vector — the same `apply_caps` helper
    both sub-allocators use, so cap semantics are identical.

Pure compute. No I/O.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from decimal import Decimal

import numpy as np

from icarus.allocator._caps import apply_caps
from icarus.allocator.equal_weight import compute_equal_weight_weights
from icarus.allocator.risk_parity import compute_risk_parity_weights
from icarus.allocator.types import CandidateInput, RosterEntry
from icarus.protocols.allocator import AllocationDecision
from icarus.protocols.regime import Regime
from icarus.types import Decision, PortfolioSnapshot

DEFAULT_OBSERVATION_WINDOW_DAYS = 14
"""Default ``observation_window_days``. Mirrors the per-template breaker
window (`lake_metrics.TemplateBreaker`) so a fresh candidate hits
risk-parity sizing at the same moment its template-level breaker has
enough data to fire — both gates "wake up" together."""


def _partition(
    inputs: Sequence[CandidateInput], window_days: int
) -> tuple[list[CandidateInput], list[CandidateInput]]:
    """Split inputs into (cold_start, steady_state) by return-history length.

    Pure helper. ``window_days`` is treated as a sample-count threshold —
    one return per day is the modelled cadence, so 14 days of returns
    == 14 samples. Templates that produce sub-daily returns will look
    "mature" faster, which is fine: the gate is about *having enough
    samples for std to be meaningful*, not calendar age.
    """
    cold: list[CandidateInput] = []
    warm: list[CandidateInput] = []
    for ci in inputs:
        if ci.recent_returns.size < window_days:
            cold.append(ci)
        else:
            warm.append(ci)
    return cold, warm


def compute_composed_weights(
    inputs: Sequence[CandidateInput],
    *,
    observation_window_days: int = DEFAULT_OBSERVATION_WINDOW_DAYS,
    template_cap_pct: Decimal | None = None,
) -> tuple[dict[str, Decimal], dict[str, Decimal], str]:
    """Pure functional core for the composed allocator.

    Returns ``(weights, template_caps_applied, mode)``. ``mode`` is
    ``"cold_start"`` / ``"risk_parity"`` / ``"composed"`` so the
    decision-engine can record which side ran each cycle without
    re-deriving it.
    """
    if not inputs:
        return {}, {}, "cold_start"

    cold, warm = _partition(inputs, observation_window_days)
    n_total = Decimal(len(inputs))

    # Run each side over its own subset WITHOUT applying caps yet — we
    # apply the unified two-stage cap once at the end so the per-template
    # aggregation sees both cohorts together.
    cold_weights: dict[str, Decimal] = {}
    if cold:
        share = Decimal(len(cold)) / n_total
        cold_raw, _ = compute_equal_weight_weights(
            cold, template_cap_pct=Decimal(1)  # disable per-template cap here
        )
        # Scale this cohort's internal weights down to its NAV-share.
        cold_weights = {cid: w * share for cid, w in cold_raw.items()}

    warm_weights: dict[str, Decimal] = {}
    if warm:
        share = Decimal(len(warm)) / n_total
        warm_raw, _, _ = compute_risk_parity_weights(
            warm, template_cap_pct=Decimal(1)
        )
        warm_weights = {cid: w * share for cid, w in warm_raw.items()}

    merged = {**cold_weights, **warm_weights}
    final, template_caps = apply_caps(
        merged, inputs, template_cap_pct=template_cap_pct
    )

    if cold and warm:
        mode = "composed"
    elif warm:
        mode = "risk_parity"
    else:
        mode = "cold_start"
    return final, template_caps, mode


def composed_commentary(
    inputs: Sequence[CandidateInput],
    template_caps: Mapping[str, Decimal],
    mode: str,
    window_days: int,
) -> str:
    """Human-readable rationale for the composed allocator's choice."""
    if not inputs:
        return "composed: no live candidates this cycle; nothing to allocate"
    n_cold = sum(1 for ci in inputs if ci.recent_returns.size < window_days)
    n_warm = len(inputs) - n_cold
    base = (
        f"composed[{mode}]: {n_cold} cold-start + {n_warm} steady-state "
        f"(threshold={window_days} observations)"
    )
    if template_caps:
        caps_str = ", ".join(
            f"{tmpl} clipped to {cap}" for tmpl, cap in sorted(template_caps.items())
        )
        return f"{base}; template caps bound: {caps_str}"
    return base


class ComposedAllocator:
    """Protocol-satisfying per-candidate switching allocator.

    Wraps `compute_composed_weights` in the Protocol shape so the
    decision-engine can plug this in interchangeably with the bare
    equal-weight / risk-parity allocators when it wants per-candidate
    switching.
    """

    name: str = "composed"

    def __init__(
        self,
        roster: Mapping[str, RosterEntry],
        returns_lookup: Callable[[str], np.ndarray],
        *,
        observation_window_days: int = DEFAULT_OBSERVATION_WINDOW_DAYS,
    ):
        """Bind a roster snapshot, returns-lookup, and switching window."""
        self._roster = dict(roster)
        self._returns_lookup = returns_lookup
        self._window_days = observation_window_days

    def allocate(
        self,
        candidate_decisions: Mapping[str, Decision],
        portfolio: PortfolioSnapshot,
        regime: Regime,
    ) -> AllocationDecision:
        """Protocol entry point. Returns dollar targets per candidate."""
        inputs = _build_inputs(
            candidate_decisions, self._roster, self._returns_lookup
        )
        weights, template_caps, mode = compute_composed_weights(
            inputs, observation_window_days=self._window_days
        )
        targets = {
            cid: (portfolio.nav_usd * frac) for cid, frac in weights.items()
        }
        return AllocationDecision(
            target_usd_by_candidate=targets,
            mode=mode,
            template_caps_applied=template_caps,
            commentary=composed_commentary(
                inputs, template_caps, mode, self._window_days
            ),
        )


def compose_allocators(
    observation_window_days: int = DEFAULT_OBSERVATION_WINDOW_DAYS,
) -> Callable[
    [
        Mapping[str, Decision],
        PortfolioSnapshot,
        Regime,
        Mapping[str, RosterEntry],
        Callable[[str], np.ndarray],
    ],
    AllocationDecision,
]:
    """Return a stateless switching function.

    The Protocol class `ComposedAllocator` binds roster + returns_lookup
    at construction time. `compose_allocators` is the *functional* form:
    a closure over the window-days choice that takes everything else at
    call time. Useful for the decision-engine when roster and
    returns_lookup change every cycle (the usual case) and the
    allocator object would otherwise be re-built each cycle.

    The returned callable's signature is:

        f(candidate_decisions, portfolio, regime, roster, returns_lookup)
        -> AllocationDecision
    """

    def _allocate(
        candidate_decisions: Mapping[str, Decision],
        portfolio: PortfolioSnapshot,
        regime: Regime,
        roster: Mapping[str, RosterEntry],
        returns_lookup: Callable[[str], np.ndarray],
    ) -> AllocationDecision:
        allocator = ComposedAllocator(
            roster,
            returns_lookup,
            observation_window_days=observation_window_days,
        )
        return allocator.allocate(candidate_decisions, portfolio, regime)

    return _allocate


def _build_inputs(
    decisions: Mapping[str, Decision],
    roster: Mapping[str, RosterEntry],
    returns_lookup: Callable[[str], np.ndarray],
) -> list[CandidateInput]:
    """Join `decisions` with `roster` and returns into `CandidateInput`."""
    out: list[CandidateInput] = []
    for cid, decision in decisions.items():
        entry = roster.get(cid)
        if entry is None:
            continue
        out.append(
            CandidateInput(
                candidate_id=cid,
                template_id=entry.template_id,
                confidence=decision.confidence,
                allocation_max_pct=entry.allocation_max_pct,
                recent_returns=CandidateInput.coerce_returns(
                    returns_lookup(cid)
                ),
            )
        )
    return out
