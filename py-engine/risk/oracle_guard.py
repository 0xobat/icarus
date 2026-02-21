"""Oracle manipulation guard — multi-source price validation with TWAP fallback."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from monitoring.logger import get_logger

_logger = get_logger("oracle-guard", enable_file=False)

# Defaults
DEFAULT_DEVIATION_THRESHOLD = 0.02  # 2%
DEFAULT_MIN_SOURCES = 2
DEFAULT_TWAP_WINDOW_SECONDS = 300  # 5 minutes
DEFAULT_STALE_THRESHOLD_SECONDS = 60


@dataclass
class PriceSource:
    """A price quote from a single source."""

    source: str
    price: float
    timestamp: float  # epoch seconds


@dataclass
class ValidationResult:
    """Result of oracle price validation."""

    token: str
    valid: bool
    price: float | None = None
    method: str = ""  # "multi_source", "twap_fallback", "single_source"
    sources_used: list[str] = field(default_factory=list)
    deviation: float | None = None
    reason: str = ""


class OracleGuard:
    """Validates prices against multiple independent sources before trading use.

    Features:
    - Every price validated against 2+ independent sources
    - Reject prices with >2% deviation between sources
    - TWAP-based smoothing as fallback when spot prices unreliable
    - Log all price rejections with source details
    - Graceful degradation: don't block when one source temporarily unavailable
    """

    def __init__(
        self,
        *,
        deviation_threshold: float = DEFAULT_DEVIATION_THRESHOLD,
        min_sources: int = DEFAULT_MIN_SOURCES,
        twap_window_seconds: int = DEFAULT_TWAP_WINDOW_SECONDS,
        stale_threshold_seconds: int = DEFAULT_STALE_THRESHOLD_SECONDS,
    ) -> None:
        self._deviation_threshold = deviation_threshold
        self._min_sources = min_sources
        self._twap_window = twap_window_seconds
        self._stale_threshold = stale_threshold_seconds
        # Token -> list of recent price points for TWAP
        self._price_history: dict[str, list[PriceSource]] = {}

    @property
    def deviation_threshold(self) -> float:
        return self._deviation_threshold

    @property
    def min_sources(self) -> int:
        return self._min_sources

    def validate_price(
        self,
        token: str,
        sources: list[PriceSource],
    ) -> ValidationResult:
        """Validate a token price using multiple sources.

        Returns a ValidationResult indicating whether the price is safe to use.
        """
        if not sources:
            _logger.warning(
                "No price sources provided",
                extra={"data": {"token": token}},
            )
            return ValidationResult(
                token=token,
                valid=False,
                reason="no sources provided",
            )

        # Filter out stale sources
        now = time.time()
        fresh = [s for s in sources if (now - s.timestamp) < self._stale_threshold]
        stale_count = len(sources) - len(fresh)
        if stale_count > 0:
            _logger.info(
                "Stale sources filtered",
                extra={"data": {
                    "token": token,
                    "stale_count": stale_count,
                    "fresh_count": len(fresh),
                }},
            )

        # Record all fresh prices for TWAP history
        for s in fresh:
            self._record_price(token, s)

        if len(fresh) >= self._min_sources:
            return self._validate_multi_source(token, fresh)

        if len(fresh) == 1:
            return self._validate_single_with_twap(token, fresh[0])

        # No fresh sources — try TWAP fallback
        return self._twap_fallback(token)

    def _validate_multi_source(
        self,
        token: str,
        sources: list[PriceSource],
    ) -> ValidationResult:
        """Validate with 2+ fresh sources by checking cross-source deviation."""
        prices = [s.price for s in sources]
        source_names = [s.source for s in sources]

        # Check all pairs for deviation
        for i in range(len(prices)):
            for j in range(i + 1, len(prices)):
                deviation = self._compute_deviation(prices[i], prices[j])
                if deviation > self._deviation_threshold:
                    _logger.warning(
                        "Price deviation rejected",
                        extra={"data": {
                            "token": token,
                            "source_a": source_names[i],
                            "price_a": prices[i],
                            "source_b": source_names[j],
                            "price_b": prices[j],
                            "deviation": round(deviation, 6),
                            "threshold": self._deviation_threshold,
                        }},
                    )
                    # Try TWAP fallback instead of rejecting entirely
                    return self._twap_fallback(token, deviation=deviation)

        # All sources agree — use average
        avg_price = sum(prices) / len(prices)
        max_dev = max(
            self._compute_deviation(prices[i], prices[j])
            for i in range(len(prices))
            for j in range(i + 1, len(prices))
        )

        _logger.debug(
            "Multi-source price validated",
            extra={"data": {
                "token": token,
                "price": avg_price,
                "sources": source_names,
                "max_deviation": round(max_dev, 6),
            }},
        )

        return ValidationResult(
            token=token,
            valid=True,
            price=avg_price,
            method="multi_source",
            sources_used=source_names,
            deviation=max_dev,
        )

    def _validate_single_with_twap(
        self,
        token: str,
        source: PriceSource,
    ) -> ValidationResult:
        """Validate a single source by comparing against TWAP history."""
        twap = self._compute_twap(token)
        if twap is not None:
            deviation = self._compute_deviation(source.price, twap)
            if deviation > self._deviation_threshold:
                _logger.warning(
                    "Single source deviates from TWAP",
                    extra={"data": {
                        "token": token,
                        "source": source.source,
                        "spot_price": source.price,
                        "twap": round(twap, 6),
                        "deviation": round(deviation, 6),
                    }},
                )
                # Use TWAP instead
                return ValidationResult(
                    token=token,
                    valid=True,
                    price=twap,
                    method="twap_fallback",
                    sources_used=["twap"],
                    deviation=deviation,
                    reason="single source deviated from TWAP",
                )

        # Single source OK (either no TWAP or within threshold)
        _logger.info(
            "Single source accepted",
            extra={"data": {
                "token": token,
                "source": source.source,
                "price": source.price,
            }},
        )
        return ValidationResult(
            token=token,
            valid=True,
            price=source.price,
            method="single_source",
            sources_used=[source.source],
            deviation=0.0,
            reason="single source — other sources temporarily unavailable",
        )

    def _twap_fallback(
        self,
        token: str,
        *,
        deviation: float | None = None,
    ) -> ValidationResult:
        """Fall back to TWAP when spot prices are unreliable."""
        twap = self._compute_twap(token)
        if twap is not None:
            _logger.info(
                "Using TWAP fallback",
                extra={"data": {
                    "token": token,
                    "twap": round(twap, 6),
                    "window_seconds": self._twap_window,
                }},
            )
            return ValidationResult(
                token=token,
                valid=True,
                price=twap,
                method="twap_fallback",
                sources_used=["twap"],
                deviation=deviation,
                reason="spot prices unreliable, using TWAP",
            )

        _logger.warning(
            "No valid price available",
            extra={"data": {"token": token}},
        )
        return ValidationResult(
            token=token,
            valid=False,
            reason="no fresh sources and no TWAP history available",
        )

    # -- TWAP computation ---------------------------------------------------

    def _record_price(self, token: str, source: PriceSource) -> None:
        """Add a price point to TWAP history."""
        if token not in self._price_history:
            self._price_history[token] = []
        self._price_history[token].append(source)
        # Prune old entries
        cutoff = time.time() - self._twap_window
        self._price_history[token] = [
            p for p in self._price_history[token] if p.timestamp >= cutoff
        ]

    def _compute_twap(self, token: str) -> float | None:
        """Compute TWAP from recorded history. Returns None if no data."""
        history = self._price_history.get(token, [])
        if not history:
            return None
        cutoff = time.time() - self._twap_window
        recent = [p for p in history if p.timestamp >= cutoff]
        if not recent:
            return None
        return sum(p.price for p in recent) / len(recent)

    def get_twap(self, token: str) -> float | None:
        """Public accessor for current TWAP value."""
        return self._compute_twap(token)

    # -- Helpers ------------------------------------------------------------

    @staticmethod
    def _compute_deviation(price_a: float, price_b: float) -> float:
        """Compute relative deviation between two prices."""
        if price_a == 0 and price_b == 0:
            return 0.0
        mid = (price_a + price_b) / 2
        if mid == 0:
            return 1.0
        return abs(price_a - price_b) / mid

    def get_price_history(self, token: str) -> list[PriceSource]:
        """Return current TWAP history for a token."""
        return list(self._price_history.get(token, []))
