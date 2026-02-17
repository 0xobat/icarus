"""Tests for position tracker — PORT-002."""

from __future__ import annotations

from decimal import Decimal

from portfolio.position_tracker import Position, PositionTracker

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tracker_with_positions() -> PositionTracker:
    """Create a tracker with a few positions for testing."""
    t = PositionTracker()
    t.open_position(
        strategy="STRAT-001", protocol="aave", chain="ethereum",
        asset="ETH", entry_price="2000", amount="1.5",
        position_id="pos-eth",
        protocol_data={"supply_amount": "1.5", "earned_interest": "0.01"},
    )
    t.open_position(
        strategy="STRAT-003", protocol="uniswap", chain="ethereum",
        asset="WBTC", entry_price="40000", amount="0.1",
        position_id="pos-btc",
        protocol_data={"tick_lower": -100, "tick_upper": 100, "fees_earned": "5.0"},
    )
    t.open_position(
        strategy="STRAT-002", protocol="lido", chain="ethereum",
        asset="stETH", entry_price="2000", amount="2.0",
        position_id="pos-steth",
        protocol_data={"staked_amount": "2.0", "rewards": "0.05"},
    )
    return t


# ---------------------------------------------------------------------------
# Open / Close lifecycle
# ---------------------------------------------------------------------------

class TestLifecycle:
    """Position open/close with P&L tracking."""

    def test_open_creates_position(self) -> None:
        t = PositionTracker()
        pos = t.open_position(
            strategy="STRAT-001", protocol="aave", chain="ethereum",
            asset="ETH", entry_price="2000", amount="1.5",
        )
        assert pos.status == "open"
        assert pos.entry_price == Decimal("2000")
        assert pos.amount == Decimal("1.5")
        assert pos.current_value == Decimal("3000")
        assert pos.unrealized_pnl == Decimal(0)

    def test_open_with_custom_id(self) -> None:
        t = PositionTracker()
        pos = t.open_position(
            strategy="STRAT-001", protocol="aave", chain="ethereum",
            asset="ETH", entry_price="2000", amount="1",
            position_id="my-id",
        )
        assert pos.id == "my-id"

    def test_close_calculates_realized_pnl(self) -> None:
        t = PositionTracker()
        t.open_position(
            strategy="STRAT-001", protocol="aave", chain="ethereum",
            asset="ETH", entry_price="2000", amount="1.0",
            position_id="p1",
        )
        closed = t.close_position("p1", exit_price="2500")
        assert closed is not None
        assert closed.status == "closed"
        assert closed.realized_pnl == Decimal("500")  # (2500-2000)*1
        assert closed.close_time is not None
        assert closed.unrealized_pnl == Decimal(0)

    def test_close_with_loss(self) -> None:
        t = PositionTracker()
        t.open_position(
            strategy="STRAT-001", protocol="aave", chain="ethereum",
            asset="ETH", entry_price="2000", amount="1.0",
            position_id="p1",
        )
        closed = t.close_position("p1", exit_price="1800")
        assert closed is not None
        assert closed.realized_pnl == Decimal("-200")

    def test_close_without_exit_price_uses_current(self) -> None:
        t = PositionTracker()
        t.open_position(
            strategy="STRAT-001", protocol="aave", chain="ethereum",
            asset="ETH", entry_price="2000", amount="1.0",
            position_id="p1",
        )
        t.update_prices({"ETH": "2200"})
        closed = t.close_position("p1")
        assert closed is not None
        assert closed.realized_pnl == Decimal("200")

    def test_close_unknown_returns_none(self) -> None:
        t = PositionTracker()
        result = t.close_position("nonexistent")
        assert result is None

    def test_closed_removed_from_open(self) -> None:
        t = PositionTracker()
        t.open_position(
            strategy="STRAT-001", protocol="aave", chain="ethereum",
            asset="ETH", entry_price="2000", amount="1",
            position_id="p1",
        )
        t.close_position("p1", exit_price="2100")
        assert t.get_position("p1") is None
        assert t.get_summary()["open_count"] == 0
        assert t.get_summary()["closed_count"] == 1


# ---------------------------------------------------------------------------
# P&L calculation
# ---------------------------------------------------------------------------

