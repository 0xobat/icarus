/**
 * LISTEN-003: L2 chain listeners (Arbitrum, Base).
 *
 * Extends chain listening to L2 networks with:
 * - WebSocket connections to Arbitrum and Base via Alchemy
 * - Events published to same market:events channel with chain identifier
 * - L2-specific protocol parsing (GMX events, Aerodrome pool changes)
 * - L2-specific handling (faster block times, different finality)
 * - Per-chain enable/disable via configuration
 */

import {
  createPublicClient,
  webSocket,
  http,
  type PublicClient,
  type Log,
  type Block,
  type Abi,
  parseAbi,
} from 'viem';
import { arbitrum, base } from 'viem/chains';
import {
  normalizeNewBlock,
  normalizeContractEvent,
  type MarketEvent,
  type Chain as MarketChain,
  type EventType,
} from './event-normalizer.js';

// ── L2 Protocol ABIs ──────────────────────────────

/** GMX V2 event ABI for position changes on Arbitrum. */
const GMX_EVENT_ABI = parseAbi([
  'event PositionIncrease(bytes32 indexed key, address account, address collateralToken, address indexToken, uint256 collateralDelta, uint256 sizeDelta, bool isLong, uint256 price, uint256 fee)',
  'event PositionDecrease(bytes32 indexed key, address account, address collateralToken, address indexToken, uint256 collateralDelta, uint256 sizeDelta, bool isLong, uint256 price, uint256 fee)',
  'event Swap(address account, address tokenIn, address tokenOut, uint256 amountIn, uint256 amountOut)',
]);

/** Aerodrome pool event ABI for liquidity changes on Base. */
const AERODROME_EVENT_ABI = parseAbi([
  'event Swap(address indexed sender, uint256 amount0In, uint256 amount1In, uint256 amount0Out, uint256 amount1Out, address indexed to)',
  'event Mint(address indexed sender, uint256 amount0, uint256 amount1)',
  'event Burn(address indexed sender, uint256 amount0, uint256 amount1, address indexed to)',
  'event Sync(uint112 reserve0, uint112 reserve1)',
]);

// ── Types ──────────────────────────────────────────

/** Configuration for a single L2 chain listener. */
export interface L2ChainConfig {
  /** Chain name for event identification. */
  chain: MarketChain;
  /** Whether this chain listener is enabled. */
  enabled: boolean;
  /** Alchemy WebSocket URL for the chain. */
  wsUrl?: string;
  /** Alchemy HTTP URL for the chain. */
  httpUrl?: string;
  /** Block time in milliseconds (used for health monitoring). */
  blockTimeMs: number;
  /** Finality delay in blocks before events are considered confirmed. */
  finalityBlocks: number;
  /** Protocol-specific contract subscriptions. */
  protocolContracts?: L2ProtocolContract[];
}

/** A protocol contract to monitor on an L2. */
export interface L2ProtocolContract {
  /** Human-readable protocol name. */
  protocol: string;
  /** Contract address to watch. */
  address: `0x${string}`;
  /** ABI for decoding events. */
  abi: Abi;
  /** Specific event name to filter (optional). */
  eventName?: string;
  /** Event type classification for market events. */
  eventType: EventType;
}

/** Options for the L2 listener manager. */
export interface L2ListenerOptions {
  /** Arbitrum chain configuration. */
  arbitrum?: Partial<L2ChainConfig>;
  /** Base chain configuration. */
  base?: Partial<L2ChainConfig>;
  /** Callback when normalized events are ready. */
  onEvent?: (event: MarketEvent) => void;
  /** Callback for structured log output. */
  onLog?: (event: string, message: string, extra?: Record<string, unknown>) => void;
  /** Override public clients for testing. */
  publicClients?: Partial<Record<MarketChain, PublicClient>>;
}

/** Status of a single chain listener. */
export interface ChainListenerStatus {
  /** Whether the chain is enabled in configuration. */
  enabled: boolean;
  /** Whether the WebSocket is currently connected. */
  connected: boolean;
  /** Total reconnection attempts. */
  reconnectCount: number;
  /** Timestamp of the last received event. */
  lastEventTime: number;
  /** Block time in ms for this chain. */
  blockTimeMs: number;
  /** Finality delay in blocks. */
  finalityBlocks: number;
}

type Unwatch = () => void;

// ── Default Addresses ──────────────────────────────

/** GMX V2 EventEmitter on Arbitrum. */
const DEFAULT_GMX_EVENT_EMITTER: `0x${string}` = '0xC8ee91a54287DB53897056e12D9819156D3822Fb';
/** Aerodrome Router on Base. */
const DEFAULT_AERODROME_ROUTER: `0x${string}` = '0xcF77a3Ba9A5CA399B7c97c74d54e5b1Beb874E43';

