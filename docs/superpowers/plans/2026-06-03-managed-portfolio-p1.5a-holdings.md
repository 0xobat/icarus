# Managed Portfolio P1.5a — On-chain Holdings Provider Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development or superpowers:executing-plans. Checkbox (`- [ ]`) steps.

**Goal:** `RpcHoldingsProvider` — a real on-chain holdings reader implementing the P1.4 `HoldingsProvider` Protocol. Reads ERC-20 `balanceOf(SAFE_ADDRESS)` for the crypto + stable legs on Base, normalizes by decimals, prices via the P1.2 slice, and returns `(crypto_usd, stable_usd)`. Fully unit-testable with a mocked web3 (the only non-unit part is the live RPC, exercised in P1.5's operator run).

**Architecture:** New module `decision_engine/holdings.py`. Mirrors `lib/.../data_adapters/rpc.py`'s web3 injection seam (`w3=` constructor arg) so tests pass an `AsyncMock`-shaped object. Reuses `order_resolver.lookup_token` for addresses+decimals and `pricing.price_usd` for valuation. Self-contained: it fetches its own `MarketSnapshot` via an injected `DataAdapter` (a double-fetch vs the cycle; negligible for a per-tick Chainlink read — a P2 optimization passes one snapshot through).

**Tech Stack:** Python 3.13, `uv`, `pytest` (`asyncio_mode=auto`), `Decimal`, web3 (mocked).

---

## Context the implementer needs

- **The Protocol to satisfy** (`decision_engine.managed_cycle.HoldingsProvider`): `async current_usd_holdings(self) -> tuple[Decimal, Decimal]`, returning `(crypto_usd, stable_usd)`.
- **Address+decimals** come from `decision_engine.order_resolver.lookup_token(chain, symbol) -> TokenInfo(address, decimals)`.
- **Pricing** from `decision_engine.pricing.price_usd(symbol, market) -> Decimal` (USDC→$1, WETH→ETH price from the snapshot).
- **web3 shape** (from `rpc.py` + its tests): `w3.eth.contract(address=..., abi=...)` returns a contract whose `.functions.balanceOf(addr).call()` is awaitable and returns the raw integer balance. The test seam mirrors `lib/tests/data_adapters/test_rpc_unit.py` (`MagicMock` + `AsyncMock` leaves).
- Tests: `decision-engine/tests/test_holdings_unit.py`. Run: `uv run pytest <path> -v`.

## File Structure
- **Create:** `decision-engine/src/decision_engine/holdings.py` — `RpcHoldingsProvider`.
- **Create:** `decision-engine/tests/test_holdings_unit.py` — unit tests with mocked web3 + fake adapter.

---

## Task 1: `RpcHoldingsProvider`

**Files:** Create `decision-engine/src/decision_engine/holdings.py`; Test `decision-engine/tests/test_holdings_unit.py`.

- [ ] **Step 1: Write the failing test**

Create `decision-engine/tests/test_holdings_unit.py`:

```python
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest decision-engine/tests/test_holdings_unit.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'decision_engine.holdings'`

- [ ] **Step 3: Implement**

Create `decision-engine/src/decision_engine/holdings.py`:

```python
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
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest decision-engine/tests/test_holdings_unit.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Run full decision-engine suite (no regressions)**

Run: `uv run pytest decision-engine/tests -q`
Expected: all pass (was 306 → 310).

- [ ] **Step 6: Commit**

```bash
git add decision-engine/src/decision_engine/holdings.py decision-engine/tests/test_holdings_unit.py
git commit -m "feat(daedalus): P1.5a on-chain holdings provider (real balanceOf reads, priced)"
```

---

## Self-Review (against P1.5 brief "RpcHoldingsProvider")

**1. Spec coverage:** implements `HoldingsProvider.current_usd_holdings` via real `balanceOf` reads + decimals normalization + P1.2 pricing. `w3=` injection seam for tests. Out of scope (flagged): multi-asset, Solana, passing one shared snapshot (P2).

**2. Placeholder scan:** none.

**3. Type consistency:** `RpcHoldingsProvider(w3, adapter, safe_address, crypto_symbol, stable_symbol, chain)`, `_balance_tokens`, `current_usd_holdings` consistent. Satisfies the `HoldingsProvider` Protocol (asserted by `test_satisfies_holdings_protocol`). `lookup_token`/`price_usd` signatures match P1.1/P1.2.

**4. Arithmetic verified:** 2e18 wei / 1e18 = 2 WETH × $3000 = $6000; 6000e6 / 1e6 = 6000 USDC × $1 = $6000; 5e17/1e18 = 0.5 × $4000 = $2000.

---

## Execution Handoff
Subagent-Driven or Inline. Depends on P1.1 (`lookup_token`), P1.2 (`price_usd`), P1.4 (`HoldingsProvider` Protocol) — all merged. Additive; no live wiring.
