import "dotenv/config";

import { type Address } from "viem";
import { RedisManager } from "./redis/client.js";
import { AlchemyWebSocketManager } from "./listeners/websocket-manager.js";
import { MarketEventPublisher } from "./listeners/market-event-publisher.js";
import { L2ListenerManager } from "./listeners/l2-listener.js";
import {
  TransactionBuilder,
  type ProtocolAdapter,
} from "./execution/transaction-builder.js";
import { EventReporter } from "./execution/event-reporter.js";
import { SafeWalletManager } from "./wallet/safe-wallet.js";
import * as aave from "./execution/aave-v3-adapter.js";
import * as aerodrome from "./execution/aerodrome-adapter.js";

const SERVICE_NAME = "ts-executor";

/** Structured JSON logger. */
function log(
  event: string,
  message: string,
  extra?: Record<string, unknown>,
): void {
  console.log(
    JSON.stringify({
      timestamp: new Date().toISOString(),
      service: SERVICE_NAME,
      event,
      message,
      ...extra,
    }),
  );
}

/**
 * Build protocol adapter map from encode-only modules.
 * Maps protocol names to ProtocolAdapter interface for TransactionBuilder.
 */
function buildAdapterMap(): Map<string, ProtocolAdapter> {
  const map = new Map<string, ProtocolAdapter>();

  map.set("aave_v3", {
    async buildTransaction(action, params) {
      const asset = params.tokenIn as Address;
      const amount = BigInt(params.amount);
      const recipient = (params.recipient ?? params.tokenIn) as Address;
      switch (action) {
        case "supply":
          return {
            to: aave.AAVE_V3_POOL,
            data: aave.encodeSupply(asset, amount, recipient),
          };
        case "withdraw":
          return {
            to: aave.AAVE_V3_POOL,
            data: aave.encodeWithdraw(asset, amount, recipient),
          };
        default:
          throw new Error(`Unsupported aave_v3 action: ${action}`);
      }
    },
  });

  map.set("aerodrome", {
    async buildTransaction(action, params) {
      const p = params as Record<string, string | undefined>;
      const amount = BigInt(p.amount!);
      const recipient = (p.recipient ?? p.tokenIn) as Address;
      const deadline = BigInt(
        p.deadline ?? String(Math.floor(Date.now() / 1000) + 1800),
      );
      switch (action) {
        case "mint_lp":
          return {
            to: aerodrome.ROUTER_ADDRESS,
            data: aerodrome.encodeAddLiquidity({
              tokenA: p.tokenIn as Address,
              tokenB: p.tokenOut as Address,
              stable: p.stable !== "false",
              amountADesired: amount,
              amountBDesired: BigInt(p.amountB ?? amount.toString()),
              amountAMin: BigInt(p.amountAMin ?? "0"),
              amountBMin: BigInt(p.amountBMin ?? "0"),
              to: recipient,
              deadline,
            }),
          };
        case "burn_lp":
          return {
            to: aerodrome.ROUTER_ADDRESS,
            data: aerodrome.encodeRemoveLiquidity({
              tokenA: p.tokenIn as Address,
              tokenB: p.tokenOut as Address,
              stable: p.stable !== "false",
              liquidity: amount,
              amountAMin: BigInt(p.amountAMin ?? "0"),
              amountBMin: BigInt(p.amountBMin ?? "0"),
              to: recipient,
              deadline,
            }),
          };
        case "stake":
          return {
            to: p.gauge as Address,
            data: aerodrome.encodeGaugeDeposit(amount),
          };
        case "unstake":
          return {
            to: p.gauge as Address,
            data: aerodrome.encodeGaugeWithdraw(amount),
          };
        case "collect_fees":
          return {
            to: p.gauge as Address,
            data: aerodrome.encodeGetReward(recipient),
          };
        case "swap":
          return {
            to: aerodrome.ROUTER_ADDRESS,
            data: aerodrome.encodeSwap(
              amount,
              BigInt(p.amountOutMin ?? "0"),
              [{
                from: p.tokenIn as Address,
                to: p.tokenOut as Address,
                stable: p.stable !== "false",
                factory: aerodrome.POOL_FACTORY,
              }],
              recipient,
              deadline,
            ),
          };
        default:
          throw new Error(`Unsupported aerodrome action: ${action}`);
      }
    },
  });

  return map;
}

/** Initialize all service components and return them. */
async function initializeComponents(): Promise<{
  redis: RedisManager;
  wsManager: AlchemyWebSocketManager;
  publisher: MarketEventPublisher;
  l2Manager: L2ListenerManager;
  txBuilder: TransactionBuilder;
  reporter: EventReporter;
  safeWallet: SafeWalletManager;
}> {
  const redis = new RedisManager();
  const reporter = new EventReporter();
  const publisher = new MarketEventPublisher({ onLog: log });

  const wsManager = new AlchemyWebSocketManager({
    onEvent: (event) => void publisher.handleEvent(event),
    onLog: log,
  });

  const l2Manager = new L2ListenerManager({
    onEvent: (event) => void publisher.handleEvent(event),
    onLog: log,
  });

  const safeWallet = await SafeWalletManager.create({
    onLog: log,
  });

  const adapterMap = buildAdapterMap();

  const txBuilder = new TransactionBuilder({
    safeWallet,
    adapters: adapterMap,
    reporter,
    onLog: log,
  });

  return {
    redis,
    wsManager,
    publisher,
    l2Manager,
    txBuilder,
    reporter,
    safeWallet,
  };
}

/** Bootstrap and run the TypeScript executor service. */
async function main(): Promise<void> {
  log("startup", "TypeScript executor starting...");

  const { redis, wsManager, publisher, l2Manager, txBuilder, reporter } =
    await initializeComponents();

  // Connect Redis and attach services
  await redis.connect();
  reporter.attach(redis);
  publisher.attach(redis);
  log("redis_connected", "Redis connection established");

  // Start WebSocket manager for mainnet/Sepolia events
  await wsManager.connect();
  log("ws_connected", "WebSocket manager connected");

  // Start L2 chain listener (Base)
  await l2Manager.connectAll();
  log("l2_started", "L2 listeners connected");

  // Start transaction builder — subscribes to execution:orders,
  // handles nonce management, retries, and publishes results
  await txBuilder.start(redis);
  log("tx_builder_started", "Transaction builder listening for orders");

  log("ready", "TypeScript executor ready — all modules initialized");

  // Keep process alive until shutdown signal
  await new Promise<void>((resolve) => {
    const shutdown = async () => {
      log("shutdown", "TypeScript executor shutting down...");

      try {
        await wsManager.disconnect();
        await l2Manager.disconnectAll();
        await redis.disconnect();
      } catch (err) {
        log(
          "shutdown_error",
          `Error during shutdown: ${err instanceof Error ? err.message : String(err)}`,
        );
      }

      log("stopped", "TypeScript executor stopped");
      resolve();
    };

    process.on("SIGTERM", () => void shutdown());
    process.on("SIGINT", () => void shutdown());
  });
}

export { main, initializeComponents, buildAdapterMap, log };

if (!process.env.VITEST) {
  main().catch((err) => {
    log("fatal_error", err instanceof Error ? err.message : String(err));
    process.exit(1);
  });
}
