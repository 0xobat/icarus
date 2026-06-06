"""Tests for the USDC depeg circuit breaker — capital protection.

Halts rebalancing when USDC is off-peg beyond a threshold (default 100 bps).
Backward compatible: untripped before the first update (no feed → assume
pegged, today's behaviour).
"""

from __future__ import annotations

from decimal import Decimal

from decision_engine.risk.depeg_breaker import DepegBreaker


def _make_breaker(**kwargs) -> DepegBreaker:
    return DepegBreaker(**kwargs)


class TestDepegDetection:

    def test_untripped_before_first_update(self) -> None:
        b = _make_breaker()
        assert not b.is_tripped
        assert b.current_price is None

    def test_pegged_price_untripped(self) -> None:
        b = _make_breaker()
        b.update(Decimal("1.000"))
        assert not b.is_tripped

    def test_depeg_below_threshold_tripped(self) -> None:
        # $0.985 = 150 bps deviation > 100 bps default → tripped.
        b = _make_breaker()
        b.update(Decimal("0.985"))
        assert b.is_tripped

    def test_boundary_exactly_at_threshold_not_tripped(self) -> None:
        # $0.99 = exactly 100 bps. Strict `>` → NOT tripped.
        b = _make_breaker()
        b.update(Decimal("0.99"))
        assert not b.is_tripped

    def test_one_bps_above_threshold_trips(self) -> None:
        # $0.9899 = 101 bps > 100 → tripped (strict `>` boundary, trip side).
        b = _make_breaker()
        b.update(Decimal("0.9899"))
        assert b.is_tripped
        assert b.deviation_bps == Decimal("101")

    def test_large_deviation_trips(self) -> None:
        # $0.50 = 5000 bps off-peg → very much tripped.
        b = _make_breaker()
        b.update(Decimal("0.50"))
        assert b.is_tripped
        assert b.deviation_bps == Decimal("5000")

    def test_depeg_above_peg_tripped(self) -> None:
        # $1.02 = 200 bps deviation > 100 bps → tripped.
        b = _make_breaker()
        b.update(Decimal("1.02"))
        assert b.is_tripped

    def test_deviation_bps_correct(self) -> None:
        b = _make_breaker()
        b.update(Decimal("0.985"))
        assert b.deviation_bps == Decimal("150")

    def test_deviation_bps_zero_before_update(self) -> None:
        b = _make_breaker()
        assert b.deviation_bps == Decimal("0")

    def test_current_price_tracks_last_update(self) -> None:
        b = _make_breaker()
        b.update(Decimal("0.997"))
        assert b.current_price == Decimal("0.997")

    def test_custom_threshold(self) -> None:
        # At 50 bps threshold, $0.994 (60 bps) trips.
        b = _make_breaker(threshold_bps=50)
        b.update(Decimal("0.994"))
        assert b.is_tripped
        # $0.996 (40 bps) does not.
        b.update(Decimal("0.996"))
        assert not b.is_tripped
