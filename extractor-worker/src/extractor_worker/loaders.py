"""Source loaders — turn a PaperJob's (source_type, source_ref) into raw text.

Three source types per `lib/src/icarus/envelopes/research.py:SourceType`:
  - paper_pdf    → read PDF off disk, extract text via pypdf
  - blog_url     → fetch URL, extract readable content via readability-lxml
  - dune_query   → DEFERRED to W4 (Dune adapter); raises NotImplementedError

The text returned is what gets embedded in the frontier prompt. Size matters
for cost — we cap at 60_000 chars (~15k tokens) which fits most papers and
keeps the prompt + response under Claude Opus 4.7's context comfortably.
Truncation happens at character boundaries with a sentinel comment so the
model knows it was cut.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import structlog
from pypdf import PdfReader
from readability import Document

_logger = structlog.get_logger(service="extractor.loaders")

MAX_SOURCE_CHARS = 60_000
TRUNCATION_NOTE = "\n\n[... source truncated at {n} chars; ~{pct}% remains uncited ...]\n"

HTTP_TIMEOUT_SECONDS = 30.0
HTTP_USER_AGENT = "icarus-extractor/0.1 (+https://github.com/0xObat/icarus)"


class SourceLoadError(RuntimeError):
    """Anything that prevented us from getting source text. The worker
    treats this as a non-retryable failure (the source is broken, not the
    LLM) and writes an Alert row instead of requeuing."""


def _truncate(text: str) -> str:
    if len(text) <= MAX_SOURCE_CHARS:
        return text
    cut = text[:MAX_SOURCE_CHARS]
    pct = round(100 * (len(text) - MAX_SOURCE_CHARS) / len(text))
    return cut + TRUNCATION_NOTE.format(n=MAX_SOURCE_CHARS, pct=pct)


def load_pdf(path: str) -> str:
    """Extract text from a PDF on disk. Raises SourceLoadError on file/parse failure."""
    p = Path(path)
    if not p.exists():
        msg = f"PDF not found: {path}"
        raise SourceLoadError(msg)
    try:
        reader = PdfReader(str(p))
        pages = [page.extract_text() or "" for page in reader.pages]
    except Exception as e:
        msg = f"pypdf failed to parse {path}: {e}"
        raise SourceLoadError(msg) from e

    text = "\n\n".join(pages).strip()
    if not text:
        msg = f"PDF parsed but yielded no text (scanned image?): {path}"
        raise SourceLoadError(msg)

    _logger.info("pdf_loaded", path=path, pages=len(pages), chars=len(text))
    return _truncate(text)


async def load_blog(url: str) -> str:
    """Fetch a URL and extract readable article body.

    Raises SourceLoadError on network/parse failure."""
    try:
        async with httpx.AsyncClient(
            timeout=HTTP_TIMEOUT_SECONDS,
            follow_redirects=True,
            headers={"User-Agent": HTTP_USER_AGENT},
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
            html = response.text
    except httpx.HTTPError as e:
        msg = f"failed to fetch {url}: {e}"
        raise SourceLoadError(msg) from e

    try:
        doc = Document(html)
        title = doc.title()
        body_html = doc.summary()
    except Exception as e:
        msg = f"readability failed to parse {url}: {e}"
        raise SourceLoadError(msg) from e

    # Strip HTML tags from body — the LLM doesn't need them, and tag soup
    # eats prompt tokens. We use lxml directly to avoid a regex parser.
    from lxml import html as lxml_html

    try:
        tree = lxml_html.fromstring(body_html)
        body_text = tree.text_content().strip()
    except Exception as e:
        msg = f"lxml failed to extract text from {url}: {e}"
        raise SourceLoadError(msg) from e

    if not body_text:
        msg = f"blog body extracted but empty: {url}"
        raise SourceLoadError(msg)

    text = f"# {title}\n\n{body_text}" if title else body_text
    _logger.info("blog_loaded", url=url, chars=len(text))
    return _truncate(text)


async def load_dune(query_id: str) -> str:
    """Dune Analytics query loader. DEFERRED to W4 (Dune adapter)."""
    msg = (
        f"Dune adapter not implemented until W4 (blueprint §'Build sequence'). "
        f"Cannot extract from dune_query:{query_id}."
    )
    raise NotImplementedError(msg)


async def load_source(source_type: str, source_ref: str) -> str:
    """Dispatch on source_type. Mirrors the SourceType literal in
    `icarus.envelopes.research`."""
    if source_type == "paper_pdf":
        return load_pdf(source_ref)
    if source_type == "blog_url":
        return await load_blog(source_ref)
    if source_type == "dune_query":
        return await load_dune(source_ref)
    msg = f"unknown source_type: {source_type}"
    raise SourceLoadError(msg)
