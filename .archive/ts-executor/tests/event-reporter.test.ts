import { describe, it, expect, vi, beforeEach } from 'vitest';
import { EventReporter } from '../src/execution/event-reporter.js';
import { type ExecutionOrder } from '../src/execution/transaction-builder.js';
import { CHANNELS } from '../src/redis/client.js';
import { type TransactionReceipt } from 'viem';

// ── Test helpers ──────────────────────────────────

function createMockOrder(overrides: Partial<ExecutionOrder> = {}): ExecutionOrder {
  return {
    version: '1.0.0',
    orderId: 'order-123',
    correlationId: 'corr-456',
    timestamp: new Date().toISOString(),
    chain: 'ethereum',
    protocol: 'aave_v3',
    action: 'supply',
    params: { tokenIn: '0xtoken', amount: '1000000' },
    limits: { maxGasWei: '1000000000000', maxSlippageBps: 50, deadlineUnix: Math.floor(Date.now() / 1000) + 3600 },
    ...overrides,
  };
}

function createMockReceipt(overrides: Partial<TransactionReceipt> = {}): TransactionReceipt {
  return {
    transactionHash: '0xtxhash123',
    blockNumber: 100n,
    gasUsed: 150000n,
    effectiveGasPrice: 30000000000n,
    status: 'success',
    blockHash: '0xblockhash',
    contractAddress: null,
    cumulativeGasUsed: 150000n,
    from: '0xfrom',
    logsBloom: '0x',
    to: '0xto',
    transactionIndex: 0,
    type: 'eip1559',
    logs: [],
    root: undefined,
    blobGasPrice: undefined,
    blobGasUsed: undefined,
    ...overrides,
  } as any;
}

function createMockRedis(opts: { publishFn?: (...args: any[]) => Promise<void> } = {}) {
  return {
    publish: opts.publishFn ?? vi.fn().mockResolvedValue(undefined),
    subscribe: vi.fn(),
  } as any;
}

function createMockPublicClient() {
  return {
    getTransaction: vi.fn().mockResolvedValue({
      to: '0xcontract',
      input: '0xdata',
      value: 0n,
      blockNumber: 100n,
    }),
    call: vi.fn().mockRejectedValue(new Error('execution reverted: Insufficient balance')),
  } as any;
}

// ── Tests ──────────────────────────────────────────

