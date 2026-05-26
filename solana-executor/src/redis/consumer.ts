/**
 * BLMOVE-based ExecutionOrder consumer for the Solana executor.
 *
 * Why BLMOVE (and not Streams like ts-executor uses):
 *  - The Solana side of the bus is queue-shaped: one order, one signer
 *    slot, no fan-out. BLMOVE gives us atomic claim into an inflight list
 *    so a crash mid-process can be recovered from without losing work.
 *  - The matching channel discipline (`execution:orders:solana` →
 *    `execution:orders:solana:inflight`) is the Solana-side mirror of the
 *    Base-side Streams consumer-group acknowledgment.
 *
 * Lifecycle of a message:
 *  1. BLMOVE pops from `execution:orders:solana` LEFT and atomically pushes
 *     onto `execution:orders:solana:inflight` RIGHT.
 *  2. Parse + AJV-validate against execution-order.schema.json. On either
 *     failure: ACK (LREM from inflight) and drop. Bad envelopes are a
 *     producer bug — we log and move on.
 *  3. Hand the order to the processor.
 *  4. Processor finishes (success or failure) → call {@link ack} to LREM
 *     the raw payload from the inflight list.
 *
 * On orderly shutdown: stop the BLMOVE loop, but leave inflight untouched.
 * The next boot picks up unfinished work via a separate (manual, for W7 v1)
 * inflight scan; W10+ adds an automatic recovery scan.
 */
import type Redis from "ioredis";
import type { Logger } from "pino";
import {
  validate,
  type ValidationResult,
} from "../validation/schema-validator.js";
import type { ExecutionOrder } from "../adapters/types.js";

export const ORDERS_CHANNEL = "execution:orders:solana";
export const ORDERS_INFLIGHT_CHANNEL = "execution:orders:solana:inflight";

export interface ClaimedOrder {
  /** The raw JSON string as it sat on the queue — needed to ACK via LREM. */
  raw: string;
  /** Schema-validated envelope. */
  order: ExecutionOrder;
}

export interface OrdersConsumerOptions {
  redis: Redis;
  logger: Logger;
  /** Block timeout for BLMOVE, in seconds. 0 = block forever. Default 5s. */
  blockTimeoutSec?: number;
}

/**
 * Long-running BLMOVE consumer for execution orders.
 *
 * Usage:
 *   const consumer = new OrdersConsumer({ redis, logger });
 *   consumer.start(async (claimed) => {
 *     // ... process claimed.order
 *     await consumer.ack(claimed);
 *   });
 *   // ... later
 *   await consumer.stop();
 */
export class OrdersConsumer {
  private readonly redis: Redis;
  private readonly logger: Logger;
  private readonly blockTimeoutSec: number;
  private running = false;
  private loopPromise: Promise<void> | null = null;

  constructor(opts: OrdersConsumerOptions) {
    this.redis = opts.redis;
    this.logger = opts.logger;
    this.blockTimeoutSec = opts.blockTimeoutSec ?? 5;
  }

  /**
   * Start the BLMOVE loop. `handler` is called once per validated order; the
   * handler MUST call {@link ack} when it is done (success or failure) or
   * the inflight list will grow without bound.
   */
  start(handler: (claimed: ClaimedOrder) => Promise<void>): void {
    if (this.running) {
      throw new Error("OrdersConsumer already started");
    }
    this.running = true;
    this.loopPromise = this.loop(handler).catch((err: unknown) => {
      this.logger.error(
        { err: err instanceof Error ? err.message : String(err) },
        "orders_consumer_loop_died",
      );
    });
  }

  /**
   * Stop the consumer loop. Pending BLMOVE will return null after the next
   * block window expires; we then exit cleanly.
   */
  async stop(): Promise<void> {
    this.running = false;
    if (this.loopPromise) {
      await this.loopPromise;
      this.loopPromise = null;
    }
  }

  /**
   * Acknowledge a claimed order by removing exactly one matching payload
   * from the inflight list. Idempotent — LREM with count=1 is safe to
   * re-call.
   */
  async ack(claimed: ClaimedOrder): Promise<void> {
    await this.redis.lrem(ORDERS_INFLIGHT_CHANNEL, 1, claimed.raw);
  }

  /**
   * Pop one order with a bounded block. Exposed for tests; the running loop
   * calls this in a hot loop. Returns null on timeout or on parse failure
   * (the parse-failed envelope is already ACKed before we return null).
   */
  async claimOne(): Promise<ClaimedOrder | null> {
    // ioredis exposes BLMOVE as `blmove(source, dest, srcDir, destDir, timeout)`
    const raw = await this.redis.blmove(
      ORDERS_CHANNEL,
      ORDERS_INFLIGHT_CHANNEL,
      "LEFT",
      "RIGHT",
      this.blockTimeoutSec,
    );
    if (raw === null) return null;

    let parsed: unknown;
    try {
      parsed = JSON.parse(raw);
    } catch (err) {
      this.logger.warn(
        { err: err instanceof Error ? err.message : String(err) },
        "execution_order_parse_failed",
      );
      // Drop the bad envelope — there is no producer to retry. ACK it.
      await this.redis.lrem(ORDERS_INFLIGHT_CHANNEL, 1, raw);
      return null;
    }

    const result: ValidationResult = validate("execution-order", parsed);
    if (!result.valid) {
      this.logger.warn(
        {
          errors: result.errors?.map(
            (e) => `${e.instancePath || "/"}: ${e.message}`,
          ),
        },
        "execution_order_schema_invalid",
      );
      await this.redis.lrem(ORDERS_INFLIGHT_CHANNEL, 1, raw);
      return null;
    }

    const order = parsed as ExecutionOrder;
    if (order.chain !== "solana") {
      // Misrouted envelope — drop with a clear log so the producer can be fixed.
      this.logger.warn(
        { order_id: order.order_id, chain: order.chain },
        "execution_order_misrouted",
      );
      await this.redis.lrem(ORDERS_INFLIGHT_CHANNEL, 1, raw);
      return null;
    }

    return { raw, order };
  }

  private async loop(
    handler: (claimed: ClaimedOrder) => Promise<void>,
  ): Promise<void> {
    while (this.running) {
      let claimed: ClaimedOrder | null;
      try {
        claimed = await this.claimOne();
      } catch (err) {
        if (!this.running) break;
        this.logger.error(
          { err: err instanceof Error ? err.message : String(err) },
          "orders_consumer_claim_error",
        );
        // Avoid tight crash loop on connection issues.
        await new Promise((r) => setTimeout(r, 1000));
        continue;
      }
      if (claimed === null) continue;

      try {
        await handler(claimed);
      } catch (err) {
        this.logger.error(
          {
            err: err instanceof Error ? err.message : String(err),
            order_id: claimed.order.order_id,
          },
          "orders_consumer_handler_error",
        );
        // Handler did not ACK. We ACK on its behalf so the inflight list
        // does not bloat; the processor is responsible for emitting a
        // failure ExecutionResult before throwing.
        await this.ack(claimed);
      }
    }
  }
}
