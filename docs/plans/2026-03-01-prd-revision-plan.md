# PRD Revision & TS-Executor Simplification — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Update the PRD to reflect Safe wallet, ethskills.com guidance, and P1 sub-phases; then simplify ts-executor by removing dead code and replacing heavy adapters with encode-only modules.

**Architecture:** The PRD gets editorial updates (wallet, MEV, phases, tech stack). The codebase gets surgical deletions (4 dead adapters, Flashbots, contract allowlist) and two adapter rewrites (Aave, Lido → thin encode modules). index.ts gets simplified to remove dead adapter wiring. Tests updated to match.

**Tech Stack:** TypeScript, viem, vitest. No new dependencies.

---

## Task 1: Update PRD — Wallet & Tech Stack References

**Files:**
- Modify: `docs/prd.md:116` (architecture diagram)
- Modify: `docs/prd.md:142` (key decisions table)
- Modify: `docs/prd.md:160` (project structure)
- Modify: `docs/prd.md:196-197` (tech stack table)

**Step 1: Update architecture diagram TS layer (line 116-120)**

Replace:
```
│  Smart Wallet ── Flashbots ── Protocol Adapters    │
│  (ERC-4337)      (MEV protection) (Aave, Uni)     │
│                                                    │
│  Contract Allowlist                                │
```

With:
```
│  Safe Wallet ── Protocol Encoders                  │
│  (1-of-2 multisig) (Aave V3, Lido)               │
```

**Step 2: Update key decisions table (lines 142, 144)**

Replace wallet row:
```
| Wallet         | Alchemy Smart Wallet (Account Abstraction) | On-chain spending limits, programmable guardrails                |
```
With:
```
| Wallet         | Safe 1-of-2 Multisig (Safe{Core} SDK)      | ethskills: battle-tested ($100B+ secured), agent EOA + human recovery |
```

Replace MEV row:
```
| MEV Protection | Flashbots Protect                          | Private mempool routing prevents frontrunning/sandwich attacks   |
```
With:
```
| MEV Protection | Flashbots Protect RPC (P1b)                | Private mempool routing for swaps; not needed for P1a supply/withdraw |
```

**Step 3: Update project structure (lines 157-164)**

Replace:
```
│   └── src/
│       ├── listeners/             # Alchemy WebSocket handlers
│       ├── execution/             # TX builder, Flashbots, protocol adapters
│       ├── wallet/                # Smart wallet (ERC-4337)
│       ├── security/              # Contract allowlist
│       ├── redis/                 # Redis client
│       ├── validation/            # Schema validation
│       └── index.ts
```
With:
```
│   └── src/
│       ├── listeners/             # Alchemy WebSocket handlers
│       ├── execution/             # TX builder, protocol encoders (Aave, Lido)
│       ├── wallet/                # Safe 1-of-2 multisig
│       ├── redis/                 # Redis client
│       ├── validation/            # Schema validation
│       └── index.ts
```

**Step 4: Update tech stack table (lines 196-197)**

Replace:
```
| Wallet                 | Alchemy Smart Wallet (Account Abstraction) |
| MEV Protection         | Flashbots Protect                          |
```
With:
```
| Wallet                 | Safe 1-of-2 Multisig (Safe{Core} SDK)      |
| MEV Protection         | Flashbots Protect RPC (P1b+)               |
```

**Step 5: Commit**

```bash
git add docs/prd.md
git commit -m "docs(icarus): update PRD wallet, MEV, tech stack for Safe + ethskills"
```

---

## Task 2: Update PRD — Risk, Event Flow, and Exposure Limits

**Files:**
- Modify: `docs/prd.md:219-220` (exposure limits)
- Modify: `docs/prd.md:229` (risk matrix — key compromise)
- Modify: `docs/prd.md:287` (event flow step 7)

**Step 1: Update exposure limits (lines 219-220)**

Replace:
```
- Smart contract allowlist enforced at TS executor level
- Flashbots Protect for all swap transactions
```
With:
```
- Safe guard allowlist enforced at wallet level
- Flashbots Protect for swap transactions (P1b+)
```

**Step 2: Update risk matrix key compromise row (line 229)**

Replace:
```
| Key compromise         | Critical | Smart wallet spending caps, hot/cold split           |
```
With:
```
| Key compromise         | Critical | Safe spending caps, 1-of-2 multisig (agent + human recovery) |
```

**Step 3: Update event flow step 7 (line 287)**

Replace:
```
7. **TS Executor** constructs TX via viem, routes through Flashbots Protect, submits
```
With:
```
7. **TS Executor** constructs TX via viem, submits via Safe wallet (Flashbots routing added in P1b for swaps)
```

**Step 4: Commit**

```bash
git add docs/prd.md
git commit -m "docs(icarus): update PRD risk, event flow, exposure limits for Safe"
```

---

## Task 3: Update PRD — Phase Split (P1a/P1b/P1c)

**Files:**
- Modify: `docs/prd.md:308-314` (phases section)

**Step 1: Replace P1 section with P1a/P1b/P1c**

