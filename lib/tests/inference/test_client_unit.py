"""Unit tests for `OllamaClient` — happy path + the three failure modes
that must surface as `InferenceUnavailable`.

The cycle MUST be able to catch one specific exception type and continue —
tests pin that contract: any HTTP-layer or parse-layer failure becomes
`InferenceUnavailable`, never a leaked httpx / json exception.
"""

from __future__ import annotations

import json

import httpx
import pytest
from icarus.inference import Advisory, InferenceUnavailable, OllamaClient


def _ndjson_stream(chunks: list[dict[str, object]]) -> bytes:
    """Render a list of Ollama-style JSON chunks as NDJSON bytes."""
    return ("\n".join(json.dumps(c) for c in chunks) + "\n").encode("utf-8")


@pytest.mark.asyncio
async def test_happy_path_concatenates_streamed_chunks() -> None:
    """(a) Three NDJSON chunks merge into one Advisory.text + latency_ms set."""
    chunks = [
        {"response": "Hello, ", "done": False},
        {"response": "live ", "done": False},
        {"response": "lake.", "done": True},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/generate"
        body = json.loads(request.content)
        assert body["model"] == "deepseek-r1:14b"
        assert body["stream"] is True
        return httpx.Response(200, content=_ndjson_stream(chunks))

    transport = httpx.MockTransport(handler)

    client = OllamaClient(base_url="http://fake-ollama:11434", timeout_seconds=2.0)

    # Patch httpx.AsyncClient to inject the mock transport.
    real_init = httpx.AsyncClient.__init__

    def patched_init(self: httpx.AsyncClient, *args: object, **kwargs: object) -> None:
        kwargs["transport"] = transport
        real_init(self, *args, **kwargs)

    monkey_attr = "AsyncClient"
    original = getattr(httpx, monkey_attr)
    try:
        httpx.AsyncClient.__init__ = patched_init  # type: ignore[method-assign]
        advisory = await client.ask("test prompt", system="be brief")
    finally:
        httpx.AsyncClient.__init__ = real_init  # type: ignore[method-assign]
        setattr(httpx, monkey_attr, original)

    assert isinstance(advisory, Advisory)
    assert advisory.text == "Hello, live lake."
    assert advisory.model == "deepseek-r1:14b"
    assert advisory.latency_ms >= 0


@pytest.mark.asyncio
async def test_timeout_raises_inference_unavailable() -> None:
    """(b) httpx.TimeoutException → InferenceUnavailable with timeout in message."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("simulated slow model")

    transport = httpx.MockTransport(handler)
    client = OllamaClient(base_url="http://fake-ollama:11434", timeout_seconds=5.0)

    real_init = httpx.AsyncClient.__init__

    def patched_init(self: httpx.AsyncClient, *args: object, **kwargs: object) -> None:
        kwargs["transport"] = transport
        real_init(self, *args, **kwargs)

    try:
        httpx.AsyncClient.__init__ = patched_init  # type: ignore[method-assign]
        with pytest.raises(InferenceUnavailable) as exc_info:
            await client.ask("prompt")
    finally:
        httpx.AsyncClient.__init__ = real_init  # type: ignore[method-assign]

    assert "timeout" in str(exc_info.value).lower()
    assert "5.0" in str(exc_info.value)


@pytest.mark.asyncio
async def test_connect_error_raises_inference_unavailable() -> None:
    """(c) httpx.ConnectError (Ollama down) → InferenceUnavailable."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    transport = httpx.MockTransport(handler)
    client = OllamaClient(base_url="http://fake-ollama:11434", timeout_seconds=5.0)

    real_init = httpx.AsyncClient.__init__

    def patched_init(self: httpx.AsyncClient, *args: object, **kwargs: object) -> None:
        kwargs["transport"] = transport
        real_init(self, *args, **kwargs)

    try:
        httpx.AsyncClient.__init__ = patched_init  # type: ignore[method-assign]
        with pytest.raises(InferenceUnavailable) as exc_info:
            await client.ask("prompt")
    finally:
        httpx.AsyncClient.__init__ = real_init  # type: ignore[method-assign]

    msg = str(exc_info.value)
    assert "transport" in msg.lower() or "connect" in msg.lower()


@pytest.mark.asyncio
async def test_garbage_response_raises_inference_unavailable() -> None:
    """(d) Non-JSON chunk in the stream → InferenceUnavailable (no repair attempt)."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = (
            json.dumps({"response": "ok ", "done": False}).encode()
            + b"\n"
            + b"this is not json at all\n"
        )
        return httpx.Response(200, content=body)

    transport = httpx.MockTransport(handler)
    client = OllamaClient(base_url="http://fake-ollama:11434", timeout_seconds=5.0)

    real_init = httpx.AsyncClient.__init__

    def patched_init(self: httpx.AsyncClient, *args: object, **kwargs: object) -> None:
        kwargs["transport"] = transport
        real_init(self, *args, **kwargs)

    try:
        httpx.AsyncClient.__init__ = patched_init  # type: ignore[method-assign]
        with pytest.raises(InferenceUnavailable) as exc_info:
            await client.ask("prompt")
    finally:
        httpx.AsyncClient.__init__ = real_init  # type: ignore[method-assign]

    assert "non-JSON" in str(exc_info.value) or "json" in str(exc_info.value).lower()


def test_base_url_resolves_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Constructor reads OLLAMA_URL env var when base_url is not passed."""
    monkeypatch.setenv("OLLAMA_URL", "http://inference:11434")
    client = OllamaClient()
    assert client.base_url == "http://inference:11434"


def test_explicit_base_url_overrides_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Explicit base_url wins over OLLAMA_URL env var."""
    monkeypatch.setenv("OLLAMA_URL", "http://inference:11434")
    client = OllamaClient(base_url="http://override:9999")
    assert client.base_url == "http://override:9999"


def test_default_model_is_deepseek_r1_14b() -> None:
    """Default model matches blueprint default."""
    client = OllamaClient(base_url="http://x:1")
    assert client.model == "deepseek-r1:14b"
