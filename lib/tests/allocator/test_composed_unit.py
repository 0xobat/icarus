"""Unit tests for the composed (per-candidate switching) allocator."""

from __future__ import annotations

from decimal import Decimal

import numpy as np
from icarus.allocator.composed import compute_composed_weights
from icarus.allocator.types import CandidateInput

OBSERVATION_WINDOW_DAYS = 14


def _ci(
    cid: str,
    returns: np.ndarray,
    *,
    template_id: str | None = None,
    confidence: float = 1.0,
    cap: float = 1.0,
) -> CandidateInput:
    # Each candidate gets its own template by default so per-template
    # caps don't accidentally bind in tests where we're checking the
    # switching logic, not cap behaviour.
    return CandidateInput(
        candidate_id=cid,
        template_id=template_id if template_id is not None else f"T_{cid}",
        confidence=Decimal(str(confidence)),
        allocation_max_pct=Decimal(str(cap)),
        recent_returns=CandidateInput.coerce_returns(returns),
    )


def test_short_history_cohort_uses_equal_weight() -> None:
    """(a) candidates with < observation_window history → equal-weight.

    Three candidates with 5 returns each (< 14-day window) get the
    cold-start branch. Sample std would still be defined (n=5 > 2) but
    we choose equal-weight because the variance estimate is too noisy
    to trust for sizing.
    """
    rng = np.random.default_rng(seed=10)
    short = rng.normal(loc=0.0, scale=0.02, size=5)

    inputs = [_ci(c, short) for c in ("A", "B", "C")]
    weights, caps, mode = compute_composed_weights(
        inputs,
        observation_window_days=OBSERVATION_WINDOW_DAYS,
        template_cap_pct=Decimal(1),
    )

    assert mode == "cold_start"
    expected = Decimal(1) / Decimal(3)
    assert weights == {"A": expected, "B": expected, "C": expected}
    assert caps == {}


def test_long_history_cohort_uses_risk_parity() -> None:
    """(b) candidates with ≥ observation_window history → risk-parity.

    Two candidates, 30 returns each, with 2x-vol differential. The
    composed allocator routes them through risk-parity so the
    higher-vol candidate gets half the weight of the lower-vol one
    (same property as `test_double_vol_candidate_gets_half_weight`).
    """
    rng = np.random.default_rng(seed=11)
    base = rng.normal(loc=0.0, scale=0.01, size=30)
    a_ret = base / float(np.std(base, ddof=1)) * 0.01
    b_ret = base / float(np.std(base, ddof=1)) * 0.02

    inputs = [_ci("A", a_ret), _ci("B", b_ret)]
    weights, caps, mode = compute_composed_weights(
        inputs,
        observation_window_days=OBSERVATION_WINDOW_DAYS,
        template_cap_pct=Decimal(1),
    )

    assert mode == "risk_parity"
    # Tiny slack for float→Decimal coercion through `Decimal(str(w / total))`.
    assert abs(weights["A"] - Decimal(2) / Decimal(3)) < Decimal("1e-15")
    assert abs(weights["B"] - Decimal(1) / Decimal(3)) < Decimal("1e-15")
    assert caps == {}


def test_mixed_cohort_splits_nav_share_by_subset_size() -> None:
    """(c) cold-start + steady-state cohorts → each side gets `n/N` share.

    Setup: 1 cold-start candidate (short returns), 2 steady-state
    candidates with equal vol (long returns). NAV shares:
      cold cohort  = 1/3 of NAV → cold candidate gets 1/3
      warm cohort  = 2/3 of NAV → equal-vol risk-parity → each warm
                                 candidate gets 1/3
    Final: each of the three candidates ends at 1/3, which is the
    "would-have-been" cold-start answer — that's the correct behaviour
    for an equal-vol warm cohort: composition with cold-start collapses
    to 1/N only when the warm side is itself uniform. We verify the
    share-allocation step by feeding *uneven* warm vols below.
    """
    rng = np.random.default_rng(seed=12)
    short = rng.normal(loc=0.0, scale=0.02, size=5)
    base = rng.normal(loc=0.0, scale=0.01, size=30)
    warm_a = base / float(np.std(base, ddof=1)) * 0.01  # sigma 1 %
    warm_b = base / float(np.std(base, ddof=1)) * 0.02  # sigma 2 %

    inputs = [
        _ci("COLD", short),
        _ci("WARM_LO", warm_a),
        _ci("WARM_HI", warm_b),
    ]
    weights, caps, mode = compute_composed_weights(
        inputs,
        observation_window_days=OBSERVATION_WINDOW_DAYS,
        template_cap_pct=Decimal(1),
    )

    assert mode == "composed"
    assert caps == {}

    tol = Decimal("1e-15")
    # Cold candidate is the only member of its subset (1/1 inside cohort),
    # cohort share is 1/3 of NAV → COLD weight = 1/3.
    assert abs(weights["COLD"] - Decimal(1) / Decimal(3)) < tol

    # Warm subset share = 2/3 of NAV. Inside the warm subset, risk-parity
    # with 1:2 vol ratio gives 2/3 to the lower-vol candidate and 1/3
    # to the higher-vol candidate. So:
    #   WARM_LO = (2/3) * (2/3) = 4/9
    #   WARM_HI = (2/3) * (1/3) = 2/9
    assert abs(
        weights["WARM_LO"] - Decimal(2) / Decimal(3) * (Decimal(2) / Decimal(3))
    ) < tol
    assert abs(
        weights["WARM_HI"] - Decimal(2) / Decimal(3) * (Decimal(1) / Decimal(3))
    ) < tol

    # Total NAV deployed sums to 1.0 (no caps bound), within float coercion slack.
    assert abs(sum(weights.values()) - Decimal(1)) < tol
