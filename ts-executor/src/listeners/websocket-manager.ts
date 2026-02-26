/**
 * LISTEN-001: Alchemy WebSocket subscription manager.
 *
 * Manages persistent WebSocket connection to Alchemy for real-time
 * blockchain event monitoring. Features:
 * - Automatic reconnection with exponential backoff
 * - Block, contract event, and pending transaction subscriptions
 * - Health monitoring (alerts on prolonged disconnection)
 * - Rate limit detection with backpressure
 */

import {
  createPublicClient,
  webSocket,
  type PublicClient,
  type WatchBlocksReturnType,
  type WatchContractEventReturnType,
  type Log,
  type Block,
  type Abi,
} from 'viem';
import { sepolia } from 'viem/chains';
import {
  normalizeNewBlock,
  normalizeContractEvent,
  type MarketEvent,
  type Chain,
  type EventType,
} from './event-normalizer.js';

// ── Types ──────────────────────────────────────────

export interface WebSocketManagerOptions {
  /** Alchemy WebSocket URL. Defaults to env ALCHEMY_SEPOLIA_WS_URL. */
  wsUrl?: string;
  /** Chain identifier for normalized events. */
  chain?: Chain;
  /** Callback when normalized events are ready. */
  onEvent?: (event: MarketEvent) => void;
  /** Callback for structured log output. */
  onLog?: (event: string, message: string, extra?: Record<string, unknown>) => void;
  /** Health check timeout in ms. Defaults to 60_000 (60s). */
  healthTimeoutMs?: number;
  /** Initial reconnect delay in ms. Defaults to 200. */
  initialReconnectDelayMs?: number;
  /** Max reconnect delay in ms. Defaults to 30_000 (30s). */
  maxReconnectDelayMs?: number;
  /** Max events queued during backpressure before dropping. Defaults to 1000. */
  maxBackpressureQueue?: number;
}

export interface ContractSubscription {
  address: `0x${string}`;
  abi: Abi;
  eventName?: string;
  protocol: string;
  eventType: EventType;
}

type Unwatch = () => void;

// ── Manager ──────────────────────────────────────────

/** Manages persistent WebSocket connections to Alchemy with auto-reconnection. */
export class AlchemyWebSocketManager {
  private client: PublicClient | null = null;
  private unwatchers: Unwatch[] = [];
  private contractSubs: ContractSubscription[] = [];

  // Reconnection state
  private reconnectDelay: number;
  private readonly initialDelay: number;
  private readonly maxDelay: number;
  private reconnecting = false;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private _stopped = false;

  // Health monitoring
  private lastEventTime: number = Date.now();
  private healthTimer: ReturnType<typeof setInterval> | null = null;
  private readonly healthTimeoutMs: number;
  private healthAlerted = false;

  // Rate limit backpressure
  private backpressureActive = false;
  private backpressureQueue: MarketEvent[] = [];
  private readonly maxBackpressureQueue: number;
  private backpressureTimer: ReturnType<typeof setTimeout> | null = null;

  // Config
  private readonly wsUrl: string;
  private readonly chain: Chain;
  private readonly onEvent: (event: MarketEvent) => void;
  private readonly log: (event: string, message: string, extra?: Record<string, unknown>) => void;

  // Observable state for testing
  private _connected = false;
  private _reconnectCount = 0;

  constructor(opts: WebSocketManagerOptions = {}) {
    this.wsUrl = opts.wsUrl ?? process.env.ALCHEMY_SEPOLIA_WS_URL ?? '';
    this.chain = opts.chain ?? 'ethereum';
    this.onEvent = opts.onEvent ?? (() => {});
    this.log = opts.onLog ?? (() => {});
    this.healthTimeoutMs = opts.healthTimeoutMs ?? 60_000;
    this.initialDelay = opts.initialReconnectDelayMs ?? 200;
    this.maxDelay = opts.maxReconnectDelayMs ?? 30_000;
    this.reconnectDelay = this.initialDelay;
    this.maxBackpressureQueue = opts.maxBackpressureQueue ?? 1000;
  }

  /** Check if the WebSocket is currently connected. */
  get connected(): boolean {
    return this._connected;
  }

  /** Get the total number of reconnection attempts. */
  get reconnectCount(): number {
    return this._reconnectCount;
  }

  /** Check if the manager has been stopped. */
  get stopped(): boolean {
    return this._stopped;
  }

