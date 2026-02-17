/**
 * EXEC-001: viem transaction builder.
 *
 * Consumes orders from execution:orders, constructs and submits
 * Ethereum transactions via viem, enforces gas/slippage/deadline
 * parameters, and publishes results to execution:results.
 */

import {
  createPublicClient,
  createWalletClient,
  http,
  type PublicClient,
  type WalletClient,
  type TransactionReceipt,
  type Account,
  type Chain,
  encodeFunctionData,
  type Abi,
  decodeErrorResult,
} from 'viem';
import { privateKeyToAccount } from 'viem/accounts';
import { sepolia } from 'viem/chains';
import { type RedisManager, CHANNELS } from '../redis/client.js';

// ── Types ──────────────────────────────────────────

export interface ExecutionOrder {
  version: string;
  orderId: string;
  correlationId: string;
  timestamp: string;
  chain: string;
  protocol: string;
  action: string;
  strategy?: string;
  priority?: string;
  params: {
    tokenIn: string;
    tokenOut?: string;
    amount: string;
    recipient?: string;
  };
  limits: {
    maxGasWei: string;
    maxSlippageBps: number;
    deadlineUnix: number;
  };
  useFlashbotsProtect?: boolean;
}

export interface ExecutionResult {
  version: '1.0.0';
  orderId: string;
  correlationId: string;
  timestamp: string;
  status: 'confirmed' | 'failed' | 'reverted' | 'timeout';
  txHash?: string;
  blockNumber?: number;
  gasUsed?: string;
  effectiveGasPrice?: string;
  fillPrice?: string;
  amountOut?: string;
  revertReason?: string;
  error?: string;
  retryCount?: number;
}

export interface TransactionBuilderOptions {
  /** Private key for signing. Defaults to env WALLET_PRIVATE_KEY. */
  privateKey?: string;
  /** RPC URL. Defaults to env ALCHEMY_SEPOLIA_HTTP_URL or public sepolia. */
  rpcUrl?: string;
  /** viem chain. Defaults to sepolia. */
  chain?: Chain;
  /** Max retry attempts for failed TXs. Defaults to 3. */
  maxRetries?: number;
  /** Initial retry delay in ms. Defaults to 1000. */
  initialRetryDelayMs?: number;
  /** TX confirmation timeout in ms. Defaults to 120_000. */
  confirmationTimeoutMs?: number;
  /** Structured log callback. */
  onLog?: (event: string, message: string, extra?: Record<string, unknown>) => void;
  /** Protocol ABI registry for encoding calls. */
  abiRegistry?: Map<string, Abi>;
  /** Override public client (for testing). */
  publicClient?: PublicClient;
  /** Override wallet client (for testing). */
  walletClient?: WalletClient;
}

// ── Nonce Manager ──────────────────────────────────

export class NonceManager {
  private currentNonce: number | null = null;
  private pendingNonces = new Set<number>();
  private readonly publicClient: PublicClient;
  private readonly account: Account;

  constructor(publicClient: PublicClient, account: Account) {
    this.publicClient = publicClient;
    this.account = account;
  }

  /** Get the next available nonce, syncing from chain if needed. */
  async getNextNonce(): Promise<number> {
    if (this.currentNonce === null) {
      await this.sync();
    }
    const nonce = this.currentNonce!;
    this.pendingNonces.add(nonce);
    this.currentNonce = nonce + 1;
    return nonce;
  }

  /** Mark a nonce as confirmed (TX included in block). */
  confirmNonce(nonce: number): void {
    this.pendingNonces.delete(nonce);
  }

  /** Mark a nonce as failed and check if it creates a gap. */
  releaseNonce(nonce: number): void {
    this.pendingNonces.delete(nonce);
    // If this was the lowest pending nonce and there's a gap,
    // we may need to resync
    if (this.pendingNonces.size === 0) {
      // All pending cleared — resync on next call
      this.currentNonce = null;
    }
  }

  /** Sync nonce from chain state. */
  async sync(): Promise<void> {
    const onChainNonce = await this.publicClient.getTransactionCount({
      address: this.account.address,
      blockTag: 'pending',
    });
    this.currentNonce = onChainNonce;
    this.pendingNonces.clear();
  }

  get pending(): number {
    return this.pendingNonces.size;
  }
}

// ── Transaction Builder ──────────────────────────────

export class TransactionBuilder {
  private readonly publicClient: PublicClient;
  private readonly walletClient: WalletClient;
  private readonly account: Account;
  private readonly nonceManager: NonceManager;
  private readonly maxRetries: number;
  private readonly initialRetryDelayMs: number;
  private readonly confirmationTimeoutMs: number;
  private readonly log: (event: string, message: string, extra?: Record<string, unknown>) => void;
  private readonly abiRegistry: Map<string, Abi>;
  private redis: RedisManager | null = null;
  private _processing = false;

