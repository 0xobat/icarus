"""Tests for protocol TVL monitor circuit breaker — RISK-005."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from risk.tvl_monitor import (
    CRITICAL_THRESHOLD,
    WARNING_THRESHOLD,
    TVLMonitor,
    TVLMonitorConfig,
    TVLSnapshot,
)


def _make_monitor(**kwargs) -> TVLMonitor:
    if kwargs:
        config = TVLMonitorConfig(**kwargs)
        return TVLMonitor(config=config)
    return TVLMonitor()


# ---------------------------------------------------------------------------
# TVLSnapshot dataclass
# ---------------------------------------------------------------------------
class TestTVLSnapshot:

    def test_snapshot_fields(self) -> None:
        snap = TVLSnapshot(
            protocol="aave",
            chain="ethereum",
            tvl_usd=Decimal("1000000"),
            source="defillama",
        )
        assert snap.protocol == "aave"
        assert snap.chain == "ethereum"
        assert snap.tvl_usd == Decimal("1000000")
        assert snap.source == "defillama"
        assert isinstance(snap.timestamp, datetime)

    def test_snapshot_default_timestamp(self) -> None:
        before = datetime.now(UTC)
        snap = TVLSnapshot(
            protocol="lido",
            chain="ethereum",
            tvl_usd=Decimal("5000000"),
            source="on-chain",
        )
        after = datetime.now(UTC)
        assert before <= snap.timestamp <= after


# ---------------------------------------------------------------------------
# TVLMonitorConfig
# ---------------------------------------------------------------------------
class TestTVLMonitorConfig:

    def test_default_config(self) -> None:
        config = TVLMonitorConfig()
        assert config.warning_threshold == Decimal("0.15")
        assert config.critical_threshold == Decimal("0.30")
        assert config.window_hours == 24

    def test_custom_config(self) -> None:
        config = TVLMonitorConfig(
            warning_threshold=Decimal("0.10"),
            critical_threshold=Decimal("0.25"),
            window_hours=12,
        )
        assert config.warning_threshold == Decimal("0.10")
        assert config.critical_threshold == Decimal("0.25")
        assert config.window_hours == 12

    def test_default_threshold_constants(self) -> None:
        assert WARNING_THRESHOLD == Decimal("0.15")
        assert CRITICAL_THRESHOLD == Decimal("0.30")


# ---------------------------------------------------------------------------
# Normal TVL tracking
# ---------------------------------------------------------------------------
class TestNormalTracking:

    def test_record_single_snapshot(self) -> None:
        m = _make_monitor()
        m.record_tvl("aave", "ethereum", Decimal("10000000"), "defillama")
        result = m.check_protocol("aave", "ethereum")
        assert result["status"] == "normal"
        assert result["current_tvl"] == Decimal("10000000")
        assert result["peak_tvl"] == Decimal("10000000")
        assert result["drop_pct"] == Decimal(0)

    def test_record_multiple_snapshots_stable(self) -> None:
        m = _make_monitor()
        m.record_tvl("aave", "ethereum", Decimal("10000000"), "defillama")
        m.record_tvl("aave", "ethereum", Decimal("10100000"), "defillama")
        m.record_tvl("aave", "ethereum", Decimal("10050000"), "defillama")
        result = m.check_protocol("aave", "ethereum")
        assert result["status"] == "normal"
        assert result["peak_tvl"] == Decimal("10100000")
        assert result["current_tvl"] == Decimal("10050000")

    def test_is_healthy_normal(self) -> None:
        m = _make_monitor()
        m.record_tvl("aave", "ethereum", Decimal("10000000"), "defillama")
        assert m.is_healthy("aave", "ethereum")

    def test_no_data_check(self) -> None:
        m = _make_monitor()
        result = m.check_protocol("unknown", "ethereum")
        assert result["status"] == "no_data"
        assert result["current_tvl"] is None
        assert result["peak_tvl"] is None
        assert result["drop_pct"] is None

    def test_is_healthy_no_data(self) -> None:
        m = _make_monitor()
        assert m.is_healthy("unknown", "ethereum")


# ---------------------------------------------------------------------------
# Warning threshold (15%)
# ---------------------------------------------------------------------------
class TestWarningThreshold:

    def test_warning_at_15pct_drop(self) -> None:
        m = _make_monitor()
        m.record_tvl("aave", "ethereum", Decimal("10000000"), "defillama")
        m.record_tvl("aave", "ethereum", Decimal("8500000"), "defillama")
        result = m.check_protocol("aave", "ethereum")
        assert result["status"] == "warning"
        assert result["drop_pct"] == Decimal("0.15")

    def test_no_warning_below_15pct(self) -> None:
        m = _make_monitor()
        m.record_tvl("aave", "ethereum", Decimal("10000000"), "defillama")
        m.record_tvl("aave", "ethereum", Decimal("8600000"), "defillama")
        result = m.check_protocol("aave", "ethereum")
        assert result["status"] == "normal"

    def test_is_healthy_false_at_warning(self) -> None:
        m = _make_monitor()
        m.record_tvl("aave", "ethereum", Decimal("10000000"), "defillama")
        m.record_tvl("aave", "ethereum", Decimal("8500000"), "defillama")
        assert not m.is_healthy("aave", "ethereum")

    def test_warning_alert_generated(self) -> None:
        m = _make_monitor()
        m.record_tvl("aave", "ethereum", Decimal("10000000"), "defillama")
        m.record_tvl("aave", "ethereum", Decimal("8500000"), "defillama")
        m.check_protocol("aave", "ethereum")
        warning_alerts = [a for a in m.alerts if a["level"] == "warning"]
        assert len(warning_alerts) == 1
        assert warning_alerts[0]["action"] == "monitor_closely"
        assert warning_alerts[0]["protocol"] == "aave"


# ---------------------------------------------------------------------------
# Critical threshold (30%)
# ---------------------------------------------------------------------------
class TestCriticalThreshold:

    def test_critical_at_30pct_drop(self) -> None:
        m = _make_monitor()
        m.record_tvl("aave", "ethereum", Decimal("10000000"), "defillama")
        m.record_tvl("aave", "ethereum", Decimal("7000000"), "defillama")
        result = m.check_protocol("aave", "ethereum")
        assert result["status"] == "critical"
        assert result["drop_pct"] == Decimal("0.3")

    def test_should_withdraw_at_critical(self) -> None:
        m = _make_monitor()
        m.record_tvl("aave", "ethereum", Decimal("10000000"), "defillama")
        m.record_tvl("aave", "ethereum", Decimal("7000000"), "defillama")
        assert m.should_withdraw("aave", "ethereum")

    def test_should_not_withdraw_below_critical(self) -> None:
        m = _make_monitor()
        m.record_tvl("aave", "ethereum", Decimal("10000000"), "defillama")
        m.record_tvl("aave", "ethereum", Decimal("8000000"), "defillama")
        assert not m.should_withdraw("aave", "ethereum")

    def test_critical_alert_generated(self) -> None:
        m = _make_monitor()
        m.record_tvl("aave", "ethereum", Decimal("10000000"), "defillama")
        m.record_tvl("aave", "ethereum", Decimal("7000000"), "defillama")
        m.check_protocol("aave", "ethereum")
        critical_alerts = [a for a in m.alerts if a["level"] == "critical"]
        assert len(critical_alerts) == 1
        assert critical_alerts[0]["action"] == "emergency_withdrawal"

    def test_should_withdraw_no_data(self) -> None:
        m = _make_monitor()
        assert not m.should_withdraw("unknown", "ethereum")


# ---------------------------------------------------------------------------
# 24h window pruning
# ---------------------------------------------------------------------------
class TestWindowPruning:

    def test_old_snapshots_pruned(self) -> None:
        m = _make_monitor(window_hours=24)
        # Manually insert an old snapshot
        old_time = datetime.now(UTC) - timedelta(hours=25)
        old_snap = TVLSnapshot(
            protocol="aave",
            chain="ethereum",
            tvl_usd=Decimal("10000000"),
            source="defillama",
            timestamp=old_time,
        )
        m._snapshots[("aave", "ethereum")].append(old_snap)
        # Record a new snapshot — should trigger pruning
        m.record_tvl("aave", "ethereum", Decimal("9000000"), "defillama")
        # Old snapshot should be pruned, only new one remains
        result = m.check_protocol("aave", "ethereum")
        assert result["current_tvl"] == Decimal("9000000")
        assert result["peak_tvl"] == Decimal("9000000")
        assert result["drop_pct"] == Decimal(0)

    def test_recent_snapshots_kept(self) -> None:
        m = _make_monitor(window_hours=24)
        # Insert a snapshot from 12 hours ago
        recent_time = datetime.now(UTC) - timedelta(hours=12)
        recent_snap = TVLSnapshot(
            protocol="aave",
            chain="ethereum",
            tvl_usd=Decimal("10000000"),
            source="defillama",
            timestamp=recent_time,
        )
        m._snapshots[("aave", "ethereum")].append(recent_snap)
        # Record a new, lower snapshot
        m.record_tvl("aave", "ethereum", Decimal("7000000"), "defillama")
        # Peak should be from the 12h-old snapshot (still in window)
        result = m.check_protocol("aave", "ethereum")
        assert result["peak_tvl"] == Decimal("10000000")
        assert result["current_tvl"] == Decimal("7000000")


# ---------------------------------------------------------------------------
# Multiple protocols
# ---------------------------------------------------------------------------
class TestMultipleProtocols:

    def test_independent_protocol_tracking(self) -> None:
        m = _make_monitor()
        m.record_tvl("aave", "ethereum", Decimal("10000000"), "defillama")
        m.record_tvl("lido", "ethereum", Decimal("20000000"), "defillama")
        m.record_tvl("aave", "ethereum", Decimal("7000000"), "defillama")

        aave = m.check_protocol("aave", "ethereum")
        lido = m.check_protocol("lido", "ethereum")

        assert aave["status"] == "critical"
        assert lido["status"] == "normal"

    def test_same_protocol_different_chains(self) -> None:
        m = _make_monitor()
        m.record_tvl("aave", "ethereum", Decimal("10000000"), "defillama")
        m.record_tvl("aave", "arbitrum", Decimal("5000000"), "defillama")
        m.record_tvl("aave", "ethereum", Decimal("7000000"), "defillama")

        eth = m.check_protocol("aave", "ethereum")
        arb = m.check_protocol("aave", "arbitrum")

        assert eth["status"] == "critical"
        assert arb["status"] == "normal"

    def test_get_all_statuses(self) -> None:
        m = _make_monitor()
        m.record_tvl("aave", "ethereum", Decimal("10000000"), "defillama")
        m.record_tvl("lido", "ethereum", Decimal("20000000"), "defillama")

        statuses = m.get_all_statuses()
        assert len(statuses) == 2
        assert ("aave", "ethereum") in statuses
        assert ("lido", "ethereum") in statuses


# ---------------------------------------------------------------------------
# Withdrawal targets
# ---------------------------------------------------------------------------
class TestWithdrawalTargets:

    def test_withdrawal_targets_empty_when_healthy(self) -> None:
        m = _make_monitor()
        m.record_tvl("aave", "ethereum", Decimal("10000000"), "defillama")
        assert m.get_withdrawal_targets() == []

    def test_withdrawal_targets_includes_critical(self) -> None:
        m = _make_monitor()
        m.record_tvl("aave", "ethereum", Decimal("10000000"), "defillama")
        m.record_tvl("aave", "ethereum", Decimal("7000000"), "defillama")
        m.record_tvl("lido", "ethereum", Decimal("20000000"), "defillama")

        targets = m.get_withdrawal_targets()
        assert ("aave", "ethereum") in targets
        assert ("lido", "ethereum") not in targets

    def test_multiple_withdrawal_targets(self) -> None:
        m = _make_monitor()
        m.record_tvl("aave", "ethereum", Decimal("10000000"), "defillama")
        m.record_tvl("aave", "ethereum", Decimal("6000000"), "defillama")
        m.record_tvl("lido", "ethereum", Decimal("20000000"), "defillama")
        m.record_tvl("lido", "ethereum", Decimal("13000000"), "defillama")

        targets = m.get_withdrawal_targets()
        assert len(targets) == 2


# ---------------------------------------------------------------------------
# Reset
# ---------------------------------------------------------------------------
class TestReset:

    def test_reset_clears_history(self) -> None:
        m = _make_monitor()
        m.record_tvl("aave", "ethereum", Decimal("10000000"), "defillama")
        m.record_tvl("aave", "ethereum", Decimal("7000000"), "defillama")
        assert m.should_withdraw("aave", "ethereum")

        m.reset("aave", "ethereum")
        result = m.check_protocol("aave", "ethereum")
        assert result["status"] == "no_data"
        assert not m.should_withdraw("aave", "ethereum")

    def test_reset_does_not_affect_other_protocols(self) -> None:
        m = _make_monitor()
        m.record_tvl("aave", "ethereum", Decimal("10000000"), "defillama")
        m.record_tvl("lido", "ethereum", Decimal("20000000"), "defillama")

        m.reset("aave", "ethereum")
        assert m.check_protocol("aave", "ethereum")["status"] == "no_data"
        assert m.check_protocol("lido", "ethereum")["status"] == "normal"

    def test_reset_nonexistent_protocol(self) -> None:
        m = _make_monitor()
        # Should not raise
        m.reset("unknown", "ethereum")


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------
class TestEdgeCases:

    def test_single_snapshot_always_normal(self) -> None:
        m = _make_monitor()
        m.record_tvl("aave", "ethereum", Decimal("10000000"), "defillama")
        result = m.check_protocol("aave", "ethereum")
        assert result["status"] == "normal"
        assert result["drop_pct"] == Decimal(0)

    def test_custom_thresholds(self) -> None:
        m = _make_monitor(
            warning_threshold=Decimal("0.05"),
            critical_threshold=Decimal("0.10"),
        )
        m.record_tvl("aave", "ethereum", Decimal("10000000"), "defillama")
        m.record_tvl("aave", "ethereum", Decimal("8900000"), "defillama")
        result = m.check_protocol("aave", "ethereum")
        assert result["status"] == "critical"

    def test_tvl_increase_stays_normal(self) -> None:
        m = _make_monitor()
        m.record_tvl("aave", "ethereum", Decimal("10000000"), "defillama")
        m.record_tvl("aave", "ethereum", Decimal("12000000"), "defillama")
        result = m.check_protocol("aave", "ethereum")
        assert result["status"] == "normal"
        assert result["drop_pct"] == Decimal(0)

    def test_dual_source_tracking(self) -> None:
        m = _make_monitor()
        m.record_tvl("aave", "ethereum", Decimal("10000000"), "defillama")
        m.record_tvl("aave", "ethereum", Decimal("9900000"), "on-chain")
        result = m.check_protocol("aave", "ethereum")
        assert result["status"] == "normal"
        # Current TVL should be the latest snapshot
        assert result["current_tvl"] == Decimal("9900000")
