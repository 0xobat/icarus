# Managed Portfolio P1.1 — Order Resolver Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a pure, fully-tested "order resolver" that turns a high-level rebalance intent (chain, asset symbols, USD amount, prices, recipient) into executor-ready `OrderParams` — real token addresses, smallest-unit amounts, recipient, and a slippage-bounded `amount_out_min`. This retires the critique's fatal finding #3 (orders that carried only a USD scalar).

**Architecture:** A new self-contained module `decision_engine/order_resolver.py` with (1) a static token registry `(chain, symbol) → address + decimals`, (2) a USD→smallest-unit converter, (3) `resolve_swap_params()` that assembles a valid `icarus.envelopes.orders.OrderParams` for an Aerodrome-style swap. No DB, Redis, or network — pure functions, unit-tested in isolation. The decision-engine cycle (a later plan) calls this; the `ts-executor` Aerodrome adapter consumes the `OrderParams` verbatim.

**Tech Stack:** Python 3.13, `uv` workspace, `pytest` (`--import-mode=importlib`, `asyncio_mode=auto`), pydantic v2 envelopes, `Decimal` arithmetic.

---

## Context the implementer needs (read before starting)

- **The order contract** lives in `lib/src/icarus/envelopes/orders.py`. `OrderParams` fields: `token_in: str|None`, `token_out: str|None`, `amount: Decimal|None` (**smallest unit** — wei/lamports), `recipient: str|None`, `pool_id: str|None`, `venue: str|None`, `extra: dict[str, str|int|Decimal|None]`. The model is `frozen=True, extra="forbid"`.
- **What the executor reads** (`ts-executor/src/index.ts:101-187`, the `aerodrome` adapter, `swap` action): `params.token_in` and `params.token_out` cast `as Address` (must be real hex addresses), `BigInt(params.amount)` (must be an integer smallest-unit string), `params.recipient` (falls back to `token_in` — a known trap, so recipient must always be set), `extra.amount_out_min` via `BigInt(extra.amount_out_min ?? "0")` (must be an integer string), `extra.deadline` (unix seconds string), `extra.stable` (`"true"`/`"false"`). There is **no token-symbol registry on the executor side** — addresses must come from us.
- **Real Base mainnet addresses** (used below): USDC = `0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913` (6 decimals); WETH = `0x4200000000000000000000000000000000000006` (18 decimals).
- **Scope discipline (YAGNI):** P1's trivial target is "hold USDC + ETH on Base, 60/40, no staking/LP." So this plan registers **only USDC + WETH on Base** and supports **only the `swap` primitive**. SOL/wBTC/Solana/lending/LP land in later plans, not here.
- **Test location & runner:** tests go in `decision-engine/tests/`. Run a single test with `uv run pytest <path>::<test> -v` from the repo root.

---

## File Structure

- **Create:** `decision-engine/src/decision_engine/order_resolver.py` — the resolver (token registry + converter + `resolve_swap_params`). One responsibility: intent → executor-ready `OrderParams`.
- **Create:** `decision-engine/tests/test_order_resolver_unit.py` — unit tests for all three pieces.

No existing files are modified in this plan.

---

## Task 1: Token registry + lookup

**Files:**
- Create: `decision-engine/src/decision_engine/order_resolver.py`
- Test: `decision-engine/tests/test_order_resolver_unit.py`

- [ ] **Step 1: Write the failing test**

Create `decision-engine/tests/test_order_resolver_unit.py`:

```python
"""Unit tests for the order resolver (managed-portfolio P1.1)."""

from __future__ import annotations

import pytest

from decision_engine.order_resolver import TokenInfo, lookup_token


def test_lookup_usdc_base() -> None:
    info = lookup_token("base", "USDC")
    assert info == TokenInfo(
        address="0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913", decimals=6
    )


def test_lookup_weth_base() -> None:
    info = lookup_token("base", "WETH")
    assert info.address == "0x4200000000000000000000000000000000000006"
    assert info.decimals == 18


def test_lookup_unknown_symbol_raises() -> None:
    with pytest.raises(KeyError, match="UNKNOWN"):
        lookup_token("base", "UNKNOWN")


def test_lookup_unsupported_chain_raises() -> None:
    with pytest.raises(KeyError, match="solana"):
        lookup_token("solana", "USDC")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest decision-engine/tests/test_order_resolver_unit.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'decision_engine.order_resolver'`

- [ ] **Step 3: Write minimal implementation**

Create `decision-engine/src/decision_engine/order_resolver.py`:

