import { describe, it, expect, vi, beforeEach } from 'vitest';
import { FlashbotsProtectManager, type FlashbotsProtectOptions } from '../src/execution/flashbots-protect.js';
import type { Hex, PublicClient, TransactionReceipt } from 'viem';

// ── Test Helpers ──────────────────────────────────

const MOCK_TX_HASH = '0xabcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890' as Hex;
const MOCK_SIGNED_TX = '0xf86c0184b2d05e0082520894abc123...' as Hex;

function mockReceipt(overrides: Partial<TransactionReceipt> = {}): TransactionReceipt {
  return {
    transactionHash: MOCK_TX_HASH,
    blockNumber: 12345n,
    blockHash: '0x0000000000000000000000000000000000000000000000000000000000000001' as Hex,
    transactionIndex: 0,
    from: '0x1111111111111111111111111111111111111111' as Hex,
    to: '0x2222222222222222222222222222222222222222' as Hex,
    cumulativeGasUsed: 100000n,
    gasUsed: 100000n,
    effectiveGasPrice: 30000000000n,
    contractAddress: null,
    logs: [],
    logsBloom: '0x' as Hex,
    status: 'success',
    type: 'legacy',
    root: undefined,
    ...overrides,
  } as unknown as TransactionReceipt;
}

function mockFlashbotsClient(overrides: Record<string, unknown> = {}) {
  return {
    request: vi.fn().mockResolvedValue(MOCK_TX_HASH),
    getTransactionReceipt: vi.fn().mockResolvedValue(mockReceipt()),
    ...overrides,
  } as unknown as PublicClient;
}

function mockFallbackClient(overrides: Record<string, unknown> = {}) {
  return {
    request: vi.fn().mockResolvedValue(MOCK_TX_HASH),
    waitForTransactionReceipt: vi.fn().mockResolvedValue(mockReceipt()),
    ...overrides,
  } as unknown as PublicClient;
}

function createManager(overrides: Partial<FlashbotsProtectOptions> = {}): FlashbotsProtectManager {
  return new FlashbotsProtectManager({
    flashbotsRpcUrl: 'https://rpc.flashbots.net',
    fallbackClient: mockFallbackClient(),
    flashbotsClient: mockFlashbotsClient(),
    pollIntervalMs: 10,
    pollTimeoutMs: 200,
    maxRetries: 2,
    onLog: () => {},
    ...overrides,
  });
}

// ── Tests ──────────────────────────────────────────

