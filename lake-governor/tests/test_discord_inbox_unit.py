"""Unit tests for ``DiscordInbox`` — W9 reply-ingestion poller.

Coverage map (per W9 spec):

  (a) Unconfigured (no bot token) → ``listen()`` yields nothing, logs
      a warning, returns without raising.
  (b) Mocked Discord API returns 2 messages → both yield as content
      strings, in oldest-first order.
  (c) After yielding, the seen-state file is updated with the latest
      message id (so a restart doesn't re-emit).
  (d) Mocked 401 response → logs error, stops yielding (the listener
      doesn't loop forever burning quota on a misconfigured bot).
  (e) Rate-limit-remaining=0 with reset-after=2s → poller sleeps for
      the advertised reset window before returning from
      ``messages_since`` (so the *next* call is safe).

We mock ``httpx`` via ``MockTransport`` — no respx dependency needed
and the transport sees every request, which is enough for these tests.
"""

from __future__ import annotations

import asyncio
import json

import httpx
from lake_governor.discord_inbox import DiscordInbox, make_listener_function

# W12 review #2: every inbound message must carry an operator-allowlisted
# author.id or it is dropped before reaching the reply parser. Tests run
# with a single allowlisted id and stamp every fixture message with it.
OPERATOR_ID = "111111111111111111"
ALLOWLIST = frozenset({OPERATOR_ID})


def _msg(id_: str, content: str, *, author: str = OPERATOR_ID) -> dict:
    """Shape one Discord-API-style message fixture."""
    return {"id": id_, "content": content, "author": {"id": author}}


def _json_response(payload: list[dict], headers: dict[str, str] | None = None):
    return httpx.Response(
        200,
        content=json.dumps(payload).encode("utf-8"),
        headers={"content-type": "application/json", **(headers or {})},
    )


def _make_transport(handler):
    return httpx.MockTransport(handler)


def _inbox(**kwargs) -> DiscordInbox:
    """Construct an inbox with the test allowlist applied by default."""
    kwargs.setdefault("operator_user_ids", ALLOWLIST)
    return DiscordInbox(**kwargs)


# ─── (a) Unconfigured inbox is a silent no-op ────────────────────────────────


async def test_listen_unconfigured_yields_nothing(tmp_path, caplog):
    """No bot token / channel id → log a warning and return without yielding."""
    inbox = DiscordInbox(bot_token=None, channel_id=None)
    state_path = tmp_path / "seen.txt"

    collected: list[str] = []
    async for content in inbox.listen(state_path):
        collected.append(content)

    assert collected == []
    # State file is never touched in the no-op path.
    assert not state_path.exists()


async def test_messages_since_unconfigured_yields_nothing(tmp_path):
    inbox = DiscordInbox(bot_token="", channel_id="")
    out: list[tuple[str, str]] = []
    async for item in inbox.messages_since(None):
        out.append(item)
    assert out == []


# ─── (b) Two messages from Discord → both yield, oldest-first ───────────────


