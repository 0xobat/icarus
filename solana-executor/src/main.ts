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
import { buildDefaultAdapters } from "./adapters/index.js";

const SERVICE_NAME = "solana-executor";

/**
 * Strip the password component out of a Redis URL before logging.
 * `redis://:secret@host:port` → `redis://:***@host:port`. Defensive on
 * malformed URLs — never logs the original on parse failure.
 */
export function redactRedisUrl(url: string): string {
  try {
    const parsed = new URL(url);
    if (parsed.password) parsed.password = "***";
    return parsed.toString();
  } catch {
    return "redis://[unparseable]";
  }
}

/**
 * Build the adapter registry, injecting the signer pubkey into every
 * adapter that needs it. Required because Jupiter bakes the pubkey
 * into the swap's ATAs and Kamino bakes it into the obligation owner —
 * any default-constructed adapter would fail on-chain.
 */
export function buildAdapterRegistry(
  signer: PublicKey,
  logger: Logger,
): AdapterRegistry {
  const registry = new Map<string, AdapterRegistry extends ReadonlyMap<string, infer V> ? V : never>();
  const adapters = buildDefaultAdapters({ signer });
  for (const [protocol, adapter] of Object.entries(adapters)) {
    registry.set(protocol, adapter as never);
    logger.info({ protocol }, "adapter_registered");
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
  logger.info({ url: redactRedisUrl(redisUrl) }, "redis_connected");

  const publisher = new ResultsPublisher({ redis, logger });
  const adapters = buildAdapterRegistry(signer.signerPubkey, logger);
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
