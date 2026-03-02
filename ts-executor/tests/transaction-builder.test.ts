import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import {
  TransactionBuilder,
  type SafeWalletLike,
  type ExecutionOrder,
  type TransactionBuilderOptions,
  type ProtocolAdapter,
} from '../src/execution/transaction-builder.js';
import type { EventReporter } from '../src/execution/event-reporter.js';
import type { Address } from 'viem';

// ── Test Helpers ──────────────────────────────────

function createMockSafeWallet(overrides?: Partial<SafeWalletLike>): SafeWalletLike {
  return {
    address: '0x1111111111111111111111111111111111111111' as Address,
    signerAddress: '0x2222222222222222222222222222222222222222' as Address,
    executeTransaction: vi.fn().mockResolvedValue({
      hash: '0xabcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890' as `0x${string}`,
      receipt: {
        status: 'success',
        blockNumber: 12345n,
        gasUsed: 100000n,
        effectiveGasPrice: 30000000000n,
        transactionHash: '0xabcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890',
      },
    }),
    executeBatch: vi.fn().mockResolvedValue({
      hash: '0xabcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890' as `0x${string}`,
      receipt: {
        status: 'success',
        blockNumber: 12345n,
        gasUsed: 42000n,
        effectiveGasPrice: 30000000000n,
        transactionHash: '0xabcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890',
      },
    }),
    validateOrder: vi.fn().mockReturnValue({ allowed: true }),
    recordSpend: vi.fn(),
    ...overrides,
  };
}

function makeOrder(overrides: Partial<ExecutionOrder> = {}): ExecutionOrder {
  return {
    version: '1.0.0',
    orderId: 'order-001',
    correlationId: 'corr-001',
    timestamp: new Date().toISOString(),
    chain: 'ethereum',
    protocol: 'aave_v3',
    action: 'supply',
    params: {
      tokenIn: '0x1234567890abcdef1234567890abcdef12345678',
      amount: '1000000000000000000',
    },
    limits: {
      maxGasWei: '50000000000000000', // 0.05 ETH
      maxSlippageBps: 50,
      deadlineUnix: Math.floor(Date.now() / 1000) + 3600, // 1 hour from now
    },
    ...overrides,
  };
}

function mockPublicClient(overrides: Record<string, unknown> = {}) {
  return {
    getGasPrice: vi.fn().mockResolvedValue(BigInt('30000000000')), // 30 gwei
    getTransactionCount: vi.fn().mockResolvedValue(5),
    waitForTransactionReceipt: vi.fn().mockResolvedValue({
      status: 'success',
      blockNumber: BigInt(12345),
      gasUsed: BigInt(100000),
      effectiveGasPrice: BigInt('30000000000'),
    }),
    getTransaction: vi.fn().mockResolvedValue(null),
    call: vi.fn(),
    ...overrides,
  } as any;
}

function createBuilder(opts: Partial<TransactionBuilderOptions> = {}) {
  return new TransactionBuilder({
    safeWallet: createMockSafeWallet(),
    publicClient: mockPublicClient(),
    initialRetryDelayMs: 10, // Fast retries for tests
    onLog: () => {},
    ...opts,
  });
}

// ── TransactionBuilder Tests ──────────────────────────

