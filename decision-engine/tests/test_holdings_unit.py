"""Unit tests for RpcHoldingsProvider (managed-portfolio P1.5a). Fully mocked."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from icarus.types import MarketSnapshot
from icarus.types.market import Chain

from decision_engine.holdings import RpcHoldingsProvider
from decision_engine.managed_cycle import HoldingsProvider

_SAFE = "0x1111111111111111111111111111111111111111"
_USDC = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
_WETH = "0x4200000000000000000000000000000000000006"


class _FakeAdapter:
    name = "fake"
    historical_supported = False

    def __init__(self, eth_usd: Decimal) -> None:
        self._eth = eth_usd

    async def fetch_live(self, chain: Chain) -> MarketSnapshot:
        return MarketSnapshot(
            timestamp=datetime(2026, 6, 3, tzinfo=UTC), chain=chain,
            prices={"ETH": self._eth}, apys={}, pool_state={},
            gas_gwei=Decimal("0.05"), metadata={},
        )


def _make_w3(balances_by_address: dict[str, int]) -> MagicMock:
    """Mock AsyncWeb3 whose `eth.contract(address=...)` yields a contract
    returning the pre-set raw balanceOf for that address."""
    w3 = MagicMock(name="AsyncWeb3")
    eth = MagicMock(name="eth")

    def _contract(*, address: str, abi: Any) -> MagicMock:
        c = MagicMock(name=f"ERC20:{address}")
        c.functions.balanceOf.return_value.call = AsyncMock(
            return_value=balances_by_address[address]
        )
        return c

    eth.contract = MagicMock(side_effect=_contract)
    w3.eth = eth
    return w3


def _provider(w3: MagicMock, eth_usd: Decimal = Decimal("3000")) -> RpcHoldingsProvider:
    return RpcHoldingsProvider(
        w3=w3, adapter=_FakeAdapter(eth_usd), safe_address=_SAFE,
        crypto_symbol="WETH", stable_symbol="USDC", chain="base",
    )


def test_satisfies_holdings_protocol() -> None:
    assert isinstance(_provider(_make_w3({_WETH: 0, _USDC: 0})), HoldingsProvider)


@pytest.mark.asyncio
async def test_reads_and_prices_both_legs() -> None:
    # 2 WETH (2e18) @ $3000 = $6000 crypto; 6000 USDC (6000e6) @ $1 = $6000 stable.
    w3 = _make_w3({_WETH: 2_000000000000000000, _USDC: 6000_000000})
    crypto_usd, stable_usd = await _provider(w3).current_usd_holdings()
    assert crypto_usd == Decimal("6000")
    assert stable_usd == Decimal("6000")


@pytest.mark.asyncio
async def test_fractional_weth_balance() -> None:
    # 0.5 WETH (5e17) @ $4000 = $2000; 1000 USDC = $1000.
    w3 = _make_w3({_WETH: 500000000000000000, _USDC: 1000_000000})
    crypto_usd, stable_usd = await _provider(w3, eth_usd=Decimal("4000")).current_usd_holdings()
    assert crypto_usd == Decimal("2000.0")
    assert stable_usd == Decimal("1000")


@pytest.mark.asyncio
async def test_zero_balances() -> None:
    w3 = _make_w3({_WETH: 0, _USDC: 0})
    crypto_usd, stable_usd = await _provider(w3).current_usd_holdings()
    assert crypto_usd == Decimal("0")
    assert stable_usd == Decimal("0")
