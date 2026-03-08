/**
 * Safe 1-of-2 multisig wallet manager.
 *
 * Agent wallet as owner #1 (hot/automated), human wallet as owner #2 (cold/recovery).
 * Threshold = 1 so the agent can sign alone. Bounded approvals, spending limits,
 * and contract allowlist enforcement.
 */

import {
  type Address,
  type Hex,
  type Chain,
  type TransactionReceipt,
  createPublicClient,
  http,
  parseAbi,
  formatEther,
} from 'viem';
import { privateKeyToAccount, type PrivateKeyAccount } from 'viem/accounts';
import { sepolia } from 'viem/chains';
import SafeDefault from '@safe-global/protocol-kit';

// Handle CJS/ESM double-default: at runtime the default import may be
// a namespace object whose `.default` holds the actual Safe class.
const Safe = (
  typeof (SafeDefault as unknown as Record<string, unknown>).init === 'function'
    ? SafeDefault
    : (SafeDefault as unknown as { default: typeof SafeDefault }).default
) as typeof SafeDefault;
type Safe = SafeDefault;

// Protocol Kit MetaTransactionData shape (avoids direct @safe-global/types-kit dependency)
interface MetaTransactionData {
  to: string;
  value: string;
  data: string;
  operation?: number;
}

/** Safe operation types — Call = 0, DelegateCall = 1. */
const OperationType = { Call: 0, DelegateCall: 1 } as const;

// Re-export the canonical SafeWalletLike interface from TransactionBuilder
export type { SafeWalletLike } from '../execution/transaction-builder.js';
import type { SafeWalletLike } from '../execution/transaction-builder.js';

// ── Options ──────────────────────────────────────

export interface SafeWalletOptions {
  /** Private key for the agent EOA signer. Defaults to env WALLET_PRIVATE_KEY. */
  privateKey?: string;
  /** Safe contract address. Defaults to env SAFE_ADDRESS. */
  safeAddress?: Address;
  /** Second owner (human recovery wallet). Defaults to env SAFE_RECOVERY_ADDRESS. */
  recoveryAddress?: Address;
  /** RPC URL. Defaults to env ALCHEMY_SEPOLIA_HTTP_URL. */
  rpcUrl?: string;
  /** Chain. Defaults to sepolia. */
  chain?: Chain;
  /** Per-transaction spending cap in wei. Defaults to env SPENDING_CAP_PER_TX_WEI or 0.5 ETH. */
  perTxCapWei?: bigint;
  /** Daily spending cap in wei. Defaults to env SPENDING_CAP_DAILY_WEI or 2 ETH. */
  dailyCapWei?: bigint;
  /** Allowlisted contract addresses. */
  allowlist?: Set<Address>;
  /** Structured log callback. */
  onLog?: (event: string, message: string, extra?: Record<string, unknown>) => void;
}

// ── ERC-20 ABI for balance queries ──────────────

const ERC20_ABI = parseAbi([
  'function balanceOf(address account) view returns (uint256)',
]);

/**
 * Parse CONTRACT_ALLOWLIST env var (comma-separated hex addresses) into a Set.
 * Returns empty set when env var is unset or empty — validateOrder() treats this as fail-closed.
 */
function loadAllowlistFromEnv(): Set<Address> {
  const raw = process.env.CONTRACT_ALLOWLIST ?? '';
  if (!raw.trim()) return new Set<Address>();
  return new Set(
    raw.split(',')
      .map((s) => s.trim())
      .filter((s) => s.length > 0) as Address[],
  );
}

// ── Safe Wallet Manager ──────────────────────────

/** Manages a Safe 1-of-2 multisig with spending limits and allowlist enforcement. */
export class SafeWalletManager implements SafeWalletLike {
  private readonly account: PrivateKeyAccount;
  private readonly safeAddress: Address;
  private readonly protocolKit: Safe;
  private readonly rpcUrl: string;
  private readonly chain: Chain;
  private readonly limits: { perTxCapWei: bigint; dailyCapWei: bigint };
  private readonly allowlistSet: Set<Address>;
  private readonly log: (event: string, message: string, extra?: Record<string, unknown>) => void;

  // Daily spending tracker
  private dailySpent: bigint = 0n;
  private currentDay: string = '';

