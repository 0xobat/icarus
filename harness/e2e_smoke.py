"""End-to-end smoke test (W9) — paper → paper-trade → orders on both chains.

This driver proves the full Research → Curation → Execution path is wired
correctly without waiting 5 real days of data. It is the in-repo, fixture-
driven version of the blueprint's "paper-to-paper-trade in 5 days" gate.

Sequence (per template — LEND-001 on Base, LEND-KAMINO-001 on Solana):

  1. Initialise SQLite via DatabaseManager + Base.metadata.create_all.
  2. Load TemplateRegistry from templates/.
  3. Synthesise 3 top-K ParameterSearchResult rows (Sharpe ~1.0, MaxDD ~3%).
  4. Synthesise 6 WalkForwardResult rows per candidate (matches the W3
     walk-forward (60, 15, 3) window count).
  5. For each top-K row, create a Candidate (backtest state) and call
     CandidateStateMachine.enter_paper_trade(...) to land in paper_trade
     with a LakeRoster row.
  6. Simulate 15 days of paper-trade observations by writing
     PaperTradeState directly (observed_sharpe=0.85, observed_max_dd=0.03 —
     both inside the W5 promotion criteria).
  7. Run lake-governor's PromotionGate.scan_eligible — assert ≥1 eligible
     candidate per template (>=4 total).
  8. Promote one eligible candidate per template to live_capped so the
     RosterCache.live() set has at least one entry per chain.
  9. Run the decision-engine cycle once with stub adapter/regime/allocator
     and a RedisExecutorPublisher backed by fakeredis; assert that at
     least one ExecutionOrder is published on each of
     ``execution:orders:base`` and ``execution:orders:solana``.

Exit 0 on full success; exit 1 with a clear failure message on any
assertion miss. Every step logs to structlog so an operator running the
script can trace the path.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import fakeredis.aioredis
import structlog
from decision_engine.cycle import DecisionCycle, RedisExecutorPublisher
from decision_engine.risk_gate import RiskContext, RiskDecision
from decision_engine.roster_listener import RosterCache, RosterEntry
from icarus.db.database import DatabaseConfig, DatabaseManager
from icarus.db.models import (
    Candidate,
    LakeRoster,
    PaperTradeState,
    ParameterSearchResult,
    WalkForwardResult,
)
from icarus.dsl.registry import TemplateRegistry
from icarus.envelopes.orders import ExecutionOrder
from icarus.protocols.allocator import AllocationDecision
from icarus.protocols.regime import Regime
from icarus.types import Decision, MarketSnapshot, PortfolioSnapshot
from icarus.types.market import Chain, PoolState
from lake_governor.paper_trade import OBSERVATION_WINDOW_DAYS
from lake_governor.promotion_gate import (
    CandidateEligibility,
    PromotionGate,
    ReplyToken,
    ReplyTokenMatch,
)
from lake_governor.state_machine import CandidateStateMachine

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_ROOT = REPO_ROOT / "templates"

TEMPLATE_IDS: tuple[str, ...] = ("LEND-001", "LEND-KAMINO-001")
TOP_K = 3
WALK_FORWARD_WINDOWS = 6  # matches manifest walk_forward [60, 15, 3] count
PAPER_TRADE_OBSERVATION_DAYS = OBSERVATION_WINDOW_DAYS + 1  # 15 days


# ---------------------------------------------------------------------------
# Structured logging — operator can follow each step in JSON.
# ---------------------------------------------------------------------------


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


logger = structlog.get_logger(service="harness.e2e_smoke")


class SmokeError(RuntimeError):
    """Raised on the first failed assertion so the shell wrapper can exit 1."""


# ---------------------------------------------------------------------------
# Stub Execution-cluster collaborators (the smoke test is fixture-driven; we
# do not need real DefiLlama / Ollama / risk-module wiring to prove the
# cycle assembles + publishes orders).
# ---------------------------------------------------------------------------


_CHAIN_FOR_TEMPLATE: Mapping[str, Chain] = {
    "LEND-001": "base",
    "LEND-KAMINO-001": "solana",
}


def _market_for_chain(chain: Chain) -> MarketSnapshot:
    """Build a snapshot that drives both default-param evaluate()s to ``enter``.

    Keys chosen to match each template's evaluate(): LEND-001 reads
    ``aave_v3.usdc.base`` + ``base:aave_v3:usdc``; LEND-KAMINO-001 reads
    ``kamino.usdc.solana`` + ``solana:kamino:usdc``. APYs are well above
    each template's default ``apy_threshold`` (5% / 3%) and TVL clears the
    default 1M floor with room to spare.
    """
    base_apys = {
        "aave_v3.usdc.base": Decimal("0.08"),
        "aave_v3.usdbc.base": Decimal("0.06"),
    }
    sol_apys = {
        "kamino.usdc.solana": Decimal("0.05"),
        "kamino.usdt.solana": Decimal("0.04"),
        "kamino.sol.solana": Decimal("0.03"),
    }
    pool_base = {
        "base:aave_v3:usdc": PoolState(
            pool_id="base:aave_v3:usdc",
            tvl=Decimal("50000000"),
            depth=Decimal("1000000"),
            fees_24h=Decimal("0"),
        ),
        "base:aave_v3:usdbc": PoolState(
            pool_id="base:aave_v3:usdbc",
            tvl=Decimal("20000000"),
            depth=Decimal("500000"),
            fees_24h=Decimal("0"),
        ),
    }
    pool_sol = {
        "solana:kamino:usdc": PoolState(
            pool_id="solana:kamino:usdc",
            tvl=Decimal("40000000"),
            depth=Decimal("800000"),
            fees_24h=Decimal("0"),
        ),
        "solana:kamino:usdt": PoolState(
            pool_id="solana:kamino:usdt",
            tvl=Decimal("15000000"),
            depth=Decimal("400000"),
            fees_24h=Decimal("0"),
        ),
        "solana:kamino:sol": PoolState(
            pool_id="solana:kamino:sol",
            tvl=Decimal("8000000"),
            depth=Decimal("200000"),
            fees_24h=Decimal("0"),
        ),
    }
    if chain == "base":
        return MarketSnapshot(
            timestamp=datetime.now(UTC),
            chain="base",
            prices={"USDC": Decimal("1"), "ETH": Decimal("3500"), "SOL": Decimal("150")},
            apys=base_apys,
            pool_state=pool_base,
            gas_gwei=Decimal("0.05"),
            metadata={},
        )
    return MarketSnapshot(
        timestamp=datetime.now(UTC),
        chain="solana",
        prices={"USDC": Decimal("1"), "USDT": Decimal("1"), "SOL": Decimal("150")},
        apys=sol_apys,
        pool_state=pool_sol,
        gas_gwei=Decimal("0"),
        metadata={"priority_fees": {"p50_microlamports": "50000"}},
    )


@dataclass
class StubAdapter:
    name: str = "smoke-stub-adapter"
    historical_supported: bool = False

    async def fetch_live(self, chain: Chain) -> MarketSnapshot:
        return _market_for_chain(chain)

    def fetch_historical(self, chain, start, end):  # pragma: no cover
        raise NotImplementedError("smoke adapter is live-only")


class StubRegimeClassifier:
    name = "smoke-stub-regime"

    def classify(self, market: MarketSnapshot) -> Regime:
        return Regime(
            volatility="low",
            funding="neutral",
            trend="trending_up",
            tvl="stable",
            confidence=Decimal("0.8"),
            features={},
            source=self.name,
            rationale="",
        )


class StubAllocator:
    """Equal-split across actionable candidates, capped at $1000 per leg."""

    name = "smoke-stub-allocator"

    def allocate(
        self,
        candidate_decisions: Mapping[str, Decision],
        portfolio: PortfolioSnapshot,
        regime: Regime,
    ) -> AllocationDecision:
        actionable = {
            cid: d
            for cid, d in candidate_decisions.items()
            if d.action in ("enter", "rebalance", "exit") and d.target_size > 0
        }
        if not actionable:
            return AllocationDecision(
                target_usd_by_candidate={},
                mode="cold_start",
                template_caps_applied={},
                commentary="smoke: no actionable decisions",
            )
        per = Decimal("1000")
        return AllocationDecision(
            target_usd_by_candidate={cid: per for cid in actionable},
            mode="cold_start",
            template_caps_applied={},
            commentary="smoke: equal $1k per actionable candidate",
        )


class StubRiskGate:
    """All-pass gate — risk-module behaviour is tested elsewhere; here we
    only need the gate seam to exist so the cycle's publish path executes.
    """

    def check(self, order: ExecutionOrder, ctx: RiskContext) -> RiskDecision:
        return RiskDecision(passed=True, checker="smoke-stub-pass", reason="")


class SmokeCycle(DecisionCycle):
    """DecisionCycle override that injects a fixed PortfolioSnapshot.

    The base ``_read_portfolio`` returns ``cash_usd=0`` because the W7
    portfolio accountant has not landed yet. The templates' evaluate()
    bodies size their entry off ``portfolio_state.cash_usd``, so without
    cash they return ``hold`` and the publish path never fires.

    We give the cycle a $100k portfolio so both templates produce ``enter``
    decisions; this is the smoke-only equivalent of the future portfolio-
    accountant column read.
    """

    cash_usd: Decimal = Decimal("100000")

    def _read_portfolio(self) -> PortfolioSnapshot:  # type: ignore[override]
        return PortfolioSnapshot(
            nav_usd=self.cash_usd,
            positions={},
            cash_usd=self.cash_usd,
            drawdown_from_peak=Decimal("0"),
            last_rebalance=datetime.now(UTC),
        )


# ---------------------------------------------------------------------------
# Phase 1: synthesise Research outputs (parameter search + walk-forward).
# ---------------------------------------------------------------------------


def _seed_research_rows(db: DatabaseManager, template_id: str) -> list[Candidate]:
    """Insert 3 top-K ParameterSearchResult rows + 6 WalkForwardResult rows
    per candidate, plus the matching Candidate rows in ``backtest`` state.

    Returns the inserted Candidate rows in insertion order so the caller can
    promote them.
    """
    candidates: list[Candidate] = []
    now = datetime.now(UTC)
    with db.get_session() as session:
        for i in range(TOP_K):
            params = {
                "apy_threshold": f"0.0{4 + i}",
                "min_liquidity_usd": "1000000",
                "asset_variant": "USDC",
            }
            params_json = json.dumps(params, sort_keys=True, separators=(",", ":"))
            psr = ParameterSearchResult(
                template_id=template_id,
                template_version="0.1.0",
                params_json=params_json,
                sharpe=Decimal("1.0"),
                deflated_sharpe=Decimal("0.85"),
                max_dd=Decimal("0.03"),
                turnover=Decimal("0.10"),
                oos_sharpe=Decimal("0.95"),
                compute_seconds=Decimal("1.000"),
                is_top_k=True,
                created_at=now,
            )
            session.add(psr)

            candidate_id = f"smoke-{template_id.lower()}-{i}-{uuid.uuid4().hex[:6]}"
            candidate = Candidate(
                candidate_id=candidate_id,
                template_id=template_id,
                template_version="0.1.0",
                params_json=params_json,
                state="backtest",
                entered_state_at=now,
                created_at=now,
            )
            session.add(candidate)
            candidates.append(candidate)

            # Six walk-forward windows = the W3 default (60, 15, 3) cycle count.
            for w in range(WALK_FORWARD_WINDOWS):
                train_start = now - timedelta(days=60 + 15 * w)
                train_end = train_start + timedelta(days=60)
                test_start = train_end
                test_end = test_start + timedelta(days=15)
                session.add(
                    WalkForwardResult(
                        candidate_id=candidate_id,
                        train_start=train_start,
                        train_end=train_end,
                        test_start=test_start,
                        test_end=test_end,
                        train_sharpe=Decimal("1.10"),
                        test_sharpe=Decimal("0.95"),
                        test_max_dd=Decimal("0.03"),
                        regime_label="stable",
                        created_at=now,
                    )
                )
        session.commit()
        # Detach the candidate objects from the session so the caller can use
        # their candidate_id without a SQLAlchemy DetachedInstanceError.
        for c in candidates:
            session.refresh(c)
            session.expunge(c)
    logger.info(
        "research_rows_seeded",
        template_id=template_id,
        top_k=TOP_K,
        walk_forward_per_candidate=WALK_FORWARD_WINDOWS,
    )
    return candidates


# ---------------------------------------------------------------------------
# Phase 2: enter paper_trade via the real state machine.
# ---------------------------------------------------------------------------


async def _enter_paper_trade_all(
    db: DatabaseManager, candidates: list[Candidate], template_id: str
) -> None:
    for c in candidates:
        sm = CandidateStateMachine(db, c.candidate_id)
        result = await sm.enter_paper_trade(
            allocation_max_pct=Decimal("0.70"),
            template_id=template_id,
            reason="smoke-top-k-promotion",
        )
        logger.info(
            "candidate_entered_paper_trade",
            candidate_id=c.candidate_id,
            template_id=template_id,
            previous_state=result.previous_state,
            new_state=result.new_state,
        )


# ---------------------------------------------------------------------------
# Phase 3: simulate 15 days of paper-trade observations.
# ---------------------------------------------------------------------------


def _seed_paper_trade_state(db: DatabaseManager, candidates: list[Candidate]) -> None:
    """Write a PaperTradeState row per candidate matching the W5 criteria.

    observed_sharpe = 0.85 (>= 0.80 * backtest_oos_sharpe=0.95 → floor 0.76),
    observed_max_dd = 0.03 (<= 1.20 * backtest_max_dd=0.03 → ceiling 0.036).
    """
    now = datetime.now(UTC)
    entered_at = now - timedelta(days=PAPER_TRADE_OBSERVATION_DAYS)
    with db.get_session() as session:
        for c in candidates:
            row = PaperTradeState(
                candidate_id=c.candidate_id,
                entered_paper_at=entered_at,
                shadow_positions_json="[]",
                observed_sharpe=Decimal("0.85"),
                observed_max_dd=Decimal("0.03"),
                observation_days=PAPER_TRADE_OBSERVATION_DAYS,
                last_updated_at=now,
            )
            session.add(row)
        session.commit()
    logger.info(
        "paper_trade_state_seeded",
        candidate_count=len(candidates),
        observation_days=PAPER_TRADE_OBSERVATION_DAYS,
    )


# ---------------------------------------------------------------------------
# Phase 4: scan promotion-gate eligibility.
# ---------------------------------------------------------------------------


class _NoOpWebhookPoster:
    """Satisfies the PromotionGate Stream-A poster Protocol without I/O.

    The smoke test does not exercise the Discord round-trip itself —
    Stream A's webhook + reply-token round-trip is already covered by
    its own unit tests. Here we only need a poster that returns a
    plausible ReplyToken so the gate can run end-to-end.
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
    ) -> ReplyToken:
        return ReplyToken(
            token_id=f"smoke-{uuid.uuid4().hex[:8]}",
            candidate_id=candidate_id,
            template_id=template_id,
        )


