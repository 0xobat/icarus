/**
 * EXEC-010: Event reporter.
 *
 * Publishes TX results to execution:results (schema-validated via RedisManager):
 * - Every TX gets a result: success or failure
 * - Includes: TX hash, status, fill price, gas used, block number, timestamp
 * - Decoded revert reasons for failures
 * - Conforms to execution-results.schema.json
 */

import {
  type PublicClient,
  type TransactionReceipt,
  createPublicClient,
  http,
  type Chain,
} from 'viem';
import { resolveChain } from '../config.js';
import { type RedisManager, CHANNELS } from '../redis/client.js';
import { type ExecutionOrder, type ExecutionResult } from './transaction-builder.js';

// ── Types ──────────────────────────────────────────

export interface EventReporterOptions {
  /** RPC URL for receipt lookups. */
  rpcUrl?: string;
  /** Chain. Defaults to sepolia. */
  chain?: Chain;
  /** Structured log callback. */
  onLog?: (event: string, message: string, extra?: Record<string, unknown>) => void;
  /** Override public client (for testing). */
  publicClient?: PublicClient;
}

export interface ReportResult {
  published: boolean;
  result: ExecutionResult;
}

// ── Event Reporter ──────────────────────────────────

/** Publishes transaction results to the execution:results Redis channel. */
export class EventReporter {
  private redis: RedisManager | null = null;
  private readonly publicClient: PublicClient;
  private readonly log: (event: string, message: string, extra?: Record<string, unknown>) => void;

  // Stats
  private _reported = 0;
  private _errors = 0;
  private _confirmed = 0;
  private _failed = 0;
  private _reverted = 0;
  private _timeouts = 0;

  constructor(opts: EventReporterOptions = {}) {
    this.log = opts.onLog ?? (() => {});

    const chain = opts.chain ?? resolveChain();
    const rpcUrl = opts.rpcUrl ?? process.env.ALCHEMY_SEPOLIA_HTTP_URL;

    this.publicClient = opts.publicClient ?? createPublicClient({
      chain,
      transport: http(rpcUrl),
    });
  }

  /** Attach RedisManager for publishing. */
  attach(redis: RedisManager): void {
    this.redis = redis;
    this.log('reporter_attached', 'Event reporter attached to Redis');
  }

  /** Get reporting statistics. */
  get stats() {
    return {
      reported: this._reported,
      errors: this._errors,
      confirmed: this._confirmed,
      failed: this._failed,
      reverted: this._reverted,
      timeouts: this._timeouts,
    };
  }

  /**
   * Report a successful TX confirmation.
   */
  async reportConfirmed(
    order: ExecutionOrder,
    receipt: TransactionReceipt,
    extra?: { fillPrice?: string; amountOut?: string; retryCount?: number },
  ): Promise<ReportResult> {
    const result: ExecutionResult = {
      version: '1.0.0',
      orderId: order.orderId,
      correlationId: order.correlationId,
      timestamp: new Date().toISOString(),
      status: 'confirmed',
      txHash: receipt.transactionHash,
      blockNumber: Number(receipt.blockNumber),
      gasUsed: receipt.gasUsed.toString(),
      effectiveGasPrice: receipt.effectiveGasPrice.toString(),
      ...(extra?.fillPrice && { fillPrice: extra.fillPrice }),
      ...(extra?.amountOut && { amountOut: extra.amountOut }),
      ...(extra?.retryCount !== undefined && { retryCount: extra.retryCount }),
    };

    this._confirmed++;
    return this.publishResult(result);
  }

  /**
   * Report a TX failure (never reached chain or rejected).
   */
  async reportFailed(
    order: ExecutionOrder,
    error: string,
    retryCount?: number,
  ): Promise<ReportResult> {
    const result: ExecutionResult = {
      version: '1.0.0',
      orderId: order.orderId,
      correlationId: order.correlationId,
      timestamp: new Date().toISOString(),
      status: 'failed',
      error,
      ...(retryCount !== undefined && { retryCount }),
    };

    this._failed++;
    return this.publishResult(result);
  }

