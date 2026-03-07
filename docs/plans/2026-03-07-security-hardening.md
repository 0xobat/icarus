# Security Hardening Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix 4 security issues identified in the security review — slippage protection, Redis auth, spend persistence, and token-aware spending limits.

**Architecture:** All changes are in `ts-executor/` and `docker-compose.yml`. No Python changes. Each task is independent and can be committed separately. TDD throughout.

**Tech Stack:** TypeScript, Vitest, ioredis, viem, docker-compose

**Design doc:** `docs/plans/2026-03-07-security-hardening-design.md`

---

### Task 1: Reject slippage-sensitive operations missing min amounts

**Files:**
- Modify: `ts-executor/src/index.ts:73-133` (aerodrome adapter switch cases)
- Test: `ts-executor/tests/index.test.ts`

**Step 1: Write failing tests**

Add these tests to the `buildAdapterMap` describe block in `ts-executor/tests/index.test.ts`:

```typescript
it('aerodrome swap throws without amountOutMin', async () => {
  const { buildAdapterMap } = await import('../src/index.js');
  const map = buildAdapterMap();
  const adapter = map.get('aerodrome')!;

  await expect(adapter.buildTransaction(
    'swap',
    {
      tokenIn: '0x4200000000000000000000000000000000000006',
      tokenOut: '0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913',
      amount: '1000000000000000000',
      stable: 'false',
      recipient: '0x0000000000000000000000000000000000000002',
    },
    { maxGasWei: '500000000000000', maxSlippageBps: 50, deadlineUnix: Math.floor(Date.now() / 1000) + 300 },
  )).rejects.toThrow('amountOutMin');
});

it('aerodrome mint_lp throws without amountAMin and amountBMin', async () => {
  const { buildAdapterMap } = await import('../src/index.js');
  const map = buildAdapterMap();
  const adapter = map.get('aerodrome')!;

  await expect(adapter.buildTransaction(
    'mint_lp',
    {
      tokenIn: '0x4200000000000000000000000000000000000006',
      tokenOut: '0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913',
      amount: '1000000000000000000',
      amountB: '2500000000',
      stable: 'false',
      recipient: '0x0000000000000000000000000000000000000002',
    },
    { maxGasWei: '500000000000000', maxSlippageBps: 50, deadlineUnix: Math.floor(Date.now() / 1000) + 300 },
  )).rejects.toThrow('amountAMin');
});

it('aerodrome burn_lp throws without amountAMin and amountBMin', async () => {
  const { buildAdapterMap } = await import('../src/index.js');
  const map = buildAdapterMap();
  const adapter = map.get('aerodrome')!;

  await expect(adapter.buildTransaction(
    'burn_lp',
    {
      tokenIn: '0x4200000000000000000000000000000000000006',
      tokenOut: '0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913',
      amount: '1000000000000000000',
      stable: 'false',
      recipient: '0x0000000000000000000000000000000000000002',
    },
    { maxGasWei: '500000000000000', maxSlippageBps: 50, deadlineUnix: Math.floor(Date.now() / 1000) + 300 },
  )).rejects.toThrow('amountAMin');
});
```

**Step 2: Run tests to verify they fail**

Run: `cd ts-executor && pnpm test -- --run tests/index.test.ts`
Expected: 3 new tests FAIL (no throw, operations silently default to 0)

**Step 3: Implement — add guards in aerodrome adapter switch cases**

In `ts-executor/src/index.ts`, modify the aerodrome `buildTransaction` function:

For `swap` (around line 118), add before the return:
```typescript
case "swap":
  if (!p.amountOutMin) {
    throw new Error("amountOutMin is required for swap (slippage protection)");
  }
  return { ... };
```

For `mint_lp` (around line 74), add before the return:
```typescript
case "mint_lp":
  if (!p.amountAMin || !p.amountBMin) {
    throw new Error("amountAMin and amountBMin are required for mint_lp (slippage protection)");
  }
  return { ... };
```

