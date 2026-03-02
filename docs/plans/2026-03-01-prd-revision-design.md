# PRD Revision & TS-Executor Simplification Design

**Date:** 2026-03-01
**Status:** Approved
**Scope:** Update PRD to reflect architectural decisions (Safe wallet, ethskills.com guidance), split P1 into sub-phases, simplify ts-executor codebase.

---

## Context

The PRD (docs/prd.md) is out of sync with the codebase. The Safe 1-of-2 multisig wallet replaced ERC-4337, but the PRD still references Alchemy Smart Wallet throughout. Additionally, 4 of 6 protocol adapters are dead code (unreachable from the main execution path), Flashbots Protect is not imported anywhere, and a standalone contract allowlist is instantiated but never wired in.

ethskills.com recommendations ground these decisions:
- **Wallet**: Safe 1-of-2 for AI agents ("Safe secures $100B+", ERC-4337 "early")
- **MEV**: Flashbots Protect RPC for swap transactions (private mempool routing)
- **Architecture**: "Composition over creation" — lightweight protocol interaction, no heavy abstractions
- **Security**: Tight slippage bounds, limited approvals, oracle staleness checks

## Decisions

### 1. Split P1 into Sub-Phases

Current P1 is monolithic: all strategies, all adapters, all chains. Only Aave and Lido are wired end-to-end. Split into validation-gated sub-phases:

**P1a — Core Loop (Tier 1, Ethereum Sepolia)**
- Infrastructure: Redis, PostgreSQL, Docker, main loop
- Chain listeners: Ethereum only (Alchemy WS)
- Wallet: Safe 1-of-2 multisig
- Execution: TransactionBuilder + encode-only modules (Aave V3, Lido)
- Strategies: Aave lending optimization, Lido liquid staking
- AI: Claude decision engine, code-gen, insight synthesis
- Risk: All circuit breakers, exposure limits, oracle guards
- Allowlist: Safe guard contract (single mechanism)
- Monitoring, portfolio, harness, reporting: all current features
- **Gate**: End-to-end Aave supply/withdraw cycle on Sepolia.

**P1b — Tier 2 Expansion**
- Uniswap V3 encode module + concentrated liquidity strategy
- Yield farming strategy
- Flashbots Protect as thin RPC routing in TransactionBuilder
- **Gate**: Uniswap LP position managed end-to-end on Sepolia.

**P1c — Tier 3 + L2**
- L2 chain listeners (Arbitrum, Base)
- Flash loan, GMX, Aerodrome encode modules
- Flash loan arbitrage + rate arbitrage strategies
- L2 gas estimation
- **Gate**: Flash loan arb executes on Sepolia. L2 listeners receiving events.

P2 (stress testing), P3 (Solana), P4 (Railway deploy) unchanged.

### 2. Safe 1-of-2 Multisig (already implemented)

Replace all ERC-4337/Alchemy Smart Wallet references in the PRD:
- Owner 1: Agent EOA (automated operations)
- Owner 2: Human cold wallet (recovery/override)
- Threshold: 1 (agent operates autonomously)
- Safe guard contract handles allowlisting
- Safe spending caps handle transaction limits

### 3. MEV Protection (P1b)

Delete dead `flashbots-protect.ts` (315 lines, not imported anywhere). Reimplement in P1b as thin RPC routing layer in TransactionBuilder — not a standalone module. Aave supply/withdraw (P1a) are not MEV-sensitive; Flashbots matters when Uniswap swaps arrive.

### 4. Safe Guard Allowlist (replaces standalone module)

Delete `contract-allowlist.ts` (226 lines, instantiated but never wired). The Safe wallet's built-in guard validates target addresses. One mechanism, not two.

### 5. Encode-Only Protocol Modules

Replace heavy adapter classes (~600-720 lines each) with thin encode-only modules (~50-80 lines each). Each module contains: ABI definitions, encode functions, contract address config. No class, no constructor, no wallet references, no operational methods. TransactionBuilder handles all execution.