class TestPnlCalculation:
    """Unrealized and realized P&L must be accurate."""

    def test_unrealized_after_price_increase(self) -> None:
        t = PositionTracker()
        t.open_position(
            strategy="STRAT-001", protocol="aave", chain="ethereum",
            asset="ETH", entry_price="2000", amount="2.0",
            position_id="p1",
        )
        t.update_prices({"ETH": "2500"})
        pos = t.get_position("p1")
        assert pos is not None
        assert pos.current_value == Decimal("5000")
        assert pos.unrealized_pnl == Decimal("1000")

    def test_unrealized_after_price_decrease(self) -> None:
        t = PositionTracker()
        t.open_position(
            strategy="STRAT-001", protocol="aave", chain="ethereum",
            asset="ETH", entry_price="2000", amount="1.0",
            position_id="p1",
        )
        t.update_prices({"ETH": "1500"})
        pos = t.get_position("p1")
        assert pos is not None
        assert pos.unrealized_pnl == Decimal("-500")

    def test_multiple_updates(self) -> None:
        t = PositionTracker()
        t.open_position(
            strategy="STRAT-001", protocol="aave", chain="ethereum",
            asset="ETH", entry_price="2000", amount="1.0",
            position_id="p1",
        )
        t.update_prices({"ETH": "2100"})
        assert t.get_position("p1").unrealized_pnl == Decimal("100")
        t.update_prices({"ETH": "1900"})
        assert t.get_position("p1").unrealized_pnl == Decimal("-100")

    def test_price_update_ignores_unknown_assets(self) -> None:
        t = PositionTracker()
        t.open_position(
            strategy="STRAT-001", protocol="aave", chain="ethereum",
            asset="ETH", entry_price="2000", amount="1.0",
            position_id="p1",
        )
        t.update_prices({"WBTC": "40000"})
        # ETH position unchanged
        assert t.get_position("p1").current_value == Decimal("2000")


# ---------------------------------------------------------------------------
# Query filtering
# ---------------------------------------------------------------------------

class TestQueryFiltering:
    """Positions must be queryable by strategy, protocol, chain, asset."""

    def test_query_all(self) -> None:
        t = _tracker_with_positions()
        assert len(t.query()) == 3

    def test_query_by_strategy(self) -> None:
        t = _tracker_with_positions()
        results = t.query(strategy="STRAT-001")
        assert len(results) == 1
        assert results[0].asset == "ETH"

    def test_query_by_protocol(self) -> None:
        t = _tracker_with_positions()
        results = t.query(protocol="lido")
        assert len(results) == 1
        assert results[0].asset == "stETH"

    def test_query_by_chain(self) -> None:
        t = _tracker_with_positions()
        results = t.query(chain="ethereum")
        assert len(results) == 3

    def test_query_by_asset(self) -> None:
        t = _tracker_with_positions()
        results = t.query(asset="WBTC")
        assert len(results) == 1

    def test_query_combined_filters(self) -> None:
        t = _tracker_with_positions()
        results = t.query(protocol="aave", chain="ethereum")
        assert len(results) == 1
        assert results[0].id == "pos-eth"

    def test_query_no_matches(self) -> None:
        t = _tracker_with_positions()
        results = t.query(protocol="compound")
        assert len(results) == 0

    def test_query_include_closed(self) -> None:
        t = _tracker_with_positions()
        t.close_position("pos-btc", exit_price="42000")
        # Default excludes closed
        assert len(t.query()) == 2
        # Include closed
        assert len(t.query(include_closed=True)) == 3


# ---------------------------------------------------------------------------
# Protocol-specific tracking
# ---------------------------------------------------------------------------

class TestProtocolSpecific:
    """Protocol-specific data stored in protocol_data dict."""

    def test_aave_fields(self) -> None:
        t = _tracker_with_positions()
        pos = t.get_position("pos-eth")
        assert pos.protocol_data["supply_amount"] == "1.5"
        assert pos.protocol_data["earned_interest"] == "0.01"

    def test_uniswap_fields(self) -> None:
        t = _tracker_with_positions()
        pos = t.get_position("pos-btc")
        assert pos.protocol_data["tick_lower"] == -100
        assert pos.protocol_data["tick_upper"] == 100
        assert pos.protocol_data["fees_earned"] == "5.0"

    def test_lido_fields(self) -> None:
        t = _tracker_with_positions()
        pos = t.get_position("pos-steth")
        assert pos.protocol_data["staked_amount"] == "2.0"
        assert pos.protocol_data["rewards"] == "0.05"


