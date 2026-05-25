"""Cold-start allocator — equal NAV-fraction per live candidate.

Implements the blueprint W6 cold-start rule:

    "cold-start equal-weight → steady-state risk-parity"

Used when the lake's live cohort has less than a few weeks of returns
each, so a covariance-based weighting would be dominated by sampling
noise. The 1/N rule is the documented robust fallback (DeMiguel, Garlappi,
Uppal 2009 — "Optimal Versus Naive Diversification: How Inefficient is
the 1/N Portfolio Strategy?"). We don't claim it dominates risk-parity
in steady state; we claim it dominates risk-parity *while the covariance
estimate is noise*.

Per-candidate ``allocation_max_pct`` caps still bind here — a small lake
of 2 candidates with 0.05 cap each will only deploy 10 % of NAV; the
remaining 90 % stays in cash. The lake-level template cap (default 30 %)
also binds in degenerate cases (e.g., all 4 candidates from one template).

Pure compute. The Protocol wrapper at the bottom builds `CandidateInput`
rows from a roster lookup + `Mapping[str, Decision]` so the Protocol
contract holds while the functional core stays testable in isolation.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal

from icarus.allocator._caps import apply_caps
from icarus.allocator.types import CandidateInput, RosterEntry
from icarus.protocols.allocator import AllocationDecision
from icarus.protocols.regime import Regime
from icarus.types import Decision, PortfolioSnapshot

MODE = "cold_start"
"""`AllocationDecision.mode` value emitted by this allocator."""


def compute_equal_weight_weights(
    inputs: Sequence[CandidateInput],
    *,
    template_cap_pct: Decimal | None = None,
) -> tuple[dict[str, Decimal], dict[str, Decimal]]:
    """Pure functional core: NAV-fraction per candidate, equal weight.

    Each candidate starts at ``1/N`` of NAV; then per-candidate and
    per-template caps are applied. The result is a fraction of NAV
    (callers multiply by ``portfolio.nav_usd`` to get dollars).

    Returns ``(weights, template_caps_applied)``. Empty input → empty
    weights, empty caps — no division-by-zero, no special-case
    callers need to write.
    """
    if not inputs:
        return {}, {}

    n = Decimal(len(inputs))
    raw = {ci.candidate_id: Decimal(1) / n for ci in inputs}
    return apply_caps(raw, inputs, template_cap_pct=template_cap_pct)


def equal_weight_commentary(
    inputs: Sequence[CandidateInput],
    template_caps: Mapping[str, Decimal],
) -> str:
    """Human-readable choice rationale embedded in `AllocationDecision`.

    Per W2 decision #4, commentary is operator-facing — the webapp
    surfaces it next to the cycle's orders. The empty-cohort and
    template-cap branches give an operator enough to act without
    cross-referencing logs.
    """
    if not inputs:
        return "cold_start: no live candidates this cycle; nothing to allocate"
    n = len(inputs)
    base = f"cold_start: 1/{n} equal-weight across {n} live candidates"
    if template_caps:
        caps_str = ", ".join(
            f"{tmpl} clipped to {cap}" for tmpl, cap in sorted(template_caps.items())
        )
        return f"{base}; template caps bound: {caps_str}"
    return base


class EqualWeightAllocator:
    """Protocol-satisfying cold-start allocator.

    Attributes:
        name: Protocol-required identifier; logged on every cycle so the
            audit log records *which* allocator ran (and which mode).
    """

    name: str = "equal_weight"

    def __init__(self, roster: Mapping[str, RosterEntry]):
        """Bind a per-candidate roster lookup (`template_id`, cap).

        ``roster`` is built once per cycle by the decision-engine from
        the live `LakeRoster` query, then handed to the allocator. The
        allocator stays pure with respect to that snapshot.
        """
        self._roster = dict(roster)

    def allocate(
        self,
        candidate_decisions: Mapping[str, Decision],
        portfolio: PortfolioSnapshot,
        regime: Regime,
    ) -> AllocationDecision:
        """Protocol entry point. Returns dollar targets per candidate."""
        inputs = _build_inputs(candidate_decisions, self._roster)
        weights, template_caps = compute_equal_weight_weights(inputs)
        targets = {
            cid: (portfolio.nav_usd * frac) for cid, frac in weights.items()
        }
        return AllocationDecision(
            target_usd_by_candidate=targets,
            mode=MODE,
            template_caps_applied=template_caps,
            commentary=equal_weight_commentary(inputs, template_caps),
        )


def _build_inputs(
    decisions: Mapping[str, Decision],
    roster: Mapping[str, RosterEntry],
) -> list[CandidateInput]:
    """Join `decisions` with `roster` into the pure-compute shape.

    Decisions with no matching roster entry are dropped — the lake-governor
    is the source of truth on "who is live"; a stale Decision for an
    already-demoted candidate must not get capital. (Cold-start doesn't
    use ``recent_returns``, so we pass an empty array.)
    """
    out: list[CandidateInput] = []
    for cid, _decision in decisions.items():
        entry = roster.get(cid)
        if entry is None:
            continue
        out.append(
            CandidateInput(
                candidate_id=cid,
                template_id=entry.template_id,
                confidence=_decision.confidence,
                allocation_max_pct=entry.allocation_max_pct,
                recent_returns=CandidateInput.coerce_returns(None),
            )
        )
    return out
