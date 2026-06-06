"""LST depeg circuit breaker — detect a liquid-staking token trading off its
rate-implied fair value (managed-portfolio P3.1).

Unlike the USDC breaker (fixed $1 peg), an LST's fair value is dynamic:
`fair = exchange_rate * underlying` (the P2.4/P2.6 pricing). A depeg is the
*market* price diverging from that fair value -- the LST discounting vs. its
redemption value (a liquidity/confidence event). The breaker trips on a
**sustained** divergence (the design's 1-2% band), requiring `sustain`
consecutive off-peg observations so a transient quote blip does not de-risk the
whole sleeve.

Fail-safe-open: untripped before the first `update` (no LST price feed → never
trips), mirroring the USDC depeg breaker. The portfolio-health monitor (P3.2)
consults this to de-risk the affected sleeve; it is not a halt-all gate.
"""

from __future__ import annotations

from decimal import Decimal

from icarus.logging import get_logger

_logger = get_logger("lst-depeg-breaker", enable_file=False)

DEFAULT_THRESHOLD_BPS = 200  # 2% -- mid of the design's 1-2% band


class LstDepegBreaker:
    """Trips when an LST's market price diverges from fair value, sustained.

    - `update(market_price, fair_price)` records one observation; an off-peg
      reading (`deviation_bps > threshold`) increments a consecutive counter,
      an on-peg reading resets it to 0.
    - `is_tripped` when the consecutive off-peg count reaches `sustain`.
    - `deviation_bps` is the latest `|market - fair| / fair * 10_000` (0 before
      the first update).
    """

    def __init__(self, *, threshold_bps: int = DEFAULT_THRESHOLD_BPS, sustain: int = 1) -> None:
        self._threshold_bps = Decimal(threshold_bps)
        self._sustain = max(1, sustain)
        self._deviation_bps = Decimal("0")
        self._consecutive = 0
        self._seen = False

    @property
    def threshold_bps(self) -> int:
        return int(self._threshold_bps)

    @property
    def deviation_bps(self) -> Decimal:
        """Latest absolute deviation from fair value in bps (0 before first update)."""
        return self._deviation_bps

    @property
    def is_tripped(self) -> bool:
        return self._seen and self._consecutive >= self._sustain

    def update(self, *, market_price: Decimal, fair_price: Decimal) -> None:
        """Record one (market, fair) observation and update the breach counter."""
        if fair_price <= 0:
            raise ValueError(f"fair_price must be positive, got {fair_price}")
        self._seen = True
        self._deviation_bps = abs(market_price - fair_price) / fair_price * Decimal(10_000)
        was_tripped = self.is_tripped
        if self._deviation_bps > self._threshold_bps:
            self._consecutive += 1
        else:
            self._consecutive = 0
        if self.is_tripped and not was_tripped:
            _logger.warning(
                "LST depeg breaker TRIPPED",
                extra={"data": {
                    "market_price": str(market_price),
                    "fair_price": str(fair_price),
                    "deviation_bps": str(self._deviation_bps),
                    "threshold_bps": int(self._threshold_bps),
                    "consecutive": self._consecutive,
                }},
            )


__all__ = ["DEFAULT_THRESHOLD_BPS", "LstDepegBreaker"]
