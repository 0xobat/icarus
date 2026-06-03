"""On-chain holdings provider — reads ERC-20 balances, prices them in USD.

Managed-portfolio P1.5a. Implements the managed cycle's `HoldingsProvider`
Protocol by reading `balanceOf(SAFE_ADDRESS)` for the crypto + stable legs on
Base, normalizing by decimals (from the P1.1 token registry) and pricing via
the P1.2 slice. The `w3=` injection seam mirrors `icarus.data_adapters.rpc`,
so unit tests pass a mocked AsyncWeb3 and never touch the network.

Scope (YAGNI): the two assets of the P1 trivial target (one crypto + one
stable on Base). Multi-asset / Solana balance reads arrive with those assets.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import structlog
from icarus.protocols.data import DataAdapter
from icarus.types.market import Chain

from decision_engine.order_resolver import lookup_token
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
    """Reads on-chain crypto/stable balances and values them in USD.

    Construction:
        RpcHoldingsProvider(w3=<AsyncWeb3>, adapter=<DataAdapter>,
                            safe_address="0x...", crypto_symbol="WETH",
                            stable_symbol="USDC", chain="base")
    """

    def __init__(
        self,
        *,
        w3: Any,
        adapter: DataAdapter,
        safe_address: str,
        crypto_symbol: str = "WETH",
        stable_symbol: str = "USDC",
        chain: Chain = "base",
    ) -> None:
        self._w3 = w3
        self._adapter = adapter
        self._safe = safe_address
        self._crypto_symbol = crypto_symbol
        self._stable_symbol = stable_symbol
        self._chain = chain

    async def _balance_tokens(self, symbol: str) -> Decimal:
        """ERC-20 balance of `symbol` for the Safe, in whole tokens."""
        info = lookup_token(self._chain, symbol)
        contract = self._w3.eth.contract(address=info.address, abi=_ERC20_BALANCEOF_ABI)
        raw: int = await contract.functions.balanceOf(self._safe).call()
        return Decimal(raw) / (Decimal(10) ** info.decimals)

    async def current_usd_holdings(self) -> tuple[Decimal, Decimal]:
        """Return (crypto_usd, stable_usd) from on-chain balances + live prices."""
        market = await self._adapter.fetch_live(self._chain)
        crypto_qty = await self._balance_tokens(self._crypto_symbol)
        stable_qty = await self._balance_tokens(self._stable_symbol)
        crypto_usd = crypto_qty * price_usd(self._crypto_symbol, market)
        stable_usd = stable_qty * price_usd(self._stable_symbol, market)
        logger.info(
            "holdings_read",
            crypto_symbol=self._crypto_symbol, crypto_usd=str(crypto_usd),
            stable_symbol=self._stable_symbol, stable_usd=str(stable_usd),
        )
        return crypto_usd, stable_usd


__all__ = ["RpcHoldingsProvider"]
