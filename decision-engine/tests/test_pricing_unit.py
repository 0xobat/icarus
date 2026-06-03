"""Unit tests for the pricing/cost slice (managed-portfolio P1.2)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from icarus.types import MarketSnapshot

from decision_engine.pricing import price_usd


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
