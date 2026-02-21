import { describe, it, expect, vi, beforeEach } from 'vitest';
import { FlashbotsProtectManager, type FlashbotsResult } from '../src/execution/flashbots-protect.js';
import { privateKeyToAccount } from 'viem/accounts';

// ── Test helpers ──────────────────────────────────

const TEST_PRIVATE_KEY = '0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80';
const TEST_ACCOUNT = privateKeyToAccount(TEST_PRIVATE_KEY);
const TEST_TO = '0x70997970C51812dc3A010C7d01b50e0d17dc79C8' as `0x${string}`;

function createMockClients(opts: {
  flashbotsSendFn?: (...args: any[]) => Promise<`0x${string}`>;
  publicSendFn?: (...args: any[]) => Promise<`0x${string}`>;
  getReceiptFn?: (...args: any[]) => Promise<any>;
  waitForReceiptFn?: (...args: any[]) => Promise<any>;
} = {}) {
  const flashbotsClient = {
    sendTransaction: opts.flashbotsSendFn ?? vi.fn().mockResolvedValue('0xflash123'),
    chain: { id: 11155111, name: 'Sepolia' },
  } as any;

  const publicWalletClient = {
    sendTransaction: opts.publicSendFn ?? vi.fn().mockResolvedValue('0xpub123'),
    chain: { id: 11155111, name: 'Sepolia' },
  } as any;

  const publicClient = {
    getTransactionReceipt: opts.getReceiptFn ?? vi.fn().mockResolvedValue({
      status: 'success',
      blockNumber: 100n,
      gasUsed: 21000n,
      effectiveGasPrice: 30000000000n,
      transactionHash: '0xflash123',
    }),
    waitForTransactionReceipt: opts.waitForReceiptFn ?? vi.fn().mockResolvedValue({
      status: 'success',
      blockNumber: 100n,
      gasUsed: 21000n,
      effectiveGasPrice: 30000000000n,
    }),
  } as any;

  return { flashbotsClient, publicWalletClient, publicClient };
}

// ── Tests ──────────────────────────────────────────

