"""Tests for the P&L attribution engine (REPORT-002)."""

from __future__ import annotations

import csv
import io
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from db.database import DatabaseConfig, DatabaseManager
from db.repository import DatabaseRepository
from reporting.pnl_attribution import (
    ChainPnL,
    PeriodPnL,
    PnLAttributionEngine,
    ProtocolPnL,
    StrategyPnL,
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
def pnl_engine(repo):
    return PnLAttributionEngine(repo, eth_price_usd=Decimal("2000"))


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
        "tx_hash": "0xabc",
    }
    data.update(overrides)
    return repo.record_trade(data)


# ---------------------------------------------------------------------------
# Attribution by Strategy
# ---------------------------------------------------------------------------

class TestAttributionByStrategy:
    def test_no_trades_returns_empty(self, pnl_engine):
        result = pnl_engine.get_attribution_by_strategy()
        assert result == []

    def test_single_strategy(self, pnl_engine, repo):
        _make_trade(repo, strategy="aave_lending", amount_in="1000", amount_out="1100")
        result = pnl_engine.get_attribution_by_strategy()
        assert len(result) == 1
        assert isinstance(result[0], StrategyPnL)
        assert result[0].strategy == "aave_lending"
        assert result[0].pnl_usd == Decimal("100")

    def test_multiple_strategies(self, pnl_engine, repo):
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
        result = pnl_engine.get_attribution_by_strategy()
        assert len(result) == 2
        strategies = {r.strategy for r in result}
        assert strategies == {"aave_lending", "uniswap_lp"}

    def test_strategy_gas_tracking(self, pnl_engine, repo):
        _make_trade(
            repo, strategy="s1",
            gas_used=200000, gas_price_wei=50000000000,
            trade_id="t1",
        )
        result = pnl_engine.get_attribution_by_strategy()
        # gas = 200000 * 50e9 / 1e18 * 2000 = $20
        assert result[0].gas_cost_usd == Decimal("20")

    def test_net_pnl_includes_gas(self, pnl_engine, repo):
        _make_trade(
            repo,
            amount_in="1000", amount_out="1100",
            gas_used=100000, gas_price_wei=20000000000,
        )
        result = pnl_engine.get_attribution_by_strategy()
        expected_pnl = Decimal("100")
        expected_gas = Decimal("4")
        assert result[0].pnl_usd == expected_pnl
        assert result[0].net_pnl_usd == expected_pnl - expected_gas

    def test_contribution_pct(self, pnl_engine, repo):
        _make_trade(
            repo, strategy="s1",
            amount_in="1000", amount_out="1200",
            trade_id="t1",
        )
        _make_trade(
            repo, strategy="s2",
            amount_in="1000", amount_out="1300",
            trade_id="t2",
        )
        result = pnl_engine.get_attribution_by_strategy()
        total = sum(r.contribution_pct for r in result)
        assert abs(total - Decimal("100")) < Decimal("1")

    def test_time_filtering(self, pnl_engine, repo):
        now = datetime.now(UTC)
        _make_trade(
            repo,
            timestamp=now - timedelta(days=10),
            trade_id="t1",
        )
        _make_trade(
            repo,
            timestamp=now - timedelta(days=2),
            trade_id="t2",
        )
        result = pnl_engine.get_attribution_by_strategy(
            since=now - timedelta(days=5),
        )
        assert len(result) == 1
        assert result[0].trade_count == 1


# ---------------------------------------------------------------------------
# Attribution by Protocol
# ---------------------------------------------------------------------------

class TestAttributionByProtocol:
    def test_no_trades_returns_empty(self, pnl_engine):
        result = pnl_engine.get_attribution_by_protocol()
        assert result == []

    def test_single_protocol(self, pnl_engine, repo):
        _make_trade(repo, protocol="aave_v3", amount_in="1000", amount_out="1100")
        result = pnl_engine.get_attribution_by_protocol()
        assert len(result) == 1
        assert isinstance(result[0], ProtocolPnL)
        assert result[0].protocol == "aave_v3"
        assert result[0].pnl_usd == Decimal("100")

    def test_multiple_protocols(self, pnl_engine, repo):
        _make_trade(
            repo, protocol="aave_v3",
            amount_in="1000", amount_out="1100",
            trade_id="t1",
        )
        _make_trade(
            repo, protocol="uniswap_v3",
            amount_in="2000", amount_out="2200",
            trade_id="t2",
        )
        _make_trade(
            repo, protocol="lido",
            amount_in="500", amount_out="550",
            trade_id="t3",
        )
        result = pnl_engine.get_attribution_by_protocol()
        assert len(result) == 3
        protocols = {r.protocol for r in result}
        assert protocols == {"aave_v3", "uniswap_v3", "lido"}

    def test_protocol_gas_cost(self, pnl_engine, repo):
        _make_trade(
            repo, protocol="aave_v3",
            gas_used=150000, gas_price_wei=30000000000,
        )
        result = pnl_engine.get_attribution_by_protocol()
        # gas = 150000 * 30e9 / 1e18 * 2000 = $9
        assert result[0].gas_cost_usd == Decimal("9")


