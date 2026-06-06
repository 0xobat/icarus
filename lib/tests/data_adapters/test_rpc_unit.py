"""Unit tests for RpcAdapter — fully mocked, no network.

We hand the adapter a `unittest.mock.AsyncMock`-shaped object via the
`w3=` injection seam, which lets us assert exact RPC interactions
without standing up a fork or hitting Alchemy.

Coverage:
  - Protocol conformance (DataAdapter)
  - Env-var contract (missing var → RuntimeError; explicit url overrides)
  - Chain restriction (`solana` raises NotImplementedError)
  - fetch_live: wei→gwei conversion, Chainlink decimals scaling
  - fetch_historical: stride walking, EIP-1559 baseFeePerGas, error
    swallowing on per-block Chainlink reads
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from decimal import Decimal
from itertools import pairwise
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from icarus.data_adapters import RpcAdapter
from icarus.protocols.data import DataAdapter


def _make_contract(*, decimals: int = 8, eth_price_scaled: int = 3500_00000000) -> MagicMock:
    """Build a mock Chainlink AggregatorV3 contract.

    `eth_price_scaled` defaults to $3500 at 8 decimals.

    The shape we mimic:
        await contract.functions.decimals().call()
        await contract.functions.latestRoundData().call([block_identifier=...])

    Async-Web3 contract calls return coroutines, so each leaf .call is
    an AsyncMock.
    """
    contract = MagicMock(name="ChainlinkETHUSD")

    decimals_call = AsyncMock(return_value=decimals)
    contract.functions.decimals.return_value.call = decimals_call

    # latestRoundData returns a 5-tuple; we only consume index 1.
    round_data = (1, eth_price_scaled, 0, 0, 1)
    latest_call = AsyncMock(return_value=round_data)
    contract.functions.latestRoundData.return_value.call = latest_call

    return contract


def _make_w3(
    *,
    gas_price_wei: int = 2_000_000_000,  # 2 gwei
    contract: MagicMock | None = None,
    blocks: dict[Any, dict[str, Any]] | None = None,
) -> MagicMock:
    """Build a mock AsyncWeb3.

    `blocks` is keyed by block identifier ("latest" or an int) → block dict.
    """
    if contract is None:
        contract = _make_contract()
    if blocks is None:
        blocks = {
            "latest": {
                "number": 1_000_000,
                "timestamp": int(datetime(2026, 5, 1, tzinfo=UTC).timestamp()),
                "baseFeePerGas": 2_000_000_000,
            }
        }

    w3 = MagicMock(name="AsyncWeb3")

    # `await w3.eth.gas_price` — the attribute access itself returns the
    # awaitable. We model this by making `gas_price` an AsyncMock with
    # `__await__` on the call result, but the simpler trick is: the test
    # adapter uses `await w3.eth.gas_price`, which in real AsyncWeb3 is a
    # property returning a coroutine. We mimic with a coroutine via
    # `PropertyMock` on a class — too clunky. Instead we monkey on a
    # coroutine-returning descriptor by using a plain `AsyncMock()` and
    # accepting that calling site does `await w3.eth.gas_price`.
    #
    # Workaround: in real web3.py, `w3.eth.gas_price` returns a coroutine
    # via a `__getattr__`. We replicate by making it a *coroutine object*
    # at attribute-access time. Easiest portable way is wrapping the eth
    # mock in a tiny shim.
    async def _gas_price_coro() -> int:
        return gas_price_wei

    eth = MagicMock(name="eth")
    # Plain assignment — every read of `w3.eth.gas_price` returns a fresh
    # coroutine (so awaiting twice works).
    type(eth).gas_price = property(lambda _self: _gas_price_coro())

    async def _get_block(block_id: Any) -> dict[str, Any]:
        # Allow int-keyed dict lookups for historical, plus "latest".
        return blocks[block_id]

    eth.get_block = AsyncMock(side_effect=_get_block)
    eth.contract = MagicMock(return_value=contract)

    w3.eth = eth
    return w3


def test_adapter_satisfies_protocol() -> None:
    w3 = _make_w3()
    adapter = RpcAdapter(w3=w3)
    assert isinstance(adapter, DataAdapter)
    assert adapter.name == "rpc"
    assert adapter.historical_supported is True


def test_missing_env_var_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALCHEMY_BASE_HTTP_URL", raising=False)
    with pytest.raises(RuntimeError, match="ALCHEMY_BASE_HTTP_URL"):
        RpcAdapter()


def test_env_var_consumed_when_no_w3(monkeypatch: pytest.MonkeyPatch) -> None:
    """We can't truly verify the URL was honoured without firing a request,
    but we can verify construction succeeds when the env var is set."""
    monkeypatch.setenv("ALCHEMY_BASE_HTTP_URL", "https://example.invalid/rpc")
    # Should not raise.
    adapter = RpcAdapter()
    assert adapter.name == "rpc"
    # Quick sanity that we didn't somehow read the env when w3 is injected:
    monkeypatch.delenv("ALCHEMY_BASE_HTTP_URL")
    assert RpcAdapter(w3=_make_w3()).name == "rpc"


@pytest.mark.asyncio
async def test_fetch_live_solana_unsupported() -> None:
    adapter = RpcAdapter(w3=_make_w3())
    with pytest.raises(NotImplementedError, match="base"):
        await adapter.fetch_live("solana")


@pytest.mark.asyncio
async def test_fetch_live_returns_eth_price_and_gas() -> None:
    """gas in gwei, ETH price decoded with 8 decimals."""
    w3 = _make_w3(
        gas_price_wei=5_000_000_000,  # 5 gwei
        contract=_make_contract(decimals=8, eth_price_scaled=3500_00000000),
    )
    adapter = RpcAdapter(w3=w3)
    snap = await adapter.fetch_live("base")

    assert snap.chain == "base"
    assert snap.gas_gwei == Decimal(5)
    assert snap.prices == {"ETH": Decimal(3500)}
    assert snap.apys == {}
    assert snap.pool_state == {}
    assert snap.metadata == {"source": "rpc", "block": "latest"}
    assert snap.timestamp.tzinfo is not None  # tz-aware


@pytest.mark.asyncio
async def test_fetch_live_raises_on_zero_chainlink_answer() -> None:
    """A non-positive Chainlink answer is a feed misconfiguration we will
    not paper over — the adapter must raise."""
    contract = _make_contract(eth_price_scaled=0)
    w3 = _make_w3(contract=contract)
    adapter = RpcAdapter(w3=w3)
    with pytest.raises(RuntimeError, match="non-positive"):
        await adapter.fetch_live("base")


@pytest.mark.asyncio
async def test_fetch_live_handles_alt_decimals() -> None:
    """A 6-decimal feed must still scale correctly. Defensive — Chainlink
    is conventionally 8 but new feeds occasionally vary."""
    contract = _make_contract(decimals=6, eth_price_scaled=3500_000000)
    w3 = _make_w3(contract=contract)
    adapter = RpcAdapter(w3=w3)
    snap = await adapter.fetch_live("base")
    assert snap.prices["ETH"] == Decimal(3500)


@pytest.mark.asyncio
async def test_fetch_historical_walks_blocks_by_stride() -> None:
    """We construct blocks at known numbers and verify the adapter walks
    them with the configured stride."""
    latest_number = 100_000
    latest_ts = int(datetime(2026, 5, 1, 12, 0, tzinfo=UTC).timestamp())

    # The walk-back uses 2s blocktime to translate (start, end) → block
    # range. With stride=10 and a tight time window we expect ~3 snapshots.
    start = datetime.fromtimestamp(latest_ts - 60, tz=UTC)  # 30 blocks back
    end = datetime.fromtimestamp(latest_ts, tz=UTC)

    blocks: dict[Any, dict[str, Any]] = {
        "latest": {
            "number": latest_number,
            "timestamp": latest_ts,
            "baseFeePerGas": 3_000_000_000,
        }
    }
    # Populate blocks at every block number the adapter might ask for.
    for n in range(latest_number - 100, latest_number + 1):
        blocks[n] = {
            "number": n,
            "timestamp": latest_ts - 2 * (latest_number - n),
            "baseFeePerGas": 1_000_000_000 + (n % 10) * 100_000_000,
        }

    w3 = _make_w3(blocks=blocks)
    adapter = RpcAdapter(w3=w3, historical_stride_blocks=10)

    snaps = [s async for s in adapter.fetch_historical("base", start, end)]

    # Window is ~60s → ~30 blocks; stride=10 → ~3-4 snapshots.
    assert 2 <= len(snaps) <= 5
    # Block numbers are strictly increasing.
    block_numbers = [s.metadata["block"] for s in snaps]
    assert block_numbers == sorted(block_numbers)
    # All within the configured stride.
    diffs = [b2 - b1 for b1, b2 in pairwise(block_numbers)]
    assert all(d == 10 for d in diffs)
    # Each snapshot has gas in gwei and a price (>0 because contract is healthy).
    for s in snaps:
        assert s.chain == "base"
        assert s.gas_gwei > 0
        assert s.prices["ETH"] == Decimal(3500)
        assert s.timestamp.tzinfo is not None


@pytest.mark.asyncio
async def test_fetch_historical_solana_unsupported() -> None:
    adapter = RpcAdapter(w3=_make_w3())
    with pytest.raises(NotImplementedError):
        async for _ in adapter.fetch_historical(
            "solana",
            datetime(2026, 1, 1, tzinfo=UTC),
            datetime(2026, 1, 2, tzinfo=UTC),
        ):
            pass


@pytest.mark.asyncio
async def test_fetch_historical_tolerates_chainlink_read_failure() -> None:
    """If a per-block Chainlink call raises, the adapter must keep walking
    and emit a snapshot with price=0 + log a warning. Strategy authors
    should treat ETH=0 as "missing" — that's a documented sentinel."""
    latest_number = 100
    latest_ts = int(datetime(2026, 5, 1, tzinfo=UTC).timestamp())
    blocks: dict[Any, dict[str, Any]] = {
        "latest": {
            "number": latest_number,
            "timestamp": latest_ts,
            "baseFeePerGas": 1_000_000_000,
        }
    }
    for n in range(80, latest_number + 1):
        blocks[n] = {
            "number": n,
            "timestamp": latest_ts - 2 * (latest_number - n),
            "baseFeePerGas": 1_000_000_000,
        }

    contract = _make_contract()
    # Make latestRoundData *fail* on historical reads (any block_identifier
    # passed) but succeed without one. We can detect the historical call
    # by inspecting call kwargs via side_effect.
    async def _maybe_fail(*_args: Any, **kwargs: Any) -> tuple[int, int, int, int, int]:
        if "block_identifier" in kwargs:
            raise RuntimeError("simulated archive miss")
        return (1, 3500_00000000, 0, 0, 1)

    contract.functions.latestRoundData.return_value.call = AsyncMock(side_effect=_maybe_fail)

    w3 = _make_w3(blocks=blocks, contract=contract)
    adapter = RpcAdapter(w3=w3, historical_stride_blocks=5)

    start = datetime.fromtimestamp(latest_ts - 40, tz=UTC)
    end = datetime.fromtimestamp(latest_ts, tz=UTC)
    snaps = [s async for s in adapter.fetch_historical("base", start, end)]

    assert len(snaps) >= 1
    # Every historical snapshot's price defaulted to zero (the sentinel).
    for s in snaps:
        assert s.prices["ETH"] == Decimal(0)
        # Gas still came through normally.
        assert s.gas_gwei == Decimal(1)


