"""lake-governor entrypoint — Curation cluster paper-trade + lake state machine.

Tick shape (one cycle, ~minute-level cadence):
  1. LISTEN/NOTIFY on `executions_changed` (Execution cluster activity).
  2. Update paper-trade shadow positions for each live candidate.
  3. Step DecayDetector for each `live_capped`/`live_mature` candidate.
  4. Step per-template breaker (aggregate Sharpe vs. expected over 14d).
  5. Apply state transitions: backtest → paper_trade → live_capped → live_mature
     (with demotion paths to `demoted_paper` and `archived`).
  6. On `live_capped → live_mature` eligibility: emit PROMOTION REQUEST to
     Discord webhook + record reply-token expectation.
  7. NOTIFY `lake_roster_changed` so decision-engine refreshes.

Cross-cluster contract: this service NEVER calls decision-engine directly.
Reads `executions` + `positions` (Execution-owned) via Postgres, writes
`lake_roster` + `paper_trade_state` (Curation-owned).
"""

from __future__ import annotations

import asyncio
import signal
import sys

import structlog

logger = structlog.get_logger(service="lake-governor")

TICK_INTERVAL_SECONDS = 60


class LakeGovernor:
    def __init__(self) -> None:
        self._stop = asyncio.Event()
        # TODO(week-5): wire DecayDetector, paper-trade harness, state machine.

    async def run(self) -> None:
        logger.info("lake_governor_start", interval_s=TICK_INTERVAL_SECONDS)
        while not self._stop.is_set():
            try:
                await self._tick()
            except Exception:
                logger.exception("tick_failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=TICK_INTERVAL_SECONDS)
            except TimeoutError:
                continue
        logger.info("lake_governor_stop")

    async def _tick(self) -> None:
        raise NotImplementedError("lake-governor state machine: scheduled for week 5")

    def request_stop(self) -> None:
        self._stop.set()


def main() -> int:
    structlog.configure(processors=[structlog.processors.JSONRenderer()])
    governor = LakeGovernor()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, governor.request_stop)
    try:
        loop.run_until_complete(governor.run())
    except NotImplementedError as e:
        logger.warning("lake_governor_skeleton_only", reason=str(e))
    return 0


if __name__ == "__main__":
    sys.exit(main())