@dataclass
class _NoOpReplyTokenStore:
    """No-op store — never matches, never expires. The smoke test exercises
    the eligibility scan + promotion-by-state-machine path directly, not
    the Discord reply leg (covered by Stream A's own tests).
    """

    async def match_reply(self, *, message: str) -> ReplyTokenMatch | None:
        return None

    async def expire_stale(self, *, ttl_hours: int = 24) -> int:
        return 0


async def _scan_eligible(db: DatabaseManager) -> list[CandidateEligibility]:
    gate = PromotionGate(
        db=db,
        webhook_poster=_NoOpWebhookPoster(),
        reply_token_store=_NoOpReplyTokenStore(),
    )
    eligibilities = await gate.scan_eligible()
    by_template: dict[str, int] = {}
    for e in eligibilities:
        by_template[e.template_id] = by_template.get(e.template_id, 0) + 1
    logger.info(
        "promotion_scan_complete_smoke",
        total_eligible=len(eligibilities),
        by_template=by_template,
    )
    return eligibilities


# ---------------------------------------------------------------------------
# Phase 5: promote at least one candidate per template to live_capped, then
# run one decision-engine cycle and assert orders publish per chain.
# ---------------------------------------------------------------------------


async def _promote_one_per_template(
    db: DatabaseManager, eligibilities: list[CandidateEligibility]
) -> dict[str, str]:
    """Promote the first eligible candidate per template to ``live_capped``.

    Returns ``{template_id: candidate_id}`` for the promoted set.
    """
    promoted: dict[str, str] = {}
    for e in eligibilities:
        if e.template_id in promoted:
            continue
        sm = CandidateStateMachine(db, e.candidate_id)
        result = await sm.promote_to_live_capped(reason="smoke-direct-promotion")
        promoted[e.template_id] = e.candidate_id
        logger.info(
            "candidate_promoted_to_live_capped",
            candidate_id=e.candidate_id,
            template_id=e.template_id,
            previous_state=result.previous_state,
            new_state=result.new_state,
        )
    return promoted


