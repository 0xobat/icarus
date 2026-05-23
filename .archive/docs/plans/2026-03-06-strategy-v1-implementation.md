# Strategy v1 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Simplify Icarus from 6 strategies to 2 stablecoin-only yield strategies on Base (Aave V3 lending + Aerodrome stable LP), deleting all unused code.

**Architecture:** Keep the existing strategy-per-file pattern. Two strategy classes (LEND-001, LP-001) emit execution:orders consumed by two TS encode modules (aave-v3-adapter, aerodrome-adapter). Everything else is deleted.

**Tech Stack:** Python (strategies, tests), TypeScript (encode modules), JSON Schema (execution-orders)

**Design doc:** `docs/plans/2026-03-06-strategy-md-v1-design.md`

---

### Task 1: Rename and Rewrite STRATEGY.md

**Files:**
- Delete: `strategy.md`
- Create: `STRATEGY.md`

**Step 1: Delete old strategy.md**

```bash
git rm strategy.md
```

**Step 2: Create STRATEGY.md with only LEND-001 and LP-001**

Write `STRATEGY.md` with exactly this content:

```markdown
# Icarus Strategy Definitions — v1

Chain: Base
Assets: Stablecoins only (USDC, USDbC, DAI)
Risk Profile: Conservative

---

## Aave V3 Lending Supply

**Tier: 1** | **Risk Profile: Low Risk**

ID: LEND-001

Supply stablecoins to Aave V3 on Base. Rotate to highest supply APY
market when the APY differential exceeds threshold after gas costs.

**Protocols:** Aave V3
**Chains:** Base

**Entry Conditions:**
- Target market APY exceeds current position APY by at least 0.5% after gas
- Target market has at least $1M available liquidity
- Gas cost amortized within 14 days at APY differential

**Exit Conditions:**
- Supply APY drops below 1.0%
- Protocol TVL drops more than 30% in 24 hours (circuit breaker)

**Constraints:**
- Max 70% of portfolio
- Min position size of $100 USD equivalent
- Only rotate when net monthly gain after gas exceeds $1
- Assets: USDC, USDbC only

---

## Aerodrome Stable LP

**Tier: 1** | **Risk Profile: Low Risk**

ID: LP-001

Provide liquidity to stable-stable pools on Aerodrome (USDC/USDbC,
USDC/DAI). Harvest AERO emission rewards, swap AERO to USDC,
re-deposit to compound.

**Protocols:** Aerodrome
**Chains:** Base

**Entry Conditions:**
- Pool emission APR exceeds 3.0%
- Pool TVL exceeds $500K
- AERO token has sufficient swap liquidity

**Exit Conditions:**
- Pool emission APR drops below 1.5%
- AERO price drops more than 50% in 24 hours
- Pool TVL drops below $200K

**Constraints:**
- Max 30% of portfolio
- Min position size of $100 USD equivalent
- Stable-stable pairs only (no volatile/stable pairs)
- Harvest when pending AERO value exceeds $0.50
- Swap AERO to USDC via Aerodrome router, re-deposit
```

**Step 3: Update ingestion.py references to strategy.md → STRATEGY.md**

Search `py-engine/` for any hardcoded `"strategy.md"` path references and update to `"STRATEGY.md"`. Check:
- `py-engine/strategies/ingestion.py`
- `py-engine/ai/code_gen.py`
- `py-engine/tests/test_strategy_ingestion.py`
- `py-engine/tests/test_code_gen.py`

**Step 4: Run ingestion test**

```bash
cd py-engine && uv run pytest tests/test_strategy_ingestion.py -v
```

Expected: Some failures (old strategy IDs in test fixtures). That's OK — we'll fix tests in Task 7.

**Step 5: Commit**

```bash
git add STRATEGY.md && git commit -m "feat(icarus): rewrite STRATEGY.md — LEND-001 + LP-001 only (Base, stablecoins)"
```

---

### Task 2: Delete Unused TS Encode Modules

