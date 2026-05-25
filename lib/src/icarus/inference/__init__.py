"""Ollama inference HTTP client — advisory-only LLM seam.

Per `CLAUDE.md`: "LLM calls are advisory only, never inside capital-protecting
gates." Per blueprint Q2 (~line 380): "Rules-based regime classification is
primary; LLM regime is advisor only. Cycle never blocks on inference
availability." Per blueprint (~line 360): "inference service with 5-sec
fire-and-forget."

This package exposes a *narrow* async client around the local Ollama
`/api/generate` endpoint. Two design rules drive every decision in here:

  1. **5-second hard ceiling.** The decision cycle never waits longer.
     Any HTTP error, JSON-parse error, or timeout becomes
     `InferenceUnavailable` so the caller can fall back without ambiguity.
  2. **Specific exception type.** Callers must `except InferenceUnavailable`,
     not bare `Exception` — this keeps unrelated bugs (e.g. a programmer
     typo in prompt assembly) surfacing as crashes instead of being
     swallowed as "advisor degraded".

The `advisor` module composes the client into the conventions used by the
allocator pure-advisor seat (see
`icarus.protocols.allocator.ADVISOR_ERROR_PREFIX`).
"""

from __future__ import annotations

from icarus.inference.advisor import (
    commentary_for_allocation,
    commentary_for_decay,
)
from icarus.inference.client import Advisory, InferenceUnavailable, OllamaClient

__all__ = [
    "Advisory",
    "InferenceUnavailable",
    "OllamaClient",
    "commentary_for_allocation",
    "commentary_for_decay",
]
