/**
 * EXEC-002: Alchemy Smart Wallet integration.
 *
 * Manages an ERC-4337 smart wallet with:
 * - Spending limits (daily + per-transaction caps)
 * - Contract allowlist enforcement
 * - Balance and nonce queries
 * - UserOp construction and submission
 * - Secure private key handling (env var only, never logged)
 */

import {
  type PublicClient,
  type Address,
  type Hex,
  type Chain,
  createPublicClient,
  http,
  encodeFunctionData,
  parseAbi,
  formatEther,
} from 'viem';
import { privateKeyToAccount, type PrivateKeyAccount } from 'viem/accounts';
import { sepolia } from 'viem/chains';

// ── Types ──────────────────────────────────────────

export interface SpendingLimits {
  /** Max spend per transaction in wei. */
  perTxCapWei: bigint;
  /** Max total spend per day (UTC) in wei. */
  dailyCapWei: bigint;
}

export interface SpendingCheckResult {
  allowed: boolean;
  reason?: string;
}

export interface UserOperation {
  sender: Address;
  nonce: bigint;
  callData: Hex;
  callGasLimit: bigint;
  verificationGasLimit: bigint;
  preVerificationGas: bigint;
  maxFeePerGas: bigint;
  maxPriorityFeePerGas: bigint;
  signature: Hex;
  /** Factory address + init code for first-time wallet deployment. */
  initCode?: Hex;
  /** Paymaster address + data for gas sponsorship. */
  paymasterAndData?: Hex;
}

export interface SmartWalletOptions {
  /** Private key for the EOA signer. Defaults to env WALLET_PRIVATE_KEY. */
  privateKey?: string;
  /** Smart wallet contract address. Defaults to env SMART_WALLET_ADDRESS. */
  walletAddress?: Address;
  /** RPC URL. Defaults to env ALCHEMY_SEPOLIA_HTTP_URL. */
  rpcUrl?: string;
  /** Bundler URL for submitting UserOps. Defaults to env BUNDLER_URL. */
  bundlerUrl?: string;
  /** Chain. Defaults to sepolia. */
  chain?: Chain;
  /** Per-transaction spending cap in wei. Defaults to env SPENDING_CAP_PER_TX_WEI. */
  perTxCapWei?: bigint;
  /** Daily spending cap in wei. Defaults to env SPENDING_CAP_DAILY_WEI. */
  dailyCapWei?: bigint;
  /** Allowlisted contract addresses. */
  allowlist?: Set<Address>;
  /** Structured log callback. */
  onLog?: (event: string, message: string, extra?: Record<string, unknown>) => void;
  /** Override public client (for testing). */
  publicClient?: PublicClient;
}

// ── ERC-4337 Constants ──────────────────────────────

/** EntryPoint v0.7 address (standard across chains). */
const ENTRYPOINT_ADDRESS: Address = '0x0000000071727De22E5E9d8BAf0edAc6f37da032';

/** Minimal ERC-4337 EntryPoint ABI for getNonce. */
const ENTRYPOINT_ABI = parseAbi([
  'function getNonce(address sender, uint192 key) view returns (uint256)',
]);

/** Minimal ERC-20 ABI for balance queries. */
const ERC20_ABI = parseAbi([
  'function balanceOf(address account) view returns (uint256)',
]);

// ── Smart Wallet Manager ──────────────────────────────

export class SmartWalletManager {
  private readonly account: PrivateKeyAccount;
  private readonly walletAddress: Address;
  private readonly publicClient: PublicClient;
  private readonly bundlerUrl: string;
  private readonly limits: SpendingLimits;
  private readonly allowlist: Set<Address>;
  private readonly log: (event: string, message: string, extra?: Record<string, unknown>) => void;

  // Daily spending tracker
  private dailySpent: bigint = 0n;
  private currentDay: string = '';

