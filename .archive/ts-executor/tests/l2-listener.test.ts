import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { L2ListenerManager, type L2ListenerOptions } from '../src/listeners/l2-listener.js';
import { resetSequence, type MarketEvent } from '../src/listeners/event-normalizer.js';

// ── Test helpers ──────────────────────────────────

function createMockPublicClient() {
  const blockCallbacks: Array<(block: any) => void> = [];
  const contractCallbacks: Array<(logs: any[]) => void> = [];

  return {
    getChainId: vi.fn().mockResolvedValue(8453),
    watchBlocks: vi.fn().mockImplementation(({ onBlock }: any) => {
      blockCallbacks.push(onBlock);
      return () => {};
    }),
    watchContractEvent: vi.fn().mockImplementation(({ onLogs }: any) => {
      contractCallbacks.push(onLogs);
      return () => {};
    }),
    transport: {},
    _blockCallbacks: blockCallbacks,
    _contractCallbacks: contractCallbacks,
  } as any;
}

function createOptions(overrides?: Partial<L2ListenerOptions>): L2ListenerOptions {
  return {
    onEvent: overrides?.onEvent ?? vi.fn(),
    onLog: overrides?.onLog ?? vi.fn(),
    base: {
      enabled: true,
      wsUrl: 'wss://test-base-ws',
      blockTimeMs: 2000,
      finalityBlocks: 12,
      protocolContracts: [{
        protocol: 'aerodrome',
        address: '0xabcdef1234567890abcdef1234567890abcdef12',
        abi: [],
        eventType: 'liquidity_change' as const,
      }],
      ...overrides?.base,
    },
    publicClients: overrides?.publicClients,
  };
}

// ── Tests ──────────────────────────────────────────

