"""RegimeClassifier Protocol — primary signal feeding the allocator.

Per blueprint §"Runtime AI" (line 366): the rules-based classifier produces
four parallel sub-regimes, each computed deterministically from a
`MarketSnapshot` in milliseconds:

  - volatility regime (realised-vol bucket)
  - funding regime (aggregate funding rate sign + extremity)
  - trend regime (trending vs mean-reverting)
  - TVL regime (protocol TVL trajectory)

Per Q2 (locked): the rules-based impl is *primary*. The LLM impl (Ollama
DeepSeek-R1-Distill-Qwen-14B) is *advisory*, runs alongside, is logged
for disagreement analysis, but never gates anything.

Both impls satisfy this Protocol. The runtime cycle calls the primary
classifier synchronously (fast, deterministic) and fires the advisor
asynchronously with a 5-second timeout. Two distinct call sites; one
shared Protocol.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal, Protocol, runtime_checkable

from icarus.types import MarketSnapshot

VolRegime = Literal["low", "normal", "high"]
FundingRegime = Literal["neg_extreme", "negative", "neutral", "positive", "pos_extreme"]
TrendRegime = Literal["trending_up", "trending_down", "mean_reverting"]
TvlRegime = Literal["declining", "stable", "growing"]


@dataclass(frozen=True)
class Regime:
    """Four parallel sub-regimes plus the raw features used to derive them.

    `features` carries the underlying numerical signals (realised vol pct,
    aggregate funding rate bp, trend score, 24h TVL delta) so disagreement
    between rules and LLM impls is debuggable, not opaque, and so templates
    can read raw values directly when the bucketed labels are too coarse.

    `source` is the impl name (e.g. "rules_v1", "ollama_deepseek_r1_14b").
    `rationale` is one-line free-text — empty for rules-based, populated
    for the LLM advisor.
    """

    volatility: VolRegime
    funding: FundingRegime
    trend: TrendRegime
    tvl: TvlRegime
    confidence: Decimal
    features: Mapping[str, Decimal]
    source: str
    rationale: str


@runtime_checkable
class RegimeClassifier(Protocol):
    """Synchronous classifier — input is a single snapshot, output is one Regime."""

    name: str

    def classify(self, market: MarketSnapshot) -> Regime:
        """Return the current regime given `market`. Pure function, no I/O."""
        ...