# ---------------------------------------------------------------------------
# Attribution by Chain
# ---------------------------------------------------------------------------

class TestAttributionByChain:
    def test_no_trades_returns_empty(self, pnl_engine):
        result = pnl_engine.get_attribution_by_chain()
        assert result == []

    def test_single_chain(self, pnl_engine, repo):
        _make_trade(repo, chain="ethereum", amount_in="1000", amount_out="1100")
        result = pnl_engine.get_attribution_by_chain()
        assert len(result) == 1
        assert isinstance(result[0], ChainPnL)
        assert result[0].chain == "ethereum"

    def test_multiple_chains(self, pnl_engine, repo):
        _make_trade(
            repo, chain="ethereum",
            amount_in="1000", amount_out="1100",
            trade_id="t1",
        )
        _make_trade(
            repo, chain="arbitrum",
            amount_in="2000", amount_out="2200",
            trade_id="t2",
        )
        _make_trade(
            repo, chain="base",
            amount_in="500", amount_out="520",
            trade_id="t3",
        )
        result = pnl_engine.get_attribution_by_chain()
        assert len(result) == 3
        chains = {r.chain for r in result}
        assert chains == {"ethereum", "arbitrum", "base"}

    def test_chain_contribution_pct(self, pnl_engine, repo):
        _make_trade(
            repo, chain="ethereum",
            amount_in="1000", amount_out="1200",
            trade_id="t1",
        )
        _make_trade(
            repo, chain="arbitrum",
            amount_in="1000", amount_out="1300",
            trade_id="t2",
        )
        result = pnl_engine.get_attribution_by_chain()
        total = sum(r.contribution_pct for r in result)
        assert abs(total - Decimal("100")) < Decimal("1")


# ---------------------------------------------------------------------------
# Time Series
# ---------------------------------------------------------------------------

class TestTimeSeries:
    def test_daily_breakdown(self, pnl_engine, repo):
        now = datetime.now(UTC)
        _make_trade(
            repo,
            timestamp=now - timedelta(days=2),
            trade_id="t1",
        )
        _make_trade(
            repo,
            timestamp=now - timedelta(days=1),
            trade_id="t2",
        )
        result = pnl_engine.get_time_series(
            period="daily",
            since=now - timedelta(days=3),
            until=now,
        )
        assert len(result) == 3
        assert all(isinstance(p, PeriodPnL) for p in result)

    def test_weekly_breakdown(self, pnl_engine, repo):
        now = datetime.now(UTC)
        _make_trade(
            repo,
            timestamp=now - timedelta(days=10),
            trade_id="t1",
        )
        result = pnl_engine.get_time_series(
            period="weekly",
            since=now - timedelta(days=21),
            until=now,
        )
        assert len(result) == 3

    def test_monthly_breakdown(self, pnl_engine, repo):
        now = datetime.now(UTC)
        _make_trade(
            repo,
            timestamp=now - timedelta(days=45),
            trade_id="t1",
        )
        result = pnl_engine.get_time_series(
            period="monthly",
            since=now - timedelta(days=90),
            until=now,
        )
        assert len(result) == 3

    def test_trades_assigned_to_correct_period(self, pnl_engine, repo):
        now = datetime.now(UTC)
        _make_trade(
            repo,
            timestamp=now - timedelta(days=1, hours=12),
            amount_in="1000",
            amount_out="1100",
            trade_id="t1",
        )
        result = pnl_engine.get_time_series(
            period="daily",
            since=now - timedelta(days=3),
            until=now,
        )
        # Find the period that has trades
        periods_with_trades = [p for p in result if p.trade_count > 0]
        assert len(periods_with_trades) == 1
        assert periods_with_trades[0].pnl_usd == Decimal("100")

    def test_gas_costs_in_time_series(self, pnl_engine, repo):
        now = datetime.now(UTC)
        _make_trade(
            repo,
            timestamp=now - timedelta(hours=12),
            gas_used=100000,
            gas_price_wei=20000000000,
            trade_id="t1",
        )
        result = pnl_engine.get_time_series(
            period="daily",
            since=now - timedelta(days=1),
            until=now + timedelta(hours=1),
        )
        periods_with_trades = [p for p in result if p.trade_count > 0]
        assert len(periods_with_trades) >= 1
        assert periods_with_trades[0].gas_cost_usd == Decimal("4")

    def test_default_time_range(self, pnl_engine, repo):
        now = datetime.now(UTC)
        _make_trade(
            repo,
            timestamp=now - timedelta(days=5),
            trade_id="t1",
        )
        result = pnl_engine.get_time_series(period="daily")
        assert len(result) == 30  # default 30 days


# ---------------------------------------------------------------------------
# CSV Export
# ---------------------------------------------------------------------------

