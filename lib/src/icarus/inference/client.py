"""`OllamaClient` — async HTTP client for the local Ollama `/api/generate` endpoint.

Single responsibility: turn `(prompt, system?)` into a concatenated text
completion, or raise `InferenceUnavailable` within the 5-second budget.

Why we stream NDJSON instead of using `stream=false`:
  Ollama with `stream=false` still buffers the entire response server-side
  before returning, which means a slow model run can overshoot the
  5-second budget without us getting partial output to log. Streaming
  lets us see chunks arrive, abort cleanly at the deadline (httpx's
  per-call timeout), and surface the *reason* (timeout vs. connect vs.
  garbage) in the structured log.

Why every failure becomes `InferenceUnavailable`:
  The caller's contract is "give me text or tell me the advisor is down."
  We deliberately do NOT distinguish HTTP 503 from a JSON parse error in
  the exception type — they're indistinguishable to the cycle, which is
  going to substitute an `"advisor error: ..."` string and continue.
  The diagnostic detail goes in the message.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass

import httpx
import structlog

_logger = structlog.get_logger(service="inference.ollama")

DEFAULT_BASE_URL = "http://localhost:11434"
DEFAULT_MODEL = "deepseek-r1:14b"
DEFAULT_TIMEOUT_SECONDS = 5.0


class InferenceUnavailable(RuntimeError):  # noqa: N818 — public API name fixed by W6 spec (caller-facing seam)
    """The Ollama call did not produce a usable completion within the budget.

    Callers MUST catch this specifically (not bare `Exception`) so that
    programmer errors elsewhere keep crashing loudly instead of being
    silently treated as "advisor degraded".
    """


@dataclass(frozen=True)
class Advisory:
    """One Ollama response, plus observability fields for the structured log."""

    text: str
    latency_ms: int
    model: str


class OllamaClient:
    """Async client for a single local Ollama instance.

    Stateless across `ask()` calls — each call opens its own `AsyncClient`
    so the connection pool can't leak between invocations of independent
    cycles. The cost (TCP handshake to a localhost neighbour) is dwarfed
    by inference itself.
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        model: str = DEFAULT_MODEL,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._base_url = (
            base_url or os.environ.get("OLLAMA_URL") or DEFAULT_BASE_URL
        ).rstrip("/")
        self._model = model
        self._timeout_seconds = timeout_seconds

    @property
    def model(self) -> str:
        return self._model

    @property
    def base_url(self) -> str:
        return self._base_url

    async def ask(self, prompt: str, *, system: str | None = None) -> Advisory:
        """Send `prompt` to Ollama and return the concatenated streamed text.

        Raises `InferenceUnavailable` on any transport error, timeout, or
        malformed NDJSON. The exception message includes the failure mode
        so the caller can surface a useful "advisor error: ..." string.
        """
        payload: dict[str, object] = {
            "model": self._model,
            "prompt": prompt,
            "stream": True,
        }
        if system is not None:
            payload["system"] = system

        url = f"{self._base_url}/api/generate"
        started = time.perf_counter()

        chunks: list[str] = []
        try:
            async with httpx.AsyncClient(timeout=self._timeout_seconds) as http:
                async with http.stream("POST", url, json=payload) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line:
                            continue
                        try:
                            obj = json.loads(line)
                        except json.JSONDecodeError as exc:
                            msg = (
                                f"ollama returned non-JSON chunk: {exc.msg} "
                                f"(line prefix: {line[:80]!r})"
                            )
                            raise InferenceUnavailable(msg) from exc
                        chunk_text = obj.get("response", "")
                        if isinstance(chunk_text, str) and chunk_text:
                            chunks.append(chunk_text)
                        if obj.get("done") is True:
                            break
        except httpx.TimeoutException as exc:
            msg = (
                f"inference timeout after {self._timeout_seconds:.1f}s "
                f"(host: {self._base_url})"
            )
            _logger.warning("inference.timeout", host=self._base_url, error=str(exc))
            raise InferenceUnavailable(msg) from exc
        except httpx.HTTPStatusError as exc:
            msg = (
                f"inference HTTP {exc.response.status_code} from {self._base_url}"
            )
            _logger.warning(
                "inference.http_error",
                host=self._base_url,
                status=exc.response.status_code,
            )
            raise InferenceUnavailable(msg) from exc
        except httpx.HTTPError as exc:
            # Catches ConnectError, ReadError, RemoteProtocolError, etc.
            msg = f"inference transport error: {type(exc).__name__}: {exc}"
            _logger.warning(
                "inference.transport_error",
                host=self._base_url,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            raise InferenceUnavailable(msg) from exc

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        text = "".join(chunks)
        _logger.info(
            "inference.ok",
            model=self._model,
            latency_ms=elapsed_ms,
            text_chars=len(text),
        )
        return Advisory(text=text, latency_ms=elapsed_ms, model=self._model)
