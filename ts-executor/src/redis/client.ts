/**
 * Redis communication layer — Streams with consumer groups, and cache.
 *
 * All three channels (market:events, execution:orders, execution:results)
 * use Redis Streams with XADD/XREADGROUP for reliable, durable message
 * delivery. Pub/sub is not used. MAXLEN pruning on every write.
 */
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
  group?: string;
  consumer?: string;
  onConnectionLoss?: () => void;
  onReconnect?: () => void;
}

/** Default consumer group and consumer for ts-executor. */
const DEFAULT_GROUP = 'ts-executor';
const DEFAULT_CONSUMER = 'ts-executor-1';

/**
 * Creates a base Redis connection with retry and event handling.
 */
function createBaseClient(opts: RedisClientOptions): Redis {
  const url = opts.url ?? process.env.REDIS_URL ?? 'redis://localhost:6379';
  const client = new Redis(url, {
    retryStrategy(times: number) {
      return Math.min(times * 200, 5000);
    },
    maxRetriesPerRequest: null,
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
 * Redis manager using Streams with consumer groups, plus cache.
 *
 * All messaging uses Redis Streams (XADD for publish, XREADGROUP for
 * consume). Consumer groups provide reliable delivery with
 * acknowledgment. MAXLEN pruning on every write keeps streams bounded.
 */
export class RedisManager {
  private client: Redis;
  private _connected = false;
  private _stopping = false;
  private readonly streamMaxLen: number;
  private readonly group: string;
  private readonly consumer: string;
  private readonly opts: RedisClientOptions;
  private handlers = new Map<Channel, ((data: Record<string, unknown>) => void)[]>();
  private readerAbortControllers = new Map<Channel, AbortController>();

  constructor(opts: RedisClientOptions = {}) {
    this.opts = opts;
    this.client = createBaseClient(opts);
    this.streamMaxLen = parseInt(process.env.STREAM_MAX_LENGTH ?? '10000', 10);
    this.group = opts.group ?? DEFAULT_GROUP;
    this.consumer = opts.consumer ?? DEFAULT_CONSUMER;
  }

  /** Check if the Redis client is connected. */
  get connected(): boolean {
    return this._connected;
  }

  /** Connect the Redis client. */
  async connect(): Promise<void> {
    await this.client.connect();
    this._connected = true;
    this._stopping = false;
  }

  /** Disconnect the Redis client and stop all readers. */
  async disconnect(): Promise<void> {
    this._stopping = true;
    this._connected = false;
    // Signal all reader loops to stop
    for (const controller of this.readerAbortControllers.values()) {
      controller.abort();
    }
    this.readerAbortControllers.clear();
    await this.client.quit();
  }

  /**
   * Ensure a consumer group exists for a stream.
   * Creates the stream if it doesn't exist (MKSTREAM).
   */
  private async ensureGroup(channel: string): Promise<void> {
    try {
      await this.client.xgroup('CREATE', channel, this.group, '0', 'MKSTREAM');
    } catch (err) {
      // BUSYGROUP = group already exists, which is fine
      if (err instanceof Error && !err.message.includes('BUSYGROUP')) {
        throw err;
      }
    }
  }

  // ── Streams (publish) ──────────────────────────────────

  /**
   * Publish a validated message to a Redis Stream.
   * Validates against the channel's JSON schema, then writes with MAXLEN pruning.
   */
  async publish(channel: Channel, data: Record<string, unknown>): Promise<void> {
    const schema = CHANNEL_SCHEMA[channel];
    const result = validate(schema, data);
    if (!result.valid) {
      const msgs = (result.errors ?? []).map((e) => `${e.instancePath || '/'}: ${e.message}`).join('; ');
      throw new Error(`Cannot publish invalid message to ${channel}: ${msgs}`);
    }
    const payload = JSON.stringify(data);
    await this.client.xadd(
      channel, 'MAXLEN', '~', String(this.streamMaxLen), '*', 'data', payload,
    );
  }

  // ── Streams (subscribe via consumer groups) ────────────

  /**
   * Subscribe to a stream using consumer groups.
   * Creates the consumer group if needed, then starts a background
   * loop that reads new messages via XREADGROUP and delivers them
   * to all registered handlers after schema validation.
   */
  async subscribe(
    channel: Channel,
    handler: (data: Record<string, unknown>) => void,
  ): Promise<void> {
    if (!this.handlers.has(channel)) {
      this.handlers.set(channel, []);
    }
    this.handlers.get(channel)!.push(handler);

    // Ensure consumer group exists
    await this.ensureGroup(channel);

    // Start reader loop if not already running for this channel
    if (!this.readerAbortControllers.has(channel)) {
      const controller = new AbortController();
      this.readerAbortControllers.set(channel, controller);
      this.streamReaderLoop(channel, controller.signal);
    }
  }

  /**
   * Background loop: read from stream via XREADGROUP.
   * Reads new messages, validates against schema, delivers to handlers,
   * and acknowledges. On connection loss, retries with exponential backoff.
   */
  private streamReaderLoop(channel: Channel, signal: AbortSignal): void {
    let backoff = 200; // ms

    const readLoop = async (): Promise<void> => {
      while (!this._stopping && !signal.aborted) {
        try {
          const entries = await this.client.xreadgroup(
            'GROUP', this.group, this.consumer,
            'COUNT', '100',
            'BLOCK', '1000',
            'STREAMS', channel, '>',
          );

          // Reset backoff on success
          backoff = 200;

          if (!entries) continue;

          // ioredis returns: [[streamName, [[msgId, [field, value, ...]], ...]]]
          for (const entry of entries as [string, [string, string[]][]][]) {
            const [, messages] = entry;
            for (const [msgId, fields] of messages) {
              await this.processStreamMessage(channel, msgId, fields);
            }
          }
        } catch (err) {
          if (this._stopping || signal.aborted) break;

          console.log(JSON.stringify({
            timestamp: new Date().toISOString(),
            service: 'ts-executor',
            event: 'redis_connection_loss',
            channel,
            message: err instanceof Error ? err.message : String(err),
          }));

          this.opts.onConnectionLoss?.();

          // Exponential backoff
          await new Promise((r) => setTimeout(r, backoff));
          backoff = Math.min(backoff * 2, 30000);

          try {
            await this.client.ping();
            this.opts.onReconnect?.();
          } catch {
            // Will retry on next iteration
          }
        }
      }
    };

    // Fire and forget — runs until stopped
    readLoop().catch(() => {});
  }

  /**
   * Validate and deliver a single stream message, then acknowledge.
   */
  private async processStreamMessage(
    channel: Channel,
    msgId: string,
    fields: string[],
  ): Promise<void> {
    // ioredis returns fields as flat array: [key1, val1, key2, val2, ...]
    const fieldMap = new Map<string, string>();
    for (let i = 0; i < fields.length; i += 2) {
      fieldMap.set(fields[i], fields[i + 1]);
    }

    const raw = fieldMap.get('data');
    if (!raw) {
      await this.client.xack(channel, this.group, msgId);
      return;
    }

    let data: Record<string, unknown>;
    try {
      data = JSON.parse(raw);
    } catch {
      console.log(JSON.stringify({
        timestamp: new Date().toISOString(),
        service: 'ts-executor',
        event: 'redis_parse_error',
        channel,
        message: 'Failed to parse message JSON',
      }));
      await this.client.xack(channel, this.group, msgId);
      return;
    }

    const schema = CHANNEL_SCHEMA[channel];
    if (schema) {
      const result = validate(schema, data);
      if (!result.valid) {
        console.log(JSON.stringify({
          timestamp: new Date().toISOString(),
          service: 'ts-executor',
          event: 'schema_validation_error',
          channel,
          errors: result.errors?.map((e) => e.message),
        }));
        await this.client.xack(channel, this.group, msgId);
        return;
      }
    }

    const handlers = this.handlers.get(channel);
    if (handlers) {
      for (const h of handlers) {
        h(data);
      }
    }

    await this.client.xack(channel, this.group, msgId);
  }

  /** Expose the base client for direct commands if needed. */
  get raw(): Redis {
    return this.client;
  }
}

/** Convenience: create a standalone Redis client. */
export function createRedisClient(): Redis {
  const url = process.env.REDIS_URL ?? 'redis://localhost:6379';
  return new Redis(url, {
    retryStrategy(times: number) {
      return Math.min(times * 200, 5000);
    },
    maxRetriesPerRequest: 3,
  });
}
