"""Tests for insight synthesis pipeline -- AI-003."""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock

from ai.insight_synthesis import (
    InsightSnapshot,
    InsightSynthesizer,
    _compress_defi_metrics,
    _compress_gas,
    _compress_positions,
    _compress_prices,
    _compute_rate_trends,
    validate_snapshot,
)
from portfolio.position_tracker import Position, PositionTracker

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_price_feed(prices: dict[str, Any] | None = None):
    mock = MagicMock()
    mock.fetch_prices.return_value = prices or {
        "ETH": {"price_usd": 3200.0, "sources": ["coingecko", "defillama"]},
        "USDC": {"price_usd": 1.0, "sources": ["coingecko"]},
    }
    return mock


def _make_gas_monitor(
    cached: dict | None = None,
    spike: bool | None = False,
):
    mock = MagicMock()
    if cached is not None:
        gas_obj = MagicMock()
        gas_obj.to_dict.return_value = cached
        mock.get_cached_prices.return_value = gas_obj
    else:
        mock.get_cached_prices.return_value = None
        gas_obj = MagicMock()
        gas_obj.to_dict.return_value = {
            "fast": 30.0, "standard": 20.0, "slow": 10.0,
            "timestamp": "2026-01-01T00:00:00",
        }
        mock.update.return_value = gas_obj
    mock.is_spike.return_value = spike
    return mock


def _make_defi_metrics(metrics: dict[str, Any] | None = None):
    mock = MagicMock()
    default_metrics = {
        "aave": {
            "protocol": "aave",
            "markets": [
                {"symbol": "ETH", "supply_apy": 3.5, "utilization_rate": 80.0},
                {"symbol": "USDC", "supply_apy": 4.2, "utilization_rate": 85.0},
            ],
        },
    }

    def get_metrics(protocol):
        m = metrics or default_metrics
        return m.get(protocol)

    mock.get_metrics.side_effect = get_metrics
    return mock


def _make_position_tracker(positions: list[Position] | None = None):
    tracker = PositionTracker()
    if positions:
        for p in positions:
            tracker._open[p.id] = p
    return tracker


def _make_lifecycle_manager(statuses: dict[str, str] | None = None):
    mock = MagicMock()
    state_mock = MagicMock()
    state_mock.get_strategy_statuses.return_value = statuses or {
        "STRAT-001": "active",
    }
    mock._state = state_mock
    perf_mock = MagicMock()
    perf_mock.to_dict.return_value = {
        "strategy_id": "STRAT-001",
        "total_pnl": "0",
        "sharpe_ratio": "0",
    }
    mock.get_performance.return_value = perf_mock
    return mock


def _make_synthesizer(**kwargs):
    return InsightSynthesizer(
        price_feed=kwargs.get("price_feed", _make_price_feed()),
        gas_monitor=kwargs.get("gas_monitor", _make_gas_monitor()),
        defi_metrics=kwargs.get("defi_metrics", _make_defi_metrics()),
        position_tracker=kwargs.get("position_tracker", _make_position_tracker()),
        lifecycle_manager=kwargs.get("lifecycle_manager", _make_lifecycle_manager()),
        **{k: v for k, v in kwargs.items() if k not in {
            "price_feed", "gas_monitor", "defi_metrics",
            "position_tracker", "lifecycle_manager",
        }},
    )


# ---------------------------------------------------------------------------
# InsightSnapshot dataclass
# ---------------------------------------------------------------------------

class TestInsightSnapshot:

    def test_create_with_defaults(self) -> None:
        snap = InsightSnapshot(
            market_data={"prices": {}},
            positions={"open_count": 0},
            risk_status={"ok": True},
            strategies=[],
            recent_decisions=[],
        )
        assert snap.timestamp  # auto-set
        assert snap.snapshot_version == "1.0.0"

    def test_to_dict(self) -> None:
        snap = InsightSnapshot(
            market_data={"prices": {"ETH": "$3200"}},
            positions={"open_count": 1},
            risk_status={"ok": True},
            strategies=[{"id": "STRAT-001"}],
            recent_decisions=[{"action": "hold"}],
        )
        d = snap.to_dict()
        assert d["market_data"]["prices"]["ETH"] == "$3200"
        assert d["snapshot_version"] == "1.0.0"

    def test_from_dict(self) -> None:
        data = {
            "market_data": {},
            "positions": {},
            "risk_status": {},
            "strategies": [],
            "recent_decisions": [],
            "timestamp": "2026-01-01T00:00:00",
        }
        snap = InsightSnapshot.from_dict(data)
        assert snap.timestamp == "2026-01-01T00:00:00"