  private constructor(
    account: PrivateKeyAccount,
    safeAddress: Address,
    protocolKit: Safe,
    rpcUrl: string,
    chain: Chain,
    limits: { perTxCapWei: bigint; dailyCapWei: bigint },
    allowlist: Set<Address>,
    onLog: (event: string, message: string, extra?: Record<string, unknown>) => void,
  ) {
    this.account = account;
    this.safeAddress = safeAddress;
    this.protocolKit = protocolKit;
    this.rpcUrl = rpcUrl;
    this.chain = chain;
    this.limits = limits;
    this.allowlistSet = allowlist;
    this.log = onLog;
  }

  /**
   * Async factory — creates and initializes a SafeWalletManager.
   *
   * If `safeAddress` is provided, connects to an existing Safe.
   * Otherwise, predicts and deploys a new 1-of-2 Safe.
   */
  static async create(opts: SafeWalletOptions = {}): Promise<SafeWalletManager> {
    const privateKey = opts.privateKey ?? process.env.WALLET_PRIVATE_KEY;
    if (!privateKey) {
      throw new Error('WALLET_PRIVATE_KEY is not configured');
    }

    const account = privateKeyToAccount(privateKey as `0x${string}`);
    const chain = opts.chain ?? sepolia;
    const rpcUrl = opts.rpcUrl ?? process.env.ALCHEMY_SEPOLIA_HTTP_URL ?? '';
    const recoveryAddress = opts.recoveryAddress
      ?? (process.env.SAFE_RECOVERY_ADDRESS as Address | undefined);

    const safeAddress = opts.safeAddress
      ?? (process.env.SAFE_ADDRESS as Address | undefined);

    const limits = {
      perTxCapWei: opts.perTxCapWei
        ?? BigInt(process.env.SPENDING_CAP_PER_TX_WEI ?? '500000000000000000'), // 0.5 ETH
      dailyCapWei: opts.dailyCapWei
        ?? BigInt(process.env.SPENDING_CAP_DAILY_WEI ?? '2000000000000000000'), // 2 ETH
    };

    const allowlist = opts.allowlist ?? loadAllowlistFromEnv();
    const onLog = opts.onLog ?? (() => {});

    let protocolKit: Safe;

    if (safeAddress) {
      // Connect to existing Safe
      protocolKit = await Safe.init({
        provider: rpcUrl,
        signer: privateKey,
        safeAddress,
      });
    } else {
      // Deploy new 1-of-2 Safe
      if (!recoveryAddress) {
        throw new Error('SAFE_RECOVERY_ADDRESS is required when deploying a new Safe');
      }

      protocolKit = await Safe.init({
        provider: rpcUrl,
        signer: privateKey,
        predictedSafe: {
          safeAccountConfig: {
            owners: [account.address, recoveryAddress],
            threshold: 1,
          },
        },
      });
    }

    const resolvedSafeAddress = safeAddress ?? (await protocolKit.getAddress()) as Address;

    // Never log the private key — log only the derived address
    onLog('safe_wallet_initialized', 'Safe wallet manager initialized', {
      signerAddress: account.address,
      safeAddress: resolvedSafeAddress,
      perTxCap: formatEther(limits.perTxCapWei),
      dailyCap: formatEther(limits.dailyCapWei),
      allowlistSize: allowlist.size,
    });

    return new SafeWalletManager(
      account,
      resolvedSafeAddress,
      protocolKit,
      rpcUrl,
      chain,
      limits,
      allowlist,
      onLog,
    );
  }

  /** The Safe contract address. */
  get address(): Address {
    return this.safeAddress;
  }

  /** The agent EOA signer address. */
  get signerAddress(): Address {
    return this.account.address;
  }

  // ── Transaction Execution ──────────────────────

  /** Execute a single transaction through the Safe. */
  async executeTransaction(tx: {
    to: Address; value?: bigint; data?: Hex; operation?: number;
  }): Promise<{ hash: `0x${string}`; receipt: TransactionReceipt }> {
    const metaTx: MetaTransactionData = {
      to: tx.to,
      value: String(tx.value ?? 0n),
      data: tx.data ?? '0x',
      operation: (tx.operation ?? OperationType.Call) as number,
    };

    const safeTx = await this.protocolKit.createTransaction({
      transactions: [metaTx],
    });

    const txResult = await this.protocolKit.executeTransaction(safeTx);
    const hash = txResult.hash as `0x${string}`;

    const publicClient = createPublicClient({
      chain: this.chain,
      transport: http(this.rpcUrl),
    });

    const receipt = await publicClient.waitForTransactionReceipt({ hash });

    this.log('safe_tx_executed', 'Safe transaction executed', {
      hash,
      to: tx.to,
      value: String(tx.value ?? 0n),
    });

    return { hash, receipt };
  }

