"""SQLAlchemy ORM models for the Icarus trading database.

Schema is designed to be portable between SQLite (development) and PostgreSQL
(production on Railway). All financial amounts use ``Numeric`` which maps to
``NUMERIC`` on Postgres and ``REAL`` on SQLite. Indices are chosen for the
most common dashboard queries (by strategy, by timestamp, by chain).

NOTE: In development we use SQLite via aiosqlite. For production deployment,
swap the connection string to ``postgresql+asyncpg://...`` (Railway provides
managed PostgreSQL). The ORM layer and all queries remain the same.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Index, Integer, Numeric, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base for all Icarus ORM models."""


class Trade(Base):
    """Record of an executed trade with full context.

    Every trade executed by the system is recorded here for audit trail,
    performance analysis, and regulatory compliance.
    """

    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trade_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    strategy: Mapped[str] = mapped_column(String(64), nullable=False)
    protocol: Mapped[str] = mapped_column(String(64), nullable=False)
    chain: Mapped[str] = mapped_column(String(32), nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    asset_in: Mapped[str] = mapped_column(String(32), nullable=False)
    asset_out: Mapped[str | None] = mapped_column(String(32), nullable=True)
    amount_in: Mapped[float] = mapped_column(Numeric(precision=36, scale=18), nullable=False)
    amount_out: Mapped[float | None] = mapped_column(
        Numeric(precision=36, scale=18), nullable=True
    )
    price_at_execution: Mapped[float | None] = mapped_column(
        Numeric(precision=36, scale=18), nullable=True
    )
    gas_used: Mapped[int | None] = mapped_column(Integer, nullable=True)
    gas_price_wei: Mapped[int | None] = mapped_column(Integer, nullable=True)
    slippage_bps: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tx_hash: Mapped[str | None] = mapped_column(String(66), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_trades_strategy", "strategy"),
        Index("ix_trades_timestamp", "timestamp"),
        Index("ix_trades_chain", "chain"),
        Index("ix_trades_status", "status"),
        Index("ix_trades_strategy_timestamp", "strategy", "timestamp"),
    )


class PortfolioSnapshot(Base):
    """Point-in-time snapshot of portfolio state.

    Snapshots are taken at configurable intervals (minimum hourly) for
    tracking portfolio value, drawdown, and allocation over time.
    """

    __tablename__ = "portfolio_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    total_value_usd: Mapped[float] = mapped_column(
        Numeric(precision=36, scale=18), nullable=False
    )
    stablecoin_value_usd: Mapped[float] = mapped_column(
        Numeric(precision=36, scale=18), nullable=False
    )
    deployed_value_usd: Mapped[float] = mapped_column(
        Numeric(precision=36, scale=18), nullable=False
    )
    positions_json: Mapped[str] = mapped_column(Text, nullable=False)
    drawdown_from_peak: Mapped[float] = mapped_column(
        Numeric(precision=10, scale=6), nullable=False
    )
    peak_value_usd: Mapped[float] = mapped_column(
        Numeric(precision=36, scale=18), nullable=False
    )

    __table_args__ = (
        Index("ix_portfolio_snapshots_timestamp", "timestamp"),
    )


class StrategyPerformance(Base):
    """Aggregated strategy performance for a given period.

    Records PnL, gas costs, trade counts, and win rates per strategy
    for daily, weekly, and monthly reporting periods.
    """

    __tablename__ = "strategy_performance"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    strategy: Mapped[str] = mapped_column(String(64), nullable=False)
    period: Mapped[str] = mapped_column(String(16), nullable=False)
    pnl_usd: Mapped[float] = mapped_column(Numeric(precision=36, scale=18), nullable=False)
    return_pct: Mapped[float] = mapped_column(Numeric(precision=10, scale=6), nullable=False)
    gas_cost_usd: Mapped[float] = mapped_column(Numeric(precision=36, scale=18), nullable=False)
    trade_count: Mapped[int] = mapped_column(Integer, nullable=False)
    win_rate: Mapped[float | None] = mapped_column(Numeric(precision=10, scale=6), nullable=True)

    __table_args__ = (
        Index("ix_strategy_performance_strategy", "strategy"),
        Index("ix_strategy_performance_timestamp", "timestamp"),
        Index("ix_strategy_performance_strategy_period", "strategy", "period"),
    )


class Alert(Base):
    """System alert for circuit breakers, risk events, and operational issues.

    Alerts persist in the database for audit trail and can be acknowledged
    by operators via the dashboard or Discord bot.
    """

    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    data_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    acknowledged: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (
        Index("ix_alerts_timestamp", "timestamp"),
        Index("ix_alerts_severity", "severity"),
        Index("ix_alerts_category", "category"),
        Index("ix_alerts_acknowledged", "acknowledged"),
    )


class SchemaVersion(Base):
    """Track applied schema migrations for version control.

    Each row represents a migration that has been applied to the database.
    This enables future schema evolution without data loss.
    """

    __tablename__ = "schema_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    version: Mapped[int] = mapped_column(Integer, unique=True, nullable=False)
    description: Mapped[str] = mapped_column(String(256), nullable=False)
    applied_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
