/**
 * RISK-006: Contract allowlist.
 *
 * Config-file-based allowlist for transaction target validation:
 * - Validates target contract address before every TX
 * - Rejects non-allowlisted contracts with error to execution:results
 * - Separate allowlists per chain
 * - Requires restart to update (intentional friction)
 * - Includes default entries for Aave V3, Uniswap V3, Lido contracts
 */

import { readFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { type Address } from 'viem';
import { type RedisManager, CHANNELS } from '../redis/client.js';
import { type ExecutionOrder, type ExecutionResult } from '../execution/transaction-builder.js';

// ── Types ──────────────────────────────────────────

export type ChainId = 'ethereum' | 'arbitrum' | 'base' | 'sepolia';

export interface AllowlistEntry {
  address: string;
  name: string;
  protocol: string;
}

export interface AllowlistConfig {
  version: string;
  updatedAt: string;
  chains: Record<ChainId, AllowlistEntry[]>;
}

export interface AllowlistCheckResult {
  allowed: boolean;
  entry?: AllowlistEntry;
  reason?: string;
}

export interface ContractAllowlistOptions {
  /** Path to the allowlist JSON config file. */
  configPath?: string;
  /** Inline config (for testing, overrides configPath). */
  config?: AllowlistConfig;
  /** Structured log callback. */
  onLog?: (event: string, message: string, extra?: Record<string, unknown>) => void;
}

// ── Default Allowlist ──────────────────────────────

const DEFAULT_CONFIG: AllowlistConfig = {
  version: '1.0.0',
  updatedAt: new Date().toISOString(),
  chains: {
    ethereum: [
      { address: '0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2', name: 'Aave V3 Pool', protocol: 'aave_v3' },
      { address: '0xE592427A0AEce92De3Edee1F18E0157C05861564', name: 'Uniswap V3 SwapRouter', protocol: 'uniswap_v3' },
      { address: '0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45', name: 'Uniswap V3 SwapRouter02', protocol: 'uniswap_v3' },
      { address: '0xae7ab96520DE3A18E5e111B5EaAb095312D7fE84', name: 'Lido stETH', protocol: 'lido' },
      { address: '0x7f39C581F595B53c5cb19bD0b3f8dA6c935E2Ca0', name: 'Lido wstETH', protocol: 'lido' },
    ],
    arbitrum: [
      { address: '0x794a61358D6845594F94dc1DB02A252b5b4814aD', name: 'Aave V3 Pool', protocol: 'aave_v3' },
      { address: '0xE592427A0AEce92De3Edee1F18E0157C05861564', name: 'Uniswap V3 SwapRouter', protocol: 'uniswap_v3' },
      { address: '0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45', name: 'Uniswap V3 SwapRouter02', protocol: 'uniswap_v3' },
    ],
    base: [
      { address: '0xA238Dd80C259a72e81d7e4664a9801593F98d1c5', name: 'Aave V3 Pool', protocol: 'aave_v3' },
      { address: '0x2626664c2603336E57B271c5C0b26F421741e481', name: 'Uniswap V3 SwapRouter02', protocol: 'uniswap_v3' },
    ],
    sepolia: [
      { address: '0x6Ae43d3271ff6888e7Fc43Fd7321a503ff738951', name: 'Aave V3 Pool (Sepolia)', protocol: 'aave_v3' },
      { address: '0xE592427A0AEce92De3Edee1F18E0157C05861564', name: 'Uniswap V3 SwapRouter (Sepolia)', protocol: 'uniswap_v3' },
    ],
  },
};

// ── Contract Allowlist ──────────────────────────────

export class ContractAllowlist {
  private readonly config: AllowlistConfig;
  private readonly lookupMap: Map<string, AllowlistEntry>; // chain:lowercaseAddress -> entry
  private readonly log: (event: string, message: string, extra?: Record<string, unknown>) => void;
  private redis: RedisManager | null = null;

  // Stats
  private _checked = 0;
  private _allowed = 0;
  private _rejected = 0;

  constructor(opts: ContractAllowlistOptions = {}) {
    this.log = opts.onLog ?? (() => {});

    // Load config: inline > file > default
    if (opts.config) {
      this.config = opts.config;
    } else if (opts.configPath) {
      try {
        const raw = readFileSync(opts.configPath, 'utf-8');
        this.config = JSON.parse(raw) as AllowlistConfig;
      } catch (err) {
        this.log('allowlist_config_error', 'Failed to load allowlist config, using defaults', {
          path: opts.configPath,
          error: err instanceof Error ? err.message : String(err),
        });
        this.config = DEFAULT_CONFIG;
      }
    } else {
      this.config = DEFAULT_CONFIG;
    }

    // Build lookup map for O(1) checks
    this.lookupMap = new Map();
    for (const [chain, entries] of Object.entries(this.config.chains)) {
      for (const entry of entries) {
        const key = `${chain}:${entry.address.toLowerCase()}`;
        this.lookupMap.set(key, entry);
      }
    }

    this.log('allowlist_loaded', 'Contract allowlist loaded', {
      version: this.config.version,
      chains: Object.keys(this.config.chains),
      totalEntries: this.lookupMap.size,
    });
  }

  /** Attach RedisManager for publishing rejection results. */
  attach(redis: RedisManager): void {
    this.redis = redis;
  }

  get stats() {
    return {
      checked: this._checked,
      allowed: this._allowed,
      rejected: this._rejected,
    };
  }

  /** Get total number of allowlisted entries. */
  get totalEntries(): number {
    return this.lookupMap.size;
  }

  /** Get entries for a specific chain. */
  getChainEntries(chain: ChainId): AllowlistEntry[] {
    return this.config.chains[chain] ?? [];
  }

  /**
   * Check if a contract address is allowed on a given chain.
   */
  check(chain: string, address: string): AllowlistCheckResult {
    this._checked++;
    const key = `${chain}:${address.toLowerCase()}`;
    const entry = this.lookupMap.get(key);

    if (entry) {
      this._allowed++;
      this.log('allowlist_allowed', 'Contract address is allowlisted', {
        chain,
        address,
        name: entry.name,
        protocol: entry.protocol,
      });
      return { allowed: true, entry };
    }

    this._rejected++;
    const reason = `Contract ${address} is not on the allowlist for chain ${chain}`;
    this.log('allowlist_rejected', reason, { chain, address });
    return { allowed: false, reason };
  }

  /**
   * Validate an execution order's target address.
   * If rejected, publishes an error result to execution:results.
   */
  async validateOrder(order: ExecutionOrder): Promise<AllowlistCheckResult> {
    const targetAddress = order.params.tokenIn; // Target contract
    const chain = order.chain;

    const result = this.check(chain, targetAddress);

    if (!result.allowed && this.redis) {
      const errorResult: ExecutionResult = {
        version: '1.0.0',
        orderId: order.orderId,
        correlationId: order.correlationId,
        timestamp: new Date().toISOString(),
        status: 'failed',
        error: result.reason ?? 'Contract not on allowlist',
      };

      try {
        await this.redis.publish(
          CHANNELS.EXECUTION_RESULTS,
          errorResult as unknown as Record<string, unknown>,
        );
        this.log('allowlist_rejection_published', 'Allowlist rejection published', {
          orderId: order.orderId,
          address: targetAddress,
          chain,
        });
      } catch (err) {
        this.log('allowlist_publish_error', 'Failed to publish rejection', {
          orderId: order.orderId,
          error: err instanceof Error ? err.message : String(err),
        });
      }
    }

    return result;
  }

  /** Check if an address is allowed (simple boolean check). */
  isAllowed(chain: string, address: string): boolean {
    return this.check(chain, address).allowed;
  }

  /** Get the loaded config version. */
  get version(): string {
    return this.config.version;
  }
}
