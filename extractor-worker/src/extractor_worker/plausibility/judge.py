"""Judge driver — call the frontier with temp=0, parse the verdict, persist.

Three responsibilities:
  1. `judge_template(extracted, client)` — one frontier call at
     temperature=0 (deterministic per W2 decision #3b), returns
     JudgeResult. Parser errors are NOT retried; an unparseable
     judge response is itself a FLAG_FOR_OPERATOR signal (the model
     misbehaved on a simple prompt; operator should see it).
  2. `update_verdict(db, template_id, result)` — write the verdict
     to the existing `templates.judge_verdict` / `judge_rationale`
     columns (added in W1D4).
  3. `JUDGE_VERDICTS` constant — the three legal values, single
     source of truth for callers and tests.

The rubric must be non-empty before any call lands; we fail fast
with a clear message rather than silently auto-PASSing every template.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

import structlog
from icarus.db.database import DatabaseManager
from icarus.db.models import Template

from extractor_worker.frontier import FrontierClient
from extractor_worker.pipeline import ExtractedTemplate
from extractor_worker.plausibility.prompt import build_system_prompt, build_user_prompt
from extractor_worker.plausibility.rubric import REJECT_CRITERIA

_logger = structlog.get_logger(service="extractor.judge")

Verdict = Literal["PASS", "FLAG_FOR_OPERATOR", "REJECT"]
JUDGE_VERDICTS: tuple[Verdict, ...] = ("PASS", "FLAG_FOR_OPERATOR", "REJECT")

JUDGE_TEMPERATURE = 0.0  # W2 decision #3b — deterministic verdicts

# Same fenced-block pattern Phase A uses, scoped to one expected file.
_VERDICT_FENCE = re.compile(
    r"```json\s+verdict\.json\s*\n(?P<body>.*?)\n```",
    re.DOTALL,
)


class RubricEmptyError(RuntimeError):
    """The operator has not filled in REJECT_CRITERIA. Refuse to call
    the judge — an empty rubric means every template auto-PASSes."""


class JudgeParseError(RuntimeError):
    """Frontier response didn't yield a parseable verdict.json block."""


@dataclass(frozen=True)
class JudgeResult:
    """What the judge decided about one template."""

    template_id: str
    verdict: Verdict
    summary: str
    criteria_failed: tuple[int, ...]
    confidence: float
    input_tokens: int
    output_tokens: int

    @property
    def rationale(self) -> str:
        """Human-readable verdict line for the templates.judge_rationale
        column. Includes the criteria ids so the operator can look them
        up in rubric.py without opening the JSON column."""
        if self.criteria_failed:
            crit_str = "; failed criteria: " + ", ".join(
                f"#{i}" for i in self.criteria_failed
            )
        else:
            crit_str = ""
        return f"{self.summary} (confidence={self.confidence:.2f}){crit_str}"


def _ensure_rubric_filled() -> None:
    if not REJECT_CRITERIA:
        msg = (
            "REJECT_CRITERIA is empty. The operator must fill "
            "extractor_worker/plausibility/rubric.py before the judge runs. "
            "An empty rubric would silently auto-PASS every template, "
            "defeating Q8's veto-only-advisor design."
        )
        raise RubricEmptyError(msg)