describe('TransactionBuilder', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('throws when safeWallet is not provided and order is handled', async () => {
    vi.useRealTimers();

    const builder = new TransactionBuilder({
      publicClient: mockPublicClient(),
    });
    const order = makeOrder();
    await expect(builder.handleOrder(order)).rejects.toThrow('safeWallet is required');
  });

  it('creates successfully with valid config', () => {
    const builder = createBuilder();
    expect(builder).toBeDefined();
    expect(builder.processing).toBe(false);
  });

  describe('preflight checks', () => {
    it('rejects expired deadline', async () => {
      const builder = createBuilder();
      const order = makeOrder({
        limits: {
          maxGasWei: '50000000000000000',
          maxSlippageBps: 50,
          deadlineUnix: Math.floor(Date.now() / 1000) - 60, // 60s ago
        },
      });

      const rejection = await builder.preflight(order);
      expect(rejection).toContain('deadline expired');
    });

    it('accepts valid deadline', async () => {
      const builder = createBuilder();
      const order = makeOrder();

      const rejection = await builder.preflight(order);
      expect(rejection).toBeNull();
    });

    it('rejects when gas exceeds ceiling', async () => {
      const expensiveClient = mockPublicClient({
        getGasPrice: vi.fn().mockResolvedValue(BigInt('500000000000')), // 500 gwei
      });

      const builder = createBuilder({ publicClient: expensiveClient });
      const order = makeOrder({
        limits: {
          maxGasWei: '1000000000000', // Very low gas ceiling (0.000001 ETH)
          maxSlippageBps: 50,
          deadlineUnix: Math.floor(Date.now() / 1000) + 3600,
        },
      });

      const rejection = await builder.preflight(order);
      expect(rejection).toContain('Gas cost');
      expect(rejection).toContain('exceeds ceiling');
    });

    it('allows when gas is within ceiling', async () => {
      const cheapClient = mockPublicClient({
        getGasPrice: vi.fn().mockResolvedValue(BigInt('10000000000')), // 10 gwei
      });

      const builder = createBuilder({ publicClient: cheapClient });
      const order = makeOrder();

      const rejection = await builder.preflight(order);
      expect(rejection).toBeNull();
    });

    it('does not reject on gas check failure', async () => {
      const failingClient = mockPublicClient({
        getGasPrice: vi.fn().mockRejectedValue(new Error('RPC error')),
      });

      const builder = createBuilder({ publicClient: failingClient });
      const order = makeOrder();

      const rejection = await builder.preflight(order);
      expect(rejection).toBeNull(); // Should not reject — proceed with caution
    });
  });

  describe('order handling', () => {
    it('handles successful transaction', async () => {
      vi.useRealTimers(); // Need real timers for async flow

      const logs: Array<{ event: string; extra?: Record<string, unknown> }> = [];
      const safeWallet = createMockSafeWallet();

      const builder = createBuilder({
        safeWallet,
        onLog: (event, _msg, extra) => logs.push({ event, extra }),
      });

      const order = makeOrder();
      const result = await builder.handleOrder(order);

      expect(result.status).toBe('confirmed');
      expect(result.orderId).toBe('order-001');
      expect(result.correlationId).toBe('corr-001');
      expect(result.txHash).toBeDefined();
      expect(result.blockNumber).toBe(12345);
      expect(result.gasUsed).toBe('100000');
      expect(result.version).toBe('1.0.0');

      // Verify Safe wallet was called
      expect(safeWallet.executeTransaction).toHaveBeenCalledTimes(1);
    });

    it('handles reverted transaction', async () => {
      vi.useRealTimers();

      const safeWallet = createMockSafeWallet({
        executeTransaction: vi.fn().mockResolvedValue({
          hash: '0xabcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890' as `0x${string}`,
          receipt: {
            status: 'reverted',
            blockNumber: 12345n,
            gasUsed: 50000n,
            effectiveGasPrice: 30000000000n,
          },
        }),
      });

      const builder = createBuilder({ safeWallet });
      const order = makeOrder();
      const result = await builder.handleOrder(order);

      expect(result.status).toBe('reverted');
      expect(result.gasUsed).toBe('50000');
    });

    it('publishes failed result for expired deadline', async () => {
      vi.useRealTimers();

      const builder = createBuilder();
      const order = makeOrder({
        limits: {
          maxGasWei: '50000000000000000',
          maxSlippageBps: 50,
          deadlineUnix: Math.floor(Date.now() / 1000) - 60,
        },
      });

      const result = await builder.handleOrder(order);
      expect(result.status).toBe('failed');
      expect(result.error).toContain('deadline expired');
    });
  });

  describe('Safe wallet validation', () => {
    it('calls validateOrder before execution', async () => {
      vi.useRealTimers();

      const safeWallet = createMockSafeWallet();
      const builder = createBuilder({ safeWallet });

      const order = makeOrder();
      await builder.handleOrder(order);

      expect(safeWallet.validateOrder).toHaveBeenCalledTimes(1);
      expect(safeWallet.validateOrder).toHaveBeenCalledWith(
        order.params.tokenIn as Address,
        BigInt(order.params.amount),
      );
    });

    it('rejects order when validateOrder returns not allowed', async () => {
      vi.useRealTimers();

      const safeWallet = createMockSafeWallet({
        validateOrder: vi.fn().mockReturnValue({ allowed: false, reason: 'target not on allowlist' }),
      });
      const builder = createBuilder({ safeWallet });

      const order = makeOrder();
      const result = await builder.handleOrder(order);

      expect(result.status).toBe('failed');
      expect(result.error).toContain('target not on allowlist');
      // executeTransaction should NOT have been called
      expect(safeWallet.executeTransaction).not.toHaveBeenCalled();
    });

    it('calls recordSpend on successful execution', async () => {
      vi.useRealTimers();

      const safeWallet = createMockSafeWallet();
      const builder = createBuilder({ safeWallet });

      const order = makeOrder();
      const result = await builder.handleOrder(order);

      expect(result.status).toBe('confirmed');
      expect(safeWallet.recordSpend).toHaveBeenCalledTimes(1);
      expect(safeWallet.recordSpend).toHaveBeenCalledWith(BigInt(order.params.amount));
    });

    it('does not call recordSpend on failed execution', async () => {
      vi.useRealTimers();

      const safeWallet = createMockSafeWallet({
        executeTransaction: vi.fn().mockRejectedValue(new Error('network error')),
      });
      const builder = createBuilder({ safeWallet, maxRetries: 0, initialRetryDelayMs: 1 });

      const order = makeOrder();
      const result = await builder.handleOrder(order);

      expect(result.status).toBe('failed');
      expect(safeWallet.recordSpend).not.toHaveBeenCalled();
    });

    it('does not call recordSpend on reverted execution', async () => {
      vi.useRealTimers();

      const safeWallet = createMockSafeWallet({
        executeTransaction: vi.fn().mockResolvedValue({
          hash: '0xabcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890' as `0x${string}`,
          receipt: {
            status: 'reverted',
            blockNumber: 12345n,
            gasUsed: 50000n,
            effectiveGasPrice: 30000000000n,
          },
        }),
      });
      const builder = createBuilder({ safeWallet });

      const order = makeOrder();
      const result = await builder.handleOrder(order);

      expect(result.status).toBe('reverted');
      expect(safeWallet.recordSpend).not.toHaveBeenCalled();
    });

    it('validates order via adapter target when adapter exists', async () => {
      vi.useRealTimers();

      const mockAdapter: ProtocolAdapter = {
        buildTransaction: vi.fn().mockResolvedValue({
          to: '0xAavePoolAddress1234567890abcdef12345678' as `0x${string}`,
          data: '0xdeadbeef' as `0x${string}`,
          value: BigInt(0),
        }),
      };

      const adapters = new Map<string, ProtocolAdapter>();
      adapters.set('aave_v3', mockAdapter);

      const safeWallet = createMockSafeWallet();
      const builder = createBuilder({ adapters, safeWallet });

      const order = makeOrder({ protocol: 'aave_v3', action: 'supply' });
      await builder.handleOrder(order);

      // validateOrder should be called with the adapter's target, not tokenIn
      expect(safeWallet.validateOrder).toHaveBeenCalledWith(
        '0xAavePoolAddress1234567890abcdef12345678',
        BigInt(order.params.amount),
      );
    });
  });

  describe('Flashbots compatibility', () => {
    it('logs warning and proceeds normally when useFlashbotsProtect is set', async () => {
      vi.useRealTimers();

      const logs: Array<{ event: string; extra?: Record<string, unknown> }> = [];
      const safeWallet = createMockSafeWallet();
      const builder = createBuilder({
        safeWallet,
        onLog: (event, _msg, extra) => logs.push({ event, extra }),
      });

      const order = makeOrder({ useFlashbotsProtect: true });
      const result = await builder.handleOrder(order);

      expect(result.status).toBe('confirmed');
      const flashbotsWarning = logs.find((l) => l.event === 'exec_flashbots_unsupported');
      expect(flashbotsWarning).toBeDefined();
      expect(safeWallet.executeTransaction).toHaveBeenCalledTimes(1);
    });

    it('does not log Flashbots warning when useFlashbotsProtect is false', async () => {
      vi.useRealTimers();

      const logs: Array<{ event: string; extra?: Record<string, unknown> }> = [];
      const builder = createBuilder({
        onLog: (event, _msg, extra) => logs.push({ event, extra }),
      });

      const order = makeOrder({ useFlashbotsProtect: false });
      await builder.handleOrder(order);

      const flashbotsWarning = logs.find((l) => l.event === 'exec_flashbots_unsupported');
      expect(flashbotsWarning).toBeUndefined();
    });
  });

  describe('retry logic', () => {
    it('retries on transient failure and succeeds', async () => {
      vi.useRealTimers();

      let callCount = 0;
      const safeWallet = createMockSafeWallet({
        executeTransaction: vi.fn().mockImplementation(() => {
          callCount++;
          if (callCount === 1) {
            return Promise.reject(new Error('connection timeout'));
          }
          return Promise.resolve({
            hash: '0xabcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890' as `0x${string}`,
            receipt: {
              status: 'success',
              blockNumber: 12345n,
              gasUsed: 100000n,
              effectiveGasPrice: 30000000000n,
            },
          });
        }),
      });

      const builder = createBuilder({
        safeWallet,
        initialRetryDelayMs: 1,
      });

      const order = makeOrder();
      const result = await builder.handleOrder(order);

      expect(result.status).toBe('confirmed');
      expect(result.retryCount).toBe(1);
      expect(safeWallet.executeTransaction).toHaveBeenCalledTimes(2);
    });

    it('gives up after max retries', async () => {
      vi.useRealTimers();

      const safeWallet = createMockSafeWallet({
        executeTransaction: vi.fn().mockRejectedValue(new Error('network error')),
      });

      const builder = createBuilder({
        safeWallet,
        maxRetries: 2,
        initialRetryDelayMs: 1,
      });

      const order = makeOrder();
      const result = await builder.handleOrder(order);

      expect(result.status).toBe('failed');
      expect(result.error).toBe('network error');
      expect(result.retryCount).toBe(2);
      // 1 initial + 2 retries = 3 calls
      expect(safeWallet.executeTransaction).toHaveBeenCalledTimes(3);
    });

    it('does not retry non-retryable errors', async () => {
      vi.useRealTimers();

      const safeWallet = createMockSafeWallet({
        executeTransaction: vi.fn().mockRejectedValue(new Error('insufficient funds for gas')),
      });

      const builder = createBuilder({
        safeWallet,
        maxRetries: 3,
        initialRetryDelayMs: 1,
      });

      const order = makeOrder();
      const result = await builder.handleOrder(order);

      expect(result.status).toBe('failed');
      expect(result.error).toContain('insufficient funds');
      // Only called once — no retries
      expect(safeWallet.executeTransaction).toHaveBeenCalledTimes(1);
    });
  });

  describe('result building', () => {
    it('builds result with all required schema fields', () => {
      const builder = createBuilder();
      const order = makeOrder();
      const result = builder.buildResult(order, 'confirmed', {
        txHash: '0xabc',
        blockNumber: 100,
      });

      expect(result.version).toBe('1.0.0');
      expect(result.orderId).toBe('order-001');
      expect(result.correlationId).toBe('corr-001');
      expect(result.timestamp).toMatch(/^\d{4}-\d{2}-\d{2}T/);
      expect(result.status).toBe('confirmed');
      expect(result.txHash).toBe('0xabc');
      expect(result.blockNumber).toBe(100);
    });

    it('builds failed result with error', () => {
      const builder = createBuilder();
      const order = makeOrder();
      const result = builder.buildResult(order, 'failed', {
        error: 'Something went wrong',
        retryCount: 3,
      });

      expect(result.status).toBe('failed');
      expect(result.error).toBe('Something went wrong');
      expect(result.retryCount).toBe(3);
    });
  });

  describe('logging', () => {
    it('logs order received event', async () => {
      vi.useRealTimers();

      const logs: Array<{ event: string; extra?: Record<string, unknown> }> = [];
      const builder = createBuilder({
        onLog: (event, _msg, extra) => logs.push({ event, extra }),
      });

      const order = makeOrder();
      await builder.handleOrder(order);

      const received = logs.find((l) => l.event === 'exec_order_received');
      expect(received).toBeDefined();
      expect(received!.extra?.orderId).toBe('order-001');
      expect(received!.extra?.correlationId).toBe('corr-001');
    });

    it('logs tx sent event on submission', async () => {
      vi.useRealTimers();

      const logs: Array<{ event: string; extra?: Record<string, unknown> }> = [];
      const builder = createBuilder({
        onLog: (event, _msg, extra) => logs.push({ event, extra }),
      });

      const order = makeOrder();
      await builder.handleOrder(order);

      const sent = logs.find((l) => l.event === 'exec_tx_sent');
      expect(sent).toBeDefined();
      expect(sent!.extra?.txHash).toBeDefined();
    });
  });

  // ── Protocol Adapter Routing ──────────────────

  describe('adapter routing', () => {
    it('routes order through matching protocol adapter', async () => {
      vi.useRealTimers();

      const mockAdapter: ProtocolAdapter = {
        buildTransaction: vi.fn().mockResolvedValue({
          to: '0xAavePoolAddress1234567890abcdef12345678' as `0x${string}`,
          data: '0xdeadbeef' as `0x${string}`,
          value: BigInt(0),
        }),
      };

      const adapters = new Map<string, ProtocolAdapter>();
      adapters.set('aave_v3', mockAdapter);

      const safeWallet = createMockSafeWallet();
      const builder = createBuilder({ adapters, safeWallet });

      const order = makeOrder({ protocol: 'aave_v3', action: 'supply' });
      const result = await builder.handleOrder(order);

      expect(result.status).toBe('confirmed');
      // buildTransaction called twice: once for resolveTarget, once for buildTransactionData
      expect(mockAdapter.buildTransaction).toHaveBeenCalledWith(
        'supply',
        order.params,
        order.limits,
      );
      // Verify Safe wallet received the adapter's output
      expect(safeWallet.executeTransaction).toHaveBeenCalledWith(
        expect.objectContaining({
          to: '0xAavePoolAddress1234567890abcdef12345678',
          data: '0xdeadbeef',
        }),
      );
    });

    it('falls through to raw transfer when no adapter found', async () => {
      vi.useRealTimers();

      const adapters = new Map<string, ProtocolAdapter>();
      adapters.set('lido', {
        buildTransaction: vi.fn().mockResolvedValue({ to: '0x00' as `0x${string}` }),
      });

      const safeWallet = createMockSafeWallet();
      const builder = createBuilder({ adapters, safeWallet });

      const order = makeOrder({ protocol: 'unknown_protocol' });
      const result = await builder.handleOrder(order);

      expect(result.status).toBe('confirmed');
      // Should use raw transfer fallback (value = BigInt(amount))
      expect(safeWallet.executeTransaction).toHaveBeenCalledWith(
        expect.objectContaining({
          to: order.params.tokenIn,
          value: BigInt(order.params.amount),
        }),
      );
    });

    it('passes order limits to adapter', async () => {
      vi.useRealTimers();

      const mockAdapter: ProtocolAdapter = {
        buildTransaction: vi.fn().mockResolvedValue({
          to: '0x1234567890abcdef1234567890abcdef12345678' as `0x${string}`,
          data: '0xaa' as `0x${string}`,
        }),
      };

      const adapters = new Map<string, ProtocolAdapter>();
      adapters.set('uniswap_v3', mockAdapter);

      const builder = createBuilder({ adapters });

      const order = makeOrder({
        protocol: 'uniswap_v3',
        action: 'swap',
        limits: {
          maxGasWei: '10000000000000000',
          maxSlippageBps: 100,
          deadlineUnix: Math.floor(Date.now() / 1000) + 3600,
        },
      });
      await builder.handleOrder(order);

      expect(mockAdapter.buildTransaction).toHaveBeenCalledWith(
        'swap',
        order.params,
        order.limits,
      );
    });
  });

  // ── Consolidated Result Publishing ──────────────

  describe('reporter delegation', () => {
    function mockReporter(): EventReporter {
      return {
        reportConfirmed: vi.fn().mockResolvedValue({ published: true, result: {} }),
        reportFailed: vi.fn().mockResolvedValue({ published: true, result: {} }),
        reportReverted: vi.fn().mockResolvedValue({ published: true, result: {} }),
        reportTimeout: vi.fn().mockResolvedValue({ published: true, result: {} }),
      } as unknown as EventReporter;
    }

    it('delegates confirmed result to reporter.reportConfirmed', async () => {
      vi.useRealTimers();

      const reporter = mockReporter();
      const builder = createBuilder({ reporter });

      const order = makeOrder();
      const result = await builder.handleOrder(order);

      expect(result.status).toBe('confirmed');
      expect(reporter.reportConfirmed).toHaveBeenCalledTimes(1);
      expect(reporter.reportConfirmed).toHaveBeenCalledWith(
        order,
        expect.objectContaining({ status: 'success' }),
        expect.objectContaining({ retryCount: 0 }),
      );
    });

    it('delegates failed result to reporter.reportFailed', async () => {
      vi.useRealTimers();

      const reporter = mockReporter();
      const builder = createBuilder({ reporter });

      const order = makeOrder({
        limits: {
          maxGasWei: '50000000000000000',
          maxSlippageBps: 50,
          deadlineUnix: Math.floor(Date.now() / 1000) - 60,
        },
      });
      const result = await builder.handleOrder(order);

      expect(result.status).toBe('failed');
      expect(reporter.reportFailed).toHaveBeenCalledTimes(1);
      expect(reporter.reportFailed).toHaveBeenCalledWith(
        order,
        expect.stringContaining('deadline expired'),
        undefined,
      );
    });

    it('delegates reverted result to reporter.reportReverted', async () => {
      vi.useRealTimers();

      const safeWallet = createMockSafeWallet({
        executeTransaction: vi.fn().mockResolvedValue({
          hash: '0xabcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890' as `0x${string}`,
          receipt: {
            status: 'reverted',
            blockNumber: 12345n,
            gasUsed: 50000n,
            effectiveGasPrice: 30000000000n,
          },
        }),
      });

      const reporter = mockReporter();
      const builder = createBuilder({ safeWallet, reporter });

      const order = makeOrder();
      const result = await builder.handleOrder(order);

      expect(result.status).toBe('reverted');
      expect(reporter.reportReverted).toHaveBeenCalledTimes(1);
    });

    it('falls back to direct publishResult when no reporter', async () => {
      vi.useRealTimers();

      // No reporter — just verify it doesn't crash and still works
      const builder = createBuilder();
      const order = makeOrder();
      const result = await builder.handleOrder(order);

      expect(result.status).toBe('confirmed');
      // No error thrown — direct publish path was used
    });

    it('falls back to direct publish on reporter error', async () => {
      vi.useRealTimers();

      const reporter = mockReporter();
      (reporter.reportConfirmed as any).mockRejectedValue(new Error('Reporter down'));

      const builder = createBuilder({ reporter });
      const order = makeOrder();
      const result = await builder.handleOrder(order);

      // Should still succeed — falls back to publishResult
      expect(result.status).toBe('confirmed');
      expect(reporter.reportConfirmed).toHaveBeenCalledTimes(1);
    });
  });
});