Replace the entire P1 block (lines 310-314):
```
### P1 — Build Full System

All infrastructure, strategies, AI decision engine, risk management, monitoring, and L2 support for Ethereum + Arbitrum + Base. The bot is feature-complete and running on Sepolia testnet.

Includes: infrastructure (Redis, PostgreSQL, Docker, main loop), all chain listeners (Ethereum + L2), all protocol adapters (Aave, Uniswap V3, Lido, GMX, Aerodrome, flash loans), all 6 strategies, Claude AI engine (runtime reasoning + code-gen + insight synthesis), full risk management suite, portfolio rebalancing, Discord alerts, performance dashboard, anomaly detection, human-in-the-loop gates, tax/P&L reporting, and ML gas prediction.
```

With:
```
### P1a — Core Loop (Tier 1 Strategies, Ethereum Sepolia)

Infrastructure, Tier 1 strategies, AI decision engine, risk management, monitoring, and portfolio management on Ethereum Sepolia. The core decision-to-execution loop is validated end-to-end.

Includes: infrastructure (Redis, PostgreSQL, Docker, main loop), Ethereum chain listeners (Alchemy WS), Safe 1-of-2 multisig wallet, TransactionBuilder + encode-only protocol modules (Aave V3, Lido), Tier 1 strategies (Aave lending optimization, Lido liquid staking), Claude AI engine (runtime reasoning + code-gen + insight synthesis), full risk management suite (circuit breakers, exposure limits, Safe guard allowlist, oracle guards), portfolio management (allocator, position tracker, rebalancer), Discord alerts, performance dashboard, anomaly detection, human-in-the-loop gates, tax/P&L reporting, ML gas prediction, agent harness (state persistence, startup recovery, diagnostic mode), Sepolia integration tests.

**Gate:** End-to-end Aave supply/withdraw cycle executes on Sepolia through the full pipeline (market event → strategy evaluation → risk gate → order → TX → result).

### P1b — Tier 2 Expansion (Uniswap V3, Yield Farming)

Add Tier 2 strategies and MEV protection for swap-based operations.

Includes: Uniswap V3 encode module, concentrated liquidity strategy, yield farming strategy, Flashbots Protect RPC routing in TransactionBuilder for swap transactions.

**Gate:** Uniswap V3 LP position managed end-to-end on Sepolia.

### P1c — Tier 3 + L2 (Flash Loans, GMX, Aerodrome)

Add L2 chain support and Tier 3 high-risk strategies.

Includes: L2 chain listeners (Arbitrum, Base), flash loan encode module, GMX encode module, Aerodrome encode module, flash loan arbitrage strategy, lending rate arbitrage strategy, L2 gas estimation (L1 data posting costs).

**Gate:** Flash loan arbitrage executes on Sepolia. L2 listeners receiving and publishing events.
```

**Step 2: Commit**

```bash
git add docs/prd.md
git commit -m "docs(icarus): split P1 into P1a/P1b/P1c sub-phases with gates"
```

---

## Task 4: Update features.json — Phase Reassignment & Description Updates

**Files:**
- Modify: `harness/features.json`

**Step 1: Update EXEC-002 description (Safe wallet)**

Find the EXEC-002 object. Change:
- `"description"`: `"Safe 1-of-2 multisig wallet — battle-tested wallet with Safe guard allowlist, spending caps, and dual-owner recovery (agent EOA + human cold wallet, threshold=1)"`
- Update `"steps"` array to:
```json
[
  "Safe 1-of-2 deployed and configured on Sepolia testnet",
  "On-chain spending limits enforced (daily and per-transaction caps)",
  "Safe guard allowlist restricts interactions to approved protocol contracts only",
  "Wallet balance and nonce queryable on demand",
  "Agent EOA (owner 1) executes transactions autonomously; human cold wallet (owner 2) for recovery",
  "Private key stored securely via environment variable, never in code"
]
```

**Step 2: Update EXEC-004 step**

In EXEC-004's steps array, replace `"All interactions go through the Smart Wallet"` with `"All interactions go through the Safe wallet"`.

**Step 3: Move P1b features — set phase and reset passes**

For each of these features, set `"phase": "P1b"` and `"passes": false`:
- EXEC-003 (Flashbots Protect)
- EXEC-005 (Uniswap V3 adapter)
- STRAT-003 (Uniswap V3 concentrated liquidity)
- STRAT-004 (Yield farming)

Also update EXEC-003 description to: `"Flashbots Protect RPC routing — route swap and arbitrage transactions through private mempool via thin RPC routing layer in TransactionBuilder, not standalone module"`

Also update EXEC-005 description to: `"Uniswap V3 encode module — encode-only protocol module for minting/burning concentrated liquidity positions, collecting fees, querying pool state"`

**Step 4: Move P1c features — set phase and reset passes**

For each of these features, set `"phase": "P1c"` and `"passes": false`:
- EXEC-007 (Flash loan executor)
- EXEC-009 (L2 protocol adapters)
- LISTEN-003 (L2 chain listeners)
- DATA-005 (L2 data pipeline)
- STRAT-005 (Flash loan arbitrage)
- STRAT-006 (Lending rate arbitrage)