def _parse_verdict(response_text: str, template_id: str) -> JudgeResult:
    """Pull the verdict.json out of the fenced block and validate it.

    Strict on shape: any deviation (missing field, wrong verdict
    value, criteria_failed id out of range) raises JudgeParseError.
    The caller turns parse failures into FLAG_FOR_OPERATOR with the
    parser diagnostic so a misbehaving judge surfaces to the operator
    rather than getting hidden as a retry."""
    m = _VERDICT_FENCE.search(response_text)
    if not m:
        msg = (
            f"no fenced verdict.json block found in response "
            f"(first 300 chars: {response_text[:300]!r})"
        )
        raise JudgeParseError(msg)

    try:
        data = json.loads(m.group("body"))
    except json.JSONDecodeError as e:
        msg = f"verdict.json failed to parse: {e}"
        raise JudgeParseError(msg) from e

    if not isinstance(data, dict):
        msg = f"verdict.json root must be an object, got {type(data).__name__}"
        raise JudgeParseError(msg)

    verdict = data.get("verdict")
    if verdict not in JUDGE_VERDICTS:
        msg = f"verdict must be one of {JUDGE_VERDICTS}, got {verdict!r}"
        raise JudgeParseError(msg)

    summary = data.get("summary", "")
    if not isinstance(summary, str) or not summary.strip():
        msg = "summary must be a non-empty string"
        raise JudgeParseError(msg)

    criteria_failed_raw = data.get("criteria_failed", [])
    if not isinstance(criteria_failed_raw, list) or not all(
        isinstance(i, int) for i in criteria_failed_raw
    ):
        msg = f"criteria_failed must be list[int], got {criteria_failed_raw!r}"
        raise JudgeParseError(msg)

    # Bounds-check criteria ids against the actual rubric so the model
    # can't cite "criterion #99" that doesn't exist.
    n_criteria = len(REJECT_CRITERIA)
    for i in criteria_failed_raw:
        if not (1 <= i <= n_criteria):
            msg = (
                f"criteria_failed contains id {i}, but rubric only has "
                f"{n_criteria} criteria (ids 1..{n_criteria})"
            )
            raise JudgeParseError(msg)

    if verdict == "PASS" and criteria_failed_raw:
        msg = f"verdict=PASS but criteria_failed is non-empty: {criteria_failed_raw}"
        raise JudgeParseError(msg)

    confidence = data.get("confidence")
    if not isinstance(confidence, (int, float)) or not (0.0 <= float(confidence) <= 1.0):
        msg = f"confidence must be a float in [0, 1], got {confidence!r}"
        raise JudgeParseError(msg)

    return JudgeResult(
        template_id=template_id,
        verdict=verdict,
        summary=summary,
        criteria_failed=tuple(criteria_failed_raw),
        confidence=float(confidence),
        input_tokens=0,  # filled by caller
        output_tokens=0,
    )


async def judge_template(
    extracted: ExtractedTemplate,
    *,
    client: FrontierClient,
) -> JudgeResult:
    """Run the LLM-as-judge plausibility check.

    Returns a JudgeResult. If the frontier response can't be parsed,
    returns a FLAG_FOR_OPERATOR with the parser diagnostic in the
    summary — surfacing a misbehaving judge to the operator rather
    than retrying or silently auto-PASSing.
    """
    _ensure_rubric_filled()

    system = build_system_prompt()
    user = build_user_prompt(
        template_id=extracted.template_id,
        manifest_yaml=extracted.manifest_yaml,
        parameter_rationale=extracted.parameter_rationale_md,
        evaluate_py=extracted.evaluate_py,
    )

    completion = await client.complete(
        system=system,
        user=user,
        temperature=JUDGE_TEMPERATURE,
    )

    try:
        result = _parse_verdict(completion.text, extracted.template_id)
    except JudgeParseError as e:
        _logger.warning(
            "judge_response_unparseable",
            template_id=extracted.template_id,
            error=str(e),
        )
        return JudgeResult(
            template_id=extracted.template_id,
            verdict="FLAG_FOR_OPERATOR",
            summary=f"judge response unparseable: {e}",
            criteria_failed=(),
            confidence=0.0,
            input_tokens=completion.input_tokens,
            output_tokens=completion.output_tokens,
        )

    # Re-create with the actual token counts (parser doesn't see the completion).
    result = JudgeResult(
        template_id=result.template_id,
        verdict=result.verdict,
        summary=result.summary,
        criteria_failed=result.criteria_failed,
        confidence=result.confidence,
        input_tokens=completion.input_tokens,
        output_tokens=completion.output_tokens,
    )
    _logger.info(
        "judge_verdict",
        template_id=result.template_id,
        verdict=result.verdict,
        confidence=result.confidence,
        criteria_failed=list(result.criteria_failed),
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
    )
    return result


def _update_verdict_sync(
    db: DatabaseManager,
    template_id: str,
    result: JudgeResult,
) -> None:
    with db.get_session() as session:
        row = session.query(Template).filter_by(template_id=template_id).one_or_none()
        if row is None:
            msg = f"no templates row for {template_id}; can't update verdict"
            raise RuntimeError(msg)
        row.judge_verdict = result.verdict
        row.judge_rationale = result.rationale
        row.updated_at = datetime.now(UTC)
        session.commit()


async def update_verdict(
    db: DatabaseManager,
    template_id: str,
    result: JudgeResult,
) -> None:
    await asyncio.to_thread(_update_verdict_sync, db, template_id, result)