  /** Connect to Alchemy and start subscriptions. */
  async connect(): Promise<void> {
    if (!this.wsUrl) {
      throw new Error('ALCHEMY_SEPOLIA_WS_URL is not set');
    }

    this._stopped = false;

    try {
      this.client = createPublicClient({
        chain: sepolia,
        transport: webSocket(this.wsUrl, {
          reconnect: false, // We handle reconnection ourselves
        }),
      });

      // Verify connection works by fetching chain ID
      await this.client.getChainId();

      // Monitor underlying WebSocket for close/error events
      this.monitorSocket();

      this._connected = true;
      this.reconnectDelay = this.initialDelay; // Reset backoff on success
      this.lastEventTime = Date.now();
      this.healthAlerted = false;

      this.log('ws_connected', 'WebSocket connected to Alchemy', { chain: this.chain });

      // Start health monitoring
      this.startHealthMonitor();

      // Resubscribe to all registered contract events
      await this.resubscribe();
    } catch (err) {
      this.log('ws_connect_error', 'Failed to connect to Alchemy', {
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
    if (this.backpressureTimer) {
      clearTimeout(this.backpressureTimer);
      this.backpressureTimer = null;
    }

    for (const unwatch of this.unwatchers) {
      try {
        unwatch();
      } catch {
        // Ignore unwatch errors during shutdown
      }
    }
    this.unwatchers = [];

    if (this.client) {
      try {
        const transport = this.client.transport as { value?: { close?: () => void } };
        transport.value?.close?.();
      } catch {
        // Ignore close errors
      }
      this.client = null;
    }

    this.backpressureQueue = [];
    this.backpressureActive = false;

    this.log('ws_disconnected', 'WebSocket manager stopped');
  }

  /** Attach error/close listeners to the underlying WebSocket. */
  private monitorSocket(): void {
    if (!this.client) return;
    const transport = this.client.transport as {
      value?: { getRpcClient?: () => Promise<{ socket: WebSocket }> };
      getRpcClient?: () => Promise<{ socket: WebSocket }>;
    };
    const getRpc = transport.getRpcClient ?? transport.value?.getRpcClient;
    if (getRpc) {
      getRpc().then((rpcClient) => {
        const ws = rpcClient.socket;
        if (ws) {
          ws.addEventListener('close', () => this.handleDisconnect());
          ws.addEventListener('error', (ev) => {
            const msg = (ev as ErrorEvent).message ?? 'WebSocket error';
            this.handleTransportError(new Error(msg));
          });
        }
      }).catch(() => {
        // If we can't get the socket, we rely on viem's error callbacks in watchers
      });
    }
  }

  // ── Subscriptions ──────────────────────────────────

  /** Subscribe to new blocks. */
  watchBlocks(): void {
    if (!this.client) return;

    const unwatch = this.client.watchBlocks({
      onBlock: (block: Block) => {
        this.touchHealth();
        const event = normalizeNewBlock(
          this.chain,
          Number(block.number),
          block.hash ?? '',
          block.baseFeePerGas ?? undefined,
          block.gasUsed,
          block.timestamp,
        );
        this.emitEvent(event);
      },
      onError: (error: Error) => {
        this.log('ws_block_error', 'Block watcher error', { error: error.message });
      },
    }) as WatchBlocksReturnType;

    this.unwatchers.push(unwatch);
  }

  /** Register a contract event subscription. Survives reconnects. */
  addContractSubscription(sub: ContractSubscription): void {
    this.contractSubs.push(sub);
    if (this.client && this._connected) {
      this.subscribeToContract(sub);
    }
  }

  /** Subscribe to a specific contract event on the current client. */
  private subscribeToContract(sub: ContractSubscription): void {
    if (!this.client) return;

    const watchArgs: Parameters<PublicClient['watchContractEvent']>[0] = {
      address: sub.address,
      abi: sub.abi,
      onLogs: (logs: Log[]) => {
        this.touchHealth();
        for (const log of logs) {
          const event = normalizeContractEvent(
            this.chain,
            sub.protocol,
            sub.eventType,
            Number(log.blockNumber),
            log.transactionHash ?? '',
            {
              address: log.address,
              topics: log.topics,
              logIndex: Number(log.logIndex),
            },
          );
          this.emitEvent(event);
        }
      },
      onError: (error: Error) => {
        this.log('ws_contract_error', 'Contract watcher error', {
          address: sub.address,
          protocol: sub.protocol,
          error: error.message,
        });
      },
    };

    if (sub.eventName) {
      watchArgs.eventName = sub.eventName;
    }

    const unwatch = this.client.watchContractEvent(watchArgs) as WatchContractEventReturnType;
    this.unwatchers.push(unwatch);
  }

  /** Resubscribe to all registered subscriptions after reconnect. */
  private async resubscribe(): Promise<void> {
    this.watchBlocks();
    for (const sub of this.contractSubs) {
      this.subscribeToContract(sub);
    }
  }

  // ── Event emission with backpressure ──────────────

  /** Emit an event or queue it during backpressure. */
  private emitEvent(event: MarketEvent): void {
    if (this.backpressureActive) {
      if (this.backpressureQueue.length < this.maxBackpressureQueue) {
        this.backpressureQueue.push(event);
      } else {
        this.log('ws_backpressure_drop', 'Dropping event due to backpressure queue full', {
          sequence: event.sequence,
          eventType: event.eventType,
        });
      }
      return;
    }
    this.onEvent(event);
  }

  /** Activate backpressure (rate limit detected). */
  activateBackpressure(durationMs: number = 5_000): void {
    if (this.backpressureActive) return;

    this.backpressureActive = true;
    this.log('ws_backpressure_on', 'Rate limit detected, activating backpressure', {
      durationMs,
      queueSize: this.backpressureQueue.length,
    });

    this.backpressureTimer = setTimeout(() => {
      this.deactivateBackpressure();
    }, durationMs);
  }

  /** Deactivate backpressure and flush queued events. */
  private deactivateBackpressure(): void {
    this.backpressureActive = false;
    this.log('ws_backpressure_off', 'Backpressure released, flushing queue', {
      queueSize: this.backpressureQueue.length,
    });

    const queued = this.backpressureQueue;
    this.backpressureQueue = [];
    for (const event of queued) {
      this.onEvent(event);
    }
  }

  // ── Reconnection ──────────────────────────────────

  /** Handle a WebSocket transport error and detect rate limiting. */
  private handleTransportError(error: Error): void {
    this.log('ws_transport_error', 'WebSocket transport error', {
      error: error.message,
    });

    // Detect rate limiting (HTTP 429-like errors in WS context)
    if (error.message.includes('429') || error.message.toLowerCase().includes('rate limit')) {
      this.activateBackpressure(10_000);
    }
  }

  /** Handle a WebSocket disconnection event. */
  private handleDisconnect(): void {
    if (this._stopped) return;

    this._connected = false;
    this.log('ws_disconnected', 'WebSocket connection lost', { chain: this.chain });
    this.scheduleReconnect();
  }

  /** Schedule a reconnection attempt with exponential backoff. */
  private scheduleReconnect(): void {
    if (this._stopped || this.reconnecting) return;
    this.reconnecting = true;

    this.log('ws_reconnecting', 'Scheduling reconnection', {
      delayMs: this.reconnectDelay,
      attempt: this._reconnectCount + 1,
    });

    this.reconnectTimer = setTimeout(async () => {
      this.reconnecting = false;
      this._reconnectCount++;

      // Clean up old watchers
      for (const unwatch of this.unwatchers) {
        try { unwatch(); } catch { /* ignore */ }
      }
      this.unwatchers = [];

      // Exponential backoff
      this.reconnectDelay = Math.min(this.reconnectDelay * 2, this.maxDelay);

      await this.connect();
    }, this.reconnectDelay);
  }

  // ── Health monitoring ──────────────────────────────

  /** Record that an event was received for health monitoring. */
  private touchHealth(): void {
    this.lastEventTime = Date.now();
    if (this.healthAlerted) {
      this.healthAlerted = false;
      this.log('ws_health_recovered', 'Event flow resumed');
    }
  }

  /** Start periodic health checks for event flow silence. */
  private startHealthMonitor(): void {
    if (this.healthTimer) {
      clearInterval(this.healthTimer);
    }

    this.healthTimer = setInterval(() => {
      const silenceMs = Date.now() - this.lastEventTime;
      if (silenceMs > this.healthTimeoutMs && !this.healthAlerted) {
        this.healthAlerted = true;
        this.log('ws_health_alert', 'No events received within health timeout', {
          silenceMs,
          thresholdMs: this.healthTimeoutMs,
          chain: this.chain,
        });
      }
    }, Math.min(this.healthTimeoutMs / 2, 10_000));
  }
}
