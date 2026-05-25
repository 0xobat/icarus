"""Pure-Python loader tests — deterministic, no LLM, no network."""

from __future__ import annotations

import asyncio

import pytest
from extractor_worker.loaders import (
    MAX_SOURCE_CHARS,
    SourceLoadError,
    _truncate,
    load_dune,
    load_pdf,
    load_source,
)
from pypdf import PdfWriter


def test_truncate_passthrough_under_limit():
    s = "x" * (MAX_SOURCE_CHARS - 1)
    assert _truncate(s) == s


def test_truncate_cuts_and_annotates_over_limit():
    s = "x" * (MAX_SOURCE_CHARS + 100)
    out = _truncate(s)
    assert len(out) > MAX_SOURCE_CHARS  # cut + annotation
    assert "source truncated at" in out
    assert out.startswith("x" * MAX_SOURCE_CHARS)


def test_load_pdf_missing_file_raises():
    with pytest.raises(SourceLoadError, match="PDF not found"):
        load_pdf("/nonexistent/path.pdf")


def test_load_pdf_empty_pdf_raises(tmp_path):
    """Blank PDF (no extractable text) is treated as a broken source."""
    # PyPDF can write a one-page blank PDF.
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    pdf_path = tmp_path / "blank.pdf"
    with pdf_path.open("wb") as f:
        writer.write(f)

    with pytest.raises(SourceLoadError, match="yielded no text"):
        load_pdf(str(pdf_path))


def test_load_source_unknown_type_raises():
    with pytest.raises(SourceLoadError, match="unknown source_type"):
        asyncio.run(load_source("nonsense", "x"))


def test_load_dune_not_implemented():
    with pytest.raises(NotImplementedError, match="Dune adapter not implemented until W4"):
        asyncio.run(load_dune("12345"))
