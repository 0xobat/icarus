"""Tests for deployment-blocking bug fixes (Issues 1, 5, 7).

Issue 1: Position objects converted to dicts before passing to circuit breakers.
Issue 5: discover_strategies() wired into main().
Issue 7: maxGasWei configurable via MAX_GAS_WEI env var.
"""

from __future__ import annotations

import os
from decimal import Decimal
from unittest.mock import patch

from ai.decision_engine import Decision, DecisionAction
from main import DecisionLoop, _positions_as_dicts
from portfolio.position_tracker import Position
from risk.drawdown_breaker import DrawdownBreaker
from risk.position_loss_limit import PositionLossLimit
from risk.tvl_monitor import TVLMonitor

# ---------------------------------------------------------------------------
# Issue 1: Position.to_dict() produces dicts usable by circuit breakers
# ---------------------------------------------------------------------------


class TestPositionToDictCompat:
    """Verify Position.to_dict() output works with circuit breaker methods."""

    def _make_position(self, **overrides) -> Position:
        defaults = {
            "id": "pos-1",
            "strategy": "test-strat",
            "protocol": "aave_v3",
            "chain": "base",
            "asset": "ETH",
            "entry_price": Decimal("2000"),
            "entry_time": "2026-01-01T00:00:00+00:00",
            "amount": Decimal("1"),
            "current_value": Decimal("1800"),
        }
        defaults.update(overrides)
        return Position(**defaults)

    def test_to_dict_has_get_method(self) -> None:
        pos = self._make_position()
        d = pos.to_dict()
        assert hasattr(d, "get"), "to_dict() result must support .get()"

    def test_to_dict_keys_for_drawdown(self) -> None:
        pos = self._make_position()
        d = pos.to_dict()
        assert d.get("asset") == "ETH"
        assert d.get("protocol") == "aave_v3"

    def test_to_dict_keys_for_position_loss(self) -> None:
        pos = self._make_position()
        d = pos.to_dict()
        assert d.get("id") == "pos-1"
        assert d.get("entry_price") is not None
        assert d.get("strategy") == "test-strat"

    def test_to_dict_keys_for_tvl_monitor(self) -> None:
        pos = self._make_position()
        d = pos.to_dict()
        assert d.get("protocol") == "aave_v3"
        assert d.get("current_value") is not None

    def test_drawdown_unwind_with_position_dicts(self) -> None:
        breaker = DrawdownBreaker(initial_value=Decimal("10000"))
        breaker.update(Decimal("7000"))
        pos = self._make_position()
        orders = breaker.get_unwind_orders(
            positions=[pos.to_dict()],
            correlation_id="test-corr",
        )
        assert len(orders) == 1
        assert orders[0]["params"]["tokenIn"] == "ETH"

    def test_positions_as_dicts_handles_position_objects(self) -> None:
        pos = self._make_position()
        result = _positions_as_dicts([pos])
        assert isinstance(result[0], dict)
        assert result[0]["asset"] == "ETH"

    def test_positions_as_dicts_handles_plain_dicts(self) -> None:
        d = {"id": "p1", "asset": "ETH"}
        result = _positions_as_dicts([d])
        assert result[0] is d

    def test_tvl_generate_withdrawal_with_position_dicts(self) -> None:
        monitor = TVLMonitor()
        monitor.record_tvl("aave_v3", "base", Decimal("1000000"), "test")
        monitor.record_tvl("aave_v3", "base", Decimal("500000"), "test")
        pos = self._make_position(protocol="aave_v3")
        orders = monitor.generate_withdrawal_orders(
            positions=[pos.to_dict()],
            correlation_id="test-corr",
        )
        assert len(orders) == 1
        assert orders[0]["params"]["tokenIn"] == "ETH"

    def test_position_loss_with_position_dicts(self) -> None:
        limiter = PositionLossLimit()
        pos = self._make_position(
            entry_price=Decimal("2000"),
            current_value=Decimal("1700"),
        )
        price_map = {"ETH": Decimal("1700")}
        orders = limiter.generate_close_orders(
            positions=[pos.to_dict()],
            price_map=price_map,
            correlation_id="test-corr",
        )
        assert len(orders) == 1
        assert orders[0]["strategy"] == "CB:position_loss"


