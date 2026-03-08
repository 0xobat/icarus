"""Aerodrome stable LP strategy — LP-001 tests.

Tests the rewritten AerodromeLpStrategy which implements the Strategy
protocol and produces StrategyReport instead of execution orders.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from strategies.aerodrome_lp import (
    AERO_CRASH_THRESHOLD,
    EXIT_APR,
    HARVEST_MIN_AERO_PRICE,
    MAX_ALLOCATION_PCT,
    MIN_EMISSION_APR,
    MIN_POSITION_USD,
    MIN_TVL_ENTRY,
    MIN_TVL_EXIT,
    STRATEGY_ID,
    AerodromeLpStrategy,
)
from strategies.base import (
    GasInfo,
    MarketSnapshot,
    PoolState,
    SignalType,
    Strategy,
    TokenPrice,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 3, 8, 12, 0, 0, tzinfo=UTC)
_24H_AGO = _NOW - timedelta(hours=24)


def _make_snapshot(
    pools: list[PoolState] | None = None,
    aero_price: float = 0.80,
    aero_price_24h_ago: float | None = None,
) -> MarketSnapshot:
    """Build a MarketSnapshot with sensible defaults."""
    prices: list[TokenPrice] = [
        TokenPrice(token="AERO", price=aero_price, source="test", timestamp=_NOW),
    ]
    if aero_price_24h_ago is not None:
        prices.append(
            TokenPrice(token="AERO", price=aero_price_24h_ago, source="test", timestamp=_24H_AGO),
        )
    return MarketSnapshot(
        prices=prices,
        gas=GasInfo(current_gwei=0.01, avg_24h_gwei=0.01),
        pools=pools or [],
        timestamp=_NOW,
    )


def _make_pool(
    pool_id: str = "usdc-usdbc-stable",
    tvl: float = 2_000_000.0,
    apy: float = 0.08,
    protocol: str = "aerodrome",
) -> PoolState:
    return PoolState(
        protocol=protocol,
        pool_id=pool_id,
        tvl=tvl,
        apy=apy,
    )


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------

class TestProtocolConformance:
    def test_is_strategy_protocol(self):
        """AerodromeLpStrategy satisfies the Strategy protocol."""
        assert isinstance(AerodromeLpStrategy(), Strategy)

    def test_strategy_id(self):
        s = AerodromeLpStrategy()
        assert s.strategy_id == "LP-001"

    def test_eval_interval(self):
        s = AerodromeLpStrategy()
        assert s.eval_interval == timedelta(minutes=15)

    def test_data_window(self):
        s = AerodromeLpStrategy()
        assert s.data_window == timedelta(hours=24)


# ---------------------------------------------------------------------------
# Observations
# ---------------------------------------------------------------------------

class TestObservations:
    def test_observes_aero_price(self):
        s = AerodromeLpStrategy()
        snapshot = _make_snapshot(pools=[_make_pool()])
        report = s.evaluate(snapshot)
        metrics = [o.metric for o in report.observations]
        assert "aero_price_usd" in metrics

    def test_observes_pool_apr_and_tvl(self):
        s = AerodromeLpStrategy()
        snapshot = _make_snapshot(pools=[_make_pool()])
        report = s.evaluate(snapshot)
        metrics = [o.metric for o in report.observations]
        assert "aerodrome_best_pool_apr" in metrics
        assert "aerodrome_best_pool_tvl" in metrics

    def test_observes_pool_count(self):
        s = AerodromeLpStrategy()
        snapshot = _make_snapshot(pools=[_make_pool(), _make_pool(pool_id="other")])
        report = s.evaluate(snapshot)
        count_obs = [o for o in report.observations if o.metric == "aerodrome_pool_count"]
        assert len(count_obs) == 1
        assert count_obs[0].value == "2"

    def test_no_pools_observation(self):
        s = AerodromeLpStrategy()
        snapshot = _make_snapshot(pools=[])
        report = s.evaluate(snapshot)
        count_obs = [o for o in report.observations if o.metric == "aerodrome_pool_count"]
        assert count_obs[0].value == "0"

    def test_observes_24h_price_change(self):
        s = AerodromeLpStrategy()
        snapshot = _make_snapshot(pools=[_make_pool()], aero_price=0.80, aero_price_24h_ago=1.00)
        report = s.evaluate(snapshot)
        metrics = [o.metric for o in report.observations]
        assert "aero_price_change_24h" in metrics


# ---------------------------------------------------------------------------
# Entry signals
# ---------------------------------------------------------------------------

class TestEntrySignals:
    def test_entry_met_when_conditions_satisfied(self):
        """Entry signal when APR >= 3% and TVL >= $500K."""
        s = AerodromeLpStrategy()
        snapshot = _make_snapshot(pools=[_make_pool(apy=0.05, tvl=1_000_000)])
        report = s.evaluate(snapshot)
        entry_signals = [sig for sig in report.signals if sig.type == SignalType.ENTRY_MET]
        assert len(entry_signals) == 1
        assert entry_signals[0].actionable is True

    def test_no_entry_below_min_apr(self):
        """No entry signal when APR < 3%."""
        s = AerodromeLpStrategy()
        snapshot = _make_snapshot(pools=[_make_pool(apy=0.02, tvl=1_000_000)])
        report = s.evaluate(snapshot)
        entry_signals = [sig for sig in report.signals if sig.type == SignalType.ENTRY_MET]
        assert len(entry_signals) == 0

    def test_no_entry_below_min_tvl(self):
        """No entry signal when TVL < $500K."""
        s = AerodromeLpStrategy()
        snapshot = _make_snapshot(pools=[_make_pool(apy=0.05, tvl=100_000)])
        report = s.evaluate(snapshot)
        entry_signals = [sig for sig in report.signals if sig.type == SignalType.ENTRY_MET]
        assert len(entry_signals) == 0

    def test_threshold_approaching_good_apr_low_tvl(self):
        """Threshold approaching when APR ok but TVL too low."""
        s = AerodromeLpStrategy()
        snapshot = _make_snapshot(pools=[_make_pool(apy=0.05, tvl=100_000)])
        report = s.evaluate(snapshot)
        approaching = [
            sig for sig in report.signals
            if sig.type == SignalType.THRESHOLD_APPROACHING
        ]
        assert len(approaching) == 1
        assert approaching[0].actionable is False

    def test_entry_at_exact_thresholds(self):
        """Entry at exactly 3% APR and $500K TVL."""
        s = AerodromeLpStrategy()
        snapshot = _make_snapshot(pools=[_make_pool(apy=0.03, tvl=500_000)])
        report = s.evaluate(snapshot)
        entry_signals = [sig for sig in report.signals if sig.type == SignalType.ENTRY_MET]
        assert len(entry_signals) == 1

    def test_entry_recommendation_includes_params(self):
        """Entry recommendation includes protocol, pool_id, constraints."""
        s = AerodromeLpStrategy()
        snapshot = _make_snapshot(pools=[_make_pool(apy=0.05, tvl=1_000_000)])
        report = s.evaluate(snapshot)
        assert report.recommendation is not None
        assert report.recommendation.action == "mint_lp"
        assert report.recommendation.parameters["protocol"] == "aerodrome"
        assert report.recommendation.parameters["max_allocation_pct"] == MAX_ALLOCATION_PCT
        assert report.recommendation.parameters["min_position_usd"] == MIN_POSITION_USD


# ---------------------------------------------------------------------------
# Exit signals
# ---------------------------------------------------------------------------

class TestExitSignals:
    def test_exit_low_apr(self):
        """Exit signal when APR < 1.5%."""
        s = AerodromeLpStrategy()
        snapshot = _make_snapshot(pools=[_make_pool(apy=0.01, tvl=1_000_000)])
        report = s.evaluate(snapshot)
        exit_signals = [sig for sig in report.signals if sig.type == SignalType.EXIT_MET]
        assert len(exit_signals) == 1
        assert exit_signals[0].actionable is True

    def test_exit_low_tvl(self):
        """Exit signal when TVL < $200K."""
        s = AerodromeLpStrategy()
        snapshot = _make_snapshot(pools=[_make_pool(apy=0.05, tvl=150_000)])
        report = s.evaluate(snapshot)
        exit_signals = [sig for sig in report.signals if sig.type == SignalType.EXIT_MET]
        assert len(exit_signals) == 1

    def test_exit_aero_crash(self):
        """Exit signal when AERO drops >50% in 24h."""
        s = AerodromeLpStrategy()
        snapshot = _make_snapshot(
            pools=[_make_pool(apy=0.05, tvl=1_000_000)],
            aero_price=0.40,
            aero_price_24h_ago=1.00,
        )
        report = s.evaluate(snapshot)
        exit_signals = [sig for sig in report.signals if sig.type == SignalType.EXIT_MET]
        assert len(exit_signals) == 1
        assert "AERO price dropped" in exit_signals[0].details

    def test_no_exit_healthy(self):
        """No exit when conditions are fine."""
        s = AerodromeLpStrategy()
        snapshot = _make_snapshot(
            pools=[_make_pool(apy=0.05, tvl=1_000_000)],
            aero_price=0.80,
            aero_price_24h_ago=0.75,
        )
        report = s.evaluate(snapshot)
        exit_signals = [sig for sig in report.signals if sig.type == SignalType.EXIT_MET]
        assert len(exit_signals) == 0

    def test_exit_recommendation_burn_lp(self):
        """Exit recommendation action is burn_lp."""
        s = AerodromeLpStrategy()
        snapshot = _make_snapshot(pools=[_make_pool(apy=0.01, tvl=1_000_000)])
        report = s.evaluate(snapshot)
        assert report.recommendation is not None
        assert report.recommendation.action == "burn_lp"


# ---------------------------------------------------------------------------
# Harvest signals
# ---------------------------------------------------------------------------

class TestHarvestSignals:
    def test_harvest_when_aero_above_threshold(self):
        """Harvest signal when AERO >= $0.50."""
        s = AerodromeLpStrategy()
        snapshot = _make_snapshot(
            pools=[_make_pool(apy=0.02, tvl=1_000_000)],  # below entry, no entry signal
            aero_price=0.60,
        )
        report = s.evaluate(snapshot)
        harvest = [sig for sig in report.signals if sig.type == SignalType.HARVEST_READY]
        assert len(harvest) == 1
        assert harvest[0].actionable is True

    def test_no_harvest_below_threshold(self):
        """No harvest signal when AERO < $0.50."""
        s = AerodromeLpStrategy()
        snapshot = _make_snapshot(
            pools=[_make_pool(apy=0.02, tvl=1_000_000)],
            aero_price=0.30,
        )
        report = s.evaluate(snapshot)
        harvest = [sig for sig in report.signals if sig.type == SignalType.HARVEST_READY]
        assert len(harvest) == 0

    def test_harvest_at_exact_threshold(self):
        """Harvest signal at exactly $0.50."""
        s = AerodromeLpStrategy()
        snapshot = _make_snapshot(
            pools=[_make_pool(apy=0.02, tvl=1_000_000)],
            aero_price=0.50,
        )
        report = s.evaluate(snapshot)
        harvest = [sig for sig in report.signals if sig.type == SignalType.HARVEST_READY]
        assert len(harvest) == 1

    def test_harvest_recommendation_when_no_exit(self):
        """Harvest recommendation when no exit condition."""
        s = AerodromeLpStrategy()
        # APR below entry but above exit, AERO at harvest threshold
        snapshot = _make_snapshot(
            pools=[_make_pool(apy=0.02, tvl=1_000_000)],
            aero_price=0.60,
        )
        report = s.evaluate(snapshot)
        assert report.recommendation is not None
        assert report.recommendation.action == "harvest"

    def test_exit_takes_priority_over_harvest(self):
        """Exit recommendation overrides harvest when both trigger."""
        s = AerodromeLpStrategy()
        snapshot = _make_snapshot(
            pools=[_make_pool(apy=0.01, tvl=1_000_000)],  # below exit threshold
            aero_price=0.60,  # above harvest threshold
        )
        report = s.evaluate(snapshot)
        assert report.recommendation is not None
        assert report.recommendation.action == "burn_lp"


# ---------------------------------------------------------------------------
# Report structure
# ---------------------------------------------------------------------------

class TestReportStructure:
    def test_report_strategy_id(self):
        s = AerodromeLpStrategy()
        snapshot = _make_snapshot(pools=[_make_pool()])
        report = s.evaluate(snapshot)
        assert report.strategy_id == "LP-001"

    def test_report_has_timestamp(self):
        s = AerodromeLpStrategy()
        snapshot = _make_snapshot(pools=[_make_pool()])
        report = s.evaluate(snapshot)
        assert report.timestamp  # non-empty ISO string

    def test_empty_pools_returns_valid_report(self):
        s = AerodromeLpStrategy()
        snapshot = _make_snapshot(pools=[])
        report = s.evaluate(snapshot)
        assert report.strategy_id == "LP-001"
        assert report.signals == []
        assert report.recommendation is None

    def test_non_aerodrome_pools_ignored(self):
        """Pools from other protocols are ignored."""
        s = AerodromeLpStrategy()
        snapshot = _make_snapshot(pools=[_make_pool(protocol="aave_v3")])
        report = s.evaluate(snapshot)
        count_obs = [o for o in report.observations if o.metric == "aerodrome_pool_count"]
        assert count_obs[0].value == "0"
        assert report.signals == []

    def test_selects_best_pool_by_apy(self):
        """Best pool is the one with highest APY."""
        s = AerodromeLpStrategy()
        pools = [
            _make_pool(pool_id="low", apy=0.04, tvl=1_000_000),
            _make_pool(pool_id="high", apy=0.10, tvl=1_000_000),
        ]
        snapshot = _make_snapshot(pools=pools)
        report = s.evaluate(snapshot)
        apr_obs = [o for o in report.observations if o.metric == "aerodrome_best_pool_apr"]
        assert "0.1000" in apr_obs[0].value

    def test_no_aero_price_still_works(self):
        """Strategy works when no AERO price available."""
        s = AerodromeLpStrategy()
        snapshot = MarketSnapshot(
            prices=[],
            gas=GasInfo(current_gwei=0.01, avg_24h_gwei=0.01),
            pools=[_make_pool(apy=0.05, tvl=1_000_000)],
            timestamp=_NOW,
        )
        report = s.evaluate(snapshot)
        assert report.strategy_id == "LP-001"
        # Should still produce entry signal
        entry = [sig for sig in report.signals if sig.type == SignalType.ENTRY_MET]
        assert len(entry) == 1


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

class TestConstants:
    def test_strategy_id(self):
        assert STRATEGY_ID == "LP-001"

    def test_thresholds(self):
        assert MIN_EMISSION_APR == 0.03
        assert EXIT_APR == 0.015
        assert MIN_TVL_ENTRY == 500_000.0
        assert MIN_TVL_EXIT == 200_000.0
        assert AERO_CRASH_THRESHOLD == -0.50
        assert HARVEST_MIN_AERO_PRICE == 0.50
        assert MAX_ALLOCATION_PCT == 0.30
        assert MIN_POSITION_USD == 100.0
