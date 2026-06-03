"""Rolling gas-average tracker (EMA) for the gas-spike breaker feed (P1.5c)."""

from __future__ import annotations

from decimal import Decimal


class GasAverageTracker:
    """Exponential moving average of gas price (gwei). First sample seeds it."""

    def __init__(self, *, alpha: Decimal = Decimal("0.1")) -> None:
        if not (Decimal("0") < alpha <= Decimal("1")):
            raise ValueError(f"alpha must be in (0,1], got {alpha}")
        self._alpha = alpha
        self._avg: Decimal | None = None

    @property
    def average(self) -> Decimal:
        return self._avg if self._avg is not None else Decimal("0")

    def update(self, sample: Decimal) -> Decimal:
        self._avg = sample if self._avg is None else self._alpha * sample + (Decimal("1") - self._alpha) * self._avg
        return self._avg


__all__ = ["GasAverageTracker"]
