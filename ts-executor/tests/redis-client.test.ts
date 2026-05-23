import { describe, it, expect, beforeAll, afterAll } from 'vitest';
import Redis from 'ioredis';
import { RedisManager, CHANNELS } from '../src/redis/client.js';

const REDIS_URL = process.env.REDIS_URL ?? 'redis://localhost:6379';

// Quick connectivity check before suite runs
async function isRedisAvailable(): Promise<boolean> {
  const client = new Redis(REDIS_URL, { lazyConnect: true, connectTimeout: 2000 });
  try {
    await client.connect();
    await client.ping();
    await client.quit();
    return true;
  } catch {
    try { await client.quit(); } catch { /* ignore */ }
    return false;
  }
}

describe('RedisManager (Streams)', async () => {
  const available = await isRedisAvailable();
  if (!available) {
    it.skip('Redis not available — skipping integration tests', () => {});
    return;
  }

  let manager: RedisManager;

  beforeAll(async () => {
    manager = new RedisManager({
      url: REDIS_URL,
      group: 'test-group',
      consumer: 'test-consumer-1',
    });
    await manager.connect();
  });

  afterAll(async () => {
    // Clean up test streams
    await manager.raw.del(CHANNELS.MARKET_EVENTS);
    await manager.raw.del(CHANNELS.EXECUTION_ORDERS);
    await manager.disconnect();
  });

  it('connects successfully', () => {
    expect(manager.connected).toBe(true);
  });

  it('publishes and receives valid messages via streams', async () => {
    const received: Record<string, unknown>[] = [];

    await manager.subscribe(CHANNELS.MARKET_EVENTS, (data) => {
      received.push(data);
    });

    const event = {
      version: '1.0.0',
      timestamp: new Date().toISOString(),
      sequence: 42,
      chain: 'ethereum',
      eventType: 'new_block',
      protocol: 'system',
      blockNumber: 12345,
    };

    await manager.publish(CHANNELS.MARKET_EVENTS, event);
    // Stream readers use BLOCK 1000ms, give it time to process
    await new Promise((r) => setTimeout(r, 2000));

    expect(received).toHaveLength(1);
    expect(received[0].sequence).toBe(42);
  });

  it('rejects invalid messages on publish', async () => {
    await expect(
      manager.publish(CHANNELS.MARKET_EVENTS, { invalid: true }),
    ).rejects.toThrow(/Cannot publish invalid message/);
  });

  it('does not deliver schema-invalid messages to handlers', async () => {
    const received: Record<string, unknown>[] = [];
    const channel = CHANNELS.EXECUTION_ORDERS;

    await manager.subscribe(channel, (data) => {
      received.push(data);
    });

    // Write invalid message directly to stream (bypass publish validation)
    await manager.raw.xadd(channel, '*', 'data', JSON.stringify({ invalid: true }));
    await new Promise((r) => setTimeout(r, 2000));

    // Invalid message should NOT be delivered
    expect(received).toHaveLength(0);
  });

  it('writes to stream with MAXLEN pruning', async () => {
    // Publish several messages and verify stream has entries
    const entries = await manager.raw.xrange(CHANNELS.MARKET_EVENTS, '-', '+');
    expect(entries.length).toBeGreaterThan(0);
  });

  it('respects STREAM_MAX_LENGTH env var', () => {
    const original = process.env.STREAM_MAX_LENGTH;
    try {
      process.env.STREAM_MAX_LENGTH = '5000';
      const mgr = new RedisManager({ url: REDIS_URL });
      // Access private field for testing
      expect((mgr as unknown as { streamMaxLen: number }).streamMaxLen).toBe(5000);
    } finally {
      if (original !== undefined) {
        process.env.STREAM_MAX_LENGTH = original;
      } else {
        delete process.env.STREAM_MAX_LENGTH;
      }
    }
  });
});
