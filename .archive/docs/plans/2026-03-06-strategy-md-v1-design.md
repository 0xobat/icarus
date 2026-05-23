# Strategy.md v1 Design — Stablecoin Yield on Base

**Date:** 2026-03-06
**Status:** Approved

---

## Summary

Simplify Icarus from 6 multi-tier strategies to 2 stablecoin-only yield strategies on Base. Delete all unused strategy code and encode modules.

## Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Chain | Base only | Cheapest gas ($0.01/TX), Aerodrome is dominant DEX |
| Assets | Stablecoins only (USDC, USDbC, DAI) | No volatile exposure, no IL on stable pairs |
| Capital | $500–$2,000 | Small scale validation |
| Split | 70% lending / 30% LP | Conservative bias, LP for incremental yield |
| Lending protocol | Aave V3 | Battle-tested, deep stablecoin markets on Base |
| LP protocol | Aerodrome | Dominant Base DEX, sAMM stable pools, AERO emissions |

## Strategy 1: LEND-001 — Aave V3 Lending Supply

- **Tier:** 1 (Low Risk)
- **Protocol:** Aave V3 on Base
- **Max allocation:** 70% of portfolio

**Behavior:** Supply USDC/USDbC to Aave V3. Rotate to highest APY market when differential > 0.5% and gas cost amortizes within 14 days.

**Entry:** Target APY > current + 0.5% after gas, market has >$1M liquidity.

**Exit:** APY < 1.0%, or protocol TVL drops >30% in 24h.

**Constraints:** Min position $100. Only rotate when net gain > $1/month after gas.

## Strategy 2: LP-001 — Aerodrome Stable LP + Auto-Compound

- **Tier:** 1 (Low Risk — stable-stable pairs only)
- **Protocol:** Aerodrome on Base
- **Max allocation:** 30% of portfolio

**Behavior:** Provide liquidity to sAMM stable pools (USDC/USDbC, USDC/DAI). Harvest AERO emission rewards, swap AERO to USDC, re-deposit to compound.

**Entry:** Pool APR > 3.0% (emission-based), pool TVL > $500K, AERO has swap liquidity.

**Exit:** APR < 1.5%, AERO price drops >50% in 24h, pool TVL < $200K.

**Harvest:** When pending AERO > $0.50 (gas ~$0.01 on Base). Swap AERO → USDC via Aerodrome router. Re-deposit.

**Constraints:** Min position $100. Stable-stable pairs only.

## Portfolio Rules

- Min 15% in liquid USDC reserve (undeployed) at all times
- Max 70% in Aave V3 (LEND-001)
- Max 30% in Aerodrome (LP-001)
- Both strategies on Base only

## Codebase Simplification

### Delete (py-engine/strategies/)

| File | Reason |
|------|--------|
| `lido_staking.py` + test | Not used in v1 |
| `uniswap_v3_lp.py` + test | Not used in v1 |
| `flash_loan_arb.py` + test | Not used in v1 |
| `rate_arb.py` + test | Not used in v1 |

### Delete (ts-executor encode modules)

| File | Reason |
|------|--------|
| `lido-adapter.ts` + test | No Lido in v1 |
| `uniswap-v3-adapter.ts` + test | No Uniswap V3 in v1 |
| `flash-loan-adapter.ts` + test | No flash loans in v1 |
| `gmx-adapter.ts` + test | No GMX in v1 |

### Keep and Refactor

| File | Change |
|------|--------|
| `aave_lending.py` | Refactor to LEND-001, Base-only, stablecoins-only |
| `yield_farming.py` | Refactor to LP-001, Aerodrome-specific, ve(3,3) model |
| `aave-v3-adapter.ts` | Keep — supplies lending calldata |
| `aerodrome-adapter.ts` | Already exists (EXEC-009). Review for sAMM stable pool support |

### New Code Needed

| Component | Purpose |
|-----------|---------|
| Aerodrome swap encoding | AERO → USDC swap calldata for compound step |
| Aerodrome harvest encoding | Claim pending AERO from gauge |
| Stable pool detection | Identify sAMM vs vAMM pools |

## Notes

- Aerodrome yield is emission-based (AERO tokens from gauge votes), not fee-based. APR changes weekly with gauge votes.
- Stable-stable pools use sAMM curve — near-zero IL for pegged assets.
- AERO price directly impacts effective APR. A 50% AERO crash halves the yield.
- On $500–$2,000 capital, rotation thresholds must account for tiny absolute gains (0.5% on $1,000 = $5/year).
