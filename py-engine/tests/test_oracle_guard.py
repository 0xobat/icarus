"""Tests for oracle manipulation guard — RISK-007."""

from __future__ import annotations

import time

from risk.oracle_guard import (
    DEFAULT_DEVIATION_THRESHOLD,
    DEFAULT_MIN_SOURCES,
    OracleGuard,
    PriceSource,
    ValidationResult,
)


def _now() -> float:
    return time.time()


def _source(name: str, price: float, ts: float | None = None) -> PriceSource:
    return PriceSource(source=name, price=price, timestamp=ts or _now())


# ---------------------------------------------------------------------------
# Multi-source validation
# ---------------------------------------------------------------------------

class TestMultiSourceValidation:

    def test_two_sources_within_threshold(self) -> None:
        guard = OracleGuard()
        result = guard.validate_price("ETH", [
            _source("coingecko", 2000.0),
            _source("defillama", 2005.0),
        ])
        assert result.valid is True
        assert result.method == "multi_source"
        assert result.price is not None
        assert abs(result.price - 2002.5) < 0.01

    def test_three_sources_valid(self) -> None:
        guard = OracleGuard()
        result = guard.validate_price("ETH", [
            _source("coingecko", 2000.0),
            _source("defillama", 2003.0),
            _source("chainlink", 2001.0),
        ])
        assert result.valid is True
        assert result.method == "multi_source"
        assert len(result.sources_used) == 3

    def test_two_sources_exceed_threshold_uses_twap(self) -> None:
        guard = OracleGuard()
        # First seed TWAP history
        guard.validate_price("ETH", [
            _source("coingecko", 2000.0),
            _source("defillama", 2000.0),
        ])
        # Now prices diverge beyond 2%
        result = guard.validate_price("ETH", [
            _source("coingecko", 2000.0),
            _source("defillama", 2100.0),  # ~4.9% deviation
        ])
        assert result.valid is True
        assert result.method == "twap_fallback"

    def test_deviation_calculation(self) -> None:
        dev = OracleGuard._compute_deviation(2000.0, 2100.0)
        expected = 100.0 / 2050.0  # ~4.88%
        assert abs(dev - expected) < 0.001

    def test_deviation_both_zero(self) -> None:
        assert OracleGuard._compute_deviation(0.0, 0.0) == 0.0

    def test_deviation_one_zero(self) -> None:
        dev = OracleGuard._compute_deviation(100.0, 0.0)
        assert dev == 2.0  # |100-0| / 50 = 2.0

    def test_sources_listed_in_result(self) -> None:
        guard = OracleGuard()
        result = guard.validate_price("WBTC", [
            _source("coingecko", 40000.0),
            _source("defillama", 40010.0),
        ])
        assert "coingecko" in result.sources_used
        assert "defillama" in result.sources_used


# ---------------------------------------------------------------------------
# Single source with TWAP fallback
# ---------------------------------------------------------------------------

class TestSingleSource:

    def test_single_source_accepted_when_no_twap(self) -> None:
        guard = OracleGuard()
        result = guard.validate_price("ETH", [_source("coingecko", 2000.0)])
        assert result.valid is True
        assert result.method == "single_source"

    def test_single_source_within_twap(self) -> None:
        guard = OracleGuard()
        # Build TWAP history
        guard.validate_price("ETH", [
            _source("coingecko", 2000.0),
            _source("defillama", 2000.0),
        ])
        # Now single source close to TWAP
        result = guard.validate_price("ETH", [_source("coingecko", 2005.0)])
        assert result.valid is True

    def test_single_source_deviating_from_twap_uses_twap(self) -> None:
        guard = OracleGuard()
        # Build TWAP history at ~2000
        for _ in range(5):
            guard.validate_price("ETH", [
                _source("coingecko", 2000.0),
                _source("defillama", 2000.0),
            ])
        # Single source far from TWAP
        result = guard.validate_price("ETH", [_source("coingecko", 2200.0)])
        assert result.valid is True
        assert result.method == "twap_fallback"
        assert result.price is not None
        assert abs(result.price - 2000.0) < 50  # TWAP should be near 2000


# ---------------------------------------------------------------------------
# Stale source filtering
# ---------------------------------------------------------------------------

class TestStaleFiltering:

    def test_stale_sources_filtered(self) -> None:
        guard = OracleGuard(stale_threshold_seconds=30)
        old_ts = _now() - 60  # 60 seconds ago
        result = guard.validate_price("ETH", [
            _source("coingecko", 2000.0, old_ts),
            _source("defillama", 2000.0, _now()),
        ])
        # Only one fresh source → single_source method
        assert result.valid is True

    def test_all_stale_no_twap_invalid(self) -> None:
        guard = OracleGuard(stale_threshold_seconds=30)
        old_ts = _now() - 60
        result = guard.validate_price("NEW_TOKEN", [
            _source("coingecko", 2000.0, old_ts),
            _source("defillama", 2000.0, old_ts),
        ])
        assert result.valid is False
        assert "no fresh sources" in result.reason


