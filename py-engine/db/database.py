"""Database engine and session management for the Icarus trading database.

Provides a ``DatabaseManager`` that handles engine creation, session factory
configuration, and connection lifecycle.

NOTE: In development we use SQLite (via ``sqlite:///path/to/db``). For
production on Railway, swap to ``postgresql+psycopg2://...``. The ORM layer
and all queries remain identical -- SQLAlchemy abstracts the dialect.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from db.models import Base
from monitoring.logger import get_logger

_logger = get_logger("database", enable_file=False)


@dataclass
class DatabaseConfig:
    """Configuration for the database connection.

    Attributes:
        url: SQLAlchemy database URL. Defaults to a local SQLite file.
        echo: Whether to log all SQL statements (useful for debugging).
        pool_size: Number of connections to keep in the pool (ignored for SQLite).
        pool_recycle: Seconds before a connection is recycled (ignored for SQLite).
    """

    url: str = field(
        default_factory=lambda: os.environ.get("DATABASE_URL", "sqlite:///data/icarus.db")
    )
    echo: bool = False
    pool_size: int = 5
    pool_recycle: int = 3600


class DatabaseManager:
    """Manage SQLAlchemy engine and session lifecycle.

    Provides synchronous session factory and schema creation. Designed
    for use with the repository pattern -- callers obtain sessions via
    ``get_session()`` and use them in ``with`` blocks.

    Args:
        config: Database configuration. Uses defaults if not provided.
    """

    def __init__(self, config: DatabaseConfig | None = None) -> None:
        self._config = config or DatabaseConfig()
        self._engine: Engine | None = None
        self._session_factory: sessionmaker[Session] | None = None

    @property
    def engine(self) -> Engine:
        """Return the SQLAlchemy engine, creating it on first access."""
        if self._engine is None:
            self._engine = self._create_engine()
        return self._engine

    @property
    def session_factory(self) -> sessionmaker[Session]:
        """Return the session factory, creating it on first access."""
        if self._session_factory is None:
            self._session_factory = sessionmaker(bind=self.engine)
        return self._session_factory

    def _create_engine(self) -> Engine:
        """Create the SQLAlchemy engine with dialect-appropriate settings."""
        url = self._config.url
        kwargs: dict[str, Any] = {"echo": self._config.echo}

        if url.startswith("sqlite"):
            # SQLite does not support pool_size or pool_recycle
            kwargs["connect_args"] = {"check_same_thread": False}
        else:
            # PostgreSQL or other databases
            kwargs["pool_size"] = self._config.pool_size
            kwargs["pool_recycle"] = self._config.pool_recycle

        engine = create_engine(url, **kwargs)

        # Enable WAL mode and foreign keys for SQLite
        if url.startswith("sqlite"):
            @event.listens_for(engine, "connect")
            def _set_sqlite_pragma(dbapi_conn: Any, _connection_record: Any) -> None:
                cursor = dbapi_conn.cursor()
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.close()

        _logger.info(
            "Database engine created",
            extra={"data": {"url": _sanitize_url(url)}},
        )
        return engine

    def get_session(self) -> Session:
        """Create and return a new database session.

        Returns:
            A new SQLAlchemy Session. Caller is responsible for closing it,
            typically via a ``with`` block or context manager.
        """
        return self.session_factory()

    def create_tables(self) -> None:
        """Create all ORM-defined tables if they do not already exist.

        This is safe to call multiple times -- ``create_all`` is idempotent.
        """
        Base.metadata.create_all(self.engine)
        _logger.info("Database tables created/verified")

    def health_check(self) -> bool:
        """Verify the database connection is healthy.

        Returns:
            True if the database responds to a simple query, False otherwise.
        """
        try:
            with self.engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return True
        except Exception:
            _logger.exception("Database health check failed")
            return False

    def close(self) -> None:
        """Dispose of the engine and release all connections."""
        if self._engine is not None:
            self._engine.dispose()
            self._engine = None
            self._session_factory = None
            _logger.info("Database engine disposed")


def _sanitize_url(url: str) -> str:
    """Redact credentials from a database URL for logging."""
    if "@" in url:
        # postgresql://user:pass@host/db -> postgresql://***@host/db
        scheme_end = url.index("://") + 3
        at_pos = url.index("@")
        return url[:scheme_end] + "***" + url[at_pos:]
    return url
