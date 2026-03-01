"""Tests for portfolio rebalancer — PORT-003."""

from __future__ import annotations

import time
from decimal import Decimal
from unittest.mock import patch

from portfolio.rebalancer import (
    PortfolioRebalancer,
    RebalanceAction,
    RebalanceConfig,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _default_config() -> RebalanceConfig:
    return RebalanceConfig()


def _make_action(
    token: str = "ETH",
    amount_usd: Decimal = Decimal("500"),
    action: str = "decrease",
    current_pct: Decimal = Decimal("0.40"),
    target_pct: Decimal = Decimal("0.30"),
    protocol: str = "aave_v3",
    chain: str = "ethereum",
) -> RebalanceAction:
    return RebalanceAction(
        protocol=protocol,
        chain=chain,
        action=action,
        token=token,
        amount_usd=amount_usd,
        current_pct=current_pct,
        target_pct=target_pct,
    )


# ---------------------------------------------------------------------------
# Drift detection — should_rebalance
# ---------------------------------------------------------------------------

class TestShouldRebalance:

    def test_no_drift_returns_false(self) -> None:
        rb = PortfolioRebalancer(_default_config())
        current = {"ETH": Decimal("0.50"), "USDC": Decimal("0.50")}
        target = {"ETH": Decimal("0.50"), "USDC": Decimal("0.50")}
        assert not rb.should_rebalance(current, target)

    def test_small_drift_below_threshold_returns_false(self) -> None:
        rb = PortfolioRebalancer(_default_config())
        current = {"ETH": Decimal("0.52"), "USDC": Decimal("0.48")}
        target = {"ETH": Decimal("0.50"), "USDC": Decimal("0.50")}
        assert not rb.should_rebalance(current, target)

    def test_drift_at_threshold_returns_false(self) -> None:
        rb = PortfolioRebalancer(_default_config())
        current = {"ETH": Decimal("0.55")}
        target = {"ETH": Decimal("0.50")}
        assert not rb.should_rebalance(current, target)

    def test_drift_above_threshold_returns_true(self) -> None:
        rb = PortfolioRebalancer(_default_config())
        current = {"ETH": Decimal("0.60")}
        target = {"ETH": Decimal("0.50")}
        assert rb.should_rebalance(current, target)

    def test_empty_allocations_returns_false(self) -> None:
        rb = PortfolioRebalancer(_default_config())
        assert not rb.should_rebalance({}, {})

    def test_missing_key_in_current_counts_as_zero(self) -> None:
        """A target with no matching current allocation drifts from 0."""
        rb = PortfolioRebalancer(_default_config())
        current: dict[str, Decimal] = {}
        target = {"ETH": Decimal("0.10")}
        assert rb.should_rebalance(current, target)

    def test_missing_key_in_target_counts_as_zero(self) -> None:
        rb = PortfolioRebalancer(_default_config())
        current = {"ETH": Decimal("0.10")}
        target: dict[str, Decimal] = {}
        assert rb.should_rebalance(current, target)


# ---------------------------------------------------------------------------
# Drift detection — check_drift
# ---------------------------------------------------------------------------

class TestCheckDrift:

    def test_no_drift_returns_empty(self) -> None:
        rb = PortfolioRebalancer(_default_config())
        current = {"ETH": Decimal("0.50")}
        target = {"ETH": Decimal("0.50")}
        actions = rb.check_drift(current, target, total_value_usd=Decimal("10000"))
        assert actions == []

    def test_drift_produces_decrease_action(self) -> None:
        rb = PortfolioRebalancer(_default_config())
        current = {"ETH": Decimal("0.60")}
        target = {"ETH": Decimal("0.50")}
        actions = rb.check_drift(
            current, target,
            total_value_usd=Decimal("10000"),
            protocol_map={"ETH": "aave_v3"},
        )
        assert len(actions) == 1
        assert actions[0].action == "decrease"
        assert actions[0].token == "ETH"
        assert actions[0].amount_usd == Decimal("1000")

    def test_drift_produces_increase_action(self) -> None:
        rb = PortfolioRebalancer(_default_config())
        current = {"ETH": Decimal("0.20")}
        target = {"ETH": Decimal("0.35")}
        actions = rb.check_drift(
            current, target,
            total_value_usd=Decimal("10000"),
        )
        assert len(actions) == 1
        assert actions[0].action == "increase"
        assert actions[0].amount_usd == Decimal("1500")

    def test_dust_trades_filtered_out(self) -> None:
        config = RebalanceConfig(min_trade_usd=Decimal("100"))
        rb = PortfolioRebalancer(config)
        # Drift of 6% on a $1000 portfolio = $60 < $100 min
        current = {"ETH": Decimal("0.56")}
        target = {"ETH": Decimal("0.50")}
        actions = rb.check_drift(
            current, target,
            total_value_usd=Decimal("1000"),
        )
        assert actions == []

    def test_multiple_drifts_sorted_by_size(self) -> None:
        rb = PortfolioRebalancer(_default_config())
        current = {"ETH": Decimal("0.40"), "WBTC": Decimal("0.30")}
        target = {"ETH": Decimal("0.25"), "WBTC": Decimal("0.10")}
        actions = rb.check_drift(
            current, target,
            total_value_usd=Decimal("10000"),
        )
        assert len(actions) == 2
        # WBTC drift=20% ($2000) > ETH drift=15% ($1500)
        assert actions[0].token == "WBTC"
        assert actions[1].token == "ETH"

    def test_zero_total_value_produces_no_actions(self) -> None:
        rb = PortfolioRebalancer(_default_config())
        current = {"ETH": Decimal("0.60")}
        target = {"ETH": Decimal("0.40")}
        actions = rb.check_drift(
            current, target,
            total_value_usd=Decimal("0"),
        )
        assert actions == []


# ---------------------------------------------------------------------------
# Gas efficiency
# ---------------------------------------------------------------------------

class TestGasEfficiency:

    def test_gas_efficient_action(self) -> None:
        rb = PortfolioRebalancer(_default_config())
        action = _make_action(amount_usd=Decimal("1000"))
        # gas = $10 → 1% < 2% threshold
        assert rb.is_gas_efficient(action, Decimal("10"))

    def test_gas_inefficient_action(self) -> None:
        rb = PortfolioRebalancer(_default_config())
        action = _make_action(amount_usd=Decimal("100"))
        # gas = $5 → 5% > 2% threshold
        assert not rb.is_gas_efficient(action, Decimal("5"))

    def test_gas_at_threshold_is_efficient(self) -> None:
        rb = PortfolioRebalancer(_default_config())
        action = _make_action(amount_usd=Decimal("1000"))
        # gas = $20 → exactly 2% = threshold
        assert rb.is_gas_efficient(action, Decimal("20"))

    def test_zero_amount_is_not_efficient(self) -> None:
        rb = PortfolioRebalancer(_default_config())
        action = _make_action(amount_usd=Decimal("0"))
        assert not rb.is_gas_efficient(action, Decimal("1"))

    def test_filter_keeps_efficient_only(self) -> None:
        rb = PortfolioRebalancer(_default_config())
        actions = [
            _make_action(token="ETH", amount_usd=Decimal("1000")),
            _make_action(token="WBTC", amount_usd=Decimal("50")),
        ]
        # gas = $5 → ETH: 0.5% ok, WBTC: 10% too high
        result = rb.filter_gas_efficient(actions, Decimal("5"))
        assert len(result) == 1
        assert result[0].token == "ETH"


# ---------------------------------------------------------------------------
# Cooldown
# ---------------------------------------------------------------------------

class TestCooldown:

    def test_can_rebalance_initially(self) -> None:
        rb = PortfolioRebalancer(_default_config())
        assert rb.can_rebalance()

    def test_cannot_rebalance_during_cooldown(self) -> None:
        rb = PortfolioRebalancer(_default_config())
        rb.record_rebalance()
        assert not rb.can_rebalance()

    def test_can_rebalance_after_cooldown(self) -> None:
        config = RebalanceConfig(cooldown_seconds=1)
        rb = PortfolioRebalancer(config)
        rb.record_rebalance()
        # Simulate elapsed time by patching monotonic
        with patch.object(time, "monotonic", return_value=time.monotonic() + 2):
            assert rb.can_rebalance()


# ---------------------------------------------------------------------------
# Order generation
# ---------------------------------------------------------------------------

class TestGenerateOrders:

    def test_empty_actions_returns_empty(self) -> None:
        rb = PortfolioRebalancer(_default_config())
        orders = rb.generate_orders([], "corr-001")
        assert orders == []

    def test_single_action_produces_one_order(self) -> None:
        rb = PortfolioRebalancer(_default_config())
        actions = [_make_action(token="ETH", amount_usd=Decimal("1000"))]
        orders = rb.generate_orders(actions, "corr-002")
        assert len(orders) == 1
        order = orders[0]
        assert order["version"] == "1.0.0"
        assert order["correlationId"] == "corr-002"
        assert order["chain"] == "ethereum"
        assert order["protocol"] == "aave_v3"
        assert order["action"] == "withdraw"
        assert order["strategy"] == "rebalancer"
        assert order["params"]["tokenIn"] == "ETH"
        assert order["params"]["amount"] == "1000"

    def test_increase_action_maps_to_supply(self) -> None:
        rb = PortfolioRebalancer(_default_config())
        actions = [_make_action(action="increase")]
        orders = rb.generate_orders(actions, "corr-003")
        assert orders[0]["action"] == "supply"

    def test_one_adjustment_per_cycle(self) -> None:
        """Even with multiple actions, only the first is converted."""
        rb = PortfolioRebalancer(_default_config())
        actions = [
            _make_action(token="ETH", amount_usd=Decimal("2000")),
            _make_action(token="WBTC", amount_usd=Decimal("1000")),
        ]
        orders = rb.generate_orders(actions, "corr-004")
        assert len(orders) == 1
        assert orders[0]["params"]["tokenIn"] == "ETH"


# ---------------------------------------------------------------------------
# Config defaults
# ---------------------------------------------------------------------------

class TestRebalanceConfig:

    def test_default_values(self) -> None:
        cfg = RebalanceConfig()
        assert cfg.drift_threshold_pct == Decimal("0.05")
        assert cfg.min_trade_usd == Decimal("50")
        assert cfg.max_gas_cost_pct == Decimal("0.02")
        assert cfg.cooldown_seconds == 3600

    def test_custom_values(self) -> None:
        cfg = RebalanceConfig(
            drift_threshold_pct=Decimal("0.10"),
            min_trade_usd=Decimal("200"),
            max_gas_cost_pct=Decimal("0.05"),
            cooldown_seconds=7200,
        )
        assert cfg.drift_threshold_pct == Decimal("0.10")
        assert cfg.min_trade_usd == Decimal("200")
        assert cfg.max_gas_cost_pct == Decimal("0.05")
        assert cfg.cooldown_seconds == 7200


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:

    def test_single_position_no_drift(self) -> None:
        rb = PortfolioRebalancer(_default_config())
        current = {"ETH": Decimal("1.0")}
        target = {"ETH": Decimal("1.0")}
        assert not rb.should_rebalance(current, target)
        assert rb.check_drift(current, target, total_value_usd=Decimal("10000")) == []

    def test_single_position_with_drift(self) -> None:
        rb = PortfolioRebalancer(_default_config())
        current = {"ETH": Decimal("1.0")}
        target = {"ETH": Decimal("0.80")}
        actions = rb.check_drift(
            current, target,
            total_value_usd=Decimal("10000"),
            protocol_map={"ETH": "lido"},
            chain_map={"ETH": "ethereum"},
        )
        assert len(actions) == 1
        assert actions[0].action == "decrease"
        assert actions[0].amount_usd == Decimal("2000")
        assert actions[0].protocol == "lido"

    def test_default_config_when_none(self) -> None:
        rb = PortfolioRebalancer()
        assert rb.config.drift_threshold_pct == Decimal("0.05")
