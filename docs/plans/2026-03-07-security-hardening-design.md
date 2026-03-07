# Security Hardening Design

**Date:** 2026-03-07
**Origin:** Security review against ethskills.com/security skill
**Scope:** 4 hardening fixes. No new features. Flashbots Protect stays P1b.

## Fixes

### Fix 1: Reject slippage-sensitive ops missing min amounts

**Problem:** `amountOutMin`, `amountAMin`, `amountBMin` default to `"0"` when missing from order params in the Aerodrome adapter (index.ts). Zero slippage protection makes swaps and liquidity operations trivially sandwichable.

**Solution:** The Aerodrome adapter's `buildTransaction` throws if min amount params are missing for `swap`, `mint_lp`, `burn_lp`. Supply/withdraw/stake/unstake/collect_fees are unaffected (not slippage-sensitive).

Validation happens in the adapter switch cases in `index.ts`, before encoding. The error propagates through `TransactionBuilder.handleOrder` and emits a `failed` result back to Python.

No schema change needed. `amountOutMin`/`amountAMin`/`amountBMin` are already optional in the schema because which params are required depends on the action. Enforcement is at the adapter level.

**Files:** `ts-executor/src/index.ts`

### Fix 2: Redis authentication

**Problem:** Redis has no `requirepass`. Anyone with network access to port 6379 can inject fake execution orders or read trading decisions.

**Solution:**
- `docker-compose.yml`: add `--requirepass ${REDIS_PASSWORD}` to Redis command
- `.env.example`: add `REDIS_PASSWORD=` placeholder
- `docker-compose.yml`: update `REDIS_URL` for both services to include password
- Redis healthcheck: update to use `REDIS_PASSWORD` env var

Both TS (`ioredis`) and Python (`redis`) clients parse auth from the URL natively. No client code changes needed.

**Files:** `docker-compose.yml`, `.env.example`

### Fix 3: Persist daily spend to Redis

**Problem:** `SafeWalletManager` tracks `dailySpent` in memory. Process restart resets the counter to 0, allowing the daily cap to be bypassed.

**Solution:** On every `recordSpend()`, write the updated total to Redis key `safe:daily_spend:{YYYY-MM-DD}` with 48h TTL. On initialization and day-change, read from Redis to restore the counter. In-memory counter remains the fast path; Redis is the persistence layer.

Redis already has `--appendonly yes` (AOF persistence), so the counter survives Redis restarts too. The 48h TTL auto-cleans stale keys.

**Changes to `SafeWalletManager`:**
- Constructor/factory takes an optional Redis client
- `recordSpend()` writes to Redis after updating in-memory counter
- `resetDailyIfNeeded()` reads from Redis on day change or first call after restart
- New private methods: `_persistSpend()`, `_loadSpend()`

**Files:** `ts-executor/src/wallet/safe-wallet.ts`

### Fix 4: Token-aware spending limits

**Problem:** `validateOrder(target, amountWei)` receives `BigInt(order.params.amount)` from `TransactionBuilder.handleOrder`. For ERC-20 operations, this is the token amount, not ETH value. A $1M USDC transfer looks like ~0.000001 ETH and passes the per-tx cap.

**Solution:** Change `TransactionBuilder.handleOrder` to pass the actual ETH `value` from the built transaction to `validateOrder`, not the raw token amount. For ERC-20 operations where `value = 0`, the spending cap check passes (no ETH is spent). The per-tx and daily caps protect against ETH drain, which is the real risk the Safe guards against.

This is simpler and more correct than price-converting every token amount to an ETH equivalent.

**Files:** `ts-executor/src/execution/transaction-builder.ts`

## Testing

| Fix | Tests |
|-----|-------|
| 1 | swap/mint_lp/burn_lp without min amounts throws; with min amounts succeeds |
| 2 | Redis connection with auth (integration test or mock) |
| 3 | Spend persistence across SafeWalletManager instances sharing the same Redis |
| 4 | ERC-20 operations (value=0) pass spending caps; ETH transfers are correctly gated |

## Out of Scope

- Flashbots Protect (P1b feature, separate design cycle)
- Decimal-aware position sizing (Python-side concern, not a security fix)
- PostgreSQL credential hardening (low severity, no production deployment yet)
