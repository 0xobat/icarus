"""Research worker queue envelopes — `research:papers:pending` and
`research:search:pending` Redis lists (BLMOVE atomic claim)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from icarus.types.market import Chain

SourceType = Literal["paper_pdf", "blog_url", "dune_query"]


class _StrictBase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PaperJob(_StrictBase):
    """Item on `research:papers:pending`. extractor-worker BLMOVE-claims it,
    runs the LLM extraction + plausibility judge, writes templates/<id>/."""

    version: Literal["1.0.0"] = "1.0.0"
    job_id: str = Field(min_length=8)
    enqueued_at: datetime
    requested_by: str = Field(description="operator handle or 'scheduled_scraper'")
    source_type: SourceType
    source_ref: str = Field(description="path, URL, or Dune query ID")
    correlation_id: str
    # Operator can pre-set the chain hint so the extractor doesn't have to
    # infer it from the source — useful for Solana-only templates from a
    # paper that doesn't say "Solana" explicitly.
    chain_hint: Chain | None = None


class SearchJob(_StrictBase):
    """Item on `research:search:pending`. backtest-worker BLMOVE-claims it,
    runs a grid or Bayesian search per the embedded SearchConfig.

    Carries the full `SearchConfig` as a dict (model_dump) so the worker
    deserialises into the proper subtype via `kind` discriminator. Keeping
    it as `dict` here avoids a circular import between envelopes and
    protocols."""

    version: Literal["1.0.0"] = "1.0.0"
    job_id: str = Field(min_length=8)
    enqueued_at: datetime
    requested_by: str
    correlation_id: str
    template_id: str
    template_version: str
    deadline_unix: int = Field(ge=0, description="hard kill at this time")
    search_config: dict[str, Any] = Field(
        description="SearchConfig.model_dump() — deserialised by the worker"
    )
