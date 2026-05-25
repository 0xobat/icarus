"""backtest-worker entrypoint — Research cluster grid + walk-forward search.

Worker shape:
  - BLMOVE-claim a job from ``research:search:pending`` →
    ``research:search:inflight`` (visibility timeout 4h, enforced by an
    out-of-band reaper — W3 follow-up, same pattern as extractor-worker).
  - Resolve ``SearchConfig`` from the envelope dict via
    ``runner.deserialise_grid_search_config`` (W3 supports ``kind='grid'``
    only; Bayesian is W10).
  - Delegate the actual work to ``runner.run_one_job`` which:
      * runs the grid search across the shared snapshot stream,
      * promotes top-K candidates,
      * runs vectorbt walk-forward on each top-K candidate,
      * persists both result tables in one transaction.
  - ACK the job (LREM from ``:inflight``).

Multi-replica safety (Q10):
  - Atomic claim via BLMOVE.
  - One job per worker iteration; concurrent workers each claim
    independent jobs.
  - Failures surface to the log; the operator decides to requeue.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
from datetime import UTC, datetime
from typing import Any

import redis.asyncio as redis
import structlog
from icarus.db.database import DatabaseConfig, DatabaseManager
from icarus.envelopes.research import SearchJob
from pydantic import ValidationError

from backtest_worker.runner import build_default_registry, run_one_job

logger = structlog.get_logger(service="backtest-worker")

QUEUE_PENDING = "research:search:pending"
QUEUE_INFLIGHT = "research:search:inflight"
BLMOVE_BLOCK_SECONDS = 5
VISIBILITY_TIMEOUT_SECONDS = 4 * 60 * 60
PER_TEMPLATE_BUDGET_SECONDS = 2 * 60 * 60


class BacktestWorker:
    """Single-replica BLMOVE consumer for ``research:search:pending``.

    The worker is intentionally adapter-injected — stream A's
    ``icarus.data_adapters`` lands as a peer concern. The default
    constructor wires nothing for adapters (the worker raises in that
    case); production composition happens in ``_amain``.
    """

    def __init__(
        self,
        *,
        redis_client: redis.Redis,
        db: DatabaseManager,
        registry: Any,
        adapter: Any,
    ) -> None:
        self._redis = redis_client
        self._db = db
        self._registry = registry
        self._adapter = adapter
        self._stop = asyncio.Event()

    async def run(self) -> None:
        logger.info(
            "backtest_worker_start",
            queue=QUEUE_PENDING,
            budget_s=PER_TEMPLATE_BUDGET_SECONDS,
        )
        while not self._stop.is_set():
            try:
                await self._consume_one()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("worker_loop_unexpected_error")
                await asyncio.sleep(1)
        logger.info("backtest_worker_stop")

    async def _consume_one(self) -> None:
        # Atomic claim — same pattern as extractor-worker.
        raw = await self._redis.blmove(
            QUEUE_PENDING,
            QUEUE_INFLIGHT,
            timeout=BLMOVE_BLOCK_SECONDS,
            src="LEFT",
            dest="RIGHT",
        )
        if raw is None:
            return

        try:
            job = SearchJob.model_validate(json.loads(raw))
        except (ValidationError, json.JSONDecodeError) as e:
            logger.error("invalid_search_job", raw=raw[:500], error=str(e))
            await self._redis.lrem(QUEUE_INFLIGHT, 1, raw)
            return

        log = logger.bind(
            job_id=job.job_id,
            correlation_id=job.correlation_id,
            template_id=job.template_id,
        )
        log.info("job_claimed")

        try:
            outcome = await run_one_job(
                job,
                registry=self._registry,
                adapter=self._adapter,
                db=self._db,
            )
            log.info(
                "job_outcome_persisted",
                n_search_rows=outcome.n_search_rows,
                n_top_k=outcome.n_top_k,
                n_walk_forward_rows=outcome.n_walk_forward_rows,
            )
        except Exception as e:
            log.exception("job_failed", error_class=type(e).__name__, error=str(e))
        finally:
            # ACK regardless — doomed jobs requeue manually (operator decision).
            await self._redis.lrem(QUEUE_INFLIGHT, 1, raw)

    def request_stop(self) -> None:
        self._stop.set()


def _resolve_adapter_from_env() -> Any:
    """Late-bind to ``icarus.data_adapters`` (stream A).

    Raises ``NotImplementedError`` if the module is absent so a deploy
    fails loudly rather than silently. Tests inject their own stub.
    """
    try:
        from icarus.data_adapters import build_default_adapter  # type: ignore[import-not-found]
    except ImportError as e:
        raise NotImplementedError(
            "backtest-worker requires icarus.data_adapters from stream A; "
            f"install once that ships ({e})"
        ) from e
    return build_default_adapter()


async def _amain() -> int:
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.JSONRenderer(),
        ]
    )
    logger.info("backtest_worker_init", started_at=datetime.now(UTC).isoformat())

    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    redis_client = redis.from_url(redis_url, decode_responses=True)

    db_config = DatabaseConfig()
    db = DatabaseManager(db_config)
    db.create_tables()

    registry = build_default_registry()
    adapter = _resolve_adapter_from_env()

    worker = BacktestWorker(
        redis_client=redis_client, db=db, registry=registry, adapter=adapter
    )

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, worker.request_stop)

    try:
        await worker.run()
    finally:
        await redis_client.aclose()
        db.close()
    return 0


def main() -> int:
    return asyncio.run(_amain())


if __name__ == "__main__":
    sys.exit(main())
