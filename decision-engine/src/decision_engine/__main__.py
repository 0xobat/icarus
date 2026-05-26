"""decision-engine entrypoint — Execution cluster runtime cycle.

Cycle shape (one tick — implemented in `decision_engine.cycle.DecisionCycle`):

  1. Read lake roster from in-memory cache (kept warm by Postgres
     LISTEN/NOTIFY → `RosterListener`).
  2. Build MarketSnapshot via `DataAdapter` for each active chain.
  3. Build PortfolioSnapshot from Postgres.
  4. Classify regime (rules-based primary, fast).
  5. For each live candidate: load evaluate.py from the TemplateRegistry,
     call `evaluate(params, market, portfolio) → Decision`.
  6. Allocator: candidate decisions + portfolio + regime → AllocationDecision.
  7. Pre-trade risk gate: drop / shrink orders that violate exposure /
     circuit-breaker state.
  8. Emit orders to `execution:orders:<chain>` Redis channels.
  9. Sleep until next cycle.

Per blueprint Q2: the cycle NEVER blocks on the LLM advisor. The
rules-based regime classifier is primary; the allocator's `commentary`
field carries advisor output when it arrives in time, otherwise empty
or an `ADVISOR_ERROR_PREFIX`-prefixed diagnosis string.

Concrete impl wiring at startup:
  * `DataAdapter`        — Streams W3 (DefiLlama + chain RPC)
  * `RegimeClassifier`   — Stream A (rules-based, Stream D advisor parallel)
  * `Allocator`          — Stream B (cold-start / risk-parity)
  * `TemplateRegistry`   — already in lib.dsl.registry
  * Risk modules         — already shipped in decision_engine/risk/

This file wires the real worker loop with structlog + signal handling,
matching the W2 extractor-worker pattern.
"""

from __future__ import annotations

import asyncio
import os
import signal
import sys
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import redis.asyncio as redis
import structlog
from icarus.allocator import ComposedAllocator
from icarus.allocator.types import RosterEntry as AllocatorRosterEntry
from icarus.data_adapters import build_default_adapter
from icarus.db.database import DatabaseConfig, DatabaseManager
from icarus.dsl.registry import TemplateRegistry
from icarus.regime import RulesRegimeClassifier

from decision_engine.cycle import DecisionCycle, RedisExecutorPublisher
from decision_engine.risk.drawdown_breaker import DrawdownBreaker
from decision_engine.risk.exposure_limits import ExposureLimiter
from decision_engine.risk.gas_spike_breaker import GasSpikeBreaker
from decision_engine.risk.position_loss_limit import PositionLossLimit
from decision_engine.risk.tx_failure_monitor import TxFailureMonitor
from decision_engine.risk_gate import (
    DrawdownChecker,
    ExposureChecker,
    GasSpikeChecker,
    PositionLossChecker,
    RiskGate,
    TxFailureChecker,
)
from decision_engine.roster_listener import RosterListener

logger = structlog.get_logger(service="decision-engine")

CYCLE_INTERVAL_SECONDS = int(os.environ.get("DECISION_CYCLE_INTERVAL_SECONDS", "30"))


class DecisionEngine:
    """Worker process — owns I/O lifecycle, delegates per-tick work to DecisionCycle.

    The cycle itself is stateless across ticks. This process owns:
      * the Redis client (publish target)
      * the database session factory
      * the RosterListener task (background)
      * the SIGTERM/SIGINT stop signal
    """

    def __init__(
        self,
        *,
        cycle: DecisionCycle,
        roster_listener: RosterListener,
    ) -> None:
        self._cycle = cycle
        self._roster_listener = roster_listener
        self._stop = asyncio.Event()

    async def run(self) -> None:
        logger.info("decision_engine_start", interval_s=CYCLE_INTERVAL_SECONDS)
        await self._roster_listener.bootstrap()
        self._roster_listener.start()
        try:
            while not self._stop.is_set():
                try:
                    await self._cycle.run_one()
                except NotImplementedError as exc:
                    # Stream A/B/D stub not yet wired — log loudly and keep
                    # ticking so the operator sees the gap without losing
                    # the worker process.
                    logger.warning("cycle_stub_missing", reason=str(exc))
                except Exception:
                    logger.exception("cycle_failed")
                try:
                    await asyncio.wait_for(
                        self._stop.wait(), timeout=CYCLE_INTERVAL_SECONDS
                    )
                except TimeoutError:
                    continue
        finally:
            await self._roster_listener.stop()
            logger.info("decision_engine_stop")

    def request_stop(self) -> None:
        self._stop.set()


def _build_risk_gate(
    *,
    drawdown: DrawdownBreaker,
    exposure: ExposureLimiter,
    gas_spike: GasSpikeBreaker,
    position_loss: PositionLossLimit,
    tx_failure: TxFailureMonitor,
) -> RiskGate:
    """Compose checkers in cheap → expensive order.

    Reads-only state checks (drawdown, tx failure, gas spike) run before
    the exposure-limit check (which inspects current positions). Position
    loss is last because it touches per-strategy cooldown state.
    """
    return RiskGate(
        [
            DrawdownChecker(drawdown),
            TxFailureChecker(tx_failure),
            GasSpikeChecker(gas_spike),
            ExposureChecker(exposure),
            PositionLossChecker(position_loss),
        ]
    )


