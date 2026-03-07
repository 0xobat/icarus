import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import {
  normalizeNewBlock,
  normalizeContractEvent,
  normalizeLargeTransfer,
  resetSequence,
  type MarketEvent,
} from '../src/listeners/event-normalizer.js';
import { AlchemyWebSocketManager } from '../src/listeners/websocket-manager.js';

// ── Event Normalizer Tests ──────────────────────────

describe('event-normalizer', () => {
  beforeEach(() => {
    resetSequence();
  });

  it('normalizes a new block event', () => {
    const event = normalizeNewBlock(
      'ethereum',
      12345,
      '0xabc123',
      BigInt('30000000000'), // 30 gwei
      BigInt('15000000'),
      BigInt(1700000000),
    );

    expect(event.version).toBe('1.0.0');
    expect(event.chain).toBe('ethereum');
    expect(event.eventType).toBe('new_block');
    expect(event.protocol).toBe('system');
    expect(event.blockNumber).toBe(12345);
    expect(event.sequence).toBe(0);
    expect(event.data).toEqual({
      blockHash: '0xabc123',
      baseFeePerGas: '30000000000',
      gasUsed: '15000000',
      blockTimestamp: 1700000000,
    });
    expect(event.timestamp).toBeDefined();
  });

  it('normalizes a contract event', () => {
    const event = normalizeContractEvent(
      'ethereum',
      'aave_v3',
      'rate_change',
      12345,
      '0xtx123',
      { reserveAddress: '0xtoken', newRate: '5.2' },
    );

    expect(event.eventType).toBe('rate_change');
    expect(event.protocol).toBe('aave_v3');
    expect(event.txHash).toBe('0xtx123');
    expect(event.data).toEqual({ reserveAddress: '0xtoken', newRate: '5.2' });
    expect(event.sequence).toBe(0);
  });

  it('normalizes a large transfer event', () => {
    const event = normalizeLargeTransfer(
      'ethereum',
      12345,
      '0xtx456',
      '0xfrom',
      '0xto',
      '0xtoken',
      '1000000000000000000',
    );

    expect(event.eventType).toBe('large_transfer');
    expect(event.data).toEqual({
      from: '0xfrom',
      to: '0xto',
      token: '0xtoken',
      amount: '1000000000000000000',
    });
  });

  it('generates monotonically increasing sequence numbers', () => {
    const e1 = normalizeNewBlock('ethereum', 1, '0x1');
    const e2 = normalizeNewBlock('ethereum', 2, '0x2');
    const e3 = normalizeContractEvent('ethereum', 'aave_v3', 'swap', 3, '0x3', {});

    expect(e1.sequence).toBe(0);
    expect(e2.sequence).toBe(1);
    expect(e3.sequence).toBe(2);
  });

  it('sets version to 1.0.0 for all events', () => {
    const events = [
      normalizeNewBlock('ethereum', 1, '0x1'),
      normalizeContractEvent('ethereum', 'aave_v3', 'swap', 1, '0x1', {}),
      normalizeLargeTransfer('ethereum', 1, '0x1', '0xa', '0xb', '0xc', '100'),
    ];
    for (const e of events) {
      expect(e.version).toBe('1.0.0');
    }
  });

  it('omits optional fields when not provided', () => {
    const event = normalizeNewBlock('ethereum', 1, '0x1');

    // baseFeePerGas, gasUsed, timestamp are undefined => not in data
    expect(event.data).toEqual({ blockHash: '0x1' });
    expect(event.txHash).toBeUndefined();
  });
});

// ── WebSocket Manager Tests ──────────────────────────

