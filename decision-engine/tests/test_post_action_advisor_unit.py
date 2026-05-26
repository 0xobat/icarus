"""Unit tests for the post-action risk-breaker advisor."""

from __future__ import annotations

from decision_engine.risk.post_action_advisor import explain_breaker_trip
from icarus.inference import Advisory, InferenceUnavailable
from icarus.protocols.allocator import ADVISOR_ERROR_PREFIX


class _StubOllamaClient:
    """Stub OllamaClient returning a fixed advisory string."""

    def __init__(self, response_text: str) -> None:
        self._response_text = response_text

    async def ask(self, prompt: str, *, system: str | None = None) -> Advisory:
        return Advisory(text=self._response_text, latency_ms=10, model="stub")


class _UnavailableOllamaClient:
    """Stub OllamaClient that raises InferenceUnavailable on every call."""

    async def ask(self, prompt: str, *, system: str | None = None) -> Advisory:
        raise InferenceUnavailable("stub: advisor down for the test")


async def test_explain_breaker_trip_returns_model_text_and_falls_back_on_unavailable() -> None:
    """`explain_breaker_trip` returns the model's text on success and a
    string prefixed with ADVISOR_ERROR_PREFIX when the advisor is down."""
    success_text = (
        "Drawdown trip looks proportionate; NAV fell 12% over the cycle."
    )
    success_result = await explain_breaker_trip(
        client=_StubOllamaClient(success_text),
        breaker_name="drawdown",
        context={"nav_usd": 88000, "drawdown_pct": 0.12},
    )
    assert success_result == success_text

    fallback_result = await explain_breaker_trip(
        client=_UnavailableOllamaClient(),
        breaker_name="position_loss",
        context={"position_usd": 5000, "loss_pct": 0.25},
    )
    assert fallback_result.startswith(ADVISOR_ERROR_PREFIX)
    assert "advisor down" in fallback_result
