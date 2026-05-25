"""Domain-specific advisor helpers — compose `OllamaClient` into prose strings.

These helpers exist so the decision-engine and lake-governor never have to
own the `try/except InferenceUnavailable` dance themselves. Every helper:

  1. Builds a short, focused prompt (~200 tokens of context).
  2. Calls `client.ask(...)`.
  3. On success returns the model's prose.
  4. On `InferenceUnavailable` returns a string prefixed with
     `ADVISOR_ERROR_PREFIX` ("advisor error: "), per the convention
     memorialised on `AllocationDecision.commentary` (see
     `icarus.protocols.allocator`).

The "advisor error: ..." convention means a dashboard reader can
distinguish "LLM said something" from "LLM was down" by string-prefix
alone — no separate signalling channel, no log-mining required.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

import structlog

from icarus.inference.client import InferenceUnavailable
from icarus.protocols.allocator import ADVISOR_ERROR_PREFIX

if TYPE_CHECKING:
    from icarus.inference.client import OllamaClient
    from icarus.protocols.regime import Regime

_logger = structlog.get_logger(service="inference.advisor")

_ALLOCATION_SYSTEM = (
    "You are a risk-aware portfolio advisor for an algorithmic crypto "
    "trading system. You comment briefly (2-3 sentences) on this cycle's "
    "allocation decision. You do NOT recommend changes — the allocator is "
    "deterministic. You highlight: (a) regime fit, (b) concentration risk, "
    "(c) any candidate whose decision looks anomalous vs. the cohort."
)

_DECAY_SYSTEM = (
    "You are a strategy-decay analyst. The Page-Hinkley detector has "
    "tripped on a live candidate. In 2-3 sentences, comment on the recent "
    "Sharpe trajectory and suggest whether the decay looks regime-driven, "
    "structural, or noise. You do NOT recommend retention — the lake "
    "governor decides."
)


def _format_candidates(candidates: Sequence[Any]) -> str:
    """Render candidate list as a short bulleted summary.

    Accepts any objects (dataclasses, dicts, ORM rows). Falls back to
    `repr()` for unknown shapes so a malformed input still produces a
    sendable prompt — the LLM advisor cycle must never fail on a
    formatting error.
    """
    if not candidates:
        return "(no candidates)"
    lines = []
    for c in candidates[:20]:  # hard cap so the prompt stays ~200 tokens
        cid = getattr(c, "candidate_id", None) or getattr(c, "id", None) or repr(c)
        template = getattr(c, "template_id", None) or getattr(c, "template", "?")
        lines.append(f"- {cid} (template={template})")
    if len(candidates) > 20:
        lines.append(f"- ... and {len(candidates) - 20} more")
    return "\n".join(lines)


def _format_decisions(decisions: Sequence[Any]) -> str:
    """Render Decision objects as a short bulleted summary. Same fallback policy."""
    if not decisions:
        return "(no decisions)"
    lines = []
    for d in decisions[:20]:
        cid = getattr(d, "candidate_id", None) or getattr(d, "id", None) or "?"
        action = getattr(d, "action", None) or getattr(d, "side", "?")
        size = getattr(d, "size_usd", None) or getattr(d, "notional_usd", "?")
        lines.append(f"- {cid}: {action} size={size}")
    if len(decisions) > 20:
        lines.append(f"- ... and {len(decisions) - 20} more")
    return "\n".join(lines)


def _format_regime(regime: Regime | Any) -> str:
    """Render a Regime as a one-line summary."""
    vol = getattr(regime, "volatility", "?")
    funding = getattr(regime, "funding", "?")
    trend = getattr(regime, "trend", "?")
    tvl = getattr(regime, "tvl", "?")
    return f"vol={vol} funding={funding} trend={trend} tvl={tvl}"


async def commentary_for_allocation(
    client: OllamaClient,
    candidates: Sequence[Any],
    decisions: Sequence[Any],
    regime: Regime | Any,
) -> str:
    """Ask the LLM advisor for a brief paragraph on this cycle's allocation.

    Returns model prose on success. On `InferenceUnavailable` returns a
    string prefixed with `ADVISOR_ERROR_PREFIX` ("advisor error: ").
    Never raises — the cycle MUST be able to continue.
    """
    prompt = (
        "Current regime:\n"
        f"  {_format_regime(regime)}\n\n"
        f"Live candidates ({len(candidates)}):\n"
        f"{_format_candidates(candidates)}\n\n"
        f"This cycle's decisions ({len(decisions)}):\n"
        f"{_format_decisions(decisions)}\n\n"
        "In 2-3 sentences, comment on this allocation."
    )
    try:
        advisory = await client.ask(prompt, system=_ALLOCATION_SYSTEM)
    except InferenceUnavailable as exc:
        _logger.warning("advisor.allocation.unavailable", error=str(exc))
        return f"{ADVISOR_ERROR_PREFIX}{exc}"
    return advisory.text


async def commentary_for_decay(
    client: OllamaClient,
    candidate_id: str,
    recent_sharpe_history: Sequence[float],
) -> str:
    """Ask the LLM advisor for a brief paragraph on a Page-Hinkley decay trip.

    Returns model prose on success. On `InferenceUnavailable` returns a
    string prefixed with `ADVISOR_ERROR_PREFIX`. Never raises.
    """
    if recent_sharpe_history:
        history_str = ", ".join(f"{x:.3f}" for x in recent_sharpe_history[-30:])
    else:
        history_str = "(no history)"

    prompt = (
        f"Candidate: {candidate_id}\n"
        f"Recent rolling Sharpe (most recent last, up to 30 points):\n"
        f"  {history_str}\n\n"
        "In 2-3 sentences, comment on the decay trajectory."
    )
    try:
        advisory = await client.ask(prompt, system=_DECAY_SYSTEM)
    except InferenceUnavailable as exc:
        _logger.warning(
            "advisor.decay.unavailable",
            candidate_id=candidate_id,
            error=str(exc),
        )
        return f"{ADVISOR_ERROR_PREFIX}{exc}"
    return advisory.text
