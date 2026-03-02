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

});