def _build_roster_cache(db: DatabaseManager) -> RosterCache:
    """Materialise a RosterCache directly from the lake_roster table.

    We bypass RosterListener.bootstrap() because that path uses Postgres
    LISTEN/NOTIFY for live updates — the smoke test runs against SQLite
    where notify is a no-op (W6 design). A one-shot SELECT into the
    cache gives the cycle the same shape the live-Postgres listener
    would, which is all the cycle needs for a single tick.
    """
    from sqlalchemy import select

    cache = RosterCache()
    with db.get_session() as session:
        rows = session.execute(select(LakeRoster)).scalars().all()
        entries = [
            RosterEntry(
                candidate_id=r.candidate_id,
                template_id=r.template_id,
                state=r.state,  # type: ignore[arg-type]
                allocation_usd=Decimal(str(r.allocation_usd)),
                allocation_max_pct=Decimal(str(r.allocation_max_pct)),
                breaker_tripped=bool(r.breaker_tripped),
            )
            for r in rows
        ]
    cache.replace(entries)
    logger.info(
        "roster_cache_loaded",
        total=len(entries),
        live=len(cache.live()),
    )
    return cache


@dataclass
class _PublishCapture:
    """Subscribe to fakeredis pub/sub channels and capture published orders.

    The cycle uses ``redis.publish`` (channel pub/sub) per
    ``RedisExecutorPublisher.publish_order``. We subscribe before the
    cycle runs and drain messages after; this exercises the real
    publisher path with a working broker (fakeredis) so the assertion
    "orders publish to execution:orders:{chain}" is honest.
    """

    redis: Any
    pubsub: Any
    received: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    @classmethod
    async def attach(cls, redis_client: Any, channels: list[str]) -> _PublishCapture:
        pubsub = redis_client.pubsub()
        await pubsub.subscribe(*channels)
        # Drain the initial "subscribe" acknowledgement messages so the
        # capture loop only sees real publishes.
        for _ in channels:
            await pubsub.get_message(timeout=1.0)
        return cls(redis=redis_client, pubsub=pubsub)

    async def drain(self, *, timeout_seconds: float = 1.0) -> None:
        while True:
            msg = await self.pubsub.get_message(timeout=timeout_seconds)
            if msg is None:
                break
            if msg.get("type") != "message":
                continue
            channel = msg["channel"]
            if isinstance(channel, bytes):
                channel = channel.decode()
            data = msg["data"]
            if isinstance(data, bytes):
                data = data.decode()
            payload = json.loads(data)
            self.received.setdefault(channel, []).append(payload)

    async def close(self) -> None:
        await self.pubsub.unsubscribe()
        await self.pubsub.aclose()


