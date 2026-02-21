"""Integration tests — startup recovery with real StateManager + DiagnosticMode."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from harness.diagnostic_mode import DiagnosticMode, DiagnosticTrigger
from harness.startup_recovery import (
    HealthCheckResult,
    RecoveryResult,
    RecoveryStatus,
    StartupRecovery,
)
from harness.state_manager import StateManager

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# 1. Clean startup — all components, no issues
# ---------------------------------------------------------------------------

class TestCleanStartup:
    """Full recovery with real StateManager, mocked Redis/reconciler, all healthy."""

    def test_success_status(self, tmp_path: Path) -> None:
        sm = _make_state_manager(tmp_path)
        sm.set_position("aave-eth", {"protocol": "aave", "amount": 1.5})
        sm.set_strategy_status("STRAT-001", "active")

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
        assert result.errors == []

    def test_state_loaded_correctly(self, tmp_path: Path) -> None:
        sm = _make_state_manager(tmp_path)
        sm.set_position("aave-eth", {"protocol": "aave", "amount": 1.5})
        sm.set_strategy_status("STRAT-001", "active")

        recovery = StartupRecovery(
            state_manager=sm,
            redis_manager=_make_redis_mock(),
            reconciler=_make_reconciler(),
            health_check_fn=_healthy_checks,
        )
        result = recovery.run()

        assert result.state_loaded is True
        # State is still intact after recovery
        assert sm.get_positions()["aave-eth"]["amount"] == 1.5
        assert sm.get_strategy_statuses()["STRAT-001"] == "active"

    def test_zero_unprocessed_messages(self, tmp_path: Path) -> None:
        sm = _make_state_manager(tmp_path)

        recovery = StartupRecovery(
            state_manager=sm,
            redis_manager=_make_redis_mock(orders=[], results=[]),
            reconciler=_make_reconciler(),
            health_check_fn=_healthy_checks,
        )
        result = recovery.run()

        assert result.unprocessed_orders == 0
        assert result.unprocessed_results == 0

    def test_health_checks_all_passing(self, tmp_path: Path) -> None:
        sm = _make_state_manager(tmp_path)

        recovery = StartupRecovery(
            state_manager=sm,
            redis_manager=_make_redis_mock(),
            reconciler=_make_reconciler(),
            health_check_fn=_healthy_checks,
        )
        result = recovery.run()

        assert len(result.health_checks) == 2
        assert all(h.healthy for h in result.health_checks)


# ---------------------------------------------------------------------------
# 2. Recovery with unprocessed messages
# ---------------------------------------------------------------------------

class TestUnprocessedMessages:
    """Recovery finds pending messages in Redis streams."""

    def test_counts_unprocessed_results(self, tmp_path: Path) -> None:
        sm = _make_state_manager(tmp_path)
        sm.set_position("pos-1", {"protocol": "aave", "amount": 2.0})

        unprocessed = [
            {"id": "1", "data": {"orderId": "o1", "status": "confirmed"}},
            {"id": "2", "data": {"orderId": "o2", "status": "failed"}},
            {"id": "3", "data": {"orderId": "o3", "status": "confirmed"}},
        ]
        redis = _make_redis_mock(orders=[], results=unprocessed)

        recovery = StartupRecovery(
            state_manager=sm,
            redis_manager=redis,
            reconciler=_make_reconciler(),
            health_check_fn=_healthy_checks,
        )
        result = recovery.run()

        assert result.unprocessed_results == 3
        assert result.status == RecoveryStatus.SUCCESS

    def test_counts_unprocessed_orders(self, tmp_path: Path) -> None:
        sm = _make_state_manager(tmp_path)

        pending_orders = [
            {"id": "o1", "data": {"action": "supply", "asset": "ETH"}},
        ]
        redis = _make_redis_mock(orders=pending_orders, results=[])

        recovery = StartupRecovery(
            state_manager=sm,
            redis_manager=redis,
            reconciler=_make_reconciler(),
            health_check_fn=_healthy_checks,
        )
        result = recovery.run()

        assert result.unprocessed_orders == 1
        assert result.unprocessed_results == 0

    def test_state_persists_through_recovery(self, tmp_path: Path) -> None:
        """Positions survive the recovery reload cycle."""
        sm = _make_state_manager(tmp_path)
        sm.set_position("aave-usdc", {"protocol": "aave", "amount": 500.0})

        redis = _make_redis_mock(
            orders=[],
            results=[{"id": "r1", "data": {"orderId": "x", "status": "confirmed"}}],
        )

        recovery = StartupRecovery(
            state_manager=sm,
            redis_manager=redis,
            reconciler=_make_reconciler(),
            health_check_fn=_healthy_checks,
        )
        result = recovery.run()

        assert result.state_loaded is True
        assert result.unprocessed_results == 1
        # Position still accessible after recovery reloaded state
        assert "aave-usdc" in sm.get_positions()


# ---------------------------------------------------------------------------
# 3. Recovery triggers diagnostic mode
# ---------------------------------------------------------------------------

class TestRecoveryTriggersDiagnostic:
    """When reconciler finds unresolved discrepancies, diagnostic mode activates."""

    def test_diagnostic_callback_invoked(self, tmp_path: Path) -> None:
        sm = _make_state_manager(tmp_path)
        sm.set_position("pos-1", {"protocol": "aave", "amount": 1.0})

        recon = _make_reconciler(discrepancies=[
            {"type": "balance_mismatch", "auto_fixable": False},
        ])

        diagnostic_entered = {"called": False, "result": None}

        def on_diagnostic(result: RecoveryResult) -> None:
            diagnostic_entered["called"] = True
            diagnostic_entered["result"] = result

        recovery = StartupRecovery(
            state_manager=sm,
            redis_manager=_make_redis_mock(),
            reconciler=recon,
            health_check_fn=_healthy_checks,
            on_diagnostic=on_diagnostic,
        )
        result = recovery.run()

        assert result.status == RecoveryStatus.DIAGNOSTIC
        assert diagnostic_entered["called"] is True

    def test_diagnostic_mode_blocks_trading(self, tmp_path: Path) -> None:
        """Wire real DiagnosticMode as the on_diagnostic callback."""
        sm = _make_state_manager(tmp_path)
        sm.set_position("pos-1", {"protocol": "aave", "amount": 1.0})

        diag = DiagnosticMode(state_manager=sm)
        assert diag.should_block_trading() is False

        recon = _make_reconciler(discrepancies=[
            {"type": "balance_mismatch", "auto_fixable": False},
        ])

        def on_diagnostic(result: RecoveryResult) -> None:
            diag.enter(
                DiagnosticTrigger.STARTUP_RECONCILIATION,
                additional_context={"errors": result.errors},
            )

        recovery = StartupRecovery(
            state_manager=sm,
            redis_manager=_make_redis_mock(),
            reconciler=recon,
            health_check_fn=_healthy_checks,
            on_diagnostic=on_diagnostic,
        )
        result = recovery.run()

        assert result.status == RecoveryStatus.DIAGNOSTIC
        assert diag.is_active is True
        assert diag.should_block_trading() is True

    def test_diagnostic_captures_reconciliation_context(self, tmp_path: Path) -> None:
        """DiagnosticMode state dump includes recovery error context."""
        sm = _make_state_manager(tmp_path)
        sm.set_position("pos-1", {"protocol": "aave", "amount": 1.0})

        diag = DiagnosticMode(state_manager=sm)

        recon = _make_reconciler(discrepancies=[
            {"type": "balance_mismatch", "auto_fixable": False},
            {"type": "missing_position", "auto_fixable": False},
        ])

        def on_diagnostic(result: RecoveryResult) -> None:
            diag.enter(
                DiagnosticTrigger.STARTUP_RECONCILIATION,
                additional_context={
                    "errors": result.errors,
                    "discrepancies_found": result.discrepancies_found,
                },
            )

        recovery = StartupRecovery(
            state_manager=sm,
            redis_manager=_make_redis_mock(),
            reconciler=recon,
            health_check_fn=_healthy_checks,
            on_diagnostic=on_diagnostic,
        )
        recovery.run()

        dump = diag.state_dump
        assert dump is not None
        assert dump.trigger == "startup_reconciliation"
        assert dump.additional_context["discrepancies_found"] == 2

    def test_operational_flags_set_after_diagnostic(self, tmp_path: Path) -> None:
        """StateManager operational flags reflect diagnostic mode entry."""
        sm = _make_state_manager(tmp_path)
        sm.set_position("pos-1", {"protocol": "aave", "amount": 1.0})

        diag = DiagnosticMode(state_manager=sm)

        recon = _make_reconciler(discrepancies=[
            {"type": "balance_mismatch", "auto_fixable": False},
        ])

        def on_diagnostic(result: RecoveryResult) -> None:
            diag.enter(DiagnosticTrigger.STARTUP_RECONCILIATION)

        recovery = StartupRecovery(
            state_manager=sm,
            redis_manager=_make_redis_mock(),
            reconciler=recon,
            health_check_fn=_healthy_checks,
            on_diagnostic=on_diagnostic,
        )
        recovery.run()

        flags = sm.get_operational_flags()
        assert flags["diagnostic_mode"] is True
        assert flags["trading_paused"] is True


# ---------------------------------------------------------------------------
# 4. Recovery timing
# ---------------------------------------------------------------------------

class TestRecoveryTiming:
    """Verify recovery tracks elapsed time and reports it."""

    def test_duration_recorded(self, tmp_path: Path) -> None:
        sm = _make_state_manager(tmp_path)

        recovery = StartupRecovery(
            state_manager=sm,
            redis_manager=_make_redis_mock(),
            reconciler=_make_reconciler(),
            health_check_fn=_healthy_checks,
        )
        result = recovery.run()

        assert result.duration_seconds >= 0  # may round to 0.0 for sub-ms runs
        assert result.duration_seconds < 5  # fast test shouldn't take long

    def test_slow_health_check_reflected_in_timing(self, tmp_path: Path) -> None:
        sm = _make_state_manager(tmp_path)

        def slow_health_check() -> list[HealthCheckResult]:
            time.sleep(0.1)  # simulate slow check
            return [
                HealthCheckResult(protocol="aave", healthy=True, latency_ms=100.0),
            ]

        recovery = StartupRecovery(
            state_manager=sm,
            redis_manager=_make_redis_mock(),
            reconciler=_make_reconciler(),
            health_check_fn=slow_health_check,
        )
        result = recovery.run()

        # Recovery should take at least 100ms due to the slow health check
        assert result.duration_seconds >= 0.1

    def test_timing_data_in_result(self, tmp_path: Path) -> None:
        sm = _make_state_manager(tmp_path)

        recovery = StartupRecovery(
            state_manager=sm,
            redis_manager=_make_redis_mock(),
            reconciler=_make_reconciler(),
            health_check_fn=_healthy_checks,
        )
        result = recovery.run()

        # duration_seconds is a float, properly rounded
        assert isinstance(result.duration_seconds, float)
        assert result.duration_seconds >= 0
