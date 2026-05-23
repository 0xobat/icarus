/**
 * LISTEN-002: Market event publisher.
 *
 * Sits between WebSocket manager and Redis:
 * - Deduplicates events (txHash+eventType for TX events, blockNumber for blocks)
 * - Publishes to market:events via RedisManager.publish()
 * - Tracks publishing latency stats
 */

import { type MarketEvent } from './event-normalizer.js';
import { type RedisManager, CHANNELS } from '../redis/client.js';

// ── Types ──────────────────────────────────────────

export interface PublisherStats {
  published: number;
  deduplicated: number;
  errors: number;
  avgLatencyMs: number;
  maxLatencyMs: number;
  minLatencyMs: number;
}

export interface MarketEventPublisherOptions {
  /** Max entries in dedup cache before pruning. Defaults to 10_000. */
  maxDedupCacheSize?: number;
  /** Structured log callback. */
  onLog?: (event: string, message: string, extra?: Record<string, unknown>) => void;
}

// ── Publisher ──────────────────────────────────────────

/** Deduplicates and publishes market events to Redis. */
export class MarketEventPublisher {
  private redis: RedisManager | null = null;
  private readonly dedupCache = new Set<string>();
  private readonly maxDedupCacheSize: number;
  private readonly log: (event: string, message: string, extra?: Record<string, unknown>) => void;

  // Stats tracking
  private _published = 0;
  private _deduplicated = 0;
  private _errors = 0;
  private _totalLatencyMs = 0;
  private _maxLatencyMs = 0;
  private _minLatencyMs = Infinity;

  constructor(opts: MarketEventPublisherOptions = {}) {
    this.maxDedupCacheSize = opts.maxDedupCacheSize ?? 10_000;
    this.log = opts.onLog ?? (() => {});
  }

  /** Attach a RedisManager for publishing. */
  attach(redis: RedisManager): void {
    this.redis = redis;
    this.log('publisher_attached', 'Market event publisher attached to Redis');
  }

  /** Get current publishing stats. */
  get stats(): PublisherStats {
    return {
      published: this._published,
      deduplicated: this._deduplicated,
      errors: this._errors,
      avgLatencyMs: this._published > 0 ? this._totalLatencyMs / this._published : 0,
      maxLatencyMs: this._maxLatencyMs,
      minLatencyMs: this._minLatencyMs === Infinity ? 0 : this._minLatencyMs,
    };
  }

  /** Reset stats counters (for testing). */
  resetStats(): void {
    this._published = 0;
    this._deduplicated = 0;
    this._errors = 0;
    this._totalLatencyMs = 0;
    this._maxLatencyMs = 0;
    this._minLatencyMs = Infinity;
  }

  /**
   * Handle an incoming MarketEvent from the WebSocket manager.
   * This is the callback to pass as `onEvent` to AlchemyWebSocketManager.
   */
  async handleEvent(event: MarketEvent): Promise<boolean> {
    // 1. Dedup check
    const dedupKey = this.buildDedupKey(event);
    if (this.dedupCache.has(dedupKey)) {
      this._deduplicated++;
      this.log('publisher_dedup', 'Duplicate event filtered', {
        eventType: event.eventType,
        dedupKey,
        sequence: event.sequence,
      });
      return false;
    }

    // 2. Add to dedup cache (with pruning)
    this.addToDedupCache(dedupKey);

    // 3. Publish to Redis
    if (!this.redis) {
      this._errors++;
      this.log('publisher_no_redis', 'Cannot publish: Redis not attached', {
        sequence: event.sequence,
      });
      return false;
    }

    const startMs = Date.now();
    try {
      await this.redis.publish(
        CHANNELS.MARKET_EVENTS,
        event as unknown as Record<string, unknown>,
      );

      const latencyMs = Date.now() - startMs;
      this.recordLatency(latencyMs);
      this._published++;

      this.log('publisher_published', 'Market event published', {
        eventType: event.eventType,
        sequence: event.sequence,
        latencyMs,
        chain: event.chain,
        protocol: event.protocol,
      });

      return true;
    } catch (err) {
      this._errors++;
      this.log('publisher_error', 'Failed to publish market event', {
        sequence: event.sequence,
        eventType: event.eventType,
        error: err instanceof Error ? err.message : String(err),
      });
      return false;
    }
  }

  /**
   * Build a dedup key for an event.
   * - TX events: txHash + eventType
   * - Block events: blockNumber (as string)
   */
  private buildDedupKey(event: MarketEvent): string {
    if (event.eventType === 'new_block' && event.blockNumber !== undefined) {
      return `block:${event.blockNumber}`;
    }
    if (event.txHash) {
      return `tx:${event.txHash}:${event.eventType}`;
    }
    // Fallback: use sequence (won't dedup, but events without txHash/blockNumber are rare)
    return `seq:${event.sequence}`;
  }

  /** Add a key to the dedup cache, pruning if needed. */
  private addToDedupCache(key: string): void {
    if (this.dedupCache.size >= this.maxDedupCacheSize) {
      // Remove oldest entries (first half of the set)
      const toRemove = Math.floor(this.maxDedupCacheSize / 2);
      let removed = 0;
      for (const k of this.dedupCache) {
        if (removed >= toRemove) break;
        this.dedupCache.delete(k);
        removed++;
      }
      this.log('publisher_dedup_pruned', 'Dedup cache pruned', {
        removed,
        remaining: this.dedupCache.size,
      });
    }
    this.dedupCache.add(key);
  }

  /** Record a latency measurement. */
  private recordLatency(ms: number): void {
    this._totalLatencyMs += ms;
    if (ms > this._maxLatencyMs) this._maxLatencyMs = ms;
    if (ms < this._minLatencyMs) this._minLatencyMs = ms;
  }

  /** Get the current dedup cache size (for testing). */
  get dedupCacheSize(): number {
    return this.dedupCache.size;
  }

  /** Clear the dedup cache (for testing). */
  clearDedupCache(): void {
    this.dedupCache.clear();
  }
}
