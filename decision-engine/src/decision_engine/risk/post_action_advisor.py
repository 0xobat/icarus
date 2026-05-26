"""Post-action LLM advisor for risk-breaker trips.

Per blueprint LLM placements (~line 422-440):

    "Risk circuit-breaker firing → Post-action explanation → No — runs
    after breaker fires + capital moves"

Per CLAUDE.md: "LLM calls are advisory only, never inside
capital-protecting gates." The breakers themselves
(`drawdown_breaker`, `position_loss_limit`, `tvl_monitor`, etc.) remain
pure compute and deterministic. This module is the explicit, separate
seam the risk-gate caller invokes AFTER a breaker has fired AND the
unwind order has already been emitted — the advisor's text is logged
into `Alert.data_json['advisor_text']` for the dashboard / Discord
side, never feeds back into trading decisions.

The shape mirrors the lake-metrics `explain_decay_trip` helper: one
async function, fire-and-forget, returns prose or an "advisor error:
..." prefixed string. Never raises.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog
from icarus.inference import InferenceUnavailable
from icarus.protocols.allocator import ADVISOR_ERROR_PREFIX

if TYPE_CHECKING:
    from icarus.inference import OllamaClient

_logger = structlog.get_logger(service="risk.post_action_advisor")

_BREAKER_SYSTEM = (
    "You are a risk-incident analyst for an algorithmic crypto trading "
    "system. A circuit breaker has already fired and the unwind order "
    "has already been emitted. In 2-3 sentences, comment on the likely "
    "proximate cause and whether the breaker's reaction looks "
    "proportionate. You do NOT recommend changes — the breaker "
    "thresholds are deterministic and tuned offline."
)


def _format_context(context: dict) -> str:
    """Render the breaker context dict as a short bulleted summary.

    Falls back to ``repr`` for unknown values so a malformed input still
    produces a sendable prompt — the advisor cycle must never fail on a
    formatting error.
    """
    if not context:
        return "(no context)"
    lines = []
    for key, value in sorted(context.items()):
        lines.append(f"- {key}: {value!r}")
    return "\n".join(lines)


async def explain_breaker_trip(
    *,
    client: OllamaClient,
    breaker_name: str,
    context: dict,
) -> str:
    """Post-fire LLM advisor explanation for a risk-breaker trip.

    Args:
        client: The OllamaClient seam — opened by the caller.
        breaker_name: Stable identifier for which breaker fired —
            e.g. ``"drawdown"``, ``"position_loss"``, ``"tvl_drop"``.
        context: Whatever context the breaker exposed at fire time —
            NAV, drawdown_pct, position notional, etc. Rendered into the
            prompt verbatim.

    Returns:
        The model's prose on success, or a string prefixed with
        ``ADVISOR_ERROR_PREFIX`` ("advisor error: ") if Ollama was
        unavailable. The caller logs the result into
        ``Alert.data_json['advisor_text']``.

    Never raises — the post-action explanation path is strictly
    fire-and-forget and must not block the alert pipeline.
    """
    prompt = (
        f"Breaker fired: {breaker_name}\n"
        "Context at trip time:\n"
        f"{_format_context(context)}\n\n"
        "In 2-3 sentences, comment on the trip."
    )
    try:
        advisory = await client.ask(prompt, system=_BREAKER_SYSTEM)
    except InferenceUnavailable as exc:
        _logger.warning(
            "risk.post_action_advisor.unavailable",
            breaker=breaker_name,
            error=str(exc),
        )
        return f"{ADVISOR_ERROR_PREFIX}{exc}"
    return advisory.text
