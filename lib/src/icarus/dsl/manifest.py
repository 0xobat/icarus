"""TemplateManifest — pydantic validator for templates/<id>/manifest.yaml.

The manifest is the *spine* of a template. It carries everything the lake's
allocator, search engine, and registry need to comprehend a template without
loading its `evaluate.py`. The `evaluate.py` carries the arbitrary logic the
manifest can't.

Schema design rules:
  - Strict. `model_config = ConfigDict(extra="forbid")` so the extractor
    can't sneak unknown fields past the validator.
  - Decimal where a value is monetary, a percentage, or a search-space
    boundary. Float only for non-money knobs (e.g. lambda).
  - Discriminated union on `ParamSpec.kind` so the search engine can
    statically dispatch grid vs continuous vs categorical.

The discriminated `params` shape mirrors `icarus.protocols.backtest`'s
`GridSearchConfig` / `BayesianSearchConfig` split — same vocabulary, two
levels of the stack.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from icarus.types.market import Chain


class _StrictBase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# ─────────── Sources (where the strategy came from) ───────────

SourceType = Literal["paper_pdf", "blog_url", "dune_query", "code"]


class Source(_StrictBase):
    """One source the extractor drew from when emitting this template.

    Templates can cite multiple sources (e.g. an arXiv paper plus a
    Paradigm blog post explaining it). The first source listed is the
    primary one shown in `webapp` and Discord PROMOTION REQUEST messages.
    """

    type: SourceType
    ref: str = Field(min_length=1, description="path, URL, or Dune query ID")
    description: str | None = None


# ─────────── Parameter spec (discriminated union) ───────────


class GridParam(_StrictBase):
    """Discrete enumeration — grid search iterates every value."""

    kind: Literal["grid"] = "grid"
    values: Sequence[Decimal] = Field(min_length=1)
    description: str | None = None


class ContinuousParam(_StrictBase):
    """Real-valued interval — Bayesian search samples; grid search materialises a
    fixed sub-grid using `default_steps`.
    """

    kind: Literal["continuous"] = "continuous"
    low: Decimal
    high: Decimal
    scale: Literal["linear", "log"] = "linear"
    default_steps: int = Field(default=8, ge=2, le=64)
    description: str | None = None

    @model_validator(mode="after")
    def _range_ordered(self) -> ContinuousParam:
        if self.low >= self.high:
            raise ValueError(f"low ({self.low}) must be < high ({self.high})")
        return self


class CategoricalParam(_StrictBase):
    """Finite string choice — venue selection, asset variant, etc."""

    kind: Literal["categorical"] = "categorical"
    choices: Sequence[str] = Field(min_length=1)
    description: str | None = None


ParamSpec = Annotated[
    GridParam | ContinuousParam | CategoricalParam,
    Field(discriminator="kind"),
]


# ─────────── Expected metrics (acceptance gate hints) ───────────


class ExpectedMetrics(_StrictBase):
    """Author-asserted expected ranges for the metrics the OOS gate checks.

    These are HINTS, not gates. The actual acceptance gate is in the
    paper-trade harness (Curation cluster). The extractor populates these
    from the source paper's reported numbers; if our backtest produces
    metrics outside these ranges, that is a signal worth flagging to the
    operator via the LLM-as-judge plausibility check.
    """

    sharpe_min: Decimal
    max_dd_max: Decimal = Field(le=Decimal("1.0"), description="0-1, fraction of NAV")
    oos_sharpe_floor: Decimal | None = None
    expected_annual_return_pct: Decimal | None = None


# ─────────── Full manifest ───────────

RiskProfile = Literal["low", "medium", "high"]
Sizing = Literal["risk_parity", "kelly_fractional"]
TEMPLATE_ID_REGEX = r"^[A-Z]+(-[A-Z]+)*-\d{3,4}$"  # e.g. LEND-001, BASIS-PERP-001


class TemplateManifest(_StrictBase):
    """The full manifest schema. One per template directory."""

    # Identity
    id: str = Field(pattern=TEMPLATE_ID_REGEX, description="e.g. LEND-001, BASIS-PERP-001")
    semver: str = Field(pattern=r"^\d+\.\d+\.\d+$", description="0.1.0 style")
    title: str = Field(min_length=1, max_length=200)

    # Where it runs
    chain: Chain
    protocol: str = Field(min_length=1, description="aave_v3, aerodrome, kamino, drift, ...")
    asset_universe: Sequence[str] = Field(min_length=1, description="e.g. ['USDC', 'USDbC']")

    # Provenance
    sources: Sequence[Source] = Field(min_length=1)
    source_doc_ref: str | None = Field(default=None, description="cite by `path#section` style")

    # Risk + sizing posture
    allocation_max: Decimal = Field(
        gt=Decimal("0"),
        le=Decimal("1.0"),
        description="hard ceiling on per-candidate share of NAV (0-1)",
    )
    risk_profile: RiskProfile
    sizing: Sizing = "risk_parity"

    # Search spec — the parameter grid the backtest engine sweeps
    params: dict[str, ParamSpec] = Field(min_length=1)

    # Search engine controls (manifest can override blueprint defaults)
    top_k: int = Field(default=5, ge=1, le=100)
    turnover_lambda: Decimal = Field(default=Decimal("0.05"), ge=Decimal("0"))
    walk_forward: tuple[int, int, int] = Field(
        default=(60, 15, 3),
        description="(train_days, test_days, step_days); Q3 default 60/15/3",
    )

    # Acceptance hints
    expected_metrics: ExpectedMetrics
