"""Tests for per-position loss limit — RISK-002."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from risk.position_loss_limit import (
    DEFAULT_COOLDOWN_HOURS,
    DEFAULT_LOSS_THRESHOLD,
    PositionLossLimit,
)


def _make_limiter(**kwargs) -> PositionLossLimit:
    return PositionLossLimit(**kwargs)


# ---------------------------------------------------------------------------
# Loss threshold detection
# ---------------------------------------------------------------------------
class TestLossDetection:

    def test_detects_loss_above_threshold(self) -> None:
        lim = _make_limiter()
        check = lim.check_position(
            position_id="p1",
            entry_price=Decimal("100"),
            current_price=Decimal("89"),  # 11% loss
        )
        assert check.should_close
        assert check.loss_pct > Decimal("0.10")

    def test_no_close_below_threshold(self) -> None:
        lim = _make_limiter()
        check = lim.check_position(
            position_id="p1",
            entry_price=Decimal("100"),
            current_price=Decimal("92"),  # 8% loss
        )
        assert not check.should_close

    def test_no_close_at_exact_threshold(self) -> None:
        lim = _make_limiter()
        check = lim.check_position(
            position_id="p1",
            entry_price=Decimal("100"),
            current_price=Decimal("90"),  # exactly 10%
        )
        assert not check.should_close

    def test_no_close_on_profit(self) -> None:
        lim = _make_limiter()
        check = lim.check_position(
            position_id="p1",
            entry_price=Decimal("100"),
            current_price=Decimal("110"),  # +10%
        )
        assert not check.should_close
        assert check.loss_pct < 0

    def test_handles_zero_entry_price(self) -> None:
        lim = _make_limiter()
        check = lim.check_position(
            position_id="p1",
            entry_price=Decimal("0"),
            current_price=Decimal("100"),
        )
        assert not check.should_close
        assert check.reason == "invalid entry price"

    def test_custom_threshold(self) -> None:
        lim = _make_limiter(loss_threshold=Decimal("0.05"))
        check = lim.check_position(
            position_id="p1",
            entry_price=Decimal("100"),
            current_price=Decimal("94"),  # 6% loss
        )
        assert check.should_close

    def test_default_threshold(self) -> None:
        assert DEFAULT_LOSS_THRESHOLD == Decimal("0.10")


# ---------------------------------------------------------------------------
# Loss event recording
# ---------------------------------------------------------------------------
class TestLossEvents:

    def test_records_loss_event(self) -> None:
        lim = _make_limiter()
        now = datetime.now(UTC)
        entry_time = (now - timedelta(hours=2)).isoformat()
        event = lim.record_loss_event(
            position_id="p1",
            strategy_id="STRAT-001",
            asset="ETH",
            entry_price=Decimal("2000"),
            exit_price=Decimal("1780"),
            entry_time=entry_time,
        )
        assert event.position_id == "p1"
        assert event.strategy_id == "STRAT-001"
        assert event.asset == "ETH"
        assert event.entry_price == Decimal("2000")
        assert event.exit_price == Decimal("1780")
        assert event.loss_pct == Decimal("0.11")
        assert event.duration_seconds > 0

    def test_loss_events_accumulate(self) -> None:
        lim = _make_limiter()
        now = datetime.now(UTC).isoformat()
        lim.record_loss_event(
            position_id="p1", strategy_id="STRAT-001",
            asset="ETH", entry_price=Decimal("100"),
            exit_price=Decimal("85"), entry_time=now,
        )
        lim.record_loss_event(
            position_id="p2", strategy_id="STRAT-001",
            asset="WBTC", entry_price=Decimal("50000"),
            exit_price=Decimal("44000"), entry_time=now,
        )
        assert len(lim.loss_events) == 2

    def test_to_dict(self) -> None:
        lim = _make_limiter()
        event = lim.record_loss_event(
            position_id="p1", strategy_id="STRAT-001",
            asset="ETH", entry_price=Decimal("2000"),
            exit_price=Decimal("1780"),
            entry_time=datetime.now(UTC).isoformat(),
        )
        d = event.to_dict()
        assert d["position_id"] == "p1"
        assert d["strategy_id"] == "STRAT-001"
        assert "entry_price" in d
        assert "exit_price" in d
        assert "loss_pct" in d
        assert "duration_seconds" in d


# ---------------------------------------------------------------------------
# Strategy cooldown
# ---------------------------------------------------------------------------
class TestStrategyCooldown:

    def test_cooldown_after_loss(self) -> None:
        lim = _make_limiter()
        lim.record_loss_event(
            position_id="p1", strategy_id="STRAT-001",
            asset="ETH", entry_price=Decimal("100"),
            exit_price=Decimal("85"),
            entry_time=datetime.now(UTC).isoformat(),
        )
        assert lim.is_strategy_in_cooldown("STRAT-001")
        assert not lim.can_open_position("STRAT-001")

    def test_no_cooldown_without_loss(self) -> None:
        lim = _make_limiter()
        assert not lim.is_strategy_in_cooldown("STRAT-001")
        assert lim.can_open_position("STRAT-001")

    def test_cooldown_expires(self) -> None:
        lim = _make_limiter(cooldown_hours=24)
        lim.record_loss_event(
            position_id="p1", strategy_id="STRAT-001",
            asset="ETH", entry_price=Decimal("100"),
            exit_price=Decimal("85"),
            entry_time=datetime.now(UTC).isoformat(),
        )
        # Simulate 25 hours later
        future = datetime.now(UTC) + timedelta(hours=25)
        assert not lim.is_strategy_in_cooldown("STRAT-001", now=future)
        assert lim.can_open_position("STRAT-001", now=future)

    def test_cooldown_still_active(self) -> None:
        lim = _make_limiter(cooldown_hours=24)
        lim.record_loss_event(
            position_id="p1", strategy_id="STRAT-001",
            asset="ETH", entry_price=Decimal("100"),
            exit_price=Decimal("85"),
            entry_time=datetime.now(UTC).isoformat(),
        )
        # 12 hours later — still in cooldown
        future = datetime.now(UTC) + timedelta(hours=12)
        assert lim.is_strategy_in_cooldown("STRAT-001", now=future)

    def test_cooldown_remaining(self) -> None:
        lim = _make_limiter(cooldown_hours=24)
        lim.record_loss_event(
            position_id="p1", strategy_id="STRAT-001",
            asset="ETH", entry_price=Decimal("100"),
            exit_price=Decimal("85"),
            entry_time=datetime.now(UTC).isoformat(),
        )
        remaining = lim.get_cooldown_remaining("STRAT-001")
        assert remaining is not None
        assert remaining.total_seconds() > 0

    def test_cooldown_remaining_none_when_expired(self) -> None:
        lim = _make_limiter(cooldown_hours=24)
        lim.record_loss_event(
            position_id="p1", strategy_id="STRAT-001",
            asset="ETH", entry_price=Decimal("100"),
            exit_price=Decimal("85"),
            entry_time=datetime.now(UTC).isoformat(),
        )
        future = datetime.now(UTC) + timedelta(hours=25)
        assert lim.get_cooldown_remaining("STRAT-001", now=future) is None

    def test_cooldown_remaining_none_without_loss(self) -> None:
        lim = _make_limiter()
        assert lim.get_cooldown_remaining("STRAT-001") is None

    def test_default_cooldown_hours(self) -> None:
        assert DEFAULT_COOLDOWN_HOURS == 24

    def test_per_strategy_cooldown(self) -> None:
        lim = _make_limiter()
        lim.record_loss_event(
            position_id="p1", strategy_id="STRAT-001",
            asset="ETH", entry_price=Decimal("100"),
            exit_price=Decimal("85"),
            entry_time=datetime.now(UTC).isoformat(),
        )
        assert lim.is_strategy_in_cooldown("STRAT-001")
        assert not lim.is_strategy_in_cooldown("STRAT-002")


# ---------------------------------------------------------------------------
# Batch position checking
# ---------------------------------------------------------------------------
class TestBatchCheck:

    def test_check_all_positions(self) -> None:
        lim = _make_limiter()
        positions = [
            {"id": "p1", "asset": "ETH", "entry_price": "2000"},
            {"id": "p2", "asset": "WBTC", "entry_price": "50000"},
            {"id": "p3", "asset": "USDC", "entry_price": "1"},
        ]
        price_map = {
            "ETH": Decimal("1700"),    # 15% loss
            "WBTC": Decimal("46000"),  # 8% loss
            "USDC": Decimal("0.99"),   # 1% loss
        }
        results = lim.check_all_positions(positions, price_map)
        assert len(results) == 1
        assert results[0].position_id == "p1"
        assert results[0].should_close

    def test_empty_positions(self) -> None:
        lim = _make_limiter()
        results = lim.check_all_positions([], {"ETH": Decimal("2000")})
        assert len(results) == 0

    def test_missing_price_skipped(self) -> None:
        lim = _make_limiter()
        positions = [
            {"id": "p1", "asset": "ETH", "entry_price": "2000"},
        ]
        results = lim.check_all_positions(positions, {})
        assert len(results) == 0

    def test_multiple_closures(self) -> None:
        lim = _make_limiter()
        positions = [
            {"id": "p1", "asset": "ETH", "entry_price": "2000"},
            {"id": "p2", "asset": "WBTC", "entry_price": "50000"},
        ]
        price_map = {
            "ETH": Decimal("1700"),    # 15% loss
            "WBTC": Decimal("42000"),  # 16% loss
        }
        results = lim.check_all_positions(positions, price_map)
        assert len(results) == 2