  /** Execute multiple transactions as a batch via MultiSend. */
  async executeBatch(txs: Array<{
    to: Address; value?: bigint; data?: Hex; operation?: number;
  }>): Promise<{ hash: `0x${string}`; receipt: TransactionReceipt }> {
    const metaTxs: MetaTransactionData[] = txs.map((tx) => ({
      to: tx.to,
      value: String(tx.value ?? 0n),
      data: tx.data ?? '0x',
      operation: (tx.operation ?? OperationType.Call) as number,
    }));

    const safeTx = await this.protocolKit.createTransaction({
      transactions: metaTxs,
    });

    const txResult = await this.protocolKit.executeTransaction(safeTx);
    const hash = txResult.hash as `0x${string}`;

    const publicClient = createPublicClient({
      chain: this.chain,
      transport: http(this.rpcUrl),
    });

    const receipt = await publicClient.waitForTransactionReceipt({ hash });

    this.log('safe_batch_executed', 'Safe batch transaction executed', {
      hash,
      txCount: txs.length,
    });

    return { hash, receipt };
  }

  // ── Spending Limits ──────────────────────────────

  /** Validate an order against allowlist and spending limits. */
  validateOrder(target: Address, amountWei: bigint): { allowed: boolean; reason?: string } {
    // Allowlist check — fail-closed: reject all when allowlist is empty
    if (this.allowlistSet.size === 0) {
      return { allowed: false, reason: 'Contract allowlist is empty — all transactions rejected (fail-closed). Set CONTRACT_ALLOWLIST env var.' };
    }
    const normalizedTarget = target.toLowerCase() as Address;
    let found = false;
    for (const addr of this.allowlistSet) {
      if (addr.toLowerCase() === normalizedTarget) {
        found = true;
        break;
      }
    }
    if (!found) {
      return { allowed: false, reason: `Target contract ${target} is not on the allowlist` };
    }

    // Per-transaction cap
    if (amountWei > this.limits.perTxCapWei) {
      return {
        allowed: false,
        reason: `Amount ${formatEther(amountWei)} ETH exceeds per-tx cap of ${formatEther(this.limits.perTxCapWei)} ETH`,
      };
    }

    // Daily cap — reset if new day
    this.resetDailyIfNeeded();
    const projectedDaily = this.dailySpent + amountWei;
    if (projectedDaily > this.limits.dailyCapWei) {
      return {
        allowed: false,
        reason: `Daily spend would reach ${formatEther(projectedDaily)} ETH, exceeding cap of ${formatEther(this.limits.dailyCapWei)} ETH`,
      };
    }

    return { allowed: true };
  }

  /** Record a spend against the daily limit. */
  recordSpend(amountWei: bigint): void {
    this.resetDailyIfNeeded();
    this.dailySpent += amountWei;
    this.log('safe_spend_recorded', 'Spend recorded', {
      amount: formatEther(amountWei),
      dailyTotal: formatEther(this.dailySpent),
      dailyCap: formatEther(this.limits.dailyCapWei),
    });
  }

  // ── Balance Queries ──────────────────────────────

  /** Query the ETH balance of the Safe. */
  async getBalance(): Promise<bigint> {
    const publicClient = createPublicClient({
      chain: this.chain,
      transport: http(this.rpcUrl),
    });
    return publicClient.getBalance({ address: this.safeAddress });
  }

  /** Query an ERC-20 token balance of the Safe. */
  async getTokenBalance(tokenAddress: Address): Promise<bigint> {
    const publicClient = createPublicClient({
      chain: this.chain,
      transport: http(this.rpcUrl),
    });
    return publicClient.readContract({
      address: tokenAddress,
      abi: ERC20_ABI,
      functionName: 'balanceOf',
      args: [this.safeAddress],
    });
  }

  // ── Private helpers ──────────────────────────────

  /** Reset daily counter if the UTC day has changed. */
  private resetDailyIfNeeded(): void {
    const today = new Date().toISOString().slice(0, 10); // YYYY-MM-DD
    if (today !== this.currentDay) {
      if (this.currentDay) {
        this.log('safe_daily_reset', 'Daily spending counter reset', {
          previousDay: this.currentDay,
          previousSpent: formatEther(this.dailySpent),
        });
      }
      this.currentDay = today;
      this.dailySpent = 0n;
    }
  }
}
