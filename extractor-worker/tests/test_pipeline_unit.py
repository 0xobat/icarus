"""Pipeline unit tests — parser, validators, repair-loop wiring.

These tests do NOT call the frontier API. They exercise the
deterministic parts of the pipeline (response parser + per-validator
behavior + repair-loop control flow) by substituting a stub
FrontierClient that returns pre-canned text. This is not "mocking the
API" (which W2 decision #5 forbids) — it's exercising the response
*plumbing* with fixed inputs. The live e2e test in
test_pipeline_live.py is what asserts the real API produces something
the plumbing can validate end-to-end.
"""

from __future__ import annotations

import textwrap
from dataclasses import dataclass

import pytest
from extractor_worker.frontier import Completion, FrontierClient
from extractor_worker.pipeline import (
    ExtractionFailedError,
    ParseError,
    _parse_four_files,
    _validate_evaluate,
    _validate_manifest,
    extract_with_repair,
)

# ── parser ──────────────────────────────────────────────────────────


def test_parse_four_files_happy_path():
    text = textwrap.dedent("""
    ```yaml manifest.yaml
    id: TEST-001
    ```

    ```python evaluate.py
    def evaluate(): pass
    ```

    ```python smoke_test.py
    def test_smoke(): pass
    ```

    ```markdown parameter_rationale.md
    rationale here
    ```
    """)
    files = _parse_four_files(text)
    assert set(files) == {"manifest.yaml", "evaluate.py", "smoke_test.py", "parameter_rationale.md"}
    assert "id: TEST-001" in files["manifest.yaml"]


def test_parse_four_files_missing_one_raises():
    text = textwrap.dedent("""
    ```yaml manifest.yaml
    id: TEST-001
    ```
    """)
    with pytest.raises(ParseError, match="missing required files"):
        _parse_four_files(text)


def test_parse_four_files_extra_file_raises():
    text = textwrap.dedent("""
    ```yaml manifest.yaml
    id: TEST-001
    ```
    ```python evaluate.py
    def evaluate(): pass
    ```
    ```python smoke_test.py
    def test_smoke(): pass
    ```
    ```markdown parameter_rationale.md
    rationale
    ```
    ```python bonus.py
    print("hi")
    ```
    """)
    with pytest.raises(ParseError, match="unexpected extra files"):
        _parse_four_files(text)


# ── manifest validator ──────────────────────────────────────────────


def test_validate_manifest_rejects_wrong_id():
    yaml_text = textwrap.dedent("""
    id: WRONG-999
    semver: "0.1.0"
    title: T
    chain: base
    protocol: aave_v3
    asset_universe: [USDC]
    sources:
      - type: blog_url
        ref: https://example.com
    allocation_max: "0.5"
    risk_profile: low
    sizing: risk_parity
    params:
      x:
        kind: grid
        values: ["1"]
    expected_metrics:
      sharpe_min: "0.5"
      max_dd_max: "0.1"
    """)
    with pytest.raises(ValueError, match="operator requested 'TEST-001'"):
        _validate_manifest(yaml_text, expected_template_id="TEST-001")


# ── evaluate.py linter integration ──────────────────────────────────


def test_validate_evaluate_rejects_forbidden_import():
    src = textwrap.dedent("""
    import os
    def evaluate(params, market_data, portfolio_state):
        return None
    """)
    with pytest.raises(ValueError, match="failed AST lint"):
        _validate_evaluate(src)


# ── repair loop control flow ────────────────────────────────────────


@dataclass
class _StubClient:
    """Returns pre-canned completions in sequence. Used to assert repair
    loop logic without paying for real API calls."""

    responses: list[str]
    calls: int = 0

    async def complete(self, *, system, user, temperature=1.0, max_tokens=8192) -> Completion:
        idx = self.calls
        self.calls += 1
        if idx >= len(self.responses):
            msg = f"stub exhausted after {idx} calls"
            raise AssertionError(msg)
        return Completion(
            text=self.responses[idx],
            model="stub",
            input_tokens=100,
            output_tokens=200,
        )


_BAD_RESPONSE = "no fenced blocks here at all"


async def test_extract_with_repair_exhausts_attempts():
    # All attempts return garbage → ParseError every time → ExtractionFailedError.
    client: FrontierClient = _StubClient(responses=[_BAD_RESPONSE] * 3)
    with pytest.raises(ExtractionFailedError, match="after 3 attempts"):
        await extract_with_repair(
            client=client,
            template_id="TEST-001",
            source_type="blog_url",
            source_ref="https://example.com",
            source_text="dummy source",
            max_attempts=3,
        )
    assert client.calls == 3  # type: ignore[attr-defined]
