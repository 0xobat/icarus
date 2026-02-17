import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import {
  TransactionBuilder,
  NonceManager,
  type ExecutionOrder,
  type TransactionBuilderOptions,
} from '../src/execution/transaction-builder.js';

// ── Test Helpers ──────────────────────────────────

const TEST_PRIVATE_KEY = '0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80'; // Hardhat account #0

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

function mockWalletClient(overrides: Record<string, unknown> = {}) {
  return {
    sendTransaction: vi.fn().mockResolvedValue('0xabcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890'),
    chain: { id: 11155111, name: 'Sepolia' },
    ...overrides,
  } as any;
}

function createBuilder(opts: Partial<TransactionBuilderOptions> = {}) {
  return new TransactionBuilder({
    privateKey: TEST_PRIVATE_KEY,
    publicClient: mockPublicClient(),
    walletClient: mockWalletClient(),
    initialRetryDelayMs: 10, // Fast retries for tests
    onLog: () => {},
    ...opts,
  });
}

// ── NonceManager Tests ──────────────────────────────

describe('NonceManager', () => {
  it('syncs nonce from chain on first call', async () => {
    const client = mockPublicClient({ getTransactionCount: vi.fn().mockResolvedValue(10) });
    const account = { address: '0x1234' } as any;
    const nm = new NonceManager(client, account);

    const nonce = await nm.getNextNonce();
    expect(nonce).toBe(10);
    expect(client.getTransactionCount).toHaveBeenCalled();
  });

  it('increments nonce locally without re-fetching', async () => {
    const client = mockPublicClient({ getTransactionCount: vi.fn().mockResolvedValue(5) });
    const account = { address: '0x1234' } as any;
    const nm = new NonceManager(client, account);

    const n1 = await nm.getNextNonce();
    const n2 = await nm.getNextNonce();
    const n3 = await nm.getNextNonce();

    expect(n1).toBe(5);
    expect(n2).toBe(6);
    expect(n3).toBe(7);
    // Only called once for initial sync
    expect(client.getTransactionCount).toHaveBeenCalledTimes(1);
  });

  it('tracks pending nonces', async () => {
    const client = mockPublicClient({ getTransactionCount: vi.fn().mockResolvedValue(0) });
    const account = { address: '0x1234' } as any;
    const nm = new NonceManager(client, account);

    await nm.getNextNonce();
    await nm.getNextNonce();
    expect(nm.pending).toBe(2);

    nm.confirmNonce(0);
    expect(nm.pending).toBe(1);

    nm.confirmNonce(1);
    expect(nm.pending).toBe(0);
  });

  it('resyncs after all pending nonces released', async () => {
    const client = mockPublicClient({ getTransactionCount: vi.fn().mockResolvedValue(0) });
    const account = { address: '0x1234' } as any;
    const nm = new NonceManager(client, account);

    const n = await nm.getNextNonce();
    nm.releaseNonce(n);

    // Next call should resync since all pending cleared
    client.getTransactionCount.mockResolvedValue(1);
    const next = await nm.getNextNonce();
    expect(next).toBe(1);
    expect(client.getTransactionCount).toHaveBeenCalledTimes(2);
  });

  it('can force sync', async () => {
    const client = mockPublicClient({ getTransactionCount: vi.fn().mockResolvedValue(10) });
    const account = { address: '0x1234' } as any;
    const nm = new NonceManager(client, account);

    await nm.sync();
    const n = await nm.getNextNonce();
    expect(n).toBe(10);
  });
});

// ── TransactionBuilder Tests ──────────────────────────

describe('TransactionBuilder', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('throws when WALLET_PRIVATE_KEY is not set', () => {
    expect(() => new TransactionBuilder({
      privateKey: '',
      publicClient: mockPublicClient(),
      walletClient: mockWalletClient(),
    })).toThrow('WALLET_PRIVATE_KEY is not configured');
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
      const pubClient = mockPublicClient();
      const walClient = mockWalletClient();

      const builder = createBuilder({
        publicClient: pubClient,
        walletClient: walClient,
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

      // Verify wallet was called
      expect(walClient.sendTransaction).toHaveBeenCalledTimes(1);
    });

    it('handles reverted transaction', async () => {
      vi.useRealTimers();

      const pubClient = mockPublicClient({
        waitForTransactionReceipt: vi.fn().mockResolvedValue({
          status: 'reverted',
          blockNumber: BigInt(12345),
          gasUsed: BigInt(50000),
          effectiveGasPrice: BigInt('30000000000'),
        }),
      });

      const builder = createBuilder({ publicClient: pubClient });
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

  describe('retry logic', () => {
    it('retries on transient failure and succeeds', async () => {
      vi.useRealTimers();

      let callCount = 0;
      const walClient = mockWalletClient({
        sendTransaction: vi.fn().mockImplementation(() => {
          callCount++;
          if (callCount === 1) {
            return Promise.reject(new Error('connection timeout'));
          }
          return Promise.resolve('0xabcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890');
        }),
      });

      const builder = createBuilder({
        walletClient: walClient,
        initialRetryDelayMs: 1,
      });

      const order = makeOrder();
      const result = await builder.handleOrder(order);

      expect(result.status).toBe('confirmed');
      expect(result.retryCount).toBe(1);
      expect(walClient.sendTransaction).toHaveBeenCalledTimes(2);
    });

    it('gives up after max retries', async () => {
      vi.useRealTimers();

      const walClient = mockWalletClient({
        sendTransaction: vi.fn().mockRejectedValue(new Error('network error')),
      });

      const builder = createBuilder({
        walletClient: walClient,
        maxRetries: 2,
        initialRetryDelayMs: 1,
      });

      const order = makeOrder();
      const result = await builder.handleOrder(order);

      expect(result.status).toBe('failed');
      expect(result.error).toBe('network error');
      expect(result.retryCount).toBe(2);
      // 1 initial + 2 retries = 3 calls
      expect(walClient.sendTransaction).toHaveBeenCalledTimes(3);
    });

    it('does not retry non-retryable errors', async () => {
      vi.useRealTimers();

      const walClient = mockWalletClient({
        sendTransaction: vi.fn().mockRejectedValue(new Error('insufficient funds for gas')),
      });

      const builder = createBuilder({
        walletClient: walClient,
        maxRetries: 3,
        initialRetryDelayMs: 1,
      });

      const order = makeOrder();
      const result = await builder.handleOrder(order);

      expect(result.status).toBe('failed');
      expect(result.error).toContain('insufficient funds');
      // Only called once — no retries
      expect(walClient.sendTransaction).toHaveBeenCalledTimes(1);
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
      expect(sent!.extra?.nonce).toBeDefined();
    });
  });
});