async def _amain() -> int:
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.JSONRenderer(),
        ]
    )
    logger.info("decision_engine_init", started_at=datetime.now(UTC).isoformat())

    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    redis_client = redis.from_url(redis_url, decode_responses=True)

    db_config = DatabaseConfig()
    db = DatabaseManager(db_config)
    db.create_tables()

    template_root = Path(os.environ.get("TEMPLATE_ROOT", "templates"))
    registry = TemplateRegistry(template_root)
    registry.load()

    # Risk modules — instantiated with defaults; production config wires
    # in env-driven thresholds via each module's `*Config` dataclass.
    drawdown = DrawdownBreaker()
    exposure = ExposureLimiter()
    gas_spike = GasSpikeBreaker()
    position_loss = PositionLossLimit()
    tx_failure = TxFailureMonitor()

    risk_gate = _build_risk_gate(
        drawdown=drawdown,
        exposure=exposure,
        gas_spike=gas_spike,
        position_loss=position_loss,
        tx_failure=tx_failure,
    )

    # Real impl wiring (W6 review fix — superseded the stubs below).
    # DataAdapter: W3/W4 stream A's factory picks the v1 default
    # (DefiLlamaAdapter — public API, no key required).
    adapter = build_default_adapter()
    # RegimeClassifier: W6 stream A's rules-based primary classifier.
    regime_classifier = RulesRegimeClassifier()

    listener = RosterListener(db=db, connection_url=db_config.url)

    # Allocator: W6 stream B's ComposedAllocator. Two integration
    # adapters needed:
    #   1. Bridge the two `RosterEntry` shapes — the cache's broad row
    #      vs the allocator's narrow input. Trivial field projection.
    #   2. The cache changes on every NOTIFY event, but ComposedAllocator
    #      binds roster at construction. Solve with a thin wrapper that
    #      rebuilds the allocator on every `.allocate()` call from the
    #      live cache snapshot.
    #
    # `returns_lookup` returns empty — forces the composed-allocator
    # cold-start path (equal-weight) until per-candidate return-history
    # tracking lands (W8+). Cold-start is the blueprint default for new
    # candidates anyway.
    def _empty_returns_lookup(_candidate_id: str) -> np.ndarray:
        return np.array([], dtype=np.float64)

    class _LiveAllocator:
        """Per-cycle allocator factory that consults the live roster cache.

        ``ComposedAllocator`` is constructed fresh on each `allocate()` call
        so newly-NOTIFY-promoted candidates land in the allocation set
        without an engine restart.
        """

        name = "live-composed"

        def allocate(self, candidate_decisions, portfolio, regime):
            allocator_roster = {
                cid: AllocatorRosterEntry(
                    template_id=entry.template_id,
                    allocation_max_pct=entry.allocation_max_pct,
                )
                for cid, entry in {
                    e.candidate_id: e for e in listener.cache.live()
                }.items()
            }
            inner = ComposedAllocator(
                roster=allocator_roster,
                returns_lookup=_empty_returns_lookup,
            )
            return inner.allocate(candidate_decisions, portfolio, regime)

    allocator = _LiveAllocator()

    cycle = DecisionCycle(
        adapter=adapter,
        registry=registry,
        allocator=allocator,
        regime_classifier=regime_classifier,
        risk_gate=risk_gate,
        executor_publisher=RedisExecutorPublisher(redis_client),
        db=db,
        roster_cache=listener.cache,
    )

    engine = DecisionEngine(cycle=cycle, roster_listener=listener)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, engine.request_stop)

    try:
        await engine.run()
    finally:
        await redis_client.aclose()
        db.close()
    return 0


# ---------------------------------------------------------------------------
# Stream A/B/D late-binding stubs. Each raises NotImplementedError with
# a useful message so the cycle's late-binding catch can log + skip.
# Replace at instantiation time when the real impl ships.
# ---------------------------------------------------------------------------


class _StubDataAdapter:
    name = "stub-data-adapter"
    historical_supported = False

    async def fetch_live(self, chain):  # type: ignore[no-untyped-def]
        raise NotImplementedError(
            f"no DataAdapter wired (W3); chain={chain}. Replace _StubDataAdapter in __main__."
        )

    def fetch_historical(self, chain, start, end):  # type: ignore[no-untyped-def]
        raise NotImplementedError("stub adapter has no historical")


class _StubRegimeClassifier:
    name = "stub-regime-classifier"

    def classify(self, market):  # type: ignore[no-untyped-def]
        raise NotImplementedError(
            "no RegimeClassifier wired (Stream A). Replace _StubRegimeClassifier in __main__."
        )


class _StubAllocator:
    name = "stub-allocator"

    def allocate(self, candidate_decisions, portfolio, regime):  # type: ignore[no-untyped-def]
        raise NotImplementedError(
            "no Allocator wired (Stream B). Replace _StubAllocator in __main__."
        )


def main() -> int:
    return asyncio.run(_amain())


if __name__ == "__main__":
    sys.exit(main())
