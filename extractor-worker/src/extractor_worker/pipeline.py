"""Agentic extraction pipeline — frontier → parse → validate → repair loop.

W2 decision #1b: when validation fails we re-prompt the model with the
specific error and let it repair, up to MAX_ATTEMPTS times total. Compared
to single-shot (#1a), this trades cost for quality on first-pass-fail
sources (long papers, unusual notation, ambiguous parameter ranges).

Validation layers, in order:
  1. Parse four code blocks from the response. Missing/extra block →
     repair with format reminder.
  2. Pydantic validate manifest.yaml. Schema error → repair with
     diagnostic.
  3. AST-lint evaluate.py. Lint error → repair with line + code +
     message.
  4. Parse smoke_test.py via ast.parse. Syntax error → repair.

The pipeline does NOT execute the smoke test. That happens in
`extractor_worker.writer` after the files land on disk, because executing
untrusted code requires the same registry-loader sandboxing the DSL
runtime already provides — duplicating it here would diverge two
sandboxes.

Cost ceiling: extraction is capped at MAX_ATTEMPTS frontier calls per
job. The structured log emits cumulative input/output tokens on
ExtractionResult so the operator can audit spend per template.
"""

from __future__ import annotations

import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import structlog
import yaml
from icarus.dsl.linter import lint_evaluate_py
from icarus.dsl.manifest import TemplateManifest
from pydantic import ValidationError

from extractor_worker.frontier import Completion, FrontierClient
from extractor_worker.prompt import (
    build_repair_prompt,
    build_system_prompt,
    build_user_prompt,
)

_logger = structlog.get_logger(service="extractor.pipeline")

MAX_ATTEMPTS = 3

# Matches a fenced block opened with ```{lang} {filename}\n...content...\n```
# Tolerant of trailing whitespace on the fence line and Unix/Windows line
# endings. Non-greedy body so multiple blocks don't merge.
_FENCE_PATTERN = re.compile(
    r"```(?P<lang>[a-zA-Z]+)\s+(?P<filename>[\w./_-]+)\s*\n"
    r"(?P<body>.*?)\n```",
    re.DOTALL,
)

REQUIRED_FILES = ("manifest.yaml", "evaluate.py", "smoke_test.py", "parameter_rationale.md")


class ExtractionFailedError(RuntimeError):
    """All MAX_ATTEMPTS exhausted without a valid 4-file template."""


class ParseError(RuntimeError):
    """The frontier response didn't yield four parseable fenced blocks."""


@dataclass(frozen=True)
class ExtractedTemplate:
    """The validated 4-file payload from a successful extraction.

    `manifest` is the pydantic-validated model. The raw `manifest.yaml`
    text is also kept so the templates Postgres row preserves the exact
    bytes the LLM produced (audit trail)."""

    template_id: str
    manifest: TemplateManifest
    manifest_yaml: str
    evaluate_py: str
    smoke_test_py: str
    parameter_rationale_md: str
    attempts: int
    total_input_tokens: int
    total_output_tokens: int


@dataclass
class _Attempt:
    """Mutable per-attempt state. Used internally by the repair loop."""

    response: Completion
    files: dict[str, str] = field(default_factory=dict)
    error_class: str | None = None
    error_message: str | None = None


def _parse_four_files(response_text: str) -> dict[str, str]:
    """Pull the four expected files out of fenced code blocks.

    Raises ParseError if any required file is missing, duplicate, or
    if there are extra unrecognized files.
    """
    found: dict[str, str] = {}
    for match in _FENCE_PATTERN.finditer(response_text):
        name = match.group("filename")
        body = match.group("body")
        if name in found:
            msg = f"duplicate file in response: {name}"
            raise ParseError(msg)
        found[name] = body

    missing = [f for f in REQUIRED_FILES if f not in found]
    if missing:
        msg = (
            f"missing required files: {missing}. "
            f"Got: {sorted(found.keys())}. "
            f"Expected exactly: {list(REQUIRED_FILES)}."
        )
        raise ParseError(msg)

    extra = [name for name in found if name not in REQUIRED_FILES]
    if extra:
        msg = f"unexpected extra files: {extra}. Emit only: {list(REQUIRED_FILES)}."
        raise ParseError(msg)

    return found


def _validate_manifest(yaml_text: str, expected_template_id: str) -> TemplateManifest:
    """Parse + pydantic-validate manifest.yaml. The expected_template_id
    invariant guards against the LLM picking a different id than the
    operator-requested one."""
    try:
        parsed = yaml.safe_load(yaml_text)
    except yaml.YAMLError as e:
        msg = f"YAML parse failed: {e}"
        raise ValueError(msg) from e

    if not isinstance(parsed, dict):
        msg = f"manifest.yaml root must be a mapping, got {type(parsed).__name__}"
        raise ValueError(msg)

    manifest = TemplateManifest.model_validate(parsed)
    if manifest.id != expected_template_id:
        msg = (
            f"manifest.id is '{manifest.id}' but operator requested "
            f"'{expected_template_id}'. The id is operator-assigned, not LLM-chosen."
        )
        raise ValueError(msg)
    return manifest


