"""Unit tests for the advisor helpers — happy path returns model prose,
`InferenceUnavailable` becomes an `"advisor error: ..."` string.

The "advisor error: " prefix is the contract memorialised on
`AllocationDecision.commentary` (see `icarus.protocols.allocator`). These
tests pin both halves of that contract from the advisor side.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from icarus.inference import Advisory, InferenceUnavailable
from icarus.inference.advisor import (
    commentary_for_allocation,
    commentary_for_decay,
)
from icarus.protocols.allocator import ADVISOR_ERROR_PREFIX


@dataclass
class _FakeRegime:
    volatility: str = "normal"
    funding: str = "neutral"
    trend: str = "mean_reverting"
    tvl: str = "stable"


@dataclass
class _FakeCandidate:
    candidate_id: str
    template_id: str


@dataclass
class _FakeDecision:
    candidate_id: str
    action: str
    size_usd: float


class _StubOkClient:
    """Stub client whose `ask` returns a canned Advisory and records the prompt."""

    def __init__(self, text: str = "Allocation looks balanced.") -> None:
        self._text = text
        self.last_prompt: str | None = None
        self.last_system: str | None = None

    async def ask(self, prompt: str, *, system: str | None = None) -> Advisory:
        self.last_prompt = prompt
        self.last_system = system
        return Advisory(text=self._text, latency_ms=42, model="stub")


class _StubFailClient:
    """Stub client whose `ask` always raises InferenceUnavailable."""

    def __init__(self, message: str = "inference timeout after 5.0s") -> None:
        self._message = message

    async def ask(self, prompt: str, *, system: str | None = None) -> Advisory:
        raise InferenceUnavailable(self._message)


@pytest.mark.asyncio
async def test_commentary_for_allocation_happy_path_returns_model_text() -> None:
    """(a) On a successful Ollama call the model's text is returned verbatim."""
    client = _StubOkClient(text="This cycle is well diversified.")
    regime = _FakeRegime()
    candidates = [
        _FakeCandidate("c1", "BASIS-PERP-001"),
        _FakeCandidate("c2", "BASIS-PERP-001"),
    ]
    decisions = [
        _FakeDecision("c1", "open", 1000.0),
        _FakeDecision("c2", "hold", 0.0),
    ]

    result = await commentary_for_allocation(
        client,  # type: ignore[arg-type]
        candidates,
        decisions,
        regime,
    )

    assert result == "This cycle is well diversified."
    # Prompt contains the regime + candidate + decision summaries so the
    # LLM has the context it needs.
    assert client.last_prompt is not None
    assert "vol=normal" in client.last_prompt
    assert "c1" in client.last_prompt
    assert "open" in client.last_prompt
    assert client.last_system is not None


@pytest.mark.asyncio
async def test_commentary_for_allocation_on_unavailable_returns_advisor_error() -> None:
    """(b) On InferenceUnavailable the helper returns the ADVISOR_ERROR_PREFIX string."""
    client = _StubFailClient(message="inference HTTP 504 after 5.0s")
    regime = _FakeRegime()

    result = await commentary_for_allocation(
        client,  # type: ignore[arg-type]
        candidates=[],
        decisions=[],
        regime=regime,
    )

    assert result.startswith(ADVISOR_ERROR_PREFIX)
    assert "inference HTTP 504" in result


@pytest.mark.asyncio
async def test_commentary_for_decay_happy_path() -> None:
    """Decay commentary returns model prose on success."""
    client = _StubOkClient(text="Decay looks regime-driven.")
    history = [1.2, 1.1, 0.9, 0.7, 0.4, 0.1]

    result = await commentary_for_decay(
        client,  # type: ignore[arg-type]
        candidate_id="cand-xyz",
        recent_sharpe_history=history,
    )

    assert result == "Decay looks regime-driven."
    assert client.last_prompt is not None
    assert "cand-xyz" in client.last_prompt
    # Numbers formatted into the prompt so the model sees the trajectory.
    assert "1.200" in client.last_prompt
    assert "0.100" in client.last_prompt


@pytest.mark.asyncio
async def test_commentary_for_decay_on_unavailable_returns_advisor_error() -> None:
    """Decay commentary uses the same ADVISOR_ERROR_PREFIX convention."""
    client = _StubFailClient(message="inference transport error: ConnectError")

    result = await commentary_for_decay(
        client,  # type: ignore[arg-type]
        candidate_id="c1",
        recent_sharpe_history=[],
    )

    assert result.startswith(ADVISOR_ERROR_PREFIX)
    assert "ConnectError" in result


def test_advisor_does_not_crash_on_unknown_object_shapes() -> None:
    """Internal formatters tolerate arbitrary input — the cycle MUST NOT fail
    because a candidate had an unexpected attribute layout."""
    # Smoke test the private formatters via the public surface — pass dicts
    # instead of dataclasses.
    from icarus.inference.advisor import (
        _format_candidates,
        _format_decisions,
        _format_regime,
    )

    assert _format_candidates([{"foo": "bar"}]).startswith("- ")
    assert _format_decisions([{"foo": "bar"}]).startswith("- ")
    out = _format_regime(object())
    assert "vol=" in out


@pytest.mark.asyncio
async def test_long_candidate_list_truncated_in_prompt() -> None:
    """Prompts cap at 20 entries so they stay near the ~200-token budget."""
    client = _StubOkClient()
    candidates: list[Any] = [_FakeCandidate(f"c{i}", "T") for i in range(50)]
    decisions: list[Any] = [_FakeDecision(f"c{i}", "hold", 0.0) for i in range(50)]

    await commentary_for_allocation(
        client,  # type: ignore[arg-type]
        candidates,
        decisions,
        _FakeRegime(),
    )

    prompt = client.last_prompt or ""
    assert "and 30 more" in prompt
