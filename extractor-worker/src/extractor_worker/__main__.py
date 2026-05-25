"""extractor-worker entrypoint — Research cluster paper/blog → template.

Worker loop:
  - BLMOVE-claim a job from `research:papers:pending` → `:inflight`.
  - Load source text (PDF / blog / dune-stub).
  - Run agentic extraction pipeline (≤3 attempts with repair on failure).
  - Write 4 files + Postgres `templates` row atomically.
  - ACK the job (LREM from `:inflight`).
  - On failure: record an `Alert` row (category=extractor_failure) and
    let the visibility timeout return the job to `:pending` for retry.

Multi-replica safety (Q10):
  - Atomic claim via BLMOVE.
  - Visibility timeout: if a worker dies mid-job, an out-of-band
    reaper (W3) returns the job to the pending queue. v1 logs orphans
    rather than auto-reaping; an explicit reaper script is a follow-up.
  - Idempotent writes: re-running a paper job for an existing
    template_id raises TemplateWriteError (the existing template wins).

Cost discipline (W2 decision #2c — no fallback API):
  - On FrontierError or NotImplementedError, the worker does NOT
    silently retry — it surfaces to Alert and ACKs the job to avoid
    rerunning a doomed extraction. Operator decides to requeue manually.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
import uuid
from datetime import UTC, datetime

import redis.asyncio as redis
import structlog
from icarus.db.database import DatabaseConfig, DatabaseManager
from icarus.envelopes.research import PaperJob
from pydantic import ValidationError

from extractor_worker.frontier import AnthropicClient, FrontierError
from extractor_worker.loaders import SourceLoadError, load_source
from extractor_worker.pipeline import ExtractionFailedError, extract_with_repair
from extractor_worker.plausibility import (
    RubricEmptyError,
    judge_template,
    update_verdict,
)
from extractor_worker.writer import (
    TemplateWriteError,
    cleanup_orphan_temp_dirs,
    record_extraction_failure,
    write_template,
)

logger = structlog.get_logger(service="extractor-worker")

QUEUE_PENDING = "research:papers:pending"
QUEUE_INFLIGHT = "research:papers:inflight"
VISIBILITY_TIMEOUT_SECONDS = int(os.environ.get("EXTRACTOR_VISIBILITY_TIMEOUT_SECONDS", "1800"))
BLMOVE_BLOCK_SECONDS = 5  # short block so SIGTERM is observed promptly


def _generate_template_id(job: PaperJob) -> str:
    """Provisional template id when the operator didn't pre-assign one.

    The operator can pre-assign by encoding it in source_ref's URL
    fragment (e.g. `https://...#LEND-002`); else we assign EXTR-<8hex>
    and the operator can rename later. Renaming is a manual DB+FS op
    in v1 (W4 ships a rename CLI).
    """
    # If source_ref has a #FRAGMENT that matches the template-id regex,
    # use that. Otherwise generate.
    if "#" in job.source_ref:
        candidate = job.source_ref.split("#", 1)[1]
        # The pydantic validator on TemplateManifest enforces the regex.
        # We don't repeat the check here; if it's wrong, validation fails
        # downstream and the operator sees a clean error.
        if candidate:
            return candidate
    return f"EXTR-{uuid.uuid4().hex[:8].upper()}"


class ExtractorWorker:
    def __init__(
        self,
        *,
        redis_client: redis.Redis,
        db: DatabaseManager,
        frontier: AnthropicClient,
    ) -> None:
        self._redis = redis_client
        self._db = db
        self._frontier = frontier
        self._stop = asyncio.Event()

    async def run(self) -> None:
        cleanup_orphan_temp_dirs()
        logger.info("extractor_worker_start", queue=QUEUE_PENDING)
        while not self._stop.is_set():
            try:
                await self._consume_one()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("worker_loop_unexpected_error")
                await asyncio.sleep(1)
        logger.info("extractor_worker_stop")

    async def _consume_one(self) -> None:
        # BLMOVE: atomic pop from pending → push onto inflight. Visibility
        # timeout is enforced by an out-of-band reaper (W3 follow-up);
        # for now the inflight queue is the audit trail.
        raw = await self._redis.blmove(
            QUEUE_PENDING,
            QUEUE_INFLIGHT,
            timeout=BLMOVE_BLOCK_SECONDS,
            src="LEFT",
            dest="RIGHT",
        )
        if raw is None:
            return  # block timeout; loop will re-check stop event

        try:
            job = PaperJob.model_validate(json.loads(raw))
        except (ValidationError, json.JSONDecodeError) as e:
            logger.error("invalid_paper_job", raw=raw[:500], error=str(e))
            # ACK — bad envelopes are bugs, not transient failures.
            await self._redis.lrem(QUEUE_INFLIGHT, 1, raw)
            return

        log = logger.bind(
            job_id=job.job_id,
            correlation_id=job.correlation_id,
            source_type=job.source_type,
        )
        log.info("job_claimed")
        template_id = _generate_template_id(job)

        try:
            source_text = await load_source(job.source_type, job.source_ref)
            extracted = await extract_with_repair(
                client=self._frontier,
                template_id=template_id,
                source_type=job.source_type,
                source_ref=job.source_ref,
                source_text=source_text,
                chain_hint=job.chain_hint,
            )
            await write_template(extracted, db=self._db)
            log.info(
                "extraction_complete",
                template_id=template_id,
                attempts=extracted.attempts,
                input_tokens=extracted.total_input_tokens,
                output_tokens=extracted.total_output_tokens,
            )
            # Plausibility judge (Q8 veto-only advisor). Failures here do
            # NOT roll back the template — the row stays with its default
            # judge_verdict='FLAG_FOR_OPERATOR'. Operator sees both the
            # template and the judge-call alert.
            try:
                verdict_result = await judge_template(extracted, client=self._frontier)
                await update_verdict(self._db, template_id, verdict_result)
                log.info(
                    "plausibility_judged",
                    template_id=template_id,
                    verdict=verdict_result.verdict,
                    confidence=verdict_result.confidence,
                    input_tokens=verdict_result.input_tokens,
                    output_tokens=verdict_result.output_tokens,
                )
            except (FrontierError, RubricEmptyError) as je:
                log.warning(
                    "judge_call_failed",
                    template_id=template_id,
                    error_class=type(je).__name__,
                    error=str(je),
                )
                await record_extraction_failure(
                    self._db,
                    paper_job_id=job.job_id,
                    template_id=template_id,
                    source_type=job.source_type,
                    source_ref=job.source_ref,
                    error_class=f"judge:{type(je).__name__}",
                    error_message=str(je),
                )
        except (
            SourceLoadError,
            FrontierError,
            ExtractionFailedError,
            TemplateWriteError,
            NotImplementedError,
        ) as e:
            log.error(
                "extraction_failed",
                template_id=template_id,
                error_class=type(e).__name__,
                error=str(e),
            )
            await record_extraction_failure(
                self._db,
                paper_job_id=job.job_id,
                template_id=template_id,
                source_type=job.source_type,
                source_ref=job.source_ref,
                error_class=type(e).__name__,
                error_message=str(e),
            )
        finally:
            # ACK the job — either succeeded or failed-and-alerted.
            # Doomed jobs requeue manually (operator decision), not silently.
            await self._redis.lrem(QUEUE_INFLIGHT, 1, raw)

    def request_stop(self) -> None:
        self._stop.set()


async def _amain() -> int:
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.JSONRenderer(),
        ]
    )
    logger.info("extractor_worker_init", started_at=datetime.now(UTC).isoformat())

    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    redis_client = redis.from_url(redis_url, decode_responses=True)

    db_config = DatabaseConfig()
    db = DatabaseManager(db_config)
    db.create_tables()

    frontier = AnthropicClient()

    worker = ExtractorWorker(redis_client=redis_client, db=db, frontier=frontier)

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