Also update EXEC-007 description to: `"Flash loan encode module — encode-only module for atomic flash loan arbitrage via Aave V3"`

Also update EXEC-009 description to: `"L2 encode modules (GMX, Aerodrome) — encode-only protocol modules for L2-specific DeFi on Arbitrum and Base"`

**Step 5: Update RISK-006 description (Safe guard allowlist)**

Change RISK-006:
- `"description"`: `"Safe guard allowlist — Safe guard contract validates target addresses for all transactions; single mechanism replacing standalone allowlist module"`
- Update steps to:
```json
[
  "Allowlist maintained as Safe guard contract configuration",
  "Safe wallet validates target contract address via guard before every transaction",
  "Non-allowlisted contracts rejected with error published to execution:results",
  "Allowlist updates require Safe owner transaction (intentional friction)",
  "Allowlist includes Aave V3, Lido, and their periphery contracts for P1a",
  "Separate allowlists per chain added in P1c (Arbitrum, Base)"
]
```

**Step 6: Commit**

```bash
git add harness/features.json
git commit -m "feat(icarus): update features.json — P1a/P1b/P1c split, Safe descriptions"
```

---

## Task 5: Delete Dead Adapter Files

**Files:**
- Delete: `ts-executor/src/execution/uniswap-v3-adapter.ts` (698 lines)
- Delete: `ts-executor/src/execution/flash-loan-executor.ts` (448 lines)
- Delete: `ts-executor/src/execution/gmx-adapter.ts` (640 lines)
- Delete: `ts-executor/src/execution/aerodrome-adapter.ts` (580 lines)
- Delete: `ts-executor/src/execution/flashbots-protect.ts` (315 lines)
- Delete: `ts-executor/src/security/contract-allowlist.ts` (226 lines)
- Delete: `ts-executor/tests/uniswap-v3-adapter.test.ts` (418 lines)
- Delete: `ts-executor/tests/flash-loan-executor.test.ts` (421 lines)
- Delete: `ts-executor/tests/gmx-adapter.test.ts` (398 lines)
- Delete: `ts-executor/tests/aerodrome-adapter.test.ts` (398 lines)
- Delete: `ts-executor/tests/flashbots-protect.test.ts` (296 lines)
- Delete: `ts-executor/tests/contract-allowlist.test.ts` (313 lines)

**Step 1: Delete all 12 files**

```bash
cd ts-executor
git rm src/execution/uniswap-v3-adapter.ts \
       src/execution/flash-loan-executor.ts \
       src/execution/gmx-adapter.ts \
       src/execution/aerodrome-adapter.ts \
       src/execution/flashbots-protect.ts \
       src/security/contract-allowlist.ts \
       tests/uniswap-v3-adapter.test.ts \
       tests/flash-loan-executor.test.ts \
       tests/gmx-adapter.test.ts \
       tests/aerodrome-adapter.test.ts \
       tests/flashbots-protect.test.ts \
       tests/contract-allowlist.test.ts
```

**Step 2: Remove the security/ directory if empty**

```bash
rmdir src/security/ 2>/dev/null || true
```

**Step 3: Run `pnpm tsc --noEmit` to see what breaks**

Expected: compilation errors in `src/index.ts` (imports for deleted modules). This is fixed in Task 7.

Do NOT commit yet — commit after Task 7 fixes the imports.

---

## Task 6: Rewrite Aave V3 and Lido as Encode-Only Modules

**Files:**
- Rewrite: `ts-executor/src/execution/aave-v3-adapter.ts` (402 → ~60 lines)
- Rewrite: `ts-executor/src/execution/lido-adapter.ts` (572 → ~70 lines)
- Rewrite: `ts-executor/tests/aave-v3-adapter.test.ts` (309 → ~80 lines)
- Rewrite: `ts-executor/tests/lido-adapter.test.ts` (381 → ~90 lines)

**Step 1: Rewrite aave-v3-adapter.ts as encode-only module**

Replace the entire file with:

```typescript
/**
 * EXEC-004: Aave V3 encode module.
 *
 * Lightweight protocol encoder for Aave V3 supply/withdraw operations.
 * ABI definitions + encode functions only. TransactionBuilder handles execution.
 */

import { type Address, type Hex, encodeFunctionData, parseAbi } from 'viem';

// ── ABIs ──────────────────────────────────────────

export const AAVE_POOL_ABI = parseAbi([
  'function supply(address asset, uint256 amount, address onBehalfOf, uint16 referralCode)',
  'function withdraw(address asset, uint256 amount, address to) returns (uint256)',
  'function getReserveData(address asset) view returns ((uint256 configuration, uint128 liquidityIndex, uint128 currentLiquidityRate, uint128 variableBorrowIndex, uint128 currentVariableBorrowRate, uint128 currentStableBorrowRate, uint40 lastUpdateTimestamp, uint16 id, address aTokenAddress, address stableDebtTokenAddress, address variableDebtTokenAddress, address interestRateStrategyAddress, uint128 accruedToTreasury, uint128 unbacked, uint128 isolationModeTotalDebt))',
]);

export const ERC20_ABI = parseAbi([
  'function approve(address spender, uint256 amount) returns (bool)',
  'function allowance(address owner, address spender) view returns (uint256)',
  'function balanceOf(address account) view returns (uint256)',
  'function decimals() view returns (uint8)',
]);

// ── Addresses ──────────────────────────────────────

/** Aave V3 Pool on Sepolia. */
export const AAVE_V3_POOL: Address =
  (process.env.AAVE_V3_POOL_ADDRESS as Address | undefined)
  ?? '0x6Ae43d3271ff6888e7Fc43Fd7321a503ff738951';

// ── Encoders ──────────────────────────────────────

export function encodeSupply(asset: Address, amount: bigint, onBehalfOf: Address): Hex {
  return encodeFunctionData({
    abi: AAVE_POOL_ABI,
    functionName: 'supply',
    args: [asset, amount, onBehalfOf, 0],
  });
}

export function encodeWithdraw(asset: Address, amount: bigint, to: Address): Hex {
  return encodeFunctionData({
    abi: AAVE_POOL_ABI,
    functionName: 'withdraw',
    args: [asset, amount, to],
  });
}
```

**Step 2: Rewrite lido-adapter.ts as encode-only module**

Replace the entire file with:

```typescript
/**
 * EXEC-006: Lido encode module.
 *
 * Lightweight protocol encoder for Lido staking operations.
 * ABI definitions + encode functions only. TransactionBuilder handles execution.
 */

import { type Address, type Hex, encodeFunctionData, parseAbi } from 'viem';

// ── ABIs ──────────────────────────────────────────

export const LIDO_STETH_ABI = parseAbi([
  'function submit(address _referral) payable returns (uint256)',
  'function balanceOf(address _account) view returns (uint256)',
  'function approve(address _spender, uint256 _amount) returns (bool)',
  'function allowance(address _owner, address _spender) view returns (uint256)',
]);

export const WSTETH_ABI = parseAbi([
  'function wrap(uint256 _stETHAmount) returns (uint256)',
  'function unwrap(uint256 _wstETHAmount) returns (uint256)',
  'function balanceOf(address _account) view returns (uint256)',
  'function getWstETHByStETH(uint256 _stETHAmount) view returns (uint256)',
  'function getStETHByWstETH(uint256 _wstETHAmount) view returns (uint256)',
]);

// ── Addresses ──────────────────────────────────────

/** Lido stETH on Sepolia. */
export const STETH_ADDRESS: Address =
  (process.env.LIDO_STETH_ADDRESS as Address | undefined)
  ?? '0x3e3FE7dBc6B4C189E7128855dD526361c49b40Af';

/** Wrapped stETH on Sepolia. */
export const WSTETH_ADDRESS: Address =
  (process.env.LIDO_WSTETH_ADDRESS as Address | undefined)
  ?? '0xB82381A3fBD3FaFA77B3a7bE693342618240067b';

// ── Encoders ──────────────────────────────────────

export function encodeStake(referral?: Address): Hex {
  return encodeFunctionData({
    abi: LIDO_STETH_ABI,
    functionName: 'submit',
    args: [referral ?? '0x0000000000000000000000000000000000000000' as Address],
  });
}

export function encodeWrap(stethAmount: bigint): Hex {
  return encodeFunctionData({
    abi: WSTETH_ABI,
    functionName: 'wrap',
    args: [stethAmount],
  });
}

export function encodeUnwrap(wstethAmount: bigint): Hex {
  return encodeFunctionData({
    abi: WSTETH_ABI,
    functionName: 'unwrap',
    args: [wstethAmount],
  });
}
```

**Step 3: Rewrite aave-v3-adapter.test.ts**

Replace the entire file with:

```typescript
import { describe, it, expect } from 'vitest';
import {
  encodeSupply,
  encodeWithdraw,
  AAVE_V3_POOL,
  AAVE_POOL_ABI,
  ERC20_ABI,
} from '../src/execution/aave-v3-adapter.js';
import type { Address } from 'viem';

const MOCK_ASSET: Address = '0x0000000000000000000000000000000000000001';
const MOCK_RECIPIENT: Address = '0x0000000000000000000000000000000000000002';

describe('Aave V3 encode module', () => {
  it('exports pool address', () => {
    expect(AAVE_V3_POOL).toMatch(/^0x[0-9a-fA-F]{40}$/);
  });

  it('exports ABIs', () => {
    expect(AAVE_POOL_ABI).toBeDefined();
    expect(ERC20_ABI).toBeDefined();
  });

  it('encodes supply call data', () => {
    const data = encodeSupply(MOCK_ASSET, 1000000n, MOCK_RECIPIENT);
    expect(data).toMatch(/^0x/);
    expect(typeof data).toBe('string');
    expect(data.length).toBeGreaterThan(10);
  });

  it('encodes withdraw call data', () => {
    const data = encodeWithdraw(MOCK_ASSET, 1000000n, MOCK_RECIPIENT);
    expect(data).toMatch(/^0x/);
    expect(typeof data).toBe('string');
    expect(data.length).toBeGreaterThan(10);
  });

  it('produces different call data for supply vs withdraw', () => {
    const supplyData = encodeSupply(MOCK_ASSET, 1000000n, MOCK_RECIPIENT);
    const withdrawData = encodeWithdraw(MOCK_ASSET, 1000000n, MOCK_RECIPIENT);
    expect(supplyData).not.toBe(withdrawData);
  });
});
```

