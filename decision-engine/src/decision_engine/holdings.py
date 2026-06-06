"""On-chain holdings provider — reads ERC-20 balances, prices them in USD.

Managed-portfolio P2.2. Implements the managed cycle's `HoldingsProvider`
Protocol by reading `balanceOf(SAFE_ADDRESS)` for each target asset on Base,
normalizing by decimals (from the P1.1 token registry) and pricing via the P1.2
slice into a per-asset USD dict. The `w3=` injection seam mirrors
`icarus.data_adapters.rpc`, so unit tests pass a mocked AsyncWeb3 and never
touch the network.

Scope: spot ERC-20 balances for the N target assets on Base. aToken (Aave) and
LST (wstETH) position legs arrive with their execution phases (P2.3/P2.4), where
those balances become non-zero; Solana balance reads land with P2.6.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Any

import structlog
from icarus.protocols.data import DataAdapter
from icarus.types.market import Chain

from decision_engine.order_resolver import DEFAULT_CHAIN_ID, lookup_token
from decision_engine.pricing import price_usd

logger = structlog.get_logger(service="decision-engine.holdings")

# Minimal ERC-20 ABI — only balanceOf.
_ERC20_BALANCEOF_ABI: list[dict[str, Any]] = [
    {
        "inputs": [{"internalType": "address", "name": "account", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    }
]


class RpcHoldingsProvider:
    """Reads on-chain balances for N target assets and values them in USD.

    Construction:
        RpcHoldingsProvider(w3=<AsyncWeb3>, adapter=<DataAdapter>,
                            safe_address="0x...", symbols=("WETH","USDC","WBTC"),
                            chain="base")
    """

    def __init__(
        self,
        *,
        w3: Any,
        adapter: DataAdapter,
        safe_address: str,
        symbols: Sequence[str],
        chain: Chain = "base",
        chain_id: int = DEFAULT_CHAIN_ID,
    ) -> None:
        self._w3 = w3
        self._adapter = adapter
        self._safe = safe_address
        self._symbols = tuple(symbols)
        self._chain = chain
        self._chain_id = chain_id

    async def _balance_tokens(self, symbol: str) -> Decimal:
        """ERC-20 balance of `symbol` for the Safe, in whole tokens."""
        info = lookup_token(self._chain, symbol, chain_id=self._chain_id)
        contract = self._w3.eth.contract(address=info.address, abi=_ERC20_BALANCEOF_ABI)
        raw: int = await contract.functions.balanceOf(self._safe).call()
        return Decimal(raw) / (Decimal(10) ** info.decimals)

    async def current_usd_by_asset(self) -> dict[str, Decimal]:
        """Return {symbol: usd} for each target asset from on-chain balances."""
        market = await self._adapter.fetch_live(self._chain)
        holdings: dict[str, Decimal] = {}
        for symbol in self._symbols:
            qty = await self._balance_tokens(symbol)
            holdings[symbol] = qty * price_usd(symbol, market)
        logger.info(
            "holdings_read",
            holdings={s: str(v) for s, v in holdings.items()},
            nav_usd=str(sum(holdings.values(), Decimal("0"))),
        )
        return holdings


__all__ = ["RpcHoldingsProvider"]
