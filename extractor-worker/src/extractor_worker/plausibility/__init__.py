"""LLM-as-judge plausibility check (Q8) — veto-only advisor.

Reads a template's manifest + parameter_rationale + evaluate.py and
emits one of {PASS, FLAG_FOR_OPERATOR, REJECT}. Default verdict for
non-trivial templates is FLAG_FOR_OPERATOR per the blueprint.

This is a VETO-ONLY ADVISOR (blueprint §"LLM placements"): a PASS does
NOT mean the template enters the search queue automatically; it means
the judge couldn't find anything disqualifying. The registry loader
(W3+) gates queue entry on `judge_verdict = 'PASS'`, but live
promotion still requires the operator. The judge never approves; it
only fails to reject.

W2 decision #3b: same Anthropic model as the extractor, but called
with temperature=0 so the verdict is deterministic across reruns. The
diversity argument for cross-family judging (Zheng et al. 2023) is
real, but #2c already commits us to a single API. Same-model judging
with explicit rubric is the next-best mitigation.
"""

from extractor_worker.plausibility.judge import (
    JUDGE_VERDICTS,
    JudgeParseError,
    JudgeResult,
    RubricEmptyError,
    judge_template,
    update_verdict,
)
from extractor_worker.plausibility.rubric import REJECT_CRITERIA

__all__ = [
    "JUDGE_VERDICTS",
    "REJECT_CRITERIA",
    "JudgeParseError",
    "JudgeResult",
    "RubricEmptyError",
    "judge_template",
    "update_verdict",
]
