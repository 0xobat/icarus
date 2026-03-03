/**
 * EXEC-003: Flashbots Protect RPC routing.
 *
 * Thin RPC routing layer that submits signed transactions to the
 * Flashbots Protect endpoint instead of the public mempool.
 * Falls back to the standard publicClient if Flashbots is unreachable.
 */

import {
  createPublicClient,
  http,
  type Hex,
  type PublicClient,
  type TransactionReceipt,
} from 'viem';
import { sepolia } from 'viem/chains';

// ── Types ──────────────────────────────────────────

/** Status of a transaction submitted via Flashbots Protect. */
export type FlashbotsStatus = 'pending' | 'included' | 'failed' | 'unknown';

export interface FlashbotsProtectOptions {
  /** Flashbots Protect RPC URL. */
  flashbotsRpcUrl: string;
  /** Fallback public RPC client for when Flashbots is unreachable. */
  fallbackClient: PublicClient;
  /** Max retries before falling back to public mempool. Defaults to 2. */
  maxRetries?: number;
  /** Status poll interval in ms. Defaults to 4000. */
  pollIntervalMs?: number;
  /** Status poll timeout in ms. Defaults to 120_000. */
  pollTimeoutMs?: number;
  /** Structured log callback. */
  onLog?: (event: string, message: string, extra?: Record<string, unknown>) => void;
  /** Override Flashbots client (for testing). */
  flashbotsClient?: PublicClient;
}

// ── FlashbotsProtectManager ────────────────────────

/** Routes signed transactions through the Flashbots Protect RPC endpoint. */
export class FlashbotsProtectManager {
  private readonly flashbotsClient: PublicClient;
  private readonly fallbackClient: PublicClient;
  private readonly maxRetries: number;
  private readonly pollIntervalMs: number;
  private readonly pollTimeoutMs: number;
  private readonly log: (event: string, message: string, extra?: Record<string, unknown>) => void;
  private readonly rpcUrl: string;

  // Stats
  private _sent = 0;
  private _included = 0;
  private _fallbacks = 0;
  private _failures = 0;

  constructor(opts: FlashbotsProtectOptions) {
    this.rpcUrl = opts.flashbotsRpcUrl;
    this.fallbackClient = opts.fallbackClient;
    this.maxRetries = opts.maxRetries ?? 2;
    this.pollIntervalMs = opts.pollIntervalMs ?? 4_000;
    this.pollTimeoutMs = opts.pollTimeoutMs ?? 120_000;
    this.log = opts.onLog ?? (() => {});

    this.flashbotsClient = opts.flashbotsClient ?? createPublicClient({
      chain: sepolia,
      transport: http(this.rpcUrl),
    });
  }

  /** Get routing statistics. */
  get stats() {
    return {
      sent: this._sent,
      included: this._included,
      fallbacks: this._fallbacks,
      failures: this._failures,
    };
  }

  /**
   * Submit a signed transaction to Flashbots Protect.
   * Falls back to the public mempool if Flashbots is unreachable.
   * @returns Transaction hash and receipt.
   */
  async sendTransaction(
    signedTx: Hex,
    orderId: string,
  ): Promise<{ hash: Hex; receipt: TransactionReceipt; usedFallback: boolean }> {
    const startMs = Date.now();

    // Try Flashbots Protect first
    for (let attempt = 0; attempt <= this.maxRetries; attempt++) {
      try {
        const hash = await this.sendRawToFlashbots(signedTx);
        this._sent++;

        this.log('flashbots_tx_sent', 'Transaction submitted to Flashbots Protect', {
          orderId,
          txHash: hash,
          attempt,
        });

        // Poll for inclusion
        const receipt = await this.pollForInclusion(hash, orderId);
        const latencyMs = Date.now() - startMs;

        this._included++;
        this.log('flashbots_tx_included', 'Transaction included via Flashbots Protect', {
          orderId,
          txHash: hash,
          latencyMs,
          blockNumber: Number(receipt.blockNumber),
        });

        return { hash, receipt, usedFallback: false };
      } catch (err) {
        const errMsg = err instanceof Error ? err.message : String(err);
        this.log('flashbots_attempt_failed', 'Flashbots Protect attempt failed', {
          orderId,
          attempt,
          error: errMsg,
        });

        if (attempt < this.maxRetries) {
          continue;
        }
      }
    }

    // Fallback to public mempool
    this._fallbacks++;
    this.log('flashbots_fallback', 'Falling back to public mempool after Flashbots failure', {
      orderId,
      alert: true,
    });

    return this.sendViaFallback(signedTx, orderId, startMs);
  }

  /**
   * Check transaction status via Flashbots Protect.
   * @returns Current status of the transaction.
   */
  async getTransactionStatus(txHash: Hex): Promise<FlashbotsStatus> {
    try {
      const receipt = await this.flashbotsClient.getTransactionReceipt({ hash: txHash });
      if (receipt) {
        return receipt.status === 'success' ? 'included' : 'failed';
      }
      return 'pending';
    } catch {
      return 'unknown';
    }
  }

  /** Submit raw transaction to the Flashbots Protect RPC. */
  private async sendRawToFlashbots(signedTx: Hex): Promise<Hex> {
    const hash = await this.flashbotsClient.request({
      method: 'eth_sendRawTransaction',
      params: [signedTx],
    });
    return hash as Hex;
  }

  /** Poll the Flashbots client for transaction receipt until inclusion or timeout. */
  private async pollForInclusion(
    txHash: Hex,
    orderId: string,
  ): Promise<TransactionReceipt> {
    const deadline = Date.now() + this.pollTimeoutMs;

    while (Date.now() < deadline) {
      try {
        const receipt = await this.flashbotsClient.getTransactionReceipt({ hash: txHash });
        if (receipt) {
          return receipt;
        }
      } catch {
        // Receipt not available yet — keep polling
      }

      this.log('flashbots_poll', 'Polling Flashbots tx status', {
        orderId,
        txHash,
        remainingMs: deadline - Date.now(),
      });

      await this.sleep(this.pollIntervalMs);
    }

    throw new Error(`Flashbots transaction ${txHash} timed out after ${this.pollTimeoutMs}ms`);
  }

  /** Send via fallback public client. */
  private async sendViaFallback(
    signedTx: Hex,
    orderId: string,
    startMs: number,
  ): Promise<{ hash: Hex; receipt: TransactionReceipt; usedFallback: boolean }> {
    const hash = await this.fallbackClient.request({
      method: 'eth_sendRawTransaction',
      params: [signedTx],
    }) as Hex;

    this.log('flashbots_fallback_sent', 'Transaction submitted to public mempool', {
      orderId,
      txHash: hash,
    });

    const receipt = await this.fallbackClient.waitForTransactionReceipt({
      hash,
      timeout: this.pollTimeoutMs,
    });

    const latencyMs = Date.now() - startMs;
    this.log('flashbots_fallback_included', 'Transaction included via public mempool', {
      orderId,
      txHash: hash,
      latencyMs,
      blockNumber: Number(receipt.blockNumber),
    });

    return { hash, receipt, usedFallback: true };
  }

  /** Async sleep utility. */
  private sleep(ms: number): Promise<void> {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }
}
