"""Tests for INFRA-005: PostgreSQL models and repository."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime

import pytest

from db.database import DatabaseConfig, DatabaseManager
from db.models import (
    Base,
    DecisionAuditLog,
    PortfolioPosition,
    StrategyStatus,
)
from db.repository import DatabaseRepository

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


def _make_position_data(**overrides):
    data = {
        "position_id": "pos-001",
        "strategy": "LEND-001",
        "protocol": "aave_v3",
        "chain": "base",
        "asset": "USDC",
        "entry_price": "1.0",
        "amount": "1000.0",
        "current_value": "1000.0",
    }
    data.update(overrides)
    return data


def _make_decision_data(**overrides):
    data = {
        "correlation_id": "corr-001",
        "decision_action": "EXECUTE",
    }
    data.update(overrides)
    return data


# ---------------------------------------------------------------------------
# Model Tests
# ---------------------------------------------------------------------------


class TestNewModels:
    def test_portfolio_position_tablename(self):
        assert PortfolioPosition.__tablename__ == "portfolio_positions"

    def test_strategy_status_tablename(self):
        assert StrategyStatus.__tablename__ == "strategy_statuses"

    def test_decision_audit_log_tablename(self):
        assert DecisionAuditLog.__tablename__ == "decision_audit_log"

    def test_position_indices(self):
        idx_names = {idx.name for idx in PortfolioPosition.__table__.indexes}
        assert "ix_positions_strategy" in idx_names
        assert "ix_positions_status" in idx_names
        assert "ix_positions_protocol" in idx_names
        assert "ix_positions_chain" in idx_names

    def test_strategy_status_indices(self):
        idx_names = {idx.name for idx in StrategyStatus.__table__.indexes}
        assert "ix_strategy_statuses_strategy_id" in idx_names
        assert "ix_strategy_statuses_status" in idx_names

    def test_decision_audit_log_indices(self):
        idx_names = {idx.name for idx in DecisionAuditLog.__table__.indexes}
        assert "ix_decision_audit_timestamp" in idx_names
        assert "ix_decision_audit_correlation_id" in idx_names
        assert "ix_decision_audit_action" in idx_names

    def test_tables_created_by_create_all(self, db_manager):
        table_names = set(Base.metadata.tables.keys())
        assert "portfolio_positions" in table_names
        assert "strategy_statuses" in table_names
        assert "decision_audit_log" in table_names


# ---------------------------------------------------------------------------
# Position CRUD Tests
# ---------------------------------------------------------------------------


class TestPositionCRUD:
    def test_save_position_creates_new(self, repo):
        pos = repo.save_position(_make_position_data())
        assert pos.position_id == "pos-001"
        assert pos.strategy == "LEND-001"
        assert pos.protocol == "aave_v3"
        assert pos.chain == "base"
        assert pos.asset == "USDC"
        assert pos.status == "open"

    def test_save_position_with_all_fields(self, repo):
        now = datetime.now(UTC)
        pos = repo.save_position(_make_position_data(
            entry_time=now,
            unrealized_pnl="5.50",
            realized_pnl="10.0",
            status="closed",
            close_time=now,
            protocol_data={"pool": "0xabc"},
        ))
        assert float(pos.unrealized_pnl) == pytest.approx(5.50, rel=1e-2)
        assert float(pos.realized_pnl) == pytest.approx(10.0, rel=1e-2)
        assert pos.status == "closed"
        assert pos.close_time is not None
        parsed = json.loads(pos.protocol_data_json)
        assert parsed["pool"] == "0xabc"

    def test_save_position_updates_existing(self, repo):
        repo.save_position(_make_position_data())
        updated = repo.save_position(_make_position_data(
            current_value="1050.0",
            unrealized_pnl="50.0",
            amount="1000.0",
        ))
        assert float(updated.current_value) == pytest.approx(1050.0, rel=1e-2)
        assert float(updated.unrealized_pnl) == pytest.approx(50.0, rel=1e-2)

    def test_save_position_upsert_preserves_status(self, repo):
        repo.save_position(_make_position_data(status="open"))
        updated = repo.save_position(_make_position_data(
            current_value="900.0",
            amount="1000.0",
        ))
        assert updated.status == "open"

    def test_get_positions_returns_all(self, repo):
        for i in range(3):
            repo.save_position(_make_position_data(position_id=f"pos-{i}"))
        positions = repo.get_positions()
        assert len(positions) == 3

    def test_get_positions_filter_by_strategy(self, repo):
        repo.save_position(_make_position_data(position_id="p1", strategy="LEND-001"))
        repo.save_position(_make_position_data(position_id="p2", strategy="LP-001"))
        result = repo.get_positions(strategy="LEND-001")
        assert len(result) == 1
        assert result[0].strategy == "LEND-001"

    def test_get_positions_filter_by_protocol(self, repo):
        repo.save_position(_make_position_data(position_id="p1", protocol="aave_v3"))
        repo.save_position(_make_position_data(position_id="p2", protocol="aerodrome"))
        result = repo.get_positions(protocol="aerodrome")
        assert len(result) == 1

    def test_get_positions_filter_by_status(self, repo):
        repo.save_position(_make_position_data(position_id="p1", status="open"))
        repo.save_position(_make_position_data(position_id="p2", status="closed"))
        open_pos = repo.get_positions(status="open")
        assert len(open_pos) == 1
        assert open_pos[0].position_id == "p1"

    def test_get_positions_respects_limit(self, repo):
        for i in range(10):
            repo.save_position(_make_position_data(position_id=f"pos-{i}"))
        positions = repo.get_positions(limit=3)
        assert len(positions) == 3

    def test_get_position_by_id(self, repo):
        repo.save_position(_make_position_data(position_id="find-me"))
        pos = repo.get_position("find-me")
        assert pos is not None
        assert pos.position_id == "find-me"

    def test_get_position_not_found(self, repo):
        assert repo.get_position("nonexistent") is None

    def test_position_unique_id_enforced(self, repo):
        repo.save_position(_make_position_data(position_id="unique-1"))
        # Second save with same ID should update, not fail
        updated = repo.save_position(_make_position_data(
            position_id="unique-1",
            current_value="2000.0",
            amount="1000.0",
        ))
        assert float(updated.current_value) == pytest.approx(2000.0, rel=1e-2)
        assert len(repo.get_positions()) == 1

    def test_position_decimal_precision(self, repo):
        pos = repo.save_position(_make_position_data(
            entry_price="0.000000000000000001",
            amount="1000000.123456789012345678",
            current_value="0.000000000000000001",
        ))
        assert pos.entry_price is not None
        assert pos.amount is not None

    def test_position_close_flow(self, repo):
        repo.save_position(_make_position_data(position_id="close-me"))
        now = datetime.now(UTC)
        closed = repo.save_position(_make_position_data(
            position_id="close-me",
            status="closed",
            close_time=now,
            realized_pnl="15.0",
            unrealized_pnl="0",
            current_value="1015.0",
            amount="1000.0",
        ))
        assert closed.status == "closed"
        assert closed.close_time is not None
        assert float(closed.realized_pnl) == pytest.approx(15.0, rel=1e-2)


# ---------------------------------------------------------------------------
# Strategy Status CRUD Tests
# ---------------------------------------------------------------------------


class TestStrategyStatusCRUD:
    def test_save_strategy_status_creates_new(self, repo):
        ss = repo.save_strategy_status("LEND-001", "active")
        assert ss.strategy_id == "LEND-001"
        assert ss.status == "active"
        assert ss.updated_at is not None

    def test_save_strategy_status_updates_existing(self, repo):
        repo.save_strategy_status("LEND-001", "active")
        updated = repo.save_strategy_status("LEND-001", "inactive")
        assert updated.status == "inactive"

    def test_get_strategy_statuses(self, repo):
        repo.save_strategy_status("LEND-001", "active")
        repo.save_strategy_status("LP-001", "inactive")
        statuses = repo.get_strategy_statuses()
        assert len(statuses) == 2
        ids = {s.strategy_id for s in statuses}
        assert ids == {"LEND-001", "LP-001"}

    def test_get_strategy_statuses_ordered_by_id(self, repo):
        repo.save_strategy_status("LP-001")
        repo.save_strategy_status("LEND-001")
        statuses = repo.get_strategy_statuses()
        assert statuses[0].strategy_id == "LEND-001"
        assert statuses[1].strategy_id == "LP-001"

    def test_get_strategy_status_by_id(self, repo):
        repo.save_strategy_status("LEND-001", "active")
        ss = repo.get_strategy_status("LEND-001")
        assert ss is not None
        assert ss.status == "active"

    def test_get_strategy_status_not_found(self, repo):
        assert repo.get_strategy_status("nonexistent") is None

    def test_strategy_status_default_active(self, repo):
        ss = repo.save_strategy_status("LEND-001")
        assert ss.status == "active"

    def test_strategy_status_toggle(self, repo):
        repo.save_strategy_status("LEND-001", "active")
        repo.save_strategy_status("LEND-001", "inactive")
        repo.save_strategy_status("LEND-001", "active")
        ss = repo.get_strategy_status("LEND-001")
        assert ss.status == "active"
        # Should still be a single row
        all_statuses = repo.get_strategy_statuses()
        assert len(all_statuses) == 1


# ---------------------------------------------------------------------------
# Decision Audit Log Tests
# ---------------------------------------------------------------------------


class TestDecisionAuditLog:
    def test_record_decision_minimal(self, repo):
        entry = repo.record_decision(_make_decision_data())
        assert entry.id is not None
        assert entry.correlation_id == "corr-001"
        assert entry.decision_action == "EXECUTE"
        assert entry.passed_verification is True

    def test_record_decision_with_all_fields(self, repo):
        now = datetime.now(UTC)
        entry = repo.record_decision(_make_decision_data(
            timestamp=now,
            reasoning="High APY opportunity on Aave",
            strategy_reports=[{"strategy": "LEND-001", "actionable": True}],
            orders=[{"action": "supply", "amount": "1000"}],
            passed_verification=True,
            risk_flags=["gas_spike_warning"],
            prompt_tokens=500,
            completion_tokens=200,
        ))
        assert entry.reasoning == "High APY opportunity on Aave"
        assert entry.prompt_tokens == 500
        assert entry.completion_tokens == 200

        reports = json.loads(entry.strategy_reports_json)
        assert len(reports) == 1
        assert reports[0]["strategy"] == "LEND-001"

        orders = json.loads(entry.orders_json)
        assert orders[0]["action"] == "supply"

        flags = json.loads(entry.risk_flags_json)
        assert "gas_spike_warning" in flags

    def test_record_decision_failed_verification(self, repo):
        entry = repo.record_decision(_make_decision_data(
            passed_verification=False,
            risk_flags=["exposure_limit_exceeded"],
        ))
        assert entry.passed_verification is False

    def test_get_decisions_returns_all(self, repo):
        for i in range(5):
            repo.record_decision(_make_decision_data(
                correlation_id=f"corr-{i}",
            ))
        decisions = repo.get_decisions()
        assert len(decisions) == 5

    def test_get_decisions_filter_by_since(self, repo):
        old = datetime(2024, 6, 1, tzinfo=UTC)
        new = datetime(2025, 6, 1, tzinfo=UTC)
        repo.record_decision(_make_decision_data(
            correlation_id="old", timestamp=old,
        ))
        repo.record_decision(_make_decision_data(
            correlation_id="new", timestamp=new,
        ))
        recent = repo.get_decisions(since=datetime(2025, 1, 1, tzinfo=UTC))
        assert len(recent) == 1
        assert recent[0].correlation_id == "new"

    def test_get_decisions_filter_by_action(self, repo):
        repo.record_decision(_make_decision_data(
            correlation_id="c1", decision_action="EXECUTE",
        ))
        repo.record_decision(_make_decision_data(
            correlation_id="c2", decision_action="HOLD",
        ))
        repo.record_decision(_make_decision_data(
            correlation_id="c3", decision_action="EXECUTE",
        ))
        executes = repo.get_decisions(action="EXECUTE")
        assert len(executes) == 2

    def test_get_decisions_respects_limit(self, repo):
        for i in range(10):
            repo.record_decision(_make_decision_data(correlation_id=f"c-{i}"))
        decisions = repo.get_decisions(limit=3)
        assert len(decisions) == 3

    def test_get_decisions_ordered_by_timestamp_desc(self, repo):
        t1 = datetime(2025, 1, 1, tzinfo=UTC)
        t2 = datetime(2025, 6, 1, tzinfo=UTC)
        repo.record_decision(_make_decision_data(
            correlation_id="c1", timestamp=t1,
        ))
        repo.record_decision(_make_decision_data(
            correlation_id="c2", timestamp=t2,
        ))
        decisions = repo.get_decisions()
        assert decisions[0].correlation_id == "c2"

    def test_decision_with_raw_json_fields(self, repo):
        entry = repo.record_decision(_make_decision_data(
            strategy_reports_json='[{"s": "LEND-001"}]',
            orders_json='[{"action": "supply"}]',
            risk_flags_json='["flag1"]',
        ))
        assert json.loads(entry.strategy_reports_json)[0]["s"] == "LEND-001"
        assert json.loads(entry.orders_json)[0]["action"] == "supply"
        assert "flag1" in json.loads(entry.risk_flags_json)

    def test_decision_missing_required_field_raises(self, repo):
        with pytest.raises(KeyError):
            repo.record_decision({"correlation_id": "c1"})


# ---------------------------------------------------------------------------
# In-Memory Cache Tests
# ---------------------------------------------------------------------------


class TestLoadCache:
    def test_load_cache_empty(self, repo):
        cache = repo.load_cache()
        assert cache["positions"] == {}
        assert cache["strategy_statuses"] == {}
        assert cache["latest_snapshot"] is None

    def test_load_cache_with_positions(self, repo):
        repo.save_position(_make_position_data(position_id="p1"))
        repo.save_position(_make_position_data(position_id="p2"))
        cache = repo.load_cache()
        assert len(cache["positions"]) == 2
        assert "p1" in cache["positions"]
        assert "p2" in cache["positions"]
        assert cache["positions"]["p1"]["strategy"] == "LEND-001"

    def test_load_cache_excludes_closed_positions(self, repo):
        repo.save_position(_make_position_data(position_id="open-1"))
        repo.save_position(_make_position_data(
            position_id="closed-1", status="closed",
        ))
        cache = repo.load_cache()
        assert len(cache["positions"]) == 1
        assert "open-1" in cache["positions"]

    def test_load_cache_with_strategy_statuses(self, repo):
        repo.save_strategy_status("LEND-001", "active")
        repo.save_strategy_status("LP-001", "inactive")
        cache = repo.load_cache()
        assert cache["strategy_statuses"]["LEND-001"] == "active"
        assert cache["strategy_statuses"]["LP-001"] == "inactive"

    def test_load_cache_with_snapshot(self, repo):
        from tests.test_database import _make_snapshot_data
        repo.take_portfolio_snapshot(_make_snapshot_data())
        cache = repo.load_cache()
        assert cache["latest_snapshot"] is not None
        assert "total_value_usd" in cache["latest_snapshot"]

    def test_load_cache_position_values_are_strings(self, repo):
        repo.save_position(_make_position_data(
            entry_price="1.005",
            amount="500.25",
            current_value="502.75",
        ))
        cache = repo.load_cache()
        pos = cache["positions"]["pos-001"]
        # Values should be strings for JSON serialization compatibility
        assert isinstance(pos["entry_price"], str)
        assert isinstance(pos["amount"], str)
        assert isinstance(pos["current_value"], str)


# ---------------------------------------------------------------------------
# Query Performance Tests
# ---------------------------------------------------------------------------


class TestNewQueryPerformance:
    def test_position_query_under_200ms(self, repo):
        for i in range(100):
            repo.save_position(_make_position_data(
                position_id=f"pos-{i}",
                strategy="LEND-001" if i % 2 == 0 else "LP-001",
            ))
        start = time.monotonic()
        result = repo.get_positions(strategy="LEND-001", limit=50)
        elapsed_ms = (time.monotonic() - start) * 1000
        assert len(result) > 0
        assert elapsed_ms < 200, f"Query took {elapsed_ms:.1f}ms"

    def test_strategy_status_query_under_200ms(self, repo):
        for i in range(50):
            repo.save_strategy_status(f"STRAT-{i:03d}")
        start = time.monotonic()
        result = repo.get_strategy_statuses()
        elapsed_ms = (time.monotonic() - start) * 1000
        assert len(result) == 50
        assert elapsed_ms < 200, f"Query took {elapsed_ms:.1f}ms"

    def test_decision_query_under_200ms(self, repo):
        for i in range(100):
            repo.record_decision(_make_decision_data(
                correlation_id=f"corr-{i}",
                decision_action="EXECUTE" if i % 2 == 0 else "HOLD",
            ))
        start = time.monotonic()
        result = repo.get_decisions(action="EXECUTE", limit=50)
        elapsed_ms = (time.monotonic() - start) * 1000
        assert len(result) > 0
        assert elapsed_ms < 200, f"Query took {elapsed_ms:.1f}ms"

    def test_load_cache_under_200ms(self, repo):
        for i in range(50):
            repo.save_position(_make_position_data(position_id=f"pos-{i}"))
        for i in range(10):
            repo.save_strategy_status(f"STRAT-{i:03d}")
        start = time.monotonic()
        cache = repo.load_cache()
        elapsed_ms = (time.monotonic() - start) * 1000
        assert len(cache["positions"]) == 50
        assert elapsed_ms < 200, f"Query took {elapsed_ms:.1f}ms"


# ---------------------------------------------------------------------------
# Edge Cases
# ---------------------------------------------------------------------------


class TestNewEdgeCases:
    def test_position_with_zero_value(self, repo):
        pos = repo.save_position(_make_position_data(
            current_value="0",
            unrealized_pnl="-1000.0",
        ))
        assert float(pos.current_value) == pytest.approx(0.0)

    def test_decision_with_none_optional_fields(self, repo):
        entry = repo.record_decision(_make_decision_data())
        assert entry.reasoning is None
        assert entry.strategy_reports_json is None
        assert entry.orders_json is None
        assert entry.risk_flags_json is None
        assert entry.prompt_tokens is None
        assert entry.completion_tokens is None

    def test_position_protocol_data_json(self, repo):
        data = {"aave_token": "aUSDC", "pool": "0x123", "interest_mode": 2}
        pos = repo.save_position(_make_position_data(protocol_data=data))
        parsed = json.loads(pos.protocol_data_json)
        assert parsed["aave_token"] == "aUSDC"
        assert parsed["interest_mode"] == 2

    def test_concurrent_repos_share_state(self, db_manager):
        repo1 = DatabaseRepository(db_manager)
        repo2 = DatabaseRepository(db_manager)
        repo1.save_position(_make_position_data(position_id="shared-1"))
        pos = repo2.get_position("shared-1")
        assert pos is not None

    def test_database_url_env_var(self):
        import os
        config = DatabaseConfig()
        # Default should be SQLite when DATABASE_URL not set
        if "DATABASE_URL" not in os.environ:
            assert "sqlite" in config.url
