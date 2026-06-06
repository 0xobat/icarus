"""USDC depeg circuit breaker — halt rebalancing during a stablecoin depeg.

Capital protection. When USDC is priced from a live feed (Chainlink USDC/USD)
and drifts off-peg beyond a threshold (default 100 bps), this breaker trips and
the depeg checker halts every rebalance — every managed rebalance touches USDC,
so a halt-all is the correct (and conservative) policy.

Why a breaker, not just a price: if USDC drops to $0.97 and we only update the
price, the stable sleeve shrinks, crypto weight rises, and the planner's instinct
is to sell good crypto and buy more of the depegging stable. The breaker prevents
that by halting rebalancing while off-peg.

Backward compatible / fail-safe-open: before the first `update` (no feed
configured → no USDC price in the snapshot → `update` is never called) the
breaker reports untripped. That is today's behaviour: with no USDC feed, USDC is
pinned to $1 and rebalancing proceeds. Only a live feed reporting an off-peg
price can trip it.
"""

from __future__ import annotations

from decimal import Decimal

from icarus.logging import get_logger

_logger = get_logger("depeg-breaker", enable_file=False)

DEFAULT_THRESHOLD_BPS = 100


class DepegBreaker:
    """USDC depeg circuit breaker.

    - `update(usdc_price)` stores the latest live USDC/USD price.
    - `is_tripped` is True when `abs(price - peg) / peg * 10_000 > threshold_bps`
      (strict `>`: a deviation exactly at the threshold is NOT tripped).
    - Untripped before the first `update` (no price → assume pegged).
    """

    def __init__(
        self,
        *,
        threshold_bps: int = DEFAULT_THRESHOLD_BPS,
        peg: Decimal = Decimal("1"),
    ) -> None:
        self._threshold_bps = Decimal(threshold_bps)
        self._peg = peg
        self._current_price: Decimal | None = None

    @property
    def current_price(self) -> Decimal | None:
        """Most recent USDC/USD price, or None before the first update."""
        return self._current_price

    @property
    def threshold_bps(self) -> int:
        """The depeg threshold in basis points."""
        return int(self._threshold_bps)

    @property
    def deviation_bps(self) -> Decimal:
        """Absolute deviation from peg in basis points (0 before first update)."""
        if self._current_price is None:
            return Decimal("0")
        return abs(self._current_price - self._peg) / self._peg * Decimal(10_000)

    @property
    def is_tripped(self) -> bool:
        """True when the live price is off-peg beyond the threshold (strict >)."""
        if self._current_price is None:
            return False
        return self.deviation_bps > self._threshold_bps

    def update(self, usdc_price: Decimal) -> None:
        """Store the latest USDC/USD price and log on a peg state change."""
        was_tripped = self.is_tripped
        self._current_price = usdc_price
        if self.is_tripped and not was_tripped:
            _logger.warning(
                "Depeg breaker TRIPPED",
                extra={"data": {
                    "price": str(usdc_price),
                    "deviation_bps": str(self.deviation_bps),
                    "threshold_bps": int(self._threshold_bps),
                }},
            )
        elif was_tripped and not self.is_tripped:
            _logger.info(
                "Depeg breaker RECOVERED",
                extra={"data": {
                    "price": str(usdc_price),
                    "deviation_bps": str(self.deviation_bps),
                    "threshold_bps": int(self._threshold_bps),
                }},
            )


__all__ = ["DEFAULT_THRESHOLD_BPS", "DepegBreaker"]
