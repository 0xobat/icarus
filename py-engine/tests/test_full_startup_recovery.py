"""Tests for full startup recovery sequence — HARNESS-002.

Tests the run_startup_recovery() function and its integration with
PostgreSQL, Redis Streams, PositionReconciler, and HoldMode.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock, PropertyMock

from harness.hold_mode import HoldMode, HoldTrigger
from harness.startup_recovery import (
    FullRecoveryResult,
    _recovery_enter_hold,
    _recovery_health_check,
    _recovery_load_pg_state,
    _recovery_reconcile,
    _recovery_replay_streams,
    run_startup_recovery,
)

# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


def _make_repository(
    positions: dict[str, Any] | None = None,
    strategy_statuses: dict[str, str] | None = None,
) -> MagicMock:
    """Create a mock DatabaseRepository with load_cache()."""
    repo = MagicMock()
    repo.load_cache.return_value = {
        "positions": positions or {},
        "strategy_statuses": strategy_statuses or {},
        "latest_snapshot": None,
    }
    return repo


def _make_redis(
    pending_messages: dict[str, list[Any]] | None = None,
    ping_ok: bool = True,
) -> MagicMock:
    """Create a mock RedisManager with consumer group support."""
    redis = MagicMock()
    redis._group = "py-engine"
    redis._consumer = "py-engine-1"

    client = MagicMock()
    if ping_ok:
        client.ping.return_value = True
    else:
        client.ping.side_effect = ConnectionError("Redis down")

    # Mock xreadgroup for pending messages
    def _xreadgroup(group, consumer, streams, count=100):
        results = []
        for channel, _id in streams.items():
            msgs = (pending_messages or {}).get(channel, [])
            if msgs:
                results.append((channel, msgs))
        return results or None

    client.xreadgroup.side_effect = _xreadgroup
    type(redis).client = PropertyMock(return_value=client)

    return redis


def _make_db_manager(healthy: bool = True) -> MagicMock:
    """Create a mock DatabaseManager."""
    db = MagicMock()
    session = MagicMock()
    if not healthy:
        session.execute.side_effect = ConnectionError("PG down")
    db.get_session.return_value = session
    return db


def _make_hold_mode() -> HoldMode:
    """Create a HoldMode instance (in-memory, no Redis)."""
    return HoldMode()


@dataclass
class MockReconciliationResult:
    """Mock for data.reconciliation.ReconciliationResult."""

    discrepancies_found: int = 0
    positions_closed: int = 0
    positions_created: int = 0
    positions_updated: int = 0
    success: bool = True
    discrepancies: list[Any] = field(default_factory=list)


@dataclass
class MockDiscrepancy:
    """Mock for data.reconciliation.PositionDiscrepancy."""

    position_id: str | None = None
    expected_value: float = 0.0
    actual_value: float = 0.0
    asset: str = ""
    protocol: str = ""
    discrepancy_type: str = ""


def _make_reconciler(
    discrepancies_found: int = 0,
    discrepancies: list[Any] | None = None,
    success: bool = True,
    raise_error: bool = False,
) -> MagicMock:
    """Create a mock PositionReconciler."""
    recon = MagicMock()
    if raise_error:
        recon.run.side_effect = RuntimeError("on-chain query failed")
    else:
        recon.run.return_value = MockReconciliationResult(
            discrepancies_found=discrepancies_found,
            success=success,
            discrepancies=discrepancies or [],
        )
    return recon


# ---------------------------------------------------------------------------
# Step 1: PostgreSQL state loading
# ---------------------------------------------------------------------------

class TestPGStateLoading:

    def test_loads_positions_and_statuses(self) -> None:
        repo = _make_repository(
            positions={"p1": {}, "p2": {}},
            strategy_statuses={"LEND-001": "active"},
        )
        result = FullRecoveryResult()
        _recovery_load_pg_state(repo, result)

        assert result.positions_loaded == 2
        assert result.strategy_statuses_loaded == 1
        assert "pg_state_loaded" in result.steps_completed
        assert result.errors == []

    def test_empty_database(self) -> None:
        repo = _make_repository()
        result = FullRecoveryResult()
        _recovery_load_pg_state(repo, result)

        assert result.positions_loaded == 0
        assert result.strategy_statuses_loaded == 0
        assert "pg_state_loaded" in result.steps_completed

    def test_pg_error_adds_error(self) -> None:
        repo = MagicMock()
        repo.load_cache.side_effect = ConnectionError("PG unavailable")
        result = FullRecoveryResult()
        _recovery_load_pg_state(repo, result)

        assert result.positions_loaded == 0
        assert any("Failed to load state" in e for e in result.errors)


# ---------------------------------------------------------------------------
# Step 2: Redis Stream replay
# ---------------------------------------------------------------------------

class TestStreamReplay:

    def test_no_pending_messages(self) -> None:
        redis = _make_redis()
        result = FullRecoveryResult()
        _recovery_replay_streams(redis, result)

        assert result.messages_replayed == 0
        assert "streams_replayed" in result.steps_completed

    def test_counts_pending_messages(self) -> None:
        redis = _make_redis(pending_messages={
            "execution:results": [
                ("1-0", {"data": '{"status": "confirmed"}'}),
                ("2-0", {"data": '{"status": "failed"}'}),
            ],
            "market:events": [
                ("3-0", {"data": '{"eventType": "price_update"}'}),
            ],
        })
        result = FullRecoveryResult()
        _recovery_replay_streams(redis, result)

        assert result.messages_replayed == 3
        assert "streams_replayed" in result.steps_completed

    def test_stream_error_records_error(self) -> None:
        redis = MagicMock()
        redis._group = "py-engine"
        redis._consumer = "py-engine-1"
        client = MagicMock()
        client.xreadgroup.side_effect = ConnectionError("Redis timeout")
        type(redis).client = PropertyMock(return_value=client)
        redis._ensure_group.return_value = None

        result = FullRecoveryResult()
        _recovery_replay_streams(redis, result)

        # Errors are recorded but recovery continues
        assert "streams_replayed" in result.steps_completed


# ---------------------------------------------------------------------------
# Step 3 & 4: On-chain reconciliation
# ---------------------------------------------------------------------------

class TestReconciliation:

    def test_no_reconciler_skips(self) -> None:
        result = FullRecoveryResult()
        _recovery_reconcile(None, "0x123", MagicMock(), result)

        assert "onchain_reconciliation_skipped" in result.steps_completed
        assert result.reconciliation_success is True

    def test_no_wallet_skips(self) -> None:
        recon = _make_reconciler()
        result = FullRecoveryResult()
        _recovery_reconcile(recon, "", MagicMock(), result)

        assert "onchain_reconciliation_skipped" in result.steps_completed
        recon.run.assert_not_called()

    def test_clean_reconciliation(self) -> None:
        recon = _make_reconciler(discrepancies_found=0, success=True)
        repo = MagicMock()
        result = FullRecoveryResult()
        _recovery_reconcile(recon, "0xabc", repo, result)

        assert result.reconciliation_success is True
        assert result.reconciliation_discrepancies == 0
        assert "onchain_reconciliation_complete" in result.steps_completed
        recon.run.assert_called_once_with("0xabc", repo)

    def test_auto_fixable_discrepancies(self) -> None:
        discs = [
            MockDiscrepancy(
                discrepancy_type="value_mismatch",
                asset="USDC", protocol="aave_v3",
            ),
        ]
        recon = _make_reconciler(
            discrepancies_found=1, discrepancies=discs, success=True,
        )
        result = FullRecoveryResult()
        _recovery_reconcile(recon, "0xabc", MagicMock(), result)

        assert result.reconciliation_discrepancies == 1
        assert result.reconciliation_success is True

    def test_missing_onchain_triggers_failure(self) -> None:
        discs = [
            MockDiscrepancy(
                discrepancy_type="missing_onchain",
                asset="USDC", protocol="aave_v3",
            ),
        ]
        recon = _make_reconciler(
            discrepancies_found=1, discrepancies=discs, success=True,
        )
        result = FullRecoveryResult()
        _recovery_reconcile(recon, "0xabc", MagicMock(), result)

        assert result.reconciliation_success is False

    def test_reconciler_exception(self) -> None:
        recon = _make_reconciler(raise_error=True)
        result = FullRecoveryResult()
        _recovery_reconcile(recon, "0xabc", MagicMock(), result)

        assert result.reconciliation_success is False
        assert any("On-chain reconciliation failed" in e for e in result.errors)


# ---------------------------------------------------------------------------
# Step 5: Health checks
# ---------------------------------------------------------------------------

class TestHealthChecks:

    def test_both_healthy(self) -> None:
        redis = _make_redis(ping_ok=True)
        db = _make_db_manager(healthy=True)
        result = FullRecoveryResult()
        _recovery_health_check(redis, db, result)

        assert result.health_check_redis is True
        assert result.health_check_postgres is True
        assert "health_check_complete" in result.steps_completed

    def test_redis_down(self) -> None:
        redis = _make_redis(ping_ok=False)
        db = _make_db_manager(healthy=True)
        result = FullRecoveryResult()
        _recovery_health_check(redis, db, result)

        assert result.health_check_redis is False
        assert result.health_check_postgres is True
        assert any("Redis health check failed" in e for e in result.errors)

    def test_postgres_down(self) -> None:
        redis = _make_redis(ping_ok=True)
        db = _make_db_manager(healthy=False)
        result = FullRecoveryResult()
        _recovery_health_check(redis, db, result)

        assert result.health_check_redis is True
        assert result.health_check_postgres is False
        assert any("PostgreSQL health check failed" in e for e in result.errors)


# ---------------------------------------------------------------------------
# Step 6: Hold mode entry
# ---------------------------------------------------------------------------

class TestHoldModeEntry:

    def test_enters_hold_on_health_failure(self) -> None:
        hold = _make_hold_mode()
        result = FullRecoveryResult()
        _recovery_enter_hold(hold, result, "Health check failed")

        assert result.entered_hold_mode is True
        assert result.hold_reason == "Health check failed"
        assert hold.is_active()
        assert hold.trigger == HoldTrigger.IRRECONCILABLE_STATE

    def test_enters_hold_on_reconciliation_failure(self) -> None:
        hold = _make_hold_mode()
        result = FullRecoveryResult()
        _recovery_enter_hold(hold, result, "On-chain reconciliation failed")

        assert hold.is_active()
        assert "reconciliation" in result.hold_reason.lower()


# ---------------------------------------------------------------------------
# Full recovery integration
# ---------------------------------------------------------------------------

class TestFullRecovery:

    def test_clean_recovery_success(self) -> None:
        result = run_startup_recovery(
            redis=_make_redis(),
            db_manager=_make_db_manager(),
            repository=_make_repository(positions={"p1": {}}),
            hold_mode=_make_hold_mode(),
            position_tracker=MagicMock(),
        )

        assert result.success is True
        assert result.entered_hold_mode is False
        assert result.positions_loaded == 1
        assert result.duration_seconds >= 0
        assert "pg_state_loaded" in result.steps_completed
        assert "streams_replayed" in result.steps_completed
        assert "health_check_complete" in result.steps_completed

    def test_redis_down_enters_hold(self) -> None:
        hold = _make_hold_mode()
        result = run_startup_recovery(
            redis=_make_redis(ping_ok=False),
            db_manager=_make_db_manager(),
            repository=_make_repository(),
            hold_mode=hold,
            position_tracker=MagicMock(),
        )

        assert result.success is False
        assert result.entered_hold_mode is True
        assert hold.is_active()

    def test_pg_down_enters_hold(self) -> None:
        hold = _make_hold_mode()
        result = run_startup_recovery(
            redis=_make_redis(),
            db_manager=_make_db_manager(healthy=False),
            repository=_make_repository(),
            hold_mode=hold,
            position_tracker=MagicMock(),
        )

        assert result.success is False
        assert result.entered_hold_mode is True

    def test_reconciliation_failure_enters_hold(self) -> None:
        discs = [
            MockDiscrepancy(discrepancy_type="missing_onchain"),
        ]
        recon = _make_reconciler(
            discrepancies_found=1, discrepancies=discs, success=True,
        )
        hold = _make_hold_mode()

        result = run_startup_recovery(
            redis=_make_redis(),
            db_manager=_make_db_manager(),
            repository=_make_repository(),
            hold_mode=hold,
            position_tracker=MagicMock(),
            reconciler=recon,
            wallet_address="0xabc",
        )

        assert result.success is False
        assert result.entered_hold_mode is True
        assert hold.is_active()

    def test_clean_reconciliation_no_hold(self) -> None:
        recon = _make_reconciler(discrepancies_found=0, success=True)
        hold = _make_hold_mode()

        result = run_startup_recovery(
            redis=_make_redis(),
            db_manager=_make_db_manager(),
            repository=_make_repository(),
            hold_mode=hold,
            position_tracker=MagicMock(),
            reconciler=recon,
            wallet_address="0xabc",
        )

        assert result.success is True
        assert result.entered_hold_mode is False
        assert "onchain_reconciliation_complete" in result.steps_completed

    def test_no_reconciler_still_succeeds(self) -> None:
        result = run_startup_recovery(
            redis=_make_redis(),
            db_manager=_make_db_manager(),
            repository=_make_repository(),
            hold_mode=_make_hold_mode(),
            position_tracker=MagicMock(),
            reconciler=None,
        )

        assert result.success is True
        assert "onchain_reconciliation_skipped" in result.steps_completed


# ---------------------------------------------------------------------------
# DecisionLoop.startup_recovery() integration
# ---------------------------------------------------------------------------

class TestDecisionLoopIntegration:

    def test_decision_loop_has_startup_recovery(self) -> None:
        """DecisionLoop exposes a startup_recovery() method."""
        from main import DecisionLoop
        assert hasattr(DecisionLoop, "startup_recovery")

    def test_startup_recovery_returns_result(self) -> None:
        """startup_recovery() returns a FullRecoveryResult."""
        from main import DecisionLoop

        redis = _make_redis()
        db = _make_db_manager()
        repo = _make_repository()
        state = MagicMock()
        state.get_positions.return_value = {}
        state.get_strategy_statuses.return_value = {}

        loop = DecisionLoop(redis, db, repo, state)
        result = loop.startup_recovery()

        assert isinstance(result, FullRecoveryResult)


# ---------------------------------------------------------------------------
# FullRecoveryResult
# ---------------------------------------------------------------------------

class TestFullRecoveryResult:

    def test_success_when_all_healthy(self) -> None:
        result = FullRecoveryResult()
        assert result.success is True

    def test_not_success_when_redis_down(self) -> None:
        result = FullRecoveryResult(health_check_redis=False)
        assert result.success is False

    def test_not_success_when_pg_down(self) -> None:
        result = FullRecoveryResult(health_check_postgres=False)
        assert result.success is False

    def test_not_success_when_hold_mode(self) -> None:
        result = FullRecoveryResult(entered_hold_mode=True)
        assert result.success is False