```python
"""Order resolver — turns a rebalance intent into executor-ready OrderParams.

Managed-portfolio P1.1. Pure functions, no I/O. The decision-engine cycle
calls this to build the `params` block of an ExecutionOrder; the ts-executor
protocol adapters consume the result verbatim (real addresses, smallest-unit
amounts, recipient, slippage-bounded amount_out_min).

Scope (YAGNI): only USDC + WETH on Base, and only the `swap` primitive — the
P1 trivial target ("hold USDC + ETH on Base, 60/40"). Later phases add SOL,
wBTC, Solana, lending, and LP.
"""

from __future__ import annotations

from dataclasses import dataclass

from icarus.types.market import Chain


@dataclass(frozen=True)
class TokenInfo:
    """A token's on-chain address and ERC-20 decimals."""

    address: str
    decimals: int


# Static token registry. Real Base mainnet addresses.
_TOKEN_REGISTRY: dict[Chain, dict[str, TokenInfo]] = {
    "base": {
        "USDC": TokenInfo("0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913", 6),
        "WETH": TokenInfo("0x4200000000000000000000000000000000000006", 18),
    },
}


def lookup_token(chain: Chain, symbol: str) -> TokenInfo:
    """Resolve (chain, symbol) → TokenInfo. Raises KeyError if unregistered."""
    try:
        chain_tokens = _TOKEN_REGISTRY[chain]
    except KeyError as exc:
        raise KeyError(f"no token registry for chain {chain!r}") from exc
    try:
        return chain_tokens[symbol]
    except KeyError as exc:
        raise KeyError(f"token {symbol!r} not registered on chain {chain!r}") from exc
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest decision-engine/tests/test_order_resolver_unit.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add decision-engine/src/decision_engine/order_resolver.py decision-engine/tests/test_order_resolver_unit.py
git commit -m "feat(daedalus): P1.1 order resolver — token registry + lookup"
```

---

## Task 2: USD → smallest-unit converter

**Files:**
- Modify: `decision-engine/src/decision_engine/order_resolver.py`
- Test: `decision-engine/tests/test_order_resolver_unit.py`

- [ ] **Step 1: Write the failing test**

Append to `decision-engine/tests/test_order_resolver_unit.py`:

```python
from decimal import Decimal

from decision_engine.order_resolver import usd_to_smallest_unit


def test_usd_to_smallest_unit_weth() -> None:
    # $6000 of WETH at $3000 = 2 WETH = 2e18 wei
    assert usd_to_smallest_unit(
        Decimal("6000"), Decimal("3000"), 18
    ) == Decimal("2000000000000000000")


def test_usd_to_smallest_unit_usdc() -> None:
    # $6000 of USDC at $1 = 6000 USDC = 6000e6 (6 decimals)
    assert usd_to_smallest_unit(
        Decimal("6000"), Decimal("1"), 6
    ) == Decimal("6000000000")


def test_usd_to_smallest_unit_floors_fractional_base_units() -> None:
    # $100 of WETH at $3000 = 0.033333... WETH; must floor to an integer wei,
    # never emit a fractional smallest-unit.
    result = usd_to_smallest_unit(Decimal("100"), Decimal("3000"), 18)
    assert result == Decimal("33333333333333333")  # floor(100/3000 * 1e18)
    assert result == result.to_integral_value()


def test_usd_to_smallest_unit_rejects_nonpositive_price() -> None:
    with pytest.raises(ValueError, match="price"):
        usd_to_smallest_unit(Decimal("100"), Decimal("0"), 18)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest decision-engine/tests/test_order_resolver_unit.py -v`
Expected: FAIL with `ImportError: cannot import name 'usd_to_smallest_unit'`

- [ ] **Step 3: Write minimal implementation**

Add to `decision-engine/src/decision_engine/order_resolver.py` — add `from decimal import Decimal, ROUND_DOWN` to the imports, then append:

```python
def usd_to_smallest_unit(
    usd_amount: Decimal, price_usd: Decimal, decimals: int
) -> Decimal:
    """Convert a USD value into a token's smallest unit (wei/atomic).

    token_qty = usd_amount / price_usd; smallest_unit = token_qty * 10**decimals,
    floored to an integer (never emit a fractional base unit, which would be an
    invalid on-chain amount).
    """
    if price_usd <= 0:
        raise ValueError(f"price_usd must be positive, got {price_usd}")
    token_qty = usd_amount / price_usd
    smallest = token_qty * (Decimal(10) ** decimals)
    return smallest.quantize(Decimal(1), rounding=ROUND_DOWN)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest decision-engine/tests/test_order_resolver_unit.py -v`
Expected: PASS (8 passed)

- [ ] **Step 5: Commit**