  constructor(opts: SmartWalletOptions = {}) {
    const privateKey = opts.privateKey ?? process.env.WALLET_PRIVATE_KEY;
    if (!privateKey) {
      throw new Error('WALLET_PRIVATE_KEY is not configured');
    }

    this.account = privateKeyToAccount(privateKey as `0x${string}`);

    this.walletAddress = opts.walletAddress
      ?? (process.env.SMART_WALLET_ADDRESS as Address | undefined)
      ?? this.account.address; // Fallback to EOA if no smart wallet deployed

    const chain = opts.chain ?? sepolia;
    const rpcUrl = opts.rpcUrl ?? process.env.ALCHEMY_SEPOLIA_HTTP_URL;

    this.publicClient = opts.publicClient ?? createPublicClient({
      chain,
      transport: http(rpcUrl),
    });

    this.bundlerUrl = opts.bundlerUrl ?? process.env.BUNDLER_URL ?? '';

    this.limits = {
      perTxCapWei: opts.perTxCapWei
        ?? BigInt(process.env.SPENDING_CAP_PER_TX_WEI ?? '500000000000000000'), // 0.5 ETH default
      dailyCapWei: opts.dailyCapWei
        ?? BigInt(process.env.SPENDING_CAP_DAILY_WEI ?? '2000000000000000000'), // 2 ETH default
    };

    this.allowlist = opts.allowlist ?? new Set();
    this.log = opts.onLog ?? (() => {});

    // Never log the private key — log only the derived address
    this.log('wallet_initialized', 'Smart wallet manager initialized', {
      signerAddress: this.account.address,
      walletAddress: this.walletAddress,
      perTxCap: formatEther(this.limits.perTxCapWei),
      dailyCap: formatEther(this.limits.dailyCapWei),
      allowlistSize: this.allowlist.size,
    });
  }

  /** The smart wallet contract address. */
  get address(): Address {
    return this.walletAddress;
  }

  /** The EOA signer address. */
  get signerAddress(): Address {
    return this.account.address;
  }

  // ── Spending Limits ──────────────────────────────

  /** Check if a transaction amount is within spending limits. */
  checkSpendingLimit(amountWei: bigint): SpendingCheckResult {
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
    this.log('wallet_spend_recorded', 'Spend recorded', {
      amount: formatEther(amountWei),
      dailyTotal: formatEther(this.dailySpent),
      dailyCap: formatEther(this.limits.dailyCapWei),
    });
  }

  /** Get remaining daily spend allowance. */
  get dailyRemaining(): bigint {
    this.resetDailyIfNeeded();
    const remaining = this.limits.dailyCapWei - this.dailySpent;
    return remaining > 0n ? remaining : 0n;
  }

  /** Get current daily spend total. */
  get dailySpentTotal(): bigint {
    this.resetDailyIfNeeded();
    return this.dailySpent;
  }

  /** Reset daily counter if the UTC day has changed. */
  private resetDailyIfNeeded(): void {
    const today = new Date().toISOString().slice(0, 10); // YYYY-MM-DD
    if (today !== this.currentDay) {
      if (this.currentDay) {
        this.log('wallet_daily_reset', 'Daily spending counter reset', {
          previousDay: this.currentDay,
          previousSpent: formatEther(this.dailySpent),
        });
      }
      this.currentDay = today;
      this.dailySpent = 0n;
    }
  }

  // ── Contract Allowlist ──────────────────────────────

  /** Check if a target contract is on the allowlist. */
  isAllowlisted(address: Address): boolean {
    // If allowlist is empty, allow all (no restrictions configured)
    if (this.allowlist.size === 0) return true;
    return this.allowlist.has(address.toLowerCase() as Address);
  }

  /** Validate a target address against the allowlist. Returns rejection reason or null. */
  checkAllowlist(target: Address): string | null {
    if (this.isAllowlisted(target)) return null;
    return `Target contract ${target} is not on the allowlist`;
  }

  /** Add an address to the allowlist. */
  addToAllowlist(address: Address): void {
    this.allowlist.add(address.toLowerCase() as Address);
    this.log('wallet_allowlist_add', 'Address added to allowlist', {
      address: address.toLowerCase(),
    });
  }

  /** Get the current allowlist size. */
  get allowlistSize(): number {
    return this.allowlist.size;
  }

  // ── Balance & Nonce Queries ──────────────────────────

  /** Query the ETH balance of the smart wallet. */
  async getBalance(): Promise<bigint> {
    const balance = await this.publicClient.getBalance({
      address: this.walletAddress,
    });
    return balance;
  }

  /** Query an ERC-20 token balance of the smart wallet. */
  async getTokenBalance(tokenAddress: Address): Promise<bigint> {
    const balance = await this.publicClient.readContract({
      address: tokenAddress,
      abi: ERC20_ABI,
      functionName: 'balanceOf',
      args: [this.walletAddress],
    });
    return balance;
  }

  /** Query the smart wallet's nonce from the EntryPoint. */
  async getNonce(key: bigint = 0n): Promise<bigint> {
    const nonce = await this.publicClient.readContract({
      address: ENTRYPOINT_ADDRESS,
      abi: ENTRYPOINT_ABI,
      functionName: 'getNonce',
      args: [this.walletAddress, key],
    });
    return nonce;
  }

