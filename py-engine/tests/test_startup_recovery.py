"""Tests for startup recovery sequence — HARNESS-002."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from harness.startup_recovery import (
    RECOVERY_HARD_LIMIT_SECONDS,
    RECOVERY_TARGET_SECONDS,
    HealthCheckResult,
    RecoveryStatus,
    StartupRecovery,
)
from harness.state_manager import StateManager


def _make_state_manager(tmp_path: Path) -> StateManager:
    return StateManager(state_path=tmp_path / "state.json")


def _make_redis_mock(
    orders: list[dict[str, Any]] | None = None,
    results: list[dict[str, Any]] | None = None,
) -> MagicMock:
    mock = MagicMock()
    mock.stream_read.side_effect = lambda channel, **kw: (
        (orders or []) if "orders" in channel else (results or [])
    )
    return mock


def _make_reconciler(
    discrepancies: list[dict[str, Any]] | None = None,
) -> MagicMock:
    mock = MagicMock()
    mock.reconcile_positions.return_value = discrepancies or []
    return mock


def _healthy_checks() -> list[HealthCheckResult]:
    return [
        HealthCheckResult(protocol="aave", healthy=True, latency_ms=12.5),
        HealthCheckResult(protocol="uniswap", healthy=True, latency_ms=8.3),
    ]


def _mixed_checks() -> list[HealthCheckResult]:
    return [
        HealthCheckResult(protocol="aave", healthy=True, latency_ms=12.5),
        HealthCheckResult(protocol="chainlink", healthy=False, message="timeout"),
    ]


# ---------------------------------------------------------------------------
# State loading
# ---------------------------------------------------------------------------

class TestStateLoading:

    def test_loads_state_on_run(self, tmp_path: Path) -> None:
        sm = _make_state_manager(tmp_path)
        recovery = StartupRecovery(state_manager=sm)
        result = recovery.run()
        assert result.state_loaded is True

    def test_state_load_failure_enters_diagnostic(self, tmp_path: Path) -> None:
        sm = MagicMock()
        sm.reload.side_effect = RuntimeError("corrupt state")
        recovery = StartupRecovery(state_manager=sm)
        result = recovery.run()
        assert result.state_loaded is False
        assert any("State load failed" in e for e in result.errors)

    def test_existing_positions_visible_after_load(self, tmp_path: Path) -> None:
        sm = _make_state_manager(tmp_path)
        sm.set_position("aave-eth", {"protocol": "aave", "amount": 1.5})
        recovery = StartupRecovery(state_manager=sm)
        result = recovery.run()
        assert result.state_loaded is True
        assert sm.get_positions()["aave-eth"]["amount"] == 1.5


# ---------------------------------------------------------------------------
# Redis stream checking
# ---------------------------------------------------------------------------

class TestStreamChecking:

    def test_no_redis_skips_check(self, tmp_path: Path) -> None:
        sm = _make_state_manager(tmp_path)
        recovery = StartupRecovery(state_manager=sm, redis_manager=None)
        result = recovery.run()
        assert result.unprocessed_orders == 0
        assert result.unprocessed_results == 0

    def test_counts_unprocessed_orders(self, tmp_path: Path) -> None:
        sm = _make_state_manager(tmp_path)
        redis = _make_redis_mock(
            orders=[{"id": "1", "data": {}}],
            results=[{"id": "2", "data": {}}, {"id": "3", "data": {}}],
        )
        recovery = StartupRecovery(state_manager=sm, redis_manager=redis)
        result = recovery.run()
        assert result.unprocessed_orders == 1
        assert result.unprocessed_results == 2

    def test_empty_streams(self, tmp_path: Path) -> None:
        sm = _make_state_manager(tmp_path)
        redis = _make_redis_mock(orders=[], results=[])
        recovery = StartupRecovery(state_manager=sm, redis_manager=redis)
        result = recovery.run()
        assert result.unprocessed_orders == 0
        assert result.unprocessed_results == 0

    def test_redis_error_adds_error(self, tmp_path: Path) -> None:
        sm = _make_state_manager(tmp_path)
        redis = MagicMock()
        redis.stream_read.side_effect = ConnectionError("Redis down")
        recovery = StartupRecovery(state_manager=sm, redis_manager=redis)
        result = recovery.run()
        assert any("Redis stream check failed" in e for e in result.errors)


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------

class TestReconciliation:

    def test_no_reconciler_skips(self, tmp_path: Path) -> None:
        sm = _make_state_manager(tmp_path)
        recovery = StartupRecovery(state_manager=sm, reconciler=None)
        result = recovery.run()
        assert result.discrepancies_found == 0

    def test_no_positions_skips_reconcile(self, tmp_path: Path) -> None:
        sm = _make_state_manager(tmp_path)
        recon = _make_reconciler()
        recovery = StartupRecovery(state_manager=sm, reconciler=recon)
        result = recovery.run()
        recon.reconcile_positions.assert_not_called()
        assert result.discrepancies_found == 0

    def test_counts_discrepancies(self, tmp_path: Path) -> None:
        sm = _make_state_manager(tmp_path)
        sm.set_position("pos1", {"protocol": "aave", "amount": 1.0})
        recon = _make_reconciler(discrepancies=[
            {"type": "balance_mismatch", "auto_fixable": True},
            {"type": "missing_position", "auto_fixable": False},
        ])
        recovery = StartupRecovery(state_manager=sm, reconciler=recon)
        result = recovery.run()
        assert result.discrepancies_found == 2
        assert result.discrepancies_resolved == 1

    def test_all_auto_fixable_no_errors(self, tmp_path: Path) -> None:
        sm = _make_state_manager(tmp_path)
        sm.set_position("pos1", {"protocol": "aave", "amount": 1.0})
        recon = _make_reconciler(discrepancies=[
            {"type": "interest_drift", "auto_fixable": True},
        ])
        recovery = StartupRecovery(state_manager=sm, reconciler=recon)
        result = recovery.run()
        assert result.discrepancies_found == 1
        assert result.discrepancies_resolved == 1
        assert not any("unresolved" in e for e in result.errors)

    def test_unresolved_discrepancies_add_error(self, tmp_path: Path) -> None:
        sm = _make_state_manager(tmp_path)
        sm.set_position("pos1", {"protocol": "aave", "amount": 1.0})
        recon = _make_reconciler(discrepancies=[
            {"type": "missing_position", "auto_fixable": False},
        ])
        recovery = StartupRecovery(state_manager=sm, reconciler=recon)
        result = recovery.run()
        assert any("unresolved" in e for e in result.errors)

    def test_reconciler_exception_adds_error(self, tmp_path: Path) -> None:
        sm = _make_state_manager(tmp_path)
        sm.set_position("pos1", {"protocol": "aave", "amount": 1.0})
        recon = MagicMock()
        recon.reconcile_positions.side_effect = RuntimeError("chain query failed")
        recovery = StartupRecovery(state_manager=sm, reconciler=recon)
        result = recovery.run()
        assert any("Reconciliation failed" in e for e in result.errors)


# ---------------------------------------------------------------------------
# Health checks
# ---------------------------------------------------------------------------

class TestHealthChecks:

    def test_no_health_fn_skips(self, tmp_path: Path) -> None:
        sm = _make_state_manager(tmp_path)
        recovery = StartupRecovery(state_manager=sm, health_check_fn=None)
        result = recovery.run()
        assert result.health_checks == []

    def test_all_healthy_no_errors(self, tmp_path: Path) -> None:
        sm = _make_state_manager(tmp_path)
        recovery = StartupRecovery(state_manager=sm, health_check_fn=_healthy_checks)
        result = recovery.run()
        assert len(result.health_checks) == 2
        assert all(h.healthy for h in result.health_checks)
        assert not any("Health check" in e for e in result.errors)

    def test_unhealthy_protocol_adds_error(self, tmp_path: Path) -> None:
        sm = _make_state_manager(tmp_path)
        recovery = StartupRecovery(state_manager=sm, health_check_fn=_mixed_checks)
        result = recovery.run()
        assert any("chainlink" in e for e in result.errors)

    def test_health_check_exception_adds_error(self, tmp_path: Path) -> None:
        sm = _make_state_manager(tmp_path)

        def _failing_checks() -> list[HealthCheckResult]:
            raise ConnectionError("network down")

        recovery = StartupRecovery(state_manager=sm, health_check_fn=_failing_checks)
        result = recovery.run()
        assert any("Health checks failed" in e for e in result.errors)


# ---------------------------------------------------------------------------
# Recovery status determination
# ---------------------------------------------------------------------------

class TestRecoveryStatus:

    def test_clean_run_returns_success(self, tmp_path: Path) -> None:
        sm = _make_state_manager(tmp_path)
        recovery = StartupRecovery(state_manager=sm)
        result = recovery.run()
        assert result.status == RecoveryStatus.SUCCESS

    def test_errors_cause_diagnostic(self, tmp_path: Path) -> None:
        sm = MagicMock()
        sm.reload.side_effect = RuntimeError("bad state")
        recovery = StartupRecovery(state_manager=sm)
        result = recovery.run()
        assert result.status == RecoveryStatus.DIAGNOSTIC

    def test_catastrophic_failure_returns_failed(self, tmp_path: Path) -> None:
        """Multiple cascading errors should result in FAILED status."""
        sm = MagicMock()
        sm.reload.side_effect = RuntimeError("corrupt")
        sm.get_positions.side_effect = RuntimeError("no state")
        redis = MagicMock()
        redis.stream_read.side_effect = ConnectionError("Redis down")
        recovery = StartupRecovery(state_manager=sm, redis_manager=redis)
        result = recovery.run()
        # Multiple errors → diagnostic status
        assert result.status == RecoveryStatus.DIAGNOSTIC
        assert len(result.errors) >= 2

    def test_duration_recorded(self, tmp_path: Path) -> None:
        sm = _make_state_manager(tmp_path)
        recovery = StartupRecovery(state_manager=sm)
        result = recovery.run()
        assert result.duration_seconds >= 0
        assert result.duration_seconds < 5


# ---------------------------------------------------------------------------
# Diagnostic callback
# ---------------------------------------------------------------------------

class TestDiagnosticCallback:

    def test_callback_invoked_on_diagnostic(self, tmp_path: Path) -> None:
        sm = MagicMock()
        sm.reload.side_effect = RuntimeError("corrupt")
        callback = MagicMock()
        recovery = StartupRecovery(state_manager=sm, on_diagnostic=callback)
        result = recovery.run()
        assert result.status == RecoveryStatus.DIAGNOSTIC
        callback.assert_called_once_with(result)

    def test_callback_not_invoked_on_success(self, tmp_path: Path) -> None:
        sm = _make_state_manager(tmp_path)
        callback = MagicMock()
        recovery = StartupRecovery(state_manager=sm, on_diagnostic=callback)
        result = recovery.run()
        assert result.status == RecoveryStatus.SUCCESS
        callback.assert_not_called()


# ---------------------------------------------------------------------------
# Timing constants
# ---------------------------------------------------------------------------

class TestTimingConstants:

    def test_target_under_hard_limit(self) -> None:
        assert RECOVERY_TARGET_SECONDS < RECOVERY_HARD_LIMIT_SECONDS

    def test_target_is_60s(self) -> None:
        assert RECOVERY_TARGET_SECONDS == 60

    def test_hard_limit_is_300s(self) -> None:
        assert RECOVERY_HARD_LIMIT_SECONDS == 300


# ---------------------------------------------------------------------------
# Full integration scenario
# ---------------------------------------------------------------------------

class TestFullRecovery:

    def test_full_clean_recovery(self, tmp_path: Path) -> None:
        sm = _make_state_manager(tmp_path)
        sm.set_position("pos1", {"protocol": "aave", "amount": 2.0})

        redis = _make_redis_mock(orders=[], results=[])
        recon = _make_reconciler(discrepancies=[])

        recovery = StartupRecovery(
            state_manager=sm,
            redis_manager=redis,
            reconciler=recon,
            health_check_fn=_healthy_checks,
        )
        result = recovery.run()
        assert result.status == RecoveryStatus.SUCCESS
        assert result.state_loaded is True
        assert result.discrepancies_found == 0
        assert len(result.health_checks) == 2
        assert result.errors == []

    def test_full_recovery_with_issues(self, tmp_path: Path) -> None:
        sm = _make_state_manager(tmp_path)
        sm.set_position("pos1", {"protocol": "aave", "amount": 2.0})

        redis = _make_redis_mock(
            orders=[{"id": "1", "data": {}}],
            results=[],
        )
        recon = _make_reconciler(discrepancies=[
            {"type": "balance_mismatch", "auto_fixable": False},
        ])

        recovery = StartupRecovery(
            state_manager=sm,
            redis_manager=redis,
            reconciler=recon,
            health_check_fn=_mixed_checks,
        )
        result = recovery.run()
        assert result.status == RecoveryStatus.DIAGNOSTIC
        assert result.unprocessed_orders == 1
        assert result.discrepancies_found == 1
        assert len(result.errors) >= 1
