/**
 * EXEC-003: Flashbots Protect.
 *
 * Routes transactions through Flashbots Protect RPC endpoint to avoid
 * front-running and sandwich attacks:
 * - Routes through https://rpc.flashbots.net
 * - Polls for TX status until inclusion or timeout
 * - Falls back to public mempool if Flashbots unreachable
 * - Per-transaction opt-out via order config
 * - Logs latency overhead
 */

import {
  createWalletClient,
  createPublicClient,
  http,
  type WalletClient,
  type PublicClient,
  type TransactionReceipt,
  type Account,
  type Chain,
} from 'viem';
import { sepolia } from 'viem/chains';

// ── Types ──────────────────────────────────────────

export type FlashbotsStatus = 'PENDING' | 'INCLUDED' | 'FAILED' | 'CANCELLED' | 'UNKNOWN';

export interface FlashbotsStatusResponse {
  status: FlashbotsStatus;
  hash?: string;
  maxBlockNumber?: number;
  receivedTimestamp?: string;
}

export interface FlashbotsProtectOptions {
  /** Flashbots Protect RPC URL. Defaults to https://rpc.flashbots.net. */
  flashbotsRpcUrl?: string;
  /** Public RPC URL for fallback and status checks. */
  publicRpcUrl?: string;
  /** Chain. Defaults to sepolia. */
  chain?: Chain;
  /** Poll interval for TX status in ms. Defaults to 2_000. */
  pollIntervalMs?: number;
  /** Timeout for TX inclusion in ms. Defaults to 120_000. */
  timeoutMs?: number;
  /** Max retries for Flashbots submission. Defaults to 2. */
  maxRetries?: number;
  /** Structured log callback. */
  onLog?: (event: string, message: string, extra?: Record<string, unknown>) => void;
  /** Override public client (for testing). */
  publicClient?: PublicClient;
  /** Override Flashbots wallet client (for testing). */
  flashbotsClient?: WalletClient;
  /** Override public wallet client for fallback (for testing). */
  publicWalletClient?: WalletClient;
}

export interface SendTransactionParams {
  to: `0x${string}`;
  data?: `0x${string}`;
  value?: bigint;
  nonce?: number;
  account: Account;
  chain?: Chain;
  gas?: bigint;
  maxFeePerGas?: bigint;
  maxPriorityFeePerGas?: bigint;
}

export interface FlashbotsResult {
  txHash: `0x${string}`;
  receipt: TransactionReceipt | null;
  usedFlashbots: boolean;
  latencyMs: number;
  status: FlashbotsStatus;
}

// ── Flashbots Protect Manager ──────────────────────

export class FlashbotsProtectManager {
  private readonly flashbotsRpcUrl: string;
  private readonly publicClient: PublicClient;
  private readonly flashbotsClient: WalletClient;
  private readonly publicWalletClient: WalletClient;
  private readonly pollIntervalMs: number;
  private readonly timeoutMs: number;
  private readonly maxRetries: number;
  private readonly log: (event: string, message: string, extra?: Record<string, unknown>) => void;

  // Stats
  private _totalSent = 0;
  private _flashbotsSent = 0;
  private _publicFallback = 0;
  private _timeouts = 0;

  constructor(opts: FlashbotsProtectOptions = {}) {
    this.flashbotsRpcUrl = opts.flashbotsRpcUrl ?? 'https://rpc.flashbots.net';
    this.pollIntervalMs = opts.pollIntervalMs ?? 2_000;
    this.timeoutMs = opts.timeoutMs ?? 120_000;
    this.maxRetries = opts.maxRetries ?? 2;
    this.log = opts.onLog ?? (() => {});

    const chain = opts.chain ?? sepolia;
    const publicRpcUrl = opts.publicRpcUrl ?? process.env.ALCHEMY_SEPOLIA_HTTP_URL;

    this.publicClient = opts.publicClient ?? createPublicClient({
      chain,
      transport: http(publicRpcUrl),
    });

    this.flashbotsClient = opts.flashbotsClient ?? createWalletClient({
      chain,
      transport: http(this.flashbotsRpcUrl),
    });

    this.publicWalletClient = opts.publicWalletClient ?? createWalletClient({
      chain,
      transport: http(publicRpcUrl),
    });
  }

  get stats() {
    return {
      totalSent: this._totalSent,
      flashbotsSent: this._flashbotsSent,
      publicFallback: this._publicFallback,
      timeouts: this._timeouts,
    };
  }