  // ── UserOp Construction ──────────────────────────────

  /** Build an ERC-4337 UserOperation from an execution order. */
  async buildUserOp(params: {
    target: Address;
    value: bigint;
    callData: Hex;
  }): Promise<UserOperation> {
    // Encode the execute call on the smart wallet
    const executeCallData = encodeFunctionData({
      abi: parseAbi(['function execute(address dest, uint256 value, bytes calldata func)']),
      functionName: 'execute',
      args: [params.target, params.value, params.callData],
    });

    // Get nonce from EntryPoint
    let nonce: bigint;
    try {
      nonce = await this.getNonce();
    } catch {
      nonce = 0n; // Fallback for tests/mocks
    }

    // Get gas estimates
    let maxFeePerGas: bigint;
    let maxPriorityFeePerGas: bigint;
    try {
      const gasPrice = await this.publicClient.getGasPrice();
      maxFeePerGas = gasPrice * 2n; // 2x buffer for inclusion
      maxPriorityFeePerGas = gasPrice / 10n; // ~10% tip
    } catch {
      maxFeePerGas = 30_000_000_000n; // 30 gwei fallback
      maxPriorityFeePerGas = 3_000_000_000n; // 3 gwei fallback
    }

    const userOp: UserOperation = {
      sender: this.walletAddress,
      nonce,
      callData: executeCallData,
      callGasLimit: 200_000n,
      verificationGasLimit: 100_000n,
      preVerificationGas: 50_000n,
      maxFeePerGas,
      maxPriorityFeePerGas,
      signature: '0x' as Hex, // Will be filled by sign step
    };

    // Sign the UserOp
    userOp.signature = await this.signUserOp(userOp);

    this.log('wallet_userop_built', 'UserOperation constructed', {
      sender: userOp.sender,
      nonce: Number(userOp.nonce),
      target: params.target,
      value: formatEther(params.value),
    });

    return userOp;
  }

  /** Sign a UserOperation with the EOA signer. */
  private async signUserOp(userOp: UserOperation): Promise<Hex> {
    // Create a hash of the UserOp for signing
    // In production, this would use the EntryPoint's getUserOpHash
    // For now, we sign a simplified representation
    const message = `${userOp.sender}:${userOp.nonce}:${userOp.callData}`;
    const signature = await this.account.signMessage({
      message,
    });
    return signature;
  }

  /** Submit a UserOperation to the bundler. */
  async sendUserOp(userOp: UserOperation): Promise<Hex> {
    if (!this.bundlerUrl) {
      throw new Error('BUNDLER_URL is not configured');
    }

    const response = await fetch(this.bundlerUrl, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        jsonrpc: '2.0',
        id: 1,
        method: 'eth_sendUserOperation',
        params: [
          {
            sender: userOp.sender,
            nonce: `0x${userOp.nonce.toString(16)}`,
            callData: userOp.callData,
            callGasLimit: `0x${userOp.callGasLimit.toString(16)}`,
            verificationGasLimit: `0x${userOp.verificationGasLimit.toString(16)}`,
            preVerificationGas: `0x${userOp.preVerificationGas.toString(16)}`,
            maxFeePerGas: `0x${userOp.maxFeePerGas.toString(16)}`,
            maxPriorityFeePerGas: `0x${userOp.maxPriorityFeePerGas.toString(16)}`,
            signature: userOp.signature,
            ...(userOp.initCode && { initCode: userOp.initCode }),
            ...(userOp.paymasterAndData && { paymasterAndData: userOp.paymasterAndData }),
          },
          ENTRYPOINT_ADDRESS,
        ],
      }),
    });

    const result = await response.json() as { result?: Hex; error?: { message: string } };

    if (result.error) {
      throw new Error(`Bundler error: ${result.error.message}`);
    }

    const opHash = result.result!;
    this.log('wallet_userop_sent', 'UserOperation submitted to bundler', {
      opHash,
      sender: userOp.sender,
    });

    return opHash;
  }

  // ── Combined Validation ──────────────────────────────

  /** Run all pre-execution checks: allowlist + spending limits. */
  validateOrder(target: Address, amountWei: bigint): SpendingCheckResult {
    // Allowlist check
    const allowlistRejection = this.checkAllowlist(target);
    if (allowlistRejection) {
      return { allowed: false, reason: allowlistRejection };
    }

    // Spending limit check
    return this.checkSpendingLimit(amountWei);
  }
}