def _validate_evaluate(evaluate_py: str) -> None:
    """AST-lint evaluate.py by writing it to a temp file (the linter
    accepts a Path). Raises ValueError summarising the first error if
    any errors fired."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(evaluate_py)
        tmp_path = Path(f.name)
    try:
        report = lint_evaluate_py(tmp_path)
    finally:
        tmp_path.unlink(missing_ok=True)

    if not report.ok:
        # Concatenate every error so the repair prompt sees the full picture.
        # Cheaper to fix all errors in one repair than chain three repairs.
        lines = [
            f"  {i.code} L{i.line}:{i.col} — {i.message}" for i in report.errors
        ]
        msg = "evaluate.py failed AST lint:\n" + "\n".join(lines)
        raise ValueError(msg)


def _validate_smoke_test_syntax(smoke_test_py: str) -> None:
    """Confirm smoke_test.py parses as Python. Execution is deferred to
    the writer (after files land on disk, behind the registry loader's
    smoke-test runner)."""
    import ast

    try:
        ast.parse(smoke_test_py)
    except SyntaxError as e:
        msg = f"smoke_test.py SyntaxError L{e.lineno}: {e.msg}"
        raise ValueError(msg) from e


def _validate_all(files: dict[str, str], expected_template_id: str) -> TemplateManifest:
    """Run all validators in order. Raises on the first failure so the
    repair prompt is laser-focused on one diagnostic at a time."""
    manifest = _validate_manifest(files["manifest.yaml"], expected_template_id)
    _validate_evaluate(files["evaluate.py"])
    _validate_smoke_test_syntax(files["smoke_test.py"])
    if not files["parameter_rationale.md"].strip():
        msg = "parameter_rationale.md is empty; must justify each parameter range"
        raise ValueError(msg)
    return manifest


async def extract_with_repair(
    *,
    client: FrontierClient,
    template_id: str,
    source_type: str,
    source_ref: str,
    source_text: str,
    chain_hint: str | None = None,
    max_attempts: int = MAX_ATTEMPTS,
) -> ExtractedTemplate:
    """Single-template extraction with agentic repair (W2 decision #1b).

    Cools the temperature on repair attempts so the model varies less
    around the diagnostic it's responding to.
    """
    system = build_system_prompt()
    attempts: list[_Attempt] = []

    for attempt_idx in range(1, max_attempts + 1):
        if attempt_idx == 1:
            user = build_user_prompt(
                template_id=template_id,
                chain_hint=chain_hint,
                source_type=source_type,
                source_ref=source_ref,
                source_text=source_text,
            )
            temperature = 1.0
        else:
            prior = attempts[-1]
            assert prior.error_class is not None and prior.error_message is not None
            user = build_repair_prompt(
                previous_response=prior.response.text,
                error_class=prior.error_class,
                error_message=prior.error_message,
            )
            temperature = 0.3  # cool down — let the model converge on the fix

        response = await client.complete(
            system=system,
            user=user,
            temperature=temperature,
        )
        attempt = _Attempt(response=response)
        attempts.append(attempt)

        try:
            attempt.files = _parse_four_files(response.text)
            manifest = _validate_all(attempt.files, expected_template_id=template_id)
        except (ParseError, ValueError, ValidationError) as e:
            attempt.error_class = type(e).__name__
            attempt.error_message = str(e)
            _logger.warning(
                "extraction_attempt_failed",
                template_id=template_id,
                attempt=attempt_idx,
                max_attempts=max_attempts,
                error_class=attempt.error_class,
                error_message=attempt.error_message,
            )
            continue

        # Success.
        total_in = sum(a.response.input_tokens for a in attempts)
        total_out = sum(a.response.output_tokens for a in attempts)
        _logger.info(
            "extraction_succeeded",
            template_id=template_id,
            attempts=attempt_idx,
            total_input_tokens=total_in,
            total_output_tokens=total_out,
        )
        return ExtractedTemplate(
            template_id=template_id,
            manifest=manifest,
            manifest_yaml=attempt.files["manifest.yaml"],
            evaluate_py=attempt.files["evaluate.py"],
            smoke_test_py=attempt.files["smoke_test.py"],
            parameter_rationale_md=attempt.files["parameter_rationale.md"],
            attempts=attempt_idx,
            total_input_tokens=total_in,
            total_output_tokens=total_out,
        )

    last = attempts[-1]
    msg = (
        f"extraction failed after {max_attempts} attempts. "
        f"Last error: {last.error_class}: {last.error_message}"
    )
    _logger.error(
        "extraction_exhausted",
        template_id=template_id,
        attempts=max_attempts,
        last_error_class=last.error_class,
        last_error_message=last.error_message,
    )
    raise ExtractionFailedError(msg)