**Files:**
- Delete: `ts-executor/src/execution/lido-adapter.ts`
- Delete: `ts-executor/src/execution/uniswap-v3-adapter.ts`
- Delete: `ts-executor/src/execution/flash-loan-adapter.ts`
- Delete: `ts-executor/src/execution/gmx-adapter.ts`
- Delete: `ts-executor/tests/lido-adapter.test.ts`
- Delete: `ts-executor/tests/uniswap-v3-adapter.test.ts`
- Delete: `ts-executor/tests/flash-loan-adapter.test.ts`
- Delete: `ts-executor/tests/gmx-adapter.test.ts`
- Delete: `ts-executor/tests/flashbots-protect.test.ts`
- Modify: `ts-executor/src/index.ts`
- Modify: `ts-executor/tests/index.test.ts`

**Step 1: Delete adapter source files**

```bash
cd ts-executor
git rm src/execution/lido-adapter.ts
git rm src/execution/uniswap-v3-adapter.ts
git rm src/execution/flash-loan-adapter.ts
git rm src/execution/gmx-adapter.ts
```

**Step 2: Delete adapter test files**

```bash
git rm tests/lido-adapter.test.ts
git rm tests/uniswap-v3-adapter.test.ts
git rm tests/flash-loan-adapter.test.ts
git rm tests/gmx-adapter.test.ts
git rm tests/flashbots-protect.test.ts
```

**Step 3: Clean up index.ts imports and adapter map**

In `ts-executor/src/index.ts`:

Remove these imports:
```typescript
import * as lido from "./execution/lido-adapter.js";
import * as flashLoan from "./execution/flash-loan-adapter.js";
import * as uniV3 from "./execution/uniswap-v3-adapter.js";
import * as gmx from "./execution/gmx-adapter.js";
```

In `buildAdapterMap()`, remove the `map.set(...)` blocks for: `"lido"`, `"uniswap_v3"`, `"flash_loan"`, `"gmx"`.

Keep only: `"aave_v3"` and `"aerodrome"`.

Also remove FlashbotsProtectManager import and all flashbots wiring in `initializeComponents()` (the `flashbotsProtect` variable, the `if (flashbotsRpcUrl)` block, and the `flashbotsProtect` option passed to TransactionBuilder).

Check if `FlashbotsProtectManager` import in `transaction-builder.ts` needs cleanup too — if TransactionBuilder accepts optional flashbotsProtect, just stop passing it.

**Step 4: Update the aerodrome adapter routing to use schema-compliant action names**

The execution-orders schema action enum is: `supply`, `withdraw`, `swap`, `mint_lp`, `burn_lp`, `stake`, `unstake`, `collect_fees`, `flash_loan`.

In `buildAdapterMap()`, update the aerodrome adapter's `switch (action)` to use generic action names:

```typescript
map.set("aerodrome", {
  async buildTransaction(action, params) {
    const p = params as Record<string, string | undefined>;
    const amount = BigInt(p.amount!);
    const recipient = (p.recipient ?? p.tokenIn) as Address;
    const deadline = BigInt(
      p.deadline ?? String(Math.floor(Date.now() / 1000) + 1800),
    );
    switch (action) {
      case "mint_lp":
        return {
          to: aerodrome.ROUTER_ADDRESS,
          data: aerodrome.encodeAddLiquidity({
            tokenA: p.tokenIn as Address,
            tokenB: p.tokenOut as Address,
            stable: p.stable !== "false", // default true for v1
            amountADesired: amount,
            amountBDesired: BigInt(p.amountB ?? amount.toString()),
            amountAMin: BigInt(p.amountAMin ?? "0"),
            amountBMin: BigInt(p.amountBMin ?? "0"),
            to: recipient,
            deadline,
          }),
        };
      case "burn_lp":
        return {
          to: aerodrome.ROUTER_ADDRESS,
          data: aerodrome.encodeRemoveLiquidity({
            tokenA: p.tokenIn as Address,
            tokenB: p.tokenOut as Address,
            stable: p.stable !== "false",
            liquidity: amount,
            amountAMin: BigInt(p.amountAMin ?? "0"),
            amountBMin: BigInt(p.amountBMin ?? "0"),
            to: recipient,
            deadline,
          }),
        };
      case "stake":
        return {
          to: p.gauge as Address,
          data: aerodrome.encodeGaugeDeposit(amount),
        };
      case "unstake":
        return {
          to: p.gauge as Address,
          data: aerodrome.encodeGaugeWithdraw(amount),
        };
      case "collect_fees":
        return {
          to: p.gauge as Address,
          data: aerodrome.encodeGetReward(recipient),
        };
      case "swap":
        return {
          to: aerodrome.ROUTER_ADDRESS,
          data: aerodrome.encodeSwap({
            amountIn: amount,
            amountOutMin: BigInt(p.amountOutMin ?? "0"),
            routes: [{
              from: p.tokenIn as Address,
              to: p.tokenOut as Address,
              stable: p.stable !== "false",
              factory: aerodrome.FACTORY_ADDRESS,
            }],
            to: recipient,
            deadline,
          }),
        };
      default:
        throw new Error(`Unsupported aerodrome action: ${action}`);
    }
  },
});
```

