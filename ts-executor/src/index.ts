import 'dotenv/config';

import { type Address, createPublicClient, http } from 'viem';
import { sepolia } from 'viem/chains';
import { RedisManager } from './redis/client.js';
import { AlchemyWebSocketManager } from './listeners/websocket-manager.js';
import { MarketEventPublisher } from './listeners/market-event-publisher.js';
import { L2ListenerManager } from './listeners/l2-listener.js';
import { TransactionBuilder, type ProtocolAdapter } from './execution/transaction-builder.js';
import { EventReporter } from './execution/event-reporter.js';
import { FlashbotsProtectManager } from './execution/flashbots-protect.js';
import { SafeWalletManager } from './wallet/safe-wallet.js';
import * as aave from './execution/aave-v3-adapter.js';
import * as lido from './execution/lido-adapter.js';
import * as flashLoan from './execution/flash-loan-adapter.js';
import * as uniV3 from './execution/uniswap-v3-adapter.js';

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
 * Build protocol adapter map from encode-only modules.
 * Maps protocol names to ProtocolAdapter interface for TransactionBuilder.
 */
function buildAdapterMap(): Map<string, ProtocolAdapter> {
  const map = new Map<string, ProtocolAdapter>();

  map.set('aave_v3', {
    async buildTransaction(action, params) {
      const asset = params.tokenIn as Address;
      const amount = BigInt(params.amount);
      const recipient = (params.recipient ?? params.tokenIn) as Address;
      switch (action) {
        case 'supply':
          return { to: aave.AAVE_V3_POOL, data: aave.encodeSupply(asset, amount, recipient) };
        case 'withdraw':
          return { to: aave.AAVE_V3_POOL, data: aave.encodeWithdraw(asset, amount, recipient) };
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
          return { to: lido.STETH_ADDRESS, data: lido.encodeStake(), value: amount };
        case 'wrap':
          return { to: lido.WSTETH_ADDRESS, data: lido.encodeWrap(amount) };
        case 'unwrap':
          return { to: lido.WSTETH_ADDRESS, data: lido.encodeUnwrap(amount) };
        default:
          throw new Error(`Unsupported lido action: ${action}`);
      }
    },
  });

  map.set('uniswap_v3', {
    async buildTransaction(action, params) {
      const p = params as Record<string, string | undefined>;
      const recipient = (p.recipient ?? p.tokenIn) as Address;
      const amount = BigInt(p.amount!);
      const deadline = BigInt(p.deadline ?? String(Math.floor(Date.now() / 1000) + 1800));
      switch (action) {
        case 'mint':
          return {
            to: uniV3.POSITION_MANAGER_ADDRESS,
            data: uniV3.encodeMint({
              token0: p.tokenIn as Address,
              token1: p.tokenOut as Address,
              fee: Number(p.fee ?? '3000'),
              tickLower: Number(p.tickLower ?? '-887220'),
              tickUpper: Number(p.tickUpper ?? '887220'),
              amount0Desired: amount,
              amount1Desired: BigInt(p.amount1 ?? amount.toString()),
              amount0Min: BigInt(p.amount0Min ?? '0'),
              amount1Min: BigInt(p.amount1Min ?? '0'),
              recipient,
              deadline,
            }),
          };
        case 'increase_liquidity':
          return {
            to: uniV3.POSITION_MANAGER_ADDRESS,
            data: uniV3.encodeIncreaseLiquidity({
              tokenId: BigInt(p.tokenId!),
              amount0Desired: amount,
              amount1Desired: BigInt(p.amount1 ?? amount.toString()),
              amount0Min: BigInt(p.amount0Min ?? '0'),
              amount1Min: BigInt(p.amount1Min ?? '0'),
              deadline,
            }),
          };
        case 'decrease_liquidity':
          return {
            to: uniV3.POSITION_MANAGER_ADDRESS,
            data: uniV3.encodeDecreaseLiquidity({
              tokenId: BigInt(p.tokenId!),
              liquidity: amount,
              amount0Min: BigInt(p.amount0Min ?? '0'),
              amount1Min: BigInt(p.amount1Min ?? '0'),
              deadline,
            }),
          };
        case 'collect':
          return {
            to: uniV3.POSITION_MANAGER_ADDRESS,
            data: uniV3.encodeCollect({
              tokenId: BigInt(p.tokenId!),
              recipient,
              amount0Max: BigInt(p.amount0Max ?? String(2n ** 128n - 1n)),
              amount1Max: BigInt(p.amount1Max ?? String(2n ** 128n - 1n)),
            }),
          };
        case 'burn':
          return {
            to: uniV3.POSITION_MANAGER_ADDRESS,
            data: uniV3.encodeBurn(BigInt(p.tokenId!)),
          };
        default:
          throw new Error(`Unsupported uniswap_v3 action: ${action}`);
      }
    },
  });

  map.set('flash_loan', {
    async buildTransaction(action, params) {
      const receiver = (params.receiver ?? params.recipient) as Address;
      const amount = BigInt(params.amount);
      switch (action) {
        case 'flash_loan': {
          const asset = params.tokenIn as Address;
          return {
            to: flashLoan.AAVE_V3_POOL,
            data: flashLoan.encodeFlashLoan({
              receiverAddress: receiver,
              assets: [asset],
              amounts: [amount],
              interestRateModes: [0n],
              onBehalfOf: receiver,
              params: (params.callbackData ?? '0x') as `0x${string}`,
            }),
          };
        }
        case 'flash_loan_simple': {
          const asset = params.tokenIn as Address;
          return {
            to: flashLoan.AAVE_V3_POOL,
            data: flashLoan.encodeFlashLoanSimple({
              receiverAddress: receiver,
              asset,
              amount,
              params: (params.callbackData ?? '0x') as `0x${string}`,
            }),
          };
        }
        default:
          throw new Error(`Unsupported flash_loan action: ${action}`);
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

  // Initialize Flashbots Protect if configured
  let flashbotsProtect: FlashbotsProtectManager | undefined;
  const flashbotsRpcUrl = process.env.FLASHBOTS_RPC_URL;
  if (flashbotsRpcUrl) {
    const fallbackClient = createPublicClient({
      chain: sepolia,
      transport: http(process.env.ALCHEMY_SEPOLIA_HTTP_URL),
    });
    flashbotsProtect = new FlashbotsProtectManager({
      flashbotsRpcUrl,
      fallbackClient,
      onLog: log,
    });
    log('flashbots_init', 'Flashbots Protect manager initialized', { rpcUrl: flashbotsRpcUrl });
  }

  const txBuilder = new TransactionBuilder({
    safeWallet,
    adapters: adapterMap,
    reporter,
    flashbotsProtect,
    onLog: log,
  });

  return {
    redis, wsManager, publisher, l2Manager, txBuilder,
    reporter, safeWallet,
  };
}

/** Bootstrap and run the TypeScript executor service. */
async function main(): Promise<void> {
  log('startup', 'TypeScript executor starting...');

  const {
    redis, wsManager, publisher, l2Manager, txBuilder,
    reporter,
  } = await initializeComponents();

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

export { main, initializeComponents, buildAdapterMap, log };

if (!process.env.VITEST) {
  main().catch((err) => {
    log('fatal_error', err instanceof Error ? err.message : String(err));
    process.exit(1);
  });
}
