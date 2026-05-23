"""backtest-worker entrypoint — Research cluster grid + walk-forward search.

Worker shape:
  - BLMOVE-claim a job from `research:search:pending` (visibility timeout 4h).
  - Resolve `SearchConfig` from the envelope (Grid or Bayesian discriminated
    union, see icarus.protocols.backtest).
  - Stream historical snapshots via DataAdapter.fetch_historical (async iter).
  - Run BacktestEngine.search(config) — vectorbt walk-forward + deflated Sharpe.
  - Checkpoint partial progress to Postgres each test window so a kill yields
    a usable ResultSurface.
  - Write final ResultSurface + top-K candidate rows; NOTIFY `templates_changed`.
  - ACK the job.

Multi-replica safe; 2-CPU-hour per-template hard kill enforced by the worker
(not the engine), with checkpointing so partial work survives.
"""

from __future__ import annotations

import asyncio
import signal
import sys

import structlog

logger = structlog.get_logger(service="backtest-worker")

QUEUE = "research:search:pending"
QUEUE_INFLIGHT = "research:search:inflight"
VISIBILITY_TIMEOUT_SECONDS = 4 * 60 * 60
PER_TEMPLATE_BUDGET_SECONDS = 2 * 60 * 60


class BacktestWorker:
    def __init__(self) -> None:
        self._stop = asyncio.Event()
        # TODO(week-3): wire Redis client (BLMOVE), BacktestEngine impl (vectorbt),
        # DataAdapter set, Postgres writer.

    async def run(self) -> None:
        logger.info("backtest_worker_start", queue=QUEUE, budget_s=PER_TEMPLATE_BUDGET_SECONDS)
        while not self._stop.is_set():
            try:
                await self._consume_one()
            except Exception:
                logger.exception("job_failed")
                await asyncio.sleep(1)
        logger.info("backtest_worker_stop")

    async def _consume_one(self) -> None:
        raise NotImplementedError("backtest worker loop: scheduled for week 3")

    def request_stop(self) -> None:
        self._stop.set()


def main() -> int:
    structlog.configure(processors=[structlog.processors.JSONRenderer()])
    worker = BacktestWorker()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, worker.request_stop)
    try:
        loop.run_until_complete(worker.run())
    except NotImplementedError as e:
        logger.warning("backtest_worker_skeleton_only", reason=str(e))
    return 0


if __name__ == "__main__":
    sys.exit(main())