# ---------------------------------------------------------------------------
# Issue 5: discover_strategies() is callable and returns strategies
# ---------------------------------------------------------------------------


class TestDiscoverStrategiesWiring:
    """Verify discover_strategies works and main imports it."""

    def test_discover_strategies_importable_from_main(self) -> None:
        """main.py imports discover_strategies."""
        import main
        assert hasattr(main, "discover_strategies")

    def test_discover_returns_dict(self) -> None:
        from strategies import discover_strategies
        result = discover_strategies()
        assert isinstance(result, dict)

    def test_discovered_strategies_are_instantiable(self) -> None:
        from strategies import discover_strategies
        discovered = discover_strategies()
        for sid, cls in discovered.items():
            instance = cls()
            assert hasattr(instance, "strategy_id")
            assert hasattr(instance, "evaluate")


# ---------------------------------------------------------------------------
# Issue 7: maxGasWei configurable via MAX_GAS_WEI env var
# ---------------------------------------------------------------------------


class TestMaxGasWeiConfigurable:
    """Verify maxGasWei reads from MAX_GAS_WEI env var."""

    def test_drawdown_uses_env_var(self) -> None:
        with patch.dict(os.environ, {"MAX_GAS_WEI": "999"}):
            breaker = DrawdownBreaker(initial_value=Decimal("10000"))
            breaker.update(Decimal("7000"))
            orders = breaker.get_unwind_orders(
                [{"asset": "ETH"}], correlation_id="c1",
            )
            assert orders[0]["limits"]["maxGasWei"] == "999"

    def test_drawdown_uses_default(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MAX_GAS_WEI", None)
            breaker = DrawdownBreaker(initial_value=Decimal("10000"))
            breaker.update(Decimal("7000"))
            orders = breaker.get_unwind_orders(
                [{"asset": "ETH"}], correlation_id="c1",
            )
            assert orders[0]["limits"]["maxGasWei"] == "500000000000000"

    def test_position_loss_uses_env_var(self) -> None:
        with patch.dict(os.environ, {"MAX_GAS_WEI": "888"}):
            limiter = PositionLossLimit()
            positions = [{
                "id": "p1",
                "asset": "ETH",
                "entry_price": "2000",
                "protocol": "aave_v3",
                "strategy_id": "test",
                "entry_time": "2026-01-01T00:00:00+00:00",
                "current_value": "1700",
            }]
            orders = limiter.generate_close_orders(
                positions=positions,
                price_map={"ETH": Decimal("1700")},
                correlation_id="c1",
            )
            assert len(orders) == 1
            assert orders[0]["limits"]["maxGasWei"] == "888"

    def test_tvl_monitor_uses_env_var(self) -> None:
        with patch.dict(os.environ, {"MAX_GAS_WEI": "777"}):
            monitor = TVLMonitor()
            monitor.record_tvl("aave", "base", Decimal("1000000"), "test")
            monitor.record_tvl("aave", "base", Decimal("500000"), "test")
            positions = [{"protocol": "aave", "asset": "ETH", "current_value": "100"}]
            orders = monitor.generate_withdrawal_orders(
                positions=positions,
                correlation_id="c1",
            )
            assert len(orders) == 1
            assert orders[0]["limits"]["maxGasWei"] == "777"

    def test_main_decision_to_orders_uses_env_var(self) -> None:
        """Verify _decision_to_orders reads MAX_GAS_WEI."""
        with patch.dict(os.environ, {"MAX_GAS_WEI": "12345"}):
            loop = object.__new__(DecisionLoop)
            decision = Decision(
                action=DecisionAction.ADJUST,
                strategy="test",
                reasoning="test",
                confidence=0.9,
                params={"chain": "base", "protocol": "aave", "action": "supply"},
            )
            orders = loop._decision_to_orders(decision, "corr-1")
            assert len(orders) == 1
            assert orders[0]["limits"]["maxGasWei"] == "12345"
