# Managed Portfolio P1.2 — Pricing & Cost Slice Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** A thin, pure pricing/cost layer that turns a `MarketSnapshot` into (a) USD prices per asset symbol (feeding the P1.1 order resolver) and (b) a USD cost estimate for a swap (feeding the P1.3 cost gate).

**Architecture:** New module `decision_engine/pricing.py`. It does NOT fetch anything — the existing `RpcAdapter` (`lib/src/icarus/data_adapters/rpc.py`) already sources real ETH/USD (Chainlink on Base) and `gas_gwei` into `MarketSnapshot`. This module is the interpretation layer: stablecoins pinned to $1, the `WETH→ETH` price-key alias, and a gas+slippage cost estimate. Pure functions, unit-tested against hand-built `MarketSnapshot`s.

**Tech Stack:** Python 3.13, `uv`, `pytest`, `Decimal`. Depends on `icarus.types.MarketSnapshot` (fields: `prices: Mapping[str,Decimal]`, `gas_gwei: Decimal`, ...).

---

## Context the implementer needs

- `MarketSnapshot` is a frozen dataclass in `lib/src/icarus/types/market.py`: `timestamp, chain, prices (Mapping[str,Decimal]), apys, pool_state, gas_gwei (Decimal), metadata`. The `RpcAdapter` populates `prices={"ETH": <chainlink usd>}` and `gas_gwei=<eth_gasPrice in gwei>` for Base.
- The P1.1 resolver (`decision_engine/order_resolver.py`) consumes `price_in_usd`/`price_out_usd` for symbols like `"USDC"`/`"WETH"`. The market snapshot keys ETH as `"ETH"`, so `"WETH"` must alias to `"ETH"`.
- Gas cost in USD = `gas_gwei × gas_units / 1e9 (→ ETH) × eth_price_usd`.
- Tests: `decision-engine/tests/test_pricing_unit.py`. Run: `uv run pytest <path> -v` from repo root.

## File Structure

- **Create:** `decision-engine/src/decision_engine/pricing.py` — `price_usd()` + `estimate_swap_cost_usd()`.
- **Create:** `decision-engine/tests/test_pricing_unit.py` — unit tests.

---

## Task 1: `price_usd` — symbol → USD from a snapshot

**Files:** Create `decision-engine/src/decision_engine/pricing.py`; Test `decision-engine/tests/test_pricing_unit.py`.

- [ ] **Step 1: Write the failing test**

Create `decision-engine/tests/test_pricing_unit.py`:

```python
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest decision-engine/tests/test_pricing_unit.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'decision_engine.pricing'`

- [ ] **Step 3: Implement**

Create `decision-engine/src/decision_engine/pricing.py`:

```python
"""Pricing & cost slice — interpret a MarketSnapshot for the resolver + cost gate.

Managed-portfolio P1.2. Pure functions, no I/O. The RpcAdapter already
sources real ETH/USD (Chainlink, Base) and gas into the MarketSnapshot; this
module pins stablecoins to $1, aliases WETH→ETH, and estimates swap cost in USD.

Scope (YAGNI): the symbols the P1 trivial target needs — USDC and WETH/ETH on
Base. SOL/wBTC/Solana pricing arrives with those assets in later phases.
"""

from __future__ import annotations

from decimal import Decimal

from icarus.types import MarketSnapshot

# Stablecoins are pinned to $1 (depeg monitoring is a P3 concern, not pricing).
_STABLE_SYMBOLS = frozenset({"USDC", "USDT", "DAI"})

# Wrapped assets price off their underlying's snapshot key.
_PRICE_KEY_ALIASES = {"WETH": "ETH"}


def price_usd(symbol: str, market: MarketSnapshot) -> Decimal:
    """USD price for an asset symbol from a market snapshot.

    Stablecoins pin to $1; wrapped assets alias to their underlying's price
    key; everything else reads `market.prices`. Raises KeyError if unpriced.
    """
    if symbol in _STABLE_SYMBOLS:
        return Decimal("1")
    price_key = _PRICE_KEY_ALIASES.get(symbol, symbol)
    try:
        return market.prices[price_key]
    except KeyError as exc:
        raise KeyError(f"no USD price for {symbol!r} (key {price_key!r}) in snapshot") from exc
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest decision-engine/tests/test_pricing_unit.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add decision-engine/src/decision_engine/pricing.py decision-engine/tests/test_pricing_unit.py
git commit -m "feat(daedalus): P1.2 pricing — price_usd (stablecoin pin + WETH alias)"
```