## Features.json Changes

### Features staying in P1a (all keep `passes: true`):
INFRA-001–007, LISTEN-001–002, EXEC-001, EXEC-004, EXEC-006, EXEC-010, DATA-001–004, AI-001–003, STRAT-001–002, STRAT-007–008, PORT-001–003, HARNESS-001–004, MON-001–004, RISK-001–005, RISK-007–008, REPORT-001–002, TEST-001, TEST-003

### Features with description updates (P1a, keep `passes: true`):
- **EXEC-002**: "Alchemy Smart Wallet" → "Safe 1-of-2 multisig wallet". Steps updated for Safe guard, spending caps, agent+human owners.
- **EXEC-004**: Step "All interactions go through the Smart Wallet" → "Safe wallet"
- **RISK-006**: "Smart contract allowlist — TS executor maintains strict list" → "Safe guard allowlist". Steps reflect Safe guard mechanism.

### Features moving to P1b (reset `passes: false`):
- **EXEC-003** (Flashbots Protect) — thin RPC routing in TransactionBuilder
- **EXEC-005** (Uniswap V3) — encode-only module
- **STRAT-003** (Concentrated liquidity)
- **STRAT-004** (Yield farming)

### Features moving to P1c (reset `passes: false`):
- **EXEC-007** (Flash loan executor) — encode-only module
- **EXEC-009** (L2 adapters: GMX, Aerodrome) — encode-only modules
- **LISTEN-003** (L2 chain listeners)
- **DATA-005** (L2 data pipeline)
- **STRAT-005** (Flash loan arbitrage)
- **STRAT-006** (Lending rate arbitrage)

These resets are honest: the code exists but is unreachable from the main execution path. Tests pass in isolation but features don't function end-to-end.

## Codebase Changes

### Files deleted (~3,800 lines):
- `src/execution/uniswap-v3-adapter.ts` (~685 lines)
- `src/execution/flash-loan-executor.ts` (~435 lines)
- `src/execution/gmx-adapter.ts` (~630 lines)
- `src/execution/aerodrome-adapter.ts` (~570 lines)
- `src/execution/flashbots-protect.ts` (~315 lines)
- `src/security/contract-allowlist.ts` (~226 lines)
- Corresponding test files for all above

### Files simplified:
- `src/execution/aave-v3-adapter.ts` (~380 → ~80 lines) — encode-only module
- `src/execution/lido-adapter.ts` (~560 → ~80 lines) — encode-only module
- `src/index.ts` — remove `buildAdapterMap()`, remove unused `validateOrder()`, simplify to Aave + Lido encode modules
- `src/redis/client.ts` — remove unused `streamRead()`, `streamTrim()`, `cacheSet()`, `cacheGet()`, `cacheDel()`
- `src/execution/transaction-builder.ts` — remove `ProtocolAdapter` interface, simplify adapter call site

### PRD edits (docs/prd.md):
1. Section 3 architecture diagram — update TS layer
2. Section 3 key decisions table — wallet, MEV, allowlist, protocol interaction
3. Section 4 project structure — remove `security/`, update descriptions
4. Section 5 tech stack — wallet and MEV rows
5. Section 6 exposure limits — Safe guard, Flashbots P1b+
6. Section 6 risk matrix — Safe spending caps
7. Section 8 event flow — remove Flashbots from P1a path
8. Section 10 phases — P1 split into P1a/P1b/P1c

### Estimated result:
- ~4,650 lines removed, ~800 lines simplified
- ts-executor: ~7,500 → ~2,800 lines (62% reduction)
- All P1a features remain green
- Same end-to-end functionality for Tier 1 strategies

### Untouched:
- Safe wallet (`src/wallet/safe-wallet.ts`)
- TransactionBuilder core execution path
- Chain listeners
- Redis pub/sub
- All Python code
