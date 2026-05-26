/**
 * OrdersConsumer tests.
 *
 * Use a hand-rolled in-memory stub for the Redis surface area we need
 * (`blmove`, `lrem`). ioredis-mock does not implement BLMOVE so we cannot
 * lean on it for this consumer.
 */
import { describe, it, expect, beforeEach } from "vitest";
import pino from "pino";
import type Redis from "ioredis";
import {
  OrdersConsumer,
  ORDERS_CHANNEL,
  ORDERS_INFLIGHT_CHANNEL,
  type ClaimedOrder,
} from "../src/redis/consumer.js";

/** Minimal in-memory Redis stub with the commands the consumer uses. */
class FakeRedis {
  lists = new Map<string, string[]>();

  async blmove(
    src: string,
    dest: string,
    _srcDir: "LEFT" | "RIGHT",
    _destDir: "LEFT" | "RIGHT",
    _timeoutSec: number,
  ): Promise<string | null> {
    const srcList = this.lists.get(src) ?? [];
    if (srcList.length === 0) return null;
    const item = srcList.shift()!;
    this.lists.set(src, srcList);
    const destList = this.lists.get(dest) ?? [];
    destList.push(item);
    this.lists.set(dest, destList);
    return item;
  }

  async lrem(key: string, count: number, value: string): Promise<number> {
    const list = this.lists.get(key) ?? [];
    let removed = 0;
    const out: string[] = [];
    for (const item of list) {
      if (removed < count && item === value) {
        removed += 1;
        continue;
      }
      out.push(item);
    }
    this.lists.set(key, out);
    return removed;
  }
}

function makeValidOrder(overrides: Record<string, unknown> = {}): string {
  return JSON.stringify({
    version: "1.0.0",
    order_id: "ord-00000001",
    correlation_id: "corr-1",
    timestamp: "2026-05-25T12:00:00.000Z",
    chain: "solana",
    protocol: "jupiter",
    action: "swap",
    strategy: "BASIS-PERP-001:cand-1",
    template_id: "BASIS-PERP-001",
    candidate_id: "cand-1",
    priority: "normal",
    params: {
      token_in: "So11111111111111111111111111111111111111112",
      token_out: "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
      amount: "1000000",
      extra: {},
    },
    limits: {
      max_slippage_bps: 50,
      deadline_unix: 9999999999,
    },
    solana_specific: {
      compute_unit_price: 5000,
      compute_unit_limit: 200000,
      lookup_tables: [],
    },
    ...overrides,
  });
}

describe("OrdersConsumer.claimOne", () => {
  const logger = pino({ level: "silent" });
  let fake: FakeRedis;
  let consumer: OrdersConsumer;

  beforeEach(() => {
    fake = new FakeRedis();
    consumer = new OrdersConsumer({
      redis: fake as unknown as Redis,
      logger,
      blockTimeoutSec: 1,
    });
  });

  it("returns null when the queue is empty", async () => {
    const claimed = await consumer.claimOne();
    expect(claimed).toBeNull();
  });

  it("BLMOVEs a valid order into the inflight list and returns the parsed envelope", async () => {
    const raw = makeValidOrder();
    fake.lists.set(ORDERS_CHANNEL, [raw]);

    const claimed = await consumer.claimOne();
    expect(claimed).not.toBeNull();
    expect(claimed!.raw).toBe(raw);
    expect(claimed!.order.order_id).toBe("ord-00000001");
    expect(claimed!.order.chain).toBe("solana");

    // Atomically moved into inflight.
    expect(fake.lists.get(ORDERS_CHANNEL)).toEqual([]);
    expect(fake.lists.get(ORDERS_INFLIGHT_CHANNEL)).toEqual([raw]);
  });

  it("drops + ACKs a non-JSON payload", async () => {
    fake.lists.set(ORDERS_CHANNEL, ["not-json-{{{"]);

    const claimed = await consumer.claimOne();
    expect(claimed).toBeNull();
    expect(fake.lists.get(ORDERS_INFLIGHT_CHANNEL)).toEqual([]);
  });

  it("drops + ACKs a schema-invalid envelope (missing required field)", async () => {
    // Strip order_id → schema invalid.
    fake.lists.set(ORDERS_CHANNEL, [
      JSON.stringify({
        version: "1.0.0",
        correlation_id: "corr-1",
        timestamp: "2026-05-25T12:00:00.000Z",
        chain: "solana",
        protocol: "jupiter",
        action: "swap",
        strategy: "s",
        params: {},
        limits: { max_slippage_bps: 50, deadline_unix: 0 },
      }),
    ]);

    const claimed = await consumer.claimOne();
    expect(claimed).toBeNull();
    expect(fake.lists.get(ORDERS_INFLIGHT_CHANNEL)).toEqual([]);
  });

  it("drops + ACKs a misrouted envelope (chain=base on the solana queue)", async () => {
    // chain=base requires solana_specific to be null per the schema.
    const raw = makeValidOrder({ chain: "base", solana_specific: null });
    fake.lists.set(ORDERS_CHANNEL, [raw]);

    const claimed = await consumer.claimOne();
    expect(claimed).toBeNull();
    expect(fake.lists.get(ORDERS_INFLIGHT_CHANNEL)).toEqual([]);
  });

  it("ack() LREMs the raw payload from the inflight list", async () => {
    const raw = makeValidOrder();
    fake.lists.set(ORDERS_INFLIGHT_CHANNEL, [raw, "other"]);

    const claimed: ClaimedOrder = {
      raw,
      order: JSON.parse(raw) as ClaimedOrder["order"],
    };
    await consumer.ack(claimed);

    expect(fake.lists.get(ORDERS_INFLIGHT_CHANNEL)).toEqual(["other"]);
  });
});
