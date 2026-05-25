"""Judge prompt — assembled from the operator's rubric + the template artifacts.

Output contract: the model emits exactly one fenced JSON block tagged
`verdict.json` containing the verdict, a one-sentence summary, the list
of criteria the model judged failed (empty if PASS), and a confidence
score. Same fenced-block convention as the extractor prompt — one
parser, one grammar across the service.
"""

from __future__ import annotations

from extractor_worker.plausibility.rubric import REJECT_CRITERIA


def _rubric_block() -> str:
    """Render REJECT_CRITERIA as a numbered list. Each criterion is
    referenceable by number in the model's response."""
    if not REJECT_CRITERIA:
        # The judge module's `judge_template()` already refuses to run
        # in this state; we still raise here so the failure mode is
        # explicit if anyone calls the prompt builder directly.
        msg = (
            "REJECT_CRITERIA is empty — the operator has not filled out "
            "the rubric. See extractor_worker/plausibility/rubric.py."
        )
        raise ValueError(msg)
    return "\n".join(f"{i}. {c}" for i, c in enumerate(REJECT_CRITERIA, start=1))


SYSTEM_PROMPT = """\
You are reviewing a freshly extracted DeFi trading strategy template for
plausibility. You are a VETO-ONLY ADVISOR: your job is to flag bad
templates, not to approve good ones. A "PASS" verdict means you found
nothing disqualifying — it does not authorize live capital.

You will be shown three files extracted from a research source:
  - manifest.yaml: the strategy spine
  - parameter_rationale.md: the extractor's justification
  - evaluate.py: the strategy logic

You will emit exactly one verdict in the format below.

# REJECT CRITERIA (operator-authored)

If any criterion below CLEARLY applies, your verdict is REJECT.
If you are UNCERTAIN whether a criterion applies, your verdict is
FLAG_FOR_OPERATOR. If you can confidently rule out every criterion,
your verdict is PASS.

{rubric_block}

# RESPONSE FORMAT (STRICT)

Emit exactly one fenced code block, no preamble, no postscript:

```json verdict.json
{{
  "verdict": "PASS" | "FLAG_FOR_OPERATOR" | "REJECT",
  "summary": "one sentence (under 200 chars) explaining the verdict",
  "criteria_failed": [<numeric ids of criteria you matched, e.g. 2, 5>],
  "confidence": <float in [0, 1]; 1.0 = certain>
}}
```

If verdict is PASS, `criteria_failed` MUST be `[]`.
If verdict is REJECT or FLAG_FOR_OPERATOR, `criteria_failed` SHOULD be
non-empty; if non-empty, every id must appear in the rubric above.

Tie-breaking: when in doubt between PASS and FLAG_FOR_OPERATOR, prefer
FLAG. When in doubt between FLAG and REJECT, prefer FLAG. The operator
reviews flagged templates; rejected templates need re-extraction.
"""


USER_PROMPT_TEMPLATE = """\
Review this template for plausibility.

template_id: {template_id}

# manifest.yaml
```yaml
{manifest_yaml}
```

# parameter_rationale.md
```markdown
{parameter_rationale}
```

# evaluate.py
```python
{evaluate_py}
```
"""


def build_system_prompt() -> str:
    return SYSTEM_PROMPT.format(rubric_block=_rubric_block())


def build_user_prompt(
    *,
    template_id: str,
    manifest_yaml: str,
    parameter_rationale: str,
    evaluate_py: str,
) -> str:
    return USER_PROMPT_TEMPLATE.format(
        template_id=template_id,
        manifest_yaml=manifest_yaml,
        parameter_rationale=parameter_rationale,
        evaluate_py=evaluate_py,
    )
