"""Strategy engine — signal generation, portfolio optimization."""

from __future__ import annotations

from strategies.aave_lending import AaveLendingStrategy
from strategies.aerodrome_lp import AerodromeLpStrategy

__all__ = ["AaveLendingStrategy", "AerodromeLpStrategy"]
