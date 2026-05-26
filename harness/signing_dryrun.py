"""Discord round-trip + Safe/Squads signing dry-run (W11).

Operator-runnable script that exercises the full
APPROVE → promotion → ExecutionOrder → sign-but-don't-broadcast path on
both chains. Three phases, each independently SKIP-able based on operator
key availability:

  Phase 1 — Discord round-trip (no keys, always runs).
      In-memory SQLite seeded with one paper-trade candidate that meets
      promotion criteria. Stubbed WebhookPoster captures the outbound
      Discord message; a synthetic ``APPROVE <token_id>`` is injected via
      the gate's listener; ``poll_replies`` flows through the real
      ``CandidateStateMachine.promote_to_live_capped``. PASS = the full
      Candidate.state transition from paper_trade → live_capped + the
      LakeRoster row updated in lockstep.

  Phase 2 — Safe (Base) signing dry-run.
      Requires ``WALLET_PRIVATE_KEY``. Shells out to
      ``pnpm --filter ts-executor run sign-dryrun``. Builds a synthetic
      ExecutionOrder for chain="base", invokes the Safe protocol-kit
      signer, and stops BEFORE broadcast. Status SKIP when the Node-side
      script is absent (W11 v1 leaves the integration as a documented
      TODO) — see ts-executor/scripts/sign-dryrun.ts when added.

  Phase 3 — Squads (Solana) signing dry-run.
      Requires ``SOLANA_MEMBER_KEYPAIR_PATH``. Shells out to
      ``pnpm --filter solana-executor run sign-dryrun``. Uses the W7
      single-signer fallback path (SOLANA_MULTISIG_PDA must remain unset
      to avoid the "multisig not implemented" raise). Same TODO note as
      Phase 2 — status SKIP if the Node-side script is absent.

Exit codes:
  0 = every requested phase PASSED (skipped phases do not count as fail).
  1 = at least one requested phase actually FAILED.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import shutil
import subprocess
import sys
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import structlog

# Ensure src layouts importable when invoked outside `uv run`. Matches the
# same belt-and-brace pattern used by harness/breaker_dryrun.py + e2e_smoke.py
# so the script runs under bare ``python`` as well as ``uv run python``.
_HARNESS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_HARNESS_DIR)
for _rel in ("lib/src", "lake-governor/src", "decision-engine/src"):
    _p = os.path.join(_REPO_ROOT, _rel)
    if _p not in sys.path and os.path.isdir(_p):
        sys.path.insert(0, _p)

from icarus.db.database import DatabaseConfig, DatabaseManager  # noqa: E402
from icarus.db.models import (  # noqa: E402
    Candidate,
    LakeRoster,
    PaperTradeState,
    ParameterSearchResult,
)
from lake_governor.paper_trade import OBSERVATION_WINDOW_DAYS  # noqa: E402
from lake_governor.promotion_gate import (  # noqa: E402
    PromotionGate,
    ReplyToken,
    ReplyTokenMatch,
)


def _configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(message)s")
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
    )


log = structlog.get_logger(service="harness", component="signing-dryrun")


# ---------------------------------------------------------------------------
# Phase result enum (string-typed for structured logging)
# ---------------------------------------------------------------------------


PHASE_PASS = "PASS"
PHASE_SKIP = "SKIP"
PHASE_FAIL = "FAIL"


@dataclass
class PhaseResult:
    name: str
    status: str  # PASS | SKIP | FAIL
    detail: str = ""
    extra: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Phase 1 — Discord round-trip (Python-only)
# ---------------------------------------------------------------------------


@dataclass
class CapturedPost:
    """One call captured from the stubbed webhook poster."""

    template_id: str
    candidate_id: str
    paper_sharpe: float
    paper_max_dd: float
    observation_days: int
    proposed_allocation_usd: Decimal
    allocation_cap_usd: Decimal
    llm_advisor_text: str | None


@dataclass
class CapturingWebhookPoster:
    """Stand-in for the real WebhookPoster.

    Records every ``post_promotion_request`` invocation and returns a
    deterministic ReplyToken so Phase 1 can assert the message body
    matches the eligibility row that triggered it.
    """

    calls: list[CapturedPost] = field(default_factory=list)
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
            CapturedPost(
                template_id=template_id,
                candidate_id=candidate_id,
                paper_sharpe=paper_sharpe,
                paper_max_dd=paper_max_dd,
                observation_days=observation_days,
                proposed_allocation_usd=proposed_allocation_usd,
                allocation_cap_usd=allocation_cap_usd,
                llm_advisor_text=llm_advisor_text,
            )
        )
        self.next_token_seq += 1
        return ReplyToken(
            token_id=f"tok-dryrun-{self.next_token_seq:04d}",
            candidate_id=candidate_id,
            template_id=template_id,
        )


@dataclass
class ScriptedReplyTokenStore:
    """Reply-token store driven by a dict[message → match].

    Mirrors the Stream A protocol surface the gate consumes. Phase 1
    seeds the dict with the synthetic ``APPROVE <token>`` we're about to
    feed through the listener.
    """

    scripted: dict[str, ReplyTokenMatch] = field(default_factory=dict)
    matched_calls: list[str] = field(default_factory=list)
    expire_calls: list[int] = field(default_factory=list)
    expire_count: int = 0

    async def match_reply(self, *, message: str) -> ReplyTokenMatch | None:
        self.matched_calls.append(message)
        return self.scripted.get(message)

    async def expire_stale(self, *, ttl_hours: int = 24) -> int:
        self.expire_calls.append(ttl_hours)
        return self.expire_count


def _seed_phase1_candidate(
    db: DatabaseManager,
    *,
    candidate_id: str = "c-dryrun-001",
    template_id: str = "TEMPLATE-DRYRUN",
) -> None:
    """Insert the full Candidate + LakeRoster + ParameterSearchResult +
    PaperTradeState chain so the candidate qualifies on the first scan.
    """
    params = {"x": 1}
    params_json = json.dumps(params)
    now = datetime.now(UTC)
    observation_days = OBSERVATION_WINDOW_DAYS + 1

    with db.get_session() as session:
        session.add(
            Candidate(
                candidate_id=candidate_id,
                template_id=template_id,
                template_version="1.0.0",
                params_json=params_json,
                state="paper_trade",
                entered_state_at=now - timedelta(days=observation_days),
            )
        )
        session.add(
            LakeRoster(
                candidate_id=candidate_id,
                template_id=template_id,
                state="paper_trade",
                allocation_usd=Decimal("0"),
                allocation_max_pct=Decimal("0.10"),
                last_transition_at=now - timedelta(days=observation_days),
                breaker_tripped=False,
            )
        )
        session.add(
            ParameterSearchResult(
                template_id=template_id,
                template_version="1.0.0",
                params_json=params_json,
                sharpe=Decimal("1.20"),
                deflated_sharpe=Decimal("1.10"),
                max_dd=Decimal("0.05"),
                turnover=Decimal("0.5"),
                oos_sharpe=Decimal("1.00"),
                compute_seconds=Decimal("1.0"),
                is_top_k=True,
            )
        )
        session.add(
            PaperTradeState(
                candidate_id=candidate_id,
                entered_paper_at=now - timedelta(days=observation_days),
                shadow_positions_json="[]",
                observed_sharpe=1.0,
                observed_max_dd=0.05,
                observation_days=observation_days,
                last_updated_at=now,
            )
        )
        session.commit()


def _make_listener(messages: Sequence[str]):
    queue = list(messages)

    def _next() -> str | None:
        if queue:
            return queue.pop(0)
        return None

    return _next


async def _run_phase1(db_url: str) -> PhaseResult:
    """Execute the Discord round-trip phase against an ephemeral SQLite."""
    log.info("phase1.start", db_url=db_url)
    manager = DatabaseManager(DatabaseConfig(url=db_url))
    manager.create_tables()
    try:
        candidate_id = "c-dryrun-001"
        template_id = "TEMPLATE-DRYRUN"
        _seed_phase1_candidate(
            manager, candidate_id=candidate_id, template_id=template_id
        )

        poster = CapturingWebhookPoster()
        store = ScriptedReplyTokenStore()
        gate = PromotionGate(
            db=manager,
            webhook_poster=poster,
            reply_token_store=store,
        )

        # Step 1: scan_eligible — must surface the seeded candidate.
        eligible = await gate.scan_eligible()
        log.info(
            "phase1.scan_eligible",
            count=len(eligible),
            candidates=[e.candidate_id for e in eligible],
        )
        if len(eligible) != 1 or eligible[0].candidate_id != candidate_id:
            return PhaseResult(
                name="phase1_discord_round_trip",
                status=PHASE_FAIL,
                detail=(
                    f"scan_eligible returned {len(eligible)} candidates; "
                    f"expected exactly [{candidate_id}]"
                ),
            )

        # Step 2: request_promotion — fires the (stubbed) webhook.
        token = await gate.request_promotion(eligible[0])
        log.info(
            "phase1.request_promotion",
            token_id=token.token_id,
            candidate_id=token.candidate_id,
            poster_calls=len(poster.calls),
        )
        if len(poster.calls) != 1:
            return PhaseResult(
                name="phase1_discord_round_trip",
                status=PHASE_FAIL,
                detail=f"webhook poster received {len(poster.calls)} calls; expected 1",
            )

        # Step 3: inject the synthetic APPROVE message and feed it through
        # poll_replies. The store returns a verdict the gate normalises to
        # APPROVE, which fires the real CandidateStateMachine.
        approval_message = f"APPROVE {token.token_id}"
        store.scripted[approval_message] = ReplyTokenMatch(
            token_id=token.token_id,
            candidate_id=candidate_id,
            template_id=template_id,
            verdict="APPROVE",
            raw_message=approval_message,
        )

        processed = await gate.poll_replies(
            listener_function=_make_listener([approval_message])
        )
        log.info("phase1.poll_replies", processed=processed)
        if processed != 1:
            return PhaseResult(
                name="phase1_discord_round_trip",
                status=PHASE_FAIL,
                detail=f"poll_replies processed {processed} replies; expected 1",
            )

        # Step 4: verify the candidate actually advanced to live_capped via
        # the real state machine. This is the load-bearing assertion — a
        # passing poll_replies that doesn't mutate state would be a silent
        # regression. Note: both tables have an autoincrement integer PK;
        # we query by the candidate_id business key instead.
        from sqlalchemy import select  # local import keeps top clean

        with manager.get_session() as session:
            cand = session.execute(
                select(Candidate).where(Candidate.candidate_id == candidate_id)
            ).scalar_one_or_none()
            roster = session.execute(
                select(LakeRoster).where(LakeRoster.candidate_id == candidate_id)
            ).scalar_one_or_none()
            cand_state = cand.state if cand is not None else None
            roster_state = roster.state if roster is not None else None
        log.info(
            "phase1.final_state",
            candidate_state=cand_state,
            roster_state=roster_state,
        )
        if cand_state != "live_capped" or roster_state != "live_capped":
            return PhaseResult(
                name="phase1_discord_round_trip",
                status=PHASE_FAIL,
                detail=(
                    f"expected candidate+roster state=live_capped; "
                    f"got candidate={cand_state!r} roster={roster_state!r}"
                ),
            )

        log.info("phase1.pass")
        return PhaseResult(
            name="phase1_discord_round_trip",
            status=PHASE_PASS,
            detail="APPROVE → promotion path completed end-to-end",
            extra={
                "token_id": token.token_id,
                "candidate_id": candidate_id,
                "final_state": cand_state,
            },
        )
    except Exception as exc:
        log.error(
            "phase1.exception", error=str(exc), error_type=type(exc).__name__
        )
        return PhaseResult(
            name="phase1_discord_round_trip",
            status=PHASE_FAIL,
            detail=f"unhandled exception: {type(exc).__name__}: {exc}",
        )
    finally:
        manager.close()


# ---------------------------------------------------------------------------
# Phase 2 / 3 — Node-side signing dry-runs
# ---------------------------------------------------------------------------


def _has_pnpm() -> bool:
    return shutil.which("pnpm") is not None


def _node_script_present(package_dir: str, script_name: str) -> bool:
    """Check whether ``pnpm --filter`` would find a runnable script.

    We inspect package.json directly — calling `pnpm run` for a missing
    script returns non-zero with a noisy error, which would muddy the
    structured-log output. A pre-check keeps the SKIP path clean.
    """
    pkg_path = Path(_REPO_ROOT) / package_dir / "package.json"
    if not pkg_path.is_file():
        return False
    try:
        manifest = json.loads(pkg_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    scripts = manifest.get("scripts") or {}
    return script_name in scripts


def _run_node_signing(
    *,
    phase_name: str,
    package_dir: str,
    script_name: str,
    required_env: dict[str, str],
) -> PhaseResult:
    """Shell out to ``pnpm --filter <pkg> run <script>``.

    The Node script is expected to build a synthetic ExecutionOrder,
    invoke the chain-specific signer (Safe / Squads single-signer), and
    print a final JSON line of the form ``{"status":"PASS"|"FAIL", ...}``.
    Stop BEFORE broadcast is the contract — verification happens inside
    the Node script.
    """
    if not _has_pnpm():
        return PhaseResult(
            name=phase_name,
            status=PHASE_SKIP,
            detail="pnpm not installed; cannot drive Node-side signer",
        )
    if not _node_script_present(package_dir, script_name):
        return PhaseResult(
            name=phase_name,
            status=PHASE_SKIP,
            detail=(
                f"package {package_dir!r} has no '{script_name}' script. "
                "TODO(operator): add scripts/sign-dryrun.ts that builds a "
                "synthetic ExecutionOrder, signs via the chain-specific "
                "wallet (Safe protocol-kit / Squads single-signer), and "
                "exits 0 without broadcasting."
            ),
        )

    cmd = ["pnpm", "--filter", package_dir, "run", script_name]
    env = os.environ.copy()
    env.update(required_env)
    log.info(
        "node_signer.invoke",
        phase=phase_name,
        cmd=cmd,
        cwd=_REPO_ROOT,
    )
    try:
        result = subprocess.run(
            cmd,
            cwd=_REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return PhaseResult(
            name=phase_name,
            status=PHASE_FAIL,
            detail=f"node script timed out after 120s: {' '.join(cmd)}",
        )
    except FileNotFoundError as exc:
        return PhaseResult(
            name=phase_name,
            status=PHASE_FAIL,
            detail=f"failed to exec pnpm: {exc}",
        )

    stdout_tail = "\n".join(result.stdout.splitlines()[-20:])
    stderr_tail = "\n".join(result.stderr.splitlines()[-10:])
    log.info(
        "node_signer.result",
        phase=phase_name,
        exit_code=result.returncode,
        stdout_tail=stdout_tail,
        stderr_tail=stderr_tail,
    )
    if result.returncode != 0:
        return PhaseResult(
            name=phase_name,
            status=PHASE_FAIL,
            detail=(
                f"node script exited {result.returncode}; "
                f"stderr tail: {stderr_tail[:200]}"
            ),
        )
    return PhaseResult(
        name=phase_name,
        status=PHASE_PASS,
        detail="node signer reported success",
    )


def _run_phase2() -> PhaseResult:
    """Safe (Base) signing dry-run.

    SKIP unless WALLET_PRIVATE_KEY is set. The operator's shell wrapper
    is the primary gate (it omits the phase when the env var is absent)
    but we duplicate the check here so a direct ``python signing_dryrun.py``
    invocation behaves the same way.
    """
    if not os.environ.get("WALLET_PRIVATE_KEY"):
        return PhaseResult(
            name="phase2_safe_signing",
            status=PHASE_SKIP,
            detail="WALLET_PRIVATE_KEY not set; skip Safe signing dry-run",
        )
    return _run_node_signing(
        phase_name="phase2_safe_signing",
        package_dir="ts-executor",
        script_name="sign-dryrun",
        required_env={
            # The operator's WALLET_PRIVATE_KEY is already in os.environ;
            # the Node script reads it directly via process.env. No
            # additional injection needed.
        },
    )


def _run_phase3() -> PhaseResult:
    """Squads (Solana) signing dry-run.

    SKIP unless SOLANA_MEMBER_KEYPAIR_PATH is set. We deliberately do NOT
    set SOLANA_MULTISIG_PDA — the W7 Squads multisig path raises
    "not implemented in W7 v1", so the dry-run must exercise the
    single-signer fallback (env-controlled by the absence of the PDA).
    """
    if not os.environ.get("SOLANA_MEMBER_KEYPAIR_PATH"):
        return PhaseResult(
            name="phase3_squads_signing",
            status=PHASE_SKIP,
            detail="SOLANA_MEMBER_KEYPAIR_PATH not set; skip Squads signing dry-run",
        )
    # Force single-signer fallback even if the operator has SOLANA_MULTISIG_PDA
    # set in their shell — the W7 multisig path raises by design and would
    # produce a confusing FAIL inside the Node script.
    forced_env: dict[str, str] = {"SOLANA_MULTISIG_PDA": ""}
    return _run_node_signing(
        phase_name="phase3_squads_signing",
        package_dir="solana-executor",
        script_name="sign-dryrun",
        required_env=forced_env,
    )


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


async def _amain(args: argparse.Namespace) -> int:
    results: list[PhaseResult] = []

    # Phase 1 — always.
    results.append(await _run_phase1(args.db_url))

    # Phase 2 — Safe (Base).
    if args.skip_phase2:
        results.append(
            PhaseResult(
                name="phase2_safe_signing",
                status=PHASE_SKIP,
                detail="--skip-phase2 requested",
            )
        )
    else:
        results.append(_run_phase2())

    # Phase 3 — Squads (Solana).
    if args.skip_phase3:
        results.append(
            PhaseResult(
                name="phase3_squads_signing",
                status=PHASE_SKIP,
                detail="--skip-phase3 requested",
            )
        )
    else:
        results.append(_run_phase3())

    # Render the summary as a single structlog event so the shell wrapper
    # can grep one line for the verdict.
    summary = {r.name: r.status for r in results}
    fail_count = sum(1 for r in results if r.status == PHASE_FAIL)
    pass_count = sum(1 for r in results if r.status == PHASE_PASS)
    skip_count = sum(1 for r in results if r.status == PHASE_SKIP)
    log.info(
        "signing_dryrun.summary",
        results=summary,
        pass_count=pass_count,
        skip_count=skip_count,
        fail_count=fail_count,
        details={r.name: r.detail for r in results},
    )

    return 0 if fail_count == 0 else 1


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db-url",
        default=f"sqlite:///{_REPO_ROOT}/.signing-dryrun-{uuid.uuid4().hex}.db",
        help="SQLite URL for Phase 1. Defaults to a unique per-run file.",
    )
    parser.add_argument(
        "--skip-phase2",
        action="store_true",
        help="Skip Phase 2 (Safe / Base) even if WALLET_PRIVATE_KEY is set.",
    )
    parser.add_argument(
        "--skip-phase3",
        action="store_true",
        help="Skip Phase 3 (Squads / Solana) even if SOLANA_MEMBER_KEYPAIR_PATH is set.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    _configure_logging()
    args = _parse_args(argv)
    return asyncio.run(_amain(args))


if __name__ == "__main__":
    sys.exit(main())
