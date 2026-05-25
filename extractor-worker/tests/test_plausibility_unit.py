"""Deterministic tests for judge parsing + rubric guard.

These do NOT call the frontier API. They exercise:
  - _parse_verdict's shape and bounds validation
  - rubric-empty fail-fast
  - JudgeResult.rationale formatting (what lands in templates.judge_rationale)

The live judge test (against real Anthropic) lives in
test_plausibility_live.py and runs only when ANTHROPIC_API_KEY is set.
"""

from __future__ import annotations

import textwrap
from unittest.mock import patch

import pytest
from extractor_worker.plausibility import judge as judge_module
from extractor_worker.plausibility.judge import (
    JUDGE_VERDICTS,
    JudgeParseError,
    JudgeResult,
    RubricEmptyError,
    _ensure_rubric_filled,
    _parse_verdict,
)

# All parse tests run against a 3-criterion rubric so id bounds-checks
# have something to assert against without depending on the operator's
# real rubric content.
_TEST_RUBRIC = ["one", "two", "three"]


@pytest.fixture(autouse=True)
def patch_rubric():
    """Substitute a 3-item rubric for every test in this module.

    We patch both binding sites — `rubric` module's exported list AND
    `judge` module's imported name — because `judge.py` does
    `from extractor_worker.plausibility.rubric import REJECT_CRITERIA`,
    which captures the list object by reference at import time.
    """
    with (
        patch.object(judge_module, "REJECT_CRITERIA", _TEST_RUBRIC),
        patch("extractor_worker.plausibility.rubric.REJECT_CRITERIA", _TEST_RUBRIC),
    ):
        yield


# ── verdict shape ───────────────────────────────────────────────────


def test_constants_three_verdicts():
    assert JUDGE_VERDICTS == ("PASS", "FLAG_FOR_OPERATOR", "REJECT")


# ── parser happy paths ──────────────────────────────────────────────


_PASS_RESPONSE = textwrap.dedent("""
```json verdict.json
{
  "verdict": "PASS",
  "summary": "All rubric criteria ruled out.",
  "criteria_failed": [],
  "confidence": 0.85
}
```
""")


_REJECT_RESPONSE = textwrap.dedent("""
```json verdict.json
{
  "verdict": "REJECT",
  "summary": "Parameter ranges contradict cited source.",
  "criteria_failed": [1, 2],
  "confidence": 0.92
}
```
""")


def test_parse_pass():
    r = _parse_verdict(_PASS_RESPONSE, "TEST-001")
    assert r.verdict == "PASS"
    assert r.criteria_failed == ()
    assert r.confidence == 0.85
    assert r.template_id == "TEST-001"


def test_parse_reject_with_criteria():
    r = _parse_verdict(_REJECT_RESPONSE, "TEST-001")
    assert r.verdict == "REJECT"
    assert r.criteria_failed == (1, 2)


# ── parser invariants (bounds + cross-field) ────────────────────────


def test_parse_rejects_pass_with_criteria():
    bad = textwrap.dedent("""
    ```json verdict.json
    {
      "verdict": "PASS",
      "summary": "fine",
      "criteria_failed": [1],
      "confidence": 0.9
    }
    ```
    """)
    with pytest.raises(JudgeParseError, match="PASS but criteria_failed is non-empty"):
        _parse_verdict(bad, "TEST-001")


def test_parse_rejects_out_of_range_criterion():
    # rubric has 3 items; id 99 must be rejected to block hallucinated criteria.
    bad = textwrap.dedent("""
    ```json verdict.json
    {
      "verdict": "REJECT",
      "summary": "x",
      "criteria_failed": [99],
      "confidence": 0.9
    }
    ```
    """)
    with pytest.raises(JudgeParseError, match="rubric only has 3 criteria"):
        _parse_verdict(bad, "TEST-001")


def test_parse_rejects_unknown_verdict():
    bad = textwrap.dedent("""
    ```json verdict.json
    {
      "verdict": "MAYBE",
      "summary": "x",
      "criteria_failed": [],
      "confidence": 0.5
    }
    ```
    """)
    with pytest.raises(JudgeParseError, match="verdict must be one of"):
        _parse_verdict(bad, "TEST-001")


def test_parse_rejects_confidence_out_of_range():
    bad = textwrap.dedent("""
    ```json verdict.json
    {
      "verdict": "PASS",
      "summary": "x",
      "criteria_failed": [],
      "confidence": 2.5
    }
    ```
    """)
    with pytest.raises(JudgeParseError, match="confidence must be"):
        _parse_verdict(bad, "TEST-001")


def test_parse_rejects_missing_fence():
    with pytest.raises(JudgeParseError, match=r"no fenced verdict\.json block"):
        _parse_verdict("just prose, no fenced block at all", "TEST-001")


# ── rubric guard ────────────────────────────────────────────────────


def test_rubric_empty_fails_fast():
    with (
        patch.object(judge_module, "REJECT_CRITERIA", []),
        patch("extractor_worker.plausibility.rubric.REJECT_CRITERIA", []),
    ):
        with pytest.raises(RubricEmptyError, match=r"rubric\.py"):
            _ensure_rubric_filled()


# ── JudgeResult.rationale formatting ────────────────────────────────


def test_rationale_includes_criteria_ids():
    r = JudgeResult(
        template_id="TEST-001",
        verdict="REJECT",
        summary="contradicts source",
        criteria_failed=(1, 3),
        confidence=0.9,
        input_tokens=0,
        output_tokens=0,
    )
    assert "#1" in r.rationale
    assert "#3" in r.rationale
    assert "confidence=0.90" in r.rationale


def test_rationale_omits_criteria_when_pass():
    r = JudgeResult(
        template_id="TEST-001",
        verdict="PASS",
        summary="fine",
        criteria_failed=(),
        confidence=1.0,
        input_tokens=0,
        output_tokens=0,
    )
    assert "failed criteria" not in r.rationale
