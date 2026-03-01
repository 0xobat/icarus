"""Tests for the tax reporting engine (REPORT-001)."""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from db.database import DatabaseConfig, DatabaseManager
from db.repository import DatabaseRepository
from reporting.tax_engine import (
    TaxReportEngine,
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
def engine(repo):
    return TaxReportEngine(repo, eth_price_usd=Decimal("2000"))


def _make_trade(repo, **overrides):
    data = {
        "strategy": "aave_lending",
        "protocol": "aave_v3",
        "chain": "ethereum",
        "action": "buy",
        "asset_in": "ETH",
        "amount_in": "1.0",
        "amount_out": None,
        "price_at_execution": "2000",
        "status": "confirmed",
        "gas_used": 100000,
        "gas_price_wei": 20000000000,
        "tx_hash": "0xabc123",
    }
    data.update(overrides)
    return repo.record_trade(data)


# ---------------------------------------------------------------------------
# Cost Basis Tracking
# ---------------------------------------------------------------------------

class TestCostBasisTracking:
    def test_acquisition_updates_cost_base(self, engine, repo):
        _make_trade(
            repo,
            action="buy",
            asset_in="ETH",
            amount_in="2.0",
            price_at_execution="2000",
        )
        engine.process_trades()
        cb = engine.get_cost_base("ETH")
        assert cb is not None
        assert cb.total_quantity == Decimal("2.0")
        # Cost = amount * price + gas
        # gas = 100000 * 20e9 / 1e18 * 2000 = $4
        assert cb.total_cost == Decimal("2.0") * Decimal("2000") + Decimal("4")

    def test_gas_included_in_cost_basis(self, engine, repo):
        _make_trade(
            repo,
            action="buy",
            asset_in="ETH",
            amount_in="1.0",
            price_at_execution="2000",
            gas_used=200000,
            gas_price_wei=50000000000,
        )
        engine.process_trades()
        cb = engine.get_cost_base("ETH")
        # gas = 200000 * 50e9 / 1e18 * 2000 = $20
        expected_cost = Decimal("2000") + Decimal("20")
        assert cb is not None
        assert cb.total_cost == expected_cost

    def test_multiple_acquisitions_average_cost(self, engine, repo):
        _make_trade(
            repo,
            action="buy",
            asset_in="ETH",
            amount_in="1.0",
            price_at_execution="2000",
            trade_id="t1",
            gas_used=0,
            gas_price_wei=0,
            timestamp=datetime(2025, 1, 1, tzinfo=UTC),
        )
        _make_trade(
            repo,
            action="buy",
            asset_in="ETH",
            amount_in="1.0",
            price_at_execution="3000",
            trade_id="t2",
            gas_used=0,
            gas_price_wei=0,
            timestamp=datetime(2025, 1, 2, tzinfo=UTC),
        )
        engine.process_trades()
        cb = engine.get_cost_base("ETH")
        assert cb is not None
        assert cb.total_quantity == Decimal("2.0")
        assert cb.total_cost == Decimal("5000")
        assert cb.acb_per_unit == Decimal("2500")

    def test_no_cost_base_for_untracked_asset(self, engine):
        assert engine.get_cost_base("UNKNOWN") is None


# ---------------------------------------------------------------------------
# Realized Gain/Loss
# ---------------------------------------------------------------------------

class TestRealizedGainLoss:
    def test_disposal_calculates_gain(self, engine, repo):
        # Buy 2 ETH at $2000 each (no gas)
        _make_trade(
            repo,
            action="buy",
            asset_in="ETH",
            amount_in="2.0",
            price_at_execution="2000",
            trade_id="t1",
            gas_used=0,
            gas_price_wei=0,
            timestamp=datetime(2025, 1, 1, tzinfo=UTC),
        )
        # Sell 1 ETH at $2500 (no gas)
        _make_trade(
            repo,
            action="sell",
            asset_in="ETH",
            amount_in="1.0",
            amount_out="1.0",
            price_at_execution="2500",
            trade_id="t2",
            gas_used=0,
            gas_price_wei=0,
            timestamp=datetime(2025, 1, 10, tzinfo=UTC),
        )
        report = engine.process_trades()
        disposals = [e for e in report.events if e.event_type == "disposal"]
        assert len(disposals) == 1
        # Proceeds = 1.0 * 2500 = 2500, cost = ACB * 1.0 = 2000
        assert disposals[0].gain_loss_usd == Decimal("500")

    def test_disposal_calculates_loss(self, engine, repo):
        # Buy 1 ETH at $3000
        _make_trade(
            repo,
            action="buy",
            asset_in="ETH",
            amount_in="1.0",
            price_at_execution="3000",
            trade_id="t1",
            gas_used=0,
            gas_price_wei=0,
            timestamp=datetime(2025, 1, 1, tzinfo=UTC),
        )
        # Sell 1 ETH at $2000
        _make_trade(
            repo,
            action="sell",
            asset_in="ETH",
            amount_in="1.0",
            amount_out="1.0",
            price_at_execution="2000",
            trade_id="t2",
            gas_used=0,
            gas_price_wei=0,
            timestamp=datetime(2025, 1, 10, tzinfo=UTC),
        )
        report = engine.process_trades()
        disposals = [e for e in report.events if e.event_type == "disposal"]
        assert len(disposals) == 1
        assert disposals[0].gain_loss_usd == Decimal("-1000")

    def test_gas_subtracted_from_proceeds_on_disposal(self, engine, repo):
        _make_trade(
            repo,
            action="buy",
            asset_in="ETH",
            amount_in="1.0",
            price_at_execution="2000",
            trade_id="t1",
            gas_used=0,
            gas_price_wei=0,
            timestamp=datetime(2025, 1, 1, tzinfo=UTC),
        )
        # Sell with gas: proceeds = 2500, gas = 100000*20e9/1e18*2000 = $4
        _make_trade(
            repo,
            action="sell",
            asset_in="ETH",
            amount_in="1.0",
            amount_out="1.0",
            price_at_execution="2500",
            trade_id="t2",
            gas_used=100000,
            gas_price_wei=20000000000,
            timestamp=datetime(2025, 1, 10, tzinfo=UTC),
        )
        report = engine.process_trades()
        disposals = [e for e in report.events if e.event_type == "disposal"]
        assert len(disposals) == 1
        # Proceeds = 2500 - $4 gas = 2496, cost = 2000, gain = 496
        assert disposals[0].gain_loss_usd == Decimal("496")


# ---------------------------------------------------------------------------
# ACB Method
# ---------------------------------------------------------------------------

class TestACBMethod:
    def test_acb_calculated_correctly(self, engine, repo):
        # Buy 10 ETH at $2000, then 10 ETH at $3000
        _make_trade(
            repo,
            action="buy",
            asset_in="ETH",
            amount_in="10",
            price_at_execution="2000",
            trade_id="t1",
            gas_used=0,
            gas_price_wei=0,
            timestamp=datetime(2025, 1, 1, tzinfo=UTC),
        )
        _make_trade(
            repo,
            action="buy",
            asset_in="ETH",
            amount_in="10",
            price_at_execution="3000",
            trade_id="t2",
            gas_used=0,
            gas_price_wei=0,
            timestamp=datetime(2025, 1, 2, tzinfo=UTC),
        )
        # Sell 5 ETH at $3500
        _make_trade(
            repo,
            action="sell",
            asset_in="ETH",
            amount_in="5",
            amount_out="5",
            price_at_execution="3500",
            trade_id="t3",
            gas_used=0,
            gas_price_wei=0,
            timestamp=datetime(2025, 1, 3, tzinfo=UTC),
        )
        report = engine.process_trades()
        disposals = [e for e in report.events if e.event_type == "disposal"]
        assert len(disposals) == 1
        # ACB = (20000 + 30000) / 20 = 2500/unit
        # Cost basis for 5 = 12500, Proceeds = 5 * 3500 = 17500
        # Gain = 17500 - 12500 = 5000
        assert disposals[0].cost_basis_usd == Decimal("12500")
        assert disposals[0].gain_loss_usd == Decimal("5000")

    def test_acb_updates_after_disposal(self, engine, repo):
        _make_trade(
            repo,
            action="buy",
            asset_in="ETH",
            amount_in="10",
            price_at_execution="2000",
            trade_id="t1",
            gas_used=0,
            gas_price_wei=0,
            timestamp=datetime(2025, 1, 1, tzinfo=UTC),
        )
        _make_trade(
            repo,
            action="sell",
            asset_in="ETH",
            amount_in="5",
            amount_out="5",
            price_at_execution="3000",
            trade_id="t2",
            gas_used=0,
            gas_price_wei=0,
            timestamp=datetime(2025, 1, 2, tzinfo=UTC),
        )
        engine.process_trades()
        cb = engine.get_cost_base("ETH")
        assert cb is not None
        assert cb.total_quantity == Decimal("5")
        assert cb.total_cost == Decimal("10000")  # 5 remaining at $2000 each


# ---------------------------------------------------------------------------
# CSV Export
# ---------------------------------------------------------------------------

class TestCSVExport:
    def test_csv_has_correct_headers(self, engine, repo):
        _make_trade(repo)
        csv_content = engine.generate_csv_report(year=2025)
        reader = csv.reader(io.StringIO(csv_content))
        headers = next(reader)
        assert "date" in headers
        assert "type" in headers
        assert "asset" in headers
        assert "amount" in headers
        assert "proceeds_usd" in headers
        assert "cost_basis_usd" in headers
        assert "gain_loss_usd" in headers
        assert "tx_hash" in headers

    def test_csv_contains_trade_data(self, engine, repo):
        _make_trade(
            repo,
            action="buy",
            asset_in="ETH",
            tx_hash="0xdef456",
            timestamp=datetime(2025, 6, 15, tzinfo=UTC),
        )
        csv_content = engine.generate_csv_report(year=2025)
        assert "ETH" in csv_content
        assert "0xdef456" in csv_content

    def test_csv_contains_summary(self, engine, repo):
        _make_trade(repo, timestamp=datetime(2025, 3, 1, tzinfo=UTC))
        csv_content = engine.generate_csv_report(year=2025)
        assert "Summary" in csv_content
        assert "Total Gains" in csv_content
        assert "Total Losses" in csv_content
        assert "Net Gain/Loss" in csv_content

    def test_csv_write_to_file(self, engine, repo, tmp_path):
        _make_trade(repo, timestamp=datetime(2025, 1, 1, tzinfo=UTC))
        output_file = str(tmp_path / "tax_report.csv")
        csv_content = engine.generate_csv_report(year=2025, output_path=output_file)
        assert len(csv_content) > 0
        with open(output_file) as f:
            file_content = f.read()
        assert file_content == csv_content

    def test_csv_empty_year_returns_summary_only(self, engine, repo):
        _make_trade(repo, timestamp=datetime(2024, 6, 1, tzinfo=UTC))
        csv_content = engine.generate_csv_report(year=2025)
        # Should have headers + summary, but no data rows
        assert "Summary" in csv_content


# ---------------------------------------------------------------------------
# DeFi Tax Events
# ---------------------------------------------------------------------------

class TestDeFiTaxEvents:
    def test_swap_creates_disposal_and_acquisition(self, engine, repo):
        _make_trade(
            repo,
            action="swap",
            asset_in="ETH",
            asset_out="USDC",
            amount_in="1.0",
            amount_out="2500",
            price_at_execution="1",
            gas_used=0,
            gas_price_wei=0,
            timestamp=datetime(2025, 1, 1, tzinfo=UTC),
        )
        report = engine.process_trades()
        event_types = [e.event_type for e in report.events]
        assert "disposal" in event_types
        assert "acquisition" in event_types

    def test_staking_rewards_classified_as_income(self, engine, repo):
        _make_trade(
            repo,
            action="stake_reward",
            asset_in="ETH",
            amount_in="0.1",
            price_at_execution="2000",
            gas_used=0,
            gas_price_wei=0,
            timestamp=datetime(2025, 1, 1, tzinfo=UTC),
        )
        report = engine.process_trades()
        income_events = [e for e in report.events if e.event_type == "income"]
        assert len(income_events) == 1
        assert income_events[0].proceeds_usd == Decimal("200")
        assert report.total_income == Decimal("200")

    def test_yield_reward_classified_as_income(self, engine, repo):
        _make_trade(
            repo,
            action="yield_reward",
            asset_in="AAVE",
            amount_in="5.0",
            price_at_execution="100",
            gas_used=0,
            gas_price_wei=0,
            timestamp=datetime(2025, 2, 1, tzinfo=UTC),
        )
        report = engine.process_trades()
        income_events = [e for e in report.events if e.event_type == "income"]
        assert len(income_events) == 1
        assert income_events[0].proceeds_usd == Decimal("500")

    def test_flash_loan_fee_classified_as_expense(self, engine, repo):
        _make_trade(
            repo,
            action="flash_loan_fee",
            asset_in="USDC",
            amount_in="10",
            price_at_execution="1",
            gas_used=0,
            gas_price_wei=0,
            timestamp=datetime(2025, 3, 1, tzinfo=UTC),
        )
        report = engine.process_trades()
        expenses = [e for e in report.events if e.event_type == "expense"]
        assert len(expenses) == 1
        assert report.total_expenses == Decimal("10")

    def test_lp_entry_is_disposal(self, engine, repo):
        # First acquire the asset
        _make_trade(
            repo,
            action="buy",
            asset_in="ETH",
            amount_in="10",
            price_at_execution="2000",
            trade_id="t1",
            gas_used=0,
            gas_price_wei=0,
            timestamp=datetime(2025, 1, 1, tzinfo=UTC),
        )
        _make_trade(
            repo,
            action="supply",
            asset_in="ETH",
            amount_in="5",
            price_at_execution="2100",
            trade_id="t2",
            gas_used=0,
            gas_price_wei=0,
            timestamp=datetime(2025, 1, 2, tzinfo=UTC),
        )
        report = engine.process_trades()
        disposals = [e for e in report.events if e.event_type == "disposal"]
        assert len(disposals) >= 1

    def test_lp_exit_is_acquisition(self, engine, repo):
        _make_trade(
            repo,
            action="withdraw",
            asset_in="ETH",
            amount_in="5",
            price_at_execution="2200",
            gas_used=0,
            gas_price_wei=0,
            timestamp=datetime(2025, 1, 3, tzinfo=UTC),
        )
        report = engine.process_trades()
        acquisitions = [e for e in report.events if e.event_type == "acquisition"]
        assert len(acquisitions) >= 1

    def test_staking_reward_adds_to_cost_base(self, engine, repo):
        _make_trade(
            repo,
            action="stake_reward",
            asset_in="ETH",
            amount_in="0.5",
            price_at_execution="2000",
            gas_used=0,
            gas_price_wei=0,
            timestamp=datetime(2025, 1, 1, tzinfo=UTC),
        )
        engine.process_trades()
        cb = engine.get_cost_base("ETH")
        assert cb is not None
        assert cb.total_quantity == Decimal("0.5")
        assert cb.total_cost == Decimal("1000")  # 0.5 * 2000 FMV


# ---------------------------------------------------------------------------
# Audit Trail
# ---------------------------------------------------------------------------

class TestAuditTrail:
    def test_audit_trail_by_tx_hash(self, engine, repo):
        _make_trade(
            repo,
            action="buy",
            tx_hash="0xaaa111",
            trade_id="t1",
            timestamp=datetime(2025, 1, 1, tzinfo=UTC),
        )
        _make_trade(
            repo,
            action="sell",
            tx_hash="0xbbb222",
            trade_id="t2",
            amount_out="1.0",
            timestamp=datetime(2025, 1, 2, tzinfo=UTC),
        )
        trail = engine.get_audit_trail("0xaaa111")
        assert len(trail) == 1
        assert trail[0].tx_hash == "0xaaa111"

    def test_audit_trail_nonexistent_hash(self, engine, repo):
        _make_trade(repo, timestamp=datetime(2025, 1, 1, tzinfo=UTC))
        trail = engine.get_audit_trail("0xnonexistent")
        assert trail == []

    def test_swap_audit_trail_has_two_events(self, engine, repo):
        _make_trade(
            repo,
            action="swap",
            asset_in="ETH",
            asset_out="USDC",
            amount_in="1.0",
            amount_out="2500",
            price_at_execution="1",
            tx_hash="0xswap001",
            gas_used=0,
            gas_price_wei=0,
        )
        trail = engine.get_audit_trail("0xswap001")
        assert len(trail) == 2  # disposal + acquisition


# ---------------------------------------------------------------------------
# Tax Report Totals
# ---------------------------------------------------------------------------

class TestTaxReportTotals:
    def test_net_gain_loss_calculated(self, engine, repo):
        # Buy, then sell at profit
        _make_trade(
            repo,
            action="buy",
            asset_in="ETH",
            amount_in="10",
            price_at_execution="2000",
            trade_id="t1",
            gas_used=0,
            gas_price_wei=0,
            timestamp=datetime(2025, 1, 1, tzinfo=UTC),
        )
        _make_trade(
            repo,
            action="sell",
            asset_in="ETH",
            amount_in="10",
            amount_out="10",
            price_at_execution="2500",
            trade_id="t2",
            gas_used=0,
            gas_price_wei=0,
            timestamp=datetime(2025, 6, 1, tzinfo=UTC),
        )
        report = engine.process_trades(year=2025)
        assert report.total_gains > Decimal("0")
        assert report.net_gain_loss > Decimal("0")

    def test_year_filtering(self, engine, repo):
        _make_trade(
            repo,
            action="buy",
            trade_id="t1",
            timestamp=datetime(2024, 6, 1, tzinfo=UTC),
        )
        _make_trade(
            repo,
            action="buy",
            trade_id="t2",
            timestamp=datetime(2025, 6, 1, tzinfo=UTC),
        )
        report_2025 = engine.process_trades(year=2025)
        report_2024 = engine.process_trades(year=2024)
        # Should have different events per year
        assert all(e.date.year == 2025 for e in report_2025.events)
        assert all(e.date.year == 2024 for e in report_2024.events)
