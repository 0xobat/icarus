/**
 * EXEC-001: viem transaction builder.
 *
 * Consumes orders from execution:orders, constructs and submits
 * Ethereum transactions via Safe wallet, enforces gas/slippage/deadline
 * parameters, and publishes results to execution:results.
 */

import {
  createPublicClient,
  http,
  type PublicClient,
  type TransactionReceipt,
  type Chain,
  type Address,
  type Hex,
} from 'viem';
import { resolveChain } from '../config.js';
import { type RedisManager, CHANNELS } from '../redis/client.js';
import type { EventReporter } from './event-reporter.js';

// ── Types ──────────────────────────────────────────

/** Interface for Safe wallet operations used by TransactionBuilder. */
export interface SafeWalletLike {
  readonly address: Address;
  readonly signerAddress: Address;
  executeTransaction(tx: {
    to: Address; value?: bigint; data?: Hex; operation?: number;
  }): Promise<{ hash: `0x${string}`; receipt: TransactionReceipt }>;
  executeBatch(txs: Array<{
    to: Address; value?: bigint; data?: Hex; operation?: number;
  }>): Promise<{ hash: `0x${string}`; receipt: TransactionReceipt }>;
  validateOrder(target: Address, amountWei: bigint): Promise<{
    allowed: boolean; reason?: string;
  }> | { allowed: boolean; reason?: string };
  recordSpend(amountWei: bigint): Promise<void> | void;
}

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
    [key: string]: unknown;
  };
  limits: {
    maxGasWei: string;
    maxSlippageBps: number;
    deadlineUnix: number;
  };
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

/** Common interface for protocol-specific transaction adapters. */
export interface ProtocolAdapter {
  buildTransaction(
    action: string,
    params: ExecutionOrder['params'],
    limits: ExecutionOrder['limits'],
  ): Promise<{ to: Address; data?: Hex; value?: bigint }>;
}

export interface TransactionBuilderOptions {
  /** Safe wallet for transaction execution. Required at runtime. */
  safeWallet?: SafeWalletLike;
  /** RPC URL for read-only operations. Defaults to env ALCHEMY_SEPOLIA_HTTP_URL. */
  rpcUrl?: string;
  /** viem chain. Defaults to sepolia. */
  chain?: Chain;
  /** Max retry attempts. Defaults to 3. */
  maxRetries?: number;
  /** Initial retry delay in ms. Defaults to 1000. */
  initialRetryDelayMs?: number;
  /** TX confirmation timeout in ms. Defaults to 120_000. */
  confirmationTimeoutMs?: number;
  /** Structured log callback. */
  onLog?: (event: string, message: string, extra?: Record<string, unknown>) => void;
  /** Protocol adapter registry for order routing. */
  adapters?: Map<string, ProtocolAdapter>;
  /** Event reporter for consolidated result publishing. */
  reporter?: EventReporter;
  /** Override public client (for testing). */
  publicClient?: PublicClient;
}

/** Error indicating a TX was submitted to the mempool but confirmation failed (e.g., timeout). */
class TxSubmittedError extends Error {
  constructor(public readonly txHash: string, message: string) {
    super(message);
    this.name = 'TxSubmittedError';
  }
}

// ── Transaction Builder ──────────────────────────────

/** Builds and submits Ethereum transactions from execution orders via Safe wallet. */
export class TransactionBuilder {
  private readonly publicClient: PublicClient;
  private readonly _safeWallet: SafeWalletLike | null;
  private readonly maxRetries: number;
  private readonly initialRetryDelayMs: number;
  private readonly confirmationTimeoutMs: number;
  private readonly log: (event: string, message: string, extra?: Record<string, unknown>) => void;
  private readonly adapters: Map<string, ProtocolAdapter>;
  private readonly reporter: EventReporter | null;
  private redis: RedisManager | null = null;
  private _processing = false;
  private _orderQueue: Array<{ data: ExecutionOrder }> = [];
  private _processingOrder = false;
  private _stopping = false;

