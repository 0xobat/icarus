import { describe, it, expect, vi, beforeEach } from 'vitest';
import { MarketEventPublisher } from '../src/listeners/market-event-publisher.js';
import { resetSequence, normalizeNewBlock, normalizeContractEvent, normalizeLargeTransfer, type MarketEvent } from '../src/listeners/event-normalizer.js';
import { CHANNELS } from '../src/redis/client.js';

// ── Mock RedisManager ──────────────────────────────

function createMockRedis(opts: { publishFn?: (channel: string, data: Record<string, unknown>) => Promise<void> } = {}) {
  return {
    publish: opts.publishFn ?? vi.fn().mockResolvedValue(undefined),
    subscribe: vi.fn(),
    connect: vi.fn(),
    disconnect: vi.fn(),
  } as any;
}

// ── Tests ──────────────────────────────────────────

describe('MarketEventPublisher', () => {
  beforeEach(() => {
    resetSequence();
  });

  describe('basic publishing', () => {
    it('publishes a market event to Redis', async () => {
      const publishFn = vi.fn().mockResolvedValue(undefined);
      const redis = createMockRedis({ publishFn });
      const publisher = new MarketEventPublisher();
      publisher.attach(redis);

      const event = normalizeNewBlock('ethereum', 100, '0xhash1');
      const result = await publisher.handleEvent(event);

      expect(result).toBe(true);
      expect(publishFn).toHaveBeenCalledWith(
        CHANNELS.MARKET_EVENTS,
        expect.objectContaining({
          version: '1.0.0',
          chain: 'ethereum',
          eventType: 'new_block',
          blockNumber: 100,
        }),
      );
    });

    it('returns false when Redis is not attached', async () => {
      const publisher = new MarketEventPublisher();
      const event = normalizeNewBlock('ethereum', 100, '0xhash1');
      const result = await publisher.handleEvent(event);

      expect(result).toBe(false);
      expect(publisher.stats.errors).toBe(1);
    });

    it('returns false and increments errors on publish failure', async () => {
      const publishFn = vi.fn().mockRejectedValue(new Error('Redis down'));
      const redis = createMockRedis({ publishFn });
      const publisher = new MarketEventPublisher();
      publisher.attach(redis);

      const event = normalizeNewBlock('ethereum', 100, '0xhash1');
      const result = await publisher.handleEvent(event);

      expect(result).toBe(false);
      expect(publisher.stats.errors).toBe(1);
    });
  });

  describe('deduplication', () => {
    it('deduplicates block events by blockNumber', async () => {
      const publishFn = vi.fn().mockResolvedValue(undefined);
      const redis = createMockRedis({ publishFn });
      const publisher = new MarketEventPublisher();
      publisher.attach(redis);

      const event1 = normalizeNewBlock('ethereum', 100, '0xhash1');
      const event2 = normalizeNewBlock('ethereum', 100, '0xhash1');

      await publisher.handleEvent(event1);
      await publisher.handleEvent(event2);

      expect(publishFn).toHaveBeenCalledTimes(1);
      expect(publisher.stats.published).toBe(1);
      expect(publisher.stats.deduplicated).toBe(1);
    });

    it('deduplicates TX events by txHash+eventType', async () => {
      const publishFn = vi.fn().mockResolvedValue(undefined);
      const redis = createMockRedis({ publishFn });
      const publisher = new MarketEventPublisher();
      publisher.attach(redis);

      const event1 = normalizeContractEvent('ethereum', 'aave_v3', 'rate_change', 100, '0xtx1', {});
      const event2 = normalizeContractEvent('ethereum', 'aave_v3', 'rate_change', 100, '0xtx1', {});

      await publisher.handleEvent(event1);
      await publisher.handleEvent(event2);

      expect(publishFn).toHaveBeenCalledTimes(1);
      expect(publisher.stats.deduplicated).toBe(1);
    });

    it('does not deduplicate different TX events from same tx', async () => {
      const publishFn = vi.fn().mockResolvedValue(undefined);
      const redis = createMockRedis({ publishFn });
      const publisher = new MarketEventPublisher();
      publisher.attach(redis);

      const event1 = normalizeContractEvent('ethereum', 'aave_v3', 'rate_change', 100, '0xtx1', {});
      const event2 = normalizeContractEvent('ethereum', 'uniswap_v3', 'swap', 100, '0xtx1', {});

      await publisher.handleEvent(event1);
      await publisher.handleEvent(event2);

      expect(publishFn).toHaveBeenCalledTimes(2);
      expect(publisher.stats.deduplicated).toBe(0);
    });

    it('does not deduplicate different blocks', async () => {
      const publishFn = vi.fn().mockResolvedValue(undefined);
      const redis = createMockRedis({ publishFn });
      const publisher = new MarketEventPublisher();
      publisher.attach(redis);

      const event1 = normalizeNewBlock('ethereum', 100, '0xhash1');
      const event2 = normalizeNewBlock('ethereum', 101, '0xhash2');

      await publisher.handleEvent(event1);
      await publisher.handleEvent(event2);

      expect(publishFn).toHaveBeenCalledTimes(2);
      expect(publisher.stats.deduplicated).toBe(0);
    });

    it('prunes dedup cache when maxDedupCacheSize reached', async () => {
      const redis = createMockRedis();
      const publisher = new MarketEventPublisher({ maxDedupCacheSize: 4 });
      publisher.attach(redis);

      for (let i = 0; i < 4; i++) {
        await publisher.handleEvent(normalizeNewBlock('ethereum', i, `0xhash${i}`));
      }
      expect(publisher.dedupCacheSize).toBe(4);

      await publisher.handleEvent(normalizeNewBlock('ethereum', 100, '0xhash100'));
      expect(publisher.dedupCacheSize).toBeLessThanOrEqual(4);
    });
  });

  describe('stats tracking', () => {
    it('tracks publishing stats', async () => {
      const publishFn = vi.fn().mockResolvedValue(undefined);
      const redis = createMockRedis({ publishFn });
      const publisher = new MarketEventPublisher();
      publisher.attach(redis);

      await publisher.handleEvent(normalizeNewBlock('ethereum', 1, '0x1'));
      await publisher.handleEvent(normalizeNewBlock('ethereum', 2, '0x2'));

      const stats = publisher.stats;
      expect(stats.published).toBe(2);
      expect(stats.deduplicated).toBe(0);
      expect(stats.errors).toBe(0);
      expect(stats.avgLatencyMs).toBeGreaterThanOrEqual(0);
    });

    it('resetStats clears all counters', async () => {
      const redis = createMockRedis();
      const publisher = new MarketEventPublisher();
      publisher.attach(redis);

      await publisher.handleEvent(normalizeNewBlock('ethereum', 1, '0x1'));
      publisher.resetStats();

      const stats = publisher.stats;
      expect(stats.published).toBe(0);
      expect(stats.deduplicated).toBe(0);
      expect(stats.errors).toBe(0);
      expect(stats.avgLatencyMs).toBe(0);
      expect(stats.maxLatencyMs).toBe(0);
      expect(stats.minLatencyMs).toBe(0);
    });

    it('tracks min and max latency', async () => {
      const redis = createMockRedis();
      const publisher = new MarketEventPublisher();
      publisher.attach(redis);

      await publisher.handleEvent(normalizeNewBlock('ethereum', 1, '0x1'));
      await publisher.handleEvent(normalizeNewBlock('ethereum', 2, '0x2'));

      const stats = publisher.stats;
      expect(stats.minLatencyMs).toBeGreaterThanOrEqual(0);
      expect(stats.maxLatencyMs).toBeGreaterThanOrEqual(stats.minLatencyMs);
    });
  });

  describe('logging', () => {
    it('logs on successful publish', async () => {
      const logs: Array<{ event: string; extra?: Record<string, unknown> }> = [];
      const redis = createMockRedis();
      const publisher = new MarketEventPublisher({
        onLog: (event, _msg, extra) => logs.push({ event, extra }),
      });
      publisher.attach(redis);

      await publisher.handleEvent(normalizeNewBlock('ethereum', 1, '0x1'));

      expect(logs.some((l) => l.event === 'publisher_published')).toBe(true);
      const pubLog = logs.find((l) => l.event === 'publisher_published');
      expect(pubLog?.extra?.latencyMs).toBeDefined();
      expect(pubLog?.extra?.eventType).toBe('new_block');
    });

    it('logs on deduplication', async () => {
      const logs: Array<{ event: string }> = [];
      const redis = createMockRedis();
      const publisher = new MarketEventPublisher({
        onLog: (event) => logs.push({ event }),
      });
      publisher.attach(redis);

      const event = normalizeNewBlock('ethereum', 1, '0x1');
      await publisher.handleEvent(event);
      await publisher.handleEvent({ ...event, sequence: 999 });

      expect(logs.some((l) => l.event === 'publisher_dedup')).toBe(true);
    });

    it('logs on publish error', async () => {
      const logs: Array<{ event: string }> = [];
      const publishFn = vi.fn().mockRejectedValue(new Error('fail'));
      const redis = createMockRedis({ publishFn });
      const publisher = new MarketEventPublisher({
        onLog: (event) => logs.push({ event }),
      });
      publisher.attach(redis);

      await publisher.handleEvent(normalizeNewBlock('ethereum', 1, '0x1'));

      expect(logs.some((l) => l.event === 'publisher_error')).toBe(true);
    });
  });

  describe('clearDedupCache', () => {
    it('clears the dedup cache', async () => {
      const redis = createMockRedis();
      const publisher = new MarketEventPublisher();
      publisher.attach(redis);

      await publisher.handleEvent(normalizeNewBlock('ethereum', 1, '0x1'));
      expect(publisher.dedupCacheSize).toBe(1);

      publisher.clearDedupCache();
      expect(publisher.dedupCacheSize).toBe(0);
    });
  });

  describe('WebSocket integration', () => {
    it('can be used as WebSocket manager onEvent callback', async () => {
      const publishFn = vi.fn().mockResolvedValue(undefined);
      const redis = createMockRedis({ publishFn });
      const publisher = new MarketEventPublisher();
      publisher.attach(redis);

      const onEvent = (event: MarketEvent) => publisher.handleEvent(event);
      const event = normalizeContractEvent('ethereum', 'aave_v3', 'rate_change', 100, '0xtx1', { rate: '5.0' });
      await onEvent(event);

      expect(publishFn).toHaveBeenCalledTimes(1);
    });

    it('handles large_transfer events correctly', async () => {
      const publishFn = vi.fn().mockResolvedValue(undefined);
      const redis = createMockRedis({ publishFn });
      const publisher = new MarketEventPublisher();
      publisher.attach(redis);

      const event = normalizeLargeTransfer('ethereum', 100, '0xtx1', '0xfrom', '0xto', '0xtoken', '1000000');
      await publisher.handleEvent(event);

      expect(publishFn).toHaveBeenCalledWith(
        CHANNELS.MARKET_EVENTS,
        expect.objectContaining({
          eventType: 'large_transfer',
          txHash: '0xtx1',
        }),
      );
    });
  });
});
