"""Unit tests for the steady-state risk-parity allocator."""

from __future__ import annotations

from decimal import Decimal

import numpy as np
from icarus.allocator.risk_parity import compute_risk_parity_weights
from icarus.allocator.types import CandidateInput


def _ci(
    cid: str,
    returns: np.ndarray,
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
        recent_returns=CandidateInput.coerce_returns(returns),
    )


def test_equal_vol_yields_equal_weight() -> None:
    """(a) two candidates with equal realised vol → equal weights.

    Caps disabled (template_cap_pct=1.0) so the test isolates the
    inverse-vol math; cap behaviour gets its own dedicated test below.
    """
    rng = np.random.default_rng(seed=1)
    sigma = 0.02
    a_ret = rng.normal(loc=0.001, scale=sigma, size=60)
    b_ret = rng.normal(loc=0.001, scale=sigma, size=60)
    # Re-scale to force exactly-equal sample std so the assertion is
    # deterministic regardless of finite-sample noise.
    a_ret = a_ret / float(np.std(a_ret, ddof=1)) * sigma
    b_ret = b_ret / float(np.std(b_ret, ddof=1)) * sigma

    inputs = [
        _ci("A", returns=a_ret),
        _ci("B", returns=b_ret),
    ]
    weights, _caps, fell_back = compute_risk_parity_weights(
        inputs, template_cap_pct=Decimal(1)
    )

    assert not fell_back
    assert weights["A"] == weights["B"]
    assert sum(weights.values()) == Decimal(1)
    assert _caps == {}


def test_double_vol_candidate_gets_half_weight() -> None:
    """(b) candidate with 2x volatility gets half the weight of the other."""
    rng = np.random.default_rng(seed=2)
    base = rng.normal(loc=0.0, scale=0.01, size=60)
    a_ret = base / float(np.std(base, ddof=1)) * 0.01  # sigma = 1 %
    b_ret = base / float(np.std(base, ddof=1)) * 0.02  # sigma = 2 %

    inputs = [
        _ci("A", returns=a_ret),
        _ci("B", returns=b_ret),
    ]
    weights, _caps, fell_back = compute_risk_parity_weights(
        inputs, template_cap_pct=Decimal(1)
    )

    assert not fell_back
    # Inverse-vol: w_A is proportional to 1/sigma_A, w_B is proportional to
    # 1/sigma_B. sigma_B = 2 * sigma_A so w_A = 2 * w_B and w_A = 2/3, w_B = 1/3.
    # Allow tiny float→Decimal slack from the Decimal(str(...)) conversion.
    assert abs(weights["A"] - Decimal(2) / Decimal(3)) < Decimal("1e-15")
    assert abs(weights["B"] - Decimal(1) / Decimal(3)) < Decimal("1e-15")


def test_zero_variance_triggers_equal_weight_fallback() -> None:
    """(c) any candidate with zero-variance returns → equal-weight fallback.

    W3 pattern: degenerate variance fails closed (to the documented
    robust naive allocator) rather than producing infinite inverse-vol
    weights. The fallback flag surfaces so commentary records it.
    """
    rng = np.random.default_rng(seed=3)
    healthy = rng.normal(loc=0.0, scale=0.02, size=60)
    zero_var = np.full(60, 0.001)  # constant series → std = 0

    inputs = [
        _ci("A", returns=healthy),
        _ci("B", returns=zero_var),
    ]
    weights, caps, fell_back = compute_risk_parity_weights(
        inputs, template_cap_pct=Decimal(1)
    )

    assert fell_back is True
    # Cohort-wide fallback: each candidate gets 1/N = 0.5.
    assert weights == {"A": Decimal("0.5"), "B": Decimal("0.5")}
    assert caps == {}


def test_template_level_cap_binds_when_one_template_dominates() -> None:
    """(d) 4 candidates of same template → per-template cap (0.30) clips total.

    Without the cap, four equal-vol same-template candidates would sum
    to 1.0 in template T1. With the default 0.30 cap, all four scale
    down proportionally so the template aggregate = 0.30.
    """
    rng = np.random.default_rng(seed=4)
    base = rng.normal(loc=0.0, scale=0.01, size=60)
    same_vol = base / float(np.std(base, ddof=1)) * 0.01

    inputs = [
        _ci(c, template_id="T1", returns=same_vol)
        for c in ("A", "B", "C", "D")
    ]
    weights, caps, fell_back = compute_risk_parity_weights(inputs)

    assert not fell_back
    # Each candidate ends at 0.30 / 4 = 0.075.
    expected = Decimal("0.30") / Decimal(4)
    for cid in ("A", "B", "C", "D"):
        assert weights[cid] == expected
    # Cap exposed in audit hook.
    assert caps == {"T1": Decimal("0.30")}
    # Sum equals exactly the per-template cap.
    assert sum(weights.values()) == Decimal("0.30")
