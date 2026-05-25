"""Vendored v4.2 types required by the verbatim copy-back of `price_feed.py`.

These mirror the v4.2 definitions exactly so the ported module + its tests
run unmodified. Adaptation onto v2's `icarus.types` / data-adapter envelopes
is a separate, later task.

- `TokenPrice` is the v4.2 `strategies.base.TokenPrice` dataclass.
- `RedisManager` is a structural Protocol — `PriceFeedManager` only uses it
  as a type annotation; v4.2's tests mock the whole object, so we don't pull
  in the real `data.redis_client` (which itself depends on
  `validation.schema_validator` and other v4.2-only modules).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol


@dataclass(frozen=True)
class TokenPrice:
    """Token price with source and timestamp (v4.2 verbatim).

    Args:
        token: Token symbol (e.g. "USDC", "AERO").
        price: Price in USD.
        source: Data source (e.g. "alchemy", "defillama").
        timestamp: When this price was observed.
    """

    token: str
    price: float
    source: str
    timestamp: datetime


class RedisManager(Protocol):
    """Structural protocol matching the v4.2 RedisManager surface area used
    by `PriceFeedManager` (cache_set / cache_get + sorted-set ops on
    ``client``). Tests mock this with `MagicMock`; the real implementation
    will be wired during the W3 data-adapter port.
    """

    client: Any

    def cache_set(self, key: str, value: Any, ttl: int) -> None: ...
    def cache_get(self, key: str) -> Any | None: ...
