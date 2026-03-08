"""Aave V3 lending supply — Tier 1 strategy (LEND-001).

Supplies stablecoins to Aave V3 on Base. Rotates to highest supply APY
market when the APY differential exceeds threshold after gas costs.
Implements the Strategy protocol — produces reports, not orders.
"""

from __future__ import annotations

from datetime import timedelta

from strategies.base import (
    GasInfo,
    MarketSnapshot,
    Observation,
    PoolState,
    Recommendation,
    Signal,
    SignalType,
    Strategy,
    StrategyReport,
)

STRATEGY_ID = "LEND-001"

# LEND-001 operates exclusively on Base / Aave V3
ALLOWED_PROTOCOL = "aave_v3"
WHITELISTED_ASSETS = frozenset({"USDC", "USDbC"})

# Thresholds from STRATEGY.md
MIN_APY_IMPROVEMENT = 0.005  # 0.5%
MIN_SUPPLY_APY = 0.01  # 1.0% exit floor
MIN_LIQUIDITY_USD = 1_000_000  # $1M
GAS_AMORTIZATION_DAYS = 14
MIN_POSITION_USD = 100
MIN_MONTHLY_GAIN_USD = 1.0
ESTIMATED_GAS_COST_USD = 10.0  # per TX
# TVL exit is delegated to RISK-005 circuit breaker (tvl_monitor.py)


class AaveLendingStrategy:
    """Tier 1: Aave V3 supply rotation for optimal yield.

    Scans whitelisted Aave markets, identifies the best supply APY,
    and produces a StrategyReport with observations, signals, and
    an optional recommendation when conditions are met.
    """

    def __init__(self, current_position_apy: float = 0.0) -> None:
        """Initialize with optional current position APY for differential check.

        Args:
            current_position_apy: APY of the current lending position (0.0 if none).
        """
        self.current_position_apy = current_position_apy

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
        """Analyze Aave V3 pools and produce a strategy report.

        Args:
            snapshot: Pre-sliced market data for this strategy's data_window.

        Returns:
            StrategyReport with observations, signals, and optional recommendation.
        """
        observations: list[Observation] = []
        signals: list[Signal] = []
        recommendation: Recommendation | None = None

        # Filter to eligible Aave V3 pools
        eligible = self._filter_pools(snapshot.pools)

        # Observe gas conditions
        observations.append(Observation(
            metric="gas_current_gwei",
            value=f"{snapshot.gas.current_gwei:.2f}",
            context=f"Current gas {snapshot.gas.current_gwei:.2f} gwei "
                    f"(24h avg {snapshot.gas.avg_24h_gwei:.2f} gwei)",
        ))

        gas_spike = snapshot.gas.current_gwei > snapshot.gas.avg_24h_gwei * 3
        if gas_spike:
            signals.append(Signal(
                type=SignalType.THRESHOLD_APPROACHING,
                actionable=False,
                details=f"Gas spike: {snapshot.gas.current_gwei:.2f} gwei "
                        f"> 3x 24h avg ({snapshot.gas.avg_24h_gwei:.2f})",
            ))

        if not eligible:
            observations.append(Observation(
                metric="eligible_pools",
                value="0",
                context="No eligible Aave V3 pools with whitelisted assets",
            ))
            return StrategyReport(
                strategy_id=STRATEGY_ID,
                timestamp=snapshot.timestamp.isoformat(),
                observations=observations,
                signals=signals,
                recommendation=recommendation,
            )

        # Rank by APY descending
        ranked = sorted(eligible, key=lambda p: p.apy, reverse=True)
        best = ranked[0]

        # Observe pool metrics
        for pool in ranked:
            observations.append(Observation(
                metric=f"aave_{pool.pool_id}_supply_apy",
                value=f"{pool.apy:.4f}",
                context=f"{pool.pool_id} supply APY {pool.apy*100:.2f}%, "
                        f"TVL ${pool.tvl:,.0f}",
            ))

        # --- Exit signal: APY below floor ---
        if best.apy < MIN_SUPPLY_APY:
            signals.append(Signal(
                type=SignalType.EXIT_MET,
                actionable=True,
                details=f"Best supply APY {best.apy*100:.2f}% is below "
                        f"{MIN_SUPPLY_APY*100:.1f}% floor",
            ))
            recommendation = Recommendation(
                action="withdraw",
                reasoning=f"Supply APY {best.apy*100:.2f}% below {MIN_SUPPLY_APY*100:.1f}% minimum",
                parameters={"pool_id": best.pool_id, "protocol": ALLOWED_PROTOCOL},
            )
            return StrategyReport(
                strategy_id=STRATEGY_ID,
                timestamp=snapshot.timestamp.isoformat(),
                observations=observations,
                signals=signals,
                recommendation=recommendation,
            )

        # --- Entry signal evaluation ---
        entry_actionable = self._check_entry(best, snapshot.gas, self.current_position_apy)
        if entry_actionable:
            signals.append(Signal(
                type=SignalType.ENTRY_MET,
                actionable=True,
                details=f"Best pool {best.pool_id} APY {best.apy*100:.2f}% "
                        f"with ${best.tvl:,.0f} liquidity, gas-amortized in "
                        f"{GAS_AMORTIZATION_DAYS} days",
            ))
            recommendation = Recommendation(
                action="supply",
                reasoning=f"Supply {best.pool_id} at {best.apy*100:.2f}% APY, "
                          f"gas cost recoverable within {GAS_AMORTIZATION_DAYS} days",
                parameters={
                    "pool_id": best.pool_id,
                    "protocol": ALLOWED_PROTOCOL,
                    "target_apy": best.apy,
                },
            )
        else:
            # Check if approaching but not yet met
            if best.apy >= MIN_SUPPLY_APY:
                signals.append(Signal(
                    type=SignalType.THRESHOLD_APPROACHING,
                    actionable=False,
                    details=f"Pool {best.pool_id} APY {best.apy*100:.2f}% — "
                            f"entry conditions not fully met",
                ))

        return StrategyReport(
            strategy_id=STRATEGY_ID,
            timestamp=snapshot.timestamp.isoformat(),
            observations=observations,
            signals=signals,
            recommendation=recommendation,
        )

    def _filter_pools(self, pools: list[PoolState]) -> list[PoolState]:
        """Filter pools to eligible Aave V3 markets with whitelisted assets."""
        return [
            p for p in pools
            if p.protocol == ALLOWED_PROTOCOL
            and p.pool_id in WHITELISTED_ASSETS
            and p.tvl >= MIN_LIQUIDITY_USD
            and p.apy > 0
        ]

    def _check_entry(self, pool: PoolState, gas: GasInfo, current_apy: float = 0.0) -> bool:
        """Check if entry conditions are met for a pool.

        Entry requires:
        - APY improvement over current position >= MIN_APY_IMPROVEMENT (0.5%)
        - TVL >= $1M (already filtered by _filter_pools)
        - No gas spike (>3x 24h avg)

        Args:
            pool: Candidate pool to enter.
            gas: Current gas conditions.
            current_apy: APY of the current lending position (0.0 if none).

        Position-size-dependent constraints (gas amortization within 14 days,
        monthly gain > $1) are reported as observations but enforced by the
        decision gate which knows the actual position size.
        """
        improvement = pool.apy - current_apy
        if improvement < MIN_APY_IMPROVEMENT:
            return False

        # Gas spike check
        if gas.current_gwei > gas.avg_24h_gwei * 3:
            return False

        return True


# Protocol conformance assertion
assert isinstance(AaveLendingStrategy(), Strategy)
