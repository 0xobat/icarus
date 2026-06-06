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


def test_price_usd_cbbtc_aliases_to_btc() -> None:
    # cbBTC pegs ~1:1 to BTC → priced off the BTC/USD snapshot key (P2.3).
    market = _market({"ETH": Decimal("3000"), "BTC": Decimal("60000")})
    assert price_usd("cbBTC", market) == Decimal("60000")


def test_price_usd_wsteth_via_exchange_rate() -> None:
    # wstETH (P2.4) is priced by its ETH exchange rate * ETH/USD, not 1:1.
    # rate 1.18, ETH $3000 → wstETH $3540.
    market = _market({"ETH": Decimal("3000"), "wstETH/ETH": Decimal("1.18")})
    assert price_usd("wstETH", market) == Decimal("3540.00")


def test_price_usd_wsteth_missing_rate_raises() -> None:
    market = _market({"ETH": Decimal("3000")})  # no wstETH/ETH rate
    with pytest.raises(KeyError, match="wstETH"):
        price_usd("wstETH", market)


def test_price_usd_sol_from_snapshot() -> None:
    market = _market({"ETH": Decimal("3000"), "SOL": Decimal("150")})
    assert price_usd("SOL", market) == Decimal("150")


def test_price_usd_jitosol_via_exchange_rate() -> None:
    # jitoSOL (P2.6) priced like wstETH: jitoSOL/SOL rate * SOL/USD.
    # rate 1.12, SOL $150 → jitoSOL $168.
    market = _market({"SOL": Decimal("150"), "jitoSOL/SOL": Decimal("1.12")})
    assert price_usd("jitoSOL", market) == Decimal("168.00")


def test_price_usd_jitosol_missing_rate_raises() -> None:
    market = _market({"SOL": Decimal("150")})  # no jitoSOL/SOL rate
    with pytest.raises(KeyError, match="jitoSOL"):
        price_usd("jitoSOL", market)

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