class TestCSVExport:
    def test_strategy_csv_export(self, pnl_engine, repo):
        _make_trade(repo, strategy="aave_lending")
        data = pnl_engine.get_attribution_by_strategy()
        csv_content = pnl_engine.export_csv(data)
        reader = csv.reader(io.StringIO(csv_content))
        headers = next(reader)
        assert "strategy" in headers
        assert "pnl_usd" in headers
        assert "gas_cost_usd" in headers
        rows = list(reader)
        assert len(rows) == 1

    def test_protocol_csv_export(self, pnl_engine, repo):
        _make_trade(repo, protocol="aave_v3")
        data = pnl_engine.get_attribution_by_protocol()
        csv_content = pnl_engine.export_csv(data)
        assert "protocol" in csv_content
        assert "aave_v3" in csv_content

    def test_chain_csv_export(self, pnl_engine, repo):
        _make_trade(repo, chain="ethereum")
        data = pnl_engine.get_attribution_by_chain()
        csv_content = pnl_engine.export_csv(data)
        assert "chain" in csv_content
        assert "ethereum" in csv_content

    def test_time_series_csv_export(self, pnl_engine, repo):
        now = datetime.now(UTC)
        _make_trade(repo, timestamp=now - timedelta(hours=1), trade_id="t1")
        data = pnl_engine.get_time_series(
            period="daily",
            since=now - timedelta(days=1),
            until=now + timedelta(hours=1),
        )
        csv_content = pnl_engine.export_csv(data)
        assert "period_label" in csv_content

    def test_csv_write_to_file(self, pnl_engine, repo, tmp_path):
        _make_trade(repo)
        data = pnl_engine.get_attribution_by_strategy()
        output_file = str(tmp_path / "pnl_report.csv")
        csv_content = pnl_engine.export_csv(data, output_path=output_file)
        with open(output_file) as f:
            assert f.read() == csv_content

    def test_empty_data_returns_empty_string(self, pnl_engine):
        csv_content = pnl_engine.export_csv([])
        assert csv_content == ""


# ---------------------------------------------------------------------------
# JSON Export
# ---------------------------------------------------------------------------

class TestJSONExport:
    def test_strategy_json_export(self, pnl_engine, repo):
        _make_trade(repo, strategy="aave_lending")
        data = pnl_engine.get_attribution_by_strategy()
        json_str = pnl_engine.export_json(data)
        parsed = json.loads(json_str)
        assert len(parsed) == 1
        assert parsed[0]["strategy"] == "aave_lending"

    def test_json_decimal_serialized_as_string(self, pnl_engine, repo):
        _make_trade(repo, amount_in="1000", amount_out="1100")
        data = pnl_engine.get_attribution_by_strategy()
        json_str = pnl_engine.export_json(data)
        parsed = json.loads(json_str)
        # Decimals should be strings, not floats
        assert isinstance(parsed[0]["pnl_usd"], str)

    def test_empty_data_returns_empty_array(self, pnl_engine):
        json_str = pnl_engine.export_json([])
        assert json_str == "[]"

    def test_time_series_json_export(self, pnl_engine, repo):
        now = datetime.now(UTC)
        _make_trade(repo, timestamp=now - timedelta(hours=1), trade_id="t1")
        data = pnl_engine.get_time_series(
            period="daily",
            since=now - timedelta(days=1),
            until=now + timedelta(hours=1),
        )
        json_str = pnl_engine.export_json(data)
        parsed = json.loads(json_str)
        assert len(parsed) >= 1
        assert "period_label" in parsed[0]

    def test_multiple_protocols_json(self, pnl_engine, repo):
        _make_trade(repo, protocol="aave_v3", trade_id="t1")
        _make_trade(repo, protocol="uniswap_v3", trade_id="t2")
        _make_trade(repo, protocol="lido", trade_id="t3")
        data = pnl_engine.get_attribution_by_protocol()
        json_str = pnl_engine.export_json(data)
        parsed = json.loads(json_str)
        assert len(parsed) == 3
        protocols = {p["protocol"] for p in parsed}
        assert protocols == {"aave_v3", "uniswap_v3", "lido"}


# ---------------------------------------------------------------------------
# Edge Cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_trades_with_no_gas_data(self, pnl_engine, repo):
        _make_trade(repo, gas_used=None, gas_price_wei=None)
        result = pnl_engine.get_attribution_by_strategy()
        assert len(result) == 1
        assert result[0].gas_cost_usd == Decimal("0")

    def test_zero_pnl_contribution(self, pnl_engine, repo):
        _make_trade(
            repo, strategy="s1",
            amount_in="1000", amount_out="1000",
            trade_id="t1",
        )
        result = pnl_engine.get_attribution_by_strategy()
        assert len(result) == 1
        assert result[0].pnl_usd == Decimal("0")

    def test_until_filter(self, pnl_engine, repo):
        now = datetime.now(UTC)
        _make_trade(
            repo,
            timestamp=now - timedelta(days=5),
            trade_id="t1",
        )
        _make_trade(
            repo,
            timestamp=now - timedelta(days=1),
            trade_id="t2",
        )
        result = pnl_engine.get_attribution_by_strategy(
            until=now - timedelta(days=3),
        )
        assert len(result) == 1
        assert result[0].trade_count == 1