describe('EventReporter', () => {
  describe('constructor', () => {
    it('creates with default options', () => {
      const reporter = new EventReporter({ publicClient: createMockPublicClient() });
      expect(reporter).toBeDefined();
      expect(reporter.stats.reported).toBe(0);
    });
  });

  describe('reportConfirmed', () => {
    it('publishes a confirmed result to Redis', async () => {
      const publishFn = vi.fn().mockResolvedValue(undefined);
      const redis = createMockRedis({ publishFn });
      const reporter = new EventReporter({ publicClient: createMockPublicClient() });
      reporter.attach(redis);

      const order = createMockOrder();
      const receipt = createMockReceipt();

      const report = await reporter.reportConfirmed(order, receipt);

      expect(report.published).toBe(true);
      expect(report.result.status).toBe('confirmed');
      expect(report.result.orderId).toBe('order-123');
      expect(report.result.txHash).toBe('0xtxhash123');
      expect(report.result.blockNumber).toBe(100);
      expect(report.result.gasUsed).toBe('150000');
      expect(report.result.effectiveGasPrice).toBe('30000000000');
      expect(publishFn).toHaveBeenCalledWith(
        CHANNELS.EXECUTION_RESULTS,
        expect.objectContaining({ status: 'confirmed' }),
      );
    });

    it('includes fill price and amount out when provided', async () => {
      const redis = createMockRedis();
      const reporter = new EventReporter({ publicClient: createMockPublicClient() });
      reporter.attach(redis);

      const report = await reporter.reportConfirmed(
        createMockOrder(),
        createMockReceipt(),
        { fillPrice: '1850.50', amountOut: '1000000000' },
      );

      expect(report.result.fillPrice).toBe('1850.50');
      expect(report.result.amountOut).toBe('1000000000');
    });

    it('includes retry count when provided', async () => {
      const redis = createMockRedis();
      const reporter = new EventReporter({ publicClient: createMockPublicClient() });
      reporter.attach(redis);

      const report = await reporter.reportConfirmed(
        createMockOrder(),
        createMockReceipt(),
        { retryCount: 2 },
      );

      expect(report.result.retryCount).toBe(2);
    });
  });

  describe('reportFailed', () => {
    it('publishes a failure result', async () => {
      const publishFn = vi.fn().mockResolvedValue(undefined);
      const redis = createMockRedis({ publishFn });
      const reporter = new EventReporter({ publicClient: createMockPublicClient() });
      reporter.attach(redis);

      const report = await reporter.reportFailed(
        createMockOrder(),
        'Gas price too high',
        1,
      );

      expect(report.published).toBe(true);
      expect(report.result.status).toBe('failed');
      expect(report.result.error).toBe('Gas price too high');
      expect(report.result.retryCount).toBe(1);
      expect(reporter.stats.failed).toBe(1);
    });
  });

  describe('reportReverted', () => {
    it('publishes a reverted result with decoded reason', async () => {
      const publicClient = createMockPublicClient();
      const redis = createMockRedis();
      const reporter = new EventReporter({ publicClient });
      reporter.attach(redis);

      const report = await reporter.reportReverted(
        createMockOrder(),
        createMockReceipt({ status: 'reverted' } as any),
      );

      expect(report.published).toBe(true);
      expect(report.result.status).toBe('reverted');
      expect(report.result.revertReason).toBeDefined();
      expect(report.result.revertReason).toContain('Insufficient balance');
      expect(reporter.stats.reverted).toBe(1);
    });

    it('handles decode failure gracefully', async () => {
      const publicClient = createMockPublicClient();
      publicClient.getTransaction = vi.fn().mockRejectedValue(new Error('not found'));
      const redis = createMockRedis();
      const reporter = new EventReporter({ publicClient });
      reporter.attach(redis);

      const report = await reporter.reportReverted(
        createMockOrder(),
        createMockReceipt({ status: 'reverted' } as any),
      );

      expect(report.result.revertReason).toBe('Unable to fetch revert reason');
    });
  });

  describe('reportTimeout', () => {
    it('publishes a timeout result', async () => {
      const redis = createMockRedis();
      const reporter = new EventReporter({ publicClient: createMockPublicClient() });
      reporter.attach(redis);

      const report = await reporter.reportTimeout(
        createMockOrder(),
        '0xtxhash',
        3,
      );

      expect(report.published).toBe(true);
      expect(report.result.status).toBe('timeout');
      expect(report.result.txHash).toBe('0xtxhash');
      expect(report.result.retryCount).toBe(3);
      expect(reporter.stats.timeouts).toBe(1);
    });

    it('works without txHash', async () => {
      const redis = createMockRedis();
      const reporter = new EventReporter({ publicClient: createMockPublicClient() });
      reporter.attach(redis);

      const report = await reporter.reportTimeout(createMockOrder());
      expect(report.result.txHash).toBeUndefined();
    });
  });

  describe('reportFromReceipt', () => {
    it('auto-detects confirmed status', async () => {
      const redis = createMockRedis();
      const reporter = new EventReporter({ publicClient: createMockPublicClient() });
      reporter.attach(redis);

      const report = await reporter.reportFromReceipt(
        createMockOrder(),
        createMockReceipt({ status: 'success' }),
      );

      expect(report.result.status).toBe('confirmed');
      expect(reporter.stats.confirmed).toBe(1);
    });

    it('auto-detects reverted status', async () => {
      const redis = createMockRedis();
      const reporter = new EventReporter({ publicClient: createMockPublicClient() });
      reporter.attach(redis);

      const report = await reporter.reportFromReceipt(
        createMockOrder(),
        createMockReceipt({ status: 'reverted' } as any),
      );

      expect(report.result.status).toBe('reverted');
      expect(reporter.stats.reverted).toBe(1);
    });
  });

  describe('error handling', () => {
    it('returns published=false when Redis not attached', async () => {
      const reporter = new EventReporter({ publicClient: createMockPublicClient() });
      const report = await reporter.reportConfirmed(createMockOrder(), createMockReceipt());

      expect(report.published).toBe(false);
      expect(reporter.stats.errors).toBe(1);
    });

    it('returns published=false on Redis publish failure', async () => {
      const publishFn = vi.fn().mockRejectedValue(new Error('Redis down'));
      const redis = createMockRedis({ publishFn });
      const reporter = new EventReporter({ publicClient: createMockPublicClient() });
      reporter.attach(redis);

      const report = await reporter.reportConfirmed(createMockOrder(), createMockReceipt());

      expect(report.published).toBe(false);
      expect(reporter.stats.errors).toBe(1);
    });
  });

  describe('stats tracking', () => {
    it('tracks all report types', async () => {
      const redis = createMockRedis();
      const reporter = new EventReporter({ publicClient: createMockPublicClient() });
      reporter.attach(redis);

      await reporter.reportConfirmed(createMockOrder(), createMockReceipt());
      await reporter.reportFailed(createMockOrder({ orderId: 'o2' }), 'error');
      await reporter.reportReverted(createMockOrder({ orderId: 'o3' }), createMockReceipt({ status: 'reverted' } as any));
      await reporter.reportTimeout(createMockOrder({ orderId: 'o4' }));

      expect(reporter.stats.reported).toBe(4);
      expect(reporter.stats.confirmed).toBe(1);
      expect(reporter.stats.failed).toBe(1);
      expect(reporter.stats.reverted).toBe(1);
      expect(reporter.stats.timeouts).toBe(1);
    });
  });

  describe('logging', () => {
    it('logs on successful publish', async () => {
      const logs: Array<{ event: string; extra?: Record<string, unknown> }> = [];
      const redis = createMockRedis();
      const reporter = new EventReporter({
        publicClient: createMockPublicClient(),
        onLog: (event, _msg, extra) => logs.push({ event, extra }),
      });
      reporter.attach(redis);

      await reporter.reportConfirmed(createMockOrder(), createMockReceipt());

      expect(logs.some((l) => l.event === 'reporter_published')).toBe(true);
      const pubLog = logs.find((l) => l.event === 'reporter_published');
      expect(pubLog?.extra?.orderId).toBe('order-123');
      expect(pubLog?.extra?.status).toBe('confirmed');
    });
  });

  describe('schema compliance', () => {
    it('produces results with all required fields', async () => {
      const redis = createMockRedis();
      const reporter = new EventReporter({ publicClient: createMockPublicClient() });
      reporter.attach(redis);

      const report = await reporter.reportConfirmed(createMockOrder(), createMockReceipt());
      const result = report.result;

      // Required fields per execution-results.schema.json
      expect(result.version).toBe('1.0.0');
      expect(result.orderId).toBeDefined();
      expect(result.correlationId).toBeDefined();
      expect(result.timestamp).toMatch(/^\d{4}-\d{2}-\d{2}T/);
      expect(['confirmed', 'failed', 'reverted', 'timeout']).toContain(result.status);
    });
  });
});
