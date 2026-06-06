"""Unit tests for the LST depeg breaker (managed-portfolio P3.1)."""

from __future__ import annotations

from decimal import Decimal

from decision_engine.risk.lst_depeg_breaker import LstDepegBreaker


def test_untripped_before_first_update() -> None:
    breaker = LstDepegBreaker(threshold_bps=200)
    assert not breaker.is_tripped
    assert breaker.deviation_bps == Decimal("0")


def test_on_peg_is_untripped() -> None:
    # market == fair → 0 bps deviation.
    breaker = LstDepegBreaker(threshold_bps=200)
    breaker.update(market_price=Decimal("3540"), fair_price=Decimal("3540"))
    assert not breaker.is_tripped


def test_small_deviation_within_threshold_untripped() -> None:
    # market 3525 vs fair 3540 → 42.4 bps < 200 → untripped.
    breaker = LstDepegBreaker(threshold_bps=200)
    breaker.update(market_price=Decimal("3525"), fair_price=Decimal("3540"))
    assert not breaker.is_tripped


def test_single_off_peg_trips_when_sustain_one() -> None:
    # market 3400 vs fair 3540 → ~395 bps > 200 → trips immediately (sustain=1).
    breaker = LstDepegBreaker(threshold_bps=200, sustain=1)
    breaker.update(market_price=Decimal("3400"), fair_price=Decimal("3540"))
    assert breaker.is_tripped
    assert breaker.deviation_bps > Decimal("200")


def test_sustain_requires_consecutive_breaches() -> None:
    breaker = LstDepegBreaker(threshold_bps=200, sustain=3)
    breaker.update(market_price=Decimal("3400"), fair_price=Decimal("3540"))
    assert not breaker.is_tripped  # 1st breach
    breaker.update(market_price=Decimal("3400"), fair_price=Decimal("3540"))
    assert not breaker.is_tripped  # 2nd breach
    breaker.update(market_price=Decimal("3400"), fair_price=Decimal("3540"))
    assert breaker.is_tripped  # 3rd consecutive → tripped


def test_on_peg_reading_resets_the_counter() -> None:
    breaker = LstDepegBreaker(threshold_bps=200, sustain=2)
    breaker.update(market_price=Decimal("3400"), fair_price=Decimal("3540"))  # breach 1
    assert not breaker.is_tripped
    breaker.update(market_price=Decimal("3540"), fair_price=Decimal("3540"))  # on-peg → reset
    assert not breaker.is_tripped
    breaker.update(market_price=Decimal("3400"), fair_price=Decimal("3540"))  # breach 1 again
    assert not breaker.is_tripped  # counter reset → not 2 consecutive


def test_deviation_is_relative_to_fair_not_one() -> None:
    # Confirms the peg is the dynamic fair value, not a fixed 1.0.
    breaker = LstDepegBreaker(threshold_bps=200)
    breaker.update(market_price=Decimal("160"), fair_price=Decimal("168"))  # jitoSOL
    # |160-168|/168 = 476 bps.
    assert breaker.deviation_bps == (Decimal("8") / Decimal("168") * Decimal("10000"))
    assert breaker.is_tripped
