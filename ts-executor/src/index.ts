import 'dotenv/config';

import { RedisManager } from './redis/client.js';
import { AlchemyWebSocketManager } from './listeners/websocket-manager.js';
import { MarketEventPublisher } from './listeners/market-event-publisher.js';
import { L2ListenerManager } from './listeners/l2-listener.js';
import { TransactionBuilder, type ExecutionOrder } from './execution/transaction-builder.js';
import { EventReporter } from './execution/event-reporter.js';
import { FlashbotsProtectManager } from './execution/flashbots-protect.js';
import { SmartWalletManager } from './wallet/smart-wallet.js';
import { ContractAllowlist } from './security/contract-allowlist.js';
import { AaveV3Adapter } from './execution/aave-v3-adapter.js';
import { LidoAdapter } from './execution/lido-adapter.js';
import { UniswapV3Adapter } from './execution/uniswap-v3-adapter.js';
import { FlashLoanExecutor } from './execution/flash-loan-executor.js';
import { AerodromeAdapter } from './execution/aerodrome-adapter.js';
import { GmxAdapter } from './execution/gmx-adapter.js';

const SERVICE_NAME = 'ts-executor';

/** Structured JSON logger. */
function log(event: string, message: string, extra?: Record<string, unknown>): void {
  console.log(JSON.stringify({
    timestamp: new Date().toISOString(),
    service: SERVICE_NAME,
    event,
    message,
    ...extra,
  }));
}

/**
 * Protocol adapter registry — maps protocol names to their adapters.
 * Each adapter handles protocol-specific transaction construction.
 */
interface ProtocolAdapters {
  aave_v3: AaveV3Adapter;
  lido: LidoAdapter;
  uniswap_v3: UniswapV3Adapter;
  flash_loan: FlashLoanExecutor;
  aerodrome: AerodromeAdapter;
  gmx: GmxAdapter;
}

/**
 * Validate an order against the contract allowlist before execution.
 * Returns true if execution should proceed, false if rejected.
 */
async function validateOrder(
  order: ExecutionOrder,
  allowlist: ContractAllowlist,
  reporter: EventReporter,
): Promise<boolean> {
  const check = await allowlist.validateOrder(order);
  if (!check.allowed) {
    log('order_rejected', `Allowlist rejected: ${check.reason}`, {
      correlationId: order.correlationId,
    });
    await reporter.reportFailed(order, check.reason ?? 'allowlist_rejected');
    return false;
  }
  return true;
}

/** Initialize all service components and return them. */
function initializeComponents(): {
  redis: RedisManager;
  wsManager: AlchemyWebSocketManager;
  publisher: MarketEventPublisher;
  l2Manager: L2ListenerManager;
  txBuilder: TransactionBuilder;
  reporter: EventReporter;
  flashbots: FlashbotsProtectManager;
  wallet: SmartWalletManager;
  allowlist: ContractAllowlist;
  adapters: ProtocolAdapters;
} {
  const redis = new RedisManager();
  const wsManager = new AlchemyWebSocketManager();
  const publisher = new MarketEventPublisher();
  const l2Manager = new L2ListenerManager();
  const txBuilder = new TransactionBuilder();
  const reporter = new EventReporter();
  const flashbots = new FlashbotsProtectManager();
  const wallet = new SmartWalletManager();
  const allowlist = new ContractAllowlist();

  const adapters: ProtocolAdapters = {
    aave_v3: new AaveV3Adapter(),
    lido: new LidoAdapter(),
    uniswap_v3: new UniswapV3Adapter(),
    flash_loan: new FlashLoanExecutor(),
    aerodrome: new AerodromeAdapter(),
    gmx: new GmxAdapter(),
  };

  return {
    redis, wsManager, publisher, l2Manager, txBuilder,
    reporter, flashbots, wallet, allowlist, adapters,
  };
}

/** Bootstrap and run the TypeScript executor service. */
async function main(): Promise<void> {
  log('startup', 'TypeScript executor starting...');

  const {
    redis, wsManager, l2Manager, txBuilder,
    reporter,
  } = initializeComponents();

  // Connect Redis and attach to reporter
  await redis.connect();
  reporter.attach(redis);
  log('redis_connected', 'Redis connection established');

  // Start transaction builder — subscribes to execution:orders,
  // handles nonce management, retries, and publishes results
  await txBuilder.start(redis);
  log('tx_builder_started', 'Transaction builder listening for orders');

  log('ready', 'TypeScript executor ready — all modules initialized');

  // Keep process alive until shutdown signal
  await new Promise<void>((resolve) => {
    const shutdown = async () => {
      log('shutdown', 'TypeScript executor shutting down...');

      try {
        await wsManager.disconnect();
        await l2Manager.disconnectAll();
        await redis.disconnect();
      } catch (err) {
        log('shutdown_error', `Error during shutdown: ${err instanceof Error ? err.message : String(err)}`);
      }

      log('stopped', 'TypeScript executor stopped');
      resolve();
    };

    process.on('SIGTERM', () => void shutdown());
    process.on('SIGINT', () => void shutdown());
  });
}

export { main, initializeComponents, validateOrder, log };
export type { ProtocolAdapters };

if (!process.env.VITEST) {
  main().catch((err) => {
    log('fatal_error', err instanceof Error ? err.message : String(err));
    process.exit(1);
  });
}