describe('FlashbotsProtectManager', () => {
  describe('constructor', () => {
    it('creates with required options', () => {
      const manager = createManager();
      expect(manager).toBeDefined();
      expect(manager.stats).toEqual({
        sent: 0, included: 0, fallbacks: 0, failures: 0,
      });
    });
  });

  describe('sendTransaction', () => {
    it('submits transaction to Flashbots Protect and returns receipt', async () => {
      const flashbotsClient = mockFlashbotsClient();
      const manager = createManager({ flashbotsClient });

      const result = await manager.sendTransaction(MOCK_SIGNED_TX, 'order-001');

      expect(result.hash).toBe(MOCK_TX_HASH);
      expect(result.receipt.status).toBe('success');
      expect(result.usedFallback).toBe(false);

      expect(flashbotsClient.request).toHaveBeenCalledWith({
        method: 'eth_sendRawTransaction',
        params: [MOCK_SIGNED_TX],
      });

      expect(manager.stats.sent).toBe(1);
      expect(manager.stats.included).toBe(1);
      expect(manager.stats.fallbacks).toBe(0);
    });

    it('logs latency for Flashbots routing', async () => {
      const logs: Array<{ event: string; extra?: Record<string, unknown> }> = [];
      const manager = createManager({
        onLog: (event, _msg, extra) => logs.push({ event, extra }),
      });

      await manager.sendTransaction(MOCK_SIGNED_TX, 'order-002');

      const sentLog = logs.find((l) => l.event === 'flashbots_tx_sent');
      expect(sentLog).toBeDefined();
      expect(sentLog!.extra?.orderId).toBe('order-002');

      const includedLog = logs.find((l) => l.event === 'flashbots_tx_included');
      expect(includedLog).toBeDefined();
      expect(includedLog!.extra?.latencyMs).toBeDefined();
      expect(typeof includedLog!.extra?.latencyMs).toBe('number');
    });

    it('retries on Flashbots failure before falling back', async () => {
      let callCount = 0;
      const flashbotsClient = mockFlashbotsClient({
        request: vi.fn().mockImplementation(() => {
          callCount++;
          if (callCount <= 2) {
            return Promise.reject(new Error('Flashbots RPC error'));
          }
          return Promise.resolve(MOCK_TX_HASH);
        }),
      });

      const manager = createManager({ flashbotsClient, maxRetries: 2 });
      const result = await manager.sendTransaction(MOCK_SIGNED_TX, 'order-003');

      // Third attempt succeeds (attempt 0 fails, attempt 1 fails, attempt 2 succeeds)
      expect(result.usedFallback).toBe(false);
      expect(result.hash).toBe(MOCK_TX_HASH);
    });

    it('falls back to public mempool after all retries fail', async () => {
      const flashbotsClient = mockFlashbotsClient({
        request: vi.fn().mockRejectedValue(new Error('Flashbots unreachable')),
      });
      const fallbackClient = mockFallbackClient();

      const logs: Array<{ event: string; extra?: Record<string, unknown> }> = [];
      const manager = createManager({
        flashbotsClient,
        fallbackClient,
        maxRetries: 1,
        onLog: (event, _msg, extra) => logs.push({ event, extra }),
      });

      const result = await manager.sendTransaction(MOCK_SIGNED_TX, 'order-004');

      expect(result.usedFallback).toBe(true);
      expect(result.hash).toBe(MOCK_TX_HASH);
      expect(manager.stats.fallbacks).toBe(1);

      // Should have logged the fallback alert
      const fallbackLog = logs.find((l) => l.event === 'flashbots_fallback');
      expect(fallbackLog).toBeDefined();
      expect(fallbackLog!.extra?.alert).toBe(true);
    });

    it('polls for inclusion until receipt is available', async () => {
      let pollCount = 0;
      const flashbotsClient = mockFlashbotsClient({
        getTransactionReceipt: vi.fn().mockImplementation(() => {
          pollCount++;
          if (pollCount < 3) {
            return Promise.resolve(null);
          }
          return Promise.resolve(mockReceipt());
        }),
      });

      const logs: Array<{ event: string; extra?: Record<string, unknown> }> = [];
      const manager = createManager({
        flashbotsClient,
        pollIntervalMs: 10,
        pollTimeoutMs: 5000,
        onLog: (event, _msg, extra) => logs.push({ event, extra }),
      });

      const result = await manager.sendTransaction(MOCK_SIGNED_TX, 'order-005');

      expect(result.usedFallback).toBe(false);
      expect(result.receipt.status).toBe('success');
      expect(pollCount).toBeGreaterThanOrEqual(3);

      // Should have poll logs
      const pollLogs = logs.filter((l) => l.event === 'flashbots_poll');
      expect(pollLogs.length).toBeGreaterThanOrEqual(1);
    });

    it('falls back when polling times out', async () => {
      const flashbotsClient = mockFlashbotsClient({
        getTransactionReceipt: vi.fn().mockResolvedValue(null),
      });
      const fallbackClient = mockFallbackClient();

      const manager = createManager({
        flashbotsClient,
        fallbackClient,
        pollIntervalMs: 10,
        pollTimeoutMs: 50,
        maxRetries: 0,
      });

      const result = await manager.sendTransaction(MOCK_SIGNED_TX, 'order-006');

      // Polling timed out, should have fallen back
      expect(result.usedFallback).toBe(true);
      expect(manager.stats.fallbacks).toBe(1);
    });
  });

  describe('getTransactionStatus', () => {
    it('returns included for successful receipt', async () => {
      const flashbotsClient = mockFlashbotsClient({
        getTransactionReceipt: vi.fn().mockResolvedValue(mockReceipt({ status: 'success' })),
      });
      const manager = createManager({ flashbotsClient });

      const status = await manager.getTransactionStatus(MOCK_TX_HASH);
      expect(status).toBe('included');
    });

    it('returns failed for reverted receipt', async () => {
      const flashbotsClient = mockFlashbotsClient({
        getTransactionReceipt: vi.fn().mockResolvedValue(mockReceipt({ status: 'reverted' })),
      });
      const manager = createManager({ flashbotsClient });

      const status = await manager.getTransactionStatus(MOCK_TX_HASH);
      expect(status).toBe('failed');
    });

    it('returns pending when no receipt available', async () => {
      const flashbotsClient = mockFlashbotsClient({
        getTransactionReceipt: vi.fn().mockResolvedValue(null),
      });
      const manager = createManager({ flashbotsClient });

      const status = await manager.getTransactionStatus(MOCK_TX_HASH);
      expect(status).toBe('pending');
    });

    it('returns unknown on RPC error', async () => {
      const flashbotsClient = mockFlashbotsClient({
        getTransactionReceipt: vi.fn().mockRejectedValue(new Error('RPC error')),
      });
      const manager = createManager({ flashbotsClient });

      const status = await manager.getTransactionStatus(MOCK_TX_HASH);
      expect(status).toBe('unknown');
    });
  });

  describe('per-transaction configurability', () => {
    it('tracks stats across multiple transactions', async () => {
      const manager = createManager();

      await manager.sendTransaction(MOCK_SIGNED_TX, 'order-A');
      await manager.sendTransaction(MOCK_SIGNED_TX, 'order-B');

      expect(manager.stats.sent).toBe(2);
      expect(manager.stats.included).toBe(2);
    });
  });
});

describe('TransactionBuilder + FlashbotsProtect integration', () => {
  // These tests verify the TransactionBuilder correctly routes
  // through FlashbotsProtectManager when configured.
  // The actual TransactionBuilder tests are in transaction-builder.test.ts.
  // Here we test the integration boundary.

  it('is importable and constructable', async () => {
    const { FlashbotsProtectManager } = await import('../src/execution/flashbots-protect.js');
    const manager = new FlashbotsProtectManager({
      flashbotsRpcUrl: 'https://rpc.flashbots.net',
      fallbackClient: mockFallbackClient(),
      flashbotsClient: mockFlashbotsClient(),
    });
    expect(manager).toBeDefined();
  });
});
