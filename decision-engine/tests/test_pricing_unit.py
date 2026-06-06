"""Unit tests for the pricing/cost slice (managed-portfolio P1.2)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from decision_engine.pricing import estimate_swap_cost_usd, price_usd
from icarus.types import MarketSnapshot


def _market(prices: dict[str, Decimal], gas_gwei: Decimal = Decimal("0.05")) -> MarketSnapshot:
    return MarketSnapshot(
        timestamp=datetime(2026, 6, 3, tzinfo=UTC),
        chain="base",
        prices=prices,
        apys={},
        pool_state={},
        gas_gwei=gas_gwei,
        metadata={},
    )


def test_price_usd_stablecoin_is_one() -> None:
    market = _market({"ETH": Decimal("3000")})
    assert price_usd("USDC", market) == Decimal("1")


def test_price_usd_usdc_from_snapshot_when_present() -> None:
    # A live USDC/USD feed put a real price in the snapshot — use it, not $1.
    market = _market({"ETH": Decimal("3000"), "USDC": Decimal("0.9996")})
    assert price_usd("USDC", market) == Decimal("0.9996")


def test_price_usd_usdc_falls_back_to_one_when_absent() -> None:
    # No USDC feed configured → snapshot omits USDC → pin to $1 (today's behaviour).
    market = _market({"ETH": Decimal("3000")})
    assert price_usd("USDC", market) == Decimal("1")


def test_price_usd_eth_from_snapshot() -> None:
    market = _market({"ETH": Decimal("3000")})
    assert price_usd("ETH", market) == Decimal("3000")


def test_price_usd_weth_aliases_to_eth() -> None:
    market = _market({"ETH": Decimal("3000")})
    assert price_usd("WETH", market) == Decimal("3000")


def test_price_usd_unpriced_symbol_raises() -> None:
    market = _market({"ETH": Decimal("3000")})
    with pytest.raises(KeyError, match="SOL"):
        price_usd("SOL", market)

def test_estimate_swap_cost_combines_gas_and_slippage() -> None:
    # gas_gwei=1, gas_units=200_000 → 0.0002 ETH; at $3000 → $0.60 gas.
    # slippage: $10_000 trade at 50 bps → $50.00. total = $50.60.
    market = _market({"ETH": Decimal("3000")}, gas_gwei=Decimal("1"))
    cost = estimate_swap_cost_usd(
        trade_usd=Decimal("10000"),
        slippage_bps=50,
        market=market,
        eth_price_usd=Decimal("3000"),
        gas_units=200_000,
    )
    assert cost == Decimal("50.60")


def test_estimate_swap_cost_zero_gas() -> None:
    market = _market({"ETH": Decimal("3000")}, gas_gwei=Decimal("0"))
    cost = estimate_swap_cost_usd(
        trade_usd=Decimal("10000"),
        slippage_bps=50,
        market=market,
        eth_price_usd=Decimal("3000"),
        gas_units=200_000,
    )
    assert cost == Decimal("50.00")  # slippage only