---

## Task 2: `estimate_swap_cost_usd` — gas + slippage cost for the cost gate

**Files:** Modify `decision-engine/src/decision_engine/pricing.py`; Test `decision-engine/tests/test_pricing_unit.py`.

- [ ] **Step 1: Write the failing test**

Append to `decision-engine/tests/test_pricing_unit.py`:

```python
from decision_engine.pricing import estimate_swap_cost_usd


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
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest decision-engine/tests/test_pricing_unit.py -v`
Expected: FAIL — `ImportError: cannot import name 'estimate_swap_cost_usd'`

- [ ] **Step 3: Implement**

Append to `decision-engine/src/decision_engine/pricing.py`:

```python
# A Base Aerodrome swap is ~150-250k gas; 200k is a safe central estimate for
# the cost gate. Tunable later from real receipts (ExecutionResult.gas_used_wei).
DEFAULT_SWAP_GAS_UNITS = 200_000


def estimate_swap_cost_usd(
    *,
    trade_usd: Decimal,
    slippage_bps: int,
    market: MarketSnapshot,
    eth_price_usd: Decimal,
    gas_units: int = DEFAULT_SWAP_GAS_UNITS,
) -> Decimal:
    """Estimate the USD cost of a swap = gas cost + slippage allowance.

    gas_usd  = gas_gwei * gas_units / 1e9 (→ ETH) * eth_price_usd
    slip_usd = trade_usd * slippage_bps / 10_000
    The cost gate compares the rebalance's corrective value against this.
    """
    gas_eth = market.gas_gwei * Decimal(gas_units) / Decimal(10**9)
    gas_usd = gas_eth * eth_price_usd
    slippage_usd = trade_usd * Decimal(slippage_bps) / Decimal(10_000)
    return gas_usd + slippage_usd
```

Also append `estimate_swap_cost_usd` and `DEFAULT_SWAP_GAS_UNITS` to a module `__all__` (add `__all__ = ["price_usd", "estimate_swap_cost_usd", "DEFAULT_SWAP_GAS_UNITS"]` if not present).

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest decision-engine/tests/test_pricing_unit.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Run full decision-engine suite (no regressions)**

Run: `uv run pytest decision-engine/tests -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add decision-engine/src/decision_engine/pricing.py decision-engine/tests/test_pricing_unit.py
git commit -m "feat(daedalus): P1.2 pricing — estimate_swap_cost_usd (gas + slippage)"
```

---

## Self-Review (against design §5 "Data layer" + §7 P1.2)

**1. Spec coverage:** "Real spot prices feeding the resolver" → `price_usd` (Task 1, real ETH from Chainlink via the snapshot). "gas estimates feeding the cost gate" → `estimate_swap_cost_usd` (Task 2). Out of scope (later phases, flagged): SOL/wBTC/Solana prices, on-chain quote refinement of slippage.

**2. Placeholder scan:** none — complete code + exact commands.

**3. Type consistency:** `price_usd(symbol, market)` and `estimate_swap_cost_usd(*, trade_usd, slippage_bps, market, eth_price_usd, gas_units)` are identical in tests and impl. `MarketSnapshot` import matches `icarus.types`.

**4. Arithmetic verified:** gas: `1 gwei × 200000 / 1e9 = 0.0002 ETH × $3000 = $0.60`; slippage `$10000 × 50/10000 = $50.00`; total `$50.60` ✓.

---

## Execution Handoff
Two execution options: (1) Subagent-Driven (recommended), (2) Inline. Note: depends on P1.1 (`order_resolver`) being merged, though this module has no direct import of it — they meet in P1.3.
