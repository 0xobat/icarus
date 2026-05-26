"""Discord webhook posting + reply-token state for operator alerts.

The lake-governor's promotion gate (W8 Stream B) imports from here to
post a structured PROMOTION REQUEST and durably record the round-trip
correlation token. Reply parsing is regex-based and the matched verdict
flips the persisted token row to ``approved`` / ``rejected``.

Per the blueprint advisor-not-gate principle, ``WebhookPoster.post``
**never raises** — Discord outage cannot break the decision cycle.
"""

from __future__ import annotations

from icarus.discord.reply_tokens import (
    ReplyToken,
    ReplyTokenMatch,
    ReplyTokenStore,
)
from icarus.discord.webhook import (
    WebhookError,
    WebhookOutcome,
    WebhookPoster,
)

__all__ = [
    "ReplyToken",
    "ReplyTokenMatch",
    "ReplyTokenStore",
    "WebhookError",
    "WebhookOutcome",
    "WebhookPoster",
]