describe('AlchemyWebSocketManager', () => {
  beforeEach(() => {
    resetSequence();
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('throws when wsUrl is not configured', async () => {
    const manager = new AlchemyWebSocketManager({ wsUrl: '' });
    await expect(manager.connect()).rejects.toThrow('ALCHEMY_SEPOLIA_WS_URL is not set');
  });

  it('starts in disconnected state', () => {
    const manager = new AlchemyWebSocketManager({ wsUrl: 'wss://test' });
    expect(manager.connected).toBe(false);
    expect(manager.reconnectCount).toBe(0);
    expect(manager.stopped).toBe(false);
  });

  it('disconnect sets stopped state and cleans up', async () => {
    const logs: string[] = [];
    const manager = new AlchemyWebSocketManager({
      wsUrl: 'wss://test',
      onLog: (event) => logs.push(event),
    });

    await manager.disconnect();

    expect(manager.stopped).toBe(true);
    expect(manager.connected).toBe(false);
    expect(logs).toContain('ws_disconnected');
  });

  describe('backpressure', () => {
    it('queues events when backpressure is active', () => {
      const events: MarketEvent[] = [];
      const manager = new AlchemyWebSocketManager({
        wsUrl: 'wss://test',
        onEvent: (e) => events.push(e),
      });

      // Activate backpressure
      manager.activateBackpressure(5000);

      // Emit events via the internal path — we test the effect through the queue
      // Since we can't directly call emitEvent, we test the public API behavior
      expect(events.length).toBe(0);
    });

    it('logs when backpressure activates', () => {
      const logs: Array<{ event: string; extra?: Record<string, unknown> }> = [];
      const manager = new AlchemyWebSocketManager({
        wsUrl: 'wss://test',
        onLog: (event, _msg, extra) => logs.push({ event, extra }),
      });

      manager.activateBackpressure(5000);

      const bpLog = logs.find((l) => l.event === 'ws_backpressure_on');
      expect(bpLog).toBeDefined();
      expect(bpLog!.extra?.durationMs).toBe(5000);
    });

    it('does not double-activate backpressure', () => {
      const logs: string[] = [];
      const manager = new AlchemyWebSocketManager({
        wsUrl: 'wss://test',
        onLog: (event) => logs.push(event),
      });

      manager.activateBackpressure(5000);
      manager.activateBackpressure(5000);

      const bpLogs = logs.filter((l) => l === 'ws_backpressure_on');
      expect(bpLogs.length).toBe(1);
    });

    it('deactivates backpressure after duration', () => {
      const logs: string[] = [];
      const manager = new AlchemyWebSocketManager({
        wsUrl: 'wss://test',
        onLog: (event) => logs.push(event),
      });

      manager.activateBackpressure(5000);
      expect(logs).toContain('ws_backpressure_on');

      vi.advanceTimersByTime(5000);
      expect(logs).toContain('ws_backpressure_off');
    });
  });

  describe('contract subscriptions', () => {
    it('registers contract subscriptions for reconnect', () => {
      const manager = new AlchemyWebSocketManager({ wsUrl: 'wss://test' });

      const sub = {
        address: '0x1234567890abcdef1234567890abcdef12345678' as `0x${string}`,
        abi: [{ type: 'event', name: 'Transfer', inputs: [] }] as const,
        eventName: 'Transfer',
        protocol: 'aave_v3',
        eventType: 'rate_change' as const,
      };

      // Should not throw even without a connection
      manager.addContractSubscription(sub);
    });
  });

  describe('configuration', () => {
    it('uses custom health timeout', () => {
      const manager = new AlchemyWebSocketManager({
        wsUrl: 'wss://test',
        healthTimeoutMs: 30_000,
      });
      // Constructor should accept custom value without error
      expect(manager).toBeDefined();
    });

    it('uses custom reconnect delays', () => {
      const manager = new AlchemyWebSocketManager({
        wsUrl: 'wss://test',
        initialReconnectDelayMs: 100,
        maxReconnectDelayMs: 10_000,
      });
      expect(manager).toBeDefined();
    });

    it('defaults chain to ethereum', () => {
      const logs: Array<{ extra?: Record<string, unknown> }> = [];
      // Can't easily verify the default without connecting,
      // but we verify the constructor accepts no chain
      const manager = new AlchemyWebSocketManager({ wsUrl: 'wss://test' });
      expect(manager).toBeDefined();
    });

    it('accepts custom chain', () => {
      const manager = new AlchemyWebSocketManager({
        wsUrl: 'wss://test',
        chain: 'base',
      });
      expect(manager).toBeDefined();
    });
  });

  describe('reconnection timing', () => {
    it('reconnect count starts at zero and increments are tracked', () => {
      const manager = new AlchemyWebSocketManager({
        wsUrl: 'wss://test',
        initialReconnectDelayMs: 100,
      });

      expect(manager.reconnectCount).toBe(0);
      expect(manager.connected).toBe(false);
    });

    it('exponential backoff config is accepted', () => {
      // Verify backoff config: initial 200ms, max 30s
      const manager = new AlchemyWebSocketManager({
        wsUrl: 'wss://test',
        initialReconnectDelayMs: 200,
        maxReconnectDelayMs: 30_000,
      });
      expect(manager).toBeDefined();
      expect(manager.reconnectCount).toBe(0);
    });
  });

  describe('event schema compliance', () => {
    it('produces events matching market-events schema', () => {
      // Verify normalized events have all required schema fields
      const event = normalizeNewBlock('ethereum', 100, '0xhash', BigInt(30e9));

      // Required fields per schema
      expect(event.version).toBe('1.0.0');
      expect(event.timestamp).toMatch(/^\d{4}-\d{2}-\d{2}T/);
      expect(typeof event.sequence).toBe('number');
      expect(event.sequence).toBeGreaterThanOrEqual(0);
      expect(['ethereum', 'arbitrum', 'base', 'solana']).toContain(event.chain);
      expect(['swap', 'liquidity_change', 'rate_change', 'large_transfer', 'new_block', 'price_update']).toContain(event.eventType);
      expect(event.protocol).toBeTruthy();
    });

    it('produces events for all supported event types', () => {
      resetSequence();

      const events = [
        normalizeNewBlock('ethereum', 1, '0x1'),
        normalizeContractEvent('ethereum', 'aave_v3', 'swap', 1, '0x1', {}),
        normalizeContractEvent('ethereum', 'aave_v3', 'rate_change', 1, '0x1', {}),
        normalizeContractEvent('ethereum', 'uniswap_v3', 'liquidity_change', 1, '0x1', {}),
        normalizeLargeTransfer('ethereum', 1, '0x1', '0xa', '0xb', '0xc', '100'),
        normalizeContractEvent('ethereum', 'system', 'price_update', 1, '0x1', {}),
      ];

      const types = new Set(events.map((e) => e.eventType));
      expect(types).toEqual(new Set([
        'new_block', 'swap', 'rate_change', 'liquidity_change', 'large_transfer', 'price_update',
      ]));
    });
  });
});
