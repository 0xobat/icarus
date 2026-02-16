import Redis from 'ioredis';
import { validate } from '../validation/schema-validator.js';
import type { SchemaName } from '../validation/schema-validator.js';

export const CHANNELS = {
  MARKET_EVENTS: 'market:events',
  EXECUTION_ORDERS: 'execution:orders',
  EXECUTION_RESULTS: 'execution:results',
} as const;

export type Channel = (typeof CHANNELS)[keyof typeof CHANNELS];

/** Map channels to their schema names for validation. */
const CHANNEL_SCHEMA: Record<Channel, SchemaName> = {
  [CHANNELS.MARKET_EVENTS]: 'market-events',
  [CHANNELS.EXECUTION_ORDERS]: 'execution-orders',
  [CHANNELS.EXECUTION_RESULTS]: 'execution-results',
};

export interface RedisClientOptions {
  url?: string;
  onConnectionLoss?: () => void;
  onReconnect?: () => void;
}

export interface StreamEntry {
  id: string;
  data: Record<string, unknown>;
}

/**
 * Creates a base Redis connection with retry and event handling.
 */
function createBaseClient(opts: RedisClientOptions): Redis {
  const url = opts.url ?? process.env.REDIS_URL ?? 'redis://localhost:6379';
  const client = new Redis(url, {
    retryStrategy(times: number) {
      return Math.min(times * 200, 5000);
    },
    maxRetriesPerRequest: null, // Allow infinite retries for pub/sub
    lazyConnect: true,
  });

  client.on('error', (err) => {
    console.log(JSON.stringify({
      timestamp: new Date().toISOString(),
      service: 'ts-executor',
      event: 'redis_error',
      message: err.message,
    }));
  });

  client.on('close', () => {
    opts.onConnectionLoss?.();
  });

  client.on('ready', () => {
    opts.onReconnect?.();
  });

  return client;
}

/**
 * Full Redis manager: pub/sub, streams, and cache.
 */
export class RedisManager {
  private pub: Redis;
  private sub: Redis;
  private client: Redis;
  private handlers = new Map<Channel, ((data: Record<string, unknown>) => void)[]>();
  private _connected = false;

  constructor(private opts: RedisClientOptions = {}) {
    this.pub = createBaseClient(opts);
    this.sub = createBaseClient(opts);
    this.client = createBaseClient(opts);
  }

  get connected(): boolean {
    return this._connected;
  }

  async connect(): Promise<void> {
    await Promise.all([
      this.pub.connect(),
      this.sub.connect(),
      this.client.connect(),
    ]);
    this._connected = true;
  }

  async disconnect(): Promise<void> {
    this._connected = false;
    await Promise.all([
      this.pub.quit(),
      this.sub.quit(),
      this.client.quit(),
    ]);
  }

  // ── Pub/Sub ──────────────────────────────────────────

  /**
   * Publish a validated message to a channel.
   * Also writes to the corresponding stream for durability.
   */
  async publish(channel: Channel, data: Record<string, unknown>): Promise<void> {
    const schema = CHANNEL_SCHEMA[channel];
    const result = validate(schema, data);
    if (!result.valid) {
      const msgs = (result.errors ?? []).map((e) => `${e.instancePath || '/'}: ${e.message}`).join('; ');
      throw new Error(`Cannot publish invalid message to ${channel}: ${msgs}`);
    }
    const payload = JSON.stringify(data);
    await this.pub.publish(channel, payload);
    // Also write to stream for durability
    await this.client.xadd(`stream:${channel}`, '*', 'data', payload);
  }

  /**
   * Subscribe to a channel. Messages are validated before delivery.
   */
  async subscribe(
    channel: Channel,
    handler: (data: Record<string, unknown>) => void,
  ): Promise<void> {
    if (!this.handlers.has(channel)) {
      this.handlers.set(channel, []);
      await this.sub.subscribe(channel);
    }
    this.handlers.get(channel)!.push(handler);

    // Wire up the message handler once
    if (this.handlers.size === 1) {
      this.sub.on('message', (ch: string, message: string) => {
        const handlers = this.handlers.get(ch as Channel);
        if (!handlers) return;

        let data: Record<string, unknown>;
        try {
          data = JSON.parse(message);
        } catch {
          console.log(JSON.stringify({
            timestamp: new Date().toISOString(),
            service: 'ts-executor',
            event: 'redis_parse_error',
            channel: ch,
            message: 'Failed to parse message JSON',
          }));
          return;
        }

        const schema = CHANNEL_SCHEMA[ch as Channel];
        if (schema) {
          const result = validate(schema, data);
          if (!result.valid) {
            console.log(JSON.stringify({
              timestamp: new Date().toISOString(),
              service: 'ts-executor',
              event: 'schema_validation_error',
              channel: ch,
              errors: result.errors?.map((e) => e.message),
            }));
            return;
          }
        }

        for (const h of handlers) {
          h(data);
        }
      });
    }
  }

  // ── Streams ──────────────────────────────────────────

  /**
   * Read entries from a stream starting from the given ID.
   */
  async streamRead(
    channel: Channel,
    fromId = '0-0',
    count = 100,
  ): Promise<StreamEntry[]> {
    const result = await this.client.xrange(`stream:${channel}`, fromId, '+', 'COUNT', count);
    return result.map(([id, fields]) => {
      const obj: Record<string, string> = {};
      for (let i = 0; i < fields.length; i += 2) {
        obj[fields[i]] = fields[i + 1];
      }
      return {
        id,
        data: obj.data ? JSON.parse(obj.data) : obj,
      };
    });
  }

  /**
   * Prune stream entries older than the given max length.
   */
  async streamTrim(channel: Channel, maxLen: number): Promise<void> {
    await this.client.xtrim(`stream:${channel}`, 'MAXLEN', '~', maxLen);
  }

  // ── Cache ──────────────────────────────────────────

  /**
   * Set a cached value with TTL in seconds.
   */
  async cacheSet(key: string, value: unknown, ttlSeconds: number): Promise<void> {
    await this.client.setex(`cache:${key}`, ttlSeconds, JSON.stringify(value));
  }

  /**
   * Get a cached value. Returns null if expired or missing.
   */
  async cacheGet<T = unknown>(key: string): Promise<T | null> {
    const raw = await this.client.get(`cache:${key}`);
    if (!raw) return null;
    return JSON.parse(raw) as T;
  }

  /**
   * Delete a cached value.
   */
  async cacheDel(key: string): Promise<void> {
    await this.client.del(`cache:${key}`);
  }

  /** Expose the base client for direct commands if needed. */
  get raw(): Redis {
    return this.client;
  }
}

/** Convenience: create a RedisManager with default options. */
export function createRedisClient(): Redis {
  const url = process.env.REDIS_URL ?? 'redis://localhost:6379';
  return new Redis(url, {
    retryStrategy(times: number) {
      return Math.min(times * 200, 5000);
    },
    maxRetriesPerRequest: 3,
  });
}