# ---------------------------------------------------------------------------
# Snapshot validation
# ---------------------------------------------------------------------------

class TestValidateSnapshot:

    def test_valid_snapshot(self) -> None:
        snap = InsightSnapshot(
            market_data={},
            positions={},
            risk_status={},
            strategies=[],
            recent_decisions=[],
        )
        valid, errors = validate_snapshot(snap.to_dict())
        assert valid
        assert not errors

    def test_missing_market_data(self) -> None:
        data = {
            "positions": {},
            "risk_status": {},
            "strategies": [],
            "recent_decisions": [],
        }
        valid, errors = validate_snapshot(data)
        assert not valid
        assert any("market_data" in e for e in errors)

    def test_missing_multiple_fields(self) -> None:
        valid, errors = validate_snapshot({})
        assert not valid
        assert len(errors) == 5  # All 5 required fields missing

    def test_wrong_type_market_data(self) -> None:
        data = {
            "market_data": "not a dict",
            "positions": {},
            "risk_status": {},
            "strategies": [],
            "recent_decisions": [],
        }
        valid, errors = validate_snapshot(data)
        assert not valid
        assert any("dict" in e for e in errors)

    def test_wrong_type_strategies(self) -> None:
        data = {
            "market_data": {},
            "positions": {},
            "risk_status": {},
            "strategies": "not a list",
            "recent_decisions": [],
        }
        valid, errors = validate_snapshot(data)
        assert not valid
        assert any("list" in e for e in errors)


# ---------------------------------------------------------------------------
# Compression helpers
# ---------------------------------------------------------------------------

class TestCompressPrices:

    def test_compresses_multi_source(self) -> None:
        prices = {
            "ETH": {"price_usd": 3200.50, "sources": ["coingecko", "defillama"]},
        }
        result = _compress_prices(prices)
        assert "ETH" in result
        assert "$3,200.50" in result["ETH"]
        assert "2 sources" in result["ETH"]

    def test_compresses_single_source(self) -> None:
        prices = {
            "USDC": {"price_usd": 1.0, "sources": ["coingecko"]},
        }
        result = _compress_prices(prices)
        assert "1 source" in result["USDC"]

    def test_handles_empty(self) -> None:
        assert _compress_prices({}) == {}


class TestCompressGas:

    def test_compresses_gas_data(self) -> None:
        gas = {"fast": 30, "standard": 20, "slow": 10, "is_spike": False}
        result = _compress_gas(gas)
        assert result["fast_gwei"] == "30"
        assert result["is_spike"] == "False"

    def test_handles_empty(self) -> None:
        result = _compress_gas({})
        assert result["status"] == "unavailable"


class TestCompressPositions:

    def test_compresses_summary(self) -> None:
        summary = {
            "open_count": 2,
            "total_value": "5000",
            "total_unrealized_pnl": "100",
            "total_realized_pnl": "50",
        }
        result = _compress_positions(summary)
        assert result["open_count"] == 2
        assert result["total_value"] == "5000"


class TestCompressDefiMetrics:

    def test_compresses_aave_style(self) -> None:
        metrics = {
            "aave": {
                "markets": [
                    {"symbol": "ETH", "supply_apy": 3.5, "utilization_rate": 80},
                    {"symbol": "USDC", "supply_apy": 4.2, "utilization_rate": 85},
                ],
            },
        }
        result = _compress_defi_metrics(metrics)
        assert "aave" in result
        assert result["aave"]["market_count"] == 2
        # Top market should be USDC (higher APY)
        top = result["aave"]["top_markets"][0]
        assert top["symbol"] == "USDC"

    def test_compresses_uniswap_style(self) -> None:
        metrics = {
            "uniswap_v3": {
                "pools": [
                    {"pair": "ETH/USDC", "volume_24h": 1000000},
                    {"pair": "WBTC/ETH", "volume_24h": 500000},
                ],
            },
        }
        result = _compress_defi_metrics(metrics)
        assert "uniswap_v3" in result
        assert result["uniswap_v3"]["pool_count"] == 2


# ---------------------------------------------------------------------------
# Rate trends computation
# ---------------------------------------------------------------------------