  /** Safe wallet accessor. Throws if not configured. */
  private get safeWallet(): SafeWalletLike {
    if (!this._safeWallet) {
      throw new Error('safeWallet is required');
    }
    return this._safeWallet;
  }

  constructor(opts: TransactionBuilderOptions = {}) {
    this._safeWallet = opts.safeWallet ?? null;
    const chain = opts.chain ?? resolveChain();
    const rpcUrl = opts.rpcUrl ?? process.env.ALCHEMY_SEPOLIA_HTTP_URL;

    this.publicClient = opts.publicClient ?? createPublicClient({
      chain,
      transport: http(rpcUrl),
    });

    this.maxRetries = opts.maxRetries ?? 3;
    this.initialRetryDelayMs = opts.initialRetryDelayMs ?? 1_000;
    this.confirmationTimeoutMs = opts.confirmationTimeoutMs ?? 120_000;
    this.log = opts.onLog ?? (() => {});
    this.adapters = opts.adapters ?? new Map();
    this.reporter = opts.reporter ?? null;
  }

  /** Check if an order is currently being processed. */
  get processing(): boolean {
    return this._processing;
  }

  /** Attach RedisManager and subscribe to execution:orders. */
  async start(redis: RedisManager): Promise<void> {
    this.redis = redis;
    await redis.subscribe(CHANNELS.EXECUTION_ORDERS, (data) => {
      void this._enqueueOrder(data as unknown as ExecutionOrder);
    });
    this.log('exec_started', 'Transaction builder listening for orders');
  }

  /** Enqueue an order for serial processing. Prevents concurrent execution and spending limit races. */
  private async _enqueueOrder(data: ExecutionOrder): Promise<void> {
    if (this._stopping) {
      this.log('order_rejected', 'Order rejected — shutting down', { orderId: data.orderId });
      return;
    }
    this._orderQueue.push({ data });
    if (!this._processingOrder) {
      await this._processNextOrder();
    }
  }