describe('FlashbotsProtectManager', () => {
  describe('constructor', () => {
    it('creates with default options', () => {
      const manager = new FlashbotsProtectManager({
        publicClient: {} as any,
        flashbotsClient: {} as any,
        publicWalletClient: {} as any,
      });
      expect(manager).toBeDefined();
      expect(manager.stats.totalSent).toBe(0);
    });
  });

  describe('sendTransaction via Flashbots', () => {
    it('sends transaction through Flashbots and polls for inclusion', async () => {
      const { flashbotsClient, publicWalletClient, publicClient } = createMockClients();
      const logs: Array<{ event: string }> = [];

      const manager = new FlashbotsProtectManager({
        publicClient,
        flashbotsClient,
        publicWalletClient,
        pollIntervalMs: 10,
        timeoutMs: 5000,
        onLog: (event) => logs.push({ event }),
      });

      const result = await manager.sendTransaction({
        to: TEST_TO,
        value: 1000n,
        account: TEST_ACCOUNT,
      });

      expect(result.usedFlashbots).toBe(true);
      expect(result.status).toBe('INCLUDED');
      expect(result.receipt).toBeDefined();
      expect(result.latencyMs).toBeGreaterThanOrEqual(0);
      expect(flashbotsClient.sendTransaction).toHaveBeenCalled();
      expect(manager.stats.flashbotsSent).toBe(1);
    });

    it('falls back to public mempool when Flashbots fails', async () => {
      const flashbotsSendFn = vi.fn().mockRejectedValue(new Error('Flashbots unreachable'));
      const { flashbotsClient, publicWalletClient, publicClient } = createMockClients({ flashbotsSendFn });
      const logs: Array<{ event: string }> = [];

      const manager = new FlashbotsProtectManager({
        publicClient,
        flashbotsClient,
        publicWalletClient,
        maxRetries: 1,
        onLog: (event) => logs.push({ event }),
      });

      const result = await manager.sendTransaction({
        to: TEST_TO,
        value: 1000n,
        account: TEST_ACCOUNT,
      });

      expect(result.usedFlashbots).toBe(false);
      expect(publicWalletClient.sendTransaction).toHaveBeenCalled();
      expect(manager.stats.publicFallback).toBe(1);
      expect(logs.some((l) => l.event === 'flashbots_fallback')).toBe(true);
    });

    it('retries Flashbots submission before falling back', async () => {
      const flashbotsSendFn = vi.fn().mockRejectedValue(new Error('Flashbots error'));
      const { flashbotsClient, publicWalletClient, publicClient } = createMockClients({ flashbotsSendFn });

      const manager = new FlashbotsProtectManager({
        publicClient,
        flashbotsClient,
        publicWalletClient,
        maxRetries: 2,
      });

      await manager.sendTransaction({
        to: TEST_TO,
        value: 1000n,
        account: TEST_ACCOUNT,
      });

      // Should have tried 3 times (initial + 2 retries)
      expect(flashbotsSendFn).toHaveBeenCalledTimes(3);
    });
  });

  describe('opt-out', () => {
    it('uses public mempool when useFlashbots is false', async () => {
      const { flashbotsClient, publicWalletClient, publicClient } = createMockClients();
      const logs: Array<{ event: string }> = [];

      const manager = new FlashbotsProtectManager({
        publicClient,
        flashbotsClient,
        publicWalletClient,
        onLog: (event) => logs.push({ event }),
      });

      const result = await manager.sendTransaction(
        { to: TEST_TO, value: 1000n, account: TEST_ACCOUNT },
        false, // opt-out
      );

      expect(result.usedFlashbots).toBe(false);
      expect(flashbotsClient.sendTransaction).not.toHaveBeenCalled();
      expect(publicWalletClient.sendTransaction).toHaveBeenCalled();
      expect(logs.some((l) => l.event === 'flashbots_opt_out')).toBe(true);
    });
  });

  describe('timeout handling', () => {
    it('returns PENDING when Flashbots TX not included within timeout', async () => {
      const getReceiptFn = vi.fn().mockRejectedValue(new Error('not found'));
      const flashbotsSendFn = vi.fn().mockResolvedValue('0xflash123' as `0x${string}`);
      const { flashbotsClient, publicWalletClient, publicClient } = createMockClients({
        flashbotsSendFn,
        getReceiptFn,
      });

      const manager = new FlashbotsProtectManager({
        publicClient,
        flashbotsClient,
        publicWalletClient,
        pollIntervalMs: 10,
        timeoutMs: 50,
      });

      const result = await manager.sendTransaction({
        to: TEST_TO,
        value: 1000n,
        account: TEST_ACCOUNT,
      });

      expect(result.status).toBe('PENDING');
      expect(result.usedFlashbots).toBe(true);
      expect(manager.stats.timeouts).toBe(1);
    });
  });

  describe('stats', () => {
    it('tracks total transactions sent', async () => {
      const { flashbotsClient, publicWalletClient, publicClient } = createMockClients();

      const manager = new FlashbotsProtectManager({
        publicClient,
        flashbotsClient,
        publicWalletClient,
        pollIntervalMs: 10,
      });

      await manager.sendTransaction({ to: TEST_TO, value: 100n, account: TEST_ACCOUNT });
      await manager.sendTransaction({ to: TEST_TO, value: 200n, account: TEST_ACCOUNT }, false);

      expect(manager.stats.totalSent).toBe(2);
      expect(manager.stats.flashbotsSent).toBe(1);
      expect(manager.stats.publicFallback).toBe(1);
    });
  });

  describe('latency tracking', () => {
    it('records latency in result', async () => {
      const { flashbotsClient, publicWalletClient, publicClient } = createMockClients();

      const manager = new FlashbotsProtectManager({
        publicClient,
        flashbotsClient,
        publicWalletClient,
        pollIntervalMs: 10,
      });

      const result = await manager.sendTransaction({
        to: TEST_TO,
        value: 1000n,
        account: TEST_ACCOUNT,
      });

      expect(result.latencyMs).toBeGreaterThanOrEqual(0);
      expect(typeof result.latencyMs).toBe('number');
    });
  });

  describe('logging', () => {
    it('logs on Flashbots submission', async () => {
      const { flashbotsClient, publicWalletClient, publicClient } = createMockClients();
      const logs: Array<{ event: string; extra?: Record<string, unknown> }> = [];

      const manager = new FlashbotsProtectManager({
        publicClient,
        flashbotsClient,
        publicWalletClient,
        pollIntervalMs: 10,
        onLog: (event, _msg, extra) => logs.push({ event, extra }),
      });

      await manager.sendTransaction({ to: TEST_TO, value: 1000n, account: TEST_ACCOUNT });

      expect(logs.some((l) => l.event === 'flashbots_submitted')).toBe(true);
      expect(logs.some((l) => l.event === 'flashbots_included')).toBe(true);
    });

    it('logs on public fallback', async () => {
      const flashbotsSendFn = vi.fn().mockRejectedValue(new Error('fail'));
      const { flashbotsClient, publicWalletClient, publicClient } = createMockClients({ flashbotsSendFn });
      const logs: Array<{ event: string }> = [];

      const manager = new FlashbotsProtectManager({
        publicClient,
        flashbotsClient,
        publicWalletClient,
        maxRetries: 0,
        onLog: (event) => logs.push({ event }),
      });

      await manager.sendTransaction({ to: TEST_TO, value: 1000n, account: TEST_ACCOUNT });

      expect(logs.some((l) => l.event === 'flashbots_fallback')).toBe(true);
      expect(logs.some((l) => l.event === 'public_submitted')).toBe(true);
    });
  });

  describe('public mempool failure', () => {
    it('returns FAILED status when public send also fails', async () => {
      const flashbotsSendFn = vi.fn().mockRejectedValue(new Error('Flashbots down'));
      const publicSendFn = vi.fn().mockRejectedValue(new Error('Public RPC down'));
      const { flashbotsClient, publicWalletClient, publicClient } = createMockClients({
        flashbotsSendFn,
        publicSendFn,
      });

      const manager = new FlashbotsProtectManager({
        publicClient,
        flashbotsClient,
        publicWalletClient,
        maxRetries: 0,
      });

      const result = await manager.sendTransaction({
        to: TEST_TO,
        value: 1000n,
        account: TEST_ACCOUNT,
      });

      expect(result.status).toBe('FAILED');
      expect(result.usedFlashbots).toBe(false);
    });
  });
});
