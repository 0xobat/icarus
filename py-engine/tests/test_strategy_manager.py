"""Tests for the strategy lifecycle manager (STRAT-002)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from db.database import DatabaseConfig, DatabaseManager
from db.repository import DatabaseRepository
from strategies.base import (
    GasInfo,
    MarketSnapshot,
    Observation,
    Signal,
    SignalType,
    StrategyReport,
    TokenPrice,
)
from strategies.manager import StrategyManager, _slice_snapshot

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class FakeStrategy:
    """Minimal strategy for testing."""

    strategy_id = "FAKE-001"
    eval_interval = timedelta(minutes=5)
    data_window = timedelta(hours=1)

    def evaluate(self, snapshot: MarketSnapshot) -> StrategyReport:
        return StrategyReport(
            strategy_id=self.strategy_id,
            timestamp=snapshot.timestamp.isoformat(),
            observations=[Observation(metric="test", value="1", context="test")],
            signals=[
                Signal(
                    type=SignalType.THRESHOLD_APPROACHING,
                    actionable=False,
                    details="test signal",
                )
            ],
        )


class FakeStrategy2:
    """Second minimal strategy for testing."""

    strategy_id = "FAKE-002"
    eval_interval = timedelta(minutes=10)
    data_window = timedelta(hours=2)

    def evaluate(self, snapshot: MarketSnapshot) -> StrategyReport:
        return StrategyReport(
            strategy_id=self.strategy_id,
            timestamp=snapshot.timestamp.isoformat(),
            observations=[],
            signals=[],
        )


class SlowStrategy:
    """Strategy that takes a moment to evaluate, for concurrency testing."""

    strategy_id = "SLOW-001"
    eval_interval = timedelta(minutes=1)
    data_window = timedelta(hours=1)

    def evaluate(self, snapshot: MarketSnapshot) -> StrategyReport:
        import time

        time.sleep(0.05)
        return StrategyReport(
            strategy_id=self.strategy_id,
            timestamp=snapshot.timestamp.isoformat(),
            observations=[],
            signals=[],
        )


class FailingStrategy:
    """Strategy that raises on evaluate."""

    strategy_id = "FAIL-001"
    eval_interval = timedelta(minutes=1)
    data_window = timedelta(hours=1)

    def evaluate(self, snapshot: MarketSnapshot) -> StrategyReport:
        msg = "evaluate boom"
        raise RuntimeError(msg)


def _make_snapshot() -> MarketSnapshot:
    return MarketSnapshot(
        prices=[],
        gas=GasInfo(current_gwei=1.0, avg_24h_gwei=1.0),
        pools=[],
        timestamp=datetime.now(UTC),
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
def discovered():
    return {"FAKE-001": FakeStrategy, "FAKE-002": FakeStrategy2}


@pytest.fixture()
def manager(repo, discovered):
    return StrategyManager(repo, discovered)


# ---------------------------------------------------------------------------
# Tests: init and sync
# ---------------------------------------------------------------------------


class TestInit:
    def test_new_strategies_registered_as_active(self, manager):
        active = manager.get_active_strategies()
        assert "FAKE-001" in active
        assert "FAKE-002" in active

    def test_statuses_persisted_to_db(self, repo, discovered):
        StrategyManager(repo, discovered)
        statuses = repo.get_strategy_statuses()
        ids = {s.strategy_id: s.status for s in statuses}
        assert ids["FAKE-001"] == "active"
        assert ids["FAKE-002"] == "active"

    def test_loads_existing_statuses_from_db(self, repo, discovered):
        repo.save_strategy_status("FAKE-001", "inactive")
        mgr = StrategyManager(repo, discovered)
        assert "FAKE-001" not in mgr.get_active_strategies()
        assert "FAKE-002" in mgr.get_active_strategies()

    def test_removed_strategy_marked_inactive(self, repo):
        repo.save_strategy_status("GONE-001", "active")
        mgr = StrategyManager(repo, {"FAKE-001": FakeStrategy})
        assert "GONE-001" not in mgr.get_active_strategies()
        row = repo.get_strategy_status("GONE-001")
        assert row is not None
        assert row.status == "inactive"


# ---------------------------------------------------------------------------
# Tests: activate / deactivate
# ---------------------------------------------------------------------------


class TestActivateDeactivate:
    def test_deactivate(self, manager):
        manager.deactivate("FAKE-001")
        assert "FAKE-001" not in manager.get_active_strategies()

    def test_activate_after_deactivate(self, manager):
        manager.deactivate("FAKE-001")
        manager.activate("FAKE-001")
        assert "FAKE-001" in manager.get_active_strategies()

    def test_activate_unknown_raises(self, manager):
        with pytest.raises(KeyError, match="Unknown strategy"):
            manager.activate("NOPE-999")

    def test_deactivate_unknown_raises(self, manager):
        with pytest.raises(KeyError, match="Unknown strategy"):
            manager.deactivate("NOPE-999")

    def test_status_persisted_on_toggle(self, manager, repo):
        manager.deactivate("FAKE-001")
        row = repo.get_strategy_status("FAKE-001")
        assert row is not None
        assert row.status == "inactive"

        manager.activate("FAKE-001")
        row = repo.get_strategy_status("FAKE-001")
        assert row is not None
        assert row.status == "active"


# ---------------------------------------------------------------------------
# Tests: should_evaluate / record_evaluation
# ---------------------------------------------------------------------------


class TestScheduling:
    def test_should_evaluate_first_time(self, manager):
        assert manager.should_evaluate("FAKE-001") is True

    def test_should_not_evaluate_inactive(self, manager):
        manager.deactivate("FAKE-001")
        assert manager.should_evaluate("FAKE-001") is False

    def test_should_not_evaluate_unknown(self, manager):
        assert manager.should_evaluate("NOPE-999") is False

    def test_should_not_evaluate_before_interval(self, manager):
        manager.record_evaluation("FAKE-001")
        assert manager.should_evaluate("FAKE-001") is False

    def test_should_evaluate_after_interval(self, manager):
        manager._last_evaluated["FAKE-001"] = datetime.now(UTC) - timedelta(minutes=6)
        assert manager.should_evaluate("FAKE-001") is True


# ---------------------------------------------------------------------------
# Tests: evaluate_all (async)
# ---------------------------------------------------------------------------


class TestEvaluateAll:
    def test_evaluates_active_strategies(self, manager):
        snapshot = _make_snapshot()
        reports = asyncio.run(manager.evaluate_all(snapshot))
        assert len(reports) == 2
        ids = {r.strategy_id for r in reports}
        assert ids == {"FAKE-001", "FAKE-002"}

    def test_skips_inactive(self, manager):
        manager.deactivate("FAKE-002")
        snapshot = _make_snapshot()
        reports = asyncio.run(manager.evaluate_all(snapshot))
        assert len(reports) == 1
        assert reports[0].strategy_id == "FAKE-001"

    def test_skips_not_due(self, manager):
        manager.record_evaluation("FAKE-001")
        manager.record_evaluation("FAKE-002")
        snapshot = _make_snapshot()
        reports = asyncio.run(manager.evaluate_all(snapshot))
        assert len(reports) == 0

    def test_records_evaluation_timestamp(self, manager):
        snapshot = _make_snapshot()
        asyncio.run(manager.evaluate_all(snapshot))
        assert "FAKE-001" in manager._last_evaluated
        assert "FAKE-002" in manager._last_evaluated

    def test_concurrent_evaluation(self, repo):
        strategies = {
            "SLOW-001": SlowStrategy,
            "FAKE-001": FakeStrategy,
        }
        mgr = StrategyManager(repo, strategies)
        snapshot = _make_snapshot()

        import time

        start = time.monotonic()
        reports = asyncio.run(mgr.evaluate_all(snapshot))
        elapsed = time.monotonic() - start

        assert len(reports) == 2
        # If truly concurrent, elapsed should be close to the slow one (~50ms)
        # not the sum (~100ms+). Allow generous margin.
        assert elapsed < 0.3

    def test_failing_strategy_does_not_block_others(self, repo):
        strategies = {
            "FAIL-001": FailingStrategy,
            "FAKE-001": FakeStrategy,
        }
        mgr = StrategyManager(repo, strategies)
        snapshot = _make_snapshot()
        reports = asyncio.run(mgr.evaluate_all(snapshot))
        assert len(reports) == 1
        assert reports[0].strategy_id == "FAKE-001"


# ---------------------------------------------------------------------------
# Tests: sync_with_discovered
# ---------------------------------------------------------------------------


class TestSyncWithDiscovered:
    def test_new_strategy_added(self, manager, repo):
        new_discovered = {
            "FAKE-001": FakeStrategy,
            "FAKE-002": FakeStrategy2,
            "SLOW-001": SlowStrategy,
        }
        manager.sync_with_discovered(new_discovered)
        assert "SLOW-001" in manager.get_active_strategies()

    def test_removed_strategy_deactivated(self, manager, repo):
        manager.sync_with_discovered({"FAKE-001": FakeStrategy})
        assert "FAKE-002" not in manager.get_active_strategies()
        row = repo.get_strategy_status("FAKE-002")
        assert row is not None
        assert row.status == "inactive"

    def test_already_inactive_stays_inactive(self, manager, repo):
        manager.deactivate("FAKE-002")
        manager.sync_with_discovered({"FAKE-001": FakeStrategy})
        row = repo.get_strategy_status("FAKE-002")
        assert row is not None
        assert row.status == "inactive"


# ---------------------------------------------------------------------------
# Tests: data_window pre-slicing
# ---------------------------------------------------------------------------


class RecordingStrategy:
    """Strategy that records the snapshot it receives."""

    strategy_id = "REC-001"
    eval_interval = timedelta(minutes=1)
    data_window = timedelta(hours=1)

    received_snapshot: MarketSnapshot | None = None

    def evaluate(self, snapshot: MarketSnapshot) -> StrategyReport:
        RecordingStrategy.received_snapshot = snapshot
        return StrategyReport(
            strategy_id=self.strategy_id,
            timestamp=snapshot.timestamp.isoformat(),
            observations=[],
            signals=[],
        )


class TestDataWindowSlicing:
    def test_slice_snapshot_filters_old_prices(self):
        now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        recent = TokenPrice(
            token="ETH", price=3000.0, source="test",
            timestamp=now - timedelta(minutes=30),
        )
        old = TokenPrice(
            token="BTC", price=90000.0, source="test",
            timestamp=now - timedelta(hours=2),
        )
        snapshot = MarketSnapshot(
            prices=[recent, old],
            gas=GasInfo(current_gwei=1.0, avg_24h_gwei=1.0),
            pools=[],
            timestamp=now,
        )
        sliced = _slice_snapshot(snapshot, timedelta(hours=1))
        assert len(sliced.prices) == 1
        assert sliced.prices[0].token == "ETH"

    def test_slice_snapshot_keeps_all_pools(self):
        """Pools have no timestamp — all should pass through."""
        from strategies.base import PoolState

        now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        snapshot = MarketSnapshot(
            prices=[],
            gas=GasInfo(current_gwei=1.0, avg_24h_gwei=1.0),
            pools=[PoolState(protocol="aave", pool_id="1", tvl=1e6, apy=0.05)],
            timestamp=now,
        )
        sliced = _slice_snapshot(snapshot, timedelta(hours=1))
        assert len(sliced.pools) == 1

    def test_evaluate_all_slices_by_strategy_window(self, repo):
        """A strategy with 1h window should not see 2h-old price data."""
        RecordingStrategy.received_snapshot = None
        mgr = StrategyManager(repo, {"REC-001": RecordingStrategy})

        now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        recent = TokenPrice(
            token="ETH", price=3000.0, source="test",
            timestamp=now - timedelta(minutes=30),
        )
        old = TokenPrice(
            token="BTC", price=90000.0, source="test",
            timestamp=now - timedelta(hours=2),
        )
        snapshot = MarketSnapshot(
            prices=[recent, old],
            gas=GasInfo(current_gwei=1.0, avg_24h_gwei=1.0),
            pools=[],
            timestamp=now,
        )
        asyncio.run(mgr.evaluate_all(snapshot))

        received = RecordingStrategy.received_snapshot
        assert received is not None
        assert len(received.prices) == 1
        assert received.prices[0].token == "ETH"