```bash
git add decision-engine/src/decision_engine/order_resolver.py decision-engine/tests/test_order_resolver_unit.py
git commit -m "feat(daedalus): P1.1 order resolver — USD to smallest-unit conversion"
```

---

## Task 3: `resolve_swap_params` — assemble executor-ready OrderParams

**Files:**
- Modify: `decision-engine/src/decision_engine/order_resolver.py`
- Test: `decision-engine/tests/test_order_resolver_unit.py`

- [ ] **Step 1: Write the failing test**

Append to `decision-engine/tests/test_order_resolver_unit.py`:

```python
from icarus.envelopes.orders import OrderParams

from decision_engine.order_resolver import resolve_swap_params

_SAFE = "0x1111111111111111111111111111111111111111"


def test_resolve_swap_params_usdc_to_weth() -> None:
    # Swap $6000 of USDC into WETH. USDC=$1, WETH=$3000, 50 bps slippage.
    params = resolve_swap_params(
        chain="base",
        token_in_symbol="USDC",
        token_out_symbol="WETH",
        usd_amount=Decimal("6000"),
        price_in_usd=Decimal("1"),
        price_out_usd=Decimal("3000"),
        recipient=_SAFE,
        slippage_bps=50,
        deadline_unix=1_900_000_000,
    )
    assert isinstance(params, OrderParams)
    # Addresses resolved from the registry.
    assert params.token_in == "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
    assert params.token_out == "0x4200000000000000000000000000000000000006"
    # amount = 6000 USDC in smallest unit (6 decimals).
    assert params.amount == Decimal("6000000000")
    # recipient is ALWAYS set (never falls back to token_in in the executor).
    assert params.recipient == _SAFE
    # extra carries the executor's swap knobs as strings.
    assert params.extra["deadline"] == "1900000000"
    assert params.extra["stable"] == "false"
    # expected out = 6000/3000 = 2 WETH = 2e18; min_out = 2e18 * (1 - 0.005).
    assert params.extra["amount_out_min"] == "1990000000000000000"


def test_resolve_swap_params_amount_out_min_floors() -> None:
    # 100 bps slippage on a non-round expected_out must floor to an integer.
    params = resolve_swap_params(
        chain="base",
        token_in_symbol="WETH",
        token_out_symbol="USDC",
        usd_amount=Decimal("100"),
        price_in_usd=Decimal("3000"),
        price_out_usd=Decimal("1"),
        recipient=_SAFE,
        slippage_bps=100,
        deadline_unix=1_900_000_000,
    )
    # expected_out = 100 USDC = 100e6; min_out = floor(100e6 * 0.99) = 99000000
    assert params.extra["amount_out_min"] == "99000000"
    # amount_in = 100/3000 WETH floored to wei.
    assert params.amount == Decimal("33333333333333333")


def test_resolve_swap_params_rejects_bad_slippage() -> None:
    with pytest.raises(ValueError, match="slippage_bps"):
        resolve_swap_params(
            chain="base",
            token_in_symbol="USDC",
            token_out_symbol="WETH",
            usd_amount=Decimal("6000"),
            price_in_usd=Decimal("1"),
            price_out_usd=Decimal("3000"),
            recipient=_SAFE,
            slippage_bps=1001,  # > 1000 (10%) is out of contract range
            deadline_unix=1_900_000_000,
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest decision-engine/tests/test_order_resolver_unit.py -v`
Expected: FAIL with `ImportError: cannot import name 'resolve_swap_params'`

- [ ] **Step 3: Write minimal implementation**

Add to `decision-engine/src/decision_engine/order_resolver.py` — add `from icarus.envelopes.orders import OrderParams` to the imports, then append:

```python
def resolve_swap_params(
    *,
    chain: Chain,
    token_in_symbol: str,
    token_out_symbol: str,
    usd_amount: Decimal,
    price_in_usd: Decimal,
    price_out_usd: Decimal,
    recipient: str,
    slippage_bps: int,
    deadline_unix: int,
    stable: bool = False,
) -> OrderParams:
    """Build executor-ready OrderParams for an Aerodrome-style swap.

    Resolves token symbols to addresses, converts the USD notional to the
    token_in smallest unit, and computes a slippage-bounded `amount_out_min`
    in the token_out smallest unit. `recipient` is always set explicitly — the
    Aerodrome adapter falls back to `token_in` if recipient is absent, which
    would send funds to the token contract.
    """
    if not 0 <= slippage_bps <= 1000:
        raise ValueError(f"slippage_bps must be in [0, 1000], got {slippage_bps}")

    token_in = lookup_token(chain, token_in_symbol)
    token_out = lookup_token(chain, token_out_symbol)

    amount_in = usd_to_smallest_unit(usd_amount, price_in_usd, token_in.decimals)
    expected_out = usd_to_smallest_unit(usd_amount, price_out_usd, token_out.decimals)
    amount_out_min = (
        expected_out * Decimal(10_000 - slippage_bps) / Decimal(10_000)
    ).quantize(Decimal(1), rounding=ROUND_DOWN)

    return OrderParams(
        token_in=token_in.address,
        token_out=token_out.address,
        amount=amount_in,
        recipient=recipient,
        extra={
            "amount_out_min": str(amount_out_min),
            "deadline": str(deadline_unix),
            "stable": "true" if stable else "false",
        },
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest decision-engine/tests/test_order_resolver_unit.py -v`
Expected: PASS (11 passed)

