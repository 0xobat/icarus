"""Oracle manipulation guard — multi-source price validation (RISK-007).

Wraps PriceFeedManager's cross-source validation to provide a risk gate
that rejects operations when price sources deviate by >2%. Works in
conjunction with price feed staleness detection.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from data.price_feed import PriceFeedManager, PriceResult
from monitoring.logger import get_logger

_logger = get_logger("oracle-guard", enable_file=False)


@dataclass
class DeviationDetail:
    """Record of a price deviation between sources for a single token."""

    token: str
    alchemy_price: float
    defillama_price: float
    deviation_pct: float
    threshold_pct: float
    exceeded: bool


@dataclass
class OracleCheckResult:
    """Result of an oracle guard validation."""

    safe: bool
    deviations: list[DeviationDetail] = field(default_factory=list)
    stale: bool = False
    reason: str = "ok"


class OracleGuard:
    """Multi-source oracle manipulation guard.

    Validates that prices from Alchemy and DefiLlama agree within a
    configurable deviation threshold (default 2%). Rejects all operations
    when any token exceeds the threshold or when prices are stale.

    Args:
        price_feed: The PriceFeedManager instance to query.
        deviation_threshold: Max allowed deviation as a fraction (default 0.02 = 2%).
    """

    def __init__(
        self,
        price_feed: PriceFeedManager,
        *,
        deviation_threshold: float | None = None,
    ) -> None:
        self._price_feed = price_feed
        self._threshold = (
            deviation_threshold
            if deviation_threshold is not None
            else price_feed._deviation_threshold
        )
        self._last_deviations: dict[str, DeviationDetail] = {}

    def validate_prices(
        self,
        alchemy_results: dict[str, PriceResult],
        defillama_results: dict[str, PriceResult],
    ) -> OracleCheckResult:
        """Check cross-source deviation for all tokens with dual-source prices.

        Args:
            alchemy_results: Prices from Alchemy.
            defillama_results: Prices from DefiLlama.

        Returns:
            OracleCheckResult with safe=False if any deviation exceeds threshold.
        """
        deviations: list[DeviationDetail] = []
        any_exceeded = False

        common_tokens = set(alchemy_results) & set(defillama_results)

        for token in sorted(common_tokens):
            a_price = alchemy_results[token].price_usd
            d_price = defillama_results[token].price_usd

            mid = (a_price + d_price) / 2
            if mid == 0:
                continue

            deviation = abs(a_price - d_price) / mid
            exceeded = deviation > self._threshold

            detail = DeviationDetail(
                token=token,
                alchemy_price=a_price,
                defillama_price=d_price,
                deviation_pct=round(deviation * 100, 4),
                threshold_pct=round(self._threshold * 100, 4),
                exceeded=exceeded,
            )
            deviations.append(detail)
            self._last_deviations[token] = detail

            if exceeded:
                any_exceeded = True
                _logger.warning(
                    "Oracle deviation exceeded threshold",
                    extra={"data": {
                        "token": token,
                        "alchemy_price": a_price,
                        "defillama_price": d_price,
                        "deviation_pct": detail.deviation_pct,
                        "threshold_pct": detail.threshold_pct,
                    }},
                )

        # Check staleness
        stale = self._price_feed.is_any_stale()
        if stale:
            _logger.warning(
                "Oracle guard: stale prices detected",
                extra={"data": {"stale": True}},
            )

        safe = not any_exceeded and not stale

        reason = "ok"
        if any_exceeded:
            exceeded_tokens = [d.token for d in deviations if d.exceeded]
            reason = f"price deviation exceeded for: {', '.join(exceeded_tokens)}"
        elif stale:
            reason = "stale prices detected"

        result = OracleCheckResult(
            safe=safe,
            deviations=deviations,
            stale=stale,
            reason=reason,
        )

        if not safe:
            _logger.warning(
                "Oracle guard check failed",
                extra={"data": {
                    "safe": False,
                    "reason": reason,
                    "deviation_count": len(deviations),
                    "exceeded_count": sum(1 for d in deviations if d.exceeded),
                    "stale": stale,
                }},
            )
        else:
            _logger.debug(
                "Oracle guard check passed",
                extra={"data": {
                    "token_count": len(deviations),
                }},
            )

        return result

    def get_deviations(self) -> dict[str, float]:
        """Return last known deviation percentages per token for audit logging.

        Returns:
            Dict of token symbol to deviation percentage.
        """
        return {
            token: detail.deviation_pct
            for token, detail in self._last_deviations.items()
        }

    def check(self) -> OracleCheckResult:
        """Convenience method: fetch prices from both sources and validate.

        Fetches from Alchemy and DefiLlama directly, then runs validation.
        Handles fetch failures gracefully — a single-source failure makes
        cross-validation impossible, which is logged but not treated as unsafe
        (the system falls back to whatever source is available).

        Returns:
            OracleCheckResult indicating whether prices are safe to use.
        """
        alchemy_results: dict[str, PriceResult] = {}
        defillama_results: dict[str, PriceResult] = {}

        try:
            alchemy_results = self._price_feed._fetch_alchemy()
        except Exception as e:
            _logger.warning(
                "Oracle guard: Alchemy fetch failed",
                extra={"data": {"error": str(e)}},
            )

        try:
            defillama_results = self._price_feed._fetch_defillama()
        except Exception as e:
            _logger.warning(
                "Oracle guard: DefiLlama fetch failed",
                extra={"data": {"error": str(e)}},
            )

        if not alchemy_results or not defillama_results:
            stale = self._price_feed.is_any_stale()
            if stale:
                return OracleCheckResult(
                    safe=False,
                    stale=True,
                    reason="stale prices and single-source only",
                )
            return OracleCheckResult(
                safe=True,
                reason="single source available, cross-validation skipped",
            )

        return self.validate_prices(alchemy_results, defillama_results)
