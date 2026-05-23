"""Allocator Protocol — capital allocation across the live lake.

Cold-start impl (live count < 5): equal-weight across the live set, capped
by each candidate's `allocation_max`. Steady-state impl (live count ≥ 5):
risk-parity on a rolling 60-day return window with Ledoit-Wolf shrinkage.

The Protocol does not encode which mode is active — the impl decides per
call. The runtime logs the chosen mode so it's auditable.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol, runtime_checkable

from icarus.protocols.regime import Regime
from icarus.types import Decision, PortfolioSnapshot

ADVISOR_ERROR_PREFIX = "advisor error: "
"""Sentinel prefix on `AllocationDecision.commentary` when the LLM advisor
call failed. Code that consumes commentary should test `startswith` against
this string rather than parse free text. The webapp surfaces these to the
operator so 'silent advisor outage' is impossible — every cycle's commentary
either contains the LLM's prose or an explicit error string."""


@dataclass(frozen=True)
class AllocationDecision:
    """Per-cycle output: target dollar allocation per candidate.

    `mode` is "cold_start" or "risk_parity"; logged for audit.

    `template_caps_applied` shows which lake-level template caps were
    binding this cycle, so over-fertility is observable.

    `commentary` is filled by the LLM advisor (allocator pure-advisor seat).
    Conventions:
      - LLM call succeeded: prose paragraph from the model.
      - LLM call failed: starts with `ADVISOR_ERROR_PREFIX` (`"advisor error: "`)
        followed by an insightful diagnosis — e.g.
        `"advisor error: inference HTTP 504 after 5.0s deadline (host: ollama:11434)"`,
        `"advisor error: model returned malformed JSON: missing 'recommendation' key"`,
        `"advisor error: rate-limited by frontier API, retry in 60s"`.
      - LLM not requested this cycle (e.g. cold-start <5 candidates): empty string.

    The error convention means a dashboard reader can spot advisor degradation
    without consulting separate logs.
    """

    target_usd_by_candidate: Mapping[str, Decimal]
    mode: str
    template_caps_applied: Mapping[str, Decimal]
    commentary: str


@runtime_checkable
class Allocator(Protocol):
    """Synchronous allocator.

    Inputs:
      `candidate_decisions`: each live candidate's `Decision` this cycle.
      `portfolio`: current portfolio state.
      `regime`: primary (rules-based) regime — informational; allocator may
                use it for per-template scaling but never to gate orders.

    Output: target dollar allocation per candidate, plus mode/caps for audit.
    """

    name: str

    def allocate(
        self,
        candidate_decisions: Mapping[str, Decision],
        portfolio: PortfolioSnapshot,
        regime: Regime,
    ) -> AllocationDecision:
        """Pure compute. No I/O. Same inputs → same outputs (deterministic for audit)."""
        ...
