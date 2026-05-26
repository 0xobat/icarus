"""Unit tests for ``harness/signing_dryrun.py`` Phase 1.

Phases 2 + 3 are integration tests that require operator-supplied wallet
keys + a running Node toolchain. They are intentionally out of pytest
scope — see ``harness/signing_dryrun.sh`` for the operator-runnable
gate.

Coverage map (Phase 1 only):

  (a) The CapturingWebhookPoster records every call and returns a
      deterministic ReplyToken keyed by ``next_token_seq``.
  (b) Phase 1 against a seeded eligible candidate returns PASS and the
      candidate row advances paper_trade → live_capped via the real
      ``CandidateStateMachine.promote_to_live_capped``.
  (c) Phase 1 against an empty DB (no seed) returns FAIL with a clear
      "scan_eligible returned 0" detail — this guards against the easy
      regression of "test passes because scan returned []".
  (d) Phase 1 with no APPROVE message reaching the listener returns FAIL
      because the candidate never advances out of paper_trade.

We import the driver module via the absolute path it lives at
(``harness/signing_dryrun.py``); the harness directory is not a package,
so we load it through ``importlib.util.spec_from_file_location`` rather
than as a regular import. This keeps the driver standalone-executable
(its ``__main__`` path stays clean) while still being unit-testable.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from icarus.db.models import Candidate, LakeRoster

# ─────────────────────────────────────────────────────────────────────────────
# Module loader — harness/ is not a python package
# ─────────────────────────────────────────────────────────────────────────────

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DRIVER_PATH = _REPO_ROOT / "harness" / "signing_dryrun.py"


def _load_driver():
    module_name = "icarus_harness_signing_dryrun"
    spec = importlib.util.spec_from_file_location(module_name, str(_DRIVER_PATH))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Register BEFORE exec_module so @dataclass machinery can resolve the
    # owning module via ``sys.modules[cls.__module__]`` (Python 3.12+).
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


driver = _load_driver()


# ─────────────────────────────────────────────────────────────────────────────
# (a) CapturingWebhookPoster — minimal protocol-shaped stub
# ─────────────────────────────────────────────────────────────────────────────


async def test_capturing_webhook_poster_records_and_returns_token():
    poster = driver.CapturingWebhookPoster()
    from decimal import Decimal

    token = await poster.post_promotion_request(
        template_id="T-1",
        candidate_id="c-X",
        paper_sharpe=1.0,
        paper_max_dd=0.05,
        observation_days=15,
        proposed_allocation_usd=Decimal("5"),
        allocation_cap_usd=Decimal("5"),
        llm_advisor_text=None,
    )
    assert len(poster.calls) == 1
    call = poster.calls[0]
    assert call.template_id == "T-1"
    assert call.candidate_id == "c-X"
    assert token.candidate_id == "c-X"
    assert token.template_id == "T-1"
    assert token.token_id.startswith("tok-dryrun-")


# ─────────────────────────────────────────────────────────────────────────────
# (b) Phase 1 happy path — eligible candidate promoted via real state machine
# ─────────────────────────────────────────────────────────────────────────────


async def test_phase1_pass_with_seeded_eligible_candidate(tmp_path):
    db_path = tmp_path / "phase1.db"
    db_url = f"sqlite:///{db_path}"

    result = await driver._run_phase1(db_url)

    assert result.status == driver.PHASE_PASS, result.detail
    assert result.name == "phase1_discord_round_trip"
    assert result.extra["final_state"] == "live_capped"
    assert result.extra["candidate_id"] == "c-dryrun-001"

    # The driver's connection is closed; re-open to inspect persisted state.
    from icarus.db.database import DatabaseConfig, DatabaseManager
    from sqlalchemy import select

    manager = DatabaseManager(DatabaseConfig(url=db_url))
    try:
        with manager.get_session() as session:
            cand = session.execute(
                select(Candidate).where(Candidate.candidate_id == "c-dryrun-001")
            ).scalar_one_or_none()
            roster = session.execute(
                select(LakeRoster).where(LakeRoster.candidate_id == "c-dryrun-001")
            ).scalar_one_or_none()
            assert cand is not None
            assert roster is not None
            assert cand.state == "live_capped"
            assert roster.state == "live_capped"
    finally:
        manager.close()


# ─────────────────────────────────────────────────────────────────────────────
# (c) Phase 1 fails loud when scan returns empty
# ─────────────────────────────────────────────────────────────────────────────


async def test_phase1_fails_when_no_eligible_candidate(tmp_path, monkeypatch):
    """If the seed helper is a no-op the gate scan must produce 0 candidates.

    We monkeypatch _seed_phase1_candidate to a no-op on the driver module
    so the rest of the pipeline runs against an empty DB. This guards
    against the silent-pass regression where scan_eligible returns [] and
    the rest of Phase 1 short-circuits "successfully".
    """
    monkeypatch.setattr(driver, "_seed_phase1_candidate", lambda db, **kw: None)

    db_path = tmp_path / "empty.db"
    db_url = f"sqlite:///{db_path}"

    result = await driver._run_phase1(db_url)

    assert result.status == driver.PHASE_FAIL
    assert "scan_eligible" in result.detail


# ─────────────────────────────────────────────────────────────────────────────
# (d) Phase 1 fails when poll_replies processes nothing
# ─────────────────────────────────────────────────────────────────────────────


async def test_phase1_fails_when_no_messages_processed(tmp_path, monkeypatch):
    """If the synthetic APPROVE never reaches poll_replies, the candidate
    stays in paper_trade and the driver must surface FAIL rather than
    silently returning PASS based on the (correct) scan_eligible result.

    We swap _make_listener for a generator that always returns None so
    poll_replies completes with processed=0.
    """
    monkeypatch.setattr(driver, "_make_listener", lambda msgs: (lambda: None))

    db_path = tmp_path / "no_replies.db"
    db_url = f"sqlite:///{db_path}"

    result = await driver._run_phase1(db_url)

    assert result.status == driver.PHASE_FAIL
    # Either processed=0 OR the final state check fires — both are
    # legitimate failure paths from the same root cause.
    assert ("processed 0" in result.detail) or ("expected candidate+roster" in result.detail)
