"""Extractor Protocol — paper/blog → strategy template.

Calls a frontier LLM (Anthropic / OpenAI) to produce the four-file
template directory: manifest.yaml + evaluate.py + smoke_test.py +
parameter_rationale.md.

The Protocol is async because the LLM call is network-bound and the worker
should not block the event loop while waiting. Concurrent extractions are
fine — the worker pool atomic-claim contract handles deduplication.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

SourceType = Literal["paper_pdf", "blog_url", "dune_query"]
JudgeVerdict = Literal["PASS", "FLAG_FOR_OPERATOR", "REJECT"]


@dataclass(frozen=True)
class ExtractorOutput:
    """The four files the extractor produces, plus the judge verdict.

    The files are returned as in-memory strings; the worker is responsible
    for writing them to `templates/<template_id>/` after a successful judge
    pass. `template_id` is assigned by the extractor following the manifest's
    naming convention (e.g. "LEND-001", "BASIS-PERP-002").
    """

    template_id: str
    manifest_yaml: str
    evaluate_py: str
    smoke_test_py: str
    parameter_rationale_md: str
    judge_verdict: JudgeVerdict
    judge_rationale: str


@runtime_checkable
class Extractor(Protocol):
    """Async extractor — one paper / blog / Dune query at a time."""

    name: str

    async def extract(
        self,
        source_type: SourceType,
        source_ref: str,
    ) -> ExtractorOutput:
        """Produce a template from a single source.

        `source_ref` is a path (paper_pdf), URL (blog_url), or Dune query ID.
        Raises ExtractorError on unrecoverable LLM / parse failure; the worker
        retries idempotently per its visibility-timeout contract.
        """
        ...
