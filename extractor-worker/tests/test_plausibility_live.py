"""Live end-to-end judge test — calls real Anthropic, requires the
rubric to be filled in.

Skipped if either:
  - ANTHROPIC_API_KEY is not set, OR
  - REJECT_CRITERIA is empty (operator hasn't filled the rubric).

The second skip condition is important: a CI run shouldn't fail just
because the rubric is empty — that's an operator decision, not a code
defect. The unit test in test_plausibility_unit.py already asserts
the fail-fast behavior; this test asserts the live API contract once
the operator has opted in.
"""

from __future__ import annotations

import os

import pytest
from extractor_worker.frontier import AnthropicClient
from extractor_worker.pipeline import ExtractedTemplate, extract_with_repair
from extractor_worker.plausibility import JUDGE_VERDICTS, judge_template
from extractor_worker.plausibility.rubric import REJECT_CRITERIA

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not os.environ.get("ANTHROPIC_API_KEY"),
        reason="ANTHROPIC_API_KEY not set; live tests skipped",
    ),
    pytest.mark.skipif(
        not REJECT_CRITERIA,
        reason="REJECT_CRITERIA empty; operator must fill plausibility/rubric.py",
    ),
]


# A small, plausibly-good template. The judge should NOT REJECT this
# (no obvious failure modes); FLAG_FOR_OPERATOR is acceptable per the
# blueprint's default policy for non-trivial templates.
_GOOD_SOURCE = """\
Title: USDC Supply APY Rotation
Strategy: Supply USDC to the highest-APY pool on Base among Aave V3 and
Moonwell. Rotate when best APY exceeds current by a threshold.
Parameters:
  - apy_threshold: 0.5% to 5%
  - min_pool_tvl_usd: $500K to $5M
"""


async def test_judge_does_not_reject_plausible_template():
    """End-to-end: extract a plausible template, then judge it.

    Because the judge's verdict is the operator's policy (not a code
    invariant), we only assert: verdict is one of the three legal
    values AND it's NOT REJECT on this benign source. PASS or
    FLAG_FOR_OPERATOR are both acceptable.
    """
    client = AnthropicClient()
    extracted: ExtractedTemplate = await extract_with_repair(
        client=client,
        template_id="TEST-JUDGE-001",
        source_type="blog_url",
        source_ref="https://example.com/test-judge-001",
        source_text=_GOOD_SOURCE,
        chain_hint="base",
    )

    result = await judge_template(extracted, client=client)

    assert result.verdict in JUDGE_VERDICTS
    assert result.verdict != "REJECT", (
        f"judge REJECTed a benign APY rotation template — "
        f"summary: {result.summary!r}; criteria_failed: {result.criteria_failed}. "
        f"This usually means the rubric is over-aggressive; review rubric.py."
    )
    assert 0.0 <= result.confidence <= 1.0
    assert result.summary.strip()
    assert result.input_tokens > 0
    assert result.output_tokens > 0
