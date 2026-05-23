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
from db.models import (
    Alert,
    DecisionAuditLog,
    PortfolioPosition,
    PortfolioSnapshot,
    StrategyPerformance,
    StrategyStatus,
    Trade,
)
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

    # ------------------------------------------------------------------
    # Portfolio Positions
    # ------------------------------------------------------------------

    def save_position(self, pos_data: dict[str, Any]) -> PortfolioPosition:
        """Save or update a portfolio position.

        If a position with the same ``position_id`` exists, it is updated.
        Otherwise a new row is inserted.

        Args:
            pos_data: Dictionary with position fields. Required keys:
                ``position_id``, ``strategy``, ``protocol``, ``chain``,
                ``asset``, ``entry_price``, ``amount``, ``current_value``.

        Returns:
            The saved PortfolioPosition ORM instance.
        """
        session: Session = self._db.get_session()
        try:
            existing = session.execute(
                select(PortfolioPosition).where(
                    PortfolioPosition.position_id == pos_data["position_id"]
                )
            ).scalars().first()

            protocol_data = pos_data.get("protocol_data")
            protocol_data_json = pos_data.get("protocol_data_json")
            if protocol_data_json is None and protocol_data is not None:
                protocol_data_json = json.dumps(protocol_data)

            if existing is not None:
                existing.current_value = self._to_decimal(pos_data["current_value"])
                existing.unrealized_pnl = self._to_decimal(
                    pos_data.get("unrealized_pnl", 0)
                )
                existing.realized_pnl = self._to_optional_decimal(
                    pos_data.get("realized_pnl")
                )
                existing.status = pos_data.get("status", existing.status)
                existing.amount = self._to_decimal(pos_data["amount"])
                if pos_data.get("close_time") is not None:
                    existing.close_time = pos_data["close_time"]
                if protocol_data_json is not None:
                    existing.protocol_data_json = protocol_data_json
                session.commit()
                session.refresh(existing)
                return existing

            position = PortfolioPosition(
                position_id=pos_data["position_id"],
                strategy=pos_data["strategy"],
                protocol=pos_data["protocol"],
                chain=pos_data["chain"],
                asset=pos_data["asset"],
                entry_price=self._to_decimal(pos_data["entry_price"]),
                entry_time=pos_data.get("entry_time", datetime.now(UTC)),
                amount=self._to_decimal(pos_data["amount"]),
                current_value=self._to_decimal(pos_data["current_value"]),
                unrealized_pnl=self._to_decimal(pos_data.get("unrealized_pnl", 0)),
                realized_pnl=self._to_optional_decimal(pos_data.get("realized_pnl")),
                status=pos_data.get("status", "open"),
                close_time=pos_data.get("close_time"),
                protocol_data_json=protocol_data_json,
            )
            session.add(position)
            session.commit()
            session.refresh(position)

            _logger.info(
                "Position saved",
                extra={"data": {
                    "position_id": position.position_id,
                    "strategy": position.strategy,
                    "status": position.status,
                }},
            )
            return position
        except Exception:
            session.rollback()
            _logger.exception("Failed to save position")
            raise
        finally:
            session.close()

    def get_positions(
        self,
        *,
        strategy: str | None = None,
        protocol: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[PortfolioPosition]:
        """Query portfolio positions with optional filters.

        Args:
            strategy: Filter by strategy name.
            protocol: Filter by protocol.
            status: Filter by position status (open/closed).
            limit: Maximum number of results (default 100).

        Returns:
            List of PortfolioPosition instances.
        """
        session: Session = self._db.get_session()
        try:
            stmt = select(PortfolioPosition)
            if strategy is not None:
                stmt = stmt.where(PortfolioPosition.strategy == strategy)
            if protocol is not None:
                stmt = stmt.where(PortfolioPosition.protocol == protocol)
            if status is not None:
                stmt = stmt.where(PortfolioPosition.status == status)
            stmt = stmt.order_by(PortfolioPosition.entry_time.desc()).limit(limit)
            return list(session.execute(stmt).scalars().all())
        finally:
            session.close()

    def get_position(self, position_id: str) -> PortfolioPosition | None:
        """Get a single position by position_id.

        Args:
            position_id: The unique position identifier.

        Returns:
            The PortfolioPosition, or None if not found.
        """
        session: Session = self._db.get_session()
        try:
            return session.execute(
                select(PortfolioPosition).where(
                    PortfolioPosition.position_id == position_id
                )
            ).scalars().first()
        finally:
            session.close()

    # ------------------------------------------------------------------
    # Strategy Statuses
    # ------------------------------------------------------------------

    def save_strategy_status(
        self, strategy_id: str, status: str = "active"
    ) -> StrategyStatus:
        """Save or update a strategy's active/inactive status.

        Args:
            strategy_id: The strategy identifier (e.g. ``LEND-001``).
            status: ``active`` or ``inactive``.

        Returns:
            The saved StrategyStatus ORM instance.
        """
        session: Session = self._db.get_session()
        try:
            existing = session.execute(
                select(StrategyStatus).where(
                    StrategyStatus.strategy_id == strategy_id
                )
            ).scalars().first()

            if existing is not None:
                existing.status = status
                existing.updated_at = datetime.now(UTC)
                session.commit()
                session.refresh(existing)
                return existing

            ss = StrategyStatus(
                strategy_id=strategy_id,
                status=status,
                updated_at=datetime.now(UTC),
            )
            session.add(ss)
            session.commit()
            session.refresh(ss)

            _logger.info(
                "Strategy status saved",
                extra={"data": {"strategy_id": strategy_id, "status": status}},
            )
            return ss
        except Exception:
            session.rollback()
            _logger.exception("Failed to save strategy status")
            raise
        finally:
            session.close()

    def get_strategy_statuses(self) -> list[StrategyStatus]:
        """Return all strategy statuses.

        Returns:
            List of all StrategyStatus records.
        """
        session: Session = self._db.get_session()
        try:
            stmt = select(StrategyStatus).order_by(StrategyStatus.strategy_id)
            return list(session.execute(stmt).scalars().all())
        finally:
            session.close()

    def get_strategy_status(self, strategy_id: str) -> StrategyStatus | None:
        """Get a single strategy status by strategy_id.

        Args:
            strategy_id: The strategy identifier.

        Returns:
            The StrategyStatus, or None if not found.
        """
        session: Session = self._db.get_session()
        try:
            return session.execute(
                select(StrategyStatus).where(
                    StrategyStatus.strategy_id == strategy_id
                )
            ).scalars().first()
        finally:
            session.close()

    # ------------------------------------------------------------------
    # Decision Audit Log
    # ------------------------------------------------------------------

    def record_decision(self, decision_data: dict[str, Any]) -> DecisionAuditLog:
        """Record a decision cycle for audit.

        Args:
            decision_data: Dictionary with decision details. Required keys:
                ``correlation_id``, ``decision_action``. Optional keys:
                ``timestamp``, ``reasoning``, ``strategy_reports``,
                ``orders``, ``passed_verification``, ``risk_flags``,
                ``prompt_tokens``, ``completion_tokens``.

        Returns:
            The created DecisionAuditLog ORM instance.
        """
        session: Session = self._db.get_session()
        try:
            reports = decision_data.get("strategy_reports")
            reports_json = decision_data.get("strategy_reports_json")
            if reports_json is None and reports is not None:
                reports_json = json.dumps(reports)

            orders = decision_data.get("orders")
            orders_json = decision_data.get("orders_json")
            if orders_json is None and orders is not None:
                orders_json = json.dumps(orders)

            risk_flags = decision_data.get("risk_flags")
            risk_flags_json = decision_data.get("risk_flags_json")
            if risk_flags_json is None and risk_flags is not None:
                risk_flags_json = json.dumps(risk_flags)

            entry = DecisionAuditLog(
                correlation_id=decision_data["correlation_id"],
                timestamp=decision_data.get("timestamp", datetime.now(UTC)),
                decision_action=decision_data["decision_action"],
                reasoning=decision_data.get("reasoning"),
                strategy_reports_json=reports_json,
                orders_json=orders_json,
                passed_verification=decision_data.get("passed_verification", True),
                risk_flags_json=risk_flags_json,
                prompt_tokens=decision_data.get("prompt_tokens"),
                completion_tokens=decision_data.get("completion_tokens"),
            )
            session.add(entry)
            session.commit()
            session.refresh(entry)

            _logger.info(
                "Decision recorded",
                extra={"data": {
                    "correlation_id": entry.correlation_id,
                    "action": entry.decision_action,
                    "passed_verification": entry.passed_verification,
                }},
            )
            return entry
        except Exception:
            session.rollback()
            _logger.exception("Failed to record decision")
            raise
        finally:
            session.close()

    def get_decisions(
        self,
        *,
        since: datetime | None = None,
        action: str | None = None,
        limit: int = 100,
    ) -> list[DecisionAuditLog]:
        """Query decision audit log entries.

        Args:
            since: Return only decisions after this timestamp.
            action: Filter by decision action type.
            limit: Maximum number of results (default 100).

        Returns:
            List of DecisionAuditLog instances, ordered by timestamp descending.
        """
        session: Session = self._db.get_session()
        try:
            stmt = select(DecisionAuditLog)
            if since is not None:
                stmt = stmt.where(DecisionAuditLog.timestamp >= since)
            if action is not None:
                stmt = stmt.where(DecisionAuditLog.decision_action == action)
            stmt = stmt.order_by(DecisionAuditLog.timestamp.desc()).limit(limit)
            return list(session.execute(stmt).scalars().all())
        finally:
            session.close()

    # ------------------------------------------------------------------
    # In-memory cache
    # ------------------------------------------------------------------

    def load_cache(self) -> dict[str, Any]:
        """Load portfolio state into an in-memory cache dict.

        Loads open positions, strategy statuses, and latest portfolio
        snapshot from the database for fast in-memory access.

        Returns:
            Dictionary with ``positions``, ``strategy_statuses``, and
            ``latest_snapshot`` keys.
        """
        positions = self.get_positions(status="open")
        statuses = self.get_strategy_statuses()
        snapshot = self.get_latest_snapshot()

        cache: dict[str, Any] = {
            "positions": {
                p.position_id: {
                    "position_id": p.position_id,
                    "strategy": p.strategy,
                    "protocol": p.protocol,
                    "chain": p.chain,
                    "asset": p.asset,
                    "entry_price": str(p.entry_price),
                    "amount": str(p.amount),
                    "current_value": str(p.current_value),
                    "unrealized_pnl": str(p.unrealized_pnl),
                    "status": p.status,
                }
                for p in positions
            },
            "strategy_statuses": {
                s.strategy_id: s.status for s in statuses
            },
            "latest_snapshot": {
                "total_value_usd": str(snapshot.total_value_usd),
                "drawdown_from_peak": str(snapshot.drawdown_from_peak),
                "timestamp": snapshot.timestamp.isoformat(),
            } if snapshot else None,
        }

        _logger.info(
            "Cache loaded from database",
            extra={"data": {
                "open_positions": len(cache["positions"]),
                "strategies": len(cache["strategy_statuses"]),
                "has_snapshot": cache["latest_snapshot"] is not None,
            }},
        )
        return cache
