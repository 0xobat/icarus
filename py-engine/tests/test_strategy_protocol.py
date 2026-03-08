"""Tests for Strategy protocol, data types, and auto-discovery.

Covers:
- All dataclasses (TokenPrice, GasInfo, PoolState, MarketSnapshot,
  Observation, Signal, Recommendation, StrategyReport)
- SignalType enum
- Strategy protocol conformance checking
- Signal validation constraints
- Auto-discovery of strategy classes from strategies/ directory
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

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
    TokenPrice,
)

# --- SignalType enum tests ---


class TestSignalType:
    """Tests for SignalType enum."""

    def test_all_signal_types_defined(self) -> None:
        """All five signal types exist."""
        assert SignalType.ENTRY_MET.value == "entry_met"
        assert SignalType.EXIT_MET.value == "exit_met"
        assert SignalType.HARVEST_READY.value == "harvest_ready"
        assert SignalType.REBALANCE_NEEDED.value == "rebalance_needed"
        assert SignalType.THRESHOLD_APPROACHING.value == "threshold_approaching"

    def test_signal_type_count(self) -> None:
        """Exactly 5 signal types defined."""
        assert len(SignalType) == 5

    def test_signal_type_is_str(self) -> None:
        """SignalType values are strings."""
        for st in SignalType:
            assert isinstance(st.value, str)
            assert isinstance(st, str)


# --- TokenPrice tests ---


class TestTokenPrice:
    """Tests for TokenPrice dataclass."""

    def test_create_token_price(self) -> None:
        """TokenPrice can be created with all fields."""
        now = datetime.now(UTC)
        tp = TokenPrice(token="USDC", price=1.0001, source="alchemy", timestamp=now)
        assert tp.token == "USDC"
        assert tp.price == 1.0001
        assert tp.source == "alchemy"
        assert tp.timestamp == now

    def test_token_price_frozen(self) -> None:
        """TokenPrice is immutable."""
        tp = TokenPrice(
            token="USDC", price=1.0, source="alchemy", timestamp=datetime.now(UTC)
        )
        with pytest.raises(AttributeError):
            tp.price = 2.0  # type: ignore[misc]


# --- GasInfo tests ---


class TestGasInfo:
    """Tests for GasInfo dataclass."""

    def test_create_gas_info(self) -> None:
        """GasInfo can be created with current and average."""
        gas = GasInfo(current_gwei=15.5, avg_24h_gwei=12.3)
        assert gas.current_gwei == 15.5
        assert gas.avg_24h_gwei == 12.3

    def test_gas_info_frozen(self) -> None:
        """GasInfo is immutable."""
        gas = GasInfo(current_gwei=15.5, avg_24h_gwei=12.3)
        with pytest.raises(AttributeError):
            gas.current_gwei = 20.0  # type: ignore[misc]


# --- PoolState tests ---


class TestPoolState:
    """Tests for PoolState dataclass."""

    def test_create_pool_state_with_utilization(self) -> None:
        """PoolState with all fields including utilization."""
        ps = PoolState(
            protocol="aave_v3",
            pool_id="USDC",
            tvl=50_000_000.0,
            apy=0.042,
            utilization=0.85,
        )
        assert ps.protocol == "aave_v3"
        assert ps.pool_id == "USDC"
        assert ps.tvl == 50_000_000.0
        assert ps.apy == 0.042
        assert ps.utilization == 0.85

    def test_create_pool_state_without_utilization(self) -> None:
        """PoolState with utilization defaulting to None."""
        ps = PoolState(protocol="aerodrome", pool_id="USDC-USDbC", tvl=5_000_000.0, apy=0.035)
        assert ps.utilization is None

    def test_pool_state_frozen(self) -> None:
        """PoolState is immutable."""
        ps = PoolState(protocol="aave_v3", pool_id="USDC", tvl=1.0, apy=0.01)
        with pytest.raises(AttributeError):
            ps.tvl = 2.0  # type: ignore[misc]


# --- MarketSnapshot tests ---


class TestMarketSnapshot:
    """Tests for MarketSnapshot dataclass."""

    def _make_snapshot(self) -> MarketSnapshot:
        """Create a valid MarketSnapshot for testing."""
        now = datetime.now(UTC)
        return MarketSnapshot(
            prices=[
                TokenPrice(token="USDC", price=1.0001, source="alchemy", timestamp=now),
                TokenPrice(token="AERO", price=0.85, source="defillama", timestamp=now),
            ],
            gas=GasInfo(current_gwei=0.005, avg_24h_gwei=0.004),
            pools=[
                PoolState(
                    protocol="aave_v3",
                    pool_id="USDC",
                    tvl=50_000_000.0,
                    apy=0.042,
                    utilization=0.85,
                ),
            ],
            timestamp=now,
        )

    def test_create_snapshot(self) -> None:
        """MarketSnapshot can be created with all component types."""
        snap = self._make_snapshot()
        assert len(snap.prices) == 2
        assert snap.gas.current_gwei == 0.005
        assert len(snap.pools) == 1
        assert snap.timestamp is not None

    def test_snapshot_frozen(self) -> None:
        """MarketSnapshot is immutable."""
        snap = self._make_snapshot()
        with pytest.raises(AttributeError):
            snap.timestamp = datetime.now(UTC)  # type: ignore[misc]

    def test_snapshot_empty_lists(self) -> None:
        """MarketSnapshot can have empty prices and pools."""
        snap = MarketSnapshot(
            prices=[],
            gas=GasInfo(current_gwei=0.0, avg_24h_gwei=0.0),
            pools=[],
            timestamp=datetime.now(UTC),
        )
        assert snap.prices == []
        assert snap.pools == []


# --- Observation tests ---


class TestObservation:
    """Tests for Observation dataclass."""

    def test_create_observation(self) -> None:
        """Observation can be created with metric, value, context."""
        obs = Observation(
            metric="aave_usdc_supply_apy",
            value="4.2%",
            context="Aave USDC supply APY is 4.2%, up from 3.1% yesterday",
        )
        assert obs.metric == "aave_usdc_supply_apy"
        assert obs.value == "4.2%"
        assert "4.2%" in obs.context

    def test_observation_frozen(self) -> None:
        """Observation is immutable."""
        obs = Observation(metric="test", value="1", context="ctx")
        with pytest.raises(AttributeError):
            obs.value = "2"  # type: ignore[misc]


# --- Signal tests ---


class TestSignal:
    """Tests for Signal dataclass."""

    def test_create_actionable_entry_signal(self) -> None:
        """Entry signal can be actionable."""
        sig = Signal(
            type=SignalType.ENTRY_MET,
            actionable=True,
            details="APY differential exceeds 0.5% threshold after gas",
        )
        assert sig.type == SignalType.ENTRY_MET
        assert sig.actionable is True

    def test_create_non_actionable_entry_signal(self) -> None:
        """Entry signal can be non-actionable."""
        sig = Signal(
            type=SignalType.ENTRY_MET,
            actionable=False,
            details="APY differential below threshold",
        )
        assert sig.actionable is False

    def test_threshold_approaching_must_be_non_actionable(self) -> None:
        """threshold_approaching with actionable=True raises ValueError."""
        with pytest.raises(ValueError, match="threshold_approaching"):
            Signal(
                type=SignalType.THRESHOLD_APPROACHING,
                actionable=True,
                details="APY nearing threshold",
            )

    def test_threshold_approaching_non_actionable_ok(self) -> None:
        """threshold_approaching with actionable=False is valid."""
        sig = Signal(
            type=SignalType.THRESHOLD_APPROACHING,
            actionable=False,
            details="APY nearing threshold",
        )
        assert sig.actionable is False

    @pytest.mark.parametrize(
        "signal_type",
        [
            SignalType.ENTRY_MET,
            SignalType.EXIT_MET,
            SignalType.HARVEST_READY,
            SignalType.REBALANCE_NEEDED,
        ],
    )
    def test_actionable_signal_types(self, signal_type: SignalType) -> None:
        """Non-threshold signal types can be actionable."""
        sig = Signal(type=signal_type, actionable=True, details="test")
        assert sig.actionable is True

    def test_signal_frozen(self) -> None:
        """Signal is immutable."""
        sig = Signal(type=SignalType.ENTRY_MET, actionable=True, details="test")
        with pytest.raises(AttributeError):
            sig.actionable = False  # type: ignore[misc]


# --- Recommendation tests ---


class TestRecommendation:
    """Tests for Recommendation dataclass."""

    def test_create_recommendation(self) -> None:
        """Recommendation with action, reasoning, parameters."""
        rec = Recommendation(
            action="supply",
            reasoning="USDC APY at 4.2% exceeds threshold",
            parameters={"asset": "USDC", "amount": "1000.00", "protocol": "aave_v3"},
        )
        assert rec.action == "supply"
        assert "4.2%" in rec.reasoning
        assert rec.parameters["asset"] == "USDC"

    def test_recommendation_default_params(self) -> None:
        """Recommendation parameters default to empty dict."""
        rec = Recommendation(action="hold", reasoning="No action needed")
        assert rec.parameters == {}

    def test_recommendation_frozen(self) -> None:
        """Recommendation is immutable."""
        rec = Recommendation(action="hold", reasoning="test")
        with pytest.raises(AttributeError):
            rec.action = "sell"  # type: ignore[misc]


# --- StrategyReport tests ---


class TestStrategyReport:
    """Tests for StrategyReport dataclass."""

    def test_create_report_with_recommendation(self) -> None:
        """StrategyReport with observations, signals, and recommendation."""
        report = StrategyReport(
            strategy_id="LEND-001",
            timestamp="2026-03-07T12:00:00Z",
            observations=[
                Observation(
                    metric="aave_usdc_supply_apy",
                    value="4.2%",
                    context="Supply APY at 4.2%",
                ),
            ],
            signals=[
                Signal(
                    type=SignalType.ENTRY_MET,
                    actionable=True,
                    details="APY exceeds threshold",
                ),
            ],
            recommendation=Recommendation(
                action="supply",
                reasoning="Good entry point",
                parameters={"asset": "USDC"},
            ),
        )
        assert report.strategy_id == "LEND-001"
        assert len(report.observations) == 1
        assert len(report.signals) == 1
        assert report.recommendation is not None
        assert report.recommendation.action == "supply"

    def test_create_report_without_recommendation(self) -> None:
        """StrategyReport with no recommendation (hold/no action)."""
        report = StrategyReport(
            strategy_id="LP-001",
            timestamp="2026-03-07T12:00:00Z",
            observations=[],
            signals=[],
        )
        assert report.recommendation is None

    def test_report_frozen(self) -> None:
        """StrategyReport is immutable."""
        report = StrategyReport(
            strategy_id="LEND-001",
            timestamp="2026-03-07T12:00:00Z",
            observations=[],
            signals=[],
        )
        with pytest.raises(AttributeError):
            report.strategy_id = "LP-001"  # type: ignore[misc]

    def test_report_multiple_signals(self) -> None:
        """StrategyReport can have multiple signals of different types."""
        report = StrategyReport(
            strategy_id="LEND-001",
            timestamp="2026-03-07T12:00:00Z",
            observations=[],
            signals=[
                Signal(
                    type=SignalType.ENTRY_MET,
                    actionable=True,
                    details="Entry condition met",
                ),
                Signal(
                    type=SignalType.THRESHOLD_APPROACHING,
                    actionable=False,
                    details="Gas approaching spike",
                ),
            ],
        )
        assert len(report.signals) == 2
        actionable = [s for s in report.signals if s.actionable]
        assert len(actionable) == 1


# --- Strategy Protocol tests ---


class _ValidStrategy:
    """A minimal class that satisfies the Strategy protocol."""

    @property
    def strategy_id(self) -> str:
        return "TEST-001"

    @property
    def eval_interval(self) -> timedelta:
        return timedelta(seconds=30)

    @property
    def data_window(self) -> timedelta:
        return timedelta(hours=1)

    def evaluate(self, snapshot: MarketSnapshot) -> StrategyReport:
        return StrategyReport(
            strategy_id=self.strategy_id,
            timestamp=snapshot.timestamp.isoformat(),
            observations=[],
            signals=[],
        )


class _MissingEvaluate:
    """Lacks the evaluate method."""

    @property
    def strategy_id(self) -> str:
        return "BAD-001"

    @property
    def eval_interval(self) -> timedelta:
        return timedelta(seconds=30)

    @property
    def data_window(self) -> timedelta:
        return timedelta(hours=1)


class _MissingStrategyId:
    """Lacks strategy_id property."""

    @property
    def eval_interval(self) -> timedelta:
        return timedelta(seconds=30)

    @property
    def data_window(self) -> timedelta:
        return timedelta(hours=1)

    def evaluate(self, snapshot: MarketSnapshot) -> StrategyReport:
        return StrategyReport(
            strategy_id="",
            timestamp="",
            observations=[],
            signals=[],
        )


class TestStrategyProtocol:
    """Tests for Strategy protocol conformance."""

    def test_valid_strategy_satisfies_protocol(self) -> None:
        """Class with all required members satisfies Strategy protocol."""
        s = _ValidStrategy()
        assert isinstance(s, Strategy)

    def test_missing_evaluate_fails_protocol(self) -> None:
        """Class without evaluate() does not satisfy Strategy."""
        m = _MissingEvaluate()
        assert not isinstance(m, Strategy)

    def test_missing_strategy_id_fails_protocol(self) -> None:
        """Class without strategy_id is caught by _is_strategy_class."""
        # runtime_checkable only checks method existence, not properties,
        # so isinstance may still pass. The important check is
        # _is_strategy_class which we test in TestAutoDiscovery.
        from strategies import _is_strategy_class

        assert _is_strategy_class(_MissingStrategyId) is False

    def test_valid_strategy_evaluate(self) -> None:
        """Valid strategy produces a StrategyReport from evaluate()."""
        s = _ValidStrategy()
        now = datetime.now(UTC)
        snapshot = MarketSnapshot(
            prices=[],
            gas=GasInfo(current_gwei=0.005, avg_24h_gwei=0.004),
            pools=[],
            timestamp=now,
        )
        report = s.evaluate(snapshot)
        assert report.strategy_id == "TEST-001"
        assert report.timestamp == now.isoformat()

    def test_strategy_properties(self) -> None:
        """Strategy properties return correct types."""
        s = _ValidStrategy()
        assert isinstance(s.strategy_id, str)
        assert isinstance(s.eval_interval, timedelta)
        assert isinstance(s.data_window, timedelta)
        assert s.eval_interval == timedelta(seconds=30)
        assert s.data_window == timedelta(hours=1)


# --- Auto-discovery tests ---


class TestAutoDiscovery:
    """Tests for discover_strategies() auto-discovery."""

    def test_discover_returns_dict(self) -> None:
        """discover_strategies returns a dict."""
        from strategies import discover_strategies

        result = discover_strategies()
        assert isinstance(result, dict)

    def test_discover_does_not_include_base_types(self) -> None:
        """Base module types are not registered as strategies."""
        from strategies import discover_strategies

        result = discover_strategies()
        # Strategy, MarketSnapshot, etc. should not appear
        for key in result:
            assert key not in ("Strategy", "MarketSnapshot", "StrategyReport")

    def test_is_strategy_class_valid(self) -> None:
        """_is_strategy_class accepts valid strategy classes."""
        from strategies import _is_strategy_class

        assert _is_strategy_class(_ValidStrategy) is True

    def test_is_strategy_class_missing_evaluate(self) -> None:
        """_is_strategy_class rejects classes missing evaluate."""
        from strategies import _is_strategy_class

        assert _is_strategy_class(_MissingEvaluate) is False

    def test_is_strategy_class_missing_strategy_id(self) -> None:
        """_is_strategy_class rejects classes missing strategy_id."""
        from strategies import _is_strategy_class

        assert _is_strategy_class(_MissingStrategyId) is False

    def test_is_strategy_class_rejects_plain_object(self) -> None:
        """_is_strategy_class rejects classes with no strategy attributes."""
        from strategies import _is_strategy_class

        class Empty:
            pass

        assert _is_strategy_class(Empty) is False

    def test_imports_available(self) -> None:
        """All base types are importable from strategies package."""
        from strategies import (
            GasInfo as _GasInfo,
        )
        from strategies import (
            MarketSnapshot as _MarketSnapshot,
        )
        from strategies import (
            Observation as _Observation,
        )
        from strategies import (
            PoolState as _PoolState,
        )
        from strategies import (
            Recommendation as _Recommendation,
        )
        from strategies import (
            Signal as _Signal,
        )
        from strategies import (
            SignalType as _SignalType,
        )
        from strategies import (
            Strategy as _Strategy,
        )
        from strategies import (
            StrategyReport as _StrategyReport,
        )
        from strategies import (
            TokenPrice as _TokenPrice,
        )

        # Verify they're the actual base types (not None or wrong class)
        expected = [
            _GasInfo, _MarketSnapshot, _Observation, _PoolState,
            _Recommendation, _Signal, _SignalType, _Strategy,
            _StrategyReport, _TokenPrice,
        ]
        for cls in expected:
            assert cls is not None
        assert _SignalType.ENTRY_MET.value == "entry_met"