**Step 4: Rewrite lido-adapter.test.ts**

Replace the entire file with:

```typescript
import { describe, it, expect } from 'vitest';
import {
  encodeStake,
  encodeWrap,
  encodeUnwrap,
  STETH_ADDRESS,
  WSTETH_ADDRESS,
  LIDO_STETH_ABI,
  WSTETH_ABI,
} from '../src/execution/lido-adapter.js';
import type { Address } from 'viem';

describe('Lido encode module', () => {
  it('exports contract addresses', () => {
    expect(STETH_ADDRESS).toMatch(/^0x[0-9a-fA-F]{40}$/);
    expect(WSTETH_ADDRESS).toMatch(/^0x[0-9a-fA-F]{40}$/);
  });

  it('exports ABIs', () => {
    expect(LIDO_STETH_ABI).toBeDefined();
    expect(WSTETH_ABI).toBeDefined();
  });

  it('encodes stake call data', () => {
    const data = encodeStake();
    expect(data).toMatch(/^0x/);
    expect(data.length).toBeGreaterThan(10);
  });

  it('encodes stake with custom referral', () => {
    const referral: Address = '0x0000000000000000000000000000000000000042';
    const data = encodeStake(referral);
    expect(data).toMatch(/^0x/);
  });

  it('encodes wrap call data', () => {
    const data = encodeWrap(1000000000000000000n);
    expect(data).toMatch(/^0x/);
    expect(data.length).toBeGreaterThan(10);
  });

  it('encodes unwrap call data', () => {
    const data = encodeUnwrap(500000000000000000n);
    expect(data).toMatch(/^0x/);
    expect(data.length).toBeGreaterThan(10);
  });

  it('produces different call data for wrap vs unwrap', () => {
    const wrapData = encodeWrap(1000000000000000000n);
    const unwrapData = encodeUnwrap(1000000000000000000n);
    expect(wrapData).not.toBe(unwrapData);
  });
});
```

Do NOT commit yet — commit together with Task 7.

---

## Task 7: Simplify index.ts

**Files:**
- Modify: `ts-executor/src/index.ts`

**Step 1: Rewrite index.ts**

Replace the entire file with:

```typescript
import 'dotenv/config';

import type { Address } from 'viem';
import { RedisManager } from './redis/client.js';
import { AlchemyWebSocketManager } from './listeners/websocket-manager.js';
import { MarketEventPublisher } from './listeners/market-event-publisher.js';
import { L2ListenerManager } from './listeners/l2-listener.js';
import { TransactionBuilder, type ExecutionOrder, type ProtocolAdapter } from './execution/transaction-builder.js';
import { EventReporter } from './execution/event-reporter.js';
import { SafeWalletManager } from './wallet/safe-wallet.js';
import * as aave from './execution/aave-v3-adapter.js';
import * as lido from './execution/lido-adapter.js';

const SERVICE_NAME = 'ts-executor';

/** Structured JSON logger. */
function log(event: string, message: string, extra?: Record<string, unknown>): void {
  console.log(JSON.stringify({
    timestamp: new Date().toISOString(),
    service: SERVICE_NAME,
    event,
    message,
    ...extra,
  }));
}

/**
 * Build protocol adapter map from encode-only modules.
 * Maps protocol names to ProtocolAdapter interface for TransactionBuilder.
 */
function buildAdapterMap(): Map<string, ProtocolAdapter> {
  const map = new Map<string, ProtocolAdapter>();

  map.set('aave_v3', {
    async buildTransaction(action, params) {
      const asset = params.tokenIn as Address;
      const amount = BigInt(params.amount);
      const recipient = (params.recipient ?? params.tokenIn) as Address;
      switch (action) {
        case 'supply':
          return { to: aave.AAVE_V3_POOL, data: aave.encodeSupply(asset, amount, recipient) };
        case 'withdraw':
          return { to: aave.AAVE_V3_POOL, data: aave.encodeWithdraw(asset, amount, recipient) };
        default:
          throw new Error(`Unsupported aave_v3 action: ${action}`);
      }
    },
  });

  map.set('lido', {
    async buildTransaction(action, params) {
      const amount = BigInt(params.amount);
      switch (action) {
        case 'stake':
          return { to: lido.STETH_ADDRESS, data: lido.encodeStake(), value: amount };
        case 'wrap':
          return { to: lido.WSTETH_ADDRESS, data: lido.encodeWrap(amount) };
        case 'unwrap':
          return { to: lido.WSTETH_ADDRESS, data: lido.encodeUnwrap(amount) };
        default:
          throw new Error(`Unsupported lido action: ${action}`);
      }
    },
  });

  return map;
}

/** Initialize all service components and return them. */
async function initializeComponents(): Promise<{
  redis: RedisManager;
  wsManager: AlchemyWebSocketManager;
  publisher: MarketEventPublisher;
  l2Manager: L2ListenerManager;
  txBuilder: TransactionBuilder;
  reporter: EventReporter;
  safeWallet: SafeWalletManager;
}> {
  const redis = new RedisManager();
  const reporter = new EventReporter();
  const publisher = new MarketEventPublisher({ onLog: log });

  const wsManager = new AlchemyWebSocketManager({
    onEvent: (event) => void publisher.handleEvent(event),
    onLog: log,
  });

  const l2Manager = new L2ListenerManager({
    onEvent: (event) => void publisher.handleEvent(event),
    onLog: log,
  });

  const safeWallet = await SafeWalletManager.create({
    onLog: log,
  });

  const adapterMap = buildAdapterMap();

  const txBuilder = new TransactionBuilder({
    safeWallet,
    adapters: adapterMap,
    reporter,
    onLog: log,
  });

  return {
    redis, wsManager, publisher, l2Manager, txBuilder,
    reporter, safeWallet,
  };
}

/** Bootstrap and run the TypeScript executor service. */
async function main(): Promise<void> {
  log('startup', 'TypeScript executor starting...');

  const {
    redis, wsManager, publisher, l2Manager, txBuilder,
    reporter,
  } = await initializeComponents();

  // Connect Redis and attach services
  await redis.connect();
  reporter.attach(redis);
  publisher.attach(redis);
  log('redis_connected', 'Redis connection established');

  // Start WebSocket manager for mainnet/Sepolia events
  await wsManager.connect();
  log('ws_connected', 'WebSocket manager connected');

  // Start L2 chain listeners (Arbitrum, Base)
  await l2Manager.connectAll();
  log('l2_started', 'L2 listeners connected');

  // Start transaction builder — subscribes to execution:orders,
  // handles nonce management, retries, and publishes results
  await txBuilder.start(redis);
  log('tx_builder_started', 'Transaction builder listening for orders');

  log('ready', 'TypeScript executor ready — all modules initialized');

  // Keep process alive until shutdown signal
  await new Promise<void>((resolve) => {
    const shutdown = async () => {
      log('shutdown', 'TypeScript executor shutting down...');

      try {
        await wsManager.disconnect();
        await l2Manager.disconnectAll();
        await redis.disconnect();
      } catch (err) {
        log('shutdown_error', `Error during shutdown: ${err instanceof Error ? err.message : String(err)}`);
      }

      log('stopped', 'TypeScript executor stopped');
      resolve();
    };

    process.on('SIGTERM', () => void shutdown());
    process.on('SIGINT', () => void shutdown());
  });
}

export { main, initializeComponents, buildAdapterMap, log };

if (!process.env.VITEST) {
  main().catch((err) => {
    log('fatal_error', err instanceof Error ? err.message : String(err));
    process.exit(1);
  });
}
```

Key changes:
- Removed imports: UniswapV3Adapter, FlashLoanExecutor, AerodromeAdapter, GmxAdapter, ContractAllowlist
- Removed: `ProtocolAdapters` interface, `validateOrder` function
- `buildAdapterMap()` now takes no arguments — uses imported encode modules directly
- `initializeComponents()` no longer returns `allowlist` or `adapters` (raw adapters)
- Removed dead adapter instantiation (4 adapters that threw errors)

**Step 2: Run `pnpm tsc --noEmit`**

Expected: passes cleanly (all deleted imports resolved).

---

## Task 8: Update index.test.ts

**Files:**
- Modify: `ts-executor/tests/index.test.ts`

**Step 1: Rewrite index.test.ts**

Replace the entire file with:

```typescript
import { describe, it, expect, vi, beforeAll, afterAll } from 'vitest';
import { CHANNELS } from '../src/redis/client.js';

const mockSafeWallet = {
  address: '0x' + '0'.repeat(40) as `0x${string}`,
  signerAddress: '0x' + 'a'.repeat(40) as `0x${string}`,
  validateOrder: vi.fn().mockReturnValue({ allowed: true }),
  recordSpend: vi.fn(),
  executeTransaction: vi.fn(),
  executeBatch: vi.fn(),
};

vi.mock('../src/wallet/safe-wallet.js', () => ({
  SafeWalletManager: {
    create: vi.fn().mockResolvedValue(mockSafeWallet),
  },
}));

beforeAll(() => {
  vi.stubEnv('WALLET_PRIVATE_KEY', '0x' + 'a'.repeat(64));
});

afterAll(() => {
  vi.unstubAllEnvs();
});

describe('ts-executor', () => {
  it('defines all required Redis channels', () => {
    expect(CHANNELS.MARKET_EVENTS).toBe('market:events');
    expect(CHANNELS.EXECUTION_ORDERS).toBe('execution:orders');
    expect(CHANNELS.EXECUTION_RESULTS).toBe('execution:results');
  });
});

describe('initializeComponents', () => {
  it('creates all required service components', async () => {
    const { initializeComponents } = await import('../src/index.js');
    const components = await initializeComponents();

    expect(components.redis).toBeDefined();
    expect(components.wsManager).toBeDefined();
    expect(components.publisher).toBeDefined();
    expect(components.l2Manager).toBeDefined();
    expect(components.txBuilder).toBeDefined();
    expect(components.reporter).toBeDefined();
    expect(components.safeWallet).toBeDefined();
  });

  it('passes safeWallet and reporter to txBuilder', async () => {
    const { initializeComponents } = await import('../src/index.js');
    const components = await initializeComponents();

    expect(components.txBuilder).toBeDefined();
    expect(components.txBuilder.processing).toBe(false);
    expect(components.safeWallet).toBe(mockSafeWallet);
    expect(components.reporter).toBeDefined();
  });
});

describe('buildAdapterMap', () => {
  it('creates entries for P1a protocols', async () => {
    const { buildAdapterMap } = await import('../src/index.js');
    const map = buildAdapterMap();

    expect(map.size).toBe(2);
    expect(map.has('aave_v3')).toBe(true);
    expect(map.has('lido')).toBe(true);
  });

  it('aave_v3 wrapper encodes supply transaction', async () => {
    const { buildAdapterMap } = await import('../src/index.js');
    const map = buildAdapterMap();
    const adapter = map.get('aave_v3')!;

    const result = await adapter.buildTransaction(
      'supply',
      { tokenIn: '0x0000000000000000000000000000000000000001', amount: '1000000' },
      { maxGasWei: '500000000000000', maxSlippageBps: 50, deadlineUnix: Math.floor(Date.now() / 1000) + 300 },
    );

    expect(result.to).toMatch(/^0x[0-9a-fA-F]{40}$/);
    expect(result.data).toBeDefined();
    expect(result.data!.startsWith('0x')).toBe(true);
  });

  it('aave_v3 wrapper encodes withdraw transaction', async () => {
    const { buildAdapterMap } = await import('../src/index.js');
    const map = buildAdapterMap();
    const adapter = map.get('aave_v3')!;

    const result = await adapter.buildTransaction(
      'withdraw',
      { tokenIn: '0x0000000000000000000000000000000000000001', amount: '1000000', recipient: '0x0000000000000000000000000000000000000002' },
      { maxGasWei: '500000000000000', maxSlippageBps: 50, deadlineUnix: Math.floor(Date.now() / 1000) + 300 },
    );

    expect(result.to).toMatch(/^0x[0-9a-fA-F]{40}$/);
    expect(result.data).toBeDefined();
  });

  it('aave_v3 wrapper throws on unsupported action', async () => {
    const { buildAdapterMap } = await import('../src/index.js');
    const map = buildAdapterMap();
    const adapter = map.get('aave_v3')!;

    await expect(adapter.buildTransaction(
      'borrow',
      { tokenIn: '0x0000000000000000000000000000000000000001', amount: '1000000' },
      { maxGasWei: '500000000000000', maxSlippageBps: 50, deadlineUnix: Math.floor(Date.now() / 1000) + 300 },
    )).rejects.toThrow('Unsupported aave_v3 action: borrow');
  });

  it('lido wrapper encodes stake with ETH value', async () => {
    const { buildAdapterMap } = await import('../src/index.js');
    const map = buildAdapterMap();
    const adapter = map.get('lido')!;

    const result = await adapter.buildTransaction(
      'stake',
      { tokenIn: '0x0000000000000000000000000000000000000000', amount: '1000000000000000000' },
      { maxGasWei: '500000000000000', maxSlippageBps: 50, deadlineUnix: Math.floor(Date.now() / 1000) + 300 },
    );

    expect(result.to).toMatch(/^0x[0-9a-fA-F]{40}$/);
    expect(result.data).toBeDefined();
    expect(result.value).toBe(1000000000000000000n);
  });

  it('lido wrapper encodes wrap and unwrap', async () => {
    const { buildAdapterMap } = await import('../src/index.js');
    const map = buildAdapterMap();
    const adapter = map.get('lido')!;

    const wrap = await adapter.buildTransaction(
      'wrap',
      { tokenIn: '0x0000000000000000000000000000000000000000', amount: '500000000000000000' },
      { maxGasWei: '500000000000000', maxSlippageBps: 50, deadlineUnix: Math.floor(Date.now() / 1000) + 300 },
    );
    expect(wrap.data).toBeDefined();
    expect(wrap.value).toBeUndefined();

    const unwrap = await adapter.buildTransaction(
      'unwrap',
      { tokenIn: '0x0000000000000000000000000000000000000000', amount: '500000000000000000' },
      { maxGasWei: '500000000000000', maxSlippageBps: 50, deadlineUnix: Math.floor(Date.now() / 1000) + 300 },
    );
    expect(unwrap.data).toBeDefined();
  });

  it('lido wrapper throws on unsupported action', async () => {
    const { buildAdapterMap } = await import('../src/index.js');
    const map = buildAdapterMap();
    const adapter = map.get('lido')!;

    await expect(adapter.buildTransaction(
      'borrow',
      { tokenIn: '0x0000000000000000000000000000000000000000', amount: '1000' },
      { maxGasWei: '500000000000000', maxSlippageBps: 50, deadlineUnix: Math.floor(Date.now() / 1000) + 300 },
    )).rejects.toThrow('Unsupported lido action: borrow');
  });
});

describe('log', () => {
  it('produces structured JSON output', async () => {
    const { log: logFn } = await import('../src/index.js');
    const consoleSpy = vi.spyOn(console, 'log').mockImplementation(() => {});

    logFn('test_event', 'Test message', { extra: 'data' });

    expect(consoleSpy).toHaveBeenCalledOnce();
    const output = JSON.parse(consoleSpy.mock.calls[0][0] as string);
    expect(output.event).toBe('test_event');
    expect(output.message).toBe('Test message');
    expect(output.service).toBe('ts-executor');
    expect(output.timestamp).toBeDefined();
    expect(output.extra).toBe('data');

    consoleSpy.mockRestore();
  });
});
```

