"""Unit tests for ``PromotionGate`` — the W8 live-promotion round-trip.

Coverage map (per W8 spec):

  (a) scan_eligible() returns paper-trading candidates with observation
      days ≥ window AND passing criteria. Excludes candidates younger
      than the window OR failing criteria OR not in paper_trade.
  (b) meets_promotion_criteria(): passes when Sharpe ≥ 0.8x backtest_oos
      AND MaxDD ≤ 1.2x backtest. Fails with informative reasons.
  (c) request_promotion(): forwards the eligibility fields to
      WebhookPoster.post_promotion_request and returns the ReplyToken.
  (d) poll_replies(): APPROVE → promote_to_live_capped; REJECT → log
      only, no state change; unmatched → counted but ignored.
  (e) expire_stale_requests(): returns the count from the token store.

Stubs for ``WebhookPoster``, ``ReplyTokenStore``, and the state machine
are defined here — Stream A's concrete classes are reconciled at merge,
and the gate tests' contract is the Protocol defined in promotion_gate.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from icarus.db.models import (
    Candidate,
    LakeRoster,
    PaperTradeState,
    ParameterSearchResult,
)
from lake_governor.promotion_gate import (
    CandidateEligibility,
    PromotionGate,
    ReplyToken,
    ReplyTokenMatch,
    meets_promotion_criteria,
)
from lake_governor.state_machine import TransitionResult

# ─────────────────────────────────────────────────────────────────────────────
# Stubs — Protocol-compatible doubles for Stream A's deliverables
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class _StubWebhookPoster:
    """Captures every post_promotion_request call + returns scripted tokens."""

    calls: list[dict] = field(default_factory=list)
    next_token_seq: int = 0

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
    ) -> ReplyToken:
        self.calls.append(
            {
                "template_id": template_id,
                "candidate_id": candidate_id,
                "paper_sharpe": paper_sharpe,
                "paper_max_dd": paper_max_dd,
                "observation_days": observation_days,
                "proposed_allocation_usd": proposed_allocation_usd,
                "allocation_cap_usd": allocation_cap_usd,
                "llm_advisor_text": llm_advisor_text,
            }
        )
        self.next_token_seq += 1
        return ReplyToken(
            token_id=f"tok-{self.next_token_seq:04d}",
            candidate_id=candidate_id,
            template_id=template_id,
        )


@dataclass
class _StubReplyTokenStore:
    """Scripted match_reply + expire_stale results."""

    scripted_matches: dict[str, ReplyTokenMatch] = field(default_factory=dict)
    expire_count: int = 0
    expire_calls: list[int] = field(default_factory=list)
    match_calls: list[str] = field(default_factory=list)

    async def match_reply(self, *, message: str) -> ReplyTokenMatch | None:
        self.match_calls.append(message)
        return self.scripted_matches.get(message)

    async def expire_stale(self, *, ttl_hours: int = 24) -> int:
        self.expire_calls.append(ttl_hours)
        return self.expire_count


@dataclass
class _StubStateMachine:
    """Records promote_to_live_capped invocations without touching DB."""

    candidate_id: str
    promote_calls: list[str] = field(default_factory=list)

    async def promote_to_live_capped(
        self, reason: str = "operator_approval"
    ) -> TransitionResult:
        self.promote_calls.append(reason)
        return TransitionResult(
            candidate_id=self.candidate_id,
            previous_state="paper_trade",
            new_state="live_capped",
            reason=reason,
            noop=False,
        )


# ─────────────────────────────────────────────────────────────────────────────
# DB seeding helpers
# ─────────────────────────────────────────────────────────────────────────────


def _seed_full_chain(
    db,
    *,
    candidate_id: str,
    template_id: str = "TEMPLATE-001",
    template_version: str = "1.0.0",
    state: str = "paper_trade",
    params: dict | None = None,
    observed_sharpe: float | None = 1.0,
    observed_max_dd: float | None = 0.05,
    observation_days: int = 14,
    backtest_oos_sharpe: float | None = 1.0,
    backtest_max_dd: float = 0.05,
    allocation_max_pct: Decimal = Decimal("0.10"),
    create_roster: bool = True,
    create_backtest: bool = True,
) -> None:
    """Insert the full Candidate + LakeRoster + ParameterSearchResult +
    PaperTradeState chain needed to make a candidate eligible (or not).
    """
    params = params or {"x": 1}
    params_json = json.dumps(params)
    now = datetime.now(UTC)
    with db.get_session() as session:
        session.add(
            Candidate(
                candidate_id=candidate_id,
                template_id=template_id,
                template_version=template_version,
                params_json=params_json,
                state=state,
                entered_state_at=now,
            )
        )
        if create_roster:
            session.add(
                LakeRoster(
                    candidate_id=candidate_id,
                    template_id=template_id,
                    state=state,
                    allocation_usd=Decimal("0"),
                    allocation_max_pct=allocation_max_pct,
                    last_transition_at=now,
                    breaker_tripped=False,
                )
            )
        if create_backtest:
            session.add(
                ParameterSearchResult(
                    template_id=template_id,
                    template_version=template_version,
                    params_json=params_json,
                    sharpe=Decimal("1.20"),
                    deflated_sharpe=Decimal("1.10"),
                    max_dd=Decimal(str(backtest_max_dd)),
                    turnover=Decimal("0.5"),
                    oos_sharpe=(
                        Decimal(str(backtest_oos_sharpe))
                        if backtest_oos_sharpe is not None
                        else None
                    ),
                    compute_seconds=Decimal("1.0"),
                    is_top_k=True,
                )
            )
        session.add(
            PaperTradeState(
                candidate_id=candidate_id,
                entered_paper_at=now - timedelta(days=observation_days),
                shadow_positions_json="[]",
                observed_sharpe=observed_sharpe,
                observed_max_dd=observed_max_dd,
                observation_days=observation_days,
                last_updated_at=now,
            )
        )
        session.commit()


def _build_gate(
    db,
    *,
    poster: _StubWebhookPoster | None = None,
    store: _StubReplyTokenStore | None = None,
    state_machines: dict[str, _StubStateMachine] | None = None,
) -> tuple[PromotionGate, _StubWebhookPoster, _StubReplyTokenStore, dict]:
    poster = poster or _StubWebhookPoster()
    store = store or _StubReplyTokenStore()
    sms: dict[str, _StubStateMachine] = state_machines if state_machines is not None else {}

    def factory(candidate_id: str) -> _StubStateMachine:
        sm = sms.get(candidate_id)
        if sm is None:
            sm = _StubStateMachine(candidate_id=candidate_id)
            sms[candidate_id] = sm
        return sm  # type: ignore[return-value]

    gate = PromotionGate(
        db=db,
        webhook_poster=poster,
        reply_token_store=store,
        state_machine_factory=factory,  # type: ignore[arg-type]
    )
    return gate, poster, store, sms


# ─────────────────────────────────────────────────────────────────────────────
# (b) meets_promotion_criteria — pure unit
# ─────────────────────────────────────────────────────────────────────────────


def test_criteria_passes_when_sharpe_above_floor_and_dd_below_ceiling():
    ok, reason = meets_promotion_criteria(
        paper_sharpe=1.0,
        paper_max_dd=0.05,
        backtest_oos_sharpe=1.0,
        backtest_max_dd=0.05,
    )
    assert ok is True
    assert reason == "criteria_met"


def test_criteria_passes_at_exact_sharpe_floor():
    # paper Sharpe at exactly 0.8x backtest_oos — must pass (>=).
    ok, reason = meets_promotion_criteria(
        paper_sharpe=0.8,
        paper_max_dd=0.05,
        backtest_oos_sharpe=1.0,
        backtest_max_dd=0.05,
    )
    assert ok is True, reason


def test_criteria_passes_at_exact_dd_ceiling():
    # paper MaxDD at exactly 1.2x backtest — must pass (<=).
    ok, reason = meets_promotion_criteria(
        paper_sharpe=1.0,
        paper_max_dd=0.06,
        backtest_oos_sharpe=1.0,
        backtest_max_dd=0.05,
    )
    assert ok is True, reason


def test_criteria_fails_when_sharpe_below_floor():
    ok, reason = meets_promotion_criteria(
        paper_sharpe=0.5,
        paper_max_dd=0.04,
        backtest_oos_sharpe=1.0,
        backtest_max_dd=0.05,
    )
    assert ok is False
    assert "sharpe_below_floor" in reason
    assert "0.5" in reason
    assert "0.8" in reason


def test_criteria_fails_when_max_dd_above_ceiling():
    ok, reason = meets_promotion_criteria(
        paper_sharpe=1.0,
        paper_max_dd=0.10,
        backtest_oos_sharpe=1.0,
        backtest_max_dd=0.05,
    )
    assert ok is False
    assert "max_dd_above_ceiling" in reason


def test_criteria_custom_thresholds():
    # If operator tightens the floor to 0.95x, the same row that passed
    # at 0.8x can now fail.
    ok, _ = meets_promotion_criteria(
        paper_sharpe=0.85,
        paper_max_dd=0.04,
        backtest_oos_sharpe=1.0,
        backtest_max_dd=0.05,
        sharpe_ratio_floor=0.95,
    )
    assert ok is False


# ─────────────────────────────────────────────────────────────────────────────
# (a) scan_eligible — DB-backed
# ─────────────────────────────────────────────────────────────────────────────


async def test_scan_eligible_returns_passing_paper_trade_candidate(db):
    _seed_full_chain(
        db,
        candidate_id="c-eligible",
        observed_sharpe=1.0,
        observed_max_dd=0.05,
        observation_days=15,
        backtest_oos_sharpe=1.0,
        backtest_max_dd=0.05,
    )
    gate, *_ = _build_gate(db)

    eligible = await gate.scan_eligible()

    assert len(eligible) == 1
    e = eligible[0]
    assert e.candidate_id == "c-eligible"
    assert e.template_id == "TEMPLATE-001"
    assert e.paper_sharpe == pytest.approx(1.0)
    assert e.paper_max_dd == pytest.approx(0.05)
    assert e.observation_days == 15
    assert e.backtest_oos_sharpe == pytest.approx(1.0)
    assert e.backtest_max_dd == pytest.approx(0.05)
    assert e.proposed_allocation_usd > Decimal("0")
    assert e.allocation_cap_usd > Decimal("0")


async def test_scan_eligible_excludes_under_window(db):
    _seed_full_chain(db, candidate_id="c-young", observation_days=7)
    gate, *_ = _build_gate(db)

    eligible = await gate.scan_eligible()

    assert eligible == []


async def test_scan_eligible_excludes_failing_criteria(db):
    _seed_full_chain(
        db,
        candidate_id="c-low-sharpe",
        observed_sharpe=0.2,
        observed_max_dd=0.05,
        observation_days=20,
        backtest_oos_sharpe=1.0,
        backtest_max_dd=0.05,
    )
    gate, *_ = _build_gate(db)

    eligible = await gate.scan_eligible()

    assert eligible == []


async def test_scan_eligible_excludes_when_not_in_paper_trade(db):
    """A demoted candidate may still have a PaperTradeState row; the
    gate must consult the Candidate.state + LakeRoster.state and skip."""
    _seed_full_chain(
        db,
        candidate_id="c-demoted",
        state="demoted_paper",
        observation_days=30,
    )
    gate, *_ = _build_gate(db)

    eligible = await gate.scan_eligible()

    assert eligible == []


async def test_scan_eligible_excludes_when_observed_metrics_null(db):
    """Sharpe still NULL → not eligible."""
    _seed_full_chain(
        db,
        candidate_id="c-no-metrics",
        observed_sharpe=None,
        observed_max_dd=None,
        observation_days=21,
    )
    gate, *_ = _build_gate(db)

    eligible = await gate.scan_eligible()

    assert eligible == []


async def test_scan_eligible_excludes_when_backtest_row_missing(db):
    """No top-K backtest row → no comparison basis → skip."""
    _seed_full_chain(
        db,
        candidate_id="c-no-backtest",
        create_backtest=False,
        observation_days=15,
    )
    gate, *_ = _build_gate(db)

    eligible = await gate.scan_eligible()

    assert eligible == []


async def test_scan_eligible_mixed_cohort_returns_only_passing(db):
    _seed_full_chain(db, candidate_id="c-good-1", observation_days=15)
    _seed_full_chain(db, candidate_id="c-young-1", observation_days=5)
    _seed_full_chain(
        db,
        candidate_id="c-bad-dd",
        observation_days=20,
        observed_max_dd=0.50,
        backtest_max_dd=0.05,
    )
    _seed_full_chain(db, candidate_id="c-good-2", observation_days=14)
    gate, *_ = _build_gate(db)

    eligible = await gate.scan_eligible()

    ids = sorted(e.candidate_id for e in eligible)
    assert ids == ["c-good-1", "c-good-2"]


# ─────────────────────────────────────────────────────────────────────────────
# (c) request_promotion — webhook poster call shape
# ─────────────────────────────────────────────────────────────────────────────


async def test_request_promotion_calls_poster_with_correct_fields(db):
    eligibility = CandidateEligibility(
        candidate_id="c-req",
        template_id="TEMPLATE-XYZ",
        paper_sharpe=1.23,
        paper_max_dd=0.07,
        backtest_oos_sharpe=1.5,
        backtest_max_dd=0.06,
        observation_days=17,
        proposed_allocation_usd=Decimal("5.0"),
        allocation_cap_usd=Decimal("5.0"),
    )
    gate, poster, _store, _sms = _build_gate(db)

    token = await gate.request_promotion(
        eligibility, llm_advisor_text="advisor: looks fine"
    )

    assert len(poster.calls) == 1
    call = poster.calls[0]
    assert call["candidate_id"] == "c-req"
    assert call["template_id"] == "TEMPLATE-XYZ"
    assert call["paper_sharpe"] == pytest.approx(1.23)
    assert call["paper_max_dd"] == pytest.approx(0.07)
    assert call["observation_days"] == 17
    assert call["proposed_allocation_usd"] == Decimal("5.0")
    assert call["allocation_cap_usd"] == Decimal("5.0")
    assert call["llm_advisor_text"] == "advisor: looks fine"

    assert isinstance(token, ReplyToken)
    assert token.candidate_id == "c-req"
    assert token.template_id == "TEMPLATE-XYZ"


async def test_request_promotion_default_llm_advisor_is_none(db):
    eligibility = CandidateEligibility(
        candidate_id="c-no-advisor",
        template_id="TEMPLATE-Y",
        paper_sharpe=1.0,
        paper_max_dd=0.05,
        backtest_oos_sharpe=1.0,
        backtest_max_dd=0.05,
        observation_days=14,
        proposed_allocation_usd=Decimal("5"),
        allocation_cap_usd=Decimal("5"),
    )
    gate, poster, *_ = _build_gate(db)

    await gate.request_promotion(eligibility)

    assert poster.calls[0]["llm_advisor_text"] is None


# ─────────────────────────────────────────────────────────────────────────────
# (d) poll_replies — APPROVE / REJECT / unmatched
# ─────────────────────────────────────────────────────────────────────────────


def _make_listener(messages: Sequence[str]):
    """Build a sync zero-arg listener that yields then returns None."""
    queue = list(messages)

    def _next() -> str | None:
        if queue:
            return queue.pop(0)
        return None

    return _next


async def test_poll_replies_approve_invokes_promote(db):
    store = _StubReplyTokenStore(
        scripted_matches={
            "approve tok-0001": ReplyTokenMatch(
                token_id="tok-0001",
                candidate_id="c-A",
                template_id="TEMPLATE-001",
                verdict="APPROVE",
                raw_message="approve tok-0001",
            ),
        }
    )
    gate, _poster, _store, sms = _build_gate(db, store=store)

    processed = await gate.poll_replies(
        listener_function=_make_listener(["approve tok-0001"])
    )

    assert processed == 1
    assert "c-A" in sms
    assert sms["c-A"].promote_calls == ["discord_approval_tok-0001"]


async def test_poll_replies_reject_does_not_promote(db):
    store = _StubReplyTokenStore(
        scripted_matches={
            "reject tok-0002": ReplyTokenMatch(
                token_id="tok-0002",
                candidate_id="c-B",
                template_id="TEMPLATE-001",
                verdict="REJECT",
                raw_message="reject tok-0002",
            ),
        }
    )
    gate, _poster, _store, sms = _build_gate(db, store=store)

    processed = await gate.poll_replies(
        listener_function=_make_listener(["reject tok-0002"])
    )

    assert processed == 1
    # No state machine constructed → no promote calls.
    assert "c-B" not in sms or sms["c-B"].promote_calls == []


async def test_poll_replies_unmatched_message_counted_not_promoted(db):
    store = _StubReplyTokenStore(scripted_matches={})
    gate, _poster, _store, sms = _build_gate(db, store=store)

    processed = await gate.poll_replies(
        listener_function=_make_listener(["random chatter"])
    )

    assert processed == 1
    assert sms == {}


async def test_poll_replies_mixed_batch(db):
    store = _StubReplyTokenStore(
        scripted_matches={
            "approve tok-001": ReplyTokenMatch(
                token_id="tok-001",
                candidate_id="c-X",
                template_id="T",
                verdict="approve",  # lowercase — gate normalises
                raw_message="approve tok-001",
            ),
            "reject tok-002": ReplyTokenMatch(
                token_id="tok-002",
                candidate_id="c-Y",
                template_id="T",
                verdict="REJECT",
                raw_message="reject tok-002",
            ),
        }
    )
    gate, _poster, _store, sms = _build_gate(db, store=store)

    processed = await gate.poll_replies(
        listener_function=_make_listener(
            ["approve tok-001", "noise", "reject tok-002"]
        )
    )

    assert processed == 3
    assert sms["c-X"].promote_calls == ["discord_approval_tok-001"]
    assert "c-Y" not in sms or sms["c-Y"].promote_calls == []


async def test_poll_replies_empty_listener_returns_zero(db):
    gate, *_ = _build_gate(db)

    processed = await gate.poll_replies(listener_function=_make_listener([]))

    assert processed == 0


async def test_poll_replies_async_listener_supported(db):
    """Listener may be async — gate awaits the coroutine."""
    store = _StubReplyTokenStore(
        scripted_matches={
            "approve tok-A": ReplyTokenMatch(
                token_id="tok-A",
                candidate_id="c-async",
                template_id="T",
                verdict="APPROVE",
                raw_message="approve tok-A",
            )
        }
    )
    queue = ["approve tok-A"]

    async def async_listener():
        if queue:
            return queue.pop(0)
        return None

    gate, _poster, _store, sms = _build_gate(db, store=store)
    processed = await gate.poll_replies(listener_function=async_listener)

    assert processed == 1
    assert sms["c-async"].promote_calls == ["discord_approval_tok-A"]


# ─────────────────────────────────────────────────────────────────────────────
# (e) expire_stale_requests
# ─────────────────────────────────────────────────────────────────────────────


async def test_expire_stale_returns_count_from_store(db):
    store = _StubReplyTokenStore(expire_count=7)
    gate, *_ = _build_gate(db, store=store)

    count = await gate.expire_stale_requests()

    assert count == 7
    assert store.expire_calls == [24]


async def test_expire_stale_custom_ttl(db):
    store = _StubReplyTokenStore(expire_count=2)
    gate, *_ = _build_gate(db, store=store)

    count = await gate.expire_stale_requests(ttl_hours=6)

    assert count == 2
    assert store.expire_calls == [6]


# ─────────────────────────────────────────────────────────────────────────────
# End-to-end smoke: scan → request → reply → promote
# ─────────────────────────────────────────────────────────────────────────────


async def test_end_to_end_scan_request_approve_round_trip(db):
    """One eligible candidate flows through the full gate cycle."""
    _seed_full_chain(db, candidate_id="c-e2e", observation_days=15)
    poster = _StubWebhookPoster()
    store = _StubReplyTokenStore()
    gate, _poster, _store, sms = _build_gate(db, poster=poster, store=store)

    eligible = await gate.scan_eligible()
    assert len(eligible) == 1

    token = await gate.request_promotion(eligible[0])
    assert poster.calls[0]["candidate_id"] == "c-e2e"

    # Operator replies "APPROVE <token_id>"; Stream A's token store
    # would parse the token id out + return a match.
    approval_message = f"APPROVE {token.token_id}"
    store.scripted_matches[approval_message] = ReplyTokenMatch(
        token_id=token.token_id,
        candidate_id="c-e2e",
        template_id="TEMPLATE-001",
        verdict="APPROVE",
        raw_message=approval_message,
    )

    processed = await gate.poll_replies(
        listener_function=_make_listener([approval_message])
    )
    assert processed == 1
    assert sms["c-e2e"].promote_calls == [f"discord_approval_{token.token_id}"]
