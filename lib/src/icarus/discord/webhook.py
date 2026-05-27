"""Discord webhook poster — advisory operator-alert channel.

Wraps the Discord webhook HTTP API in a small async client that:

* Reads the webhook URL from the ``DISCORD_WEBHOOK_URL`` env var (or an
  explicit constructor argument). If neither is set, calls are no-ops
  that log at debug and return ``delivered=False`` with an explanatory
  error string — local development should not need a real webhook.
* **Never raises** from :meth:`WebhookPoster.post`. Discord outage,
  network timeouts, 5xx responses — all are swallowed and surfaced via
  :class:`WebhookOutcome`. This is intentional: the blueprint's
  advisor-not-gate principle says Discord cannot break the decision
  cycle.
* Formats the structured PROMOTION REQUEST message exactly as documented
  in the blueprint (``docs/blueprint.md`` ~line 342-354) and creates the
  matching :class:`ReplyToken` row in the same call so the round-trip
  state is durable from the moment the message goes out.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal

import httpx
import structlog

from icarus.discord.reply_tokens import (
    ReplyToken,
    ReplyTokenStore,
    format_token_slug,
)

logger = structlog.get_logger(service="discord", component="webhook")

_ENV_VAR: str = "DISCORD_WEBHOOK_URL"
_DEFAULT_TIMEOUT_SECONDS: float = 10.0


class WebhookError(Exception):
    """Raised only by helpers that may legitimately fail (e.g. construction).

    :meth:`WebhookPoster.post` never raises this — see module docstring.
    """


@dataclass(frozen=True, slots=True)
class WebhookOutcome:
    """Result of a single webhook POST attempt.

    ``delivered`` is ``True`` only on a Discord 2xx response. ``error`` is
    populated for all failure modes (no URL configured, timeout, 5xx,
    transport error). ``status_code`` is ``None`` when no HTTP exchange
    happened (e.g. no URL configured or transport-level error).
    """

    delivered: bool
    status_code: int | None = None
    error: str | None = None


class WebhookPoster:
    """Post structured operator alerts to Discord.

    Construction is cheap and side-effect-free. Pass a custom
    :class:`httpx.AsyncClient` (typically with a
    :class:`httpx.MockTransport`) in tests; in production the poster
    creates a short-lived client per call so connection-pool lifecycle
    matches the decision-cycle lifecycle.

    Args:
        webhook_url: Override the env var. ``None`` (default) means read
            ``DISCORD_WEBHOOK_URL``; if that is also unset / empty, all
            posts become no-ops.
        timeout_seconds: HTTP timeout for the POST.
        client: Optional preconfigured :class:`httpx.AsyncClient`. When
            provided, the caller owns its lifecycle (we do not close it).
    """

    def __init__(
        self,
        *,
        webhook_url: str | None = None,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        resolved = webhook_url if webhook_url is not None else os.environ.get(_ENV_VAR)
        # Treat empty string the same as unset — `.env.example` ships with
        # `DISCORD_WEBHOOK_URL=` so local dev would otherwise try to POST
        # to the empty string.
        self._webhook_url: str | None = resolved if resolved else None
        self._timeout_seconds = timeout_seconds
        self._client = client

    # ─── Plain posting ──────────────────────────────────────────────────────

    async def post(
        self, content: str, *, alert_category: str = "info"
    ) -> WebhookOutcome:
        """Post a plain-text message to the configured webhook.

        Never raises. ``alert_category`` is included in the structured log
        line so operators can grep for promotion / breaker / heartbeat
        traffic separately. Discord receives only ``content``.
        """
        if self._webhook_url is None:
            logger.debug(
                "webhook.skipped.no_url",
                alert_category=alert_category,
                content_length=len(content),
            )
            return WebhookOutcome(
                delivered=False,
                status_code=None,
                error="no webhook configured",
            )

        payload = {"content": content}
        try:
            if self._client is not None:
                response = await self._client.post(
                    self._webhook_url, json=payload, timeout=self._timeout_seconds
                )
            else:
                async with httpx.AsyncClient(timeout=self._timeout_seconds) as http:
                    response = await http.post(self._webhook_url, json=payload)
        except httpx.TimeoutException as exc:
            logger.warning(
                "webhook.timeout",
                alert_category=alert_category,
                timeout_seconds=self._timeout_seconds,
                error=str(exc),
            )
            return WebhookOutcome(
                delivered=False,
                status_code=None,
                error=f"timeout after {self._timeout_seconds:.1f}s",
            )
        except httpx.HTTPError as exc:
            logger.warning(
                "webhook.transport_error",
                alert_category=alert_category,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return WebhookOutcome(
                delivered=False,
                status_code=None,
                error=f"transport error: {exc}",
            )
        except Exception as exc:
            logger.warning(
                "webhook.unexpected_error",
                alert_category=alert_category,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return WebhookOutcome(
                delivered=False,
                status_code=None,
                error=f"unexpected error: {exc}",
            )

        status = response.status_code
        # Discord returns 204 for plain content posts, 200 for some forms.
        if 200 <= status < 300:
            logger.info(
                "webhook.delivered",
                alert_category=alert_category,
                status_code=status,
                content_length=len(content),
            )
            return WebhookOutcome(delivered=True, status_code=status, error=None)

        body_snippet: str
        try:
            body_snippet = response.text[:200]
        except Exception:
            body_snippet = "<unreadable response body>"

        logger.warning(
            "webhook.http_error",
            alert_category=alert_category,
            status_code=status,
            body_snippet=body_snippet,
        )
        return WebhookOutcome(
            delivered=False,
            status_code=status,
            error=f"HTTP {status}: {body_snippet}",
        )

    # ─── Structured posts ───────────────────────────────────────────────────

    async def post_promotion_request(
        self,
        *,
        template_id: str,
        candidate_id: str,
        paper_sharpe: float,
        paper_max_dd: float,
        observation_days: int,
        proposed_allocation_usd: Decimal,
        allocation_cap_usd: Decimal,
        llm_advisor_text: str | None,
        reply_token_store: ReplyTokenStore,
    ) -> ReplyToken:
        """Format + post a PROMOTION REQUEST, return the created reply token.

        Creates the reply-token row **before** sending the message so the
        round-trip state is durable even if the POST itself fails (which
        is silent per the advisor-not-gate principle).

        Returns the :class:`ReplyToken` snapshot. The
        :class:`WebhookOutcome` is intentionally not surfaced — callers
        get the durable token; delivery success is observable via the
        structured log stream.
        """
        token = await reply_token_store.create_pending(
            candidate_id=candidate_id,
            template_id=template_id,
            kind="promotion_request",
        )
        content = _format_promotion_request(
            token_slug=format_token_slug(token.id),
            template_id=template_id,
            candidate_id=candidate_id,
            paper_sharpe=paper_sharpe,
            paper_max_dd=paper_max_dd,
            observation_days=observation_days,
            proposed_allocation_usd=proposed_allocation_usd,
            allocation_cap_usd=allocation_cap_usd,
            llm_advisor_text=llm_advisor_text,
        )
        await self.post(content, alert_category="promotion_request")
        return token


# ─── Formatting helpers ─────────────────────────────────────────────────────


def _format_promotion_request(
    *,
    token_slug: str,
    template_id: str,
    candidate_id: str,
    paper_sharpe: float,
    paper_max_dd: float,
    observation_days: int,
    proposed_allocation_usd: Decimal,
    allocation_cap_usd: Decimal,
    llm_advisor_text: str | None,
) -> str:
    """Render the PROMOTION REQUEST block per the blueprint."""
    # Blueprint format (docs/blueprint.md ~line 342-354). The reply
    # instruction uses the per-request token slug, not the candidate
    # id, so a reader of the broadcast cannot satisfy the reply parser
    # by echoing the candidate slug alone — see W12 review #2.
    lines = [
        "[PROMOTION REQUEST]",
        f"token: {token_slug}",
        f"template: {template_id}",
        f"candidate: {candidate_id}",
        f"paper-trade Sharpe: {paper_sharpe:.2f} (window: {observation_days} days)",
        f"paper-trade MaxDD: {paper_max_dd * 100:.1f}%",
        (
            f"proposed allocation: ${proposed_allocation_usd:,.0f} "
            f"(capped at ${allocation_cap_usd:,.0f})"
        ),
    ]
    if llm_advisor_text:
        # The blueprint formats this as a quoted line. We strip stray
        # newlines so the message stays a single Discord post.
        flat = " ".join(llm_advisor_text.split())
        lines.append(f'llm-advisor: "{flat}"')
    lines.append(
        f"reply with: APPROVE {token_slug}   or   REJECT {token_slug} <reason>"
    )
    return "\n".join(lines)
