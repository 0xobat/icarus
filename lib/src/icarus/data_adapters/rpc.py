"""EVM archive-RPC adapter — gas + price from a Base archive node (Alchemy).

What this adapter sources, today:
  - `gas_gwei`        ← `eth_gasPrice` (live) / `eth_getBlock.baseFeePerGas` (historical)
  - `prices["ETH"]`   ← Chainlink ETH/USD aggregator on Base

What it intentionally does NOT source (yet):
  - APYs / pool TVL — that is the DefiLlama adapter's job. RPC reads of
    Aave / Moonwell / Aerodrome pool state come in W4 when we add
    protocol-specific decoders. Keeping this adapter slim until then keeps
    the contract obvious: "anything on-chain that's not a pool".

Chain support: `base` only in W3. `solana` is a `NotImplementedError`
because EVM JSON-RPC doesn't address Solana; a parallel `SolanaRpcAdapter`
will land alongside the solana-executor work.

Provider: reads `ALCHEMY_BASE_HTTP_URL` from env. We accept the env var
(rather than a constructor arg) so the same adapter instance can be
configured by docker-compose env in prod and by a `.env` in dev without
the caller plumbing it through.

Historical iteration: `fetch_historical` walks blocks at a configurable
stride (default ~1 block per hour at Base's 2s blocktime → 1800 blocks).
Smaller strides multiply RPC cost linearly; backtests should match the
stride to the strategy's decision cadence.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol, cast

import structlog
from web3 import AsyncHTTPProvider, AsyncWeb3
from web3.contract import AsyncContract

from icarus.types import MarketSnapshot
from icarus.types.market import Chain, PoolState

_logger = structlog.get_logger(service="data_adapters.rpc")

_RPC_ENV_VAR = "ALCHEMY_BASE_HTTP_URL"

# Chainlink ETH/USD price feed on Base mainnet.
# https://docs.chain.link/data-feeds/price-feeds/addresses?network=base&page=1
_CHAINLINK_ETH_USD_BASE = "0x71041dddad3595F9CEd3DcCFBe3D1F4b0a16Bb70"

# Minimal AggregatorV3Interface ABI — only the calls we need.
_AGGREGATOR_V3_ABI: list[dict[str, Any]] = [
    {
        "inputs": [],
        "name": "decimals",
        "outputs": [{"internalType": "uint8", "name": "", "type": "uint8"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "latestRoundData",
        "outputs": [
            {"internalType": "uint80", "name": "roundId", "type": "uint80"},
            {"internalType": "int256", "name": "answer", "type": "int256"},
            {"internalType": "uint256", "name": "startedAt", "type": "uint256"},
            {"internalType": "uint256", "name": "updatedAt", "type": "uint256"},
            {"internalType": "uint80", "name": "answeredInRound", "type": "uint80"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
]

# Base mainnet ~2s block time. ~1800 blocks ≈ 1 hour.
_DEFAULT_HISTORICAL_STRIDE_BLOCKS = 1800


class _Web3Like(Protocol):
    """Structural subset of AsyncWeb3 we depend on.

    Declared so unit tests can hand us a `unittest.mock.AsyncMock` shaped
    object without having to construct a real AsyncWeb3.
    """

    eth: Any


def _wei_to_gwei(wei: int) -> Decimal:
    """Convert wei → gwei as Decimal (1 gwei = 1e9 wei)."""
    return Decimal(wei) / Decimal(10**9)


class RpcAdapter:
    """EVM archive-RPC adapter satisfying `DataAdapter`.

    Construction:
        adapter = RpcAdapter()                       # reads env
        adapter = RpcAdapter(rpc_url="https://...")  # explicit
        adapter = RpcAdapter(w3=mock_async_web3)     # unit tests

    The `w3` injection path bypasses env-var reads entirely, which is what
    makes the unit tests deterministic.
    """

    name = "rpc"
    historical_supported = True

    def __init__(
        self,
        *,
        rpc_url: str | None = None,
        w3: _Web3Like | None = None,
        chainlink_eth_usd_address: str = _CHAINLINK_ETH_USD_BASE,
        chainlink_usdc_usd_address: str | None = None,
        historical_stride_blocks: int = _DEFAULT_HISTORICAL_STRIDE_BLOCKS,
    ) -> None:
        if w3 is not None:
            self._w3: _Web3Like = w3
        else:
            url = rpc_url or os.environ.get(_RPC_ENV_VAR)
            if not url:
                raise RuntimeError(
                    f"RpcAdapter requires either `rpc_url=` or the "
                    f"{_RPC_ENV_VAR} env var to be set."
                )
            self._w3 = AsyncWeb3(AsyncHTTPProvider(url))

        self._chainlink_address = chainlink_eth_usd_address
        # Optional USDC/USD peg feed. When None, we never read it and omit
        # "USDC" from the snapshot — USDC then pins to $1 downstream (today's
        # behaviour). When set, fetch_live prices USDC live, symmetric with ETH.
        self._chainlink_usdc_address = chainlink_usdc_usd_address
        self._stride = historical_stride_blocks
        self._eth_usd_contract: AsyncContract | None = None
        self._usdc_usd_contract: AsyncContract | None = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _eth_usd(self) -> Any:
        """Return (cached) Chainlink ETH/USD contract handle.

        Typed `Any` because `_Web3Like` is structural and tests pass mocks
        that don't satisfy the full AsyncWeb3 type. The runtime call site
        only needs `.functions.latestRoundData().call()` and `.decimals()`.
        """
        if self._eth_usd_contract is None:
            w3 = cast(Any, self._w3)
            self._eth_usd_contract = w3.eth.contract(
                address=self._chainlink_address,
                abi=_AGGREGATOR_V3_ABI,
            )
        return self._eth_usd_contract

    def _usdc_usd(self) -> Any:
        """Return (cached) Chainlink USDC/USD contract handle.

        Only called when `_chainlink_usdc_address` is set. Mirrors `_eth_usd`.
        """
        if self._usdc_usd_contract is None:
            w3 = cast(Any, self._w3)
            self._usdc_usd_contract = w3.eth.contract(
                address=self._chainlink_usdc_address,
                abi=_AGGREGATOR_V3_ABI,
            )
        return self._usdc_usd_contract

    async def _eth_price_usd(self) -> Decimal:
        """Read ETH/USD from Chainlink, normalised to a Decimal in USD.

        Chainlink answers are int256 scaled by `decimals()` (typically 8).
        We pull decimals once and reuse it on subsequent reads via the
        cached contract handle.
        """
        contract = self._eth_usd()
        decimals: int = await contract.functions.decimals().call()
        round_data = await contract.functions.latestRoundData().call()
        # latestRoundData → (roundId, answer, startedAt, updatedAt, answeredInRound)
        answer: int = round_data[1]
        if answer <= 0:
            # Chainlink emits negative/zero only on misconfigured feeds.
            # Surfacing zero would silently break a strategy's price math;
            # raising forces the operator to notice.
            raise RuntimeError(
                f"Chainlink ETH/USD returned non-positive answer={answer}"
            )
        return Decimal(answer) / (Decimal(10) ** decimals)

    async def _usdc_price_usd(self) -> Decimal:
        """Read USDC/USD from Chainlink, normalised to a Decimal in USD.

        Mirrors `_eth_price_usd` exactly (same decimals scaling, same
        non-positive guard). Only called when a USDC feed is configured.
        """
        contract = self._usdc_usd()
        decimals: int = await contract.functions.decimals().call()
        round_data = await contract.functions.latestRoundData().call()
        answer: int = round_data[1]
        if answer <= 0:
            raise RuntimeError(
                f"Chainlink USDC/USD returned non-positive answer={answer}"
            )
        return Decimal(answer) / (Decimal(10) ** decimals)

    async def _gas_price_gwei(self) -> Decimal:
        """Live gas price in gwei."""
        w3 = cast(Any, self._w3)
        wei: int = await w3.eth.gas_price
        return _wei_to_gwei(wei)

    # ------------------------------------------------------------------
    # DataAdapter Protocol
    # ------------------------------------------------------------------
    async def fetch_live(self, chain: Chain) -> MarketSnapshot:
        """Snapshot gas + ETH price for `chain` at head."""
        if chain != "base":
            raise NotImplementedError(
                f"RpcAdapter only supports chain='base' in W3; got {chain!r}. "
                "SolanaRpcAdapter is a separate adapter."
            )

        _logger.info("rpc_fetch_live_start", chain=chain)
        eth_usd, gas_gwei = await self._eth_price_usd(), await self._gas_price_gwei()
        now = datetime.now(tz=UTC)

        prices: dict[str, Decimal] = {"ETH": eth_usd}
        # Price USDC live only when a feed is configured. Unset → omit USDC so it
        # pins to $1 downstream (backward compatible; no extra RPC call).
        usdc_usd: Decimal | None = None
        if self._chainlink_usdc_address is not None:
            usdc_usd = await self._usdc_price_usd()
            prices["USDC"] = usdc_usd

        snapshot = MarketSnapshot(
            timestamp=now,
            chain=chain,
            prices=prices,
            apys={},
            pool_state=cast(dict[str, PoolState], {}),
            gas_gwei=gas_gwei,
            metadata={"source": "rpc", "block": "latest"},
        )
        _logger.info(
            "rpc_fetch_live_ok",
            chain=chain,
            eth_usd=str(eth_usd),
            usdc_usd=str(usdc_usd) if usdc_usd is not None else None,
            gas_gwei=str(gas_gwei),
        )
        return snapshot

    async def fetch_historical(
        self, chain: Chain, start: datetime, end: datetime
    ) -> AsyncIterator[MarketSnapshot]:
        """Yield one snapshot per historical block stride in [start, end].

        Walks block numbers by `self._stride`, reads each block's
        `baseFeePerGas` (EIP-1559 base fee, present on Base) and uses the
        block timestamp. Price reads use `latestRoundData` evaluated at
        the historical block via `Contract.functions.X().call(block_identifier=...)`.

        Implementation note: scanning by block (not time) keeps RPC cost
        bounded. To translate (start, end) → (start_block, end_block) we
        binary-search via block timestamps. For W3 we approximate by
        starting from `latest` and walking backwards — the backtest engine
        (W4) will provide a proper time-to-block oracle.
        """
        if chain != "base":
            raise NotImplementedError(
                f"RpcAdapter only supports chain='base' in W3; got {chain!r}."
            )

        w3 = cast(Any, self._w3)
        latest_block = await w3.eth.get_block("latest")
        latest_number: int = latest_block["number"]
        latest_ts: int = latest_block["timestamp"]
        # Base average blocktime ~2s. Good enough to seed the walk-back.
        avg_blocktime_s = 2

        # Estimate the block numbers bracketing [start, end].
        start_ts = int(start.timestamp())
        end_ts = int(end.timestamp())
        start_block = max(1, latest_number - (latest_ts - start_ts) // avg_blocktime_s)
        end_block = max(1, latest_number - (latest_ts - end_ts) // avg_blocktime_s)
        start_block = min(start_block, latest_number)
        end_block = min(end_block, latest_number)

        _logger.info(
            "rpc_fetch_historical_start",
            chain=chain,
            start_block=start_block,
            end_block=end_block,
            stride=self._stride,
        )

        contract = self._eth_usd()
        decimals: int = await contract.functions.decimals().call()

        # USDC peg feed is optional and symmetric with the ETH read: when a
        # USDC/USD feed is configured, pull its decimals once and read it
        # per-block alongside ETH. Unset → omit "USDC" entirely (no extra call).
        usdc_contract = self._usdc_usd() if self._chainlink_usdc_address is not None else None
        usdc_decimals: int | None = None
        if usdc_contract is not None:
            usdc_decimals = await usdc_contract.functions.decimals().call()

        block_no = start_block
        emitted = 0
        while block_no <= end_block:
            block = await w3.eth.get_block(block_no)
            block_ts = block["timestamp"]
            block_dt = datetime.fromtimestamp(block_ts, tz=UTC)

            # baseFeePerGas is the right gas signal post-EIP-1559. Fall
            # back to `gas_price` semantics (just the base fee) if absent.
            base_fee_wei = block.get("baseFeePerGas")
            gas_gwei = _wei_to_gwei(base_fee_wei) if base_fee_wei is not None else Decimal(0)

            try:
                round_data = await contract.functions.latestRoundData().call(
                    block_identifier=block_no
                )
                answer: int = round_data[1]
                eth_usd = (
                    Decimal(answer) / (Decimal(10) ** decimals)
                    if answer > 0
                    else Decimal(0)
                )
            except Exception as exc:  # RPC errors are heterogeneous; log + continue
                _logger.warning(
                    "rpc_chainlink_read_failed",
                    block=block_no,
                    error=str(exc),
                )
                eth_usd = Decimal(0)

            prices: dict[str, Decimal] = {"ETH": eth_usd}
            if usdc_contract is not None and usdc_decimals is not None:
                try:
                    usdc_round = await usdc_contract.functions.latestRoundData().call(
                        block_identifier=block_no
                    )
                    usdc_answer: int = usdc_round[1]
                    usdc_usd = (
                        Decimal(usdc_answer) / (Decimal(10) ** usdc_decimals)
                        if usdc_answer > 0
                        else Decimal(0)
                    )
                except Exception as exc:  # mirror the ETH read: log + continue
                    _logger.warning(
                        "rpc_chainlink_usdc_read_failed",
                        block=block_no,
                        error=str(exc),
                    )
                    usdc_usd = Decimal(0)
                prices["USDC"] = usdc_usd

            yield MarketSnapshot(
                timestamp=block_dt,
                chain=chain,
                prices=prices,
                apys={},
                pool_state=cast(dict[str, PoolState], {}),
                gas_gwei=gas_gwei,
                metadata={"source": "rpc", "block": block_no},
            )
            emitted += 1
            block_no += self._stride

        _logger.info(
            "rpc_fetch_historical_done",
            chain=chain,
            emitted=emitted,
        )


__all__ = ["RpcAdapter"]
