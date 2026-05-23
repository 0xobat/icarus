"""Tests for the performance dashboard (MON-003)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from db.database import DatabaseConfig, DatabaseManager
from db.repository import DatabaseRepository
from monitoring.dashboard import (
    DrawdownInfo,
    GasSummary,
    PerformanceDashboard,
    PortfolioSummary,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def db_manager():
    config = DatabaseConfig(url="sqlite:///:memory:", echo=False)
    manager = DatabaseManager(config)
    manager.create_tables()
    yield manager
    manager.close()


@pytest.fixture()
def repo(db_manager):
    return DatabaseRepository(db_manager)


@pytest.fixture()
def dashboard(repo):
    return PerformanceDashboard(repo, eth_price_usd=Decimal("2000"))


def _make_trade(repo, **overrides):
    data = {
        "strategy": "aave_lending",
        "protocol": "aave_v3",
        "chain": "ethereum",
        "action": "supply",
        "asset_in": "USDC",
        "amount_in": "1000",
        "amount_out": "1050",
        "status": "confirmed",
        "gas_used": 100000,
        "gas_price_wei": 20000000000,
        "tx_hash": "0xabc123",
    }
    data.update(overrides)
    return repo.record_trade(data)


def _make_snapshot(repo, **overrides):
    data = {
        "total_value_usd": "10000",
        "stablecoin_value_usd": "3000",
        "deployed_value_usd": "7000",
        "positions": [],
        "drawdown_from_peak": "0.05",
        "peak_value_usd": "10500",
    }
    data.update(overrides)
    return repo.take_portfolio_snapshot(data)


# ---------------------------------------------------------------------------
# PortfolioSummary
# ---------------------------------------------------------------------------

class TestPortfolioSummary:
    def test_empty_database_returns_zero_summary(self, dashboard):
        summary = dashboard.get_portfolio_summary()
        assert isinstance(summary, PortfolioSummary)
        assert summary.total_value_usd == Decimal("0")
        assert summary.cumulative_pnl_usd == Decimal("0")
        assert summary.annualized_return_pct == Decimal("0")

    def test_single_snapshot_returns_zero_pnl(self, dashboard, repo):
        _make_snapshot(repo, total_value_usd="10000")
        summary = dashboard.get_portfolio_summary()
        assert summary.total_value_usd == Decimal("10000")
        assert summary.cumulative_pnl_usd == Decimal("0")

    def test_two_snapshots_calculates_pnl(self, dashboard, repo):
        now = datetime.now(UTC)
        _make_snapshot(
            repo,
            total_value_usd="10000",
            timestamp=now - timedelta(days=30),
        )
        _make_snapshot(
            repo,
            total_value_usd="11000",
            timestamp=now,
        )
        summary = dashboard.get_portfolio_summary()
        assert summary.total_value_usd == Decimal("11000")
        assert summary.cumulative_pnl_usd == Decimal("1000")
        assert summary.annualized_return_pct > 0

    def test_summary_is_cached(self, dashboard, repo):
        _make_snapshot(repo, total_value_usd="10000")
        s1 = dashboard.get_portfolio_summary()
        s2 = dashboard.get_portfolio_summary()
        assert s1 is s2

    def test_summary_cache_cleared_on_refresh(self, dashboard, repo):
        _make_snapshot(repo, total_value_usd="10000")
        s1 = dashboard.get_portfolio_summary()
        dashboard.refresh_metrics()
        s2 = dashboard.get_portfolio_summary()
        assert s1 is not s2


# ---------------------------------------------------------------------------
# Sharpe Ratio
# ---------------------------------------------------------------------------

class TestSharpeRatio:
    def test_insufficient_data_returns_zero(self, dashboard):
        ratio = dashboard.get_sharpe_ratio("7d")
        assert ratio == Decimal("0")

    def test_single_snapshot_returns_zero(self, dashboard, repo):
        _make_snapshot(repo)
        ratio = dashboard.get_sharpe_ratio("7d")
        assert ratio == Decimal("0")

    def test_identical_values_returns_zero(self, dashboard, repo):
        now = datetime.now(UTC)
        for i in range(5):
            _make_snapshot(
                repo,
                total_value_usd="10000",
                timestamp=now - timedelta(days=5 - i),
            )
        ratio = dashboard.get_sharpe_ratio("7d")
        assert ratio == Decimal("0")

    def test_positive_returns_yield_positive_sharpe(self, dashboard, repo):
        now = datetime.now(UTC)
        for i in range(10):
            value = str(10000 + i * 100)
            _make_snapshot(
                repo,
                total_value_usd=value,
                timestamp=now - timedelta(days=10 - i),
            )
        ratio = dashboard.get_sharpe_ratio("30d")
        assert ratio > Decimal("0")

    def test_all_time_window(self, dashboard, repo):
        now = datetime.now(UTC)
        for i in range(5):
            value = str(10000 + i * 50)
            _make_snapshot(
                repo,
                total_value_usd=value,
                timestamp=now - timedelta(days=100 - i),
            )
        ratio = dashboard.get_sharpe_ratio("all")
        assert isinstance(ratio, Decimal)

    def test_7d_window(self, dashboard, repo):
        now = datetime.now(UTC)
        for i in range(8):
            _make_snapshot(
                repo,
                total_value_usd=str(10000 + i * 100),
                timestamp=now - timedelta(days=7 - i),
            )
        ratio = dashboard.get_sharpe_ratio("7d")
        assert isinstance(ratio, Decimal)


# ---------------------------------------------------------------------------
# Strategy Attribution
# ---------------------------------------------------------------------------

class TestStrategyAttribution:
    def test_no_trades_returns_empty(self, dashboard):
        attrs = dashboard.get_strategy_attribution()
        assert attrs == []

    def test_single_strategy_attribution(self, dashboard, repo):
        _make_trade(repo, strategy="aave_lending", amount_in="1000", amount_out="1100")
        attrs = dashboard.get_strategy_attribution()
        assert len(attrs) == 1
        assert attrs[0].strategy == "aave_lending"
        assert attrs[0].pnl_usd == Decimal("100")
        assert attrs[0].trade_count == 1

    def test_multiple_strategies(self, dashboard, repo):
        _make_trade(
            repo, strategy="aave_lending",
            amount_in="1000", amount_out="1100",
            trade_id="t1",
        )
        _make_trade(
            repo, strategy="uniswap_lp",
            protocol="uniswap_v3",
            amount_in="2000", amount_out="2300",
            trade_id="t2",
        )
        attrs = dashboard.get_strategy_attribution()
        assert len(attrs) == 2
        strategies = {a.strategy for a in attrs}
        assert strategies == {"aave_lending", "uniswap_lp"}

    def test_gas_cost_included_in_attribution(self, dashboard, repo):
        _make_trade(
            repo,
            gas_used=200000,
            gas_price_wei=50000000000,
        )
        attrs = dashboard.get_strategy_attribution()
        assert len(attrs) == 1
        assert attrs[0].gas_cost_usd > Decimal("0")

    def test_contribution_pct_sums_to_100(self, dashboard, repo):
        _make_trade(
            repo, strategy="s1", amount_in="1000",
            amount_out="1100", trade_id="t1",
        )
        _make_trade(
            repo, strategy="s2", amount_in="2000",
            amount_out="2200", trade_id="t2",
        )
        attrs = dashboard.get_strategy_attribution()
        total_contrib = sum(a.contribution_pct for a in attrs)
        assert abs(total_contrib - Decimal("100")) < Decimal("1")

    def test_attribution_is_cached(self, dashboard, repo):
        _make_trade(repo)
        a1 = dashboard.get_strategy_attribution()
        a2 = dashboard.get_strategy_attribution()
        assert a1 is a2


# ---------------------------------------------------------------------------
# Gas Summary
# ---------------------------------------------------------------------------

class TestGasSummary:
    def test_no_trades_returns_zero(self, dashboard):
        gas = dashboard.get_gas_summary()
        assert isinstance(gas, GasSummary)
        assert gas.total_gas_cost_usd == Decimal("0")
        assert gas.gas_per_strategy == {}
        assert gas.gas_as_pct_of_returns == Decimal("0")

    def test_gas_cost_calculation(self, dashboard, repo):
        # gas_used=100000, gas_price_wei=20000000000
        # gas_eth = 100000 * 20e9 / 1e18 = 0.002 ETH
        # gas_usd = 0.002 * 2000 = $4.00
        _make_trade(
            repo,
            gas_used=100000,
            gas_price_wei=20000000000,
        )
        gas = dashboard.get_gas_summary()
        assert gas.total_gas_cost_usd == Decimal("4")

    def test_gas_per_strategy(self, dashboard, repo):
        _make_trade(
            repo, strategy="s1",
            gas_used=100000, gas_price_wei=20000000000,
            trade_id="t1",
        )
        _make_trade(
            repo, strategy="s2",
            gas_used=200000, gas_price_wei=20000000000,
            trade_id="t2",
        )
        gas = dashboard.get_gas_summary()
        assert "s1" in gas.gas_per_strategy
        assert "s2" in gas.gas_per_strategy
        assert gas.gas_per_strategy["s2"] > gas.gas_per_strategy["s1"]

    def test_gas_as_pct_of_positive_returns(self, dashboard, repo):
        _make_trade(
            repo,
            amount_in="1000", amount_out="1100",
            gas_used=100000, gas_price_wei=20000000000,
        )
        gas = dashboard.get_gas_summary()
        assert gas.gas_as_pct_of_returns > Decimal("0")

    def test_gas_no_returns_pct_zero(self, dashboard, repo):
        _make_trade(
            repo,
            amount_in="1000", amount_out="1000",
            gas_used=100000, gas_price_wei=20000000000,
        )
        gas = dashboard.get_gas_summary()
        # Returns are zero, so gas_pct stays 0
        assert gas.gas_as_pct_of_returns == Decimal("0")

    def test_trades_without_gas_data(self, dashboard, repo):
        _make_trade(repo, gas_used=None, gas_price_wei=None)
        gas = dashboard.get_gas_summary()
        assert gas.total_gas_cost_usd == Decimal("0")


# ---------------------------------------------------------------------------
# Drawdown Info
# ---------------------------------------------------------------------------

class TestDrawdownInfo:
    def test_no_snapshots_returns_zero(self, dashboard):
        dd = dashboard.get_drawdown_info()
        assert isinstance(dd, DrawdownInfo)
        assert dd.current_drawdown_pct == Decimal("0")
        assert dd.worst_drawdown_pct == Decimal("0")
        assert dd.worst_drawdown_timestamp is None

    def test_current_drawdown_from_snapshot(self, dashboard, repo):
        _make_snapshot(repo, drawdown_from_peak="0.10", peak_value_usd="11000")
        dd = dashboard.get_drawdown_info()
        assert dd.current_drawdown_pct == Decimal("0.10")
        assert dd.peak_value_usd == Decimal("11000")

    def test_worst_drawdown_found(self, dashboard, repo):
        now = datetime.now(UTC)
        _make_snapshot(
            repo,
            drawdown_from_peak="0.05",
            peak_value_usd="10500",
            timestamp=now - timedelta(days=3),
        )
        _make_snapshot(
            repo,
            drawdown_from_peak="0.15",
            peak_value_usd="11000",
            timestamp=now - timedelta(days=2),
        )
        _make_snapshot(
            repo,
            drawdown_from_peak="0.08",
            peak_value_usd="11000",
            timestamp=now - timedelta(days=1),
        )
        dd = dashboard.get_drawdown_info()
        assert dd.worst_drawdown_pct == Decimal("0.15")
        assert dd.worst_drawdown_timestamp is not None

    def test_drawdown_is_cached(self, dashboard, repo):
        _make_snapshot(repo)
        d1 = dashboard.get_drawdown_info()
        d2 = dashboard.get_drawdown_info()
        assert d1 is d2


# ---------------------------------------------------------------------------
# Refresh Metrics
# ---------------------------------------------------------------------------

class TestRefreshMetrics:
    def test_refresh_clears_all_caches(self, dashboard, repo):
        _make_snapshot(repo, total_value_usd="10000")
        _make_trade(repo)

        s1 = dashboard.get_portfolio_summary()
        a1 = dashboard.get_strategy_attribution()
        g1 = dashboard.get_gas_summary()
        d1 = dashboard.get_drawdown_info()

        dashboard.refresh_metrics()

        s2 = dashboard.get_portfolio_summary()
        a2 = dashboard.get_strategy_attribution()
        g2 = dashboard.get_gas_summary()
        d2 = dashboard.get_drawdown_info()

        assert s1 is not s2
        assert a1 is not a2
        assert g1 is not g2
        assert d1 is not d2

    def test_refresh_persists_strategy_performance(self, dashboard, repo):
        _make_trade(repo, strategy="aave_lending")

        dashboard.refresh_metrics()

        perfs = repo.get_strategy_performance("aave_lending")
        assert len(perfs) >= 1
        assert perfs[0].strategy == "aave_lending"
        assert perfs[0].period == "snapshot"

    def test_refresh_with_empty_database(self, dashboard):
        # Should not raise
        dashboard.refresh_metrics()


# ---------------------------------------------------------------------------
# Data Stored in PostgreSQL (via SQLAlchemy / SQLite in test)
# ---------------------------------------------------------------------------

class TestDatabaseStorage:
    def test_strategy_performance_queryable(self, dashboard, repo):
        _make_trade(repo, strategy="lending_opt", trade_id="t1")
        _make_trade(repo, strategy="lending_opt", trade_id="t2")
        dashboard.refresh_metrics()

        perfs = repo.get_strategy_performance("lending_opt")
        assert len(perfs) >= 1
        perf = perfs[0]
        assert perf.trade_count == 2

    def test_snapshots_queryable_for_frontend(self, repo):
        now = datetime.now(UTC)
        for i in range(5):
            _make_snapshot(
                repo,
                total_value_usd=str(10000 + i * 100),
                timestamp=now - timedelta(hours=5 - i),
            )
        snapshots = repo.get_snapshots(limit=10)
        assert len(snapshots) == 5


# ---------------------------------------------------------------------------
# APY Calculation edge cases
# ---------------------------------------------------------------------------

class TestAPYCalculation:
    def test_apy_with_loss(self, dashboard, repo):
        now = datetime.now(UTC)
        _make_snapshot(
            repo,
            total_value_usd="10000",
            timestamp=now - timedelta(days=30),
        )
        _make_snapshot(
            repo,
            total_value_usd="9000",
            timestamp=now,
        )
        summary = dashboard.get_portfolio_summary()
        assert summary.annualized_return_pct < 0

    def test_apy_with_zero_initial_value(self, dashboard, repo):
        now = datetime.now(UTC)
        _make_snapshot(
            repo,
            total_value_usd="0",
            timestamp=now - timedelta(days=30),
        )
        _make_snapshot(
            repo,
            total_value_usd="1000",
            timestamp=now,
        )
        summary = dashboard.get_portfolio_summary()
        # Should handle gracefully, not crash
        assert isinstance(summary.annualized_return_pct, Decimal)