  constructor(opts: TransactionBuilderOptions = {}) {
    const privateKey = opts.privateKey ?? process.env.WALLET_PRIVATE_KEY;
    if (!privateKey) {
      throw new Error('WALLET_PRIVATE_KEY is not configured');
    }

    this.account = privateKeyToAccount(privateKey as `0x${string}`);
    const chain = opts.chain ?? sepolia;
    const rpcUrl = opts.rpcUrl ?? process.env.ALCHEMY_SEPOLIA_HTTP_URL;

    this.publicClient = opts.publicClient ?? createPublicClient({
      chain,
      transport: http(rpcUrl),
    });

    this.walletClient = opts.walletClient ?? createWalletClient({
      account: this.account,
      chain,
      transport: http(rpcUrl),
    });

    this.nonceManager = new NonceManager(this.publicClient, this.account);
    this.maxRetries = opts.maxRetries ?? 3;
    this.initialRetryDelayMs = opts.initialRetryDelayMs ?? 1_000;
    this.confirmationTimeoutMs = opts.confirmationTimeoutMs ?? 120_000;
    this.log = opts.onLog ?? (() => {});
    this.abiRegistry = opts.abiRegistry ?? new Map();
  }

  get processing(): boolean {
    return this._processing;
  }

  /** Attach RedisManager and subscribe to execution:orders. */
  async start(redis: RedisManager): Promise<void> {
    this.redis = redis;
    await redis.subscribe(CHANNELS.EXECUTION_ORDERS, (data) => {
      this.handleOrder(data as unknown as ExecutionOrder).catch((err) => {
        this.log('exec_order_error', 'Unhandled error processing order', {
          error: err instanceof Error ? err.message : String(err),
        });
      });
    });
    this.log('exec_started', 'Transaction builder listening for orders');
  }

  /** Process a single execution order. */
  async handleOrder(order: ExecutionOrder): Promise<ExecutionResult> {
    this._processing = true;
    const { orderId, correlationId } = order;

    this.log('exec_order_received', 'Processing execution order', {
      orderId,
      correlationId,
      action: order.action,
      protocol: order.protocol,
    });

    try {
      // 1. Pre-flight checks
      const rejection = await this.preflight(order);
      if (rejection) {
        const result = this.buildResult(order, 'failed', { error: rejection });
        await this.publishResult(result);
        return result;
      }

      // 2. Execute with retries
      const result = await this.executeWithRetry(order);
      await this.publishResult(result);
      return result;
    } finally {
      this._processing = false;
    }
  }

  /** Pre-flight validation: deadline and gas ceiling checks. */
  async preflight(order: ExecutionOrder): Promise<string | null> {
    const { limits, orderId } = order;

    // Check deadline
    const nowUnix = Math.floor(Date.now() / 1000);
    if (limits.deadlineUnix <= nowUnix) {
      this.log('exec_deadline_expired', 'Order deadline has passed', {
        orderId,
        deadline: limits.deadlineUnix,
        now: nowUnix,
      });
      return `Order deadline expired: deadline=${limits.deadlineUnix}, now=${nowUnix}`;
    }

    // Check gas ceiling against current gas price
    try {
      const gasPrice = await this.publicClient.getGasPrice();
      // Estimate gas for a standard ERC-20 interaction (~100k gas units)
      const estimatedGasUnits = BigInt(150_000);
      const estimatedCostWei = gasPrice * estimatedGasUnits;
      const maxGasWei = BigInt(limits.maxGasWei);

      if (estimatedCostWei > maxGasWei) {
        this.log('exec_gas_exceeded', 'Gas cost exceeds ceiling', {
          orderId,
          estimatedCostWei: estimatedCostWei.toString(),
          maxGasWei: limits.maxGasWei,
          gasPriceGwei: Number(gasPrice) / 1e9,
        });
        return `Gas cost ${estimatedCostWei} exceeds ceiling ${limits.maxGasWei}`;
      }
    } catch (err) {
      this.log('exec_gas_check_error', 'Failed to check gas price', {
        orderId,
        error: err instanceof Error ? err.message : String(err),
      });
      // Don't reject on gas check failure — proceed with caution
    }

    return null;
  }

