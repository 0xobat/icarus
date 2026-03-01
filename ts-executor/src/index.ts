import 'dotenv/config';

import type { Address } from 'viem';
import { RedisManager } from './redis/client.js';
import { AlchemyWebSocketManager } from './listeners/websocket-manager.js';
import { MarketEventPublisher } from './listeners/market-event-publisher.js';
import { L2ListenerManager } from './listeners/l2-listener.js';
import { TransactionBuilder, type ExecutionOrder, type ProtocolAdapter } from './execution/transaction-builder.js';
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

/**
 * Build a ProtocolAdapter map from raw protocol adapters.
 * Wraps each adapter's encode methods to conform to the ProtocolAdapter interface,
 * which returns `{ to, data, value }` without submitting transactions.
 */
function buildAdapterMap(adapters: ProtocolAdapters): Map<string, ProtocolAdapter> {
  const map = new Map<string, ProtocolAdapter>();

  map.set('aave_v3', {
    async buildTransaction(action, params) {
      const asset = params.tokenIn as Address;
      const amount = BigInt(params.amount);
      const recipient = (params.recipient ?? params.tokenIn) as Address;
      switch (action) {
        case 'supply':
          return { to: adapters.aave_v3.pool, data: adapters.aave_v3.encodeSupply(asset, amount, recipient) };
        case 'withdraw':
          return { to: adapters.aave_v3.pool, data: adapters.aave_v3.encodeWithdraw(asset, amount, recipient) };
        default:
          throw new Error(`Unsupported aave_v3 action: ${action}`);
      }
    },
  });

  map.set('lido', {
    async buildTransaction(action, params) {
      const amount = BigInt(params.amount);
      switch (action) {
        case 'stake':
          return { to: adapters.lido.steth, data: adapters.lido.encodeStake(), value: amount };
        case 'wrap':
          return { to: adapters.lido.wsteth, data: adapters.lido.encodeWrap(amount) };
        case 'unwrap':
          return { to: adapters.lido.wsteth, data: adapters.lido.encodeUnwrap(amount) };
        default:
          throw new Error(`Unsupported lido action: ${action}`);
      }
    },
  });

  map.set('uniswap_v3', {
    async buildTransaction(action) {
      throw new Error(`uniswap_v3 action '${action}' requires complex parameters; use ABI registry`);
    },
  });

  map.set('flash_loan', {
    async buildTransaction(action) {
      throw new Error(`flash_loan action '${action}' requires complex parameters; use ABI registry`);
    },
  });

  map.set('aerodrome', {
    async buildTransaction(action) {
      throw new Error(`aerodrome action '${action}' requires complex parameters; use ABI registry`);
    },
  });

  map.set('gmx', {
    async buildTransaction(action) {
      throw new Error(`gmx action '${action}' requires complex parameters; use ABI registry`);
    },
  });

  return map;
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

  const adapterMap = buildAdapterMap(adapters);

  const txBuilder = new TransactionBuilder({
    adapters: adapterMap,
    flashbots,
    reporter,
    onLog: log,
  });

  return {
    redis, wsManager, publisher, l2Manager, txBuilder,
    reporter, flashbots, wallet, allowlist, adapters,
  };
}

/** Bootstrap and run the TypeScript executor service. */
async function main(): Promise<void> {
  log('startup', 'TypeScript executor starting...');

  const {
    redis, wsManager, publisher, l2Manager, txBuilder,
    reporter,
  } = initializeComponents();

  // Connect Redis and attach services
  await redis.connect();
  reporter.attach(redis);
  publisher.attach(redis);
  log('redis_connected', 'Redis connection established');

  // Start WebSocket manager for mainnet/Sepolia events
  await wsManager.connect();
  log('ws_connected', 'WebSocket manager connected');

  // Start L2 chain listeners (Arbitrum, Base)
  await l2Manager.connectAll();
  log('l2_started', 'L2 listeners connected');

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

export { main, initializeComponents, buildAdapterMap, validateOrder, log };
export type { ProtocolAdapters };

if (!process.env.VITEST) {
  main().catch((err) => {
    log('fatal_error', err instanceof Error ? err.message : String(err));
    process.exit(1);
  });
}