- [ ] **Step 5: Run the full decision-engine suite to confirm no regressions**

Run: `uv run pytest decision-engine/tests -q`
Expected: all pass (existing tests unaffected — new module is additive)

- [ ] **Step 6: Commit**

```bash
git add decision-engine/src/decision_engine/order_resolver.py decision-engine/tests/test_order_resolver_unit.py
git commit -m "feat(daedalus): P1.1 order resolver — resolve_swap_params (retires critique finding #3)"
```

---

## Self-Review (completed against `docs/design-managed-portfolio.md` §7 P1 + §4 build-new #3)

**1. Spec coverage:** The design's P1 "order resolver — `(chain, protocol, asset) → contract addresses + decimals`; `USD amount → token smallest-unit`; `recipient = Safe/Squads`" is covered: Task 1 (addresses + decimals), Task 2 (USD→smallest-unit), Task 3 (recipient always set + full params). LP/lending/Solana resolution is intentionally **out of scope** for this plan (later phases) and is flagged as such — not a gap.

**2. Placeholder scan:** No TBD/TODO/"handle edge cases"/"similar to" — every step has complete code and exact commands.

**3. Type consistency:** `TokenInfo(address, decimals)`, `lookup_token(chain, symbol)`, `usd_to_smallest_unit(usd_amount, price_usd, decimals)`, `resolve_swap_params(...)` are named identically in tests and implementation across all three tasks. `Chain` is imported from `icarus.types.market` (the same import `orders.py` uses). `OrderParams` field names (`token_in`, `token_out`, `amount`, `recipient`, `extra`) match `lib/src/icarus/envelopes/orders.py` verbatim. The `extra` keys (`amount_out_min`, `deadline`, `stable`) match what the `ts-executor` aerodrome adapter reads (`index.ts:106-182`).

**4. Verified arithmetic:** `6000/3000 * 1e18 = 2e18`; `2e18 * 9950/10000 = 1.99e18 = 1990000000000000000` ✓. `100/3000 * 1e18 = 33333333333333333` (floored) ✓. `100e6 * 9900/10000 = 99000000` ✓.

---

## The rest of P1 (roadmap — separate plans, written as we reach them)

Each is its own focused plan with the same TDD rigor; each needs a few more files read before it can be specified precisely (noted).

- **P1.2 — Price + gas/slippage data slice.** Real spot prices (ETH/USD, etc.) and gas estimates feeding the resolver and the cost gate. *Needs:* read `lib/src/icarus/protocols/data.py` (the `DataAdapter` Protocol), `lib/src/icarus/data_adapters/rpc.py`, and `MarketSnapshot` in `icarus.types`.
- **P1.3 — Managed-portfolio cycle (allocation engine + cost-gated rebalancer).** Replace the lake-based `DecisionCycle` with: read portfolio → current vs 60/40 target → ±10% band check → cost gate → build orders via the P1.1 resolver → risk gate → publish. *Needs:* `icarus.types` (PortfolioSnapshot/Position), `decision_engine/risk_gate.py`, `lib/src/icarus/db/models.py` (PortfolioPosition).
- **P1.4 — Breakers + exposure limiter wired to live state; execution-results consumer.** Call `.update()` on the breakers from the runtime; subscribe to `execution:results` and reconcile `PortfolioPosition` rows to on-chain truth (retires critique #2's "breakers never fed" and the "no results consumer" finding). *Needs:* `decision-engine/src/decision_engine/risk/*.py`, `lib/src/icarus/db/repository.py`, `lib/src/icarus/envelopes/results.py`.
- **P1.5 — Real end-to-end test (the P1 exit criterion).** One rebalance round-trip on the production resolver + order construction (no injected fixtures), observed end-to-end. *Needs:* `harness/e2e_smoke.py` for the existing harness pattern.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-03-managed-portfolio-p1-order-resolver.md`. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
