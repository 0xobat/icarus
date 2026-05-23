/**
 * solana-executor entrypoint — Execution cluster, Solana chain.
 *
 * Service shape (one process):
 *  - Slot listener (Helius / Triton RPC websocket) → publishes market:events:solana.
 *  - Orders consumer: subscribes to execution:orders:solana (Redis), routes to
 *    adapter set (Jupiter for swaps, Drift / Kamino / MarginFi for venues),
 *    builds + signs via Squads multisig, broadcasts.
 *  - Results publisher: publishes execution:results:solana with signature,
 *    priority fee, success/fail.
 *  - Guard: refuses any program target outside the application allowlist
 *    (Squads on-chain policy is the unbypassable guard; this is the
 *    application-level mirror).
 *
 * Blueprint §"Build sequence" week 7 builds the full impl. Week 1 day 1
 * ships only this skeleton: process boots, redis connects, gracefully
 * exits on SIGTERM, and logs that the orders consumer is not yet wired.
 */

import pino from "pino";
import { Redis } from "ioredis";

const logger = pino({ name: "solana-executor", level: process.env.LOG_LEVEL ?? "info" });

const ORDERS_CHANNEL = "execution:orders:solana";
const RESULTS_CHANNEL = "execution:results:solana";
const EVENTS_CHANNEL = "market:events:solana";

async function main(): Promise<number> {
  const redisUrl = process.env.REDIS_URL;
  if (!redisUrl) {
    logger.error("missing_env REDIS_URL");
    return 1;
  }
  const redis = new Redis(redisUrl, { lazyConnect: true });

  let stopping = false;
  const stop = async (signal: NodeJS.Signals): Promise<void> => {
    if (stopping) return;
    stopping = true;
    logger.info({ signal }, "solana_executor_stop_requested");
    await redis.quit();
    process.exit(0);
  };
  process.on("SIGTERM", stop);
  process.on("SIGINT", stop);

  try {
    await redis.connect();
    logger.info({ ordersChannel: ORDERS_CHANNEL, resultsChannel: RESULTS_CHANNEL, eventsChannel: EVENTS_CHANNEL }, "solana_executor_start");

    // Week 7 wires: slot listener, orders subscriber, TX builder, Squads signer.
    // Skeleton: idle on a heartbeat that confirms redis liveness.
    setInterval(async () => {
      try {
        await redis.ping();
      } catch (e) {
        logger.error({ err: e }, "redis_ping_failed");
      }
    }, 30_000);

    // Block forever until SIGTERM.
    await new Promise<void>(() => {});
    return 0;
  } catch (e) {
    logger.error({ err: e }, "solana_executor_fatal");
    return 1;
  }
}

main().then((code) => process.exit(code));