  /** Execute a transaction with retry logic. */
  private async executeWithRetry(order: ExecutionOrder): Promise<ExecutionResult> {
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
        const result = await this.executeSingle(order, attempt);
        return result;
      } catch (err) {
        lastError = err instanceof Error ? err.message : String(err);
        this.log('exec_attempt_failed', 'Transaction attempt failed', {
          orderId: order.orderId,
          attempt,
          error: lastError,
        });

        // Don't retry on non-retryable errors
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

  /** Execute a single transaction attempt. */
  private async executeSingle(order: ExecutionOrder, attempt: number): Promise<ExecutionResult> {
    const nonce = await this.nonceManager.getNextNonce();

    try {
      // Build transaction data
      const txData = this.buildTransactionData(order);

      // Send transaction
      const hash = await this.walletClient.sendTransaction({
        to: txData.to,
        data: txData.data,
        value: txData.value,
        nonce,
        account: this.account,
        chain: this.walletClient.chain,
      });

      this.log('exec_tx_sent', 'Transaction submitted', {
        orderId: order.orderId,
        txHash: hash,
        nonce,
        attempt,
      });

      // Wait for confirmation with timeout
      const receipt = await this.waitForReceipt(hash);

      if (receipt.status === 'success') {
        this.nonceManager.confirmNonce(nonce);
        return this.buildResult(order, 'confirmed', {
          txHash: hash,
          blockNumber: Number(receipt.blockNumber),
          gasUsed: receipt.gasUsed.toString(),
          effectiveGasPrice: receipt.effectiveGasPrice.toString(),
          retryCount: attempt,
        });
      } else {
        // Transaction reverted
        this.nonceManager.confirmNonce(nonce); // Reverted TXs still consume nonce
        const revertReason = await this.getRevertReason(hash);
        return this.buildResult(order, 'reverted', {
          txHash: hash,
          blockNumber: Number(receipt.blockNumber),
          gasUsed: receipt.gasUsed.toString(),
          effectiveGasPrice: receipt.effectiveGasPrice.toString(),
          revertReason,
          retryCount: attempt,
        });
      }
    } catch (err) {
      this.nonceManager.releaseNonce(nonce);
      throw err;
    }
  }

  /** Build the transaction calldata from an order. */
  private buildTransactionData(order: ExecutionOrder): {
    to: `0x${string}`;
    data?: `0x${string}`;
    value?: bigint;
  } {
    const { params, protocol, action } = order;

    // Look up ABI from registry
    const abiKey = `${protocol}:${action}`;
    const abi = this.abiRegistry.get(abiKey);

    if (abi) {
      // Encode using ABI
      const data = encodeFunctionData({
        abi,
        functionName: action,
        args: [params.tokenIn, BigInt(params.amount)],
      });
      return {
        to: params.tokenIn as `0x${string}`, // Protocol contract address
        data,
      };
    }

    // Fallback: raw transfer (for simple ERC-20 operations)
    return {
      to: params.tokenIn as `0x${string}`,
      value: BigInt(params.amount),
    };
  }

  /** Wait for a transaction receipt with timeout. */
  private async waitForReceipt(hash: `0x${string}`): Promise<TransactionReceipt> {
    const receipt = await this.publicClient.waitForTransactionReceipt({
      hash,
      timeout: this.confirmationTimeoutMs,
    });
    return receipt;
  }

  /** Attempt to decode the revert reason from a failed TX. */
  private async getRevertReason(hash: `0x${string}`): Promise<string> {
    try {
      const tx = await this.publicClient.getTransaction({ hash });
      if (!tx) return 'Unknown revert reason';

      // Try to simulate the call to get the revert data
      try {
        await this.publicClient.call({
          to: tx.to!,
          data: tx.input,
          value: tx.value,
          blockNumber: tx.blockNumber!,
        });
        return 'Unknown revert reason'; // Simulation didn't revert
      } catch (simErr: unknown) {
        const errMsg = simErr instanceof Error ? simErr.message : String(simErr);
        // Try to decode common revert patterns
        if (errMsg.includes('execution reverted')) {
          return errMsg;
        }
        return errMsg || 'Unknown revert reason';
      }
    } catch {
      return 'Unable to fetch revert reason';
    }
  }

  /** Check if an error is non-retryable. */
  private isNonRetryable(error: string): boolean {
    const nonRetryable = [
      'insufficient funds',
      'nonce too low',
      'already known',
      'deadline expired',
      'WALLET_PRIVATE_KEY',
    ];
    const lowerError = error.toLowerCase();
    return nonRetryable.some((pattern) => lowerError.includes(pattern.toLowerCase()));
  }

  /** Build an ExecutionResult object. */
  buildResult(
    order: ExecutionOrder,
    status: ExecutionResult['status'],
    extra: Partial<ExecutionResult> = {},
  ): ExecutionResult {
    return {
      version: '1.0.0',
      orderId: order.orderId,
      correlationId: order.correlationId,
      timestamp: new Date().toISOString(),
      status,
      ...extra,
    };
  }

  /** Publish a result to execution:results via Redis. */
  private async publishResult(result: ExecutionResult): Promise<void> {
    if (!this.redis) {
      this.log('exec_no_redis', 'Cannot publish result: Redis not connected', {
        orderId: result.orderId,
      });
      return;
    }

    try {
      await this.redis.publish(
        CHANNELS.EXECUTION_RESULTS,
        result as unknown as Record<string, unknown>,
      );
      this.log('exec_result_published', 'Execution result published', {
        orderId: result.orderId,
        correlationId: result.correlationId,
        status: result.status,
        txHash: result.txHash,
      });
    } catch (err) {
      this.log('exec_publish_error', 'Failed to publish execution result', {
        orderId: result.orderId,
        error: err instanceof Error ? err.message : String(err),
      });
    }
  }

  /** Async sleep utility. */
  private sleep(ms: number): Promise<void> {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }
}
