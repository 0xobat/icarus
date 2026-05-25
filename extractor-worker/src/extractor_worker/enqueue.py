"""Operator helper — drop a paper extraction job onto the queue.

Not a "CLI" in the rich Click/Typer sense (W2 decision #4b deferred a real
CLI), just a thin LPUSH wrapper so the operator can exercise the worker
locally without standing up the eventual scraper service.

Usage:
    python -m extractor_worker.enqueue paper_pdf path/to/paper.pdf
    python -m extractor_worker.enqueue blog_url https://example.com/post
    python -m extractor_worker.enqueue blog_url https://example.com/post#LEND-002
        # The #LEND-002 fragment pre-assigns the template id; otherwise
        # the worker auto-generates EXTR-<hex>.

The bus envelope is `PaperJob` from `icarus.envelopes.research`; this
script is the operator-side producer.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid
from datetime import UTC, datetime

import redis.asyncio as redis
from icarus.envelopes.research import PaperJob, SourceType


async def _enqueue(
    *,
    redis_url: str,
    source_type: SourceType,
    source_ref: str,
    requested_by: str,
    chain_hint: str | None,
) -> str:
    job = PaperJob(
        job_id=uuid.uuid4().hex,
        enqueued_at=datetime.now(UTC),
        requested_by=requested_by,
        source_type=source_type,
        source_ref=source_ref,
        correlation_id=uuid.uuid4().hex,
        chain_hint=chain_hint,  # type: ignore[arg-type]
    )
    client = redis.from_url(redis_url, decode_responses=True)
    try:
        # extractor-worker BLMOVEs from LEFT, so we push to LEFT too — FIFO via
        # LPUSH+BLMOVE(LEFT→RIGHT) means oldest pending job wins. (Both directions
        # work for correctness; FIFO is just kinder to operator intuition.)
        await client.lpush("research:papers:pending", job.model_dump_json())
    finally:
        await client.aclose()
    return job.job_id


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="python -m extractor_worker.enqueue",
        description="Drop a paper extraction job onto research:papers:pending.",
    )
    p.add_argument("source_type", choices=["paper_pdf", "blog_url", "dune_query"])
    p.add_argument(
        "source_ref",
        help="path / URL / dune query id (optionally with #TEMPLATE-ID fragment)",
    )
    p.add_argument(
        "--requested-by",
        default=os.environ.get("USER", "operator"),
        help="audit field; defaults to $USER",
    )
    p.add_argument("--chain-hint", choices=["base", "solana"], default=None)
    p.add_argument(
        "--redis-url",
        default=os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
    )
    args = p.parse_args(argv)

    job_id = asyncio.run(
        _enqueue(
            redis_url=args.redis_url,
            source_type=args.source_type,
            source_ref=args.source_ref,
            requested_by=args.requested_by,
            chain_hint=args.chain_hint,
        )
    )
    print(f"enqueued job_id={job_id} source_type={args.source_type} ref={args.source_ref}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
