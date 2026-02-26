"""Repository pattern for database CRUD operations.

Encapsulates all database access behind a clean interface. The repository
handles session lifecycle (open, commit, rollback, close) so callers
never touch SQLAlchemy sessions directly.

All queries use indexed columns for sub-200ms dashboard reads.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.database import DatabaseManager
from db.models import Alert, PortfolioSnapshot, StrategyPerformance, Trade
from monitoring.logger import get_logger

_logger = get_logger("db.repository", enable_file=False)


class DatabaseRepository:
    """CRUD operations for the Icarus trading database.

    Provides high-level methods for recording trades, taking portfolio
    snapshots, recording strategy performance, and managing alerts.
    All methods handle their own session lifecycle.

    Args:
        db_manager: The database manager providing session access.
    """

    def __init__(self, db_manager: DatabaseManager) -> None:
        self._db = db_manager

    def _to_decimal(self, value: Any) -> Decimal:
        """Convert a numeric value to Decimal for storage.

        Args:
            value: A number, string, or Decimal to convert.

        Returns:
            The value as a Decimal.

        Raises:
            ValueError: If the value cannot be converted.
        """
        if isinstance(value, Decimal):
            return value
        if value is None:
            msg = "Cannot convert None to Decimal"
            raise ValueError(msg)
        return Decimal(str(value))

    def _to_optional_decimal(self, value: Any) -> Decimal | None:
        """Convert a possibly-None numeric value to Decimal.

        Args:
            value: A number, string, Decimal, or None.

        Returns:
            The value as Decimal, or None if the input is None.
        """
        if value is None:
            return None
        return self._to_decimal(value)

    # ------------------------------------------------------------------
    # Trades
    # ------------------------------------------------------------------

    def record_trade(self, trade_data: dict[str, Any]) -> Trade:
        """Record an executed trade with full context.

        Args:
            trade_data: Dictionary containing trade details. Required keys:
                ``strategy``, ``protocol``, ``chain``, ``action``,
                ``asset_in``, ``amount_in``. Optional keys: ``trade_id``,
                ``correlation_id``, ``timestamp``, ``asset_out``,
                ``amount_out``, ``price_at_execution``, ``gas_used``,
                ``gas_price_wei``, ``slippage_bps``, ``tx_hash``,
                ``status``, ``error_message``, ``metadata``.

        Returns:
            The created Trade ORM instance.

        Raises:
            KeyError: If required fields are missing.
            Exception: On database errors (rolled back automatically).
        """
        session: Session = self._db.get_session()
        try:
            metadata = trade_data.get("metadata")
            metadata_json = json.dumps(metadata) if metadata is not None else None

            trade = Trade(
                trade_id=trade_data.get("trade_id", uuid.uuid4().hex),
                correlation_id=trade_data.get("correlation_id", uuid.uuid4().hex),
                timestamp=trade_data.get("timestamp", datetime.now(UTC)),
                strategy=trade_data["strategy"],
                protocol=trade_data["protocol"],
                chain=trade_data["chain"],
                action=trade_data["action"],
                asset_in=trade_data["asset_in"],
                asset_out=trade_data.get("asset_out"),
                amount_in=self._to_decimal(trade_data["amount_in"]),
                amount_out=self._to_optional_decimal(trade_data.get("amount_out")),
                price_at_execution=self._to_optional_decimal(
                    trade_data.get("price_at_execution")
                ),
                gas_used=trade_data.get("gas_used"),
                gas_price_wei=trade_data.get("gas_price_wei"),
                slippage_bps=trade_data.get("slippage_bps"),
                tx_hash=trade_data.get("tx_hash"),
                status=trade_data.get("status", "pending"),
                error_message=trade_data.get("error_message"),
                metadata_json=metadata_json,
            )
            session.add(trade)
            session.commit()
            session.refresh(trade)

            _logger.info(
                "Trade recorded",
                extra={
                    "data": {
                        "trade_id": trade.trade_id,
                        "strategy": trade.strategy,
                        "action": trade.action,
                        "status": trade.status,
                    }
                },
            )
            return trade
        except Exception:
            session.rollback()
            _logger.exception("Failed to record trade")
            raise
        finally:
            session.close()

    def get_trades(
        self,
        *,
        strategy: str | None = None,
        chain: str | None = None,
        status: str | None = None,
        since: datetime | None = None,
        limit: int = 100,
    ) -> list[Trade]:
        """Query trades with optional filters.

        All filters use indexed columns for sub-200ms response times.

        Args:
            strategy: Filter by strategy name.
            chain: Filter by blockchain.
            status: Filter by trade status.
            since: Return only trades after this timestamp.
            limit: Maximum number of results (default 100).

        Returns:
            List of Trade instances, ordered by timestamp descending.
        """
        session: Session = self._db.get_session()
        try:
            stmt = select(Trade)

            if strategy is not None:
                stmt = stmt.where(Trade.strategy == strategy)
            if chain is not None:
                stmt = stmt.where(Trade.chain == chain)
            if status is not None:
                stmt = stmt.where(Trade.status == status)
            if since is not None:
                stmt = stmt.where(Trade.timestamp >= since)

            stmt = stmt.order_by(Trade.timestamp.desc()).limit(limit)
            result = session.execute(stmt).scalars().all()
            return list(result)
        finally:
            session.close()

    # ------------------------------------------------------------------
    # Portfolio Snapshots
    # ------------------------------------------------------------------

    def take_portfolio_snapshot(
        self, snapshot_data: dict[str, Any]
    ) -> PortfolioSnapshot:
        """Record a point-in-time portfolio snapshot.

        Args:
            snapshot_data: Dictionary containing snapshot details. Required
                keys: ``total_value_usd``, ``stablecoin_value_usd``,
                ``deployed_value_usd``, ``positions``, ``drawdown_from_peak``,
                ``peak_value_usd``. Optional keys: ``timestamp``,
                ``positions_json``.

        Returns:
            The created PortfolioSnapshot ORM instance.

        Raises:
            KeyError: If required fields are missing.
        """
        session: Session = self._db.get_session()
        try:
            positions = snapshot_data.get("positions")
            positions_json = snapshot_data.get("positions_json")
            if positions_json is None:
                positions_json = json.dumps(positions if positions is not None else [])

            snapshot = PortfolioSnapshot(
                timestamp=snapshot_data.get("timestamp", datetime.now(UTC)),
                total_value_usd=self._to_decimal(snapshot_data["total_value_usd"]),
                stablecoin_value_usd=self._to_decimal(snapshot_data["stablecoin_value_usd"]),
                deployed_value_usd=self._to_decimal(snapshot_data["deployed_value_usd"]),
                positions_json=positions_json,
                drawdown_from_peak=self._to_decimal(snapshot_data["drawdown_from_peak"]),
                peak_value_usd=self._to_decimal(snapshot_data["peak_value_usd"]),
            )
            session.add(snapshot)
            session.commit()
            session.refresh(snapshot)

            _logger.info(
                "Portfolio snapshot taken",
                extra={
                    "data": {
                        "total_value_usd": str(snapshot.total_value_usd),
                        "drawdown_from_peak": str(snapshot.drawdown_from_peak),
                    }
                },
            )
            return snapshot
        except Exception:
            session.rollback()
            _logger.exception("Failed to take portfolio snapshot")
            raise
        finally:
            session.close()

    def get_latest_snapshot(self) -> PortfolioSnapshot | None:
        """Return the most recent portfolio snapshot, or None if empty.

        Returns:
            The latest PortfolioSnapshot, or None.
        """
        session: Session = self._db.get_session()
        try:
            stmt = (
                select(PortfolioSnapshot)
                .order_by(PortfolioSnapshot.timestamp.desc())
                .limit(1)
            )
            return session.execute(stmt).scalars().first()
        finally:
            session.close()

    def get_snapshots(
        self,
        *,
        since: datetime | None = None,
        limit: int = 100,
    ) -> list[PortfolioSnapshot]:
        """Query portfolio snapshots with optional time filter.

        Args:
            since: Return only snapshots after this timestamp.
            limit: Maximum number of results (default 100).

        Returns:
            List of PortfolioSnapshot instances, ordered by timestamp descending.
        """
        session: Session = self._db.get_session()
        try:
            stmt = select(PortfolioSnapshot)
            if since is not None:
                stmt = stmt.where(PortfolioSnapshot.timestamp >= since)
            stmt = stmt.order_by(PortfolioSnapshot.timestamp.desc()).limit(limit)
            return list(session.execute(stmt).scalars().all())
        finally:
            session.close()

    # ------------------------------------------------------------------
    # Strategy Performance
    # ------------------------------------------------------------------

    def record_strategy_performance(
        self, perf_data: dict[str, Any]
    ) -> StrategyPerformance:
        """Record aggregated strategy performance for a period.

        Args:
            perf_data: Dictionary containing performance details. Required
                keys: ``strategy``, ``period``, ``pnl_usd``, ``return_pct``,
                ``gas_cost_usd``, ``trade_count``. Optional keys:
                ``timestamp``, ``win_rate``.

        Returns:
            The created StrategyPerformance ORM instance.

        Raises:
            KeyError: If required fields are missing.
        """
        session: Session = self._db.get_session()
        try:
            perf = StrategyPerformance(
                timestamp=perf_data.get("timestamp", datetime.now(UTC)),
                strategy=perf_data["strategy"],
                period=perf_data["period"],
                pnl_usd=self._to_decimal(perf_data["pnl_usd"]),
                return_pct=self._to_decimal(perf_data["return_pct"]),
                gas_cost_usd=self._to_decimal(perf_data["gas_cost_usd"]),
                trade_count=perf_data["trade_count"],
                win_rate=self._to_optional_decimal(perf_data.get("win_rate")),
            )
            session.add(perf)
            session.commit()
            session.refresh(perf)

            _logger.info(
                "Strategy performance recorded",
                extra={
                    "data": {
                        "strategy": perf.strategy,
                        "period": perf.period,
                        "pnl_usd": str(perf.pnl_usd),
                    }
                },
            )
            return perf
        except Exception:
            session.rollback()
            _logger.exception("Failed to record strategy performance")
            raise
        finally:
            session.close()

    def get_strategy_performance(
        self,
        strategy: str,
        *,
        period: str | None = None,
        limit: int = 100,
    ) -> list[StrategyPerformance]:
        """Query performance records for a specific strategy.

        Args:
            strategy: The strategy name to query.
            period: Optional period filter (daily, weekly, monthly).
            limit: Maximum number of results (default 100).

        Returns:
            List of StrategyPerformance instances, ordered by timestamp descending.
        """
        session: Session = self._db.get_session()
        try:
            stmt = select(StrategyPerformance).where(
                StrategyPerformance.strategy == strategy
            )
            if period is not None:
                stmt = stmt.where(StrategyPerformance.period == period)
            stmt = stmt.order_by(StrategyPerformance.timestamp.desc()).limit(limit)
            return list(session.execute(stmt).scalars().all())
        finally:
            session.close()

    # ------------------------------------------------------------------
    # Alerts
    # ------------------------------------------------------------------

    def create_alert(self, alert_data: dict[str, Any]) -> Alert:
        """Create a system alert.

        Args:
            alert_data: Dictionary containing alert details. Required keys:
                ``severity``, ``category``, ``message``. Optional keys:
                ``timestamp``, ``data``, ``data_json``, ``acknowledged``.

        Returns:
            The created Alert ORM instance.

        Raises:
            KeyError: If required fields are missing.
        """
        session: Session = self._db.get_session()
        try:
            data = alert_data.get("data")
            data_json = alert_data.get("data_json")
            if data_json is None and data is not None:
                data_json = json.dumps(data)

            alert = Alert(
                timestamp=alert_data.get("timestamp", datetime.now(UTC)),
                severity=alert_data["severity"],
                category=alert_data["category"],
                message=alert_data["message"],
                data_json=data_json,
                acknowledged=alert_data.get("acknowledged", False),
            )
            session.add(alert)
            session.commit()
            session.refresh(alert)

            _logger.info(
                "Alert created",
                extra={
                    "data": {
                        "severity": alert.severity,
                        "category": alert.category,
                        "message": alert.message,
                    }
                },
            )
            return alert
        except Exception:
            session.rollback()
            _logger.exception("Failed to create alert")
            raise
        finally:
            session.close()

    def get_unacknowledged_alerts(
        self,
        *,
        severity: str | None = None,
        limit: int = 100,
    ) -> list[Alert]:
        """Query unacknowledged alerts.

        Args:
            severity: Optional severity filter.
            limit: Maximum number of results (default 100).

        Returns:
            List of unacknowledged Alert instances, ordered by timestamp descending.
        """
        session: Session = self._db.get_session()
        try:
            stmt = select(Alert).where(Alert.acknowledged == False)  # noqa: E712
            if severity is not None:
                stmt = stmt.where(Alert.severity == severity)
            stmt = stmt.order_by(Alert.timestamp.desc()).limit(limit)
            return list(session.execute(stmt).scalars().all())
        finally:
            session.close()

    def acknowledge_alert(self, alert_id: int) -> Alert | None:
        """Mark an alert as acknowledged.

        Args:
            alert_id: The database ID of the alert to acknowledge.

        Returns:
            The updated Alert instance, or None if not found.
        """
        session: Session = self._db.get_session()
        try:
            alert = session.get(Alert, alert_id)
            if alert is None:
                return None
            alert.acknowledged = True
            session.commit()
            session.refresh(alert)

            _logger.info(
                "Alert acknowledged",
                extra={"data": {"alert_id": alert_id}},
            )
            return alert
        except Exception:
            session.rollback()
            _logger.exception("Failed to acknowledge alert")
            raise
        finally:
            session.close()

    def get_alerts(
        self,
        *,
        severity: str | None = None,
        category: str | None = None,
        since: datetime | None = None,
        limit: int = 100,
    ) -> list[Alert]:
        """Query alerts with optional filters.

        Args:
            severity: Filter by severity level.
            category: Filter by alert category.
            since: Return only alerts after this timestamp.
            limit: Maximum number of results (default 100).

        Returns:
            List of Alert instances, ordered by timestamp descending.
        """
        session: Session = self._db.get_session()
        try:
            stmt = select(Alert)
            if severity is not None:
                stmt = stmt.where(Alert.severity == severity)
            if category is not None:
                stmt = stmt.where(Alert.category == category)
            if since is not None:
                stmt = stmt.where(Alert.timestamp >= since)
            stmt = stmt.order_by(Alert.timestamp.desc()).limit(limit)
            return list(session.execute(stmt).scalars().all())
        finally:
            session.close()
