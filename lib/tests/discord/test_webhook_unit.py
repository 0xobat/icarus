"""Unit tests for the Discord webhook poster.

The advisor-not-gate principle says ``WebhookPoster.post`` MUST NEVER
raise — Discord outage cannot break the decision cycle. These tests
exercise the three failure surfaces (success, server error, no URL)
and pin the no-raise contract for each.
"""

from __future__ import annotations

from decimal import Decimal

import httpx
import pytest
from icarus.db.database import DatabaseConfig, DatabaseManager
from icarus.discord import ReplyTokenStore, WebhookOutcome, WebhookPoster


@pytest.fixture
def db_manager(tmp_path):
    # File-backed SQLite (not ``:memory:``) so the asyncio.to_thread
    # workers in ReplyTokenStore see the same schema as the test thread.
    db_path = tmp_path / "discord.db"
    mgr = DatabaseManager(DatabaseConfig(url=f"sqlite:///{db_path}", echo=False))
    mgr.create_tables()
    yield mgr
    mgr.close()


def _mock_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_post_success_returns_delivered_true():
    """Mock 204 from Discord → delivered=True, status_code=204, no error."""
    posted: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        posted.append(request)
        return httpx.Response(204)

    client = _mock_client(handler)
    poster = WebhookPoster(
        webhook_url="https://discord.test/webhooks/123/abc",
        client=client,
    )

    outcome = await poster.post("hello world", alert_category="heartbeat")

    assert outcome == WebhookOutcome(delivered=True, status_code=204, error=None)
    assert len(posted) == 1
    assert posted[0].method == "POST"
    # Plain-text content goes in the JSON body's "content" field.
    import json as _json

    body = _json.loads(posted[0].content.decode())
    assert body == {"content": "hello world"}

    await client.aclose()


@pytest.mark.asyncio
async def test_post_5xx_does_not_raise_and_reports_error():
    """5xx response → delivered=False, error populated, NO exception."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="service unavailable")

    client = _mock_client(handler)
    poster = WebhookPoster(
        webhook_url="https://discord.test/webhooks/123/abc",
        client=client,
    )

    outcome = await poster.post("doomed message")

    assert outcome.delivered is False
    assert outcome.status_code == 503
    assert outcome.error is not None
    assert "503" in outcome.error

    await client.aclose()


@pytest.mark.asyncio
async def test_post_no_webhook_configured_is_noop(monkeypatch):
    """No DISCORD_WEBHOOK_URL → no-op, delivered=False, "no webhook configured"."""
    monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)
    poster = WebhookPoster()  # no url, no env

    outcome = await poster.post("anyone listening?")

    assert outcome.delivered is False
    assert outcome.status_code is None
    assert outcome.error == "no webhook configured"


@pytest.mark.asyncio
async def test_post_empty_env_var_treated_as_unset(monkeypatch):
    """``DISCORD_WEBHOOK_URL=`` (empty) must be treated the same as unset.

    `.env.example` ships with an empty value so this is the common
    local-dev case.
    """
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "")
    poster = WebhookPoster()
    outcome = await poster.post("hi")
    assert outcome.delivered is False
    assert outcome.error == "no webhook configured"


@pytest.mark.asyncio
async def test_post_transport_error_does_not_raise():
    """Transport-level failure (e.g. connection error) → delivered=False, no raise."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    client = _mock_client(handler)
    poster = WebhookPoster(
        webhook_url="https://discord.test/webhooks/123/abc",
        client=client,
    )

    outcome = await poster.post("hi")
    assert outcome.delivered is False
    assert outcome.status_code is None
    assert outcome.error is not None
    assert "transport error" in outcome.error

    await client.aclose()


@pytest.mark.asyncio
async def test_post_promotion_request_creates_token_and_posts(db_manager):
    """post_promotion_request → ReplyToken in DB + structured message body."""
    captured: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json

        body = _json.loads(request.content.decode())
        captured.append(body["content"])
        return httpx.Response(204)

    client = _mock_client(handler)
    poster = WebhookPoster(
        webhook_url="https://discord.test/webhooks/123/abc",
        client=client,
    )
    store = ReplyTokenStore(db_manager)

    token = await poster.post_promotion_request(
        template_id="BASIS-PERP-001",
        candidate_id="c-7af3",
        paper_sharpe=1.42,
        paper_max_dd=0.041,
        observation_days=21,
        proposed_allocation_usd=Decimal("2500"),
        allocation_cap_usd=Decimal("5000"),
        llm_advisor_text="78% of returns came in a 4-day window",
        reply_token_store=store,
    )

    assert token.status == "pending"
    assert token.candidate_id == "c-7af3"
    assert token.template_id == "BASIS-PERP-001"
    assert token.kind == "promotion_request"

    assert len(captured) == 1
    content = captured[0]
    # Blueprint format checks — pin each field shows up.
    assert "[PROMOTION REQUEST]" in content
    assert "template: BASIS-PERP-001" in content
    assert "candidate: c-7af3" in content
    assert "Sharpe: 1.42" in content
    assert "21 days" in content
    assert "4.1%" in content
    assert "$2,500" in content
    assert "$5,000" in content
    assert "llm-advisor:" in content
    assert "78% of returns" in content
    assert "APPROVE c-7af3" in content
    assert "REJECT c-7af3" in content

    await client.aclose()


@pytest.mark.asyncio
async def test_post_promotion_request_creates_token_even_when_webhook_down(
    db_manager,
):
    """If the POST fails, the ReplyToken row is still created.

    Operator might respond out-of-band; the durable state is the token row.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    client = _mock_client(handler)
    poster = WebhookPoster(
        webhook_url="https://discord.test/webhooks/123/abc",
        client=client,
    )
    store = ReplyTokenStore(db_manager)

    token = await poster.post_promotion_request(
        template_id="LEND-001",
        candidate_id="c-abc",
        paper_sharpe=1.0,
        paper_max_dd=0.02,
        observation_days=14,
        proposed_allocation_usd=Decimal("1000"),
        allocation_cap_usd=Decimal("2000"),
        llm_advisor_text=None,
        reply_token_store=store,
    )

    assert token.status == "pending"
    assert token.kind == "promotion_request"

    await client.aclose()
