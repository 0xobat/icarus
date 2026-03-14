"""Aerodrome stable LP auto-compound — Tier 1 strategy (LP-001).

Evaluates Aerodrome stable pools on Base for LP opportunities. Checks
emission APR, pool TVL, AERO price, and swap liquidity to produce
entry/exit/harvest signals and recommendations.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from monitoring.logger import get_logger

_logger = get_logger("strategy.LP-001", enable_file=False)

from strategies.base import (
    MarketSnapshot,
    Observation,
    Recommendation,
    Signal,
    SignalType,
    StrategyReport,
    TokenPrice,
)

STRATEGY_ID = "LP-001"

# Known stable pairs for Aerodrome on Base
STABLE_PAIRS = frozenset({
    ("USDC", "USDbC"),
    ("USDC", "DAI"),
    ("USDC", "USDT"),
    ("USDbC", "DAI"),
})

# Thresholds
MIN_EMISSION_APR = 0.03  # 3% entry
EXIT_APR = 0.015  # 1.5% exit
MIN_TVL_ENTRY = 500_000.0  # $500K entry
MIN_TVL_EXIT = 200_000.0  # $200K exit (below = exit signal)
AERO_CRASH_THRESHOLD = -0.50  # -50% 24h price drop
HARVEST_MIN_AERO_PRICE = 0.50  # $0.50 min AERO price to harvest
MAX_ALLOCATION_PCT = 0.30  # 30% portfolio
MIN_POSITION_USD = 100.0  # $100 min position


class AerodromeLpStrategy:
    """Tier 1: Aerodrome stable LP analyst.

    Evaluates Aerodrome stable pools by emission APR, TVL, and AERO
    price conditions. Produces StrategyReport with observations, signals,
    and recommendations — does not generate execution orders.
    """

    @property
    def strategy_id(self) -> str:
        """Unique identifier matching STRATEGY.md."""
        return STRATEGY_ID

    @property
    def eval_interval(self) -> timedelta:
        """How often evaluate() should be called."""
        return timedelta(minutes=15)

    @property
    def data_window(self) -> timedelta:
        """How far back the strategy needs market data."""
        return timedelta(hours=24)

    def evaluate(self, snapshot: MarketSnapshot) -> StrategyReport:
        """Analyze Aerodrome stable pools and return a structured report.

        Args:
            snapshot: Pre-sliced market data for this strategy's data_window.

        Returns:
            StrategyReport with observations, signals, and optional recommendation.
        """
        observations: list[Observation] = []
        signals: list[Signal] = []
        recommendation: Recommendation | None = None

        # Find Aerodrome stable pools only
        aero_pools = [
            p for p in snapshot.pools
            if p.protocol == "aerodrome" and self._is_stable_pair(p.pool_id)
        ]
        _logger.debug("Aerodrome pool filter: %d input, %d eligible (protocol=aerodrome, stable pair)",
                       len(snapshot.pools), len(aero_pools))

        # Find AERO price
        aero_price = self._get_aero_price(snapshot.prices)
        aero_price_24h_change = self._get_aero_price_change(snapshot.prices)

        # Observe AERO price
        if aero_price is not None:
            observations.append(Observation(
                metric="aero_price_usd",
                value=f"{aero_price:.4f}",
                context=f"AERO trading at ${aero_price:.4f}",
            ))

        if aero_price_24h_change is not None:
            observations.append(Observation(
                metric="aero_price_change_24h",
                value=f"{aero_price_24h_change:.4f}",
                context=f"AERO 24h price change: {aero_price_24h_change * 100:.1f}%",
            ))

        if not aero_pools:
            observations.append(Observation(
                metric="aerodrome_pool_count",
                value="0",
                context="No Aerodrome pools found in snapshot",
            ))
            return StrategyReport(
                strategy_id=self.strategy_id,
                timestamp=datetime.now(UTC).isoformat(),
                observations=observations,
                signals=signals,
                recommendation=recommendation,
            )

        # Evaluate best pool
        best_pool = max(aero_pools, key=lambda p: p.apy)

        observations.append(Observation(
            metric="aerodrome_best_pool_apr",
            value=f"{best_pool.apy:.4f}",
            context=f"Best Aerodrome pool {best_pool.pool_id} APR: {best_pool.apy * 100:.2f}%",
        ))
        observations.append(Observation(
            metric="aerodrome_best_pool_tvl",
            value=f"{best_pool.tvl:.0f}",
            context=f"Pool {best_pool.pool_id} TVL: ${best_pool.tvl:,.0f}",
        ))
        observations.append(Observation(
            metric="aerodrome_pool_count",
            value=str(len(aero_pools)),
            context=f"{len(aero_pools)} Aerodrome pool(s) in snapshot",
        ))

        # --- Entry signals ---
        entry_apr_met = best_pool.apy >= MIN_EMISSION_APR
        entry_tvl_met = best_pool.tvl >= MIN_TVL_ENTRY
        aero_has_liquidity = aero_price is not None and aero_price > 0
        entry_met = entry_apr_met and entry_tvl_met and aero_has_liquidity
        _logger.debug("Aerodrome entry check: pool=%s apr=%.4f threshold=%.4f tvl=$%.0f tvl_threshold=$%.0f liquidity=%s",
                       best_pool.pool_id, best_pool.apy, MIN_EMISSION_APR, best_pool.tvl, MIN_TVL_ENTRY, aero_has_liquidity)

        if entry_met:
            signals.append(Signal(
                type=SignalType.ENTRY_MET,
                actionable=True,
                details=(
                    f"Pool {best_pool.pool_id} meets entry: "
                    f"APR {best_pool.apy * 100:.2f}% >= {MIN_EMISSION_APR * 100:.1f}%, "
                    f"TVL ${best_pool.tvl:,.0f} >= ${MIN_TVL_ENTRY:,.0f}"
                ),
            ))
            recommendation = Recommendation(
                action="mint_lp",
                reasoning=(
                    f"Aerodrome pool {best_pool.pool_id} has strong emission APR "
                    f"({best_pool.apy * 100:.2f}%) with sufficient TVL (${best_pool.tvl:,.0f}). "
                    f"Recommend entering stable LP position."
                ),
                parameters={
                    "protocol": "aerodrome",
                    "pool_id": best_pool.pool_id,
                    "chain": "base",
                    "max_allocation_pct": MAX_ALLOCATION_PCT,
                    "min_position_usd": MIN_POSITION_USD,
                },
            )
        elif entry_apr_met and not entry_tvl_met:
            signals.append(Signal(
                type=SignalType.THRESHOLD_APPROACHING,
                actionable=False,
                details=(
                    f"Pool {best_pool.pool_id} APR meets entry "
                    f"({best_pool.apy * 100:.2f}%) but TVL too low "
                    f"(${best_pool.tvl:,.0f} < ${MIN_TVL_ENTRY:,.0f})"
                ),
            ))

        # --- Exit signals ---
        exit_low_apr = best_pool.apy < EXIT_APR
        exit_low_tvl = best_pool.tvl < MIN_TVL_EXIT
        exit_aero_crash = (
            aero_price_24h_change is not None
            and aero_price_24h_change < AERO_CRASH_THRESHOLD
        )

        if exit_low_apr or exit_low_tvl or exit_aero_crash:
            reasons = []
            if exit_low_apr:
                reasons.append(
                    f"APR {best_pool.apy * 100:.2f}% < {EXIT_APR * 100:.1f}%"
                )
            if exit_low_tvl:
                reasons.append(
                    f"TVL ${best_pool.tvl:,.0f} < ${MIN_TVL_EXIT:,.0f}"
                )
            if exit_aero_crash:
                reasons.append(
                    f"AERO price dropped {aero_price_24h_change * 100:.1f}% in 24h"  # type: ignore[operator]
                )

            signals.append(Signal(
                type=SignalType.EXIT_MET,
                actionable=True,
                details=f"Exit conditions met: {'; '.join(reasons)}",
            ))
            recommendation = Recommendation(
                action="burn_lp",
                reasoning=f"Exit triggered: {'; '.join(reasons)}. Recommend unwinding LP position.",
                parameters={
                    "protocol": "aerodrome",
                    "pool_id": best_pool.pool_id,
                    "chain": "base",
                },
            )

        # --- Harvest signal ---
        if aero_price is not None and aero_price >= HARVEST_MIN_AERO_PRICE:
            signals.append(Signal(
                type=SignalType.HARVEST_READY,
                actionable=True,
                details=(
                    f"AERO price ${aero_price:.4f} >= "
                    f"${HARVEST_MIN_AERO_PRICE:.2f}, harvest viable"
                ),
            ))
            # Only set harvest recommendation if no exit recommendation
            if recommendation is None or recommendation.action != "burn_lp":
                if recommendation is None:
                    recommendation = Recommendation(
                        action="harvest",
                        reasoning=(
                            f"AERO at ${aero_price:.4f} exceeds harvest "
                            f"threshold. Harvest and compound."
                        ),
                        parameters={
                            "protocol": "aerodrome",
                            "pool_id": best_pool.pool_id,
                            "chain": "base",
                        },
                    )

        return StrategyReport(
            strategy_id=self.strategy_id,
            timestamp=datetime.now(UTC).isoformat(),
            observations=observations,
            signals=signals,
            recommendation=recommendation,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _is_stable_pair(pool_id: str) -> bool:
        """Check if a pool_id corresponds to a known stable pair.

        Extracts token symbols from the pool_id and checks against
        STABLE_PAIRS. Pool IDs use lowercase with hyphens (e.g.
        ``usdc-usdbc-stable``). Pools explicitly tagged as volatile
        are always rejected.
        """
        pid = pool_id.lower()
        # Explicitly volatile pools are never stable
        if pid.endswith("-volatile"):
            return False
        # Strip suffix to extract token pair
        base = pid.replace("-stable", "")
        parts = base.split("-")
        if len(parts) < 2:
            return True  # single-token id, can't determine — allow
        a, b = parts[0].upper(), parts[1].upper()
        return any(
            (pa.upper(), pb.upper()) == (a, b) or (pa.upper(), pb.upper()) == (b, a)
            for pa, pb in STABLE_PAIRS
        )

    def _get_aero_price(self, prices: list[TokenPrice]) -> float | None:
        """Get the most recent AERO price from the snapshot."""
        aero_prices = [p for p in prices if p.token == "AERO"]
        if not aero_prices:
            return None
        return max(aero_prices, key=lambda p: p.timestamp).price

    def _get_aero_price_change(self, prices: list[TokenPrice]) -> float | None:
        """Calculate AERO 24h price change from snapshot prices.

        Looks for prices tagged with source containing '24h_ago' or
        computes from oldest vs newest AERO price in the window.
        """
        aero_prices = [p for p in prices if p.token == "AERO"]
        if len(aero_prices) < 2:
            return None
        sorted_prices = sorted(aero_prices, key=lambda p: p.timestamp)
        oldest = sorted_prices[0].price
        newest = sorted_prices[-1].price
        if oldest == 0:
            return None
        return (newest - oldest) / oldest
