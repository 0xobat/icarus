"""decision-engine entrypoint — Execution cluster runtime cycle.

Cycle shape (one tick):
  1. Read lake roster (Curation-owned; LISTEN/NOTIFY for changes).
  2. Build MarketSnapshot via DataAdapter for each active chain.
  3. Build PortfolioSnapshot from Postgres.
  4. Classify regime (rules-based primary, fast).
  5. Fire LLM advisor regime call (async, 5s timeout — cycle does not block).
  6. For each live candidate: load evaluate.py, call evaluate(...) → Decision.
  7. Allocator: candidate decisions + portfolio + regime → AllocationDecision.
  8. Pre-trade risk gate: drop / shrink orders that violate exposure/circuit-breaker state.
  9. Emit orders to execution:orders:<chain> Redis channels.
 10. Sleep until next cycle.

Per blueprint Q2: the cycle NEVER blocks on the LLM advisor. Rules-based
regime is the primary signal; advisor output joins the next cycle's record
if it arrives late.
"""

from __future__ import annotations

import asyncio
import signal
import sys

import structlog

logger = structlog.get_logger(service="decision-engine")

CYCLE_INTERVAL_SECONDS = 30  # blueprint does not lock; tune per template cadence


class DecisionEngine:
    """Wires the Execution-cluster runtime cycle.

    All collaborators are injected at construction time so tests can pass
    fakes that satisfy the lib.protocols interfaces. See
    `from icarus.protocols import Allocator, RegimeClassifier, DataAdapter`.
    """

    def __init__(self) -> None:
        self._stop = asyncio.Event()
        # TODO(week-6): wire concrete Allocator, RegimeClassifier, DataAdapter,
        # risk gate, postgres + redis clients here. Protocol surfaces are
        # already final in icarus.protocols.

    async def run(self) -> None:
        logger.info("decision_engine_start", interval_s=CYCLE_INTERVAL_SECONDS)
        while not self._stop.is_set():
            try:
                await self._tick()
            except Exception:
                logger.exception("cycle_failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=CYCLE_INTERVAL_SECONDS)
            except TimeoutError:
                continue
        logger.info("decision_engine_stop")

    async def _tick(self) -> None:
        # Week-6 will wire the steps documented in the module docstring.
        raise NotImplementedError("decision-engine cycle: scheduled for week 6")

    def request_stop(self) -> None:
        self._stop.set()


def _install_signal_handlers(engine: DecisionEngine) -> None:
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, engine.request_stop)


def main() -> int:
    structlog.configure(processors=[structlog.processors.JSONRenderer()])
    engine = DecisionEngine()
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        _install_signal_handlers(engine)
        loop.run_until_complete(engine.run())
    except NotImplementedError as e:
        logger.warning("decision_engine_skeleton_only", reason=str(e))
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
