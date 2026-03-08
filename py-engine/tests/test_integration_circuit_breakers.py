"""Integration tests — circuit breakers + diagnostic mode working together."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from harness.diagnostic_mode import DiagnosticMode, DiagnosticTrigger
from harness.state_manager import StateManager
from risk.drawdown_breaker import DrawdownBreaker
from risk.gas_spike_breaker import GasSpikeBreaker
from risk.tx_failure_monitor import TxFailureMonitor


def _make_state_manager(tmp_path: Path) -> StateManager:
    return StateManager(state_path=tmp_path / "state.json")


# ---------------------------------------------------------------------------
# 1. Drawdown circuit breaker integration
# ---------------------------------------------------------------------------
class TestDrawdownIntegration:

    def test_normal_through_warning_to_critical(self, tmp_path: Path) -> None:
        sm = _make_state_manager(tmp_path)
        diag = DiagnosticMode(state_manager=sm)
        breaker = DrawdownBreaker(initial_value=Decimal("10000"))

        # 14% drop — still normal
        state = breaker.update(Decimal("8600"))
        assert state.level == "normal"
        assert breaker.can_open_position()
        assert not breaker.should_unwind_all()

        # 15% drop — warning level
        state = breaker.update(Decimal("8500"))
        assert state.level == "warning"
        assert not breaker.can_open_position()
        assert not breaker.should_unwind_all()

        # >20% drop — critical level
        state = breaker.update(Decimal("7999"))
        assert state.level == "critical"
        assert not breaker.can_open_position()
        assert breaker.should_unwind_all()

        # Enter diagnostic mode on critical drawdown
        dump = diag.enter(
            DiagnosticTrigger.CRITICAL_CIRCUIT_BREAKER,
            additional_context={
                "drawdown_pct": str(breaker.drawdown_pct),
                "peak_value": str(breaker.peak_value),
            },
        )
        assert diag.is_active
        assert diag.should_block_trading()
        assert dump.trigger == "critical_circuit_breaker"
        assert Decimal(dump.additional_context["drawdown_pct"]) > Decimal("0.2")

        # Verify manual restart is required to reset breaker
        assert breaker.trading_halted
        # Value recovery does NOT auto-clear critical halt
        breaker.update(Decimal("9500"))
        assert breaker.trading_halted

        # Manual restart clears halt
        assert breaker.manual_restart()
        assert not breaker.trading_halted
        assert breaker.can_open_position()

        # Also exit diagnostic mode manually
        diag.exit()
        assert not diag.is_active
        assert not diag.should_block_trading()

    def test_drawdown_alerts_track_escalation(self, tmp_path: Path) -> None:
        breaker = DrawdownBreaker(initial_value=Decimal("10000"))

        # Gradually decline through both thresholds
        breaker.update(Decimal("8600"))  # 14% — no alert
        assert len(breaker.alerts) == 0

        breaker.update(Decimal("8500"))  # 15% — warning alert
        assert len(breaker.alerts) == 1
        assert breaker.alerts[0]["level"] == "warning"

        breaker.update(Decimal("7999"))  # >20% — critical alert
        assert len(breaker.alerts) == 2
        assert breaker.alerts[1]["level"] == "critical"


# ---------------------------------------------------------------------------
# 2. Gas spike circuit breaker integration
# ---------------------------------------------------------------------------
class TestGasSpikeIntegration:

    def test_spike_blocks_non_urgent_allows_urgent(self, tmp_path: Path) -> None:
        breaker = GasSpikeBreaker(spike_multiplier=Decimal("3"))

        # Gas spike: 100 vs average 30 = 3.33x > 3x threshold
        state = breaker.update(Decimal("100"), Decimal("30"))
        assert state.is_active

        # Non-urgent operations blocked
        assert not breaker.is_operation_allowed("supply")
        assert not breaker.is_operation_allowed("swap")
        assert not breaker.is_operation_allowed("rebalancing")

        # Urgent operations allowed
        assert breaker.is_operation_allowed("stop_loss")
        assert breaker.is_operation_allowed("emergency_withdrawal")

    def test_queue_and_release_on_gas_drop(self, tmp_path: Path) -> None:
        breaker = GasSpikeBreaker(spike_multiplier=Decimal("3"))

        # Activate spike
        breaker.update(Decimal("100"), Decimal("30"))
        assert breaker.is_active

        # Queue a non-urgent operation
        queued = breaker.queue_operation(
            operation_id="op-supply-1",
            operation_type="supply",
            payload={"asset": "USDC", "amount": "1000"},
            strategy_id="STRAT-001",
        )
        assert queued is not None
        assert queued.operation_type == "supply"
        assert len(breaker.queued_operations) == 1

        # Gas drops: 80 vs average 30 = 2.67x < 3x threshold
        state = breaker.update(Decimal("80"), Decimal("30"))
        assert not state.is_active

        # Release queued operations
        released = breaker.release_queue()
        assert len(released) == 1
        assert released[0].operation_id == "op-supply-1"
        assert len(breaker.queued_operations) == 0

    def test_spike_alerts_on_activation_and_deactivation(self) -> None:
        breaker = GasSpikeBreaker(spike_multiplier=Decimal("3"))

        breaker.update(Decimal("100"), Decimal("30"))  # activate
        assert len(breaker.alerts) == 1
        assert breaker.alerts[0]["event"] == "gas_spike_activated"

        breaker.update(Decimal("80"), Decimal("30"))  # deactivate
        assert len(breaker.alerts) == 2
        assert breaker.alerts[1]["event"] == "gas_spike_deactivated"
        assert breaker.alerts[1]["queued_released"] == 0


# ---------------------------------------------------------------------------
# 3. TX failure rate monitor integration
# ---------------------------------------------------------------------------
class TestTxFailureMonitorIntegration:

    def test_threshold_breach_triggers_diagnostic(self, tmp_path: Path) -> None:
        sm = _make_state_manager(tmp_path)
        diag = DiagnosticMode(state_manager=sm)
        # threshold=2 so 3rd failure triggers (count > threshold)
        monitor = TxFailureMonitor(window_seconds=3600, failure_threshold=2)

        # 2 failures — still operational
        monitor.record_failure(tx_id="tx1", reason="revert", details="bad approval")
        monitor.record_failure(tx_id="tx2", reason="timeout", details="RPC slow")
        assert not monitor.is_paused
        assert monitor.can_execute()

        # 3rd failure — threshold breached
        monitor.record_failure(tx_id="tx3", reason="out_of_gas", details="gas estimate low")
        assert monitor.is_paused
        assert monitor.diagnostic_mode
        assert not monitor.can_execute()

        # Enter diagnostic mode
        dump = diag.enter(
            DiagnosticTrigger.CRITICAL_CIRCUIT_BREAKER,
            additional_context={
                "failures_in_window": monitor.get_failure_count(),
                "breakdown": monitor.get_category_breakdown(),
            },
        )
        assert diag.is_active
        assert diag.should_block_trading()
        assert dump.additional_context["failures_in_window"] == 3

    def test_failure_categorization(self) -> None:
        monitor = TxFailureMonitor(window_seconds=3600, failure_threshold=10)

        # Record mix of parameter and systemic errors
        monitor.record_failure(tx_id="tx1", reason="revert")
        monitor.record_failure(tx_id="tx2", reason="out_of_gas")
        monitor.record_failure(tx_id="tx3", reason="timeout")
        monitor.record_failure(tx_id="tx4", reason="network_error")

        breakdown = monitor.get_category_breakdown()
        assert breakdown["parameter"] == 2  # revert + out_of_gas
        assert breakdown["systemic"] == 2   # timeout + network_error

    def test_manual_resume_required(self, tmp_path: Path) -> None:
        sm = _make_state_manager(tmp_path)
        diag = DiagnosticMode(state_manager=sm)
        monitor = TxFailureMonitor(window_seconds=3600, failure_threshold=2)

        # Trigger threshold breach
        for i in range(3):
            monitor.record_failure(tx_id=f"tx{i}", reason="revert")
        assert monitor.is_paused

        # Enter diagnostic mode
        diag.enter(DiagnosticTrigger.CRITICAL_CIRCUIT_BREAKER)
        assert diag.should_block_trading()

        # Manual resume clears monitor pause
        assert monitor.manual_resume()
        assert not monitor.is_paused
        assert not monitor.diagnostic_mode
        assert monitor.can_execute()

        # Also exit diagnostic mode
        diag.exit()
        assert not diag.should_block_trading()


# ---------------------------------------------------------------------------
# 4. Combined scenario — all breakers working together
# ---------------------------------------------------------------------------
class TestCombinedScenario:

    def test_gas_spike_and_tx_failures_independent(self, tmp_path: Path) -> None:
        sm = _make_state_manager(tmp_path)
        diag = DiagnosticMode(state_manager=sm)
        gas_breaker = GasSpikeBreaker(spike_multiplier=Decimal("3"))
        tx_monitor = TxFailureMonitor(window_seconds=3600, failure_threshold=2)

        # --- Start: normal state ---
        assert not gas_breaker.is_active
        assert not tx_monitor.is_paused
        assert not diag.is_active

        # --- Gas spike activates → supply blocked ---
        gas_breaker.update(Decimal("100"), Decimal("30"))
        assert gas_breaker.is_active
        assert not gas_breaker.is_operation_allowed("supply")
        assert gas_breaker.is_operation_allowed("stop_loss")

        # TX monitor still fine at this point
        assert tx_monitor.can_execute()

        # --- Meanwhile, 3 TX failures → diagnostic mode ---
        tx_monitor.record_failure(tx_id="tx1", reason="revert")
        tx_monitor.record_failure(tx_id="tx2", reason="timeout")
        tx_monitor.record_failure(tx_id="tx3", reason="out_of_gas")
        assert tx_monitor.is_paused
        assert tx_monitor.diagnostic_mode

        # Enter diagnostic mode due to TX failures
        diag.enter(
            DiagnosticTrigger.CRITICAL_CIRCUIT_BREAKER,
            additional_context={"source": "tx_failure_monitor"},
        )
        assert diag.is_active
        assert diag.should_block_trading()

        # --- Verify both breakers are independently tracked ---
        # Gas breaker is still active (independent of diagnostic mode)
        assert gas_breaker.is_active
        # TX monitor is still paused (independent of gas breaker)
        assert tx_monitor.is_paused

        # --- Diagnostic mode blocks ALL operations regardless of gas state ---
        # Even if gas normalizes, diagnostic mode still blocks trading
        gas_breaker.update(Decimal("80"), Decimal("30"))
        assert not gas_breaker.is_active  # gas recovered
        assert gas_breaker.is_operation_allowed("supply")  # gas allows it
        assert diag.should_block_trading()  # but diagnostic mode blocks all

        # State manager flags confirm trading is paused
        flags = sm.get_operational_flags()
        assert flags["trading_paused"] is True
        assert flags["diagnostic_mode"] is True

    def test_all_three_breakers_simultaneous(self, tmp_path: Path) -> None:
        sm = _make_state_manager(tmp_path)
        diag = DiagnosticMode(state_manager=sm)
        drawdown = DrawdownBreaker(initial_value=Decimal("10000"))
        gas = GasSpikeBreaker(spike_multiplier=Decimal("3"))
        tx_mon = TxFailureMonitor(window_seconds=3600, failure_threshold=2)

        # Drawdown hits critical (>20%)
        drawdown.update(Decimal("7999"))
        assert drawdown.should_unwind_all()

        # Gas spike active
        gas.update(Decimal("100"), Decimal("30"))
        assert gas.is_active

        # TX failures breach threshold
        for i in range(3):
            tx_mon.record_failure(tx_id=f"tx{i}", reason="revert")
        assert tx_mon.is_paused

        # Diagnostic mode entered
        diag.enter(DiagnosticTrigger.CRITICAL_CIRCUIT_BREAKER)
        assert diag.should_block_trading()

        # All three are independently in their triggered state
        assert drawdown.level == "critical"
        assert gas.is_active
        assert tx_mon.diagnostic_mode

        # Each requires its own reset path
        drawdown.manual_restart()
        assert not drawdown.trading_halted
        assert gas.is_active       # gas unchanged
        assert tx_mon.is_paused    # tx unchanged

        tx_mon.manual_resume()
        assert not tx_mon.is_paused
        assert gas.is_active       # gas unchanged

        gas.update(Decimal("80"), Decimal("30"))
        assert not gas.is_active   # gas recovered

        # Exit diagnostic mode last
        diag.exit()
        assert not diag.should_block_trading()

        # Everything is now clear
        assert drawdown.can_open_position()
        assert gas.is_operation_allowed("supply")
        assert tx_mon.can_execute()
        assert not diag.is_active

    def test_diagnostic_mode_state_persists(self, tmp_path: Path) -> None:
        """Diagnostic mode persists to disk via state manager."""
        sm = _make_state_manager(tmp_path)
        diag = DiagnosticMode(state_manager=sm)

        diag.enter(DiagnosticTrigger.CRITICAL_CIRCUIT_BREAKER)

        # Reload state from disk
        sm2 = StateManager(state_path=tmp_path / "state.json")
        flags = sm2.get_operational_flags()
        assert flags["diagnostic_mode"] is True
        assert flags["trading_paused"] is True