async def test_messages_since_yields_two_messages_in_oldest_first_order():
    # Discord returns newest-first; ids are snowflakes (monotonic) so
    # the inbox must reverse the list before yielding.
    payload = [
        _msg("200", "REJECT tok-2 returns concentrated"),
        _msg("100", "APPROVE tok-1"),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/channels/CHAN/messages")
        assert request.headers["Authorization"] == "Bot TOKEN"
        return _json_response(payload, headers={"X-RateLimit-Remaining": "4"})

    async with httpx.AsyncClient(transport=_make_transport(handler)) as client:
        inbox = _inbox(bot_token="TOKEN", channel_id="CHAN", http_client=client)
        out: list[tuple[str, str]] = []
        async for item in inbox.messages_since(None):
            out.append(item)

    assert out == [
        ("100", "APPROVE tok-1"),
        ("200", "REJECT tok-2 returns concentrated"),
    ]


async def test_messages_since_skips_empty_content():
    # Embeds / attachments arrive with empty ``content`` — the inbox
    # should drop them rather than feed empty strings to the parser.
    payload = [
        _msg("300", "   "),
        _msg("200", ""),
        _msg("100", "APPROVE tok-1"),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(payload)

    async with httpx.AsyncClient(transport=_make_transport(handler)) as client:
        inbox = _inbox(bot_token="T", channel_id="C", http_client=client)
        out = [item async for item in inbox.messages_since(None)]

    assert out == [("100", "APPROVE tok-1")]


# ─── (c) listen() persists the latest message id ────────────────────────────


async def test_listen_persists_latest_message_id(tmp_path):
    """After draining one batch, the state file holds the newest id."""
    payload = [
        _msg("500", "APPROVE tok-5"),
        _msg("400", "REJECT tok-4 bad fill"),
    ]
    state_path = tmp_path / "subdir" / "seen.txt"
    # ``listen()`` would loop forever; we drive it through two anext
    # calls and a third that we cancel so the persistence write happens.
    poll_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal poll_count
        poll_count += 1
        if poll_count == 1:
            return _json_response(payload)
        # Subsequent polls return empty so the iterator's sleep is
        # reached and we can cancel cleanly.
        return _json_response([])

    async with httpx.AsyncClient(transport=_make_transport(handler)) as client:
        inbox = _inbox(
            bot_token="T",
            channel_id="C",
            poll_interval_seconds=1,
            http_client=client,
        )
        agen = inbox.listen(state_path).__aiter__()
        first = await agen.__anext__()
        second = await agen.__anext__()
        # The third __anext__ would block on asyncio.sleep — race it
        # against a small timeout to force the state write that happens
        # between the inner-loop completion and the sleep.
        try:
            await asyncio.wait_for(agen.__anext__(), timeout=0.5)
        except TimeoutError:
            pass
        await agen.aclose()

    assert first == "REJECT tok-4 bad fill"
    assert second == "APPROVE tok-5"
    assert state_path.exists()
    assert state_path.read_text(encoding="utf-8").strip() == "500"


# ─── (d) 401 stops the listener — no infinite retry ─────────────────────────


async def test_listen_stops_on_401_auth_error(tmp_path, caplog):
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(401, content=b"{}", headers={"content-type": "application/json"})

    async with httpx.AsyncClient(transport=_make_transport(handler)) as client:
        inbox = _inbox(
            bot_token="BAD",
            channel_id="C",
            poll_interval_seconds=1,
            http_client=client,
        )
        out: list[str] = []
        async for content in inbox.listen(tmp_path / "seen.txt"):
            out.append(content)

    # Listener stopped without yielding; only one HTTP call happened.
    assert out == []
    assert call_count == 1


async def test_messages_since_403_stops_without_yielding():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, content=b"{}", headers={"content-type": "application/json"})

    async with httpx.AsyncClient(transport=_make_transport(handler)) as client:
        inbox = _inbox(bot_token="T", channel_id="C", http_client=client)
        out = [item async for item in inbox.messages_since(None)]
        # Sentinel is set so the listen() wrapper can stop.
        assert inbox._auth_failed is True

    assert out == []


# ─── (e) Rate-limit-remaining=0 → poller sleeps for reset_after ─────────────


async def test_messages_since_sleeps_when_rate_limit_exhausted(monkeypatch):
    """When Discord says remaining=0 we sleep for the reset window."""
    sleeps: list[float] = []

    async def fake_sleep(duration: float) -> None:
        sleeps.append(duration)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    payload = [_msg("1", "APPROVE tok-9")]

    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(
            payload,
            headers={
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset-After": "2",
            },
        )

    async with httpx.AsyncClient(transport=_make_transport(handler)) as client:
        inbox = _inbox(bot_token="T", channel_id="C", http_client=client)
        out = [item async for item in inbox.messages_since(None)]

    assert out == [("1", "APPROVE tok-9")]
    # Backoff sleep was triggered with the advertised reset_after.
    assert 2.0 in sleeps


async def test_messages_since_does_not_sleep_when_remaining_positive(monkeypatch):
    sleeps: list[float] = []

    async def fake_sleep(duration: float) -> None:
        sleeps.append(duration)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(
            [_msg("1", "APPROVE tok-3")],
            headers={"X-RateLimit-Remaining": "3", "X-RateLimit-Reset-After": "5"},
        )

    async with httpx.AsyncClient(transport=_make_transport(handler)) as client:
        inbox = _inbox(bot_token="T", channel_id="C", http_client=client)
        _ = [item async for item in inbox.messages_since(None)]

    # No rate-limit sleep was triggered (only the inner poll-loop sleep
    # would occur, which isn't part of this method).
    assert sleeps == []


# ─── messages_since uses ``after`` cursor when present ──────────────────────


async def test_messages_since_passes_after_cursor():
    seen_url: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen_url["q"] = str(request.url.params)
        return _json_response([])

    async with httpx.AsyncClient(transport=_make_transport(handler)) as client:
        inbox = _inbox(bot_token="T", channel_id="C", http_client=client)
        _ = [item async for item in inbox.messages_since("999")]

    assert "after=999" in seen_url["q"]


# ─── Transport errors are swallowed (no crash, no yield) ────────────────────


async def test_messages_since_swallows_transport_errors():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("simulated network failure")

    async with httpx.AsyncClient(transport=_make_transport(handler)) as client:
        inbox = _inbox(bot_token="T", channel_id="C", http_client=client)
        out = [item async for item in inbox.messages_since(None)]

    assert out == []


# ─── Adapter: make_listener_function bridges to gate's contract ─────────────


async def test_make_listener_function_returns_none_when_unconfigured():
    inbox = DiscordInbox(bot_token=None, channel_id=None)
    listener = make_listener_function(inbox)
    assert await listener() is None


# ─── W12 review #2 regressions: operator allowlist + fail-closed ───────────


async def test_messages_since_drops_non_allowlist_author():
    """A message from a non-allowlisted author is dropped before yield."""
    # Discord returns newest-first; the inbox reverses to yield oldest-first.
    payload = [
        _msg("300", "APPROVE tok-3", author=OPERATOR_ID),
        _msg("200", "APPROVE tok-2", author="999999999"),  # not in allowlist
        _msg("100", "APPROVE tok-1", author=OPERATOR_ID),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(payload)

    async with httpx.AsyncClient(transport=_make_transport(handler)) as client:
        inbox = _inbox(bot_token="T", channel_id="C", http_client=client)
        out = [item async for item in inbox.messages_since(None)]

    assert out == [
        ("100", "APPROVE tok-1"),
        ("300", "APPROVE tok-3"),
    ]


async def test_messages_since_fail_closed_when_allowlist_empty():
    """Empty allowlist means every message is dropped — never authorize."""
    payload = [_msg("100", "APPROVE tok-1", author=OPERATOR_ID)]

    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(payload)

    async with httpx.AsyncClient(transport=_make_transport(handler)) as client:
        inbox = DiscordInbox(
            bot_token="T",
            channel_id="C",
            http_client=client,
            operator_user_ids=frozenset(),  # explicit empty allowlist
        )
        out = [item async for item in inbox.messages_since(None)]

    assert out == []


async def test_messages_since_drops_message_missing_author_id():
    """A message with no author field at all is dropped (malformed input)."""
    payload = [
        {"id": "100", "content": "APPROVE tok-1"},  # no author key
        {"id": "200", "content": "APPROVE tok-2", "author": {}},  # author with no id
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(payload)

    async with httpx.AsyncClient(transport=_make_transport(handler)) as client:
        inbox = _inbox(bot_token="T", channel_id="C", http_client=client)
        out = [item async for item in inbox.messages_since(None)]

    assert out == []