For `burn_lp` (around line 89), add before the return:
```typescript
case "burn_lp":
  if (!p.amountAMin || !p.amountBMin) {
    throw new Error("amountAMin and amountBMin are required for burn_lp (slippage protection)");
  }
  return { ... };
```

Also remove the `?? "0"` fallbacks since the guards now ensure values are present:
- Line 83: `BigInt(p.amountAMin ?? "0")` → `BigInt(p.amountAMin!)`
- Line 84: `BigInt(p.amountBMin ?? "0")` → `BigInt(p.amountBMin!)`
- Line 97: `BigInt(p.amountAMin ?? "0")` → `BigInt(p.amountAMin!)`
- Line 98: `BigInt(p.amountBMin ?? "0")` → `BigInt(p.amountBMin!)`
- Line 123: `BigInt(p.amountOutMin ?? "0")` → `BigInt(p.amountOutMin!)`

**Step 4: Update existing tests that omit min amounts**

The existing `aerodrome wrapper encodes mint_lp` test (line 113) needs `amountAMin` and `amountBMin` added to params:
```typescript
amountAMin: '900000000000000000',
amountBMin: '2250000000',
```

**Step 5: Run tests to verify all pass**

Run: `cd ts-executor && pnpm test -- --run tests/index.test.ts`
Expected: ALL tests PASS

**Step 6: Commit**

```bash
git add ts-executor/src/index.ts ts-executor/tests/index.test.ts
git commit -m "fix(icarus): reject slippage-sensitive ops missing min amounts"
```

---

### Task 2: Add Redis authentication

**Files:**
- Modify: `docker-compose.yml:8` (Redis command)
- Modify: `docker-compose.yml:10` (healthcheck)
- Modify: `docker-compose.yml:41,59` (REDIS_URL for both services)
- Modify: `.env.example:25-26` (add REDIS_PASSWORD)

**Step 1: Modify docker-compose.yml**

Update the redis service command (line 8):
```yaml
command: redis-server --appendonly yes --maxmemory 256mb --maxmemory-policy allkeys-lru --requirepass ${REDIS_PASSWORD:-changeme}
```

Update the redis healthcheck (line 10):
```yaml
test: ["CMD", "redis-cli", "-a", "${REDIS_PASSWORD:-changeme}", "ping"]
```

Update the `REDIS_URL` in ts-executor environment (line 41):
```yaml
- REDIS_URL=redis://:${REDIS_PASSWORD:-changeme}@redis:6379
```

Update the `REDIS_URL` in py-engine environment (line 59):
```yaml
- REDIS_URL=redis://:${REDIS_PASSWORD:-changeme}@redis:6379
```

**Step 2: Update .env.example**

Add `REDIS_PASSWORD=` under the Redis section:
```
# ── Redis ────────────────────────────────────────────
REDIS_PASSWORD=
REDIS_URL=redis://localhost:6379
```

**Step 3: Verify docker-compose parses correctly**

Run: `docker compose config 2>&1 | head -30`
Expected: No errors, redis command includes `--requirepass`

**Step 4: Commit**

```bash
git add docker-compose.yml .env.example
git commit -m "fix(icarus): add Redis authentication via REDIS_PASSWORD"
```

---

### Task 3: Persist daily spend to Redis

**Files:**
- Modify: `ts-executor/src/wallet/safe-wallet.ts`
- Test: `ts-executor/tests/safe-wallet.test.ts`

**Step 1: Write failing tests**

Add a new describe block to `ts-executor/tests/safe-wallet.test.ts`:

```typescript
describe('spend persistence', () => {
  it('persists daily spend to redis on recordSpend', async () => {
    const mockRedis = {
      set: vi.fn().mockResolvedValue('OK'),
      get: vi.fn().mockResolvedValue(null),
    };

    const wallet = await createWallet({ redis: mockRedis as any });
    wallet.recordSpend(parseEther('1'));

    expect(mockRedis.set).toHaveBeenCalledWith(
      expect.stringMatching(/^safe:daily_spend:\d{4}-\d{2}-\d{2}$/),
      parseEther('1').toString(),
      'EX',
      172800, // 48h
    );
  });

  it('loads persisted spend on first call', async () => {
    const mockRedis = {
      set: vi.fn().mockResolvedValue('OK'),
      get: vi.fn().mockResolvedValue(parseEther('1.5').toString()),
    };

    const wallet = await createWallet({
      redis: mockRedis as any,
      perTxCapWei: parseEther('5'),
      dailyCapWei: parseEther('2'),
    });

    // Should load 1.5 ETH from Redis, then 1.5 + 1 = 2.5 > 2 daily cap
    const result = wallet.validateOrder(TEST_CONTRACT, parseEther('1'));
    expect(result.allowed).toBe(false);
    expect(result.reason).toContain('Daily spend');
  });

  it('works without redis (in-memory only)', async () => {
    const wallet = await createWallet({
      perTxCapWei: parseEther('5'),
      dailyCapWei: parseEther('10'),
    });

    wallet.recordSpend(parseEther('1'));
    const result = wallet.validateOrder(TEST_CONTRACT, parseEther('1'));
    expect(result.allowed).toBe(true);
  });
});
```

**Step 2: Run tests to verify they fail**

Run: `cd ts-executor && pnpm test -- --run tests/safe-wallet.test.ts`
Expected: FAIL — `SafeWalletOptions` doesn't accept `redis` yet

**Step 3: Implement spend persistence**

In `ts-executor/src/wallet/safe-wallet.ts`:

1. Add `redis` to `SafeWalletOptions`:
```typescript
export interface SafeWalletOptions {
  // ... existing fields ...
  /** Redis client for spend persistence. Optional — falls back to in-memory only. */
  redis?: { get(key: string): Promise<string | null>; set(key: string, value: string, ...args: unknown[]): Promise<unknown> };
}
```

2. Add a private field to `SafeWalletManager`:
```typescript
private readonly redis: { get(key: string): Promise<string | null>; set(key: string, value: string, ...args: unknown[]): Promise<unknown> } | null;
```

3. Update the private constructor to accept `redis`:
```typescript
private constructor(
  account: PrivateKeyAccount,
  safeAddress: Address,
  protocolKit: Safe,
  rpcUrl: string,
  chain: Chain,
  limits: { perTxCapWei: bigint; dailyCapWei: bigint },
  allowlist: Set<Address>,
  onLog: (...) => void,
  redis: SafeWalletOptions['redis'] | null,
) {
  // ... existing assignments ...
  this.redis = redis ?? null;
}
```

4. Update `create()` to pass `opts.redis ?? null` to the constructor.

5. Update `recordSpend()` to persist:
```typescript
recordSpend(amountWei: bigint): void {
  this.resetDailyIfNeeded();
  this.dailySpent += amountWei;
  this.log('safe_spend_recorded', 'Spend recorded', {
    amount: formatEther(amountWei),
    dailyTotal: formatEther(this.dailySpent),
    dailyCap: formatEther(this.limits.dailyCapWei),
  });
  this.persistSpend();
}
```

6. Add `persistSpend()` and `loadSpend()` private methods:
```typescript
private persistSpend(): void {
  if (!this.redis) return;
  const key = `safe:daily_spend:${this.currentDay}`;
  this.redis.set(key, this.dailySpent.toString(), 'EX', 172800).catch(() => {
    this.log('safe_persist_error', 'Failed to persist daily spend to Redis');
  });
}

private loadSpend(): void {
  if (!this.redis) return;
  const key = `safe:daily_spend:${this.currentDay}`;
  this.redis.get(key).then((val) => {
    if (val !== null) {
      this.dailySpent = BigInt(val);
      this.log('safe_spend_loaded', 'Loaded daily spend from Redis', {
        day: this.currentDay,
        amount: formatEther(this.dailySpent),
      });
    }
  }).catch(() => {
    this.log('safe_load_error', 'Failed to load daily spend from Redis');
  });
}
```