Key changes:
- Removed test for 6-protocol adapter map (now 2)
- Removed "complex protocol adapters throw" test (those adapters are gone)
- Removed `validateOrder` tests (function removed)
- `buildAdapterMap()` called with no args
- `initializeComponents` no longer checks for `allowlist` or raw `adapters`

---

## Task 9: Remove Unused Redis Methods

**Files:**
- Modify: `ts-executor/src/redis/client.ts:179-232`

**Step 1: Delete streamRead, streamTrim, cacheSet, cacheGet, cacheDel**

Remove lines 179-232 (the `// ── Streams ──` and `// ── Cache ──` sections). Keep everything before line 179 and everything after line 232.

Also remove the `StreamEntry` interface (lines 26-29) and its export.

**Step 2: Run `pnpm tsc --noEmit`**

Expected: passes cleanly. No code imports these methods.

---

## Task 10: Commit All Code Changes, Run Full Test Suite

**Step 1: Commit the code deletion + rewrites (Tasks 5-9)**

```bash
cd ts-executor
git add -A
git commit -m "refactor(icarus): simplify ts-executor — encode-only modules, delete dead code

- Delete 4 dead adapters (Uniswap, FlashLoan, GMX, Aerodrome)
- Delete flashbots-protect.ts (dead, not imported)
- Delete contract-allowlist.ts (replaced by Safe guard)
- Rewrite Aave V3 + Lido as encode-only modules
- Simplify index.ts: remove dead wiring, validateOrder
- Remove unused Redis stream/cache methods
- ~4,600 lines removed, 62% reduction"
```

**Step 2: Run `pnpm tsc --noEmit`**

Expected: passes cleanly.

**Step 3: Run `pnpm test`**

Expected: all remaining tests pass. Should be ~210 tests (was ~306, minus ~96 from deleted test files).

**Step 4: Run `bash harness/verify.sh`**

Expected: all P1a tests pass, Python tests unaffected.

---

## Task 11: Update progress.txt

**Files:**
- Modify: `harness/progress.txt`

**Step 1: Append session entry**

```
--- Session 2026-03-01: PRD Revision & TS-Executor Simplification ---
Lead: Claude Opus 4.6

Changes:
1. PRD updated: Safe 1-of-2 replaces ERC-4337, P1 split into P1a/P1b/P1c,
   tech stack updated, risk/exposure limits updated for Safe guard allowlist
2. features.json: 10 features moved (4 to P1b, 6 to P1c) with passes reset to false
   EXEC-002, EXEC-004, RISK-006 descriptions updated for Safe wallet
3. ts-executor simplified:
   - Deleted: uniswap-v3-adapter, flash-loan-executor, gmx-adapter, aerodrome-adapter,
     flashbots-protect, contract-allowlist (+ all test files)
   - Rewrote: aave-v3-adapter, lido-adapter as encode-only modules (~60-70 lines each)
   - Simplified: index.ts (removed dead wiring), redis/client.ts (removed unused methods)
   - ~4,600 lines removed (62% reduction)

verify.sh: PASS
Next: P1a features are complete. P1b work begins with Uniswap V3 encode module + Flashbots RPC routing.
```

**Step 2: Commit**

```bash
git add harness/progress.txt
git commit -m "docs(icarus): update progress.txt for PRD revision session"
```

---

Plan complete and saved to `docs/plans/2026-03-01-prd-revision-plan.md`. Two execution options:

**1. Subagent-Driven (this session)** — I dispatch fresh subagent per task, review between tasks, fast iteration

**2. Parallel Session (separate)** — Open new session with executing-plans, batch execution with checkpoints

Which approach?