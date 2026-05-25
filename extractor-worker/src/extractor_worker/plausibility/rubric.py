"""Operator-owned rubric — the criteria the LLM-as-judge applies.

This file is intentionally short. It defines THE POLICY that decides
when a freshly extracted template gets REJECT'd outright vs.
FLAG_FOR_OPERATOR'd for manual review.

The rubric is rendered verbatim into the judge prompt as a numbered
list. The model is told: "if any criterion below clearly applies,
verdict is REJECT; if uncertain or partially applies, verdict is
FLAG_FOR_OPERATOR; if none apply, verdict is PASS."

# WHY THIS LIVES IN ITS OWN FILE

The rubric is the *only* policy knob between "an LLM read your paper"
and "the bot starts searching parameter space." Everything else —
prompt scaffolding, output parsing, DB writes — is infrastructure.
The rubric is the operator's voice. Editing this file does NOT
require editing the judge module or re-running the judge prompt
template; the next judge run picks up the new criteria automatically.

# RUBRIC AXES SUGGESTED BY THE BLUEPRINT (Q8)

  - parameter ranges contradict the source paper (e.g. paper says
    "tested 5-15%", manifest says "low=0, high=1")
  - evaluate.py logic doesn't match what parameter_rationale.md claims
    (e.g. rationale says "exit on funding < 0", code never reads funding)
  - manifest.expected_metrics are physically implausible (e.g. Sharpe > 5)
  - asset_universe references an asset that doesn't exist on the
    declared chain (e.g. USDbC on Solana)
  - the strategy requires data the MarketSnapshot doesn't carry
    (the extractor should have caught this; if it slipped through,
    REJECT)
  - parameter_rationale.md is generic boilerplate that doesn't cite
    the source (signal that the extractor hallucinated the rationale)
"""

from __future__ import annotations

# ─────────────────── OPERATOR INPUT REQUIRED ───────────────────
#
# Fill REJECT_CRITERIA with 5-10 short, declarative criteria. Each
# string is rendered as a numbered bullet in the judge prompt, so
# write them as direct statements the LLM can pattern-match against.
#
# Style tips:
#   - Lead with the failure mode, not the principle.
#     GOOD: "Parameter ranges in manifest.yaml don't match values
#            cited in parameter_rationale.md."
#     BAD:  "Templates should be internally consistent."
#   - Keep each under ~25 words.
#   - Be specific to DeFi quant strategies — generic safety rules
#     (e.g. "no malicious code") are already enforced by the AST
#     linter; the judge's job is *quality*, not safety.
#   - Phrase as a binary check the LLM can apply without context the
#     judge prompt doesn't supply.
#
# Example shape (replace with your actual rubric):
# REJECT_CRITERIA: list[str] = [
#     "Parameter ranges in manifest.yaml contradict numbers cited in the source paper.",
#     "evaluate.py reads parameters that manifest.yaml does not declare, or vice versa.",
#     "expected_metrics asserts Sharpe > 5 or annual return > 200% (physically implausible).",
#     "asset_universe lists assets that don't exist on the declared chain.",
#     "parameter_rationale.md is generic boilerplate with no citations to the source.",
# ]

REJECT_CRITERIA: list[str] = [
    (
        "Parameter ranges in manifest.yaml contradict numbers cited in the "
        "source paper or parameter_rationale.md."
    ),
    (
        "evaluate.py reads parameters that manifest.yaml does not declare, or "
        "manifest declares parameters that evaluate.py never reads."
    ),
    (
        "expected_metrics asserts Sharpe > 5 or expected_annual_return_pct > "
        "2.0 (physically implausible for any DeFi strategy at v1 scale)."
    ),
    (
        "asset_universe lists assets that don't exist on the declared chain "
        "(e.g. USDbC on Solana, JitoSOL on Base)."
    ),
    (
        "parameter_rationale.md is generic boilerplate with no specific "
        "citations to the source — every parameter justification should "
        "reference observable source content."
    ),
    (
        "evaluate.py requires data fields that MarketSnapshot, "
        "PortfolioSnapshot, or Decision do not carry (the strategy is "
        "structurally incompatible with the v2 contract)."
    ),
]