# ---------------------------------------------------------------------------
# No sources
# ---------------------------------------------------------------------------

class TestNoSources:

    def test_empty_sources_invalid(self) -> None:
        guard = OracleGuard()
        result = guard.validate_price("ETH", [])
        assert result.valid is False
        assert "no sources" in result.reason


# ---------------------------------------------------------------------------
# TWAP computation
# ---------------------------------------------------------------------------

class TestTwap:

    def test_twap_calculation(self) -> None:
        guard = OracleGuard()
        now = _now()
        guard.validate_price("ETH", [
            _source("a", 2000.0, now - 10),
            _source("b", 2000.0, now - 10),
        ])
        guard.validate_price("ETH", [
            _source("a", 2010.0, now - 5),
            _source("b", 2010.0, now - 5),
        ])
        twap = guard.get_twap("ETH")
        assert twap is not None
        # Should be average of all recorded prices: (2000, 2000, 2010, 2010) / 4 = 2005
        assert abs(twap - 2005.0) < 1.0

    def test_twap_none_for_unknown_token(self) -> None:
        guard = OracleGuard()
        assert guard.get_twap("UNKNOWN") is None

    def test_twap_prunes_old_entries(self) -> None:
        guard = OracleGuard(twap_window_seconds=10)
        old_ts = _now() - 20  # Beyond window
        # Record old price
        guard._record_price("ETH", _source("a", 1000.0, old_ts))
        # Record fresh price
        guard._record_price("ETH", _source("a", 2000.0))
        twap = guard.get_twap("ETH")
        assert twap is not None
        assert abs(twap - 2000.0) < 1.0  # Old entry pruned

    def test_price_history_accessible(self) -> None:
        guard = OracleGuard()
        guard.validate_price("ETH", [
            _source("coingecko", 2000.0),
            _source("defillama", 2000.0),
        ])
        history = guard.get_price_history("ETH")
        assert len(history) >= 2


# ---------------------------------------------------------------------------
# Graceful degradation
# ---------------------------------------------------------------------------

class TestGracefulDegradation:

    def test_one_source_unavailable_still_works(self) -> None:
        """When one source fails, the other source should still be accepted."""
        guard = OracleGuard()
        # Simulate only one source available
        result = guard.validate_price("ETH", [_source("coingecko", 2000.0)])
        assert result.valid is True
        assert result.price == 2000.0

    def test_twap_fallback_on_deviation(self) -> None:
        """When sources disagree, TWAP provides a safe fallback."""
        guard = OracleGuard()
        # Build TWAP
        guard.validate_price("ETH", [
            _source("coingecko", 2000.0),
            _source("defillama", 2000.0),
        ])
        # Sources now wildly disagree
        result = guard.validate_price("ETH", [
            _source("coingecko", 2000.0),
            _source("defillama", 2500.0),  # ~22% deviation
        ])
        assert result.valid is True
        assert result.method == "twap_fallback"


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

class TestConfiguration:

    def test_custom_deviation_threshold(self) -> None:
        guard = OracleGuard(deviation_threshold=0.05)
        assert guard.deviation_threshold == 0.05
        # 4% deviation should now be accepted
        result = guard.validate_price("ETH", [
            _source("coingecko", 2000.0),
            _source("defillama", 2080.0),
        ])
        assert result.valid is True
        assert result.method == "multi_source"

    def test_custom_min_sources(self) -> None:
        guard = OracleGuard(min_sources=3)
        assert guard.min_sources == 3
        # Two sources should be treated as insufficient
        result = guard.validate_price("ETH", [
            _source("coingecko", 2000.0),
            _source("defillama", 2000.0),
        ])
        # With only 2 sources and min_sources=3, should not use multi_source
        assert result.method != "multi_source"

    def test_default_deviation_is_2pct(self) -> None:
        assert DEFAULT_DEVIATION_THRESHOLD == 0.02

    def test_default_min_sources_is_2(self) -> None:
        assert DEFAULT_MIN_SOURCES == 2


# ---------------------------------------------------------------------------
# Price rejection logging
# ---------------------------------------------------------------------------

class TestRejectionLogging:

    def test_deviation_result_includes_details(self) -> None:
        guard = OracleGuard()
        # Build TWAP first
        guard.validate_price("ETH", [
            _source("coingecko", 2000.0),
            _source("defillama", 2000.0),
        ])
        # Trigger deviation
        result = guard.validate_price("ETH", [
            _source("coingecko", 2000.0),
            _source("defillama", 2100.0),
        ])
        assert result.deviation is not None
        assert result.deviation > DEFAULT_DEVIATION_THRESHOLD


# ---------------------------------------------------------------------------
# ValidationResult dataclass
# ---------------------------------------------------------------------------

class TestValidationResult:

    def test_defaults(self) -> None:
        r = ValidationResult(token="ETH", valid=True)
        assert r.price is None
        assert r.method == ""
        assert r.sources_used == []
        assert r.deviation is None
        assert r.reason == ""
