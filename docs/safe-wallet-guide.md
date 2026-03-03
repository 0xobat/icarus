# Safe Wallet & Agent Interaction Guide

Icarus uses a **Safe 1-of-2 multisig** as the agent's on-chain wallet. This gives the autonomous agent the ability to sign transactions on its own while preserving human recovery access.

## How It Works

```
Owner #1: Agent EOA (hot key, automated)
Owner #2: Human wallet (cold key, recovery)
Threshold: 1 (either owner can sign alone)
```

The agent signs and submits transactions through the Safe contract using `@safe-global/protocol-kit`. Because the threshold is 1, no human co-signature is needed for normal operation. The human wallet exists purely as a recovery path — if the agent key is compromised or lost, owner #2 can rotate keys or withdraw funds.

## Environment Variables

Add these to your `.env` file:

```bash
# Agent's private key (EOA that owns the Safe)
WALLET_PRIVATE_KEY=0x...

# Safe contract address (omit to deploy a new Safe on first run)
SAFE_ADDRESS=0x...

# Human recovery wallet (required when deploying a new Safe)
SAFE_RECOVERY_ADDRESS=0x...

# RPC endpoint
ALCHEMY_SEPOLIA_HTTP_URL=https://eth-sepolia.g.alchemy.com/v2/YOUR_KEY

# Spending limits (optional, shown with defaults)
SPENDING_CAP_PER_TX_WEI=500000000000000000   # 0.5 ETH per transaction
SPENDING_CAP_DAILY_WEI=2000000000000000000   # 2 ETH per day
```

### First Run vs Existing Safe

| Scenario | What to set |
|----------|-------------|
| **New deployment** | Set `WALLET_PRIVATE_KEY` + `SAFE_RECOVERY_ADDRESS`. Leave `SAFE_ADDRESS` empty. The agent predicts and deploys a fresh 1-of-2 Safe. |
| **Existing Safe** | Set `WALLET_PRIVATE_KEY` + `SAFE_ADDRESS`. The agent connects to your existing Safe (the private key must belong to an owner). |

## Transaction Flow

When the Python engine emits an execution order, the TypeScript executor processes it through this pipeline:

```
Order received (Redis: execution:orders)
        |
   Pre-flight checks
   - Is the deadline still valid?
   - Is gas below the ceiling?
        |
   Safe wallet validation
   - Is the target on the allowlist? (if configured)
   - Does the amount fit within the per-TX cap?
   - Does it fit within today's daily cap?
        |
   Protocol adapter builds calldata
   (aave_v3, lido, uniswap_v3, etc.)
        |
   Safe executes transaction
   - Creates a Safe transaction object
   - Signs with the agent EOA
   - Submits on-chain
   - Waits for receipt
        |
   Result published (Redis: execution:results)
   - On success: spend recorded against daily limit
   - On failure: retried up to 3x with exponential backoff
```

## Spending Limits

Two layers of protection run in-process (no on-chain overhead):

**Per-transaction cap** — Any single order exceeding `SPENDING_CAP_PER_TX_WEI` is rejected before hitting the chain.

**Daily cap** — Total spend across all transactions in a UTC day. Resets at midnight UTC. If a new order would push the daily total past `SPENDING_CAP_DAILY_WEI`, it's rejected.

Both limits are checked via `validateOrder()` before any transaction is submitted.

## Contract Allowlist

If you pass an `allowlist` set when creating the wallet, only transactions targeting those addresses will be permitted. Any order aimed at an unlisted contract is rejected.

```typescript
const safeWallet = await SafeWalletManager.create({
  allowlist: new Set([
    '0xAavePoolAddress...',
    '0xLidoStETHAddress...',
  ] as Address[]),
});
```

When the allowlist is empty (default), all targets are permitted and spending limits are the only guard.

## Batching

For multi-step operations (e.g., approve + swap), the Safe supports batched execution via MultiSend:

```typescript
await safeWallet.executeBatch([
  { to: tokenAddress, data: approveCalldata },
  { to: routerAddress, data: swapCalldata },
]);
```

Both transactions execute atomically in a single on-chain transaction.

## Human Recovery

Because the Safe has two owners and a threshold of 1, the human wallet (owner #2) can:

1. **Withdraw funds** — Sign a transaction from the Safe to move assets to a secure address.
2. **Rotate the agent key** — Remove owner #1 and add a new agent EOA via the Safe's `swapOwner` function.
3. **Raise the threshold** — Change threshold to 2 to require both signatures (effectively pausing the agent).
4. **Remove the agent** — Call `removeOwner` to revoke agent access entirely.

All of these can be done through the [Safe Web App](https://app.safe.global) by connecting the human wallet.

## Balance Queries

The wallet exposes read-only balance methods (useful for monitoring, not gated by spending limits):

```typescript
const ethBalance = await safeWallet.getBalance();
const usdcBalance = await safeWallet.getTokenBalance('0xUSDC...' as Address);
```

## Quick Reference

| Component | Role |
|-----------|------|
| `SafeWalletManager` | Wraps Safe Protocol Kit, enforces spending limits |
| `TransactionBuilder` | Consumes orders, routes through adapters, calls SafeWallet |
| `ContractAllowlist` | Optional target address filter |
| `EventReporter` | Publishes execution results to Redis |
| Protocol Adapters | Build calldata for specific DeFi protocols |