// ── Default Configs ──────────────────────────────

/** Default configuration for Arbitrum. */
function defaultArbitrumConfig(overrides?: Partial<L2ChainConfig>): L2ChainConfig {
  return {
    chain: 'arbitrum',
    enabled: overrides?.enabled ?? true,
    wsUrl: overrides?.wsUrl ?? process.env.ALCHEMY_ARBITRUM_WS_URL,
    httpUrl: overrides?.httpUrl ?? process.env.ALCHEMY_ARBITRUM_HTTP_URL,
    blockTimeMs: overrides?.blockTimeMs ?? 250,
    finalityBlocks: overrides?.finalityBlocks ?? 64,
    protocolContracts: overrides?.protocolContracts ?? [
      {
        protocol: 'gmx',
        address: (process.env.GMX_EVENT_EMITTER_ADDRESS as `0x${string}` | undefined) ?? DEFAULT_GMX_EVENT_EMITTER,
        abi: GMX_EVENT_ABI,
        eventType: 'swap',
      },
    ],
  };
}

/** Default configuration for Base. */
function defaultBaseConfig(overrides?: Partial<L2ChainConfig>): L2ChainConfig {
  return {
    chain: 'base',
    enabled: overrides?.enabled ?? true,
    wsUrl: overrides?.wsUrl ?? process.env.ALCHEMY_BASE_WS_URL,
    httpUrl: overrides?.httpUrl ?? process.env.ALCHEMY_BASE_HTTP_URL,
    blockTimeMs: overrides?.blockTimeMs ?? 2000,
    finalityBlocks: overrides?.finalityBlocks ?? 12,
    protocolContracts: overrides?.protocolContracts ?? [
      {
        protocol: 'aerodrome',
        address: (process.env.AERODROME_ROUTER_ADDRESS as `0x${string}` | undefined) ?? DEFAULT_AERODROME_ROUTER,
        abi: AERODROME_EVENT_ABI,
        eventType: 'liquidity_change',
      },
    ],
  };
}

// ── Single Chain Listener ──────────────────────────

/** Manages a WebSocket connection and subscriptions for one L2 chain. */
class SingleChainListener {
  private client: PublicClient | null = null;
  private unwatchers: Unwatch[] = [];
  private _connected = false;
  private _reconnectCount = 0;
  private _lastEventTime = Date.now();
  private reconnecting = false;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private healthTimer: ReturnType<typeof setInterval> | null = null;
  private _stopped = false;
  private reconnectDelay: number;
  private readonly initialDelay = 200;
  private readonly maxDelay = 30_000;

  readonly config: L2ChainConfig;
  private readonly onEvent: (event: MarketEvent) => void;
  private readonly log: (event: string, message: string, extra?: Record<string, unknown>) => void;
  private readonly testClient: PublicClient | null;

  constructor(
    config: L2ChainConfig,
    onEvent: (event: MarketEvent) => void,
    log: (event: string, message: string, extra?: Record<string, unknown>) => void,
    testClient?: PublicClient,
  ) {
    this.config = config;
    this.onEvent = onEvent;
    this.log = log;
    this.testClient = testClient ?? null;
    this.reconnectDelay = this.initialDelay;
  }

  /** Check if this chain listener is connected. */
  get connected(): boolean {
    return this._connected;
  }

  /** Get the reconnection count. */
  get reconnectCount(): number {
    return this._reconnectCount;
  }

  /** Get the last event timestamp. */
  get lastEventTime(): number {
    return this._lastEventTime;
  }

  /** Get the status of this chain listener. */
  getStatus(): ChainListenerStatus {
    return {
      enabled: this.config.enabled,
      connected: this._connected,
      reconnectCount: this._reconnectCount,
      lastEventTime: this._lastEventTime,
      blockTimeMs: this.config.blockTimeMs,
      finalityBlocks: this.config.finalityBlocks,
    };
  }