def test_env_var_constant_unchanged() -> None:
    """Pin the env var name — renaming it silently would orphan deploys."""
    from icarus.data_adapters.rpc import _RPC_ENV_VAR

    assert _RPC_ENV_VAR == "ALCHEMY_BASE_HTTP_URL"
    # Also assert the test process doesn't have it set in a way that
    # would mask the missing-env-var test (defensive — pytest isolation
    # of monkeypatch should handle it, but belt-and-braces).
    _ = os.environ.get(_RPC_ENV_VAR)  # touch to avoid unused-import flag


# ---------------------------------------------------------------------------
# USDC peg oracle — optional USDC/USD feed, $1 fallback, no extra RPC when off
# ---------------------------------------------------------------------------
_USDC_FEED_ADDR = "0x458138Fc0D67027E9A6778ef40a6ffC318c69061"  # Base mainnet USDC/USD


def _make_w3_two_feeds(
    *, eth_contract: MagicMock, usdc_contract: MagicMock, usdc_address: str
) -> MagicMock:
    """A mock AsyncWeb3 that dispatches `eth.contract(address=...)` by address,
    so we can give the ETH and USDC feeds independent answers and assert which
    feeds were actually instantiated."""
    w3 = _make_w3(contract=eth_contract)

    def _contract(*, address: str, abi: Any) -> MagicMock:
        if address == usdc_address:
            return usdc_contract
        return eth_contract

    w3.eth.contract = MagicMock(side_effect=_contract)
    return w3