class TestComputeRateTrends:

    def test_first_cycle_no_trends(self) -> None:
        result = _compute_rate_trends({"aave": {}}, None)
        assert "note" in result

    def test_detects_apy_increase(self) -> None:
        current = {
            "aave": {
                "markets": [
                    {"symbol": "ETH", "supply_apy": 5.0},
                ],
            },
        }
        previous = {
            "aave": {
                "markets": [
                    {"symbol": "ETH", "supply_apy": 3.0},
                ],
            },
        }
        result = _compute_rate_trends(current, previous)
        assert "aave" in result
        changes = result["aave"]["rate_changes"]
        assert len(changes) == 1
        assert changes[0]["direction"] == "up"

    def test_detects_apy_decrease(self) -> None:
        current = {
            "aave": {
                "markets": [
                    {"symbol": "ETH", "supply_apy": 2.0},
                ],
            },
        }
        previous = {
            "aave": {
                "markets": [
                    {"symbol": "ETH", "supply_apy": 4.0},
                ],
            },
        }
        result = _compute_rate_trends(current, previous)
        changes = result["aave"]["rate_changes"]
        assert changes[0]["direction"] == "down"

    def test_ignores_small_changes(self) -> None:
        current = {
            "aave": {
                "markets": [
                    {"symbol": "ETH", "supply_apy": 3.02},
                ],
            },
        }
        previous = {
            "aave": {
                "markets": [
                    {"symbol": "ETH", "supply_apy": 3.01},
                ],
            },
        }
        result = _compute_rate_trends(current, previous)
        # Change < 1%, should not be flagged
        assert "aave" not in result


# ---------------------------------------------------------------------------
# Synthesizer -- data collection
# ---------------------------------------------------------------------------

class TestSynthesizerDataCollection:

    def test_collects_prices(self) -> None:
        pf = _make_price_feed({"ETH": {"price_usd": 3200, "sources": ["cg"]}})
        synth = _make_synthesizer(price_feed=pf)
        snapshot = synth.synthesize()
        assert "ETH" in snapshot.market_data["prices"]

    def test_collects_gas(self) -> None:
        gm = _make_gas_monitor(cached={"fast": 30, "standard": 20, "slow": 10, "timestamp": "now"})
        synth = _make_synthesizer(gas_monitor=gm)
        snapshot = synth.synthesize()
        assert "gas" in snapshot.market_data

    def test_collects_positions(self) -> None:
        tracker = _make_position_tracker()
        synth = _make_synthesizer(position_tracker=tracker)
        snapshot = synth.synthesize()
        assert "open_count" in snapshot.positions

    def test_collects_strategies(self) -> None:
        lm = _make_lifecycle_manager({"STRAT-001": "active", "STRAT-002": "evaluating"})
        synth = _make_synthesizer(lifecycle_manager=lm)
        snapshot = synth.synthesize()
        assert len(snapshot.strategies) == 2

    def test_graceful_on_price_failure(self) -> None:
        pf = MagicMock()
        pf.fetch_prices.side_effect = Exception("network error")
        synth = _make_synthesizer(price_feed=pf)
        snapshot = synth.synthesize()
        assert snapshot.market_data["prices"] == {}

    def test_graceful_on_gas_failure(self) -> None:
        gm = MagicMock()
        gm.get_cached_prices.side_effect = Exception("fail")
        synth = _make_synthesizer(gas_monitor=gm)
        snapshot = synth.synthesize()
        assert snapshot.market_data["gas"]["status"] == "unavailable"

    def test_graceful_on_defi_metrics_failure(self) -> None:
        dm = MagicMock()
        dm.get_metrics.side_effect = Exception("fail")
        synth = _make_synthesizer(defi_metrics=dm)
        snapshot = synth.synthesize()
        # Should have empty defi_protocols
        assert snapshot.market_data["defi_protocols"] == {}


# ---------------------------------------------------------------------------
# Synthesizer -- enrichment and compression
# ---------------------------------------------------------------------------

