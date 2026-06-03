"""Unit tests for GasAverageTracker (managed-portfolio P1.5c)."""

from decimal import Decimal

import pytest
from decision_engine.gas_tracker import GasAverageTracker


def test_first_sample_is_its_own_average() -> None:
    t = GasAverageTracker(alpha=Decimal("0.2"))
    assert t.update(Decimal("10")) == Decimal("10")


def test_ema_converges_toward_samples() -> None:
    t = GasAverageTracker(alpha=Decimal("0.5"))
    t.update(Decimal("10"))
    avg = t.update(Decimal("20"))  # 0.5*20 + 0.5*10 = 15
    assert avg == Decimal("15")
    assert t.average == Decimal("15")


def test_alpha_zero_rejected() -> None:
    with pytest.raises(ValueError):
        GasAverageTracker(alpha=Decimal("0"))


def test_alpha_one_allowed() -> None:
    t = GasAverageTracker(alpha=Decimal("1"))
    assert t.update(Decimal("5")) == Decimal("5")
