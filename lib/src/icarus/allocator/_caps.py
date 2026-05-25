"""Shared per-candidate + lake-level template cap enforcement.

Both `EqualWeightAllocator` and `RiskParityAllocator` end their pipeline
with the same two-stage clipping: per-candidate ``allocation_max_pct``,
then lake-level per-template cap (default 0.30 of NAV). The default
is configurable via ``ICARUS_ALLOCATOR_TEMPLATE_CAP_PCT``.

Returns NAV-fractions (not dollar amounts) so callers can do final
``nav * fraction`` rounding in their own Decimal precision.

Pure compute.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from decimal import Decimal

from icarus.allocator.types import CandidateInput

DEFAULT_TEMPLATE_CAP_PCT = Decimal("0.30")
"""Default lake-level per-template cap, NAV-fraction.

Blueprint §"allocator": "per-template allocation_max cap; lake-level
template cap". 30 % keeps any single template from dominating the book
even if it spawns many fertile candidates.
"""

ENV_TEMPLATE_CAP_PCT = "ICARUS_ALLOCATOR_TEMPLATE_CAP_PCT"
"""Override env var. Operators tune the cap without a code change."""


def resolve_template_cap_pct() -> Decimal:
    """Read the per-template cap, falling back to the default.

    Pure read of ``os.environ`` — kept here (not on a config object) so
    the cap is observable per-call rather than baked at process start.
    Misconfiguration (non-numeric, ≤ 0, > 1) falls back to the default
    rather than raising — the allocator must never refuse to allocate
    over a bad env var.
    """
    raw = os.environ.get(ENV_TEMPLATE_CAP_PCT)
    if raw is None:
        return DEFAULT_TEMPLATE_CAP_PCT
    try:
        cap = Decimal(raw)
    except (ValueError, ArithmeticError):
        return DEFAULT_TEMPLATE_CAP_PCT
    if cap <= Decimal(0) or cap > Decimal(1):
        return DEFAULT_TEMPLATE_CAP_PCT
    return cap


def apply_caps(
    raw_weights: Mapping[str, Decimal],
    inputs: Sequence[CandidateInput],
    *,
    template_cap_pct: Decimal | None = None,
) -> tuple[dict[str, Decimal], dict[str, Decimal]]:
    """Apply per-candidate then per-template caps to NAV-fraction weights.

    Two-stage clip:
      1. Per-candidate: ``min(weight, allocation_max_pct)``.
      2. Per-template: if ``sum(weights for template) > template_cap_pct``,
         scale every candidate in that template proportionally so the
         sum equals the cap.

    Returns ``(final_weights, template_caps_applied)`` where
    ``template_caps_applied`` maps ``template_id`` → the scaled-to
    fraction (== the cap value) for every template that triggered the
    per-template clip. Templates that did not hit the cap are omitted, so
    a caller reading the audit decision can read off "which caps bound
    this cycle" without re-deriving the math.

    Pure compute. Input ordering preserved in the output dict.
    """
    cap_pct = template_cap_pct if template_cap_pct is not None else resolve_template_cap_pct()
    by_id = {ci.candidate_id: ci for ci in inputs}

    # Stage 1: per-candidate cap.
    capped: dict[str, Decimal] = {}
    for cid, w in raw_weights.items():
        max_pct = by_id[cid].allocation_max_pct
        capped[cid] = min(w, max_pct)

    # Stage 2: per-template cap. Aggregate then scale.
    by_template: dict[str, list[str]] = {}
    for cid in capped:
        tmpl = by_id[cid].template_id
        by_template.setdefault(tmpl, []).append(cid)

    template_caps_applied: dict[str, Decimal] = {}
    for tmpl, cids in by_template.items():
        total = sum((capped[cid] for cid in cids), start=Decimal(0))
        if total > cap_pct:
            # Proportional scale so the sum lands exactly on the cap.
            scale = cap_pct / total
            for cid in cids:
                capped[cid] = capped[cid] * scale
            template_caps_applied[tmpl] = cap_pct

    return capped, template_caps_applied