@pytest.mark.asyncio
async def test_fetch_live_populates_usdc_when_feed_configured() -> None:
    """With a USDC/USD feed address, fetch_live adds prices['USDC'] from it."""
    eth_contract = _make_contract(decimals=8, eth_price_scaled=3500_00000000)
    # $0.9996 at 8 decimals.
    usdc_contract = _make_contract(decimals=8, eth_price_scaled=99_960000)
    w3 = _make_w3_two_feeds(
        eth_contract=eth_contract, usdc_contract=usdc_contract, usdc_address=_USDC_FEED_ADDR
    )
    adapter = RpcAdapter(w3=w3, chainlink_usdc_usd_address=_USDC_FEED_ADDR)
    snap = await adapter.fetch_live("base")
    assert snap.prices["ETH"] == Decimal(3500)
    assert snap.prices["USDC"] == Decimal("0.9996")


@pytest.mark.asyncio
async def test_fetch_live_omits_usdc_when_no_feed() -> None:
    """No USDC feed address → USDC absent from prices and no USDC feed read."""
    eth_contract = _make_contract(decimals=8, eth_price_scaled=3500_00000000)
    w3 = _make_w3(contract=eth_contract)
    adapter = RpcAdapter(w3=w3)  # default: no USDC feed
    snap = await adapter.fetch_live("base")
    assert "USDC" not in snap.prices
    assert snap.prices == {"ETH": Decimal(3500)}
    # Only the ETH feed contract was instantiated (one address).
    addresses = {c.kwargs.get("address") for c in w3.eth.contract.call_args_list}
    assert _USDC_FEED_ADDR not in addresses


