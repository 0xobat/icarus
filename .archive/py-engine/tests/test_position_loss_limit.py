"""Tests for per-position loss limit — RISK-002."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock

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


# ---------------------------------------------------------------------------
# Direct emission — generate_close_orders
# ---------------------------------------------------------------------------
class TestGenerateCloseOrders:

    def _make_positions(self) -> list[dict]:
        return [
            {
                "id": "pos-1",
                "asset": "ETH",
                "entry_price": "2000",
                "current_value": "1700",
                "protocol": "aave_v3",
                "strategy_id": "LEND-001",
                "entry_time": datetime.now(UTC).isoformat(),
            },
            {
                "id": "pos-2",
                "asset": "USDC",
                "entry_price": "1",
                "current_value": "0.99",
                "protocol": "aerodrome",
                "strategy_id": "LP-001",
                "entry_time": datetime.now(UTC).isoformat(),
            },
        ]

    def test_generates_orders_for_breached_positions(self) -> None:
        lim = _make_limiter()
        positions = self._make_positions()
        price_map = {
            "ETH": Decimal("1700"),   # 15% loss — breaches
            "USDC": Decimal("0.99"),  # 1% loss — safe
        }
        orders = lim.generate_close_orders(
            positions=positions,
            price_map=price_map,
            correlation_id="cid-test",
        )
        assert len(orders) == 1
        order = orders[0]
        assert order["strategy"] == "CB:position_loss"
        assert order["action"] == "withdraw"
        assert order["priority"] == "urgent"
        assert order["chain"] == "base"
        assert order["protocol"] == "aave_v3"
        assert order["params"]["tokenIn"] == "ETH"
        assert order["params"]["amount"] == "1700"

    def test_schema_fields_present(self) -> None:
        lim = _make_limiter()
        positions = self._make_positions()
        price_map = {"ETH": Decimal("1700"), "USDC": Decimal("0.99")}
        orders = lim.generate_close_orders(
            positions=positions,
            price_map=price_map,
            correlation_id="cid-schema",
        )
        assert len(orders) == 1
        order = orders[0]
        # All required schema fields
        assert order["version"] == "1.0.0"
        assert "orderId" in order
        assert len(order["orderId"]) > 0
        assert order["correlationId"] == "cid-schema"
        assert "timestamp" in order
        assert order["chain"] in ("ethereum", "base")
        assert order["protocol"] in ("aave_v3", "aerodrome")
        assert order["action"] == "withdraw"
        assert order["strategy"] == "CB:position_loss"
        assert order["priority"] in ("urgent", "normal", "low")
        # Limits
        assert "limits" in order
        assert "maxGasWei" in order["limits"]
        assert "maxSlippageBps" in order["limits"]
        assert "deadlineUnix" in order["limits"]
        assert isinstance(order["limits"]["deadlineUnix"], int)

    def test_records_loss_events_when_orders_generated(self) -> None:
        lim = _make_limiter()
        positions = self._make_positions()
        price_map = {"ETH": Decimal("1700"), "USDC": Decimal("0.99")}
        lim.generate_close_orders(
            positions=positions,
            price_map=price_map,
            correlation_id="cid-loss",
        )
        assert len(lim.loss_events) == 1
        event = lim.loss_events[0]
        assert event.position_id == "pos-1"
        assert event.strategy_id == "LEND-001"
        assert event.asset == "ETH"

    def test_cooldown_started_after_order(self) -> None:
        lim = _make_limiter()
        positions = self._make_positions()
        price_map = {"ETH": Decimal("1700"), "USDC": Decimal("0.99")}
        lim.generate_close_orders(
            positions=positions,
            price_map=price_map,
            correlation_id="cid-cd",
        )
        assert lim.is_strategy_in_cooldown("LEND-001")
        assert not lim.is_strategy_in_cooldown("LP-001")

    def test_no_orders_when_no_breach(self) -> None:
        lim = _make_limiter()
        positions = self._make_positions()
        price_map = {
            "ETH": Decimal("1950"),   # 2.5% loss — safe
            "USDC": Decimal("0.99"),  # 1% loss — safe
        }
        orders = lim.generate_close_orders(
            positions=positions,
            price_map=price_map,
            correlation_id="cid-safe",
        )
        assert orders == []
        assert len(lim.loss_events) == 0

    def test_multiple_breaches_generate_multiple_orders(self) -> None:
        lim = _make_limiter()
        positions = [
            {
                "id": "pos-1",
                "asset": "ETH",
                "entry_price": "2000",
                "current_value": "1700",
                "protocol": "aave_v3",
                "strategy_id": "LEND-001",
                "entry_time": datetime.now(UTC).isoformat(),
            },
            {
                "id": "pos-2",
                "asset": "WBTC",
                "entry_price": "50000",
                "current_value": "42000",
                "protocol": "aerodrome",
                "strategy_id": "LP-001",
                "entry_time": datetime.now(UTC).isoformat(),
            },
        ]
        price_map = {
            "ETH": Decimal("1700"),    # 15% loss
            "WBTC": Decimal("42000"),  # 16% loss
        }
        orders = lim.generate_close_orders(
            positions=positions,
            price_map=price_map,
            correlation_id="cid-multi",
        )
        assert len(orders) == 2
        assert len(lim.loss_events) == 2
        strategies = {o["strategy"] for o in orders}
        assert strategies == {"CB:position_loss"}


# ---------------------------------------------------------------------------
# Redis TTL cooldown
# ---------------------------------------------------------------------------
class TestRedisTTLCooldown:

    def _mock_redis(self) -> MagicMock:
        """Create a mock RedisManager with mock client."""
        redis_mgr = MagicMock()
        mock_client = MagicMock()
        redis_mgr.client = mock_client
        return redis_mgr

    def test_sets_redis_ttl_key_on_loss_event(self) -> None:
        redis_mgr = self._mock_redis()
        lim = _make_limiter(redis=redis_mgr, cooldown_hours=24)
        lim.record_loss_event(
            position_id="p1",
            strategy_id="LEND-001",
            asset="ETH",
            entry_price=Decimal("2000"),
            exit_price=Decimal("1780"),
            entry_time=datetime.now(UTC).isoformat(),
        )
        redis_mgr.client.set.assert_called_once()
        call_args = redis_mgr.client.set.call_args
        assert call_args[0][0] == "cooldown:LEND-001"
        assert call_args[1]["ex"] == 24 * 3600

    def test_checks_redis_for_cooldown(self) -> None:
        redis_mgr = self._mock_redis()
        redis_mgr.client.exists.return_value = True
        lim = _make_limiter(redis=redis_mgr)
        assert lim.is_strategy_in_cooldown("LEND-001")
        redis_mgr.client.exists.assert_called_with("cooldown:LEND-001")

    def test_falls_back_to_memory_when_redis_not_in_cooldown(self) -> None:
        redis_mgr = self._mock_redis()
        redis_mgr.client.exists.return_value = False
        lim = _make_limiter(redis=redis_mgr)
        # No in-memory cooldown either
        assert not lim.is_strategy_in_cooldown("LEND-001")

    def test_redis_cooldown_survives_without_memory(self) -> None:
        """Redis says in cooldown even though in-memory dict is empty."""
        redis_mgr = self._mock_redis()
        redis_mgr.client.exists.return_value = True
        lim = _make_limiter(redis=redis_mgr)
        # In-memory has nothing, but Redis says yes
        assert lim.is_strategy_in_cooldown("LEND-001")

    def test_no_redis_uses_memory_only(self) -> None:
        """Without redis parameter, only in-memory cooldowns are used."""
        lim = _make_limiter()
        assert not lim.is_strategy_in_cooldown("LEND-001")
        lim.record_loss_event(
            position_id="p1",
            strategy_id="LEND-001",
            asset="ETH",
            entry_price=Decimal("100"),
            exit_price=Decimal("85"),
            entry_time=datetime.now(UTC).isoformat(),
        )
        assert lim.is_strategy_in_cooldown("LEND-001")

    def test_redis_error_falls_back_to_memory(self) -> None:
        """When Redis raises, fall back to in-memory cooldown."""
        redis_mgr = self._mock_redis()
        redis_mgr.client.exists.side_effect = Exception("connection lost")
        lim = _make_limiter(redis=redis_mgr)
        # Record a loss to set in-memory cooldown
        lim.record_loss_event(
            position_id="p1",
            strategy_id="LEND-001",
            asset="ETH",
            entry_price=Decimal("100"),
            exit_price=Decimal("85"),
            entry_time=datetime.now(UTC).isoformat(),
        )
        # Redis errors but in-memory says cooldown active
        assert lim.is_strategy_in_cooldown("LEND-001")

    def test_redis_set_error_does_not_crash(self) -> None:
        """When Redis SET fails, the loss event is still recorded."""
        redis_mgr = self._mock_redis()
        redis_mgr.client.set.side_effect = Exception("connection lost")
        lim = _make_limiter(redis=redis_mgr)
        event = lim.record_loss_event(
            position_id="p1",
            strategy_id="LEND-001",
            asset="ETH",
            entry_price=Decimal("100"),
            exit_price=Decimal("85"),
            entry_time=datetime.now(UTC).isoformat(),
        )
        assert event is not None
        assert len(lim.loss_events) == 1
        # In-memory cooldown still works
        assert lim.is_strategy_in_cooldown("LEND-001")

    def test_generate_close_orders_sets_redis_cooldown(self) -> None:
        """generate_close_orders sets Redis TTL via record_loss_event."""
        redis_mgr = self._mock_redis()
        lim = _make_limiter(redis=redis_mgr, cooldown_hours=24)
        positions = [
            {
                "id": "pos-1",
                "asset": "ETH",
                "entry_price": "2000",
                "current_value": "1700",
                "protocol": "aave_v3",
                "strategy_id": "LEND-001",
                "entry_time": datetime.now(UTC).isoformat(),
            },
        ]
        price_map = {"ETH": Decimal("1700")}
        lim.generate_close_orders(
            positions=positions,
            price_map=price_map,
            correlation_id="cid-redis",
        )
        redis_mgr.client.set.assert_called_once()
        call_args = redis_mgr.client.set.call_args
        assert call_args[0][0] == "cooldown:LEND-001"
        assert call_args[1]["ex"] == 86400


# ---------------------------------------------------------------------------
# is_any_in_cooldown() (used by InsightSynthesizer)
# ---------------------------------------------------------------------------
class TestIsAnyInCooldown:

    def test_no_cooldowns(self) -> None:
        lim = PositionLossLimit()
        assert lim.is_any_in_cooldown() is False

    def test_active_cooldown(self) -> None:
        lim = PositionLossLimit()
        lim.record_loss_event(
            position_id="p1",
            strategy_id="STRAT-001",
            asset="ETH",
            entry_price=Decimal("2000"),
            exit_price=Decimal("1700"),
            entry_time=datetime.now(UTC).isoformat(),
        )
        assert lim.is_any_in_cooldown() is True

    def test_expired_cooldown(self) -> None:
        lim = PositionLossLimit(cooldown_hours=1)
        lim.record_loss_event(
            position_id="p1",
            strategy_id="STRAT-001",
            asset="ETH",
            entry_price=Decimal("2000"),
            exit_price=Decimal("1700"),
            entry_time=datetime.now(UTC).isoformat(),
        )
        future = datetime.now(UTC) + timedelta(hours=2)
        assert lim.is_any_in_cooldown(now=future) is False
