"""lake-governor entrypoint — Curation cluster paper-trade + lake state machine.

Tick shape (one cycle, ~minute-level cadence):
  1. PaperTradeHarness pulls a market snapshot and steps each candidate
     in `paper_trade` state, persisting shadow positions + observed
     sharpe/max_dd to `paper_trade_state`.
  2. PromotionGate.scan_eligible looks for paper-trade candidates that
     cleared the observation window + criteria; for each, posts a
     [PROMOTION REQUEST] to Discord and creates a reply-token row.
  3. PromotionGate.poll_replies parses any operator APPROVE/REJECT
     replies in the inbound buffer and applies them.
  4. PromotionGate.expire_stale_requests sweeps the 24h TTL.
  5. (Future ticks: decay detector + per-template breaker run here.
     For W8 v1 the detectors exist in lib but aren't yet driven by
     the runtime loop — wiring lands in W9 alongside the webapp's
     visibility surface.)

Cross-cluster contract: this service NEVER calls decision-engine directly.
Reads `executions` + `positions` (Execution-owned) via Postgres, writes
`lake_roster` + `paper_trade_state` + `discord_reply_tokens` (Curation-owned).
NOTIFY events on `lake_roster_changed` are emitted by the state machine
itself within each transition (W5 commit 23e3d37).
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
from datetime import UTC, datetime
from pathlib import Path

import structlog
from icarus.data_adapters import build_default_adapter
from icarus.db.database import DatabaseConfig, DatabaseManager
from icarus.db.models import Candidate as CandidateRow
from icarus.db.models import LakeRoster
from icarus.discord import ReplyTokenStore, WebhookPoster
from icarus.dsl import build_db_verdict_lookup
from icarus.dsl.registry import TemplateRegistry
from sqlalchemy import select

from lake_governor.discord_inbox import DiscordInbox, make_listener_function
from lake_governor.paper_trade import PaperTradeHarness
from lake_governor.promotion_gate import PromotionGate
from lake_governor.state_machine import CandidateStateMachine

logger = structlog.get_logger(service="lake-governor")

TICK_INTERVAL_SECONDS = int(os.environ.get("LAKE_GOVERNOR_TICK_SECONDS", "60"))
DEFAULT_CHAIN = os.environ.get("LAKE_GOVERNOR_PRIMARY_CHAIN", "base")  # type: ignore[assignment]


async def _no_replies() -> None:
    """Default reply source — always returns ``None`` (no message).

    Used when Discord credentials aren't configured. ``PromotionGate.
    poll_replies`` calls its listener as a zero-arg callable and breaks
    on the first ``None``, so this is the empty-batch sentinel.
    """
    return None


def _build_discord_reply_listener(db: DatabaseManager):
    """Return the zero-arg listener that ``PromotionGate.poll_replies`` consumes.

    When ``DISCORD_BOT_TOKEN`` and ``DISCORD_CHANNEL_ID`` are both set,
    we construct a :class:`DiscordInbox` and adapt its async iterator
    to the gate's per-call contract via
    :func:`make_listener_function`. Otherwise we fall back to
    :func:`_no_replies` — the inbox's ``configured`` flag would do this
    itself, but short-circuiting here keeps the boot-time log line
    clearer and avoids constructing an httpx client that will never
    fire.

    ``db`` is unused today but accepted so the factory signature is
    stable if a future inbox variant needs to persist reply audit rows.
    """
    inbox = DiscordInbox()
    if not inbox.configured:
        return _no_replies
    return make_listener_function(inbox)


class LakeGovernor:
    """Owns the Curation-cluster runtime loop.

    Per-tick work delegates to a paper-trade callable (rebuilds
    `PaperTradeHarness` from the current DB snapshot of paper-trade
    candidates each tick, so newly-promoted-into-paper-trade candidates
    are picked up automatically) and to `PromotionGate` (operator-
    approval round-trip). All durable state lives in Postgres.
    """

    def __init__(
        self,
        *,
        harness_factory: callable,  # async () -> list[CycleOutcome]
        gate: PromotionGate,
        reply_listener: callable,  # zero-arg () -> str | None | awaitable
    ) -> None:
        self._harness = harness_factory
        self._gate = gate
        self._reply_listener = reply_listener
        self._stop = asyncio.Event()

    async def run(self) -> None:
        logger.info("lake_governor_start", interval_s=TICK_INTERVAL_SECONDS)
        while not self._stop.is_set():
            try:
                await self._tick()
            except Exception:
                logger.exception("tick_failed")
            try:
                await asyncio.wait_for(
                    self._stop.wait(), timeout=TICK_INTERVAL_SECONDS
                )
            except TimeoutError:
                continue
        logger.info("lake_governor_stop")

    async def _tick(self) -> None:
        tick_started_at = datetime.now(UTC)

        # 1. Paper-trade harness: step every candidate currently in
        # `paper_trade` state once. PaperTradeHarness binds candidates
        # at construction so we rebuild it each tick from the DB
        # snapshot — newly-promoted-into-paper-trade candidates land in
        # the next tick automatically.
        paper_outcomes = await self._harness()

        # 2. Scan for promotion-eligible candidates and post requests.
        eligible = await self._gate.scan_eligible()
        for entry in eligible:
            await self._gate.request_promotion(entry)

        # 3. Process any operator replies that landed since last tick.
        # Listener is supplied by the entrypoint — Discord polling when
        # credentials are set, or a no-op stub for local/dev runs.
        replies_processed = await self._gate.poll_replies(
            listener_function=self._reply_listener
        )

        # 4. Expire any reply tokens older than the 24h TTL.
        expired = await self._gate.expire_stale_requests()

        logger.info(
            "lake_governor_tick",
            duration_ms=int(
                (datetime.now(UTC) - tick_started_at).total_seconds() * 1000
            ),
            paper_candidates_stepped=len(paper_outcomes),
            promotion_requests_emitted=len(eligible),
            replies_processed=replies_processed,
            reply_tokens_expired=expired,
        )

    def request_stop(self) -> None:
        self._stop.set()


def _state_machine_factory(db: DatabaseManager):
    """Returns a callable(candidate_id) -> CandidateStateMachine.

    The promotion gate needs to construct a fresh state machine per
    candidate without owning their lifecycle. This factory closes over
    the shared DB manager.
    """

    def _make(candidate_id: str) -> CandidateStateMachine:
        return CandidateStateMachine(db, candidate_id)

    return _make


async def _amain() -> int:
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.JSONRenderer(),
        ]
    )
    logger.info("lake_governor_init", started_at=datetime.now(UTC).isoformat())

    db_config = DatabaseConfig()
    db = DatabaseManager(db_config)
    db.create_tables()

    templates_root = Path(os.environ.get("TEMPLATES_DIR", "/app/templates"))
    registry = TemplateRegistry(
        templates_root, verdict_lookup=build_db_verdict_lookup(db)
    )
    registry.load()

    adapter = build_default_adapter()

    async def _step_paper_trade_candidates():
        """Snapshot current paper-trade candidates and run one harness cycle.

        PaperTradeHarness binds candidates at construction — building a
        fresh one per tick keeps newly-promoted candidates included
        without exposing a mutable add/remove API on the harness itself.
        """
        candidates: list[tuple[str, object, dict]] = []
        with db.get_session() as session:
            stmt = (
                select(CandidateRow, LakeRoster)
                .join(LakeRoster, LakeRoster.candidate_id == CandidateRow.candidate_id)
                .where(LakeRoster.state == "paper_trade")
            )
            for cand_row, _roster_row in session.execute(stmt).all():
                template = registry.by_id(cand_row.template_id)
                params = json.loads(cand_row.params_json or "{}")
                candidates.append((cand_row.candidate_id, template, params))
        if not candidates:
            return []
        harness = PaperTradeHarness(
            adapter=adapter,
            chain=DEFAULT_CHAIN,  # type: ignore[arg-type]
            candidates=candidates,
            db=db,
        )
        return await harness.run_cycle()

    webhook_poster = WebhookPoster()  # reads DISCORD_WEBHOOK_URL from env
    reply_token_store = ReplyTokenStore(db=db)

    gate = PromotionGate(
        db=db,
        webhook_poster=webhook_poster,
        reply_token_store=reply_token_store,
        state_machine_factory=_state_machine_factory(db),
    )

    reply_listener = _build_discord_reply_listener(db)

    governor = LakeGovernor(
        harness_factory=_step_paper_trade_candidates,
        gate=gate,
        reply_listener=reply_listener,
    )

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, governor.request_stop)

    try:
        await governor.run()
    finally:
        db.close()
    return 0


def main() -> int:
    return asyncio.run(_amain())


if __name__ == "__main__":
    sys.exit(main())