  /**
   * Send a transaction, optionally routing through Flashbots Protect.
   * Falls back to public mempool if Flashbots fails.
   */
  async sendTransaction(
    params: SendTransactionParams,
    useFlashbots: boolean = true,
  ): Promise<FlashbotsResult> {
    this._totalSent++;
    const startMs = Date.now();

    if (!useFlashbots) {
      this.log('flashbots_opt_out', 'Flashbots opt-out, using public mempool', {
        to: params.to,
      });
      return this.sendViaPublic(params, startMs);
    }

    // Try Flashbots Protect
    for (let attempt = 0; attempt <= this.maxRetries; attempt++) {
      try {
        const txHash = await this.flashbotsClient.sendTransaction({
          to: params.to,
          data: params.data,
          value: params.value,
          nonce: params.nonce,
          account: params.account,
          chain: params.chain ?? this.flashbotsClient.chain,
          gas: params.gas,
          maxFeePerGas: params.maxFeePerGas,
          maxPriorityFeePerGas: params.maxPriorityFeePerGas,
        });

        this.log('flashbots_submitted', 'Transaction sent via Flashbots Protect', {
          txHash,
          attempt,
        });

        // Poll for inclusion
        const result = await this.pollForInclusion(txHash, startMs);
        this._flashbotsSent++;
        return result;
      } catch (err) {
        const errorMsg = err instanceof Error ? err.message : String(err);
        this.log('flashbots_submit_error', 'Flashbots submission failed', {
          attempt,
          error: errorMsg,
        });

        if (attempt === this.maxRetries) {
          this.log('flashbots_fallback', 'All Flashbots attempts failed, falling back to public mempool', {
            attempts: attempt + 1,
          });
          return this.sendViaPublic(params, startMs);
        }
      }
    }

    // Should not reach here, but fallback just in case
    return this.sendViaPublic(params, startMs);
  }

  /** Send via public mempool (fallback). */
  private async sendViaPublic(
    params: SendTransactionParams,
    startMs: number,
  ): Promise<FlashbotsResult> {
    this._publicFallback++;

    try {
      const txHash = await this.publicWalletClient.sendTransaction({
        to: params.to,
        data: params.data,
        value: params.value,
        nonce: params.nonce,
        account: params.account,
        chain: params.chain ?? this.publicWalletClient.chain,
        gas: params.gas,
        maxFeePerGas: params.maxFeePerGas,
        maxPriorityFeePerGas: params.maxPriorityFeePerGas,
      });

      this.log('public_submitted', 'Transaction sent via public mempool', { txHash });

      const receipt = await this.waitForReceipt(txHash);
      const latencyMs = Date.now() - startMs;

      this.log('public_confirmed', 'Public TX confirmed', {
        txHash,
        latencyMs,
        blockNumber: receipt ? Number(receipt.blockNumber) : undefined,
      });

      return {
        txHash,
        receipt,
        usedFlashbots: false,
        latencyMs,
        status: receipt ? 'INCLUDED' : 'PENDING',
      };
    } catch (err) {
      const latencyMs = Date.now() - startMs;
      const errorMsg = err instanceof Error ? err.message : String(err);
      this.log('public_send_error', 'Public mempool send failed', { error: errorMsg });

      return {
        txHash: '0x0' as `0x${string}`,
        receipt: null,
        usedFlashbots: false,
        latencyMs,
        status: 'FAILED',
      };
    }
  }

  /** Poll for Flashbots TX inclusion or timeout. */
  private async pollForInclusion(
    txHash: `0x${string}`,
    startMs: number,
  ): Promise<FlashbotsResult> {
    const deadline = startMs + this.timeoutMs;

    while (Date.now() < deadline) {
      try {
        const receipt = await this.publicClient.getTransactionReceipt({ hash: txHash });
        if (receipt) {
          const latencyMs = Date.now() - startMs;
          this.log('flashbots_included', 'Flashbots TX included', {
            txHash,
            blockNumber: Number(receipt.blockNumber),
            latencyMs,
          });
          return {
            txHash,
            receipt,
            usedFlashbots: true,
            latencyMs,
            status: 'INCLUDED',
          };
        }
      } catch {
        // Receipt not available yet — keep polling
      }

      await this.sleep(this.pollIntervalMs);
    }

    // Timeout
    this._timeouts++;
    const latencyMs = Date.now() - startMs;
    this.log('flashbots_timeout', 'Flashbots TX not included within timeout', {
      txHash,
      timeoutMs: this.timeoutMs,
      latencyMs,
    });

    return {
      txHash,
      receipt: null,
      usedFlashbots: true,
      latencyMs,
      status: 'PENDING',
    };
  }

  /** Wait for a receipt with timeout. */
  private async waitForReceipt(txHash: `0x${string}`): Promise<TransactionReceipt | null> {
    try {
      return await this.publicClient.waitForTransactionReceipt({
        hash: txHash,
        timeout: this.timeoutMs,
      });
    } catch {
      return null;
    }
  }

  private sleep(ms: number): Promise<void> {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }
}