# ---------------------------------------------------------------------------
# Execution result handling
# ---------------------------------------------------------------------------

class TestExecutionResult:
    """on_execution_result processes Redis execution:results messages."""

    def test_close_on_confirmed(self) -> None:
        t = PositionTracker()
        t.open_position(
            strategy="STRAT-001", protocol="aave", chain="ethereum",
            asset="ETH", entry_price="2000", amount="1.0",
            position_id="p1",
        )
        t.on_execution_result({
            "position_id": "p1",
            "status": "confirmed",
            "action": "close",
            "fill_price": "2300",
        })
        assert t.get_position("p1") is None
        assert t.get_summary()["closed_count"] == 1

    def test_failed_does_not_close(self) -> None:
        t = PositionTracker()
        t.open_position(
            strategy="STRAT-001", protocol="aave", chain="ethereum",
            asset="ETH", entry_price="2000", amount="1.0",
            position_id="p1",
        )
        t.on_execution_result({
            "position_id": "p1",
            "status": "failed",
            "action": "close",
            "reason": "reverted",
        })
        assert t.get_position("p1") is not None

    def test_open_updates_fill_price(self) -> None:
        t = PositionTracker()
        t.open_position(
            strategy="STRAT-001", protocol="aave", chain="ethereum",
            asset="ETH", entry_price="2000", amount="1.0",
            position_id="p1",
        )
        t.on_execution_result({
            "position_id": "p1",
            "status": "confirmed",
            "action": "open",
            "fill_price": "2010",
        })
        pos = t.get_position("p1")
        assert pos.entry_price == Decimal("2010")
        assert pos.current_value == Decimal("2010")


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

class TestSummary:
    """get_summary provides aggregate stats."""

    def test_empty_tracker(self) -> None:
        t = PositionTracker()
        s = t.get_summary()
        assert s["open_count"] == 0
        assert s["closed_count"] == 0
        assert s["total_value"] == "0"
        assert s["total_unrealized_pnl"] == "0"

    def test_with_positions(self) -> None:
        t = _tracker_with_positions()
        s = t.get_summary()
        assert s["open_count"] == 3
        # ETH: 2000*1.5=3000, WBTC: 40000*0.1=4000, stETH: 2000*2=4000
        assert s["total_value"] == "11000.0"

    def test_includes_realized_after_close(self) -> None:
        t = PositionTracker()
        t.open_position(
            strategy="STRAT-001", protocol="aave", chain="ethereum",
            asset="ETH", entry_price="2000", amount="1.0",
            position_id="p1",
        )
        t.close_position("p1", exit_price="2500")
        s = t.get_summary()
        assert Decimal(s["total_realized_pnl"]) == Decimal("500")


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

class TestPersistence:
    """State serialization round-trip."""

    def test_round_trip(self) -> None:
        t = _tracker_with_positions()
        t.close_position("pos-btc", exit_price="41000")
        data = t.to_state_dict()

        t2 = PositionTracker.from_state_dict(data)
        assert len(t2.query()) == 2  # 2 open
        assert len(t2.query(include_closed=True)) == 3
        assert t2.get_position("pos-eth").entry_price == Decimal("2000")

    def test_empty_round_trip(self) -> None:
        t = PositionTracker()
        data = t.to_state_dict()
        t2 = PositionTracker.from_state_dict(data)
        assert t2.get_summary()["open_count"] == 0

    def test_postgres_stub(self) -> None:
        t = _tracker_with_positions()
        t.backup_to_postgres()  # should not raise


# ---------------------------------------------------------------------------
# Position dataclass
# ---------------------------------------------------------------------------

class TestPositionDataclass:
    """Position serialization and deserialization."""

    def test_to_dict_and_back(self) -> None:
        pos = Position(
            id="test", strategy="S1", protocol="aave", chain="ethereum",
            asset="ETH", entry_price=Decimal("2000"), entry_time="2026-01-01T00:00:00+00:00",
            amount=Decimal("1.5"), current_value=Decimal("3000"),
        )
        d = pos.to_dict()
        assert d["entry_price"] == "2000"
        restored = Position.from_dict(d)
        assert restored.entry_price == Decimal("2000")
        assert restored.amount == Decimal("1.5")
