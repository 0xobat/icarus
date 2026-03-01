"""Tests for anomaly detection — MON-004."""

from __future__ import annotations

import time
from decimal import Decimal
from unittest.mock import patch

from monitoring.anomaly_detection import (
    Anomaly,
    AnomalyDetector,
    AnomalySeverity,
    AnomalyType,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _make_detector(**kwargs) -> AnomalyDetector:
    return AnomalyDetector(**kwargs)


# ---------------------------------------------------------------------------
# Anomaly dataclass
# ---------------------------------------------------------------------------
class TestAnomalyDataclass:

    def test_auto_timestamp(self) -> None:
        a = Anomaly(anomaly_type="test", severity="info", message="msg")
        assert a.timestamp > 0

    def test_explicit_timestamp(self) -> None:
        a = Anomaly(anomaly_type="test", severity="info", message="msg", timestamp=42.0)
        assert a.timestamp == 42.0

    def test_default_data(self) -> None:
        a = Anomaly(anomaly_type="test", severity="info", message="msg")
        assert a.data == {}


# ---------------------------------------------------------------------------
# Balance anomaly
# ---------------------------------------------------------------------------
class TestBalanceAnomaly:

    def test_no_anomaly_within_threshold(self) -> None:
        d = _make_detector()
        result = d.check_balance_anomaly(
            expected=Decimal("1000"),
            actual=Decimal("1040"),
            threshold_pct=5.0,
        )
        assert result is None

    def test_anomaly_above_threshold(self) -> None:
        d = _make_detector()
        result = d.check_balance_anomaly(
            expected=Decimal("1000"),
            actual=Decimal("1060"),
            threshold_pct=5.0,
        )
        assert result is not None
        assert result.anomaly_type == AnomalyType.BALANCE_ANOMALY

    def test_warning_severity_moderate_deviation(self) -> None:
        d = _make_detector()
        result = d.check_balance_anomaly(
            expected=Decimal("1000"),
            actual=Decimal("1100"),
        )
        assert result is not None
        assert result.severity == AnomalySeverity.WARNING

    def test_critical_severity_large_deviation(self) -> None:
        d = _make_detector()
        result = d.check_balance_anomaly(
            expected=Decimal("1000"),
            actual=Decimal("1250"),
        )
        assert result is not None
        assert result.severity == AnomalySeverity.CRITICAL

    def test_negative_deviation_detected(self) -> None:
        d = _make_detector()
        result = d.check_balance_anomaly(
            expected=Decimal("1000"),
            actual=Decimal("900"),
        )
        assert result is not None
        assert "deviation" in result.message

    def test_zero_expected_nonzero_actual(self) -> None:
        d = _make_detector()
        result = d.check_balance_anomaly(
            expected=Decimal("0"),
            actual=Decimal("500"),
        )
        assert result is not None
        assert result.severity == AnomalySeverity.CRITICAL

    def test_zero_expected_zero_actual(self) -> None:
        d = _make_detector()
        result = d.check_balance_anomaly(
            expected=Decimal("0"),
            actual=Decimal("0"),
        )
        assert result is None

    def test_data_includes_values(self) -> None:
        d = _make_detector()
        result = d.check_balance_anomaly(
            expected=Decimal("1000"),
            actual=Decimal("800"),
        )
        assert result is not None
        assert result.data["expected"] == "1000"
        assert result.data["actual"] == "800"


# ---------------------------------------------------------------------------
# Gas anomaly
# ---------------------------------------------------------------------------
class TestGasAnomaly:

    def test_no_anomaly_within_threshold(self) -> None:
        d = _make_detector()
        result = d.check_gas_anomaly(
            actual_gas=Decimal("140"),
            estimated_gas=Decimal("100"),
            threshold_pct=50.0,
        )
        assert result is None

    def test_anomaly_above_threshold(self) -> None:
        d = _make_detector()
        result = d.check_gas_anomaly(
            actual_gas=Decimal("160"),
            estimated_gas=Decimal("100"),
            threshold_pct=50.0,
        )
        assert result is not None
        assert result.anomaly_type == AnomalyType.GAS_ANOMALY

    def test_warning_severity(self) -> None:
        d = _make_detector()
        result = d.check_gas_anomaly(
            actual_gas=Decimal("160"),
            estimated_gas=Decimal("100"),
        )
        assert result is not None
        assert result.severity == AnomalySeverity.WARNING

    def test_critical_at_3x(self) -> None:
        d = _make_detector()
        result = d.check_gas_anomaly(
            actual_gas=Decimal("350"),
            estimated_gas=Decimal("100"),
        )
        assert result is not None
        assert result.severity == AnomalySeverity.CRITICAL

    def test_zero_estimate_returns_none(self) -> None:
        d = _make_detector()
        result = d.check_gas_anomaly(
            actual_gas=Decimal("100"),
            estimated_gas=Decimal("0"),
        )
        assert result is None

    def test_negative_estimate_returns_none(self) -> None:
        d = _make_detector()
        result = d.check_gas_anomaly(
            actual_gas=Decimal("100"),
            estimated_gas=Decimal("-10"),
        )
        assert result is None

    def test_under_estimate_no_alert(self) -> None:
        d = _make_detector()
        result = d.check_gas_anomaly(
            actual_gas=Decimal("80"),
            estimated_gas=Decimal("100"),
        )
        assert result is None


# ---------------------------------------------------------------------------
# Execution deviation
# ---------------------------------------------------------------------------
class TestExecutionDeviation:

    def test_no_deviation_within_threshold(self) -> None:
        d = _make_detector()
        result = d.check_execution_deviation(
            expected_price=Decimal("1000"),
            actual_price=Decimal("1005"),
            threshold_bps=100,
        )
        assert result is None

    def test_deviation_above_threshold(self) -> None:
        d = _make_detector()
        result = d.check_execution_deviation(
            expected_price=Decimal("1000"),
            actual_price=Decimal("1020"),
            threshold_bps=100,
        )
        assert result is not None
        assert result.anomaly_type == AnomalyType.EXECUTION_DEVIATION

    def test_warning_severity(self) -> None:
        d = _make_detector()
        result = d.check_execution_deviation(
            expected_price=Decimal("1000"),
            actual_price=Decimal("1020"),
        )
        assert result is not None
        assert result.severity == AnomalySeverity.WARNING

    def test_critical_at_5pct(self) -> None:
        d = _make_detector()
        result = d.check_execution_deviation(
            expected_price=Decimal("1000"),
            actual_price=Decimal("1060"),
        )
        assert result is not None
        assert result.severity == AnomalySeverity.CRITICAL

    def test_negative_deviation(self) -> None:
        d = _make_detector()
        result = d.check_execution_deviation(
            expected_price=Decimal("1000"),
            actual_price=Decimal("970"),
        )
        assert result is not None

    def test_zero_expected_returns_none(self) -> None:
        d = _make_detector()
        result = d.check_execution_deviation(
            expected_price=Decimal("0"),
            actual_price=Decimal("100"),
        )
        assert result is None

    def test_data_includes_bps(self) -> None:
        d = _make_detector()
        result = d.check_execution_deviation(
            expected_price=Decimal("1000"),
            actual_price=Decimal("1020"),
        )
        assert result is not None
        assert "deviation_bps" in result.data


# ---------------------------------------------------------------------------
# Performance degradation
# ---------------------------------------------------------------------------
class TestPerformanceDegradation:

    def test_no_degradation_positive_returns(self) -> None:
        d = _make_detector()
        returns = [Decimal("0.01")] * 7
        result = d.check_performance_degradation("strat_a", returns)
        assert result is None

    def test_degradation_detected(self) -> None:
        d = _make_detector()
        returns = [Decimal("-0.02")] * 7
        result = d.check_performance_degradation("strat_a", returns)
        assert result is not None
        assert result.anomaly_type == AnomalyType.PERFORMANCE_DEGRADATION
        assert "strat_a" in result.message

    def test_warning_severity_moderate_losses(self) -> None:
        d = _make_detector()
        # 4 out of 7 losses, avg negative
        returns = [
            Decimal("-0.03"), Decimal("0.01"), Decimal("-0.02"),
            Decimal("-0.01"), Decimal("0.005"), Decimal("-0.02"),
            Decimal("-0.01"),
        ]
        result = d.check_performance_degradation("strat_b", returns)
        assert result is not None
        assert result.severity == AnomalySeverity.WARNING

    def test_critical_severity_heavy_losses(self) -> None:
        d = _make_detector()
        # >= 80% losses
        returns = [
            Decimal("-0.03"), Decimal("-0.02"), Decimal("-0.01"),
            Decimal("-0.02"), Decimal("-0.03"), Decimal("0.001"),
            Decimal("-0.01"),
        ]
        result = d.check_performance_degradation("strat_c", returns)
        assert result is not None
        assert result.severity == AnomalySeverity.CRITICAL

    def test_empty_returns_no_anomaly(self) -> None:
        d = _make_detector()
        result = d.check_performance_degradation("strat_x", [])
        assert result is None

    def test_lookback_window(self) -> None:
        d = _make_detector()
        # Old positive returns followed by recent losses
        returns = [Decimal("0.05")] * 10 + [Decimal("-0.03")] * 7
        result = d.check_performance_degradation("strat_y", returns, lookback=7)
        assert result is not None

    def test_mixed_returns_no_majority_loss(self) -> None:
        d = _make_detector()
        # Average is slightly negative but less than 50% losses
        returns = [
            Decimal("0.01"), Decimal("0.01"), Decimal("0.01"),
            Decimal("0.01"), Decimal("-0.10"),
        ]
        result = d.check_performance_degradation("strat_z", returns, lookback=5)
        # avg negative but loss_ratio < 0.5
        assert result is None

    def test_data_includes_strategy(self) -> None:
        d = _make_detector()
        returns = [Decimal("-0.02")] * 5
        result = d.check_performance_degradation("my_strat", returns, lookback=5)
        assert result is not None
        assert result.data["strategy"] == "my_strat"


# ---------------------------------------------------------------------------
# Protocol health
# ---------------------------------------------------------------------------
class TestProtocolHealth:

    def test_healthy_protocol_no_anomaly(self) -> None:
        d = _make_detector()
        result = d.check_protocol_health({
            "protocol": "aave",
            "tvl_change_pct": -5,
            "utilization_pct": 70,
        })
        assert result is None

    def test_tvl_drop_triggers_critical(self) -> None:
        d = _make_detector()
        result = d.check_protocol_health({
            "protocol": "aave",
            "tvl_change_pct": -35,
            "utilization_pct": 60,
        })
        assert result is not None
        assert result.severity == AnomalySeverity.CRITICAL
        assert "TVL" in result.message

    def test_high_utilization_triggers_warning(self) -> None:
        d = _make_detector()
        result = d.check_protocol_health({
            "protocol": "compound",
            "tvl_change_pct": 0,
            "utilization_pct": 95,
        })
        assert result is not None
        assert result.severity == AnomalySeverity.WARNING
        assert "utilization" in result.message

    def test_governance_proposals_trigger_warning(self) -> None:
        d = _make_detector()
        result = d.check_protocol_health({
            "protocol": "uniswap",
            "tvl_change_pct": 0,
            "utilization_pct": 50,
            "governance_proposals": 3,
        })
        assert result is not None
        assert "governance" in result.message

    def test_multiple_issues(self) -> None:
        d = _make_detector()
        result = d.check_protocol_health({
            "protocol": "troubled",
            "tvl_change_pct": -40,
            "utilization_pct": 95,
            "governance_proposals": 2,
        })
        assert result is not None
        assert result.severity == AnomalySeverity.CRITICAL

    def test_data_includes_protocol_name(self) -> None:
        d = _make_detector()
        result = d.check_protocol_health({
            "protocol": "lido",
            "tvl_change_pct": -35,
            "utilization_pct": 50,
        })
        assert result is not None
        assert result.data["protocol"] == "lido"


# ---------------------------------------------------------------------------
# Cooldown tracking
# ---------------------------------------------------------------------------
class TestCooldown:

    def test_duplicate_suppressed_within_cooldown(self) -> None:
        d = _make_detector(cooldown_seconds=300)
        r1 = d.check_balance_anomaly(Decimal("1000"), Decimal("800"))
        assert r1 is not None
        r2 = d.check_balance_anomaly(Decimal("1000"), Decimal("700"))
        assert r2 is None  # suppressed by cooldown

    def test_different_types_not_suppressed(self) -> None:
        d = _make_detector(cooldown_seconds=300)
        r1 = d.check_balance_anomaly(Decimal("1000"), Decimal("800"))
        assert r1 is not None
        r2 = d.check_gas_anomaly(Decimal("200"), Decimal("100"))
        assert r2 is not None

    def test_cooldown_expires(self) -> None:
        d = _make_detector(cooldown_seconds=0.1)
        r1 = d.check_balance_anomaly(Decimal("1000"), Decimal("800"))
        assert r1 is not None
        # Simulate cooldown expiry
        with patch.object(time, "time", return_value=time.time() + 1):
            d._last_fired[AnomalyType.BALANCE_ANOMALY] = time.time() - 1
            r2 = d.check_balance_anomaly(Decimal("1000"), Decimal("700"))
            assert r2 is not None

    def test_zero_cooldown_allows_all(self) -> None:
        d = _make_detector(cooldown_seconds=0)
        r1 = d.check_balance_anomaly(Decimal("1000"), Decimal("800"))
        assert r1 is not None
        # Manually clear the cooldown by setting last_fired to the past
        d._last_fired[AnomalyType.BALANCE_ANOMALY] = 0
        r2 = d.check_balance_anomaly(Decimal("1000"), Decimal("700"))
        assert r2 is not None


# ---------------------------------------------------------------------------
# Anomaly history
# ---------------------------------------------------------------------------
class TestHistory:

    def test_anomalies_recorded(self) -> None:
        d = _make_detector(cooldown_seconds=0)
        d._last_fired.clear()
        d.check_balance_anomaly(Decimal("1000"), Decimal("800"))
        assert len(d.anomalies) == 1

    def test_history_is_copy(self) -> None:
        d = _make_detector(cooldown_seconds=0)
        d._last_fired.clear()
        d.check_balance_anomaly(Decimal("1000"), Decimal("800"))
        h = d.anomalies
        h.clear()
        assert len(d.anomalies) == 1

    def test_clear_history(self) -> None:
        d = _make_detector(cooldown_seconds=0)
        d._last_fired.clear()
        d.check_balance_anomaly(Decimal("1000"), Decimal("800"))
        d.clear_history()
        assert len(d.anomalies) == 0
        assert len(d._last_fired) == 0