  /**
   * Report a reverted TX with decoded revert reason.
   */
  async reportReverted(
    order: ExecutionOrder,
    receipt: TransactionReceipt,
    retryCount?: number,
  ): Promise<ReportResult> {
    const revertReason = await this.decodeRevertReason(receipt.transactionHash);

    const result: ExecutionResult = {
      version: '1.0.0',
      orderId: order.orderId,
      correlationId: order.correlationId,
      timestamp: new Date().toISOString(),
      status: 'reverted',
      txHash: receipt.transactionHash,
      blockNumber: Number(receipt.blockNumber),
      gasUsed: receipt.gasUsed.toString(),
      effectiveGasPrice: receipt.effectiveGasPrice.toString(),
      revertReason,
      ...(retryCount !== undefined && { retryCount }),
    };

    this._reverted++;
    return this.publishResult(result);
  }

  /**
   * Report a TX timeout (not included in block within deadline).
   */
  async reportTimeout(
    order: ExecutionOrder,
    txHash?: string,
    retryCount?: number,
  ): Promise<ReportResult> {
    const result: ExecutionResult = {
      version: '1.0.0',
      orderId: order.orderId,
      correlationId: order.correlationId,
      timestamp: new Date().toISOString(),
      status: 'timeout',
      ...(txHash && { txHash }),
      ...(retryCount !== undefined && { retryCount }),
    };

    this._timeouts++;
    return this.publishResult(result);
  }

  /**
   * Report from a receipt — auto-detects confirmed vs reverted.
   */
  async reportFromReceipt(
    order: ExecutionOrder,
    receipt: TransactionReceipt,
    extra?: { fillPrice?: string; amountOut?: string; retryCount?: number },
  ): Promise<ReportResult> {
    if (receipt.status === 'success') {
      return this.reportConfirmed(order, receipt, extra);
    } else {
      return this.reportReverted(order, receipt, extra?.retryCount);
    }
  }

  // ── Internal ──────────────────────────────────────

  /** Publish a result to execution:results. */
  private async publishResult(result: ExecutionResult): Promise<ReportResult> {
    this._reported++;

    if (!this.redis) {
      this._errors++;
      this.log('reporter_no_redis', 'Cannot publish result: Redis not attached', {
        orderId: result.orderId,
        status: result.status,
      });
      return { published: false, result };
    }

    try {
      await this.redis.publish(
        CHANNELS.EXECUTION_RESULTS,
        result as unknown as Record<string, unknown>,
      );

      this.log('reporter_published', 'Execution result published', {
        orderId: result.orderId,
        correlationId: result.correlationId,
        status: result.status,
        txHash: result.txHash,
        blockNumber: result.blockNumber,
      });

      return { published: true, result };
    } catch (err) {
      this._errors++;
      this.log('reporter_error', 'Failed to publish execution result', {
        orderId: result.orderId,
        error: err instanceof Error ? err.message : String(err),
      });
      return { published: false, result };
    }
  }

  /** Decode revert reason from a reverted transaction. */
  private async decodeRevertReason(txHash: string): Promise<string> {
    try {
      const tx = await this.publicClient.getTransaction({
        hash: txHash as `0x${string}`,
      });

      if (!tx || !tx.to) return 'Unknown revert reason';

      try {
        await this.publicClient.call({
          to: tx.to,
          data: tx.input,
          value: tx.value,
          blockNumber: tx.blockNumber!,
        });
        return 'Unknown revert reason';
      } catch (simErr: unknown) {
        const errMsg = simErr instanceof Error ? simErr.message : String(simErr);

        // Extract revert reason from common patterns
        const revertMatch = errMsg.match(/execution reverted:?\s*(.*)/i);
        if (revertMatch) {
          return revertMatch[1] || 'execution reverted';
        }

        // Try to decode Error(string)
        const errorDataMatch = errMsg.match(/0x08c379a0[0-9a-fA-F]*/);
        if (errorDataMatch) {
          try {
            const decoded = Buffer.from(errorDataMatch[0].slice(10), 'hex');
            // Skip the 32-byte offset, read the 32-byte length, then extract the string
            const length = Number('0x' + decoded.slice(32, 64).toString('hex'));
            return decoded.slice(64, 64 + length).toString('utf8') || 'Unknown revert';
          } catch {
            return errMsg;
          }
        }

        return errMsg || 'Unknown revert reason';
      }
    } catch {
      return 'Unable to fetch revert reason';
    }
  }
}