Note: Check `aerodrome-adapter.ts` for `encodeSwap` signature and `FACTORY_ADDRESS` export. If `FACTORY_ADDRESS` doesn't exist, find it in the adapter or add it (Base mainnet: `0x420DD381b31aEf6683db6B902084cB0FFECe40Da`).

**Step 5: Update index.test.ts**

Remove test cases that reference deleted adapters (lido, uniswap_v3, flash_loan, gmx). Update any adapter map tests to only expect `aave_v3` and `aerodrome`.

**Step 6: Run TS tests**

```bash
cd ts-executor && pnpm test
```

Expected: All remaining tests pass. Type check clean.

**Step 7: Commit**

```bash
git add -A && git commit -m "refactor(icarus): delete unused TS encode modules — keep only aave_v3, aerodrome"
```

---

### Task 3: Delete Unused Python Strategy Files

**Files:**
- Delete: `py-engine/strategies/lido_staking.py`
- Delete: `py-engine/strategies/uniswap_v3_lp.py`
- Delete: `py-engine/strategies/flash_loan_arb.py`
- Delete: `py-engine/strategies/rate_arb.py`
- Delete: `py-engine/tests/test_lido_staking.py`
- Delete: `py-engine/tests/test_uniswap_v3_lp.py`
- Delete: `py-engine/tests/test_flash_loan_arb.py`
- Delete: `py-engine/tests/test_rate_arb.py`
- Modify: `py-engine/strategies/__init__.py`

**Step 1: Delete strategy source files**

```bash
cd py-engine
git rm strategies/lido_staking.py
git rm strategies/uniswap_v3_lp.py
git rm strategies/flash_loan_arb.py
git rm strategies/rate_arb.py
```

**Step 2: Delete strategy test files**

```bash
git rm tests/test_lido_staking.py
git rm tests/test_uniswap_v3_lp.py
git rm tests/test_flash_loan_arb.py
git rm tests/test_rate_arb.py
```

**Step 3: Update strategies/__init__.py**

Keep exporting AaveLendingStrategy (will become LEND-001 in Task 4):

```python
"""Strategy engine — signal generation, portfolio optimization."""

from __future__ import annotations

from strategies.aave_lending import AaveLendingStrategy

__all__ = ["AaveLendingStrategy"]
```

No change needed yet — will update in Task 5 after creating aerodrome_lp.py.

**Step 4: Run Python tests (expect some failures from integration tests referencing old strategies)**

```bash
cd py-engine && uv run pytest tests/ --tb=short -q --ignore=tests/test_strategy_ingestion.py
```

Note: `test_strategy_ingestion.py` will fail because it parses old strategy.md content — fix in Task 7. Focus on verifying deleted imports don't break remaining tests.

**Step 5: Commit**

```bash
git add -A && git commit -m "refactor(icarus): delete unused Python strategies — lido, uniswap_v3_lp, flash_loan_arb, rate_arb"
```

---

### Task 4: Refactor aave_lending.py → LEND-001

**Files:**
- Modify: `py-engine/strategies/aave_lending.py`
- Modify: `py-engine/tests/test_aave_lending.py`

**Step 1: Write a failing test for Base-only, stablecoin-only behavior**

Add to `test_aave_lending.py`:

```python
def test_rejects_non_base_chain():
    """LEND-001 only operates on Base."""
    strategy, _, _ = _make_strategy()
    markets = [_make_market(asset="USDC", chain="ethereum", supply_apy="0.06")]
    orders = strategy.generate_orders(markets)
    assert orders == []

def test_rejects_non_stablecoin_asset():
    """LEND-001 only operates with USDC and USDbC."""
    strategy, _, _ = _make_strategy()
    markets = [_make_market(asset="ETH", chain="base", supply_apy="0.06")]
    orders = strategy.generate_orders(markets)
    assert orders == []

def test_strategy_id_is_lend_001():
    """Strategy ID should be LEND-001."""
    from strategies.aave_lending import STRATEGY_ID
    assert STRATEGY_ID == "LEND-001"
```

**Step 2: Run tests to verify they fail**

```bash
cd py-engine && uv run pytest tests/test_aave_lending.py::test_rejects_non_base_chain tests/test_aave_lending.py::test_rejects_non_stablecoin_asset tests/test_aave_lending.py::test_strategy_id_is_lend_001 -v
```

Expected: FAIL (current code allows ethereum chain, ETH asset, uses STRAT-001 ID).

**Step 3: Update aave_lending.py**

Key changes:
- `STRATEGY_ID = "LEND-001"` (was `"STRAT-001"`)
- `STRATEGY_TIER = 1` (unchanged)
- `WHITELISTED_ASSETS = frozenset({"USDC", "USDbC"})` (was ETH, WETH, WBTC, USDC, USDT, DAI)
- `ALLOWED_CHAINS = frozenset({"base"})` (new constant)
- In `evaluate()`: filter markets to only `chain in ALLOWED_CHAINS` and `asset in WHITELISTED_ASSETS`
- Update `AaveLendingConfig` defaults:
  - `min_rotation_apy_diff`: keep `Decimal("0.005")` (0.5%)
  - `gas_amortization_days`: `14` (was 30)
  - `min_monthly_gain_usd`: `Decimal("1")` (new — skip rotation if net gain < $1/month)
  - `min_supply_apy`: `Decimal("0.01")` (1% floor, was maybe different)

**Step 4: Fix existing tests**

Update all test helpers:
- `_make_market()` default `chain="base"` (was `"ethereum"`)
- `_make_market()` default `asset="USDC"` (was `"ETH"`)
- Any test referencing `STRAT-001` → `LEND-001`
- Any test using non-stablecoin assets or non-Base chains → update

**Step 5: Run all aave_lending tests**

```bash
cd py-engine && uv run pytest tests/test_aave_lending.py -v
```

Expected: All pass.

**Step 6: Commit**

```bash
git add py-engine/strategies/aave_lending.py py-engine/tests/test_aave_lending.py
git commit -m "refactor(icarus): aave_lending → LEND-001 (Base-only, stablecoins-only)"
```

---

### Task 5: Refactor yield_farming.py → aerodrome_lp.py (LP-001)

**Files:**
- Delete: `py-engine/strategies/yield_farming.py`
- Create: `py-engine/strategies/aerodrome_lp.py`
- Delete: `py-engine/tests/test_yield_farming.py`
- Create: `py-engine/tests/test_aerodrome_lp.py`
- Modify: `py-engine/strategies/__init__.py`

**Step 1: Write test file test_aerodrome_lp.py with core tests**

Key test cases to cover:

```python
"""Aerodrome stable LP auto-compound — Tier 1 strategy (LP-001)."""
from decimal import Decimal
from strategies.aerodrome_lp import (
    AerodromeLpStrategy,
    AerodromeLpConfig,
    StablePool,
    STRATEGY_ID,
)

def test_strategy_id():
    assert STRATEGY_ID == "LP-001"

def test_evaluate_filters_by_min_apr():
    """Pools below 3% APR are excluded."""

def test_evaluate_filters_by_min_tvl():
    """Pools below $500K TVL are excluded."""

def test_evaluate_rejects_non_base_chain():
    """Only Base chain pools accepted."""

def test_evaluate_rejects_volatile_pools():
    """Only stable-stable pairs accepted."""

def test_should_harvest_above_threshold():
    """Harvest when pending AERO > $0.50."""

def test_should_not_harvest_below_threshold():
    """Don't harvest when pending AERO < $0.50."""

def test_generate_orders_enter_new_pool():
    """Enter: mint_lp order emitted for best pool."""

def test_generate_orders_harvest_and_compound():
    """Harvest: collect_fees + swap + mint_lp orders."""

def test_generate_orders_exit_low_apr():
    """Exit when APR drops below 1.5%."""

def test_generate_orders_exit_aero_crash():
    """Exit when AERO drops >50% in 24h."""

def test_orders_are_schema_compliant():
    """All orders pass execution-orders schema validation."""

def test_max_allocation_30_percent():
    """Respects 30% portfolio cap."""
```