class TestSynthesizerEnrichment:

    def test_includes_derived_signals(self) -> None:
        synth = _make_synthesizer()
        snapshot = synth.synthesize()
        assert "derived_signals" in snapshot.market_data

    def test_rate_trends_populated_on_second_call(self) -> None:
        synth = _make_synthesizer()
        synth.synthesize()  # First call sets previous metrics
        snapshot = synth.synthesize()  # Second call can compute trends
        assert "rate_trends" in snapshot.market_data["derived_signals"]

    def test_token_efficient_prices(self) -> None:
        synth = _make_synthesizer()
        snapshot = synth.synthesize()
        prices = snapshot.market_data["prices"]
        # Compressed prices should be string summaries, not full dicts
        for token, summary in prices.items():
            assert isinstance(summary, str)
            assert "$" in summary


# ---------------------------------------------------------------------------
# Synthesizer -- decision history
# ---------------------------------------------------------------------------

class TestSynthesizerDecisionHistory:

    def test_records_decisions(self) -> None:
        synth = _make_synthesizer()
        synth.record_decision({"action": "hold", "strategy": "STRAT-001"})
        synth.record_decision({"action": "enter", "strategy": "STRAT-002"})
        snapshot = synth.synthesize()
        assert len(snapshot.recent_decisions) == 2

    def test_decision_history_capped(self) -> None:
        synth = _make_synthesizer(decision_history_size=3)
        for i in range(5):
            synth.record_decision({"action": "hold", "iteration": i})
        snapshot = synth.synthesize()
        assert len(snapshot.recent_decisions) == 3

    def test_decision_gets_timestamp(self) -> None:
        synth = _make_synthesizer()
        synth.record_decision({"action": "hold"})
        snapshot = synth.synthesize()
        assert "recorded_at" in snapshot.recent_decisions[0]


# ---------------------------------------------------------------------------
# Synthesizer -- schema validation
# ---------------------------------------------------------------------------

class TestSynthesizerValidation:

    def test_snapshot_validates_successfully(self) -> None:
        synth = _make_synthesizer()
        snapshot = synth.synthesize()
        valid, errors = validate_snapshot(snapshot.to_dict())
        assert valid
        assert not errors

    def test_snapshot_has_all_required_fields(self) -> None:
        synth = _make_synthesizer()
        snapshot = synth.synthesize()
        d = snapshot.to_dict()
        required = (
            "market_data", "positions", "risk_status",
            "strategies", "recent_decisions",
        )
        for field_name in required:
            assert field_name in d, f"Missing {field_name}"

    def test_snapshot_has_timestamp(self) -> None:
        synth = _make_synthesizer()
        snapshot = synth.synthesize()
        assert snapshot.timestamp

    def test_snapshot_has_version(self) -> None:
        synth = _make_synthesizer()
        snapshot = synth.synthesize()
        assert snapshot.snapshot_version == "1.0.0"


# ---------------------------------------------------------------------------
# Synthesizer -- with real position tracker
# ---------------------------------------------------------------------------

class TestSynthesizerWithPositions:

    def test_includes_position_details(self) -> None:
        tracker = PositionTracker()
        tracker.open_position(
            strategy="STRAT-001",
            protocol="aave",
            chain="ethereum",
            asset="ETH",
            entry_price="3200",
            amount="1.0",
        )
        synth = _make_synthesizer(position_tracker=tracker)
        snapshot = synth.synthesize()
        assert snapshot.positions["open_count"] == 1

    def test_includes_position_pnl(self) -> None:
        tracker = PositionTracker()
        tracker.open_position(
            strategy="STRAT-001",
            protocol="aave",
            chain="ethereum",
            asset="ETH",
            entry_price="3000",
            amount="1.0",
        )
        tracker.update_prices({"ETH": Decimal("3200")})
        synth = _make_synthesizer(position_tracker=tracker)
        snapshot = synth.synthesize()
        assert snapshot.positions["unrealized_pnl"] != "0"


# ---------------------------------------------------------------------------
# Synthesizer -- strategy inclusion
# ---------------------------------------------------------------------------

class TestSynthesizerStrategyInclusion:

    def test_includes_strategy_status(self) -> None:
        lm = _make_lifecycle_manager({"STRAT-001": "active"})
        synth = _make_synthesizer(lifecycle_manager=lm)
        snapshot = synth.synthesize()
        assert snapshot.strategies[0]["status"] == "active"

    def test_includes_strategy_performance(self) -> None:
        lm = _make_lifecycle_manager({"STRAT-001": "active"})
        synth = _make_synthesizer(lifecycle_manager=lm)
        snapshot = synth.synthesize()
        assert "performance" in snapshot.strategies[0]