  /** Connect to the chain and start subscriptions. */
  async connect(): Promise<void> {
    if (!this.config.enabled) {
      this.log('l2_chain_disabled', `${this.config.chain} listener disabled`, {
        chain: this.config.chain,
      });
      return;
    }

    this._stopped = false;

    try {
      if (this.testClient) {
        this.client = this.testClient;
      } else {
        const viemChain = this.config.chain === 'arbitrum' ? arbitrum : base;

        if (this.config.wsUrl) {
          this.client = createPublicClient({
            chain: viemChain,
            transport: webSocket(this.config.wsUrl, { reconnect: false }),
          }) as PublicClient;
        } else if (this.config.httpUrl) {
          this.client = createPublicClient({
            chain: viemChain,
            transport: http(this.config.httpUrl),
          }) as PublicClient;
        } else {
          throw new Error(`No RPC URL configured for ${this.config.chain}`);
        }

        await this.client!.getChainId();
      }

      this._connected = true;
      this.reconnectDelay = this.initialDelay;
      this._lastEventTime = Date.now();

      this.log('l2_connected', `Connected to ${this.config.chain}`, {
        chain: this.config.chain,
        blockTimeMs: this.config.blockTimeMs,
        finalityBlocks: this.config.finalityBlocks,
      });

      this.startHealthMonitor();
      this.subscribeAll();
    } catch (err) {
      this.log('l2_connect_error', `Failed to connect to ${this.config.chain}`, {
        chain: this.config.chain,
        error: err instanceof Error ? err.message : String(err),
      });
      this._connected = false;
      this.scheduleReconnect();
    }
  }

  /** Disconnect and clean up. */
  async disconnect(): Promise<void> {
    this._stopped = true;
    this._connected = false;

    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.healthTimer) {
      clearInterval(this.healthTimer);
      this.healthTimer = null;
    }

    for (const unwatch of this.unwatchers) {
      try { unwatch(); } catch { /* ignore */ }
    }
    this.unwatchers = [];

    if (this.client && !this.testClient) {
      try {
        const transport = this.client.transport as { value?: { close?: () => void } };
        transport.value?.close?.();
      } catch { /* ignore */ }
    }
    this.client = null;

    this.log('l2_disconnected', `${this.config.chain} listener stopped`, {
      chain: this.config.chain,
    });
  }

  /** Subscribe to blocks and all protocol contracts. */
  private subscribeAll(): void {
    if (!this.client) return;

    // Watch blocks
    try {
      const unwatch = this.client.watchBlocks({
        onBlock: (block: Block) => {
          this.touchHealth();
          const event = normalizeNewBlock(
            this.config.chain,
            Number(block.number),
            block.hash ?? '',
            block.baseFeePerGas ?? undefined,
            block.gasUsed,
            block.timestamp,
          );
          this.onEvent(event);
        },
        onError: (error: Error) => {
          this.log('l2_block_error', `Block watcher error on ${this.config.chain}`, {
            chain: this.config.chain,
            error: error.message,
          });
        },
      });
      this.unwatchers.push(unwatch);
    } catch {
      // Block watching may not be supported via HTTP
    }

    // Subscribe to protocol contracts
    for (const contract of (this.config.protocolContracts ?? [])) {
      this.subscribeToContract(contract);
    }
  }

  /** Subscribe to events from a specific protocol contract. */
  private subscribeToContract(contract: L2ProtocolContract): void {
    if (!this.client) return;

    try {
      const watchArgs: Parameters<PublicClient['watchContractEvent']>[0] = {
        address: contract.address,
        abi: contract.abi,
        onLogs: (logs: Log[]) => {
          this.touchHealth();
          for (const log of logs) {
            const eventData = this.parseL2Event(contract.protocol, log);
            const event = normalizeContractEvent(
              this.config.chain,
              contract.protocol,
              contract.eventType,
              Number(log.blockNumber),
              log.transactionHash ?? '',
              eventData,
            );
            this.onEvent(event);
          }
        },
        onError: (error: Error) => {
          this.log('l2_contract_error', `Contract watcher error on ${this.config.chain}`, {
            chain: this.config.chain,
            protocol: contract.protocol,
            address: contract.address,
            error: error.message,
          });
        },
      };

      if (contract.eventName) {
        watchArgs.eventName = contract.eventName;
      }

      const unwatch = this.client.watchContractEvent(watchArgs);
      this.unwatchers.push(unwatch);
    } catch {
      this.log('l2_subscribe_error', `Failed to subscribe to ${contract.protocol} on ${this.config.chain}`, {
        chain: this.config.chain,
        protocol: contract.protocol,
        address: contract.address,
      });
    }
  }

  /** Parse L2-specific event data into a normalized record. */
  private parseL2Event(protocol: string, log: Log): Record<string, unknown> {
    const base: Record<string, unknown> = {
      address: log.address,
      topics: log.topics,
      logIndex: Number(log.logIndex),
      protocol,
      chain: this.config.chain,
    };

    // Add L2-specific metadata
    if (this.config.chain === 'arbitrum') {
      base.l2BlockTimeMs = this.config.blockTimeMs;
      base.finalityBlocks = this.config.finalityBlocks;
    } else if (this.config.chain === 'base') {
      base.l2BlockTimeMs = this.config.blockTimeMs;
      base.finalityBlocks = this.config.finalityBlocks;
    }

    return base;
  }

  /** Update health tracking timestamp. */
  private touchHealth(): void {
    this._lastEventTime = Date.now();
  }

  /** Start periodic health checks. */
  private startHealthMonitor(): void {
    if (this.healthTimer) {
      clearInterval(this.healthTimer);
    }

    // Health timeout is proportional to block time but at least 30s
    const healthTimeoutMs = Math.max(this.config.blockTimeMs * 120, 30_000);

    this.healthTimer = setInterval(() => {
      const silenceMs = Date.now() - this._lastEventTime;
      if (silenceMs > healthTimeoutMs) {
        this.log('l2_health_alert', `No events on ${this.config.chain} for ${silenceMs}ms`, {
          chain: this.config.chain,
          silenceMs,
          thresholdMs: healthTimeoutMs,
        });
      }
    }, Math.min(healthTimeoutMs / 2, 10_000));
  }

  /** Schedule a reconnection attempt with exponential backoff. */
  private scheduleReconnect(): void {
    if (this._stopped || this.reconnecting) return;
    this.reconnecting = true;

    this.log('l2_reconnecting', `Scheduling reconnection for ${this.config.chain}`, {
      chain: this.config.chain,
      delayMs: this.reconnectDelay,
      attempt: this._reconnectCount + 1,
    });

    this.reconnectTimer = setTimeout(async () => {
      this.reconnecting = false;
      this._reconnectCount++;

      for (const unwatch of this.unwatchers) {
        try { unwatch(); } catch { /* ignore */ }
      }
      this.unwatchers = [];

      this.reconnectDelay = Math.min(this.reconnectDelay * 2, this.maxDelay);
      await this.connect();
    }, this.reconnectDelay);
  }
}

