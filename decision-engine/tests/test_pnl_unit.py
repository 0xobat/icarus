"""Unit tests for the PnL deposit-tracker (managed-portfolio PnL v1).

Two layers, both fully mocked (no network):
  - pure netting/pricing: `net_contributed_usd` (USDC@$1, ETH/WETH@block price,
    deposits add / withdrawals subtract, mixed, empty).
  - `ContributedCapitalTracker`: classification by funding address (swap
    proceeds ignored), in/out direction, and graceful API-error degradation
    (no raise, cache unchanged).

The tracker is a REPORTING feature: it must never raise to the engine. These
tests pin that contract.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from decision_engine.pnl import (
    ContributedCapitalTracker,
    Transfer,
    net_contributed_usd,
)

# Canonical (checksummed) test addresses.
_SAFE = "0x1111111111111111111111111111111111111111"
_OPERATOR = "0x2222222222222222222222222222222222222222"
_STRANGER = "0x3333333333333333333333333333333333333333"  # a DEX router, faucet, etc.
_USDC = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
_WETH = "0x4200000000000000000000000000000000000006"
# An ERC-20 we don't price (fake "ARB"-like token) — funded but unconfigured.
_OTHER_ERC20 = "0x912CE59144191C1204E64559FE8253a0e49E6548"


# A fixed ETH price oracle for deterministic netting tests.
async def _eth_price_at_block(_block_no: int) -> Decimal:
    return Decimal("3000")


# ---------------------------------------------------------------------------
# Task 1: pure netting + pricing
# ---------------------------------------------------------------------------


async def test_net_empty_is_zero() -> None:
    assert await net_contributed_usd([], eth_price_at_block=_eth_price_at_block) == Decimal(0)


async def test_usdc_deposit_priced_at_one_dollar() -> None:
    transfers = [
        Transfer(asset="USDC", amount=Decimal("500"), block_no=10, direction="in",
                 counterparty=_OPERATOR),
    ]
    got = await net_contributed_usd(transfers, eth_price_at_block=_eth_price_at_block)
    assert got == Decimal("500")


async def test_eth_deposit_priced_at_block_price() -> None:
    transfers = [
        Transfer(asset="ETH", amount=Decimal("2"), block_no=10, direction="in",
                 counterparty=_OPERATOR),
    ]
    got = await net_contributed_usd(transfers, eth_price_at_block=_eth_price_at_block)
    assert got == Decimal("6000")  # 2 ETH * $3000


async def test_weth_deposit_priced_like_eth() -> None:
    transfers = [
        Transfer(asset="WETH", amount=Decimal("1"), block_no=10, direction="in",
                 counterparty=_OPERATOR),
    ]
    got = await net_contributed_usd(transfers, eth_price_at_block=_eth_price_at_block)
    assert got == Decimal("3000")


async def test_withdrawal_subtracts() -> None:
    transfers = [
        Transfer(asset="USDC", amount=Decimal("1000"), block_no=10, direction="in",
                 counterparty=_OPERATOR),
        Transfer(asset="USDC", amount=Decimal("250"), block_no=20, direction="out",
                 counterparty=_OPERATOR),
    ]
    got = await net_contributed_usd(transfers, eth_price_at_block=_eth_price_at_block)
    assert got == Decimal("750")


async def test_mixed_deposits_and_withdrawals_net() -> None:
    transfers = [
        Transfer(asset="ETH", amount=Decimal("1"), block_no=10, direction="in",
                 counterparty=_OPERATOR),  # +3000
        Transfer(asset="USDC", amount=Decimal("2000"), block_no=11, direction="in",
                 counterparty=_OPERATOR),  # +2000
        Transfer(asset="WETH", amount=Decimal("0.5"), block_no=12, direction="out",
                 counterparty=_OPERATOR),  # -1500
    ]
    got = await net_contributed_usd(transfers, eth_price_at_block=_eth_price_at_block)
    assert got == Decimal("3500")


async def test_eth_price_uses_per_transfer_block() -> None:
    """Each ETH/WETH transfer is priced at *its own* block."""
    async def _price(block_no: int) -> Decimal:
        return Decimal("2000") if block_no == 10 else Decimal("4000")

    transfers = [
        Transfer(asset="ETH", amount=Decimal("1"), block_no=10, direction="in",
                 counterparty=_OPERATOR),  # +2000
        Transfer(asset="ETH", amount=Decimal("1"), block_no=99, direction="in",
                 counterparty=_OPERATOR),  # +4000
    ]
    got = await net_contributed_usd(transfers, eth_price_at_block=_price)
    assert got == Decimal("6000")


# ---------------------------------------------------------------------------
# Task 2: classification + fetch (mocked make_request)
# ---------------------------------------------------------------------------


# Map test asset symbols to their ERC-20 contract address (None for native ETH),
# mirroring Alchemy's `rawContract.address` shape.
_ASSET_ADDR = {"USDC": _USDC, "WETH": _WETH, "ETH": None, "ARB": _OTHER_ERC20}


def _transfer_row(
    *, asset: str, raw_value_hex: str, decimals: int, block_hex: str,
    from_addr: str, to_addr: str,
) -> dict[str, Any]:
    """One row in the alchemy_getAssetTransfers `transfers` list.

    ERC-20 rows carry `rawContract.address` (lowercased, as Alchemy returns it);
    native ETH rows carry a null address and asset == "ETH".
    """
    addr = _ASSET_ADDR[asset]
    return {
        "asset": asset,
        "from": from_addr.lower(),
        "to": to_addr.lower(),
        "blockNum": block_hex,
        "rawContract": {
            "value": raw_value_hex,
            "decimals": hex(decimals),
            "address": addr.lower() if addr else None,
        },
    }


def _make_w3(*, inbound: list[dict], outbound: list[dict]) -> MagicMock:
    """Mock AsyncWeb3 whose provider.make_request dispatches by direction.

    alchemy_getAssetTransfers is called twice: once with toAddress=Safe
    (inbound) and once with fromAddress=Safe (outbound). We key on which is
    present in the params.
    """
    w3 = MagicMock(name="AsyncWeb3")

    async def _make_request(method: str, params: list) -> dict:
        assert method == "alchemy_getAssetTransfers"
        p = params[0]
        if p.get("toAddress") == _SAFE:
            return {"result": {"transfers": inbound}}
        return {"result": {"transfers": outbound}}

    w3.provider.make_request = AsyncMock(side_effect=_make_request)
    return w3


def _tracker(w3: MagicMock) -> ContributedCapitalTracker:
    return ContributedCapitalTracker(
        w3=w3,
        safe_address=_SAFE,
        funding_addresses=frozenset({_OPERATOR}),
        eth_price_at_block=_eth_price_at_block,
        usdc_address=_USDC,
        weth_address=_WETH,
    )


async def test_contributed_none_before_refresh() -> None:
    tracker = _tracker(_make_w3(inbound=[], outbound=[]))
    assert tracker.contributed_usd is None


async def test_funding_deposit_counted() -> None:
    inbound = [
        # 500 USDC (6 decimals) from operator at block 0x10.
        _transfer_row(asset="USDC", raw_value_hex=hex(500_000000), decimals=6,
                      block_hex="0x10", from_addr=_OPERATOR, to_addr=_SAFE),
    ]
    tracker = _tracker(_make_w3(inbound=inbound, outbound=[]))
    await tracker.refresh()
    assert tracker.contributed_usd == Decimal("500")


async def test_swap_proceeds_from_stranger_ignored() -> None:
    """Inbound from a non-funding address (e.g. DEX router) is NOT a deposit."""
    inbound = [
        _transfer_row(asset="WETH", raw_value_hex=hex(10 * 10**18), decimals=18,
                      block_hex="0x10", from_addr=_STRANGER, to_addr=_SAFE),
    ]
    tracker = _tracker(_make_w3(inbound=inbound, outbound=[]))
    await tracker.refresh()
    assert tracker.contributed_usd == Decimal("0")


async def test_eth_deposit_priced_at_block() -> None:
    # 2 ETH (native, 18 decimals) from operator.
    inbound = [
        _transfer_row(asset="ETH", raw_value_hex=hex(2 * 10**18), decimals=18,
                      block_hex="0x10", from_addr=_OPERATOR, to_addr=_SAFE),
    ]
    tracker = _tracker(_make_w3(inbound=inbound, outbound=[]))
    await tracker.refresh()
    assert tracker.contributed_usd == Decimal("6000")  # 2 * 3000


async def test_withdrawal_to_operator_subtracts() -> None:
    inbound = [
        _transfer_row(asset="USDC", raw_value_hex=hex(1000_000000), decimals=6,
                      block_hex="0x10", from_addr=_OPERATOR, to_addr=_SAFE),
    ]
    outbound = [
        # 300 USDC sent back to the operator → a withdrawal.
        _transfer_row(asset="USDC", raw_value_hex=hex(300_000000), decimals=6,
                      block_hex="0x20", from_addr=_SAFE, to_addr=_OPERATOR),
    ]
    tracker = _tracker(_make_w3(inbound=inbound, outbound=outbound))
    await tracker.refresh()
    assert tracker.contributed_usd == Decimal("700")


async def test_outbound_to_stranger_ignored() -> None:
    """Outbound to a non-funding address (a swap into a DEX) is not a withdrawal."""
    inbound = [
        _transfer_row(asset="USDC", raw_value_hex=hex(1000_000000), decimals=6,
                      block_hex="0x10", from_addr=_OPERATOR, to_addr=_SAFE),
    ]
    outbound = [
        _transfer_row(asset="USDC", raw_value_hex=hex(400_000000), decimals=6,
                      block_hex="0x20", from_addr=_SAFE, to_addr=_STRANGER),
    ]
    tracker = _tracker(_make_w3(inbound=inbound, outbound=outbound))
    await tracker.refresh()
    assert tracker.contributed_usd == Decimal("1000")  # withdrawal ignored


async def test_api_error_leaves_cache_unchanged_and_never_raises() -> None:
    """A failing make_request must not raise and must leave the cache as-is."""
    # First a good refresh to seed the cache.
    inbound = [
        _transfer_row(asset="USDC", raw_value_hex=hex(500_000000), decimals=6,
                      block_hex="0x10", from_addr=_OPERATOR, to_addr=_SAFE),
    ]
    w3 = _make_w3(inbound=inbound, outbound=[])
    tracker = _tracker(w3)
    await tracker.refresh()
    assert tracker.contributed_usd == Decimal("500")

    # Now make the API blow up; refresh must swallow it and keep the old value.
    w3.provider.make_request = AsyncMock(side_effect=RuntimeError("alchemy 500"))
    await tracker.refresh()  # must NOT raise
    assert tracker.contributed_usd == Decimal("500")  # unchanged


async def test_unsupported_method_degrades_gracefully() -> None:
    """An error-shaped JSON-RPC reply (method unsupported) → no raise, cache None."""
    w3 = MagicMock(name="AsyncWeb3")
    w3.provider.make_request = AsyncMock(
        return_value={"error": {"code": -32601, "message": "method not found"}}
    )
    tracker = _tracker(w3)
    await tracker.refresh()  # must NOT raise
    assert tracker.contributed_usd is None


# ---------------------------------------------------------------------------
# FIX 5: negative contributed (withdrawals > deposits)
# ---------------------------------------------------------------------------


async def test_net_negative_when_withdrawals_exceed_deposits() -> None:
    """Withdrawals larger than deposits net to a negative Decimal (sign correct)."""
    transfers = [
        Transfer(asset="USDC", amount=Decimal("100"), block_no=10, direction="in",
                 counterparty=_OPERATOR),  # +100
        Transfer(asset="USDC", amount=Decimal("400"), block_no=20, direction="out",
                 counterparty=_OPERATOR),  # -400
    ]
    got = await net_contributed_usd(transfers, eth_price_at_block=_eth_price_at_block)
    assert got == Decimal("-300")


# ---------------------------------------------------------------------------
# FIX 6: unconfigured-asset exclusion (funded but not USDC/WETH/ETH)
# ---------------------------------------------------------------------------


async def test_unconfigured_asset_from_funding_addr_excluded() -> None:
    """A deposit of some other ERC-20 (not USDC/WETH) from the operator is
    unpriceable → excluded; contributed stays 0."""
    inbound = [
        _transfer_row(asset="ARB", raw_value_hex=hex(100 * 10**18), decimals=18,
                      block_hex="0x10", from_addr=_OPERATOR, to_addr=_SAFE),
    ]
    tracker = _tracker(_make_w3(inbound=inbound, outbound=[]))
    await tracker.refresh()
    assert tracker.contributed_usd == Decimal("0")


# ---------------------------------------------------------------------------
# FIX 3: eth_price_at_block raising must NOT break refresh (cache unchanged)
# ---------------------------------------------------------------------------


async def test_price_oracle_raising_leaves_cache_unchanged() -> None:
    """make_request succeeds with a valid ETH deposit but the price oracle raises
    (a zero/negative Chainlink answer). refresh() must not raise and the cache
    (prior $500) must be unchanged."""
    # Seed the cache with a good USDC-only refresh ($500, no price call needed).
    seed = [
        _transfer_row(asset="USDC", raw_value_hex=hex(500_000000), decimals=6,
                      block_hex="0x10", from_addr=_OPERATOR, to_addr=_SAFE),
    ]
    w3 = _make_w3(inbound=seed, outbound=[])
    tracker = _tracker(w3)
    await tracker.refresh()
    assert tracker.contributed_usd == Decimal("500")

    # Now an ETH deposit arrives but the price oracle raises (bad feed answer).
    async def _raising_price(_block_no: int) -> Decimal:
        raise RuntimeError("Chainlink ETH/USD returned non-positive answer=0")

    eth_inbound = [
        _transfer_row(asset="ETH", raw_value_hex=hex(2 * 10**18), decimals=18,
                      block_hex="0x10", from_addr=_OPERATOR, to_addr=_SAFE),
    ]
    raising = ContributedCapitalTracker(
        w3=_make_w3(inbound=eth_inbound, outbound=[]),
        safe_address=_SAFE,
        funding_addresses=frozenset({_OPERATOR}),
        eth_price_at_block=_raising_price,
        usdc_address=_USDC,
        weth_address=_WETH,
    )
    # Pre-seed its cache to $500 to prove "unchanged on failure".
    raising._contributed_usd = Decimal("500")
    await raising.refresh()  # must NOT raise
    assert raising.contributed_usd == Decimal("500")  # unchanged


# ---------------------------------------------------------------------------
# FIX 1: pagination — pageKey is followed; all pages aggregated
# ---------------------------------------------------------------------------


async def test_refresh_follows_pagekey_and_aggregates_all_pages() -> None:
    """make_request returns a first page WITH a pageKey then a final page WITHOUT;
    both pages' transfers must be counted."""
    page1 = [
        _transfer_row(asset="USDC", raw_value_hex=hex(300_000000), decimals=6,
                      block_hex="0x10", from_addr=_OPERATOR, to_addr=_SAFE),
    ]
    page2 = [
        _transfer_row(asset="USDC", raw_value_hex=hex(200_000000), decimals=6,
                      block_hex="0x20", from_addr=_OPERATOR, to_addr=_SAFE),
    ]

    w3 = MagicMock(name="AsyncWeb3")

    async def _make_request(method: str, params: list) -> dict:
        assert method == "alchemy_getAssetTransfers"
        p = params[0]
        # Outbound query (fromAddress=Safe) → no transfers.
        if p.get("fromAddress") == _SAFE:
            return {"result": {"transfers": []}}
        # Inbound: first call has no pageKey → return page1 + a pageKey; the
        # follow-up call carries that pageKey → return page2 with no pageKey.
        if "pageKey" not in p:
            return {"result": {"transfers": page1, "pageKey": "next-1"}}
        assert p["pageKey"] == "next-1"
        return {"result": {"transfers": page2}}

    w3.provider.make_request = AsyncMock(side_effect=_make_request)
    tracker = _tracker(w3)
    await tracker.refresh()
    # 300 + 200 USDC across two pages.
    assert tracker.contributed_usd == Decimal("500")


async def test_refresh_skips_malformed_row_without_aborting() -> None:
    """A row missing blockNum is skipped; the good row in the same page counts."""
    good = _transfer_row(asset="USDC", raw_value_hex=hex(500_000000), decimals=6,
                         block_hex="0x10", from_addr=_OPERATOR, to_addr=_SAFE)
    malformed = {
        "asset": "USDC", "from": _OPERATOR.lower(), "to": _SAFE.lower(),
        # blockNum intentionally absent.
        "rawContract": {"value": hex(999_000000), "decimals": hex(6), "address": _USDC.lower()},
    }
    tracker = _tracker(_make_w3(inbound=[malformed, good], outbound=[]))
    await tracker.refresh()
    assert tracker.contributed_usd == Decimal("500")  # only the good row