**Step 2: Run tests to verify they fail**

```bash
cd py-engine && uv run pytest tests/test_aerodrome_lp.py -v
```

Expected: FAIL (module doesn't exist).

**Step 3: Create aerodrome_lp.py**

Model after yield_farming.py but specialized for Aerodrome:

```python
"""Aerodrome stable LP auto-compound — Tier 1 strategy (LP-001).

Provides liquidity to Aerodrome sAMM stable pools on Base. Harvests
AERO emission rewards, swaps AERO to USDC, re-deposits to compound.
"""

STRATEGY_ID = "LP-001"
STRATEGY_TIER = 1

@dataclass
class StablePool:
    """Snapshot of an Aerodrome stable pool."""
    pool_id: str
    token_a: str  # e.g. "USDC"
    token_b: str  # e.g. "USDbC"
    emission_apr: Decimal  # from AERO gauge votes
    tvl_usd: Decimal
    aero_price_usd: Decimal
    gauge_address: str  # for staking/harvesting
    chain: str = "base"

@dataclass
class AerodromeLpConfig:
    min_emission_apr: Decimal = Decimal("0.03")  # 3% entry
    exit_apr_threshold: Decimal = Decimal("0.015")  # 1.5% exit
    min_tvl_usd: Decimal = Decimal("500000")  # $500K
    min_harvest_value_usd: Decimal = Decimal("0.50")  # harvest threshold
    aero_crash_exit: Decimal = Decimal("0.50")  # 50% AERO price drop
    max_allocation: Decimal = Decimal("0.30")  # 30% of portfolio
    min_position_value_usd: Decimal = Decimal("100")
    # ... limits ...

class AerodromeLpStrategy:
    def evaluate(self, pools: list[StablePool]) -> list[StablePool]:
        """Filter and rank stable pools by emission APR."""
        # Filter: chain == "base", emission_apr >= threshold, tvl >= threshold
        # Reject volatile pairs (only accept known stablecoin pairs)

    def should_harvest(self, pending_aero_value_usd: Decimal) -> bool:
        """Harvest when pending AERO > $0.50."""

    def generate_orders(self, pools, ...) -> list[dict]:
        """Generate orders: mint_lp, collect_fees, swap, burn_lp."""
        # Entry: [mint_lp] (add liquidity to best stable pool)
        # Harvest: [collect_fees, swap, mint_lp] (claim AERO, swap to USDC, re-deposit)
        # Exit: [unstake, burn_lp] (withdraw from gauge, remove liquidity)
```

Key difference from yield_farming.py: **order actions use schema-compliant names**:
- `mint_lp` (not `supply`) for adding liquidity
- `burn_lp` (not `withdraw`) for removing liquidity
- `stake` for gauge deposit
- `unstake` for gauge withdraw
- `collect_fees` for AERO harvest
- `swap` for AERO → USDC conversion

Each order must include `protocol: "aerodrome"` and `chain: "base"`.
For gauge operations, include `gauge` address in params.
For swaps, include `tokenIn` (AERO address), `tokenOut` (USDC address), `amountOutMin`.

**Step 4: Delete old yield_farming files**

```bash
git rm py-engine/strategies/yield_farming.py
git rm py-engine/tests/test_yield_farming.py
```

**Step 5: Update strategies/__init__.py**

```python
"""Strategy engine — signal generation, portfolio optimization."""
from __future__ import annotations
from strategies.aave_lending import AaveLendingStrategy
from strategies.aerodrome_lp import AerodromeLpStrategy

__all__ = ["AaveLendingStrategy", "AerodromeLpStrategy"]
```

**Step 6: Run all tests**

```bash
cd py-engine && uv run pytest tests/test_aerodrome_lp.py tests/test_aave_lending.py -v
```

Expected: All pass.

**Step 7: Commit**

```bash
git add -A && git commit -m "feat(icarus): create LP-001 Aerodrome stable LP strategy, delete yield_farming"
```

---

### Task 6: Update Strategy Ingestion Pipeline

**Files:**
- Modify: `py-engine/strategies/ingestion.py`
- Modify: `py-engine/tests/test_strategy_ingestion.py`

**Step 1: Update ingestion.py constants**

```python
KNOWN_PROTOCOLS = frozenset({
    "aave", "aave_v3", "aerodrome",
})

KNOWN_CHAINS = frozenset({
    "base",
})

VALID_TIERS = frozenset({1})  # v1 is Tier 1 only
```

Also update the ID pattern regex if it currently only matches `STRAT-\d+` — it should also match `LEND-\d+` and `LP-\d+`.

Check the `_extract_id()` method for the regex pattern and update to something like:
```python
r"(?:ID|Id|id)\s*:\s*([A-Z]+-\d+)"
```

**Step 2: Write test for parsing new STRATEGY.md format**

Update `test_strategy_ingestion.py` test fixtures to use the new STRATEGY.md content (from Task 1). Key assertions:
- Parses exactly 2 strategies
- IDs are `LEND-001` and `LP-001`
- Both are Tier 1
- Protocols are `["aave_v3"]` and `["aerodrome"]`
- Chains are `["base"]` for both
- Entry/exit conditions are parsed correctly

**Step 3: Remove tests for old strategy parsing**

Delete any test that parses Lido, Uniswap V3, Flash Loan, or Rate Arb strategy content.

**Step 4: Run ingestion tests**

```bash
cd py-engine && uv run pytest tests/test_strategy_ingestion.py -v
```

Expected: All pass.

**Step 5: Commit**

```bash
git add py-engine/strategies/ingestion.py py-engine/tests/test_strategy_ingestion.py
git commit -m "refactor(icarus): update strategy ingestion for LEND-001/LP-001 format"
```

---

### Task 7: Fix Integration Tests and Cross-References

**Files:**
- Modify: `py-engine/tests/test_integration_e2e.py`
- Modify: `py-engine/tests/test_integration_schema_validation.py`
- Modify: `py-engine/tests/test_lifecycle_manager.py`
- Modify: `py-engine/tests/test_code_gen.py`
- Modify: `py-engine/tests/test_main_loop.py`
- Check: any other file referencing `STRAT-001` through `STRAT-006`

**Step 1: Grep for all remaining references to old strategy IDs**

```bash
cd py-engine && grep -rn "STRAT-00[1-6]" --include="*.py" .
```

Update every occurrence:
- `STRAT-001` → `LEND-001`
- `STRAT-004` → `LP-001`
- Remove references to `STRAT-002`, `STRAT-003`, `STRAT-005`, `STRAT-006`

**Step 2: Fix test_integration_e2e.py**

This imports `AaveLendingStrategy`, `AaveLendingConfig`, `AaveMarket`. These still exist but:
- Update strategy ID assertions from `STRAT-001` to `LEND-001`
- Update default chain in test helpers from `ethereum` to `base`
- Update default asset from `ETH` to `USDC`

**Step 3: Fix test_integration_schema_validation.py**

Lines 202-234 import `AaveLendingStrategy`. Same updates as Step 2:
- Chain: `"base"` (was `"ethereum"`)
- Strategy ID in assertions: `LEND-001`

**Step 4: Fix test_lifecycle_manager.py**

Update any strategy ID references in test fixtures. The lifecycle manager uses strategy IDs for tracking — update to `LEND-001` / `LP-001`.

**Step 5: Fix test_code_gen.py**

This imports `StrategyIngestor, StrategySpec`. Update test fixture strategy specs to use LEND-001/LP-001 format and protocols.

**Step 6: Fix test_main_loop.py**

Check for strategy ID references in decision loop test fixtures.

**Step 7: Search for remaining references in non-test code**

```bash
grep -rn "STRAT-00[1-6]\|lido_staking\|uniswap_v3_lp\|flash_loan_arb\|rate_arb\|yield_farming" --include="*.py" py-engine/
```

Fix any remaining references. Common places:
- `py-engine/ai/code_gen.py` — may reference strategy file names
- `py-engine/main.py` — probably clean (only imports lifecycle_manager)

**Step 8: Run full Python test suite**

```bash
cd py-engine && uv run pytest tests/ --tb=short -q
```

Expected: All pass. Note the test count will be lower (deleted ~110 tests from 4 strategy files).

**Step 9: Commit**

```bash
git add -A && git commit -m "fix(icarus): update all cross-references for LEND-001/LP-001 strategy IDs"
```

---

### Task 8: Update features.json

**Files:**
- Modify: `harness/features.json`

**Step 1: Update strategy feature entries**

For features that were deleted strategies, set `passes: false` and update description to note they were removed in v1 simplification. Or better: update the feature to reflect the new strategy.

Rename/update these entries:
- `STRAT-001` → update description to "LEND-001: Aave V3 lending supply on Base (stablecoins only)", keep `passes: true`
- `STRAT-004` → update description to "LP-001: Aerodrome stable LP auto-compound on Base", set `passes: false` (new implementation, needs verification)

For deleted strategies, set `passes: false` and add note:
- `STRAT-002` (Lido) → `passes: false`, description: "Removed in v1 simplification"
- `STRAT-003` (Uniswap V3) → `passes: false`, description: "Removed in v1 simplification"
- `STRAT-005` (Flash loan) → `passes: false`, description: "Removed in v1 simplification"
- `STRAT-006` (Rate arb) → `passes: false`, description: "Removed in v1 simplification"

For deleted TS encode modules:
- `EXEC-005` (Uniswap V3 encode) → `passes: false`, description: "Removed in v1 simplification"
- `EXEC-007` (Flash loan encode) → `passes: false`, description: "Removed in v1 simplification"
- `EXEC-009` → update description to "Aerodrome encode module (Base stable pools)", keep `passes: true` (adapter still exists)

**Important:** Per CLAUDE.md rules, NEVER remove features from features.json — only add or update `passes`.

**Step 2: Commit**

```bash
git add harness/features.json
git commit -m "docs(icarus): update features.json for v1 strategy simplification"
```

---

### Task 9: Full Verification + Progress Update

**Step 1: Run init.sh to ensure deps are current**

```bash
bash harness/init.sh
```

**Step 2: Run verify.sh**

```bash
bash harness/verify.sh
```

Expected: PASS. Both TS and Python tests pass, ruff clean, type check clean.

**Step 3: Check test counts**

Expected approximate counts:
- TS: ~220-250 tests (removed ~80 from deleted adapter tests)
- Python: ~1,100-1,200 tests (removed ~110 from deleted strategy tests, added ~15 for LP-001)

**Step 4: Update progress.txt**

Append session entry:

```
--- Session 2026-03-06-XX ---
Agent: Claude Opus 4.6
Worked on: v1 strategy simplification — stablecoin yield on Base
Completed:
  - Renamed strategy.md → STRATEGY.md with LEND-001 + LP-001 only
  - Deleted 4 Python strategies (lido, uniswap_v3_lp, flash_loan_arb, rate_arb) + tests
  - Deleted 4 TS encode modules (lido, uniswap_v3, flash_loan, gmx) + tests
  - Refactored aave_lending.py → LEND-001 (Base, stablecoins only)
  - Created aerodrome_lp.py → LP-001 (Aerodrome stable LP, ve(3,3))
  - Updated strategy ingestion for new ID format
  - Updated all cross-references and integration tests
  - Updated features.json
Blocked: none
Next: Validate on Base testnet, P2 historical stress testing
```

**Step 5: Final commit**

```bash
git add harness/progress.txt
git commit -m "docs(icarus): update progress.txt for v1 strategy simplification"
```

---

## Dependency Graph

```
Task 1 (STRATEGY.md)
  ↓
Task 2 (Delete TS) ←── independent
Task 3 (Delete Python) ←── independent
  ↓
Task 4 (Refactor aave_lending) ←── depends on Task 3 (no import conflicts)
Task 5 (Create aerodrome_lp) ←── depends on Task 3
  ↓
Task 6 (Update ingestion) ←── depends on Task 1 (new STRATEGY.md)
Task 7 (Fix cross-refs) ←── depends on Tasks 4, 5 (new IDs)
  ↓
Task 8 (features.json) ←── depends on all above
Task 9 (Verify) ←── depends on all above
```

Tasks 1, 2, 3 can run in parallel. Tasks 4, 5 can run in parallel after Task 3. Tasks 6, 7 can run in parallel after 4, 5.