  /** Process orders one at a time from the queue. */
  private async _processNextOrder(): Promise<void> {
    if (this._processingOrder || this._orderQueue.length === 0) return;
    this._processingOrder = true;
    try {
      const next = this._orderQueue.shift()!;
      await this.handleOrder(next.data);
    } catch (err) {
      this.log('order_error', `Order processing failed: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      this._processingOrder = false;
      if (this._orderQueue.length > 0) {
        // Process next order — don't await, let it run in the next microtask
        void this._processNextOrder();
      }
    }
  }

  /** Signal shutdown and wait for the current in-flight order to complete. */
  async gracefulStop(): Promise<void> {
    this._stopping = true;
    const maxWait = 120_000; // 2 minutes
    const start = Date.now();
    while (this._processingOrder && Date.now() - start < maxWait) {
      await new Promise(r => setTimeout(r, 500));
    }
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
      // 1. Pre-flight checks (deadline, gas ceiling)
      const rejection = await this.preflight(order);
      if (rejection) {
        const result = this.buildResult(order, 'failed', { error: rejection });
        await this.emitResult(order, result);
        return result;
      }

      // 2. Allowlist + spending limit check via Safe wallet
      const target = await this.resolveTarget(order);
      const amount = BigInt(order.params.amount);
      const validation = await this.safeWallet.validateOrder(target, amount);
      if (!validation.allowed) {
        const result = this.buildResult(order, 'failed', {
          error: `Order validation failed: ${validation.reason ?? 'not allowed'}`,
        });
        await this.emitResult(order, result);
        return result;
      }

      // 3. Execute with retries
      const result = await this.executeWithRetry(order);
      await this.emitResult(order, result);

      // 4. Record spend on success
      if (result.status === 'confirmed') {
        await this.safeWallet.recordSpend(BigInt(order.params.amount));
      }

      return result;
    } finally {
      this._processing = false;
    }
  }

  /** Pre-flight validation: deadline and gas ceiling checks. */
  async preflight(order: ExecutionOrder): Promise<string | null> {
    const { limits, orderId, params } = order;

    // Check recipient — must be a valid Ethereum address if provided
    // Actions that send funds (supply, mint_lp, swap) require an explicit recipient
    const ACTIONS_REQUIRING_RECIPIENT = new Set(['supply', 'withdraw', 'mint_lp', 'burn_lp', 'swap', 'stake', 'unstake']);
    if (ACTIONS_REQUIRING_RECIPIENT.has(order.action)) {
      const recipient = params.recipient;
      if (!recipient || typeof recipient !== 'string' || !/^0x[0-9a-fA-F]{40}$/.test(recipient)) {
        this.log('exec_invalid_recipient', 'Order missing or invalid recipient address', {
          orderId,
          action: order.action,
          recipient: recipient ?? 'undefined',
        });
        return `Invalid or missing recipient address for action '${order.action}'. Got: ${recipient ?? 'undefined'}`;
      }
    }

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
    let lastTxHash: string | undefined;

    for (let attempt = 0; attempt <= this.maxRetries; attempt++) {
      if (attempt > 0) {
        // If a previous attempt obtained a TX hash, do NOT retry — the TX may still
        // land on-chain. Retrying would submit a second TX, causing a double-spend.
        if (lastTxHash) {
          this.log('exec_no_retry_tx_pending', 'TX hash obtained on previous attempt — not retrying to prevent double-spend', {
            orderId: order.orderId,
            txHash: lastTxHash,
            attempt,
          });
          break;
        }
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
        // If the error suggests a TX was submitted (e.g., receipt timeout),
        // mark it so we don't retry and risk a double-spend
        if (this.isTxPossiblySubmitted(lastError)) {
          lastTxHash = 'unknown-pending';
        }
        if (err instanceof TxSubmittedError) {
          lastTxHash = err.txHash;
        }
        this.log('exec_attempt_failed', 'Transaction attempt failed', {
          orderId: order.orderId,
          attempt,
          error: lastError,
          txHash: lastTxHash,
        });

        // Don't retry on non-retryable errors
        if (this.isNonRetryable(lastError)) {
          break;
        }
      }
    }

    return this.buildResult(order, lastTxHash ? 'timeout' : 'failed', {
      error: lastError,
      txHash: lastTxHash,
      retryCount: this.maxRetries,
    });
  }

  /** Execute a single transaction attempt via Safe wallet. */
  private async executeSingle(order: ExecutionOrder, attempt: number): Promise<ExecutionResult> {
    // Build transaction data via adapter routing
    const txData = await this.buildTransactionData(order);

    // Execute through Safe wallet
    return this.executeViaSafe(order, txData, attempt);
  }

  /** Execute a transaction through the Safe wallet. */
  private async executeViaSafe(
    order: ExecutionOrder,
    txData: { to: `0x${string}`; data?: `0x${string}`; value?: bigint },
    attempt: number,
  ): Promise<ExecutionResult> {
    const { hash, receipt } = await this.safeWallet.executeTransaction({
      to: txData.to,
      value: txData.value,
      data: txData.data,
    });

    this.log('exec_tx_sent', 'Transaction submitted via Safe wallet', {
      orderId: order.orderId,
      txHash: hash,
      attempt,
    });

    return this.buildResultFromReceipt(order, hash, receipt, attempt);
  }

  /** Build an ExecutionResult from a transaction receipt. */
  private async buildResultFromReceipt(
    order: ExecutionOrder,
    hash: `0x${string}`,
    receipt: TransactionReceipt,
    attempt: number,
  ): Promise<ExecutionResult> {
    if (receipt.status === 'success') {
      return this.buildResult(order, 'confirmed', {
        txHash: hash,
        blockNumber: Number(receipt.blockNumber),
        gasUsed: receipt.gasUsed.toString(),
        effectiveGasPrice: receipt.effectiveGasPrice.toString(),
        retryCount: attempt,
      });
    } else {
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
  }

  /** Resolve the target address for order validation. */
  private async resolveTarget(order: ExecutionOrder): Promise<Address> {
    const adapter = this.adapters.get(order.protocol);
    if (adapter) {
      const txData = await adapter.buildTransaction(order.action, order.params, order.limits);
      return txData.to;
    }
    return order.params.tokenIn as Address;
  }

  /** Build the transaction calldata from an order via adapter or fallback. */
  private async buildTransactionData(order: ExecutionOrder): Promise<{
    to: `0x${string}`;
    data?: `0x${string}`;
    value?: bigint;
  }> {
    const { params, protocol, action } = order;

    // 1. Try protocol adapter
    const adapter = this.adapters.get(protocol);
    if (adapter) {
      this.log('exec_adapter_routed', 'Routing order through protocol adapter', {
        orderId: order.orderId,
        protocol,
        action,
      });
      const result = await adapter.buildTransaction(action, params, order.limits);
      return {
        to: result.to as `0x${string}`,
        data: result.data as `0x${string}` | undefined,
        value: result.value,
      };
    }

    // 2. No fallback — unknown protocols must fail to prevent burning ETH
    throw new Error(`No adapter registered for protocol: ${protocol}`);
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

  /** Check if an error suggests a TX was submitted but receipt was not obtained. */
  private isTxPossiblySubmitted(error: string): boolean {
    const lowerError = error.toLowerCase();
    // Only match receipt/confirmation timeouts — NOT connection timeouts.
    // "connection timeout" means we never reached the RPC, so no TX was submitted.
    // "receipt timeout" / "waitForTransactionReceipt" means TX was sent but confirmation wasn't received.
    const receiptPatterns = [
      'waitfortransactionreceipt',
      'transaction receipt',
      'receipt timeout',
      'confirmation timeout',
    ];
    return receiptPatterns.some((p) => lowerError.includes(p));
  }

  /** Check if an error is non-retryable. */
  private isNonRetryable(error: string): boolean {
    const nonRetryable = [
      'insufficient funds',
      'nonce too low',
      'already known',
      'deadline expired',
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

  /** Emit result via reporter (preferred) or direct publish (fallback). */
  private async emitResult(order: ExecutionOrder, result: ExecutionResult): Promise<void> {
    if (this.reporter) {
      try {
        if (result.status === 'confirmed') {
          await this.reporter.reportConfirmed(order, {
            transactionHash: result.txHash as `0x${string}`,
            blockNumber: BigInt(result.blockNumber ?? 0),
            gasUsed: BigInt(result.gasUsed ?? '0'),
            effectiveGasPrice: BigInt(result.effectiveGasPrice ?? '0'),
            status: 'success',
          } as unknown as import('viem').TransactionReceipt, {
            retryCount: result.retryCount,
          });
        } else if (result.status === 'reverted') {
          await this.reporter.reportReverted(order, {
            transactionHash: result.txHash as `0x${string}`,
            blockNumber: BigInt(result.blockNumber ?? 0),
            gasUsed: BigInt(result.gasUsed ?? '0'),
            effectiveGasPrice: BigInt(result.effectiveGasPrice ?? '0'),
            status: 'reverted',
          } as unknown as import('viem').TransactionReceipt, result.retryCount);
        } else {
          await this.reporter.reportFailed(order, result.error ?? 'Unknown error', result.retryCount);
        }
        return;
      } catch (err) {
        this.log('exec_reporter_error', 'Reporter failed, falling back to direct publish', {
          orderId: result.orderId,
          error: err instanceof Error ? err.message : String(err),
        });
      }
    }
    await this.publishResult(result);
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
