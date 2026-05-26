"""Live-promotion gate — paper_trade → live_capped via Discord approval.

Cluster: Curation. Service: lake-governor. Blueprint milestone: W8.

This module is the round-trip between a paper-trading candidate that has
*earned* live promotion (criteria + observation window cleared) and the
operator's explicit ``APPROVE`` in Discord. The gate is the milestone —
not the first promotion. It must run end-to-end on a hand-seeded
candidate before any extractor-discovered template earns capital.

Per the blueprint (~lines 313, 324, 465):

  Promotion paper_trade → live_capped:
    * paper-trade Sharpe ≥ 0.80 * backtest_oos_sharpe
    * paper-trade MaxDD ≤ 1.20 * backtest_max_dd
    * no breaches of risk profile (advisory — the risk module bars
      candidates with breaches from being eligible; we read that signal
      out-of-band of the criteria function itself)
    * manual operator approval (the Discord round-trip in `poll_replies`)

The gate is **stateless across runs** — every `scan_eligible` query
hits the DB fresh. No in-memory cohort cache. This is the property that
makes the gate safe to schedule from any process (lake-governor restart
mid-cycle is a no-op; the next tick re-reads `paper_trade_state` and
re-derives the eligible set).

Stream A contract — `WebhookPoster` and `ReplyTokenStore` are developed
in parallel and consumed via Protocols here. At merge the exact class
names + method signatures are reconciled; structural typing means a
matching shape (post_promotion_request, match_reply, expire_stale)
satisfies the gate without modification.

Persistence + concurrency: this module owns no DB tables of its own.
It reads from `paper_trade_state`, `candidates`, and
`parameter_search_results`; writes happen only through
`CandidateStateMachine.promote_to_live_capped`, which writes to
`lake_roster` + `candidates` atomically inside `asyncio.to_thread`.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol

import structlog
from icarus.db.database import DatabaseManager
from icarus.db.models import Candidate, LakeRoster, PaperTradeState, ParameterSearchResult
from sqlalchemy import select
from sqlalchemy.orm import Session

from lake_governor.paper_trade import OBSERVATION_WINDOW_DAYS
from lake_governor.state_machine import CandidateStateMachine

__all__ = [
    "CandidateEligibility",
    "PromotionGate",
    "ReplyToken",
    "ReplyTokenMatch",
    "ReplyTokenStore",
    "WebhookPoster",
    "meets_promotion_criteria",
]

logger = structlog.get_logger(service="lake-governor", component="promotion-gate")


# ─── Expected Stream A contracts (Protocol — actual class names may differ) ──


@dataclass(frozen=True)
class ReplyToken:
    """Opaque handle returned by ``WebhookPoster.post_promotion_request``.

    The webhook poster stores a row keyed by this token so an inbound
    Discord reply can be matched back to the candidate it concerns.
    Carried through the gate but never inspected — the only contract is
    that `ReplyTokenMatch.token_id` matches `ReplyToken.token_id`.
    """

    token_id: str
    candidate_id: str
    template_id: str


@dataclass(frozen=True)
class ReplyTokenMatch:
    """Inbound reply matched against a pending token.

    ``verdict`` is the normalised operator decision — Stream A is
    responsible for parsing the raw Discord message ("approve", "APPROVE",
    "yes", "reject", "no") into one of {"APPROVE", "REJECT"}.
    """

    token_id: str
    candidate_id: str
    template_id: str
    verdict: str  # "APPROVE" | "REJECT"
    raw_message: str


class WebhookPoster(Protocol):
    """Stream A's outbound Discord poster.

    The concrete class lives in ``lib/src/icarus/discord/`` (Stream A).
    At merge, the signature here must agree with the implementation;
    until then the gate develops + tests against this Protocol.
    """

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
    ) -> ReplyToken: ...


class ReplyTokenStore(Protocol):
    """Stream A's reply correlation store.

    ``match_reply`` accepts a raw Discord message (typically the message
    content + a referenced token id) and returns a match if one is
    pending. ``expire_stale`` clears tokens older than ``ttl_hours``;
    the gate calls it on a schedule (operator-defined) to keep the table
    bounded.
    """

    async def match_reply(self, *, message: str) -> ReplyTokenMatch | None: ...

    async def expire_stale(self, *, ttl_hours: int = 24) -> int: ...


# ─── Eligibility + criteria ──────────────────────────────────────────────────


@dataclass(frozen=True)
class CandidateEligibility:
    """One paper-trading candidate that has cleared the criteria gate.

    The gate hands one of these to ``request_promotion``. All numeric
    fields are pre-validated (no Nones, no negative observation_days):
    the criteria function rejects rows that have not yet emitted a Sharpe
    or MaxDD reading.
    """

    candidate_id: str
    template_id: str
    paper_sharpe: float
    paper_max_dd: float
    backtest_oos_sharpe: float
    backtest_max_dd: float
    observation_days: int
    proposed_allocation_usd: Decimal
    allocation_cap_usd: Decimal


def meets_promotion_criteria(
    paper_sharpe: float,
    paper_max_dd: float,
    backtest_oos_sharpe: float,
    backtest_max_dd: float,
    *,
    sharpe_ratio_floor: float = 0.80,
    max_dd_ceiling: float = 1.20,
) -> tuple[bool, str]:
    """Pure predicate for the blueprint's paper → live_capped gate.

    Returns ``(passed, reason)``. On failure the reason names the
    specific clause that tripped so the gate can log + surface it for
    the operator dashboard. On success the reason is ``"criteria_met"``.

    Both inputs must be finite, non-NaN floats; the caller is expected
    to short-circuit on Nones from the DB before calling this.

    The blueprint phrases this as:

      observed_sharpe >= 0.8 * backtest_oos_sharpe
      AND observed_max_dd <= 1.2 * backtest_max_dd

    ``backtest_oos_sharpe`` can be zero or negative for a strategy that
    barely cleared the OOS gate; in that case the Sharpe floor is also
    zero/negative and the comparison still works (any non-negative paper
    Sharpe clears a zero floor). We do not special-case it.
    """
    # Sharpe clause
    sharpe_floor = sharpe_ratio_floor * backtest_oos_sharpe
    if paper_sharpe < sharpe_floor:
        return False, (
            f"sharpe_below_floor: paper={paper_sharpe:.4f} < "
            f"{sharpe_ratio_floor} * backtest_oos={backtest_oos_sharpe:.4f} "
            f"= {sharpe_floor:.4f}"
        )
    # MaxDD clause — both are non-negative fractions; ceiling is a relaxation.
    dd_ceiling = max_dd_ceiling * backtest_max_dd
    if paper_max_dd > dd_ceiling:
        return False, (
            f"max_dd_above_ceiling: paper={paper_max_dd:.4f} > "
            f"{max_dd_ceiling} * backtest={backtest_max_dd:.4f} "
            f"= {dd_ceiling:.4f}"
        )
    return True, "criteria_met"


# ─── The gate ────────────────────────────────────────────────────────────────


# Type alias for the state-machine factory — lets tests inject stubs without
# wiring a full DatabaseManager. Default factory builds against the real
# CandidateStateMachine.
StateMachineFactory = Callable[[str], CandidateStateMachine]


def _default_state_machine_factory(
    db: DatabaseManager,
) -> StateMachineFactory:
    def _factory(candidate_id: str) -> CandidateStateMachine:
        return CandidateStateMachine(db, candidate_id)

    return _factory


class PromotionGate:
    """Live-promotion gate — scans, requests, polls, expires.

    Lifecycle (one operator-defined tick):

      1. ``scan_eligible()`` queries `paper_trade_state` for candidates
         in `paper_trade` with observation_days ≥ OBSERVATION_WINDOW_DAYS
         and passing `meets_promotion_criteria` against their backtest
         top-K row.
      2. For each eligible cohort member: ``request_promotion()`` posts
         a Discord webhook with the stats; the returned ReplyToken is
         stored (by Stream A) for later correlation.
      3. ``poll_replies(listener_function=...)`` pulls inbound messages
         from the listener (the operator-supplied source — could be
         Discord gateway, a poll loop, or a test injector) and matches
         each against the token store. APPROVE → promote_to_live_capped.
         REJECT → log + leave candidate in paper_trade.
      4. ``expire_stale_requests()`` clears tokens whose TTL has lapsed.
         The candidate remains in paper_trade; the gate will re-emit a
         request next scan if it's still eligible.

    The gate is stateless: it caches nothing across calls. A restart
    mid-scan loses no work because every ingress (`scan_eligible`,
    `poll_replies`, `expire_stale_requests`) is independently safe to
    re-run.

    Risk-profile breaches: the blueprint's "no breaches of risk profile"
    clause is intentionally NOT enforced inside the criteria function —
    those signals flow via a separate channel (Execution-cluster
    circuit breakers demote candidates *out of* paper_trade, so a
    breached candidate never appears in `scan_eligible` output). This
    keeps the gate's criteria pure + testable.
    """

    def __init__(
        self,
        *,
        db: DatabaseManager,
        webhook_poster: WebhookPoster,
        reply_token_store: ReplyTokenStore,
        state_machine_factory: StateMachineFactory | None = None,
        observation_window_days: int = OBSERVATION_WINDOW_DAYS,
        sharpe_ratio_floor: float = 0.80,
        max_dd_ceiling: float = 1.20,
    ) -> None:
        self._db = db
        self._poster = webhook_poster
        self._tokens = reply_token_store
        self._sm_factory: StateMachineFactory = (
            state_machine_factory
            if state_machine_factory is not None
            else _default_state_machine_factory(db)
        )
        self._observation_window_days = observation_window_days
        self._sharpe_ratio_floor = sharpe_ratio_floor
        self._max_dd_ceiling = max_dd_ceiling

    # ── public API ─────────────────────────────────────────────────────

    async def scan_eligible(self) -> list[CandidateEligibility]:
        """Return the cohort of paper-trading candidates ready for promotion.

        Filtering pipeline (applied in DB then in Python — the criteria
        comparison involves a backtest-row join we keep explicit rather
        than buried in SQL):

          1. `paper_trade_state.observation_days ≥ window`
          2. Both `observed_sharpe` and `observed_max_dd` are non-null
             (a candidate that has not yet produced a Sharpe reading is
             not eligible — there is nothing to compare).
          3. Joined `Candidate.state == "paper_trade"` (defensive — a
             demotion may have raced and left a stale paper_trade_state
             row; the state machine is the source of truth).
          4. Joined `parameter_search_results` row that birthed the
             candidate (`is_top_k = True`, matching template_id +
             params_json). Carries `oos_sharpe` + `max_dd` for the gate.
          5. `meets_promotion_criteria(...)` passes.

        Returns the surviving cohort. Order is by candidate_id ascending
        so test assertions are stable.
        """
        return await asyncio.to_thread(self._sync_scan_eligible)

    async def request_promotion(
        self,
        eligibility: CandidateEligibility,
        *,
        llm_advisor_text: str | None = None,
    ) -> ReplyToken:
        """Post a Discord promotion request for one eligible candidate.

        The poster owns the token-store side of the round-trip — the
        returned ``ReplyToken`` is the handle that future replies
        correlate against. The gate does not persist it locally; the
        token store (Stream A) is the source of truth.

        ``llm_advisor_text`` is an optional advisory string the
        inference service may attach (W6 advisor wiring). It is passed
        through verbatim — the gate does not validate it.
        """
        token = await self._poster.post_promotion_request(
            template_id=eligibility.template_id,
            candidate_id=eligibility.candidate_id,
            paper_sharpe=eligibility.paper_sharpe,
            paper_max_dd=eligibility.paper_max_dd,
            observation_days=eligibility.observation_days,
            proposed_allocation_usd=eligibility.proposed_allocation_usd,
            allocation_cap_usd=eligibility.allocation_cap_usd,
            llm_advisor_text=llm_advisor_text,
        )
        logger.info(
            "promotion_request_posted",
            candidate_id=eligibility.candidate_id,
            template_id=eligibility.template_id,
            token_id=token.token_id,
            paper_sharpe=eligibility.paper_sharpe,
            paper_max_dd=eligibility.paper_max_dd,
            observation_days=eligibility.observation_days,
        )
        return token

    async def poll_replies(
        self,
        *,
        listener_function: Callable[[], Any],
    ) -> int:
        """Drain pending Discord replies and act on each.

        ``listener_function`` is operator-supplied: a zero-arg callable
        (sync or async) that yields the next inbound message string,
        or returns ``None`` when no more messages are available. The
        gate keeps calling it until ``None``.

        For each message:
          * pass to ``reply_token_store.match_reply`` — if no match, log
            and continue (the message wasn't addressed to a pending
            promotion request).
          * APPROVE → invoke ``state_machine.promote_to_live_capped(
                reason="discord_approval_<token_id>")``.
          * REJECT → log + record reason; candidate stays in paper_trade.

        Returns the count of replies processed (matched or unmatched).
        Unmatched messages are counted because the operator needs the
        total to debug "I replied but nothing happened" cases.
        """
        processed = 0
        while True:
            raw = listener_function()
            if asyncio.iscoroutine(raw):
                raw = await raw
            if raw is None:
                break
            processed += 1
            match = await self._tokens.match_reply(message=str(raw))
            if match is None:
                logger.info(
                    "promotion_reply_unmatched",
                    raw_message_preview=str(raw)[:120],
                )
                continue
            verdict = match.verdict.upper()
            if verdict == "APPROVE":
                sm = self._sm_factory(match.candidate_id)
                reason = f"discord_approval_{match.token_id}"
                result = await sm.promote_to_live_capped(reason=reason)
                logger.info(
                    "promotion_approved",
                    candidate_id=match.candidate_id,
                    template_id=match.template_id,
                    token_id=match.token_id,
                    previous_state=result.previous_state,
                    new_state=result.new_state,
                    reason=reason,
                )
            elif verdict == "REJECT":
                logger.info(
                    "promotion_rejected",
                    candidate_id=match.candidate_id,
                    template_id=match.template_id,
                    token_id=match.token_id,
                    raw_message_preview=match.raw_message[:120],
                )
            else:
                logger.warning(
                    "promotion_reply_unknown_verdict",
                    verdict=verdict,
                    token_id=match.token_id,
                    candidate_id=match.candidate_id,
                )
        return processed

    async def expire_stale_requests(self, *, ttl_hours: int = 24) -> int:
        """Drop tokens older than ``ttl_hours`` from the store.

        Returns the count expired. The candidate's state is untouched —
        a re-emitted scan will re-request promotion if the criteria still
        hold.
        """
        count = await self._tokens.expire_stale(ttl_hours=ttl_hours)
        logger.info("promotion_tokens_expired", count=count, ttl_hours=ttl_hours)
        return count

    # ── sync workers ───────────────────────────────────────────────────

    def _sync_scan_eligible(self) -> list[CandidateEligibility]:
        with self._db.get_session() as session:
            # Step 1: pull paper_trade_state rows that have cleared the
            # observation window AND have non-null observed metrics.
            stmt = (
                select(PaperTradeState)
                .where(PaperTradeState.observation_days >= self._observation_window_days)
                .where(PaperTradeState.observed_sharpe.is_not(None))
                .where(PaperTradeState.observed_max_dd.is_not(None))
                .order_by(PaperTradeState.candidate_id)
            )
            pts_rows = list(session.execute(stmt).scalars())

            eligibilities: list[CandidateEligibility] = []
            for pts in pts_rows:
                cand = self._load_candidate_if_paper_trade(session, pts.candidate_id)
                if cand is None:
                    # Defensive — paper_trade_state row may persist after
                    # a demotion (state machine doesn't delete it). Skip.
                    continue
                roster = self._load_roster(session, pts.candidate_id)
                if roster is None or roster.state != "paper_trade":
                    continue
                backtest = self._load_backtest_row(session, cand)
                if backtest is None or backtest.oos_sharpe is None:
                    # No backtest reference → nothing to compare against.
                    # We do NOT promote on missing context.
                    logger.warning(
                        "promotion_scan_missing_backtest",
                        candidate_id=pts.candidate_id,
                        template_id=cand.template_id,
                    )
                    continue

                paper_sharpe = float(pts.observed_sharpe)
                paper_max_dd = float(pts.observed_max_dd)
                backtest_oos = float(backtest.oos_sharpe)
                backtest_dd = float(backtest.max_dd)

                passed, reason = meets_promotion_criteria(
                    paper_sharpe,
                    paper_max_dd,
                    backtest_oos,
                    backtest_dd,
                    sharpe_ratio_floor=self._sharpe_ratio_floor,
                    max_dd_ceiling=self._max_dd_ceiling,
                )
                if not passed:
                    logger.info(
                        "promotion_scan_criteria_failed",
                        candidate_id=pts.candidate_id,
                        template_id=cand.template_id,
                        reason=reason,
                    )
                    continue

                proposed, cap = self._allocation_proposal(roster)
                eligibilities.append(
                    CandidateEligibility(
                        candidate_id=pts.candidate_id,
                        template_id=cand.template_id,
                        paper_sharpe=paper_sharpe,
                        paper_max_dd=paper_max_dd,
                        backtest_oos_sharpe=backtest_oos,
                        backtest_max_dd=backtest_dd,
                        observation_days=int(pts.observation_days),
                        proposed_allocation_usd=proposed,
                        allocation_cap_usd=cap,
                    )
                )

            logger.info(
                "promotion_scan_complete",
                eligible_count=len(eligibilities),
                window_days=self._observation_window_days,
            )
            return eligibilities

    # ── DB helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _load_candidate_if_paper_trade(
        session: Session, candidate_id: str
    ) -> Candidate | None:
        stmt = select(Candidate).where(Candidate.candidate_id == candidate_id)
        cand = session.execute(stmt).scalar_one_or_none()
        if cand is None or cand.state != "paper_trade":
            return None
        return cand

    @staticmethod
    def _load_roster(session: Session, candidate_id: str) -> LakeRoster | None:
        stmt = select(LakeRoster).where(LakeRoster.candidate_id == candidate_id)
        return session.execute(stmt).scalar_one_or_none()

    @staticmethod
    def _load_backtest_row(
        session: Session, candidate: Candidate
    ) -> ParameterSearchResult | None:
        """Find the top-K backtest row that birthed this candidate.

        Match on (template_id, template_version, params_json, is_top_k=True).
        params_json is the canonical key — Research persists the exact JSON
        used to seed the Candidate.
        """
        stmt = (
            select(ParameterSearchResult)
            .where(ParameterSearchResult.template_id == candidate.template_id)
            .where(
                ParameterSearchResult.template_version == candidate.template_version
            )
            .where(ParameterSearchResult.is_top_k.is_(True))
        )
        rows = list(session.execute(stmt).scalars())
        if not rows:
            return None
        # Try exact params_json match first; fall back to the only top-K
        # row if there's exactly one (params_json drift between Research
        # writers + ORM serialisation has bitten us before — keep the
        # fallback narrow).
        target = _canonicalize_params(candidate.params_json)
        for row in rows:
            if _canonicalize_params(row.params_json) == target:
                return row
        if len(rows) == 1:
            return rows[0]
        return None

    @staticmethod
    def _allocation_proposal(roster: LakeRoster) -> tuple[Decimal, Decimal]:
        """Derive the proposed allocation + cap shown to the operator.

        At promotion the state machine moves the roster to live_capped
        with a 0.5x multiplier on `allocation_max_pct`. The cap shown
        here is the percentage expressed against a nominal $1 portfolio
        — the allocator (Stream C, W6) materialises USD numbers from the
        live NAV. Until that wiring lands we surface the pct as a USD
        figure scaled by 100 so the operator sees a plausible
        order-of-magnitude (not a load-bearing number).
        """
        max_pct = Decimal(str(roster.allocation_max_pct))
        cap = max_pct * Decimal("0.5") * Decimal("100")
        # Proposed = the cap itself on first promotion; allocator may
        # ramp down. Until allocator wiring lands, propose == cap.
        return cap, cap


def _canonicalize_params(raw: str | None) -> str:
    """Stable representation of a params_json blob for matching.

    Research writes params_json via ``json.dumps(...)`` with default
    settings; the ORM round-trips it unchanged. We re-parse + re-dump
    with sorted keys to be resilient to insertion-order differences
    across Python versions (3.7+ preserves order but Research's writer
    history is older).
    """
    if not raw:
        return ""
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return raw
    return json.dumps(parsed, sort_keys=True, separators=(",", ":"))
