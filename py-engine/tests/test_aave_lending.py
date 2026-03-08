"""Tests for Aave lending supply strategy — LEND-001 (Strategy protocol)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from strategies.aave_lending import (
    ALLOWED_PROTOCOL,
    GAS_AMORTIZATION_DAYS,
    MIN_APY_IMPROVEMENT,
    MIN_LIQUIDITY_USD,
    MIN_MONTHLY_GAIN_USD,
    MIN_SUPPLY_APY,
    WHITELISTED_ASSETS,
    AaveLendingStrategy,
)
from strategies.base import (
    GasInfo,
    MarketSnapshot,
    PoolState,
    SignalType,
    Strategy,
    StrategyReport,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 3, 8, 12, 0, 0, tzinfo=UTC)


def _gas(current: float = 0.05, avg_24h: float = 0.05) -> GasInfo:
    return GasInfo(current_gwei=current, avg_24h_gwei=avg_24h)


def _pool(
    pool_id: str = "USDC",
    apy: float = 0.042,
    tvl: float = 5_000_000,
    protocol: str = ALLOWED_PROTOCOL,
    utilization: float | None = 0.80,
) -> PoolState:
    return PoolState(
        protocol=protocol,
        pool_id=pool_id,
        tvl=tvl,
        apy=apy,
        utilization=utilization,
    )


def _snapshot(
    pools: list[PoolState] | None = None,
    gas: GasInfo | None = None,
) -> MarketSnapshot:
    return MarketSnapshot(
        prices=[],
        gas=gas or _gas(),
        pools=pools or [_pool()],
        timestamp=_NOW,
    )


def _make_strategy() -> AaveLendingStrategy:
    return AaveLendingStrategy()


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------

class TestProtocolConformance:
    """AaveLendingStrategy must satisfy the Strategy protocol."""

    def test_is_strategy_instance(self) -> None:
        assert isinstance(_make_strategy(), Strategy)

    def test_strategy_id(self) -> None:
        assert _make_strategy().strategy_id == "LEND-001"

    def test_eval_interval(self) -> None:
        assert _make_strategy().eval_interval == timedelta(minutes=15)

    def test_data_window(self) -> None:
        assert _make_strategy().data_window == timedelta(hours=24)

    def test_evaluate_returns_strategy_report(self) -> None:
        report = _make_strategy().evaluate(_snapshot())
        assert isinstance(report, StrategyReport)
        assert report.strategy_id == "LEND-001"


# ---------------------------------------------------------------------------
# Observations
# ---------------------------------------------------------------------------

class TestObservations:
    """Strategy must produce factual observations about market state."""

    def test_gas_observation_always_present(self) -> None:
        report = _make_strategy().evaluate(_snapshot())
        gas_obs = [o for o in report.observations if o.metric == "gas_current_gwei"]
        assert len(gas_obs) == 1

    def test_pool_apy_observations(self) -> None:
        pools = [_pool("USDC", 0.042), _pool("USDbC", 0.035)]
        report = _make_strategy().evaluate(_snapshot(pools=pools))
        apy_obs = [o for o in report.observations if "supply_apy" in o.metric]
        assert len(apy_obs) == 2

    def test_no_eligible_pools_observation(self) -> None:
        # Use a non-eligible pool (wrong protocol) so filtering removes it
        bad_pool = _pool("USDC", apy=0.05, tvl=5_000_000, protocol="compound")
        report = _make_strategy().evaluate(_snapshot(pools=[bad_pool]))
        pool_obs = [o for o in report.observations if o.metric == "eligible_pools"]
        assert len(pool_obs) == 1
        assert pool_obs[0].value == "0"


# ---------------------------------------------------------------------------
# Entry signals
# ---------------------------------------------------------------------------

class TestEntrySignals:
    """Entry signal when APY, liquidity, and gas conditions are met."""

    def test_entry_signal_on_good_pool(self) -> None:
        # High APY, high TVL, low gas
        pool = _pool("USDC", apy=0.05, tvl=5_000_000)
        report = _make_strategy().evaluate(_snapshot(pools=[pool]))
        entry_signals = [s for s in report.signals if s.type == SignalType.ENTRY_MET]
        assert len(entry_signals) == 1
        assert entry_signals[0].actionable is True

    def test_no_entry_below_min_apy_improvement(self) -> None:
        # APY below 0.5% threshold
        pool = _pool("USDC", apy=0.003, tvl=5_000_000)
        report = _make_strategy().evaluate(_snapshot(pools=[pool]))
        entry_signals = [s for s in report.signals if s.type == SignalType.ENTRY_MET]
        assert len(entry_signals) == 0

    def test_no_entry_on_gas_spike(self) -> None:
        # Gas > 3x 24h avg
        pool = _pool("USDC", apy=0.05, tvl=5_000_000)
        gas = _gas(current=0.20, avg_24h=0.05)
        report = _make_strategy().evaluate(_snapshot(pools=[pool], gas=gas))
        entry_signals = [s for s in report.signals if s.type == SignalType.ENTRY_MET]
        assert len(entry_signals) == 0

    def test_gas_spike_produces_threshold_signal(self) -> None:
        pool = _pool("USDC", apy=0.05, tvl=5_000_000)
        gas = _gas(current=0.20, avg_24h=0.05)
        report = _make_strategy().evaluate(_snapshot(pools=[pool], gas=gas))
        spike_signals = [
            s for s in report.signals
            if s.type == SignalType.THRESHOLD_APPROACHING and "Gas spike" in s.details
        ]
        assert len(spike_signals) == 1

    def test_no_entry_insufficient_liquidity(self) -> None:
        # TVL below $1M
        pool = _pool("USDC", apy=0.05, tvl=500_000)
        report = _make_strategy().evaluate(_snapshot(pools=[pool]))
        entry_signals = [s for s in report.signals if s.type == SignalType.ENTRY_MET]
        assert len(entry_signals) == 0


# ---------------------------------------------------------------------------
# Exit signals
# ---------------------------------------------------------------------------

class TestExitSignals:
    """Exit signal when APY drops below floor."""

    def test_exit_signal_below_min_apy(self) -> None:
        pool = _pool("USDC", apy=0.005, tvl=5_000_000)
        report = _make_strategy().evaluate(_snapshot(pools=[pool]))
        exit_signals = [s for s in report.signals if s.type == SignalType.EXIT_MET]
        assert len(exit_signals) == 1
        assert exit_signals[0].actionable is True

    def test_exit_produces_withdraw_recommendation(self) -> None:
        pool = _pool("USDC", apy=0.005, tvl=5_000_000)
        report = _make_strategy().evaluate(_snapshot(pools=[pool]))
        assert report.recommendation is not None
        assert report.recommendation.action == "withdraw"

    def test_no_exit_above_min_apy(self) -> None:
        pool = _pool("USDC", apy=0.05, tvl=5_000_000)
        report = _make_strategy().evaluate(_snapshot(pools=[pool]))
        exit_signals = [s for s in report.signals if s.type == SignalType.EXIT_MET]
        assert len(exit_signals) == 0


# ---------------------------------------------------------------------------
# Recommendations
# ---------------------------------------------------------------------------

class TestRecommendations:
    """Recommendation present only when actionable signal exists."""

    def test_supply_recommendation_on_entry(self) -> None:
        pool = _pool("USDC", apy=0.05, tvl=5_000_000)
        report = _make_strategy().evaluate(_snapshot(pools=[pool]))
        assert report.recommendation is not None
        assert report.recommendation.action == "supply"
        assert "USDC" in report.recommendation.parameters.get("pool_id", "")

    def test_no_recommendation_when_no_actionable(self) -> None:
        # APY above exit floor but below entry threshold → threshold_approaching
        pool = _pool("USDC", apy=0.003, tvl=500_000)  # low TVL filters it out
        report = _make_strategy().evaluate(_snapshot(pools=[pool]))
        assert report.recommendation is None

    def test_no_recommendation_on_empty_pools(self) -> None:
        # Non-eligible pool so nothing passes filtering
        bad_pool = _pool("ETH", apy=0.05, tvl=5_000_000)
        report = _make_strategy().evaluate(_snapshot(pools=[bad_pool]))
        assert report.recommendation is None


# ---------------------------------------------------------------------------
# Pool filtering
# ---------------------------------------------------------------------------

class TestPoolFiltering:
    """Only eligible Aave V3 pools with whitelisted assets pass through."""

    def test_filters_non_aave_protocol(self) -> None:
        pool = _pool("USDC", apy=0.05, tvl=5_000_000, protocol="compound")
        report = _make_strategy().evaluate(_snapshot(pools=[pool]))
        apy_obs = [o for o in report.observations if "supply_apy" in o.metric]
        assert len(apy_obs) == 0

    def test_filters_non_whitelisted_asset(self) -> None:
        pool = _pool("ETH", apy=0.05, tvl=5_000_000)
        report = _make_strategy().evaluate(_snapshot(pools=[pool]))
        apy_obs = [o for o in report.observations if "supply_apy" in o.metric]
        assert len(apy_obs) == 0

    def test_filters_low_tvl(self) -> None:
        pool = _pool("USDC", apy=0.05, tvl=100_000)
        report = _make_strategy().evaluate(_snapshot(pools=[pool]))
        apy_obs = [o for o in report.observations if "supply_apy" in o.metric]
        assert len(apy_obs) == 0

    def test_filters_zero_apy(self) -> None:
        pool = _pool("USDC", apy=0.0, tvl=5_000_000)
        report = _make_strategy().evaluate(_snapshot(pools=[pool]))
        apy_obs = [o for o in report.observations if "supply_apy" in o.metric]
        assert len(apy_obs) == 0

    def test_whitelisted_assets(self) -> None:
        for asset in ("USDC", "USDbC"):
            assert asset in WHITELISTED_ASSETS

    def test_ranks_pools_by_apy_descending(self) -> None:
        pools = [_pool("USDbC", apy=0.035), _pool("USDC", apy=0.042)]
        report = _make_strategy().evaluate(_snapshot(pools=pools))
        apy_obs = [o for o in report.observations if "supply_apy" in o.metric]
        # First observation should be highest APY pool
        assert "USDC" in apy_obs[0].metric


# ---------------------------------------------------------------------------
# No order generation
# ---------------------------------------------------------------------------

class TestNoOrders:
    """Strategy must NOT produce orders — only reports."""

    def test_no_generate_orders_method(self) -> None:
        assert not hasattr(_make_strategy(), "generate_orders")

    def test_evaluate_returns_report_not_list(self) -> None:
        report = _make_strategy().evaluate(_snapshot())
        assert isinstance(report, StrategyReport)
        assert not isinstance(report, list)


# ---------------------------------------------------------------------------
# Constants match STRATEGY.md
# ---------------------------------------------------------------------------

class TestStrategyConstants:
    """Thresholds must match STRATEGY.md specification."""

    def test_min_apy_improvement(self) -> None:
        assert MIN_APY_IMPROVEMENT == 0.005

    def test_min_supply_apy(self) -> None:
        assert MIN_SUPPLY_APY == 0.01

    def test_min_liquidity(self) -> None:
        assert MIN_LIQUIDITY_USD == 1_000_000

    def test_gas_amortization_days(self) -> None:
        assert GAS_AMORTIZATION_DAYS == 14

    def test_min_position_usd(self) -> None:
        from strategies.aave_lending import MIN_POSITION_USD
        assert MIN_POSITION_USD == 100

    def test_min_monthly_gain(self) -> None:
        assert MIN_MONTHLY_GAIN_USD == 1.0
