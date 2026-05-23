"""extractor-worker entrypoint — Research cluster paper/blog → template.

Worker shape:
  - BLMOVE-claim a job from `research:papers:pending` (visibility timeout 30min).
  - Source-type dispatch: paper_pdf → pypdf, blog_url → httpx + readability,
    dune_query → Dune adapter.
  - Call Extractor.extract(source_type, source_ref) — concrete impl: frontier
    LLM (Anthropic Claude Opus 4.7 primary, OpenAI fallback).
  - Run LLM-as-judge plausibility check on the four output files.
  - Write template files to `templates/<id>/` (Postgres row written first; files
    second; rollback on file write failure).
  - ACK the job; on failure, the visibility timeout returns it to the queue.

Multi-replica safe from day 1 (Q10): atomic BLMOVE claim with visibility
timeout, idempotent template writes by template_id.
"""

from __future__ import annotations

import asyncio
import signal
import sys

import structlog

logger = structlog.get_logger(service="extractor-worker")

QUEUE = "research:papers:pending"
QUEUE_INFLIGHT = "research:papers:inflight"
VISIBILITY_TIMEOUT_SECONDS = 30 * 60


class ExtractorWorker:
    def __init__(self) -> None:
        self._stop = asyncio.Event()
        # TODO(week-2): wire Redis client (BLMOVE), Extractor impl, Postgres writer.

    async def run(self) -> None:
        logger.info("extractor_worker_start", queue=QUEUE)
        while not self._stop.is_set():
            try:
                await self._consume_one()
            except Exception:
                logger.exception("job_failed")
                await asyncio.sleep(1)
        logger.info("extractor_worker_stop")

    async def _consume_one(self) -> None:
        raise NotImplementedError("extractor worker loop: scheduled for week 2")

    def request_stop(self) -> None:
        self._stop.set()


def main() -> int:
    structlog.configure(processors=[structlog.processors.JSONRenderer()])
    worker = ExtractorWorker()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, worker.request_stop)
    try:
        loop.run_until_complete(worker.run())
    except NotImplementedError as e:
        logger.warning("extractor_worker_skeleton_only", reason=str(e))
    return 0


if __name__ == "__main__":
    sys.exit(main())
