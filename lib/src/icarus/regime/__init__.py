"""Rules-based regime classifier — primary signal for the runtime cycle.

Per blueprint Q2 (locked): a deterministic, milliseconds-fast classifier
that computes four parallel sub-regimes from a `MarketSnapshot` and feeds
the allocator + candidate gating. The LLM advisor (inference service)
runs *alongside* on the same snapshot, fire-and-forget on a 5-sec timeout;
its output is logged for disagreement analysis but never gates anything.

The classifier consumes recent history (price + funding + TVL series) from
`MarketSnapshot.metadata`, using the same key convention already used by
templates (e.g. `metadata["funding_rates"]` in `BASIS-PERP-001`):

  - `metadata["price_history"][asset] -> list[Decimal]`  (daily closes, oldest first)
  - `metadata["funding_rates"][pool_id] -> Decimal`       (current funding rate, signed)
  - `metadata["tvl_history"][pool_id] -> list[Decimal]`   (daily TVL, oldest first)

When history is missing or too short for a sub-regime, the classifier
returns its safe-default bucket for that dimension and downgrades
`Regime.confidence` (0 = no signal; allocator treats as cold-start
equal-weight per Q2 runbook). `Regime.features` carries the raw
intermediate values for webapp display and LLM-advisor disagreement
debugging.

Pure compute — no DB, no Redis, no HTTP.
"""

from __future__ import annotations

from icarus.regime.classifier import RegimeFeatures, RulesRegimeClassifier

__all__ = ["RegimeFeatures", "RulesRegimeClassifier"]
