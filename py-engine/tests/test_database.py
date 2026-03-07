"""Tests for the database layer: models, database manager, repository, and migrations."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from db.database import DatabaseConfig, DatabaseManager
from db.models import Alert, Base, PortfolioSnapshot, StrategyPerformance, Trade
from db.repository import DatabaseRepository

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_manager():
    """Create an in-memory SQLite database manager for testing."""
    config = DatabaseConfig(url="sqlite:///:memory:", echo=False)
    manager = DatabaseManager(config)
    manager.create_tables()
    yield manager
    manager.close()


@pytest.fixture()
def repo(db_manager):
    """Create a repository backed by the in-memory database."""
    return DatabaseRepository(db_manager)


def _make_trade_data(**overrides):
    """Create a minimal valid trade data dict with optional overrides."""
    data = {
        "strategy": "aave_lending",
        "protocol": "aave_v3",
        "chain": "ethereum",
        "action": "supply",
        "asset_in": "USDC",
        "amount_in": "1000.50",
        "status": "confirmed",
    }
    data.update(overrides)
    return data


def _make_snapshot_data(**overrides):
    """Create a minimal valid snapshot data dict with optional overrides."""
    data = {
        "total_value_usd": "10000.00",
        "stablecoin_value_usd": "3000.00",
        "deployed_value_usd": "7000.00",
        "positions": [{"protocol": "aave", "value": 7000}],
        "drawdown_from_peak": "0.05",
        "peak_value_usd": "10500.00",
    }
    data.update(overrides)
    return data


def _make_perf_data(**overrides):
    """Create a minimal valid performance data dict with optional overrides."""
    data = {
        "strategy": "aave_lending",
        "period": "daily",
        "pnl_usd": "150.25",
        "return_pct": "0.015",
        "gas_cost_usd": "12.50",
        "trade_count": 5,
    }
    data.update(overrides)
    return data


def _make_alert_data(**overrides):
    """Create a minimal valid alert data dict with optional overrides."""
    data = {
        "severity": "warning",
        "category": "circuit_breaker",
        "message": "Portfolio drawdown approaching threshold",
    }
    data.update(overrides)
    return data


# ---------------------------------------------------------------------------
# Model Tests
# ---------------------------------------------------------------------------


class TestModels:
    def test_trade_model_has_correct_tablename(self):
        assert Trade.__tablename__ == "trades"

    def test_portfolio_snapshot_model_has_correct_tablename(self):
        assert PortfolioSnapshot.__tablename__ == "portfolio_snapshots"

    def test_strategy_performance_model_has_correct_tablename(self):
        assert StrategyPerformance.__tablename__ == "strategy_performance"

    def test_alert_model_has_correct_tablename(self):
        assert Alert.__tablename__ == "alerts"

    def test_base_is_declarative_base(self):
        assert hasattr(Base, "metadata")
        assert hasattr(Base, "registry")

    def test_trade_table_has_expected_indices(self):
        idx_names = {idx.name for idx in Trade.__table__.indexes}
        assert "ix_trades_strategy" in idx_names
        assert "ix_trades_timestamp" in idx_names
        assert "ix_trades_chain" in idx_names
        assert "ix_trades_status" in idx_names
        assert "ix_trades_strategy_timestamp" in idx_names

    def test_portfolio_snapshot_table_has_timestamp_index(self):
        idx_names = {idx.name for idx in PortfolioSnapshot.__table__.indexes}
        assert "ix_portfolio_snapshots_timestamp" in idx_names

    def test_strategy_performance_table_has_expected_indices(self):
        idx_names = {idx.name for idx in StrategyPerformance.__table__.indexes}
        assert "ix_strategy_performance_strategy" in idx_names
        assert "ix_strategy_performance_timestamp" in idx_names
        assert "ix_strategy_performance_strategy_period" in idx_names

    def test_alert_table_has_expected_indices(self):
        idx_names = {idx.name for idx in Alert.__table__.indexes}
        assert "ix_alerts_timestamp" in idx_names
        assert "ix_alerts_severity" in idx_names
        assert "ix_alerts_category" in idx_names
        assert "ix_alerts_acknowledged" in idx_names


# ---------------------------------------------------------------------------
# DatabaseManager Tests
# ---------------------------------------------------------------------------


class TestDatabaseManager:
    def test_creates_engine_on_first_access(self, db_manager):
        assert db_manager.engine is not None

    def test_engine_is_cached(self, db_manager):
        engine1 = db_manager.engine
        engine2 = db_manager.engine
        assert engine1 is engine2

    def test_session_factory_cached(self, db_manager):
        sf1 = db_manager.session_factory
        sf2 = db_manager.session_factory
        assert sf1 is sf2

    def test_get_session_returns_session(self, db_manager):
        session = db_manager.get_session()
        assert session is not None
        session.close()

    def test_health_check_passes(self, db_manager):
        assert db_manager.health_check() is True

    def test_create_tables_is_idempotent(self, db_manager):
        db_manager.create_tables()
        db_manager.create_tables()
        assert db_manager.health_check() is True

    def test_close_disposes_engine(self, db_manager):
        db_manager.close()
        assert db_manager._engine is None
        assert db_manager._session_factory is None

    def test_default_config_uses_sqlite(self):
        config = DatabaseConfig()
        assert "sqlite" in config.url

    def test_custom_config(self):
        config = DatabaseConfig(
            url="sqlite:///test.db",
            echo=True,
            pool_size=10,
            pool_recycle=1800,
        )
        assert config.url == "sqlite:///test.db"
        assert config.echo is True
        assert config.pool_size == 10
        assert config.pool_recycle == 1800


# ---------------------------------------------------------------------------
# Repository — Trade Tests
# ---------------------------------------------------------------------------


class TestRepositoryTrades:
    def test_record_trade_minimal(self, repo):
        trade = repo.record_trade(_make_trade_data())
        assert trade.id is not None
        assert trade.strategy == "aave_lending"
        assert trade.action == "supply"
        assert trade.asset_in == "USDC"
        assert trade.status == "confirmed"

    def test_record_trade_with_all_fields(self, repo):
        now = datetime.now(UTC)
        trade = repo.record_trade(_make_trade_data(
            trade_id="test-trade-001",
            correlation_id="corr-001",
            timestamp=now,
            asset_out="aUSDC",
            amount_out="998.75",
            price_at_execution="1.0005",
            gas_used=150000,
            gas_price_wei=30000000000,
            slippage_bps=5,
            tx_hash="0xabc123",
            error_message=None,
            metadata={"pool_address": "0x123"},
        ))
        assert trade.trade_id == "test-trade-001"
        assert trade.correlation_id == "corr-001"
        assert trade.asset_out == "aUSDC"
        assert trade.gas_used == 150000
        assert trade.gas_price_wei == 30000000000
        assert trade.slippage_bps == 5
        assert trade.tx_hash == "0xabc123"
        assert trade.metadata_json is not None
        parsed = json.loads(trade.metadata_json)
        assert parsed["pool_address"] == "0x123"

    def test_record_trade_auto_generates_ids(self, repo):
        trade = repo.record_trade(_make_trade_data())
        assert trade.trade_id is not None
        assert len(trade.trade_id) > 0
        assert trade.correlation_id is not None
        assert len(trade.correlation_id) > 0

    def test_record_trade_unique_trade_id_enforced(self, repo):
        repo.record_trade(_make_trade_data(trade_id="unique-001"))
        with pytest.raises(Exception):
            repo.record_trade(_make_trade_data(trade_id="unique-001"))

    def test_get_trades_returns_all(self, repo):
        for i in range(3):
            repo.record_trade(_make_trade_data(trade_id=f"trade-{i}"))
        trades = repo.get_trades()
        assert len(trades) == 3

    def test_get_trades_filter_by_strategy(self, repo):
        repo.record_trade(_make_trade_data(trade_id="t1", strategy="aave_lending"))
        repo.record_trade(_make_trade_data(trade_id="t2", strategy="uniswap_lp"))
        repo.record_trade(_make_trade_data(trade_id="t3", strategy="aave_lending"))

        aave_trades = repo.get_trades(strategy="aave_lending")
        assert len(aave_trades) == 2
        assert all(t.strategy == "aave_lending" for t in aave_trades)

    def test_get_trades_filter_by_chain(self, repo):
        repo.record_trade(_make_trade_data(trade_id="t1", chain="ethereum"))
        repo.record_trade(_make_trade_data(trade_id="t2", chain="arbitrum"))

        eth_trades = repo.get_trades(chain="ethereum")
        assert len(eth_trades) == 1
        assert eth_trades[0].chain == "ethereum"

    def test_get_trades_filter_by_status(self, repo):
        repo.record_trade(_make_trade_data(trade_id="t1", status="confirmed"))
        repo.record_trade(_make_trade_data(trade_id="t2", status="failed"))
        repo.record_trade(_make_trade_data(trade_id="t3", status="confirmed"))

        failed = repo.get_trades(status="failed")
        assert len(failed) == 1
        assert failed[0].status == "failed"

    def test_get_trades_filter_by_since(self, repo):
        old_time = datetime(2024, 1, 1, tzinfo=UTC)
        new_time = datetime(2025, 6, 1, tzinfo=UTC)

        repo.record_trade(_make_trade_data(trade_id="t1", timestamp=old_time))
        repo.record_trade(_make_trade_data(trade_id="t2", timestamp=new_time))

        recent = repo.get_trades(since=datetime(2025, 1, 1, tzinfo=UTC))
        assert len(recent) == 1
        assert recent[0].trade_id == "t2"

    def test_get_trades_respects_limit(self, repo):
        for i in range(10):
            repo.record_trade(_make_trade_data(trade_id=f"trade-{i}"))
        trades = repo.get_trades(limit=3)
        assert len(trades) == 3

    def test_get_trades_ordered_by_timestamp_desc(self, repo):
        t1 = datetime(2025, 1, 1, tzinfo=UTC)
        t2 = datetime(2025, 6, 1, tzinfo=UTC)
        t3 = datetime(2025, 12, 1, tzinfo=UTC)

        repo.record_trade(_make_trade_data(trade_id="t1", timestamp=t1))
        repo.record_trade(_make_trade_data(trade_id="t3", timestamp=t3))
        repo.record_trade(_make_trade_data(trade_id="t2", timestamp=t2))

        trades = repo.get_trades()
        assert trades[0].trade_id == "t3"
        assert trades[1].trade_id == "t2"
        assert trades[2].trade_id == "t1"

    def test_record_trade_with_decimal_amounts(self, repo):
        trade = repo.record_trade(_make_trade_data(
            amount_in=Decimal("1000.123456789012345678"),
            amount_out=Decimal("999.876543210987654321"),
        ))
        assert trade.amount_in is not None
        assert trade.amount_out is not None

    def test_record_trade_missing_required_field_raises(self, repo):
        data = _make_trade_data()
        del data["strategy"]
        with pytest.raises(KeyError):
            repo.record_trade(data)

    def test_get_trades_empty_returns_empty_list(self, repo):
        trades = repo.get_trades()
        assert trades == []

    def test_get_trades_combined_filters(self, repo):
        repo.record_trade(_make_trade_data(
            trade_id="t1",
            strategy="aave_lending",
            chain="ethereum",
            status="confirmed",
        ))
        repo.record_trade(_make_trade_data(
            trade_id="t2",
            strategy="aave_lending",
            chain="arbitrum",
            status="confirmed",
        ))
        repo.record_trade(_make_trade_data(
            trade_id="t3",
            strategy="uniswap_lp",
            chain="ethereum",
            status="failed",
        ))

        result = repo.get_trades(strategy="aave_lending", chain="ethereum")
        assert len(result) == 1
        assert result[0].trade_id == "t1"


# ---------------------------------------------------------------------------
# Repository — Portfolio Snapshot Tests
# ---------------------------------------------------------------------------


class TestRepositorySnapshots:
    def test_take_snapshot(self, repo):
        snapshot = repo.take_portfolio_snapshot(_make_snapshot_data())
        assert snapshot.id is not None
        assert snapshot.positions_json is not None
        positions = json.loads(snapshot.positions_json)
        assert len(positions) == 1
        assert positions[0]["protocol"] == "aave"

    def test_take_snapshot_with_raw_positions_json(self, repo):
        data = _make_snapshot_data()
        del data["positions"]
        data["positions_json"] = json.dumps([{"protocol": "lido", "value": 5000}])
        snapshot = repo.take_portfolio_snapshot(data)
        positions = json.loads(snapshot.positions_json)
        assert positions[0]["protocol"] == "lido"

    def test_get_latest_snapshot(self, repo):
        t1 = datetime(2025, 1, 1, tzinfo=UTC)
        t2 = datetime(2025, 6, 1, tzinfo=UTC)

        repo.take_portfolio_snapshot(_make_snapshot_data(timestamp=t1))
        repo.take_portfolio_snapshot(
            _make_snapshot_data(timestamp=t2, total_value_usd="15000.00")
        )

        latest = repo.get_latest_snapshot()
        assert latest is not None
        assert float(latest.total_value_usd) == pytest.approx(15000.00, rel=1e-2)

    def test_get_latest_snapshot_empty(self, repo):
        result = repo.get_latest_snapshot()
        assert result is None

    def test_get_snapshots_with_since_filter(self, repo):
        t1 = datetime(2024, 6, 1, tzinfo=UTC)
        t2 = datetime(2025, 6, 1, tzinfo=UTC)

        repo.take_portfolio_snapshot(_make_snapshot_data(timestamp=t1))
        repo.take_portfolio_snapshot(_make_snapshot_data(timestamp=t2))

        recent = repo.get_snapshots(since=datetime(2025, 1, 1, tzinfo=UTC))
        assert len(recent) == 1

    def test_get_snapshots_respects_limit(self, repo):
        for i in range(5):
            repo.take_portfolio_snapshot(
                _make_snapshot_data(
                    timestamp=datetime(2025, 1, 1, tzinfo=UTC) + timedelta(days=i)
                )
            )
        snapshots = repo.get_snapshots(limit=2)
        assert len(snapshots) == 2

    def test_snapshot_drawdown_values(self, repo):
        snapshot = repo.take_portfolio_snapshot(
            _make_snapshot_data(drawdown_from_peak="0.15", peak_value_usd="12000.00")
        )
        assert float(snapshot.drawdown_from_peak) == pytest.approx(0.15, rel=1e-4)
        assert float(snapshot.peak_value_usd) == pytest.approx(12000.00, rel=1e-2)


# ---------------------------------------------------------------------------
# Repository — Strategy Performance Tests
# ---------------------------------------------------------------------------


class TestRepositoryPerformance:
    def test_record_performance(self, repo):
        perf = repo.record_strategy_performance(_make_perf_data())
        assert perf.id is not None
        assert perf.strategy == "aave_lending"
        assert perf.period == "daily"
        assert perf.trade_count == 5

    def test_record_performance_with_win_rate(self, repo):
        perf = repo.record_strategy_performance(_make_perf_data(win_rate="0.75"))
        assert float(perf.win_rate) == pytest.approx(0.75, rel=1e-4)

    def test_record_performance_without_win_rate(self, repo):
        perf = repo.record_strategy_performance(_make_perf_data())
        assert perf.win_rate is None

    def test_get_strategy_performance(self, repo):
        repo.record_strategy_performance(_make_perf_data(strategy="aave_lending"))
        repo.record_strategy_performance(_make_perf_data(strategy="uniswap_lp"))
        repo.record_strategy_performance(_make_perf_data(strategy="aave_lending"))

        aave_perf = repo.get_strategy_performance("aave_lending")
        assert len(aave_perf) == 2
        assert all(p.strategy == "aave_lending" for p in aave_perf)

    def test_get_strategy_performance_filter_by_period(self, repo):
        repo.record_strategy_performance(_make_perf_data(period="daily"))
        repo.record_strategy_performance(_make_perf_data(period="weekly"))
        repo.record_strategy_performance(_make_perf_data(period="daily"))

        daily = repo.get_strategy_performance("aave_lending", period="daily")
        assert len(daily) == 2

    def test_get_strategy_performance_empty(self, repo):
        result = repo.get_strategy_performance("nonexistent")
        assert result == []

    def test_performance_pnl_values(self, repo):
        perf = repo.record_strategy_performance(
            _make_perf_data(pnl_usd="-50.25", return_pct="-0.005")
        )
        assert float(perf.pnl_usd) == pytest.approx(-50.25, rel=1e-2)
        assert float(perf.return_pct) == pytest.approx(-0.005, rel=1e-4)


# ---------------------------------------------------------------------------
# Repository — Alert Tests
# ---------------------------------------------------------------------------


class TestRepositoryAlerts:
    def test_create_alert(self, repo):
        alert = repo.create_alert(_make_alert_data())
        assert alert.id is not None
        assert alert.severity == "warning"
        assert alert.category == "circuit_breaker"
        assert alert.acknowledged is False

    def test_create_alert_with_data(self, repo):
        alert = repo.create_alert(_make_alert_data(
            data={"threshold": 0.20, "current": 0.18},
        ))
        assert alert.data_json is not None
        parsed = json.loads(alert.data_json)
        assert parsed["threshold"] == 0.20

    def test_create_alert_with_raw_data_json(self, repo):
        alert = repo.create_alert(_make_alert_data(
            data_json='{"key": "value"}',
        ))
        parsed = json.loads(alert.data_json)
        assert parsed["key"] == "value"

    def test_get_unacknowledged_alerts(self, repo):
        repo.create_alert(_make_alert_data())
        repo.create_alert(_make_alert_data(acknowledged=True))
        repo.create_alert(_make_alert_data())

        unacked = repo.get_unacknowledged_alerts()
        assert len(unacked) == 2
        assert all(not a.acknowledged for a in unacked)

    def test_get_unacknowledged_alerts_filter_by_severity(self, repo):
        repo.create_alert(_make_alert_data(severity="warning"))
        repo.create_alert(_make_alert_data(severity="critical"))
        repo.create_alert(_make_alert_data(severity="warning"))

        warnings = repo.get_unacknowledged_alerts(severity="warning")
        assert len(warnings) == 2

    def test_acknowledge_alert(self, repo):
        alert = repo.create_alert(_make_alert_data())
        assert alert.acknowledged is False

        updated = repo.acknowledge_alert(alert.id)
        assert updated is not None
        assert updated.acknowledged is True

    def test_acknowledge_nonexistent_alert(self, repo):
        result = repo.acknowledge_alert(99999)
        assert result is None

    def test_get_alerts_filter_by_category(self, repo):
        repo.create_alert(_make_alert_data(category="circuit_breaker"))
        repo.create_alert(_make_alert_data(category="risk"))
        repo.create_alert(_make_alert_data(category="circuit_breaker"))

        cb_alerts = repo.get_alerts(category="circuit_breaker")
        assert len(cb_alerts) == 2

    def test_get_alerts_filter_by_since(self, repo):
        old = datetime(2024, 1, 1, tzinfo=UTC)
        new = datetime(2025, 6, 1, tzinfo=UTC)

        repo.create_alert(_make_alert_data(timestamp=old))
        repo.create_alert(_make_alert_data(timestamp=new))

        recent = repo.get_alerts(since=datetime(2025, 1, 1, tzinfo=UTC))
        assert len(recent) == 1

    def test_get_alerts_empty(self, repo):
        result = repo.get_alerts()
        assert result == []

    def test_alert_severity_levels(self, repo):
        for severity in ("info", "warning", "critical"):
            repo.create_alert(_make_alert_data(severity=severity))
        all_alerts = repo.get_alerts()
        severities = {a.severity for a in all_alerts}
        assert severities == {"info", "warning", "critical"}


# ---------------------------------------------------------------------------
# Query Performance Tests
# ---------------------------------------------------------------------------


class TestQueryPerformance:
    def test_trade_query_performance_under_200ms(self, repo):
        # Insert 100 trades
        for i in range(100):
            repo.record_trade(_make_trade_data(
                trade_id=f"perf-trade-{i}",
                strategy="aave_lending" if i % 2 == 0 else "uniswap_lp",
                chain="ethereum" if i % 3 == 0 else "arbitrum",
            ))

        start = time.monotonic()
        trades = repo.get_trades(strategy="aave_lending", limit=50)
        elapsed_ms = (time.monotonic() - start) * 1000

        assert len(trades) > 0
        assert elapsed_ms < 200, f"Query took {elapsed_ms:.1f}ms (limit: 200ms)"

    def test_snapshot_query_performance_under_200ms(self, repo):
        for i in range(50):
            repo.take_portfolio_snapshot(
                _make_snapshot_data(
                    timestamp=datetime(2025, 1, 1, tzinfo=UTC) + timedelta(hours=i)
                )
            )

        start = time.monotonic()
        latest = repo.get_latest_snapshot()
        elapsed_ms = (time.monotonic() - start) * 1000

        assert latest is not None
        assert elapsed_ms < 200, f"Query took {elapsed_ms:.1f}ms (limit: 200ms)"

    def test_alert_query_performance_under_200ms(self, repo):
        for i in range(100):
            repo.create_alert(_make_alert_data(
                severity="warning" if i % 2 == 0 else "critical",
                acknowledged=(i % 5 == 0),
            ))

        start = time.monotonic()
        alerts = repo.get_unacknowledged_alerts(severity="critical", limit=50)
        elapsed_ms = (time.monotonic() - start) * 1000

        assert len(alerts) > 0
        assert elapsed_ms < 200, f"Query took {elapsed_ms:.1f}ms (limit: 200ms)"

    def test_performance_query_under_200ms(self, repo):
        for i in range(50):
            repo.record_strategy_performance(_make_perf_data(
                strategy="aave_lending" if i % 2 == 0 else "uniswap_lp",
                period="daily" if i % 3 == 0 else "weekly",
            ))

        start = time.monotonic()
        perfs = repo.get_strategy_performance("aave_lending", period="daily")
        elapsed_ms = (time.monotonic() - start) * 1000

        assert len(perfs) > 0
        assert elapsed_ms < 200, f"Query took {elapsed_ms:.1f}ms (limit: 200ms)"


# ---------------------------------------------------------------------------
# Snapshot Interval Configuration Tests
# ---------------------------------------------------------------------------


class TestSnapshotInterval:
    def test_hourly_snapshots_stored_correctly(self, repo):
        base = datetime(2025, 1, 1, tzinfo=UTC)
        for hour in range(24):
            repo.take_portfolio_snapshot(
                _make_snapshot_data(
                    timestamp=base + timedelta(hours=hour),
                    total_value_usd=str(10000 + hour * 10),
                )
            )

        all_snaps = repo.get_snapshots(limit=100)
        assert len(all_snaps) == 24

        # Verify ordering (most recent first)
        for i in range(len(all_snaps) - 1):
            assert all_snaps[i].timestamp >= all_snaps[i + 1].timestamp

    def test_snapshot_values_track_changes(self, repo):
        repo.take_portfolio_snapshot(
            _make_snapshot_data(
                total_value_usd="10000.00",
                drawdown_from_peak="0.00",
                peak_value_usd="10000.00",
            )
        )
        repo.take_portfolio_snapshot(
            _make_snapshot_data(
                total_value_usd="9500.00",
                drawdown_from_peak="0.05",
                peak_value_usd="10000.00",
            )
        )

        latest = repo.get_latest_snapshot()
        assert float(latest.total_value_usd) == pytest.approx(9500.00, rel=1e-2)
        assert float(latest.drawdown_from_peak) == pytest.approx(0.05, rel=1e-4)


# ---------------------------------------------------------------------------
# Edge Cases and Data Integrity
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_trade_with_failed_status_and_error(self, repo):
        trade = repo.record_trade(_make_trade_data(
            trade_id="failed-001",
            status="failed",
            error_message="Insufficient liquidity",
        ))
        assert trade.status == "failed"
        assert trade.error_message == "Insufficient liquidity"

    def test_trade_with_reverted_status(self, repo):
        trade = repo.record_trade(_make_trade_data(
            trade_id="reverted-001",
            status="reverted",
            tx_hash="0xdeadbeef",
        ))
        assert trade.status == "reverted"

    def test_large_metadata_json(self, repo):
        large_data = {"key_" + str(i): "value_" + str(i) for i in range(100)}
        trade = repo.record_trade(_make_trade_data(
            trade_id="large-meta",
            metadata=large_data,
        ))
        parsed = json.loads(trade.metadata_json)
        assert len(parsed) == 100

    def test_special_characters_in_message(self, repo):
        msg = "Alert: gas > 100 gwei & price < $1000 \"quoted\""
        alert = repo.create_alert(_make_alert_data(message=msg))
        assert alert.message == msg

    def test_decimal_precision(self, repo):
        trade = repo.record_trade(_make_trade_data(
            trade_id="precision-test",
            amount_in="0.000000000000000001",
        ))
        assert trade.amount_in is not None

    def test_zero_amount_trade(self, repo):
        trade = repo.record_trade(_make_trade_data(
            trade_id="zero-amount",
            amount_in="0",
        ))
        assert float(trade.amount_in) == pytest.approx(0.0)

    def test_negative_pnl(self, repo):
        perf = repo.record_strategy_performance(_make_perf_data(
            pnl_usd="-500.00",
            return_pct="-0.05",
        ))
        assert float(perf.pnl_usd) == pytest.approx(-500.00, rel=1e-2)

    def test_concurrent_sessions_independent(self, db_manager):
        repo1 = DatabaseRepository(db_manager)
        repo2 = DatabaseRepository(db_manager)

        repo1.record_trade(_make_trade_data(trade_id="session-1"))
        repo2.record_trade(_make_trade_data(trade_id="session-2"))

        trades = repo1.get_trades()
        assert len(trades) == 2
