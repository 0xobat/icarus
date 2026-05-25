"""Atomic template write — Postgres row first, then 4 files on disk.

Write order (per the skeleton's docstring contract):
  1. INSERT into `templates` (manifest_yaml + evaluate_py_path + rationale).
  2. Write 4 files to `templates/<id>.tmp/`.
  3. Atomic os.rename `templates/<id>.tmp/` → `templates/<id>/`.
  4. On any failure between 2-3: rmdir + DELETE the templates row.

Failure surfacing: extractor failures get an `Alert` row with
category="extractor_failure" so the operator sees them in webapp +
Grafana without a dedicated table. The Template `judge_verdict`
defaults to FLAG_FOR_OPERATOR; Phase B's plausibility judge overwrites
it after extraction succeeds.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import structlog
from icarus.db.database import DatabaseManager
from icarus.db.models import Alert, Template
from sqlalchemy.exc import IntegrityError

from extractor_worker.pipeline import ExtractedTemplate

_logger = structlog.get_logger(service="extractor.writer")

DEFAULT_TEMPLATES_DIR = Path(os.environ.get("TEMPLATES_DIR", "templates"))


class TemplateWriteError(RuntimeError):
    """Either the Postgres row or the file write failed; the writer's
    rollback already ran, so the database + filesystem are consistent
    (no orphan row, no orphan dir)."""


@dataclass(frozen=True)
class WrittenTemplate:
    """Result of a successful template write — the on-disk path and the
    DB id, returned to the worker for the ACK step."""

    template_id: str
    templates_db_id: int
    templates_dir: Path


def _write_files_atomic(target_dir: Path, files: dict[str, str]) -> None:
    """Write all 4 files into a sibling .tmp dir, then atomic rename.

    Crash between mkdir and rename leaves a .tmp orphan; we clean those
    on startup (see `cleanup_orphan_temp_dirs`).
    """
    tmp_dir = target_dir.with_suffix(".tmp")
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=False)
    try:
        for name, content in files.items():
            (tmp_dir / name).write_text(content, encoding="utf-8")
        os.rename(tmp_dir, target_dir)
    except Exception:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise


def _files_payload(extracted: ExtractedTemplate) -> dict[str, str]:
    return {
        "manifest.yaml": extracted.manifest_yaml,
        "evaluate.py": extracted.evaluate_py,
        "smoke_test.py": extracted.smoke_test_py,
        "parameter_rationale.md": extracted.parameter_rationale_md,
    }


def _write_sync(
    extracted: ExtractedTemplate,
    db: DatabaseManager,
    templates_dir: Path,
) -> WrittenTemplate:
    """Synchronous body — runs inside asyncio.to_thread from the async
    worker. SQLAlchemy 1.x-style sync sessions; matches the v4.2-ported
    repository pattern.
    """
    target_dir = templates_dir / extracted.template_id
    if target_dir.exists():
        msg = (
            f"templates/{extracted.template_id}/ already exists; "
            f"re-extraction is a W3+ workflow, not v1"
        )
        raise TemplateWriteError(msg)

    manifest = extracted.manifest
    now = datetime.now(UTC)

    with db.get_session() as session:
        row = Template(
            template_id=extracted.template_id,
            semver=manifest.semver,
            title=manifest.title,
            chain=manifest.chain,
            protocol=manifest.protocol,
            asset_universe_json=json.dumps(list(manifest.asset_universe)),
            manifest_yaml=extracted.manifest_yaml,
            evaluate_py_path=str(target_dir / "evaluate.py"),
            parameter_rationale_md=extracted.parameter_rationale_md,
            # judge_verdict defaults to FLAG_FOR_OPERATOR per Q8.
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        try:
            session.commit()
        except IntegrityError as e:
            session.rollback()
            msg = (
                f"templates row for '{extracted.template_id}' already exists in DB. "
                f"({e.orig if hasattr(e, 'orig') else e})"
            )
            raise TemplateWriteError(msg) from e
        templates_db_id = row.id

    try:
        templates_dir.mkdir(parents=True, exist_ok=True)
        _write_files_atomic(target_dir, _files_payload(extracted))
    except Exception as e:
        # File write failed — rollback the DB row to keep DB ↔ FS consistent.
        with db.get_session() as session:
            session.query(Template).filter_by(template_id=extracted.template_id).delete()
            session.commit()
        msg = f"file write failed for {extracted.template_id}, DB row rolled back: {e}"
        raise TemplateWriteError(msg) from e

    _logger.info(
        "template_written",
        template_id=extracted.template_id,
        db_id=templates_db_id,
        target_dir=str(target_dir),
        chain=manifest.chain,
        protocol=manifest.protocol,
    )
    return WrittenTemplate(
        template_id=extracted.template_id,
        templates_db_id=templates_db_id,
        templates_dir=target_dir,
    )


async def write_template(
    extracted: ExtractedTemplate,
    *,
    db: DatabaseManager,
    templates_dir: Path = DEFAULT_TEMPLATES_DIR,
) -> WrittenTemplate:
    """Async-friendly wrapper. Offloads the sync DB+FS work to a thread
    so the worker's asyncio loop stays responsive to SIGTERM."""
    return await asyncio.to_thread(_write_sync, extracted, db, templates_dir)


def _record_failure_sync(
    db: DatabaseManager,
    *,
    paper_job_id: str,
    template_id: str | None,
    source_type: str,
    source_ref: str,
    error_class: str,
    error_message: str,
) -> int:
    """Write one Alert row capturing the extraction failure context.
    Severity is "warning" — failed extractions are not capital-affecting
    and the worker will requeue. Operator decides whether to escalate."""
    payload = {
        "paper_job_id": paper_job_id,
        "template_id": template_id,
        "source_type": source_type,
        "source_ref": source_ref,
        "error_class": error_class,
        "error_message": error_message,
    }
    with db.get_session() as session:
        alert = Alert(
            severity="warning",
            category="extractor_failure",
            message=f"extraction failed: {error_class}: {error_message[:200]}",
            data_json=json.dumps(payload),
        )
        session.add(alert)
        session.commit()
        return alert.id


async def record_extraction_failure(
    db: DatabaseManager,
    *,
    paper_job_id: str,
    template_id: str | None,
    source_type: str,
    source_ref: str,
    error_class: str,
    error_message: str,
) -> int:
    return await asyncio.to_thread(
        _record_failure_sync,
        db,
        paper_job_id=paper_job_id,
        template_id=template_id,
        source_type=source_type,
        source_ref=source_ref,
        error_class=error_class,
        error_message=error_message,
    )


def cleanup_orphan_temp_dirs(templates_dir: Path = DEFAULT_TEMPLATES_DIR) -> int:
    """Remove any `templates/*.tmp/` directories left over from a worker
    crash between mkdir and rename. Idempotent. Called once at worker
    startup."""
    if not templates_dir.exists():
        return 0
    removed = 0
    for child in templates_dir.iterdir():
        if child.is_dir() and child.suffix == ".tmp":
            shutil.rmtree(child, ignore_errors=True)
            removed += 1
    if removed:
        _logger.warning("removed_orphan_temp_dirs", count=removed)
    return removed
