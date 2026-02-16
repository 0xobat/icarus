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

describe('RedisManager', async () => {
  const available = await isRedisAvailable();
  if (!available) {
    it.skip('Redis not available — skipping integration tests', () => {});
    return;
  }

  let manager: RedisManager;

  beforeAll(async () => {
    manager = new RedisManager({ url: REDIS_URL });
    await manager.connect();
  });

  afterAll(async () => {
    await manager.raw.del('cache:test-key', 'cache:test-ttl');
    await manager.raw.del('stream:market:events');
    await manager.disconnect();
  });

  it('connects successfully', () => {
    expect(manager.connected).toBe(true);
  });

  it('publishes and receives valid messages via pub/sub', async () => {
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
    await new Promise((r) => setTimeout(r, 200));

    expect(received).toHaveLength(1);
    expect(received[0].sequence).toBe(42);
  });

  it('rejects invalid messages on publish', async () => {
    await expect(
      manager.publish(CHANNELS.MARKET_EVENTS, { invalid: true }),
    ).rejects.toThrow(/Cannot publish invalid message/);
  });

  it('sets and gets cached values', async () => {
    await manager.cacheSet('test-key', { price: 1234.56 }, 60);
    const result = await manager.cacheGet<{ price: number }>('test-key');
    expect(result).toEqual({ price: 1234.56 });
  });

  it('returns null for missing cache keys', async () => {
    const result = await manager.cacheGet('nonexistent-key');
    expect(result).toBeNull();
  });

  it('deletes cached values', async () => {
    await manager.cacheSet('test-ttl', 'value', 60);
    await manager.cacheDel('test-ttl');
    const result = await manager.cacheGet('test-ttl');
    expect(result).toBeNull();
  });

  it('writes to stream on publish and reads back', async () => {
    const entries = await manager.streamRead(CHANNELS.MARKET_EVENTS);
    expect(entries.length).toBeGreaterThan(0);
    expect(entries[0].data).toHaveProperty('version', '1.0.0');
  });

  it('trims stream', async () => {
    await manager.streamTrim(CHANNELS.MARKET_EVENTS, 1);
    const entries = await manager.streamRead(CHANNELS.MARKET_EVENTS);
    expect(entries.length).toBeLessThanOrEqual(2);
  });
});