describe('L2ListenerManager', () => {
  beforeEach(() => {
    resetSequence();
  });

  describe('constructor', () => {
    it('configures Base listener when enabled', () => {
      const opts = createOptions();
      const manager = new L2ListenerManager(opts);

      expect(manager.enabledChains).toContain('base');
      expect(manager.enabledChains).toHaveLength(1);
    });

    it('excludes disabled chains', () => {
      const opts = createOptions({
        base: { enabled: false },
      });
      const manager = new L2ListenerManager(opts);

      expect(manager.enabledChains).toHaveLength(0);
    });
  });

  describe('connectAll', () => {
    it('connects to Base', async () => {
      const mockBaseClient = createMockPublicClient();
      const logs: Array<{ event: string }> = [];

      const opts = createOptions({
        onLog: (event: string) => logs.push({ event }),
        publicClients: {
          base: mockBaseClient,
        },
      });

      const manager = new L2ListenerManager(opts);
      await manager.connectAll();

      expect(manager.isConnected('base')).toBe(true);
      expect(logs.some((l) => l.event === 'l2_manager_start')).toBe(true);
      expect(logs.some((l) => l.event === 'l2_manager_ready')).toBe(true);
    });

    it('logs connection for Base chain', async () => {
      const mockClient = createMockPublicClient();
      const logs: Array<{ event: string; extra?: Record<string, unknown> }> = [];

      const opts = createOptions({
        onLog: (event, _msg, extra) => logs.push({ event, extra }),
        publicClients: {
          base: mockClient,
        },
      });

      const manager = new L2ListenerManager(opts);
      await manager.connectAll();

      const baseConnected = logs.filter((l) =>
        l.event === 'l2_connected' && l.extra?.chain === 'base'
      );
      expect(baseConnected.length).toBeGreaterThanOrEqual(1);
    });
  });

  describe('disconnectAll', () => {
    it('disconnects all listeners', async () => {
      const mockClient = createMockPublicClient();
      const logs: Array<{ event: string }> = [];

      const opts = createOptions({
        onLog: (event) => logs.push({ event }),
        publicClients: {
          base: mockClient,
        },
      });

      const manager = new L2ListenerManager(opts);
      await manager.connectAll();
      await manager.disconnectAll();

      expect(manager.isConnected('base')).toBe(false);
      expect(logs.some((l) => l.event === 'l2_manager_stopped')).toBe(true);
    });
  });

  describe('event emission', () => {
    it('publishes block events with chain identifier', async () => {
      const mockClient = createMockPublicClient();
      const events: MarketEvent[] = [];

      const opts = createOptions({
        onEvent: (evt: MarketEvent) => events.push(evt),
        publicClients: {
          base: mockClient,
        },
      });

      const manager = new L2ListenerManager(opts);
      await manager.connectAll();

      // Simulate a block event on Base
      const blockCallback = mockClient._blockCallbacks[0];
      if (blockCallback) {
        blockCallback({
          number: 12345n,
          hash: '0xabc123',
          baseFeePerGas: 100000000n,
          gasUsed: 5000000n,
          timestamp: 1700000000n,
        });
      }

      expect(events.length).toBeGreaterThanOrEqual(1);
      const blockEvent = events.find((e) => e.eventType === 'new_block');
      expect(blockEvent).toBeDefined();
      expect(blockEvent?.chain).toBe('base');
      expect(blockEvent?.blockNumber).toBe(12345);
    });

    it('publishes contract events with chain identifier', async () => {
      const mockClient = createMockPublicClient();
      const events: MarketEvent[] = [];

      const opts = createOptions({
        onEvent: (evt: MarketEvent) => events.push(evt),
        publicClients: {
          base: mockClient,
        },
      });

      const manager = new L2ListenerManager(opts);
      await manager.connectAll();

      // Simulate an Aerodrome contract event on Base
      const contractCallback = mockClient._contractCallbacks[0];
      if (contractCallback) {
        contractCallback([{
          address: '0xabcdef1234567890abcdef1234567890abcdef12',
          topics: ['0xevent'],
          blockNumber: 67890n,
          transactionHash: '0xtx456',
          logIndex: 0,
        }]);
      }

      const contractEvent = events.find((e) => e.eventType === 'liquidity_change');
      expect(contractEvent).toBeDefined();
      expect(contractEvent?.chain).toBe('base');
      expect(contractEvent?.protocol).toBe('aerodrome');
    });

    it('includes L2-specific metadata in parsed events', async () => {
      const mockClient = createMockPublicClient();
      const events: MarketEvent[] = [];

      const opts = createOptions({
        onEvent: (evt: MarketEvent) => events.push(evt),
        publicClients: {
          base: mockClient,
        },
      });

      const manager = new L2ListenerManager(opts);
      await manager.connectAll();

      // Simulate Aerodrome event on Base
      const contractCallback = mockClient._contractCallbacks[0];
      if (contractCallback) {
        contractCallback([{
          address: '0xabcdef1234567890abcdef1234567890abcdef12',
          topics: ['0xaero_event'],
          blockNumber: 100n,
          transactionHash: '0xtx789',
          logIndex: 0,
        }]);
      }

      const aeroEvent = events.find((e) => e.protocol === 'aerodrome');
      expect(aeroEvent).toBeDefined();
      expect(aeroEvent?.data?.chain).toBe('base');
      expect(aeroEvent?.data?.l2BlockTimeMs).toBe(2000);
      expect(aeroEvent?.data?.finalityBlocks).toBe(12);
    });
  });

  describe('getStatus', () => {
    it('returns status for Base chain', async () => {
      const mockClient = createMockPublicClient();

      const opts = createOptions({
        publicClients: {
          base: mockClient,
        },
      });

      const manager = new L2ListenerManager(opts);
      await manager.connectAll();

      const status = manager.getStatus();

      expect(status.base).toBeDefined();
      expect(status.base.enabled).toBe(true);
      expect(status.base.connected).toBe(true);
      expect(status.base.blockTimeMs).toBe(2000);
      expect(status.base.finalityBlocks).toBe(12);
    });
  });

  describe('L2-specific quirks', () => {
    it('Base has 2s block time and 12-block finality', () => {
      const opts = createOptions();
      const manager = new L2ListenerManager(opts);
      const status = manager.getStatus();

      expect(status.base?.blockTimeMs).toBe(2000);
      expect(status.base?.finalityBlocks).toBe(12);
    });
  });

  describe('error handling', () => {
    it('throws when connecting to unconfigured chain', async () => {
      const opts = createOptions({
        base: { enabled: false },
      });
      const manager = new L2ListenerManager(opts);

      await expect(manager.connectChain('base')).rejects.toThrow('not configured');
    });

    it('returns false for unconfigured chain connection check', () => {
      const opts = createOptions({
        base: { enabled: false },
      });
      const manager = new L2ListenerManager(opts);

      expect(manager.isConnected('base')).toBe(false);
    });
  });

  describe('env var enable/disable', () => {
    const origBase = process.env.LISTEN_BASE_ENABLED;

    afterEach(() => {
      if (origBase === undefined) delete process.env.LISTEN_BASE_ENABLED;
      else process.env.LISTEN_BASE_ENABLED = origBase;
    });

    it('disables Base when LISTEN_BASE_ENABLED=false', () => {
      process.env.LISTEN_BASE_ENABLED = 'false';
      const manager = new L2ListenerManager({
        onEvent: vi.fn(),
        onLog: vi.fn(),
      });

      expect(manager.enabledChains).not.toContain('base');
    });

    it('enables Base by default when env var not set', () => {
      delete process.env.LISTEN_BASE_ENABLED;
      const manager = new L2ListenerManager({
        onEvent: vi.fn(),
        onLog: vi.fn(),
      });

      expect(manager.enabledChains).toContain('base');
    });
  });
});