7. Update `resetDailyIfNeeded()` to call `loadSpend()`:
```typescript
private resetDailyIfNeeded(): void {
  const today = new Date().toISOString().slice(0, 10);
  if (today !== this.currentDay) {
    if (this.currentDay) {
      this.log('safe_daily_reset', 'Daily spending counter reset', {
        previousDay: this.currentDay,
        previousSpent: formatEther(this.dailySpent),
      });
    }
    this.currentDay = today;
    this.dailySpent = 0n;
    this.loadSpend();
  }
}
```

**Step 4: Run tests to verify all pass**

Run: `cd ts-executor && pnpm test -- --run tests/safe-wallet.test.ts`
Expected: ALL tests PASS (including existing tests that don't pass `redis`)

**Step 5: Wire Redis into SafeWalletManager in index.ts**

In `ts-executor/src/index.ts`, update `initializeComponents()` to pass the Redis client's raw connection to SafeWalletManager. After `const redis = new RedisManager();` and `await redis.connect();`, pass `redis.raw` to the wallet:

```typescript
const safeWallet = await SafeWalletManager.create({
  onLog: log,
  redis: redis.raw,
});
```

Note: `redis.raw` is already exposed by `RedisManager` (see `get raw(): Redis` at line 179 of `redis/client.ts`). The `ioredis` client has `get()` and `set()` methods matching the interface.

However, `initializeComponents` creates Redis before connecting. Move the wallet creation after `await redis.connect()` in `main()`, or connect Redis inside `initializeComponents()`. The simpler fix: move `await redis.connect()` before `SafeWalletManager.create()` inside `initializeComponents`:

```typescript
async function initializeComponents(): Promise<{...}> {
  const redis = new RedisManager();
  await redis.connect();    // <-- Move connect here

  const safeWallet = await SafeWalletManager.create({
    onLog: log,
    redis: redis.raw,
  });
  // ... rest unchanged ...
}
```

And remove the duplicate `await redis.connect()` from `main()`.

**Step 6: Commit**

```bash
git add ts-executor/src/wallet/safe-wallet.ts ts-executor/tests/safe-wallet.test.ts ts-executor/src/index.ts
git commit -m "fix(icarus): persist daily spend to Redis — survives restarts"
```

---

### Task 4: Token-aware spending limits (pass ETH value, not token amount)

**Files:**
- Modify: `ts-executor/src/execution/transaction-builder.ts:173-218` (handleOrder)
- Test: `ts-executor/tests/transaction-builder.test.ts`

**Step 1: Write failing tests**

Add to the `Safe wallet validation` describe block in `ts-executor/tests/transaction-builder.test.ts`:

```typescript
it('validates with ETH value from built tx, not raw token amount', async () => {
  vi.useRealTimers();

  const mockAdapter: ProtocolAdapter = {
    buildTransaction: vi.fn().mockResolvedValue({
      to: '0xAavePoolAddress1234567890abcdef12345678' as `0x${string}`,
      data: '0xdeadbeef' as `0x${string}`,
      value: 0n, // ERC-20 operation, no ETH transferred
    }),
  };

  const adapters = new Map<string, ProtocolAdapter>();
  adapters.set('aave_v3', mockAdapter);

  const safeWallet = createMockSafeWallet();
  const builder = createBuilder({ adapters, safeWallet });

  const order = makeOrder({
    protocol: 'aave_v3',
    action: 'supply',
    params: {
      tokenIn: '0x1234567890abcdef1234567890abcdef12345678',
      amount: '1000000000000', // Large token amount
    },
  });
  await builder.handleOrder(order);

  // validateOrder should be called with value=0 (ETH value), not the token amount
  expect(safeWallet.validateOrder).toHaveBeenCalledWith(
    '0xAavePoolAddress1234567890abcdef12345678',
    0n,
  );
});

it('validates with ETH value for raw transfer fallback', async () => {
  vi.useRealTimers();

  const safeWallet = createMockSafeWallet();
  const builder = createBuilder({ safeWallet, adapters: new Map() });

  const order = makeOrder({
    protocol: 'unknown',
    params: {
      tokenIn: '0x1234567890abcdef1234567890abcdef12345678',
      amount: '500000000000000000', // 0.5 ETH
    },
  });
  await builder.handleOrder(order);

  // Raw transfer fallback uses value = BigInt(amount), so validate with that
  expect(safeWallet.validateOrder).toHaveBeenCalledWith(
    order.params.tokenIn as Address,
    500000000000000000n,
  );
});

it('records spend with ETH value, not token amount', async () => {
  vi.useRealTimers();

  const mockAdapter: ProtocolAdapter = {
    buildTransaction: vi.fn().mockResolvedValue({
      to: '0xAavePoolAddress1234567890abcdef12345678' as `0x${string}`,
      data: '0xdeadbeef' as `0x${string}`,
      value: 0n,
    }),
  };

  const adapters = new Map<string, ProtocolAdapter>();
  adapters.set('aave_v3', mockAdapter);

  const safeWallet = createMockSafeWallet();
  const builder = createBuilder({ adapters, safeWallet });

  const order = makeOrder({ protocol: 'aave_v3', action: 'supply' });
  const result = await builder.handleOrder(order);

  expect(result.status).toBe('confirmed');
  // recordSpend should be called with 0n (ETH value), not token amount
  expect(safeWallet.recordSpend).toHaveBeenCalledWith(0n);
});
```

**Step 2: Run tests to verify they fail**

Run: `cd ts-executor && pnpm test -- --run tests/transaction-builder.test.ts`
Expected: FAIL — validateOrder still called with `BigInt(order.params.amount)`, not the tx value

**Step 3: Implement — use built tx value for spending validation**

In `ts-executor/src/execution/transaction-builder.ts`, modify `handleOrder()` (lines 173-218).

The key change: build the transaction data **before** the spending validation, and use the built tx's `value` field instead of the raw token amount.

Replace lines 193-211 with:

```typescript
      // 2. Build transaction data via adapter
      const txData = await this.buildTransactionData(order);
      const ethValue = txData.value ?? 0n;

      // 3. Allowlist + spending limit check via Safe wallet (uses ETH value, not token amount)
      const target = txData.to as Address;
      const validation = this.safeWallet.validateOrder(target, ethValue);
      if (!validation.allowed) {
        const result = this.buildResult(order, 'failed', {
          error: `Order validation failed: ${validation.reason ?? 'not allowed'}`,
        });
        await this.emitResult(order, result);
        return result;
      }

      // 4. Execute with retries (reuse already-built txData)
      const result = await this.executeWithRetryUsingTxData(order, txData);
      await this.emitResult(order, result);

      // 5. Record spend on success (ETH value only)
      if (result.status === 'confirmed') {
        this.safeWallet.recordSpend(ethValue);
      }
```

This also removes the separate `resolveTarget()` call — the target comes from the already-built tx.

Add a new method that takes pre-built txData to avoid building twice:

```typescript
private async executeWithRetryUsingTxData(
  order: ExecutionOrder,
  txData: { to: `0x${string}`; data?: `0x${string}`; value?: bigint },
): Promise<ExecutionResult> {
  let lastError: string = '';

  for (let attempt = 0; attempt <= this.maxRetries; attempt++) {
    if (attempt > 0) {
      const delay = this.initialRetryDelayMs * Math.pow(2, attempt - 1);
      this.log('exec_retry', 'Retrying transaction', {
        orderId: order.orderId,
        attempt,
        delayMs: delay,
      });
      await this.sleep(delay);
    }

    try {
      const result = await this.executeViaSafe(order, txData, attempt);
      return result;
    } catch (err) {
      lastError = err instanceof Error ? err.message : String(err);
      this.log('exec_attempt_failed', 'Transaction attempt failed', {
        orderId: order.orderId,
        attempt,
        error: lastError,
      });

      if (this.isNonRetryable(lastError)) {
        break;
      }
    }
  }

  return this.buildResult(order, 'failed', {
    error: lastError,
    retryCount: this.maxRetries,
  });
}
```

The `resolveTarget()` method can be deleted since we no longer need it — the target comes directly from `buildTransactionData`.

**Step 4: Update existing tests that assert old behavior**

The test `validates order via adapter target when adapter exists` (line 340) currently asserts:
```typescript
expect(safeWallet.validateOrder).toHaveBeenCalledWith(
  '0xAavePoolAddress1234567890abcdef12345678',
  BigInt(order.params.amount),
);
```

Change the second argument to match the adapter's `value` (which is `0n`):
```typescript
expect(safeWallet.validateOrder).toHaveBeenCalledWith(
  '0xAavePoolAddress1234567890abcdef12345678',
  0n,
);
```

The test `calls validateOrder before execution` (line 255) currently asserts:
```typescript
expect(safeWallet.validateOrder).toHaveBeenCalledWith(
  order.params.tokenIn as Address,
  BigInt(order.params.amount),
);
```

This test uses no adapter, so the fallback path applies. The fallback builds `{ to: params.tokenIn, value: BigInt(params.amount) }`, so the validation should use that value. This test should still pass as-is since the fallback's `value` IS `BigInt(params.amount)`.

The test `calls recordSpend on successful execution` (line 288) asserts:
```typescript
expect(safeWallet.recordSpend).toHaveBeenCalledWith(BigInt(order.params.amount));
```

For the no-adapter fallback path, the value is `BigInt(params.amount)`, so this should still pass.

**Step 5: Run all tests**

Run: `cd ts-executor && pnpm test -- --run`
Expected: ALL tests PASS

**Step 6: Commit**

```bash
git add ts-executor/src/execution/transaction-builder.ts ts-executor/tests/transaction-builder.test.ts
git commit -m "fix(icarus): spending limits use ETH value, not raw token amount"
```

---

### Task 5: Run full test suite and verify

**Step 1: Run TS tests**

Run: `cd ts-executor && pnpm test -- --run`
Expected: All tests pass

**Step 2: Run Python tests** (no Python changes, but verify nothing broke)

Run: `cd py-engine && uv run pytest tests/ --tb=short -q`
Expected: All tests pass

**Step 3: Run harness verify**

Run: `bash harness/verify.sh`
Expected: Exit 0

**Step 4: Update progress.txt**

Append to `harness/progress.txt`:
```
## Session: 2026-03-07 — Security Hardening (4 fixes)

Implemented 4 security fixes from the ethskills.com/security audit:

1. **Slippage protection enforcement** — swap, mint_lp, burn_lp now throw if min
   amount params are missing. No more silent default to 0.
2. **Redis authentication** — docker-compose.yml uses REDIS_PASSWORD env var with
   requirepass. Both services pass auth via REDIS_URL.
3. **Daily spend persistence** — SafeWalletManager persists daily spend counter to
   Redis key `safe:daily_spend:{date}` with 48h TTL. Survives process restarts.
4. **Token-aware spending limits** — TransactionBuilder passes ETH value from built
   tx (not raw token amount) to validateOrder and recordSpend. ERC-20 operations
   (value=0) correctly bypass ETH spending caps.

Deferred: Flashbots Protect (P1b), PostgreSQL credential hardening (low severity).

Next: P1b — Flashbots Protect RPC routing for swap operations.
```

**Step 5: Final commit**

```bash
git add harness/progress.txt
git commit -m "docs(icarus): update progress — security hardening complete"
```