// ── L2 Listener Manager ──────────────────────────

/** Manages L2 chain listeners for Arbitrum and Base. */
export class L2ListenerManager {
  private readonly listeners: Map<MarketChain, SingleChainListener> = new Map();
  private readonly log: (event: string, message: string, extra?: Record<string, unknown>) => void;
  private readonly onEvent: (event: MarketEvent) => void;

  constructor(opts: L2ListenerOptions = {}) {
    this.onEvent = opts.onEvent ?? (() => {});
    this.log = opts.onLog ?? (() => {});

    const arbConfig = defaultArbitrumConfig(opts.arbitrum);
    const baseConfig = defaultBaseConfig(opts.base);

    if (arbConfig.enabled) {
      this.listeners.set('arbitrum', new SingleChainListener(
        arbConfig,
        this.onEvent,
        this.log,
        opts.publicClients?.arbitrum,
      ));
    }

    if (baseConfig.enabled) {
      this.listeners.set('base', new SingleChainListener(
        baseConfig,
        this.onEvent,
        this.log,
        opts.publicClients?.base,
      ));
    }
  }

  /** Get the set of enabled chain names. */
  get enabledChains(): MarketChain[] {
    return Array.from(this.listeners.keys());
  }

  /** Check if a specific chain is connected. */
  isConnected(chain: MarketChain): boolean {
    return this.listeners.get(chain)?.connected ?? false;
  }

  /** Get status for all configured chains. */
  getStatus(): Record<string, ChainListenerStatus> {
    const status: Record<string, ChainListenerStatus> = {};
    for (const [chain, listener] of this.listeners) {
      status[chain] = listener.getStatus();
    }
    return status;
  }

  /** Connect all enabled chain listeners. */
  async connectAll(): Promise<void> {
    this.log('l2_manager_start', 'Starting L2 listeners', {
      chains: this.enabledChains,
    });

    const promises = Array.from(this.listeners.values()).map((l) => l.connect());
    await Promise.all(promises);

    this.log('l2_manager_ready', 'L2 listeners connected', {
      chains: this.enabledChains,
      status: this.getStatus(),
    });
  }

  /** Disconnect all chain listeners. */
  async disconnectAll(): Promise<void> {
    const promises = Array.from(this.listeners.values()).map((l) => l.disconnect());
    await Promise.all(promises);

    this.log('l2_manager_stopped', 'All L2 listeners stopped');
  }

  /** Connect a specific chain. */
  async connectChain(chain: MarketChain): Promise<void> {
    const listener = this.listeners.get(chain);
    if (!listener) {
      throw new Error(`Chain ${chain} is not configured`);
    }
    await listener.connect();
  }

  /** Disconnect a specific chain. */
  async disconnectChain(chain: MarketChain): Promise<void> {
    const listener = this.listeners.get(chain);
    if (!listener) {
      throw new Error(`Chain ${chain} is not configured`);
    }
    await listener.disconnect();
  }
}

// ── Exports for protocol ABIs (used by EXEC-009) ──

export { GMX_EVENT_ABI, AERODROME_EVENT_ABI };