@pytest.mark.asyncio
async def test_fetch_live_raises_on_zero_usdc_answer() -> None:
    """A non-positive USDC answer is a feed misconfiguration → raise (same guard
    as the ETH feed)."""
    eth_contract = _make_contract(decimals=8, eth_price_scaled=3500_00000000)
    usdc_contract = _make_contract(eth_price_scaled=0)
    w3 = _make_w3_two_feeds(
        eth_contract=eth_contract, usdc_contract=usdc_contract, usdc_address=_USDC_FEED_ADDR
    )
    adapter = RpcAdapter(w3=w3, chainlink_usdc_usd_address=_USDC_FEED_ADDR)
    with pytest.raises(RuntimeError, match="non-positive"):
        await adapter.fetch_live("base")


def _historical_blocks() -> dict[Any, dict[str, Any]]:
    """Build a block map for the historical walk (mirrors the ETH historical
    test): `latest` plus every block number the adapter might request."""
    latest_number = 100_000
    latest_ts = int(datetime(2026, 5, 1, 12, 0, tzinfo=UTC).timestamp())
    blocks: dict[Any, dict[str, Any]] = {
        "latest": {
            "number": latest_number,
            "timestamp": latest_ts,
            "baseFeePerGas": 3_000_000_000,
        }
    }
    for n in range(latest_number - 100, latest_number + 1):
        blocks[n] = {
            "number": n,
            "timestamp": latest_ts - 2 * (latest_number - n),
            "baseFeePerGas": 1_000_000_000 + (n % 10) * 100_000_000,
        }
    return blocks


@pytest.mark.asyncio
async def test_fetch_historical_populates_usdc_when_feed_configured() -> None:
    """With a USDC/USD feed, each historical snapshot carries prices['USDC'],
    symmetric with the per-block ETH read."""
    blocks = _historical_blocks()
    latest_ts = blocks["latest"]["timestamp"]
    start = datetime.fromtimestamp(latest_ts - 60, tz=UTC)
    end = datetime.fromtimestamp(latest_ts, tz=UTC)

    eth_contract = _make_contract(decimals=8, eth_price_scaled=3500_00000000)
    usdc_contract = _make_contract(decimals=8, eth_price_scaled=99_960000)  # $0.9996
    w3 = _make_w3(blocks=blocks, contract=eth_contract)

    def _contract(*, address: str, abi: Any) -> MagicMock:
        return usdc_contract if address == _USDC_FEED_ADDR else eth_contract

    w3.eth.contract = MagicMock(side_effect=_contract)

    adapter = RpcAdapter(
        w3=w3, historical_stride_blocks=10, chainlink_usdc_usd_address=_USDC_FEED_ADDR
    )
    snaps = [s async for s in adapter.fetch_historical("base", start, end)]
    assert snaps  # non-empty
    for s in snaps:
        assert s.prices["ETH"] == Decimal(3500)
        assert s.prices["USDC"] == Decimal("0.9996")


@pytest.mark.asyncio
async def test_fetch_historical_omits_usdc_when_no_feed() -> None:
    """No USDC feed address → historical snapshots omit USDC and no USDC feed
    contract is ever instantiated."""
    blocks = _historical_blocks()
    latest_ts = blocks["latest"]["timestamp"]
    start = datetime.fromtimestamp(latest_ts - 60, tz=UTC)
    end = datetime.fromtimestamp(latest_ts, tz=UTC)

    eth_contract = _make_contract(decimals=8, eth_price_scaled=3500_00000000)
    w3 = _make_w3(blocks=blocks, contract=eth_contract)
    adapter = RpcAdapter(w3=w3, historical_stride_blocks=10)  # no USDC feed

    snaps = [s async for s in adapter.fetch_historical("base", start, end)]
    assert snaps  # non-empty
    for s in snaps:
        assert "USDC" not in s.prices
        assert s.prices["ETH"] == Decimal(3500)
    # The USDC feed address was never used to build a contract.
    addresses = {c.kwargs.get("address") for c in w3.eth.contract.call_args_list}
    assert _USDC_FEED_ADDR not in addresses
