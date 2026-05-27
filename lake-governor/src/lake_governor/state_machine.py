"""Candidate state machine — one instance per candidate.

The lake's working state lives in two ORM rows:

* ``Candidate`` (Research-owned at creation, mutated here) — the durable
  registry row; carries ``state`` + ``entered_state_at``.
* ``LakeRoster`` (Curation-owned) — the lake-governor's live view used by
  decision-engine to size allocations; created on the
  ``backtest → paper_trade`` transition and updated on every subsequent
  hop.

This module is pure transition logic + DB writes. It does **not** invoke
risk modules, decay detectors, breakers, executors, or Discord. Those
callers detect the trigger and invoke a transition method here.

Per the blueprint (~lines 319-327):

* States: ``backtest → paper_trade → live_capped → live_mature``, with
  demotion paths into ``demoted_paper`` and ``archived`` from anywhere.
* ``live_capped`` allocation cap is 0.5x ``allocation_max_pct``;
  ``live_mature`` gets the full cap.
* Demotion triggers (decay detector, per-template breaker, circuit
  breaker) are **idempotent**: re-demoting an already ``demoted_paper``
  candidate is a no-op that still records the reason in the log stream.
* **Circuit breakers always win.** If a CB fires concurrently with a
  decay or template-breaker trip, the CB's reason is the one persisted
  on the roster row (``last_demotion_reason`` field is the structured
  log event, but ``breaker_tripped`` flips True only for the CB path).

All DB writes use ``asyncio.to_thread`` so the lake-governor event loop
stays responsive while sync SQLAlchemy sessions block.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Final

import structlog
from icarus.db.database import DatabaseManager
from icarus.db.models import CANDIDATE_STATES, Candidate, LakeRoster
from icarus.db.notify import (
    CHANNEL_LAKE_ROSTER_CHANGED,
    LakeRosterChangedPayload,
    notify,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

logger = structlog.get_logger(service="lake-governor", component="state-machine")


# ─── Transition table ────────────────────────────────────────────────────────
# Source-of-truth for ``is_transition_allowed`` and for the runtime guard in
# each transition method. Frozen so callers/tests can't mutate it.
#
# Format: from_state → frozenset of allowed to_states.
_ALLOWED_TRANSITIONS: Final[dict[str, frozenset[str]]] = {
    "backtest": frozenset({"paper_trade", "archived"}),
    # paper_trade is *not* in a "live" tier yet, so the demotion paths
    # (decay / template breaker / CB) don't apply here — those only
    # trip on live_capped/live_mature per the blueprint. A failing
    # paper-trade candidate is archived rather than demoted.
    "paper_trade": frozenset({"live_capped", "archived"}),
    "live_capped": frozenset({"live_mature", "demoted_paper", "archived"}),
    "live_mature": frozenset({"demoted_paper", "archived"}),
    "demoted_paper": frozenset({"paper_trade", "archived"}),
    "archived": frozenset(),  # terminal
}

# Sanity: every key + value lives in the canonical CANDIDATE_STATES tuple.
# Catches drift if the tuple ever grows a new state without updating the
# table here.
_known = set(CANDIDATE_STATES)
assert set(_ALLOWED_TRANSITIONS) == _known, (
    f"transition table keys diverge from CANDIDATE_STATES: "
    f"{set(_ALLOWED_TRANSITIONS) ^ _known}"
)
for _from, _tos in _ALLOWED_TRANSITIONS.items():
    assert _tos <= _known, f"unknown target states from {_from!r}: {_tos - _known}"


# ``live_capped`` operates at 0.5x the candidate's ``allocation_max_pct``;
# ``live_mature`` gets the full cap. Other states allocate 0.
_LIVE_CAPPED_MULTIPLIER: Final[Decimal] = Decimal("0.5")
_LIVE_MATURE_MULTIPLIER: Final[Decimal] = Decimal("1.0")


class InvalidTransitionError(RuntimeError):
    """Raised when a caller asks for a transition not in the table."""

    def __init__(self, candidate_id: str, from_state: str, to_state: str) -> None:
        super().__init__(
            f"candidate {candidate_id!r}: {from_state} → {to_state} is not allowed"
        )
        self.candidate_id = candidate_id
        self.from_state = from_state
        self.to_state = to_state


@dataclass(frozen=True)
class TransitionResult:
    """Returned by every transition method.

    ``previous_state`` is what the roster row held before the transition;
    ``new_state`` is what it holds after. ``noop`` is True when the
    transition was idempotent (already in the target state — happens on
    repeated demotions per the precedence rule).
    """

    candidate_id: str
    previous_state: str
    new_state: str
    reason: str
    noop: bool


class CandidateStateMachine:
    """State machine for one candidate's roster row.

    Instantiate per-candidate (cheap — holds only the candidate_id and a
    reference to the DatabaseManager). The transition methods open a
    fresh sync ``Session`` inside ``asyncio.to_thread`` so a single
    instance is safe to call from multiple awaits if a caller chooses.

    All transitions are atomic at the DB level (single ``commit()``).
    """

    def __init__(self, db: DatabaseManager, candidate_id: str) -> None:
        self._db = db
        self._candidate_id = candidate_id

    # ─── Static helpers ────────────────────────────────────────────────────

    @staticmethod
    def is_transition_allowed(from_state: str, to_state: str) -> bool:
        """Return True iff ``from_state → to_state`` is in the table.

        Used by tests, by the webapp to grey out invalid manual actions,
        and internally by each transition method as the guard.
        """
        if from_state not in _ALLOWED_TRANSITIONS:
            return False
        return to_state in _ALLOWED_TRANSITIONS[from_state]

    @staticmethod
    def allowed_targets(from_state: str) -> frozenset[str]:
        """Return the set of states reachable from ``from_state``."""
        return _ALLOWED_TRANSITIONS.get(from_state, frozenset())

    # ─── Transition methods ────────────────────────────────────────────────

    async def enter_paper_trade(
        self, allocation_max_pct: Decimal, template_id: str, reason: str = "top_k_promotion"
    ) -> TransitionResult:
        """``backtest → paper_trade``.

        Creates the ``LakeRoster`` row (it did not exist while the
        candidate was still ``backtest``). The Candidate row's
        ``state``/``entered_state_at`` are updated in lockstep so the
        two views never drift.
        """
        return await asyncio.to_thread(
            self._sync_enter_paper_trade, allocation_max_pct, template_id, reason
        )

    async def promote_to_live_capped(
        self, reason: str = "operator_approval"
    ) -> TransitionResult:
        """``paper_trade → live_capped``.

        Allocation cap is set to 0.5x ``allocation_max_pct`` per the
        blueprint. The caller (live-promotion gate) is responsible for
        verifying the observation-window criteria + operator approval
        **before** invoking this method.
        """
        return await asyncio.to_thread(
            self._sync_promote, "live_capped", reason, _LIVE_CAPPED_MULTIPLIER
        )

    async def promote_to_live_mature(
        self, reason: str = "operator_approval"
    ) -> TransitionResult:
        """``live_capped → live_mature``.

        Allocation cap rises to the full ``allocation_max_pct``. Caller
        (operator approval handler) is responsible for verifying the
        4-week + Sharpe-within-25% + DD-within-1.2x + zero-CB criteria.
        """
        return await asyncio.to_thread(
            self._sync_promote, "live_mature", reason, _LIVE_MATURE_MULTIPLIER
        )

    async def demote_decay(self, reason: str = "decay_detector_trip") -> TransitionResult:
        """``live_capped|live_mature → demoted_paper`` via Page-Hinkley trip.

        Idempotent: if the candidate is already ``demoted_paper`` the
        call is a no-op (the trigger is recorded in the log stream but
        the row is not touched). This matches the blueprint's
        "conflicting fires are idempotent" rule.
        """
        return await asyncio.to_thread(
            self._sync_demote, reason, breaker_tripped=False, source="decay"
        )

    async def demote_template_breaker(
        self, reason: str = "template_breaker_trip"
    ) -> TransitionResult:
        """``live_capped|live_mature → demoted_paper`` via per-template breaker.

        Same idempotency contract as ``demote_decay``. The caller fans
        this out across every candidate sharing the template.
        """
        return await asyncio.to_thread(
            self._sync_demote, reason, breaker_tripped=False, source="template_breaker"
        )

    async def demote_circuit_breaker(
        self, reason: str = "circuit_breaker_trip"
    ) -> TransitionResult:
        """``live_capped|live_mature → demoted_paper`` via Execution-cluster CB.

        Precedence: circuit breakers always win. This path sets
        ``LakeRoster.breaker_tripped = True``; the decay / template-breaker
        paths leave it False. If a CB fires after a decay demotion (so
        the candidate is already ``demoted_paper``), the breaker flag is
        still raised and the CB reason is recorded — that is the
        "CB wins" semantics in practice.
        """
        return await asyncio.to_thread(
            self._sync_demote, reason, breaker_tripped=True, source="circuit_breaker"
        )

    async def archive(self, reason: str = "operator_archive") -> TransitionResult:
        """``* → archived``. Terminal state. Allowed from any non-archived state."""
        return await asyncio.to_thread(self._sync_archive, reason)

    # ─── Sync workers (run inside asyncio.to_thread) ───────────────────────

    def _sync_enter_paper_trade(
        self, allocation_max_pct: Decimal, template_id: str, reason: str
    ) -> TransitionResult:
        with self._db.get_session() as session:
            candidate = self._load_candidate(session)
            previous = candidate.state
            self._guard(previous, "paper_trade")

            roster = self._load_roster(session)
            if roster is not None:
                # Defensive: roster shouldn't exist when candidate is still
                # in `backtest`. If it does, the system is in a corrupt
                # state — refuse rather than silently double-insert.
                raise InvalidTransitionError(
                    self._candidate_id, previous, "paper_trade"
                ) from RuntimeError(
                    f"roster row already exists for backtest candidate "
                    f"{self._candidate_id!r}"
                )

            now = datetime.now(UTC)
            roster = LakeRoster(
                candidate_id=self._candidate_id,
                template_id=template_id,
                state="paper_trade",
                allocation_usd=Decimal("0"),
                allocation_max_pct=allocation_max_pct,
                last_transition_at=now,
                breaker_tripped=False,
            )
            session.add(roster)
            candidate.state = "paper_trade"
            candidate.entered_state_at = now
            self._emit_lake_roster_changed(
                session,
                previous_state=previous,
                new_state="paper_trade",
                template_id=template_id,
                reason=reason,
            )
            session.commit()

            self._log(previous, "paper_trade", reason, noop=False)
            return TransitionResult(
                candidate_id=self._candidate_id,
                previous_state=previous,
                new_state="paper_trade",
                reason=reason,
                noop=False,
            )

    def _sync_promote(
        self, target: str, reason: str, allocation_multiplier: Decimal
    ) -> TransitionResult:
        with self._db.get_session() as session:
            candidate = self._load_candidate(session)
            roster = self._require_roster(session)
            previous = roster.state
            self._guard(previous, target)

            now = datetime.now(UTC)
            roster.state = target
            roster.last_transition_at = now
            # ``allocation_max_pct`` on the row is the *baseline* cap
            # (set in enter_paper_trade). The 0.5x live_capped
            # invariant is applied at READ time by the decision-engine's
            # RosterListener — we deliberately do NOT mutate the field
            # here so the state machine can move freely between
            # live_capped ↔ live_mature without forgetting the baseline.
            # See decision-engine/roster_listener.py::_STATE_CAP_MULTIPLIER.
            roster.allocation_usd = Decimal("0")  # allocator will refill
            candidate.state = target
            candidate.entered_state_at = now
            self._emit_lake_roster_changed(
                session,
                previous_state=previous,
                new_state=target,
                template_id=roster.template_id,
                reason=reason,
            )
            session.commit()

            self._log(
                previous,
                target,
                reason,
                noop=False,
                allocation_multiplier=str(allocation_multiplier),
            )
            return TransitionResult(
                candidate_id=self._candidate_id,
                previous_state=previous,
                new_state=target,
                reason=reason,
                noop=False,
            )

    def _sync_demote(
        self, reason: str, *, breaker_tripped: bool, source: str
    ) -> TransitionResult:
        with self._db.get_session() as session:
            roster = self._require_roster(session)
            previous = roster.state

            # Idempotent: if already in `demoted_paper`, do not touch
            # state/transition timestamp. Still raise the breaker flag if
            # this is a CB-sourced demotion (CB wins precedence — see
            # class docstring).
            if previous == "demoted_paper":
                changed = False
                if breaker_tripped and not roster.breaker_tripped:
                    roster.breaker_tripped = True
                    session.commit()
                    changed = True
                self._log(
                    previous,
                    "demoted_paper",
                    reason,
                    noop=True,
                    source=source,
                    breaker_flag_raised=changed,
                )
                return TransitionResult(
                    candidate_id=self._candidate_id,
                    previous_state=previous,
                    new_state="demoted_paper",
                    reason=reason,
                    noop=True,
                )

            # Demotion is allowed from live_capped / live_mature only
            # (not from paper_trade — a paper-trade candidate can be
            # archived but is not "demoted" from a tier it never held).
            self._guard(previous, "demoted_paper")

            now = datetime.now(UTC)
            roster.state = "demoted_paper"
            roster.last_transition_at = now
            roster.allocation_usd = Decimal("0")
            if breaker_tripped:
                roster.breaker_tripped = True

            candidate = self._load_candidate(session)
            candidate.state = "demoted_paper"
            candidate.entered_state_at = now
            self._emit_lake_roster_changed(
                session,
                previous_state=previous,
                new_state="demoted_paper",
                template_id=roster.template_id,
                reason=reason,
            )
            session.commit()

            self._log(
                previous,
                "demoted_paper",
                reason,
                noop=False,
                source=source,
                breaker_tripped=roster.breaker_tripped,
            )
            return TransitionResult(
                candidate_id=self._candidate_id,
                previous_state=previous,
                new_state="demoted_paper",
                reason=reason,
                noop=False,
            )

    def _sync_archive(self, reason: str) -> TransitionResult:
        with self._db.get_session() as session:
            candidate = self._load_candidate(session)
            previous = candidate.state
            self._guard(previous, "archived")

            now = datetime.now(UTC)
            candidate.state = "archived"
            candidate.entered_state_at = now

            roster = self._load_roster(session)
            if roster is not None:
                # Roster may not exist if archiving directly from
                # `backtest` (no paper_trade ever entered).
                roster.state = "archived"
                roster.last_transition_at = now
                roster.allocation_usd = Decimal("0")
                self._emit_lake_roster_changed(
                    session,
                    previous_state=previous,
                    new_state="archived",
                    template_id=roster.template_id,
                    reason=reason,
                )
            session.commit()

            self._log(previous, "archived", reason, noop=False)
            return TransitionResult(
                candidate_id=self._candidate_id,
                previous_state=previous,
                new_state="archived",
                reason=reason,
                noop=False,
            )

    # ─── Internals ─────────────────────────────────────────────────────────

    def _guard(self, from_state: str, to_state: str) -> None:
        if not self.is_transition_allowed(from_state, to_state):
            raise InvalidTransitionError(self._candidate_id, from_state, to_state)

    def _emit_lake_roster_changed(
        self,
        session: Session,
        *,
        previous_state: str,
        new_state: str,
        template_id: str,
        reason: str,
    ) -> None:
        """Emit the cluster-seam notification *in the same transaction*
        as the row mutation. Postgres delivers the NOTIFY only on commit;
        SQLite degrades to a debug log per Stream D's no-op fallback.

        This is the wire that satisfies the cluster invariant: cross-cluster
        reads via Postgres notify, never service-to-service direct calls.
        Decision-engine LISTENs on ``lake_roster_changed`` and reacts to
        every transition emitted here.
        """
        payload = LakeRosterChangedPayload(
            candidate_id=self._candidate_id,
            template_id=template_id,
            previous_state=previous_state,
            new_state=new_state,
            transition_reason=reason,
            correlation_id=uuid.uuid4().hex,
            emitted_at=datetime.now(UTC),
        )
        notify(session, CHANNEL_LAKE_ROSTER_CHANGED, payload)

    def _load_candidate(self, session: Session) -> Candidate:
        stmt = select(Candidate).where(Candidate.candidate_id == self._candidate_id)
        row = session.execute(stmt).scalar_one_or_none()
        if row is None:
            raise LookupError(
                f"candidate {self._candidate_id!r} not found in `candidates` table"
            )
        return row

    def _load_roster(self, session: Session) -> LakeRoster | None:
        stmt = select(LakeRoster).where(LakeRoster.candidate_id == self._candidate_id)
        return session.execute(stmt).scalar_one_or_none()

    def _require_roster(self, session: Session) -> LakeRoster:
        roster = self._load_roster(session)
        if roster is None:
            raise LookupError(
                f"candidate {self._candidate_id!r} has no `lake_roster` row — "
                "did the caller skip enter_paper_trade()?"
            )
        return roster

    def _log(
        self,
        previous: str,
        new: str,
        reason: str,
        *,
        noop: bool,
        **extra: object,
    ) -> None:
        logger.info(
            "candidate_state_transition",
            candidate_id=self._candidate_id,
            previous_state=previous,
            new_state=new,
            reason=reason,
            noop=noop,
            **extra,
        )
