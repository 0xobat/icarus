"""Schema creation and migration framework for the Icarus database.

Uses SQLAlchemy's ``Base.metadata.create_all()`` for initial schema creation
and a ``schema_versions`` table for tracking applied migrations. This approach
supports both SQLite (development) and PostgreSQL (production on Railway).

Migration strategy:
    1. Initial schema is created via ``create_all()`` (idempotent).
    2. Future schema changes are registered as migration functions.
    3. Each migration is tracked by version number in ``schema_versions``.
    4. ``run_migrations()`` applies any unapplied migrations in order.

Backup strategy:
    - Development (SQLite): file-level backups via filesystem snapshots.
    - Production (PostgreSQL on Railway): Railway provides managed daily
      backups with point-in-time recovery. Additional pg_dump cron jobs
      can be configured for extra safety. See Railway docs:
      https://docs.railway.com/databases/postgresql
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from db.database import DatabaseManager
from db.models import Base, SchemaVersion
from monitoring.logger import get_logger

_logger = get_logger("db.migrations", enable_file=False)

# Type alias for migration functions
MigrationFn = Callable[[Session], None]

# Registry of migrations: version -> (description, migration_function)
_MIGRATIONS: dict[int, tuple[str, MigrationFn]] = {}


def register_migration(version: int, description: str) -> Callable[[MigrationFn], MigrationFn]:
    """Register a migration function for a specific version.

    Use as a decorator on migration functions. Migrations are run in
    version order and each is only applied once.

    Args:
        version: Integer version number (must be unique and sequential).
        description: Human-readable description of the migration.

    Returns:
        Decorator that registers the migration function.

    Raises:
        ValueError: If the version is already registered.
    """
    def decorator(fn: MigrationFn) -> MigrationFn:
        if version in _MIGRATIONS:
            msg = f"Migration version {version} already registered"
            raise ValueError(msg)
        _MIGRATIONS[version] = (description, fn)
        return fn
    return decorator


def initialize_schema(db_manager: DatabaseManager) -> None:
    """Create all tables defined in the ORM models.

    This is idempotent -- calling it multiple times is safe. Tables that
    already exist are left untouched.

    Args:
        db_manager: The database manager with an active engine.
    """
    Base.metadata.create_all(db_manager.engine)

    # Record version 0 (initial schema) if not already present
    session = db_manager.get_session()
    try:
        existing = session.execute(
            select(SchemaVersion).where(SchemaVersion.version == 0)
        ).scalars().first()

        if existing is None:
            initial = SchemaVersion(
                version=0,
                description="Initial schema — trades, portfolio_snapshots, "
                "strategy_performance, alerts, schema_versions",
                applied_at=datetime.now(UTC),
            )
            session.add(initial)
            session.commit()
            _logger.info("Initial schema (v0) recorded")
        else:
            _logger.info("Schema already initialized")
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_current_version(db_manager: DatabaseManager) -> int:
    """Return the highest applied migration version.

    Args:
        db_manager: The database manager with an active engine.

    Returns:
        The highest version number, or -1 if no migrations applied.
    """
    session = db_manager.get_session()
    try:
        result = session.execute(
            select(SchemaVersion.version).order_by(SchemaVersion.version.desc()).limit(1)
        ).scalars().first()
        return result if result is not None else -1
    finally:
        session.close()


def get_pending_migrations(db_manager: DatabaseManager) -> list[int]:
    """Return version numbers of unapplied migrations.

    Args:
        db_manager: The database manager with an active engine.

    Returns:
        Sorted list of version numbers that have not been applied.
    """
    current = get_current_version(db_manager)
    return sorted(v for v in _MIGRATIONS if v > current)


def run_migrations(db_manager: DatabaseManager) -> list[int]:
    """Apply all pending migrations in version order.

    Each migration runs in its own transaction. If a migration fails,
    that transaction is rolled back and subsequent migrations are skipped.

    Args:
        db_manager: The database manager with an active engine.

    Returns:
        List of version numbers that were successfully applied.

    Raises:
        Exception: If a migration fails (after rolling back that transaction).
    """
    pending = get_pending_migrations(db_manager)
    if not pending:
        _logger.info("No pending migrations")
        return []

    applied: list[int] = []
    for version in pending:
        description, fn = _MIGRATIONS[version]
        session = db_manager.get_session()
        try:
            _logger.info(
                f"Applying migration v{version}: {description}",
            )
            fn(session)

            record = SchemaVersion(
                version=version,
                description=description,
                applied_at=datetime.now(UTC),
            )
            session.add(record)
            session.commit()
            applied.append(version)
            _logger.info(f"Migration v{version} applied successfully")
        except Exception:
            session.rollback()
            _logger.exception(f"Migration v{version} failed")
            raise
        finally:
            session.close()

    return applied


def ensure_schema(db_manager: DatabaseManager) -> None:
    """Initialize schema and run all pending migrations.

    This is the single entry point for database setup. Safe to call
    on every application startup.

    Args:
        db_manager: The database manager with an active engine.
    """
    initialize_schema(db_manager)
    run_migrations(db_manager)
    current = get_current_version(db_manager)
    _logger.info(f"Database schema at version {current}")


# ---------------------------------------------------------------------------
# Example future migration (commented out as template)
# ---------------------------------------------------------------------------
# @register_migration(1, "Add execution_time_ms to trades table")
# def _migration_v1(session: Session) -> None:
#     session.execute(text(
#         "ALTER TABLE trades ADD COLUMN execution_time_ms INTEGER"
#     ))


# Ensure 'text' import is available for future migrations using raw SQL
__all__ = [
    "ensure_schema",
    "get_current_version",
    "get_pending_migrations",
    "initialize_schema",
    "register_migration",
    "run_migrations",
    "text",
]
