"""Unit tests for RpcHoldingsProvider (managed-portfolio P2.2). Fully mocked.

P2.2 generalizes the provider from a fixed crypto/stable pair to N symbols,
returning a per-asset USD dict (`current_usd_by_asset`).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from decision_engine.holdings import RpcHoldingsProvider
from decision_engine.managed_cycle import HoldingsProvider
from decision_engine.order_resolver import register_token
from icarus.types import MarketSnapshot
from icarus.types.market import Chain

_SAFE = "0x1111111111111111111111111111111111111111"
_USDC = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
_WETH = "0x4200000000000000000000000000000000000006"
_WBTC = "0x2222222222222222222222222222222222222222"  # synthetic, registered in tests

# Register a synthetic WBTC (8 decimals) so the N-asset reader has a third leg
# without committing to a real cbBTC mainnet address (that lands with P2.3).
register_token(chain_id=8453, symbol="WBTC", address=_WBTC, decimals=8)


class _FakeAdapter:
    name = "fake"
    historical_supported = False

    def __init__(self, prices: dict[str, Decimal]) -> None:
        self._prices = prices

    async def fetch_live(self, chain: Chain) -> MarketSnapshot:
        return MarketSnapshot(
            timestamp=datetime(2026, 6, 3, tzinfo=UTC), chain=chain,
            prices=dict(self._prices), apys={}, pool_state={},
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


def _provider(
    w3: MagicMock,
    prices: dict[str, Decimal],
    symbols: tuple[str, ...] = ("WETH", "USDC", "WBTC"),
    chain_id: int = 8453,
) -> RpcHoldingsProvider:
    return RpcHoldingsProvider(
        w3=w3, adapter=_FakeAdapter(prices), safe_address=_SAFE,
        symbols=symbols, chain="base", chain_id=chain_id,
    )


def test_satisfies_holdings_protocol() -> None:
    assert isinstance(
        _provider(_make_w3({_WETH: 0, _USDC: 0, _WBTC: 0}), {"ETH": Decimal("3000")}),
        HoldingsProvider,
    )


@pytest.mark.asyncio
async def test_reads_and_prices_all_legs() -> None:
    # WETH 2e18 @ $3000 = $6000; USDC 4000e6 @ $1 = $4000;
    # WBTC 0.05e8 @ $60000 = $3000.
    w3 = _make_w3({_WETH: 2_000000000000000000, _USDC: 4000_000000, _WBTC: 5_000000})
    holdings = await _provider(
        w3, {"ETH": Decimal("3000"), "WBTC": Decimal("60000")}
    ).current_usd_by_asset()
    assert holdings == {
        "WETH": Decimal("6000"),
        "USDC": Decimal("4000"),
        "WBTC": Decimal("3000"),
    }


@pytest.mark.asyncio
async def test_zero_balances() -> None:
    w3 = _make_w3({_WETH: 0, _USDC: 0, _WBTC: 0})
    holdings = await _provider(
        w3, {"ETH": Decimal("3000"), "WBTC": Decimal("60000")}
    ).current_usd_by_asset()
    assert holdings == {"WETH": Decimal("0"), "USDC": Decimal("0"), "WBTC": Decimal("0")}


@pytest.mark.asyncio
async def test_two_asset_subset_still_works() -> None:
    # The reader is generic over `symbols` — a 2-asset target reads exactly two.
    w3 = _make_w3({_WETH: 2_000000000000000000, _USDC: 6000_000000})
    holdings = await _provider(
        w3, {"ETH": Decimal("3000")}, symbols=("WETH", "USDC")
    ).current_usd_by_asset()
    assert holdings == {"WETH": Decimal("6000"), "USDC": Decimal("6000")}


# ── chain_id threading ─────────────────────────────────────────────────────

_USDC_SEPOLIA = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"
_BASE_SEPOLIA = 84532


@pytest.mark.asyncio
async def test_holdings_uses_sepolia_usdc_address_when_chain_id_84532() -> None:
    """RpcHoldingsProvider(chain_id=84532) looks up Sepolia USDC, not mainnet USDC."""
    w3 = _make_w3({_WETH: 2_000000000000000000, _USDC_SEPOLIA: 6000_000000})
    holdings = await _provider(
        w3, {"ETH": Decimal("3000")}, symbols=("WETH", "USDC"), chain_id=_BASE_SEPOLIA
    ).current_usd_by_asset()
    assert holdings == {"WETH": Decimal("6000"), "USDC": Decimal("6000")}
