"""Integration tests: defi_metrics → main.py → PoolState mapping.

Validates that the pool-building loop in DecisionLoop._evaluate_strategies
correctly maps defi_metrics output shapes to PoolState objects with the
protocol keys, TVL, and APY that strategies expect.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Fixtures: mock data shapes matching real defi_metrics.py output
# ---------------------------------------------------------------------------

AAVE_METRICS = {
    "markets": [
        {
            "symbol": "USDC",
            "supply_apy": 0.0312,
            "utilization_rate": 0.82,
            "available_liquidity": 5_000_000.0,
        },
        {
            "symbol": "WETH",
            "supply_apy": 0.0015,
            "utilization_rate": 0.45,
            "available_liquidity": 12_000_000.0,
        },
    ],
}

AERODROME_METRICS = {
    "tvl_usd": 350_000_000.0,
    "volume_24h": 15_000_000.0,
    "pools": [
        {
            "symbol": "usdc-usdbc-stable",
            "tvl_usd": 2_000_000.0,
            "apy": 0.045,
            "reward_apr": 0.03,
        },
        {
            "symbol": "usdc-dai-stable",
            "tvl_usd": 800_000.0,
            "apy": 0.032,
            "reward_apr": 0.02,
        },
    ],
}


# ---------------------------------------------------------------------------
# Helper: build a minimal DecisionLoop with mocked defi_metrics
# ---------------------------------------------------------------------------


def _build_loop_with_mocked_metrics(aave_data=None, aerodrome_data=None):
    """Create a DecisionLoop with mocked infrastructure and defi_metrics."""
    from main import DecisionLoop

    mock_redis = MagicMock()
    mock_redis.client = MagicMock()
    mock_redis._stream_max_len = 10000

    mock_db = MagicMock()
    mock_db.create_tables = MagicMock()

    mock_repo = MagicMock()
    mock_repo.get_open_positions = MagicMock(return_value=[])
    mock_repo.get_strategy_statuses = MagicMock(return_value={})
    mock_repo.get_snapshots = MagicMock(return_value=[])
    mock_repo.get_latest_snapshot = MagicMock(return_value=None)

    mock_state = MagicMock()
    mock_state.load = MagicMock(return_value={})
    mock_state.get_strategy_statuses = MagicMock(return_value={})

    with patch("main.PriceFeedManager"), \
         patch("main.GasMonitor"), \
         patch("main.DecisionEngine"), \
         patch("main.InsightSynthesizer"), \
         patch("main.PositionTracker.from_database", return_value=MagicMock(
             get_summary=MagicMock(return_value={"total_value": "10000"}),
             query=MagicMock(return_value=[]),
         )):
        loop = DecisionLoop(mock_redis, mock_db, mock_repo, mock_state)

    # Mock defi_metrics to return our test data
    def mock_get_metrics(protocol):
        if protocol == "aave":
            return aave_data
        if protocol == "aerodrome":
            return aerodrome_data
        return None

    loop.defi_metrics = MagicMock()
    loop.defi_metrics.get_metrics = mock_get_metrics

    return loop


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestAavePoolStateMapping:
    """Verify Aave defi_metrics output maps to correct PoolState objects."""

    def test_protocol_key_mapped_to_aave_v3(self):
        """Protocol key 'aave' from defi_metrics is mapped to 'aave_v3'."""
        loop = _build_loop_with_mocked_metrics(aave_data=AAVE_METRICS)
        loop._evaluate_strategies(prices={}, gas=None)
        # Detailed assertions covered by test_aave_pools_have_correct_fields

    def test_aave_pools_have_correct_fields(self):
        """Aave pools have protocol=aave_v3, TVL from available_liquidity, APY from supply_apy."""
        loop = _build_loop_with_mocked_metrics(aave_data=AAVE_METRICS)

        captured_snapshots = []

        class CapturingStrategy:
            strategy_id = "TEST-CAPTURE"
            eval_interval = timedelta(seconds=0)
            data_window = timedelta(hours=1)

            def evaluate(self, snapshot):
                captured_snapshots.append(snapshot)
                from strategies.base import StrategyReport
                return StrategyReport(
                    strategy_id="TEST-CAPTURE",
                    timestamp=datetime.now(UTC).isoformat(),
                    observations=[], signals=[], recommendation=None,
                )

        loop.register_strategy(CapturingStrategy())
        loop._evaluate_strategies(prices={}, gas=None)

        assert len(captured_snapshots) == 1
        snapshot = captured_snapshots[0]
        aave_pools = [p for p in snapshot.pools if p.protocol == "aave_v3"]

        assert len(aave_pools) == 2, f"Expected 2 Aave pools, got {len(aave_pools)}"

        usdc = next(p for p in aave_pools if p.pool_id == "USDC")
        assert usdc.protocol == "aave_v3"
        assert usdc.tvl == 5_000_000.0
        assert usdc.apy == 0.0312
        assert usdc.utilization == 0.82

    def test_no_pools_with_old_aave_key(self):
        """No pools should have protocol='aave' (unmapped key)."""
        loop = _build_loop_with_mocked_metrics(aave_data=AAVE_METRICS)

        captured = []

        class Capturer:
            strategy_id = "TEST"
            eval_interval = timedelta(seconds=0)
            data_window = timedelta(hours=1)

            def evaluate(self, snapshot):
                captured.append(snapshot)
                from strategies.base import StrategyReport
                return StrategyReport(
                    strategy_id="TEST", timestamp=datetime.now(UTC).isoformat(),
                    observations=[], signals=[], recommendation=None,
                )

        loop.register_strategy(Capturer())
        loop._evaluate_strategies(prices={}, gas=None)

        old_key_pools = [p for p in captured[0].pools if p.protocol == "aave"]
        assert len(old_key_pools) == 0, "No pools should use unmapped 'aave' protocol key"


class TestAerodromePoolStateMapping:
    """Verify Aerodrome defi_metrics output maps to correct PoolState objects."""

    def test_aerodrome_pools_from_pools_key(self):
        """Aerodrome pools are read from 'pools' key (not 'markets')."""
        loop = _build_loop_with_mocked_metrics(aerodrome_data=AERODROME_METRICS)

        captured = []

        class Capturer:
            strategy_id = "TEST"
            eval_interval = timedelta(seconds=0)
            data_window = timedelta(hours=1)

            def evaluate(self, snapshot):
                captured.append(snapshot)
                from strategies.base import StrategyReport
                return StrategyReport(
                    strategy_id="TEST", timestamp=datetime.now(UTC).isoformat(),
                    observations=[], signals=[], recommendation=None,
                )

        loop.register_strategy(Capturer())
        loop._evaluate_strategies(prices={}, gas=None)

        aero_pools = [p for p in captured[0].pools if p.protocol == "aerodrome"]
        assert len(aero_pools) == 2, f"Expected 2 Aerodrome pools, got {len(aero_pools)}"

    def test_aerodrome_pools_have_correct_fields(self):
        """Aerodrome pools have TVL from tvl_usd and APY from apy."""
        loop = _build_loop_with_mocked_metrics(aerodrome_data=AERODROME_METRICS)

        captured = []

        class Capturer:
            strategy_id = "TEST"
            eval_interval = timedelta(seconds=0)
            data_window = timedelta(hours=1)

            def evaluate(self, snapshot):
                captured.append(snapshot)
                from strategies.base import StrategyReport
                return StrategyReport(
                    strategy_id="TEST", timestamp=datetime.now(UTC).isoformat(),
                    observations=[], signals=[], recommendation=None,
                )

        loop.register_strategy(Capturer())
        loop._evaluate_strategies(prices={}, gas=None)

        aero_pools = [p for p in captured[0].pools if p.protocol == "aerodrome"]
        usdc_usdbc = next(p for p in aero_pools if p.pool_id == "usdc-usdbc-stable")
        assert usdc_usdbc.tvl == 2_000_000.0
        assert usdc_usdbc.apy == 0.045


class TestBothProtocolsCombined:
    """Verify both protocols map correctly in a single evaluation."""

    def test_combined_pool_building(self):
        """Both Aave and Aerodrome pools coexist with correct protocol keys."""
        loop = _build_loop_with_mocked_metrics(
            aave_data=AAVE_METRICS,
            aerodrome_data=AERODROME_METRICS,
        )

        captured = []

        class Capturer:
            strategy_id = "TEST"
            eval_interval = timedelta(seconds=0)
            data_window = timedelta(hours=1)

            def evaluate(self, snapshot):
                captured.append(snapshot)
                from strategies.base import StrategyReport
                return StrategyReport(
                    strategy_id="TEST", timestamp=datetime.now(UTC).isoformat(),
                    observations=[], signals=[], recommendation=None,
                )

        loop.register_strategy(Capturer())
        loop._evaluate_strategies(prices={}, gas=None)

        pools = captured[0].pools
        protocols = {p.protocol for p in pools}
        assert "aave_v3" in protocols
        assert "aerodrome" in protocols
        assert "aave" not in protocols  # unmapped key must not appear
        assert len(pools) == 4  # 2 Aave + 2 Aerodrome