async def _run_decision_cycle(
    db: DatabaseManager, registry: TemplateRegistry
) -> dict[str, list[dict[str, Any]]]:
    """Run one SmokeCycle tick and return the per-channel captured orders."""
    redis_client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    publisher = RedisExecutorPublisher(redis_client)
    cache = _build_roster_cache(db)

    cycle = SmokeCycle(
        adapter=StubAdapter(),
        registry=registry,
        allocator=StubAllocator(),
        regime_classifier=StubRegimeClassifier(),
        risk_gate=StubRiskGate(),
        executor_publisher=publisher,
        db=db,
        roster_cache=cache,
    )

    channels = ["execution:orders:base", "execution:orders:solana"]
    capture = await _PublishCapture.attach(redis_client, channels)
    try:
        result = await cycle.run_one()
        await capture.drain(timeout_seconds=0.5)
    finally:
        await capture.close()
        await redis_client.aclose()

    logger.info(
        "decision_cycle_complete_smoke",
        evaluated=result.candidates_evaluated,
        emitted=result.orders_emitted,
        dropped=result.orders_dropped,
        chains=list(result.chains_processed),
        allocator_mode=result.allocator_mode,
    )
    return capture.received


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


async def _run(db_url: str) -> int:
    _configure_logging()
    logger.info("smoke_start", db_url=db_url, template_root=str(TEMPLATE_ROOT))

    db = DatabaseManager(DatabaseConfig(url=db_url))
    db.create_tables()

    registry = TemplateRegistry(TEMPLATE_ROOT)
    load_result = registry.load()
    missing = [tid for tid in TEMPLATE_IDS if tid not in registry]
    if missing or load_result.failed:
        raise SmokeError(
            f"template registry did not load all required ids "
            f"(missing={missing}, failed={[f.message for f in load_result.failed]})"
        )
    logger.info("template_registry_loaded", ids=list(registry.ids()))

    candidates_by_template: dict[str, list[Candidate]] = {}
    for template_id in TEMPLATE_IDS:
        cands = _seed_research_rows(db, template_id)
        await _enter_paper_trade_all(db, cands, template_id)
        _seed_paper_trade_state(db, cands)
        candidates_by_template[template_id] = cands

    eligibilities = await _scan_eligible(db)
    by_template: dict[str, int] = {}
    for e in eligibilities:
        by_template[e.template_id] = by_template.get(e.template_id, 0) + 1
    for template_id in TEMPLATE_IDS:
        if by_template.get(template_id, 0) < 1:
            raise SmokeError(
                f"promotion gate found 0 eligible candidates for {template_id} "
                f"(expected >=1); by_template={by_template}"
            )
    if len(eligibilities) < 4:
        raise SmokeError(
            f"expected >=4 eligible candidates across both templates, "
            f"got {len(eligibilities)}"
        )
    logger.info(
        "promotion_gate_assert_passed",
        total_eligible=len(eligibilities),
        per_template=by_template,
    )

    promoted = await _promote_one_per_template(db, eligibilities)
    if set(promoted.keys()) != set(TEMPLATE_IDS):
        raise SmokeError(
            f"failed to promote one candidate per template; promoted={promoted}"
        )

    received = await _run_decision_cycle(db, registry)
    for chain in ("base", "solana"):
        channel = f"execution:orders:{chain}"
        orders = received.get(channel, [])
        if not orders:
            raise SmokeError(
                f"no orders captured on {channel!r}; received channels={list(received)}"
            )
        # Sanity-check the envelope shape — the consumer side schema is
        # validated in shared/schemas/, but we want a structural smoke here.
        first = orders[0]
        if first.get("chain") != chain:
            raise SmokeError(
                f"order on {channel!r} has chain={first.get('chain')!r}, expected {chain!r}"
            )
        if not first.get("order_id"):
            raise SmokeError(f"order on {channel!r} is missing order_id")
        logger.info(
            "order_published_assert_passed",
            channel=channel,
            count=len(orders),
            first_order_id=first.get("order_id"),
            first_template_id=first.get("template_id"),
        )

    db.close()
    logger.info("smoke_pass", templates=list(TEMPLATE_IDS))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db-url",
        default="sqlite:///:memory:",
        help="SQLAlchemy URL for the smoke DB (default: in-memory SQLite).",
    )
    args = parser.parse_args()
    try:
        return asyncio.run(_run(args.db_url))
    except SmokeError as exc:
        # Make sure structlog/loggers are configured even if _run fails early.
        try:
            logger.error("smoke_fail", error=str(exc))
        except Exception:
            print(f"smoke_fail: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        try:
            logger.exception("smoke_unexpected_error", error=str(exc))
        except Exception:
            print(f"smoke_unexpected_error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
