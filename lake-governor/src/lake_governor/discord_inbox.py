"""Discord reply ingestion — bot-token long-poll of a single channel.

Closes the W8 gap where ``PromotionGate.poll_replies`` ran against an
empty generator. This module pulls inbound operator messages
(``APPROVE tok-<id>`` / ``REJECT tok-<id> <reason>``) from a Discord text
channel via the bot REST API and feeds them to the gate's reply parser.

Authentication contract (W12 review #2): only messages whose
``author.id`` is in ``DISCORD_OPERATOR_USER_IDS`` are forwarded. An
empty allowlist is fail-closed — the inbox drops every message and
no promotion can be authorized through Discord until the operator
populates the env var.

Design notes
============

* **Polling, not webhooks.** Following the W9 plan: polling does not
  require a public HTTPS endpoint and matches the existing one-way
  ``WebhookPoster`` model (Discord is the "operator's notifications
  channel"). The bot uses ``GET /channels/{id}/messages?after=<id>``
  with a configurable interval (default 30s).
* **No-op when unconfigured.** Missing ``DISCORD_BOT_TOKEN`` or
  ``DISCORD_CHANNEL_ID`` is the dev-friendly default — the inbox logs
  a single warning and yields nothing, exactly like the previous
  ``_no_replies`` stub. This keeps tests and local runs frictionless.
* **Rate-limit safe.** Discord returns ``X-RateLimit-Remaining`` and
  ``X-RateLimit-Reset-After`` on every response. When remaining hits
  zero we sleep for the advertised reset window before the next call.
  This is in addition to the configured ``poll_interval_seconds`` floor.
* **Fail-stop on auth errors.** 401/403 means the bot token is wrong
  or missing the ``Read Messages`` permission. We log loudly and stop
  the iterator — silently retrying would burn API quota without ever
  recovering.
* **Durable cursor.** The last-seen message id is persisted to disk
  (``DISCORD_INBOX_STATE_PATH``, default ``/run/icarus/discord_last_seen.txt``)
  so a lake-governor restart resumes from where it left off rather
  than re-processing the full history.

The module exposes two entry-points:

* :meth:`DiscordInbox.messages_since` — async iterator of
  ``(message_id, content)`` tuples for one polling pass. Surfaced
  separately so tests can drive it without the persistence layer.
* :meth:`DiscordInbox.listen` — long-running async iterator of just
  ``content`` strings, with state persisted between polls. This is
  what the lake-governor entrypoint binds to the promotion gate's
  ``listener_function`` (via a thin queue-backed adapter).
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import structlog

logger = structlog.get_logger(service="lake-governor", component="discord-inbox")

_DISCORD_API_BASE = "https://discord.com/api/v10"
_DEFAULT_STATE_PATH = Path("/run/icarus/discord_last_seen.txt")
_REQUEST_TIMEOUT_SECONDS = 10.0
# Discord caps ``limit`` at 100; we don't expect bursts that large in the
# operator channel, but ask for the max so a backlog clears in one call.
_MESSAGE_FETCH_LIMIT = 100


def _state_path_from_env() -> Path:
    raw = os.environ.get("DISCORD_INBOX_STATE_PATH")
    return Path(raw) if raw else _DEFAULT_STATE_PATH


def _operator_ids_from_env() -> frozenset[str]:
    """Parse ``DISCORD_OPERATOR_USER_IDS`` (comma-separated allowlist)."""
    raw = os.environ.get("DISCORD_OPERATOR_USER_IDS", "")
    return frozenset(p.strip() for p in raw.split(",") if p.strip())


class DiscordInbox:
    """Polling reader for a single Discord text channel.

    Construct once at service start. The same instance is safe to reuse
    across many ``listen()`` invocations — internal state is the
    ``httpx.AsyncClient`` and the polling cursor (passed explicitly).
    """

    def __init__(
        self,
        *,
        bot_token: str | None = None,
        channel_id: str | None = None,
        poll_interval_seconds: int = 30,
        http_client: httpx.AsyncClient | None = None,
        operator_user_ids: frozenset[str] | None = None,
    ) -> None:
        self._bot_token = bot_token or os.environ.get("DISCORD_BOT_TOKEN")
        self._channel_id = channel_id or os.environ.get("DISCORD_CHANNEL_ID")
        self._poll_interval_seconds = max(1, int(poll_interval_seconds))
        # Operator allowlist — messages from authors NOT in this set are
        # filtered out before reaching the reply parser. Empty allowlist
        # means no inbound message can authorize a promotion (fail-closed);
        # the operator MUST set DISCORD_OPERATOR_USER_IDS to receive any
        # APPROVE/REJECT. See W12 review #2 (Discord auth bypass).
        self._operator_user_ids = (
            operator_user_ids
            if operator_user_ids is not None
            else _operator_ids_from_env()
        )
        # Optional injection for tests — production constructs its own
        # client per ``listen()`` call so the connection lifecycle is
        # tied to the iterator.
        self._http_client = http_client

    @property
    def configured(self) -> bool:
        """Whether the inbox has the credentials needed to poll."""
        return bool(self._bot_token) and bool(self._channel_id)

    # ─── One-shot pass over new messages ────────────────────────────────────

    async def messages_since(
        self, after_message_id: str | None
    ) -> AsyncIterator[tuple[str, str]]:
        """Yield ``(message_id, content)`` for each new message in the channel.

        Performs a single ``GET /channels/{id}/messages`` call. Honors
        Discord's rate-limit headers — if ``X-RateLimit-Remaining`` is
        ``0`` we sleep for ``X-RateLimit-Reset-After`` seconds before
        returning (so the *next* call into this method is safe).

        On auth failure (401/403) logs an error and stops without
        yielding. On transient failure (5xx, transport error) logs and
        stops; the next tick will retry.
        """
        if not self.configured:
            return

        # ``after`` semantics: Discord returns messages with id > after,
        # newest first. If no cursor yet, leave it off and ask for the
        # most recent ``_MESSAGE_FETCH_LIMIT``; the caller persists the
        # newest id so subsequent polls start from there.
        params: dict[str, str | int] = {"limit": _MESSAGE_FETCH_LIMIT}
        if after_message_id:
            params["after"] = after_message_id

        url = f"{_DISCORD_API_BASE}/channels/{self._channel_id}/messages"
        headers = {"Authorization": f"Bot {self._bot_token}"}

        owns_client = self._http_client is None
        client = self._http_client or httpx.AsyncClient(
            timeout=_REQUEST_TIMEOUT_SECONDS
        )
        try:
            try:
                response = await client.get(url, headers=headers, params=params)
            except httpx.HTTPError as exc:
                logger.warning(
                    "discord_inbox.fetch_failed",
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
                return

            if response.status_code in (401, 403):
                logger.error(
                    "discord_inbox.bot_misconfigured",
                    status_code=response.status_code,
                    hint="check DISCORD_BOT_TOKEN and channel Read Messages permission",
                )
                # Surface a stop signal through a sentinel attribute the
                # ``listen()`` loop reads.
                self._auth_failed = True
                return

            if response.status_code != 200:
                logger.warning(
                    "discord_inbox.unexpected_status",
                    status_code=response.status_code,
                    body_preview=response.text[:200],
                )
                return

            messages = response.json() or []
            # Discord returns newest-first; flip so we yield oldest first
            # — this matches the order the operator typed them and is
            # what callers persisting "latest seen" need.
            for msg in reversed(messages):
                # ``content`` may be empty if the message is purely an
                # embed/attachment — skip rather than feed an empty
                # string into the reply parser.
                content = (msg.get("content") or "").strip()
                message_id = msg.get("id")
                if not message_id or not content:
                    continue
                # Operator allowlist — every reply that authorizes a
                # promotion must come from a known operator user id.
                # The bot literally prints the candidate id it expects
                # back, so without this filter any writer in the channel
                # (second bot, drifted permissions, compromised non-
                # operator account) could echo APPROVE and cause real
                # capital to move. Fail-closed on empty allowlist —
                # the operator must explicitly set
                # DISCORD_OPERATOR_USER_IDS to receive any reply.
                author_id = str((msg.get("author") or {}).get("id", ""))
                if not author_id or author_id not in self._operator_user_ids:
                    logger.warning(
                        "discord_inbox.non_operator_dropped",
                        message_id=str(message_id),
                        author_id=author_id or "<missing>",
                        content_preview=content[:80],
                    )
                    continue
                yield (str(message_id), content)

            await self._respect_rate_limit(response)
        finally:
            if owns_client:
                await client.aclose()

    @staticmethod
    async def _respect_rate_limit(response: httpx.Response) -> None:
        """Sleep for ``Reset-After`` when ``Remaining`` is zero.

        Discord uses bucketed rate limits — when remaining hits 0 the
        bucket is exhausted until ``Reset-After`` seconds elapse. We
        only sleep on the exhausted-bucket case; non-zero remaining
        means the regular ``poll_interval_seconds`` is enough.
        """
        try:
            remaining = int(response.headers.get("X-RateLimit-Remaining", "1"))
        except ValueError:
            remaining = 1
        if remaining > 0:
            return
        try:
            reset_after = float(response.headers.get("X-RateLimit-Reset-After", "0"))
        except ValueError:
            reset_after = 0.0
        if reset_after <= 0:
            return
        logger.info("discord_inbox.rate_limit_backoff", reset_after_s=reset_after)
        await asyncio.sleep(reset_after)

    # ─── Long-running stream with persistence ───────────────────────────────

    async def listen(self, seen_state_path: Path | None = None) -> AsyncIterator[str]:
        """Yield message contents indefinitely; persist the cursor between.

        Reads the last-seen message id from ``seen_state_path`` on
        startup so a restart doesn't re-emit old messages, then loops:
        poll → yield each new message's content → write the latest id
        → sleep for ``poll_interval_seconds`` → repeat.

        Stops without raising if:
          * the inbox is unconfigured (logs a single warning and returns),
          * a 401/403 is observed (auth failure — see ``messages_since``).
        """
        if not self.configured:
            logger.warning(
                "discord_inbox.unconfigured",
                hint="set DISCORD_BOT_TOKEN and DISCORD_CHANNEL_ID to enable",
                has_token=bool(self._bot_token),
                has_channel=bool(self._channel_id),
            )
            return

        state_path = seen_state_path or _state_path_from_env()
        last_seen = self._read_state(state_path)
        logger.info(
            "discord_inbox.listen_start",
            channel_id=self._channel_id,
            last_seen=last_seen,
            poll_interval_s=self._poll_interval_seconds,
        )

        while True:
            self._auth_failed = False
            newest_id = last_seen
            async for message_id, content in self.messages_since(last_seen):
                yield content
                newest_id = message_id

            if self._auth_failed:
                # Don't burn quota re-polling a misconfigured bot.
                logger.error("discord_inbox.listen_stop_auth_failure")
                return

            if newest_id != last_seen:
                self._write_state(state_path, newest_id)
                last_seen = newest_id

            await asyncio.sleep(self._poll_interval_seconds)

    # ─── Persistence helpers ────────────────────────────────────────────────

    @staticmethod
    def _read_state(path: Path) -> str | None:
        try:
            raw = path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return None
        except OSError as exc:
            logger.warning(
                "discord_inbox.state_read_failed",
                path=str(path),
                error=str(exc),
            )
            return None
        return raw or None

    @staticmethod
    def _write_state(path: Path, message_id: str | None) -> None:
        if not message_id:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(message_id, encoding="utf-8")
        except OSError as exc:
            # Persistence is best-effort: if we can't write the cursor
            # we'll just re-poll the same window next tick. Log but
            # don't crash the listener.
            logger.warning(
                "discord_inbox.state_write_failed",
                path=str(path),
                error=str(exc),
            )


def make_listener_function(
    inbox: DiscordInbox, *, seen_state_path: Path | None = None
):
    """Adapt a :class:`DiscordInbox` to the gate's ``listener_function`` contract.

    ``PromotionGate.poll_replies`` calls its listener as a zero-arg
    callable and expects either the next message string or ``None``
    when the per-tick batch is drained. The inbox's native shape is
    an async iterator that runs forever; this adapter:

    * Starts the iterator lazily on first call.
    * Returns the next available message without waiting through the
      inbox's ``poll_interval_seconds`` sleep — we use ``asyncio.wait``
      with a tiny timeout so each ``poll_replies`` tick consumes only
      the messages already buffered server-side at the moment of the
      call, then returns ``None`` so the gate moves on to the next
      cycle stage (expire_stale_requests, etc.). The next lake-governor
      tick picks up any later arrivals.
    """
    state_path = seen_state_path
    iterator: AsyncIterator[str] | None = None
    exhausted = False

    async def _next() -> str | None:
        nonlocal iterator, exhausted
        if exhausted or not inbox.configured:
            return None
        if iterator is None:
            iterator = inbox.listen(state_path).__aiter__()
        try:
            # Race the iterator against a near-zero timeout: anything
            # already queued on the server (and so already inside the
            # inbox's poll loop) returns immediately; anything that
            # would require waiting for the next poll interval is
            # deferred to the next gate tick.
            return await asyncio.wait_for(iterator.__anext__(), timeout=0.01)
        except TimeoutError:
            return None
        except StopAsyncIteration:
            exhausted = True
            return None

    return _next
