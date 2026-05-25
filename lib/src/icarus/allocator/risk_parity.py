"""Steady-state allocator — inverse-volatility risk-parity.

Implements the blueprint W6 steady-state rule:

    "cold-start equal-weight → steady-state risk-parity"

Each live candidate contributes equal *risk* (not equal capital) to the
book: weight is proportional to ``1 / sigma_i`` where ``sigma_i`` is the
candidate's realised return-volatility on the rolling window. This is
the standard naive risk-parity formulation — full ERC (equal risk
contribution) on the covariance matrix would buy a little extra
diversification when correlations are non-zero, but adds a numerical
solve we don't need at the lake's expected size (~10s of candidates,
mostly orthogonal templates).

Confidence multiplies into the inverse-vol weight before normalisation,
so a low-conviction candidate gets proportionally less risk budget even
when its volatility says otherwise.

Degenerate handling (W3 pattern: early-return-on-zero-variance):
  * Any candidate with ``len(recent_returns) < 2`` or
    ``std(recent_returns) ≈ 0`` cannot be risk-weighted (vol is
    undefined or pathologically small → weight blows up). In that case
    we fall back to equal-weight across the entire cohort and stamp the
    commentary with ``risk_parity: degenerate variance, fell back to
    equal-weight``.

Caps then apply uniformly: per-candidate ``allocation_max_pct`` first,
then lake-level per-template cap (default 30 %).

Pure compute. No I/O.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from decimal import Decimal

import numpy as np

from icarus.allocator._caps import apply_caps
from icarus.allocator.equal_weight import compute_equal_weight_weights
from icarus.allocator.types import CandidateInput, RosterEntry
from icarus.protocols.allocator import AllocationDecision
from icarus.protocols.regime import Regime
from icarus.types import Decision, PortfolioSnapshot

MODE = "risk_parity"
"""`AllocationDecision.mode` value emitted by this allocator."""

MIN_OBSERVATIONS = 2
"""Below this many returns, sample std is undefined; force equal-weight."""

ZERO_VARIANCE_EPS = 1e-12
"""Sample std at or below this is treated as zero (numerical floor)."""


def _is_degenerate(returns: np.ndarray) -> bool:
    """True when the return series cannot give a usable volatility."""
    if returns.size < MIN_OBSERVATIONS:
        return True
    if not np.all(np.isfinite(returns)):
        return True
    sigma = float(np.std(returns, ddof=1))
    return sigma <= ZERO_VARIANCE_EPS


def compute_risk_parity_weights(
    inputs: Sequence[CandidateInput],
    *,
    template_cap_pct: Decimal | None = None,
) -> tuple[dict[str, Decimal], dict[str, Decimal], bool]:
    """Pure functional core: NAV-fraction per candidate, inverse-vol.

    Returns ``(weights, template_caps_applied, fell_back)``. ``fell_back``
    is True iff at least one candidate had degenerate variance and the
    allocator dropped to equal-weight across the *entire* cohort (we
    don't partially fall back — a single degenerate candidate poisons
    the cohort's risk budget calculation, and equal-weight is the
    documented robust fallback).

    Empty input → empty results, no fallback flagged.
    """
    if not inputs:
        return {}, {}, False

    # Degenerate guard: any single candidate with no usable variance
    # collapses the inverse-vol normalisation, so fall back uniformly.
    if any(_is_degenerate(ci.recent_returns) for ci in inputs):
        weights, caps = compute_equal_weight_weights(
            inputs, template_cap_pct=template_cap_pct
        )
        return weights, caps, True

    # Inverse-vol weights, scaled by confidence, then normalise to sum-to-1.
    inv_vol_weighted = []
    for ci in inputs:
        sigma = float(np.std(ci.recent_returns, ddof=1))
        # Multiply by confidence so low-conviction candidates shrink.
        inv_vol_weighted.append(float(ci.confidence) / sigma)
    total = sum(inv_vol_weighted)
    if total <= 0:
        # All confidences zero (or numerically vanished). Same fallback.
        weights, caps = compute_equal_weight_weights(
            inputs, template_cap_pct=template_cap_pct
        )
        return weights, caps, True

    raw = {
        ci.candidate_id: Decimal(str(w / total))
        for ci, w in zip(inputs, inv_vol_weighted, strict=True)
    }
    capped, template_caps = apply_caps(raw, inputs, template_cap_pct=template_cap_pct)
    return capped, template_caps, False


def risk_parity_commentary(
    inputs: Sequence[CandidateInput],
    template_caps: Mapping[str, Decimal],
    fell_back: bool,
) -> str:
    """Human-readable choice rationale embedded in `AllocationDecision`.

    The fallback string is searchable in the audit log so an operator
    can grep for "degenerate variance" across cycles to spot a
    structural data-quality issue (e.g. a price feed serving constants).
    """
    if not inputs:
        return "risk_parity: no live candidates this cycle; nothing to allocate"
    n = len(inputs)
    if fell_back:
        return (
            f"risk_parity: degenerate variance in cohort of {n}, "
            f"fell back to equal-weight"
        )
    base = f"risk_parity: inverse-vol weighting across {n} live candidates"
    if template_caps:
        caps_str = ", ".join(
            f"{tmpl} clipped to {cap}" for tmpl, cap in sorted(template_caps.items())
        )
        return f"{base}; template caps bound: {caps_str}"
    return base


class RiskParityAllocator:
    """Protocol-satisfying steady-state allocator.

    Needs more than the bare Protocol inputs (recent returns per
    candidate aren't on `Decision`), so construction takes a
    ``returns_lookup`` callable that maps ``candidate_id`` to a 1-D
    return ndarray. The decision-engine builds that lookup once per
    cycle (typically from a Postgres query against the realised-pnl
    table) and passes it in.
    """

    name: str = "risk_parity"

    def __init__(
        self,
        roster: Mapping[str, RosterEntry],
        returns_lookup: Callable[[str], np.ndarray],
    ):
        """Bind a roster snapshot and a returns-lookup callable.

        ``returns_lookup`` is invoked at most once per candidate per
        cycle; missing candidates should return an empty ndarray (the
        degenerate-variance fallback will catch them).
        """
        self._roster = dict(roster)
        self._returns_lookup = returns_lookup

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
        weights, template_caps, fell_back = compute_risk_parity_weights(inputs)
        targets = {
            cid: (portfolio.nav_usd * frac) for cid, frac in weights.items()
        }
        # If we fell back, surface the cold-start mode label so the audit
        # log accurately reports what actually drove sizing this cycle.
        mode = "cold_start_fallback" if fell_back else MODE
        return AllocationDecision(
            target_usd_by_candidate=targets,
            mode=mode,
            template_caps_applied=template_caps,
            commentary=risk_parity_commentary(inputs, template_caps, fell_back),
        )


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
