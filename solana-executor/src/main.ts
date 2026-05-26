/**
 * solana-executor entrypoint — Execution cluster, Solana chain.
 *
 * Mirrors ts-executor/src/index.ts in shape: bootstrap Redis + signer +
 * processor, attach the BLMOVE consumer loop, handle SIGTERM/SIGINT for
 * graceful shutdown.
 *
 * Adapter registry comes from `./adapters/index.js` (W7 Stream C — Jupiter,
 * Kamino). The barrel exports `ADAPTERS` (a Record<protocol, SolanaAdapter>);
 * we copy it into a Map for the read-only registry consumed by the processor.
 */
import pino, { type Logger } from "pino";
import { Connection, PublicKey } from "@solana/web3.js";
import Redis from "ioredis";

import { OrdersConsumer } from "./redis/consumer.js";
import { ResultsPublisher } from "./redis/publisher.js";
import {
  loadKeypair,
  loadSquadsEnv,
  SquadsSigner,
} from "./wallet/squads.js";
import { OrderProcessor, type AdapterRegistry } from "./processor.js";

const SERVICE_NAME = "solana-executor";

/**
 * Build the adapter registry by importing the Stream C barrel
 * (`./adapters/index.js`). If the barrel is missing — e.g. the two streams
 * have not yet merged — log a warning and return an empty registry. The
 * executor still boots; every order is dropped with a `failed` result
 * tagged "No adapter registered for protocol …".
 */
export async function buildAdapterRegistry(
  logger: Logger,
): Promise<AdapterRegistry> {
  const registry: AdapterRegistry = new Map();
  try {
    const mod = (await import("./adapters/index.js").catch(() => null)) as
      | { ADAPTERS?: Record<string, unknown> }
      | null;
    if (mod === null || !mod.ADAPTERS) {
      logger.warn({}, "adapter_registry_empty_no_barrel");
      return registry;
    }
    const writable = registry as Map<string, AdapterRegistry extends ReadonlyMap<string, infer V> ? V : never>;
    for (const [protocol, adapter] of Object.entries(mod.ADAPTERS)) {
      if (
        adapter &&
        typeof adapter === "object" &&
        typeof (adapter as { name?: unknown }).name === "string" &&
        typeof (adapter as { buildInstructions?: unknown }).buildInstructions ===
          "function"
      ) {
        writable.set(protocol, adapter as never);
        logger.info({ protocol }, "adapter_registered");
      } else {
        logger.warn({ protocol }, "adapter_registry_invalid_export");
      }
    }
  } catch (err) {
    logger.error(
      { err: err instanceof Error ? err.message : String(err) },
      "adapter_registry_load_failed",
    );
  }
  return registry;
}

/** Bootstrap and run the Solana executor service. */
export async function main(): Promise<void> {
  const logger = pino({
    name: SERVICE_NAME,
    level: process.env.LOG_LEVEL ?? "info",
  });

  const redisUrl = process.env.REDIS_URL;
  if (!redisUrl) {
    logger.error("missing_env REDIS_URL");
    process.exit(1);
  }

  const env = loadSquadsEnv();
  const connection = new Connection(env.rpcUrl, "confirmed");
  const memberKeypair = loadKeypair(env.memberKeypairPath);

  const signerOpts: ConstructorParameters<typeof SquadsSigner>[0] = {
    connection,
    memberKeypair,
    logger,
  };
  if (env.multisigPda) {
    signerOpts.multisigPda = new PublicKey(env.multisigPda);
  }
  if (env.priorityFeeMicrolamports) {
    signerOpts.priorityFeeMicrolamports = BigInt(env.priorityFeeMicrolamports);
  }
  const signer = new SquadsSigner(signerOpts);

  const redis = new Redis(redisUrl, { lazyConnect: true });
  await redis.connect();
  logger.info({ url: redisUrl }, "redis_connected");

  const publisher = new ResultsPublisher({ redis, logger });
  const adapters = await buildAdapterRegistry(logger);
  const processor = new OrderProcessor({
    adapters,
    signer,
    publisher,
    logger,
  });

  const consumer = new OrdersConsumer({ redis, logger });
  consumer.start((claimed) =>
    processor
      .handle(claimed)
      .finally(() => consumer.ack(claimed).catch(() => undefined)),
  );

  logger.info(
    {
      mode: signer.isMultisig ? "multisig" : "single_signer_fallback",
      adapter_count: adapters.size,
    },
    "solana_executor_ready",
  );

  // Graceful shutdown.
  let stopping = false;
  const stop = async (signal: NodeJS.Signals): Promise<void> => {
    if (stopping) return;
    stopping = true;
    logger.info({ signal }, "solana_executor_stop_requested");
    try {
      await consumer.stop();
      await redis.quit();
    } catch (err) {
      logger.error(
        { err: err instanceof Error ? err.message : String(err) },
        "solana_executor_shutdown_error",
      );
    }
    logger.info({}, "solana_executor_stopped");
    process.exit(0);
  };
  process.on("SIGTERM", () => void stop("SIGTERM"));
  process.on("SIGINT", () => void stop("SIGINT"));

  // Block forever — consumer.start() handles the work in the background.
  await new Promise<void>(() => {});
}

if (!process.env.VITEST) {
  main().catch((err: unknown) => {
    console.error(
      JSON.stringify({
        timestamp: new Date().toISOString(),
        service: SERVICE_NAME,
        event: "fatal_error",
        message: err instanceof Error ? err.message : String(err),
      }),
    );
    process.exit(1);
  });
}
