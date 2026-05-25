"""Frontier LLM client — Anthropic-only for v1 (no OpenAI fallback per W2 decisions).

The `FrontierClient` Protocol is the seam where v2 of this service could
gain a multi-provider router. Today there is exactly one implementation.

Error policy (W2 decision #2c — "no other API defined, so no fail-over"):
  - Anthropic SDK exceptions propagate as `FrontierError` to the caller.
  - The caller (pipeline / worker) decides whether to repair, requeue, or
    surface to operator. This module never silently swaps providers.

Cost discipline: extraction is the most expensive LLM call in the system
(long source + structured-output rules + repair retries). We log
input/output tokens on every call so the operator can audit spend in the
structured log stream.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Protocol

import anthropic
import structlog

_logger = structlog.get_logger(service="extractor.frontier")

DEFAULT_MODEL = "claude-opus-4-7"  # Blueprint §"Build sequence" W2: Claude Opus 4.7 primary
DEFAULT_MAX_TOKENS = 8192


class FrontierError(RuntimeError):
    """Anything that prevented us from getting a usable completion."""


@dataclass(frozen=True)
class Completion:
    """One frontier response. Token counts surface to the structured log
    so the operator can audit spend per template."""

    text: str
    model: str
    input_tokens: int
    output_tokens: int


class FrontierClient(Protocol):
    """Minimal seam — one method, plain types. Easy to extend, easy to
    mock if the operator ever overrides W2 decision #5 (live always)."""

    async def complete(
        self,
        *,
        system: str,
        user: str,
        temperature: float = 1.0,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> Completion: ...


class AnthropicClient:
    """Anthropic implementation. Reads `ANTHROPIC_API_KEY` from env.

    Uses the async SDK so the worker's BLMOVE loop can stay async-clean.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str = DEFAULT_MODEL,
    ) -> None:
        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            msg = (
                "ANTHROPIC_API_KEY not set; extractor-worker cannot start. "
                "Per W2 decision #2c there is no fallback provider."
            )
            raise FrontierError(msg)
        self._client = anthropic.AsyncAnthropic(api_key=key)
        self._model = model

    async def complete(
        self,
        *,
        system: str,
        user: str,
        temperature: float = 1.0,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> Completion:
        try:
            response = await self._client.messages.create(
                model=self._model,
                system=system,
                messages=[{"role": "user", "content": user}],
                temperature=temperature,
                max_tokens=max_tokens,
            )
        except anthropic.APIError as e:
            _logger.error(
                "frontier_call_failed",
                model=self._model,
                error_class=type(e).__name__,
                error=str(e),
            )
            msg = f"Anthropic API error ({type(e).__name__}): {e}"
            raise FrontierError(msg) from e

        # Anthropic returns a list of content blocks; for plain prompts there
        # is one text block. We assert this rather than tolerate; an unexpected
        # tool-use response would indicate a prompt bug worth catching loudly.
        if not response.content or response.content[0].type != "text":
            msg = f"unexpected response shape: {response.content!r}"
            raise FrontierError(msg)

        text = response.content[0].text
        _logger.info(
            "frontier_call_ok",
            model=self._model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )
        return Completion(
            text=text,
            model=self._model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )
