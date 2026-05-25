"""Unit tests for the cold-start equal-weight allocator."""

from __future__ import annotations

from decimal import Decimal

from icarus.allocator.equal_weight import compute_equal_weight_weights
from icarus.allocator.types import CandidateInput


def _ci(
    cid: str,
    *,
    template_id: str | None = None,
    confidence: float = 1.0,
    cap: float = 1.0,
) -> CandidateInput:
    # Default each candidate to its own template so per-template caps
    # don't accidentally bind in tests that aren't checking cap behaviour.
    return CandidateInput(
        candidate_id=cid,
        template_id=template_id if template_id is not None else f"T_{cid}",
        confidence=Decimal(str(confidence)),
        allocation_max_pct=Decimal(str(cap)),
        recent_returns=CandidateInput.coerce_returns(None),
    )


def test_three_candidates_get_equal_one_third_nav() -> None:
    """(a) 3 candidates → each gets 1/3 NAV-fraction.

    Cap disabled (template_cap_pct=1.0) because 1/3 > 0.30 default; the
    default cap is correct production behaviour but obscures the pure
    1/N math we're checking here. Cap binding gets its own test below.
    """
    inputs = [_ci("A"), _ci("B"), _ci("C")]
    weights, caps = compute_equal_weight_weights(
        inputs, template_cap_pct=Decimal(1)
    )

    expected = Decimal(1) / Decimal(3)
    assert weights == {"A": expected, "B": expected, "C": expected}
    # Three exact Decimal(1/3)s sum to 0.999... at Decimal's default 28-digit
    # precision (rounding closes the gap, not the sum). Use a tolerance.
    assert abs(sum(weights.values()) - Decimal(1)) < Decimal("1e-27")
    assert caps == {}


def test_per_candidate_allocation_max_caps_bind() -> None:
    """(b) per-candidate `allocation_max_pct` cap binds.

    Two candidates from different templates, each capped at 0.10. Naive
    1/2 = 0.50 would breach. Output must clip to 0.10 / 0.10. (The
    *un-deployed* 0.80 just stays in cash — the allocator is not
    obliged to deploy 100 % of NAV; per-candidate caps are upper
    bounds, not redistribution triggers.)
    """
    inputs = [
        _ci("A", template_id="T1", cap=0.10),
        _ci("B", template_id="T2", cap=0.10),
    ]
    weights, caps = compute_equal_weight_weights(inputs)

    assert weights == {"A": Decimal("0.10"), "B": Decimal("0.10")}
    # No template aggregation triggered (each template has one candidate
    # capped well below the 0.30 default).
    assert caps == {}


def test_zero_candidates_returns_empty() -> None:
    """(c) empty input → empty output, no exceptions."""
    weights, caps = compute_equal_weight_weights([])
    assert weights == {}
    assert caps == {}
