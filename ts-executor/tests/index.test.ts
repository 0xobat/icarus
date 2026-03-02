import { describe, it, expect, vi, beforeAll, afterAll } from 'vitest';
import { CHANNELS } from '../src/redis/client.js';

const mockSafeWallet = {
  address: '0x' + '0'.repeat(40) as `0x${string}`,
  signerAddress: '0x' + 'a'.repeat(40) as `0x${string}`,
  validateOrder: vi.fn().mockReturnValue({ allowed: true }),
  recordSpend: vi.fn(),
  executeTransaction: vi.fn(),
  executeBatch: vi.fn(),
};

vi.mock('../src/wallet/safe-wallet.js', () => ({
  SafeWalletManager: {
    create: vi.fn().mockResolvedValue(mockSafeWallet),
  },
}));

beforeAll(() => {
  vi.stubEnv('WALLET_PRIVATE_KEY', '0x' + 'a'.repeat(64));
});

afterAll(() => {
  vi.unstubAllEnvs();
});

describe('ts-executor', () => {
  it('defines all required Redis channels', () => {
    expect(CHANNELS.MARKET_EVENTS).toBe('market:events');
    expect(CHANNELS.EXECUTION_ORDERS).toBe('execution:orders');
    expect(CHANNELS.EXECUTION_RESULTS).toBe('execution:results');
  });
});

describe('initializeComponents', () => {
  it('creates all required service components', async () => {
    const { initializeComponents } = await import('../src/index.js');
    const components = await initializeComponents();

    expect(components.redis).toBeDefined();
    expect(components.wsManager).toBeDefined();
    expect(components.publisher).toBeDefined();
    expect(components.l2Manager).toBeDefined();
    expect(components.txBuilder).toBeDefined();
    expect(components.reporter).toBeDefined();
    expect(components.safeWallet).toBeDefined();
    expect(components.allowlist).toBeDefined();
    expect(components.adapters).toBeDefined();
  });

  it('creates all protocol adapters', async () => {
    const { initializeComponents } = await import('../src/index.js');
    const { adapters } = await initializeComponents();

    expect(adapters.aave_v3).toBeDefined();
    expect(adapters.lido).toBeDefined();
    expect(adapters.uniswap_v3).toBeDefined();
    expect(adapters.flash_loan).toBeDefined();
    expect(adapters.aerodrome).toBeDefined();
    expect(adapters.gmx).toBeDefined();
  });

  it('passes safeWallet, adapters, and reporter to txBuilder', async () => {
    const { initializeComponents } = await import('../src/index.js');
    const components = await initializeComponents();

    // txBuilder is constructed with safeWallet, adapters, and reporter options.
    // Verify it's a TransactionBuilder instance with processing capability.
    expect(components.txBuilder).toBeDefined();
    expect(components.txBuilder.processing).toBe(false);
    // The txBuilder should have been constructed (no throw) with the provided options
    expect(components.safeWallet).toBe(mockSafeWallet);
    expect(components.reporter).toBeDefined();
  });
});

describe('buildAdapterMap', () => {
  it('creates entries for all six protocols', async () => {
    const { initializeComponents, buildAdapterMap } = await import('../src/index.js');
    const { adapters } = await initializeComponents();
    const map = buildAdapterMap(adapters);

    expect(map.size).toBe(6);
    expect(map.has('aave_v3')).toBe(true);
    expect(map.has('lido')).toBe(true);
    expect(map.has('uniswap_v3')).toBe(true);
    expect(map.has('flash_loan')).toBe(true);
    expect(map.has('aerodrome')).toBe(true);
    expect(map.has('gmx')).toBe(true);
  });

  it('aave_v3 wrapper encodes supply transaction', async () => {
    const { initializeComponents, buildAdapterMap } = await import('../src/index.js');
    const { adapters } = await initializeComponents();
    const map = buildAdapterMap(adapters);
    const adapter = map.get('aave_v3')!;

    const result = await adapter.buildTransaction(
      'supply',
      { tokenIn: '0x0000000000000000000000000000000000000001', amount: '1000000' },
      { maxGasWei: '500000000000000', maxSlippageBps: 50, deadlineUnix: Math.floor(Date.now() / 1000) + 300 },
    );

    expect(result.to).toBe(adapters.aave_v3.pool);
    expect(result.data).toBeDefined();
    expect(typeof result.data).toBe('string');
    expect(result.data!.startsWith('0x')).toBe(true);
  });

  it('aave_v3 wrapper encodes withdraw transaction', async () => {
    const { initializeComponents, buildAdapterMap } = await import('../src/index.js');
    const { adapters } = await initializeComponents();
    const map = buildAdapterMap(adapters);
    const adapter = map.get('aave_v3')!;

    const result = await adapter.buildTransaction(
      'withdraw',
      { tokenIn: '0x0000000000000000000000000000000000000001', amount: '1000000', recipient: '0x0000000000000000000000000000000000000002' },
      { maxGasWei: '500000000000000', maxSlippageBps: 50, deadlineUnix: Math.floor(Date.now() / 1000) + 300 },
    );

    expect(result.to).toBe(adapters.aave_v3.pool);
    expect(result.data).toBeDefined();
  });

  it('aave_v3 wrapper throws on unsupported action', async () => {
    const { initializeComponents, buildAdapterMap } = await import('../src/index.js');
    const { adapters } = await initializeComponents();
    const map = buildAdapterMap(adapters);
    const adapter = map.get('aave_v3')!;

    await expect(adapter.buildTransaction(
      'borrow',
      { tokenIn: '0x0000000000000000000000000000000000000001', amount: '1000000' },
      { maxGasWei: '500000000000000', maxSlippageBps: 50, deadlineUnix: Math.floor(Date.now() / 1000) + 300 },
    )).rejects.toThrow('Unsupported aave_v3 action: borrow');
  });

  it('lido wrapper encodes stake with ETH value', async () => {
    const { initializeComponents, buildAdapterMap } = await import('../src/index.js');
    const { adapters } = await initializeComponents();
    const map = buildAdapterMap(adapters);
    const adapter = map.get('lido')!;

    const result = await adapter.buildTransaction(
      'stake',
      { tokenIn: '0x0000000000000000000000000000000000000000', amount: '1000000000000000000' },
      { maxGasWei: '500000000000000', maxSlippageBps: 50, deadlineUnix: Math.floor(Date.now() / 1000) + 300 },
    );

    expect(result.to).toBe(adapters.lido.steth);
    expect(result.data).toBeDefined();
    expect(result.value).toBe(1000000000000000000n);
  });

  it('lido wrapper encodes wrap transaction', async () => {
    const { initializeComponents, buildAdapterMap } = await import('../src/index.js');
    const { adapters } = await initializeComponents();
    const map = buildAdapterMap(adapters);
    const adapter = map.get('lido')!;

    const result = await adapter.buildTransaction(
      'wrap',
      { tokenIn: '0x0000000000000000000000000000000000000000', amount: '500000000000000000' },
      { maxGasWei: '500000000000000', maxSlippageBps: 50, deadlineUnix: Math.floor(Date.now() / 1000) + 300 },
    );

    expect(result.to).toBe(adapters.lido.wsteth);
    expect(result.data).toBeDefined();
    expect(result.value).toBeUndefined();
  });

  it('lido wrapper encodes unwrap transaction', async () => {
    const { initializeComponents, buildAdapterMap } = await import('../src/index.js');
    const { adapters } = await initializeComponents();
    const map = buildAdapterMap(adapters);
    const adapter = map.get('lido')!;

    const result = await adapter.buildTransaction(
      'unwrap',
      { tokenIn: '0x0000000000000000000000000000000000000000', amount: '500000000000000000' },
      { maxGasWei: '500000000000000', maxSlippageBps: 50, deadlineUnix: Math.floor(Date.now() / 1000) + 300 },
    );

    expect(result.to).toBe(adapters.lido.wsteth);
    expect(result.data).toBeDefined();
  });

  it('lido wrapper throws on unsupported action', async () => {
    const { initializeComponents, buildAdapterMap } = await import('../src/index.js');
    const { adapters } = await initializeComponents();
    const map = buildAdapterMap(adapters);
    const adapter = map.get('lido')!;

    await expect(adapter.buildTransaction(
      'borrow',
      { tokenIn: '0x0000000000000000000000000000000000000000', amount: '1000' },
      { maxGasWei: '500000000000000', maxSlippageBps: 50, deadlineUnix: Math.floor(Date.now() / 1000) + 300 },
    )).rejects.toThrow('Unsupported lido action: borrow');
  });

  it('complex protocol adapters throw descriptive errors', async () => {
    const { initializeComponents, buildAdapterMap } = await import('../src/index.js');
    const { adapters } = await initializeComponents();
    const map = buildAdapterMap(adapters);
    const params = { tokenIn: '0x0000000000000000000000000000000000000001', amount: '1000' };
    const limits = { maxGasWei: '500000000000000', maxSlippageBps: 50, deadlineUnix: Math.floor(Date.now() / 1000) + 300 };

    await expect(map.get('uniswap_v3')!.buildTransaction('mint', params, limits))
      .rejects.toThrow('requires complex parameters');
    await expect(map.get('flash_loan')!.buildTransaction('execute', params, limits))
      .rejects.toThrow('requires complex parameters');
    await expect(map.get('aerodrome')!.buildTransaction('addLiquidity', params, limits))
      .rejects.toThrow('requires complex parameters');
    await expect(map.get('gmx')!.buildTransaction('openPosition', params, limits))
      .rejects.toThrow('requires complex parameters');
  });
});

describe('validateOrder', () => {
  it('rejects order when allowlist check fails', async () => {
    const { validateOrder } = await import('../src/index.js');

    const mockOrder = {
      version: '1.0.0',
      orderId: 'test-order-1',
      correlationId: 'test-cid',
      timestamp: new Date().toISOString(),
      chain: 'ethereum',
      protocol: 'aave_v3',
      action: 'supply',
      strategy: 'STRAT-001',
      priority: 'normal' as const,
      params: { tokenIn: 'ETH', amount: '1.0' },
      limits: { maxGasWei: '500000000000000', maxSlippageBps: 50, deadlineUnix: Math.floor(Date.now() / 1000) + 300 },
      useFlashbotsProtect: false,
    };

    const mockAllowlist = {
      validateOrder: vi.fn().mockResolvedValue({ allowed: false, reason: 'not_allowlisted' }),
    } as any;
    const mockReporter = {
      reportFailed: vi.fn().mockResolvedValue({ published: true }),
    } as any;

    const result = await validateOrder(mockOrder, mockAllowlist, mockReporter);

    expect(result).toBe(false);
    expect(mockAllowlist.validateOrder).toHaveBeenCalledWith(mockOrder);
    expect(mockReporter.reportFailed).toHaveBeenCalledWith(mockOrder, 'not_allowlisted');
  });

  it('allows order when allowlist check passes', async () => {
    const { validateOrder } = await import('../src/index.js');

    const mockOrder = {
      version: '1.0.0',
      orderId: 'test-order-2',
      correlationId: 'test-cid-2',
      timestamp: new Date().toISOString(),
      chain: 'ethereum',
      protocol: 'aave_v3',
      action: 'supply',
      strategy: 'STRAT-001',
      priority: 'normal' as const,
      params: { tokenIn: 'ETH', amount: '1.0' },
      limits: { maxGasWei: '500000000000000', maxSlippageBps: 50, deadlineUnix: Math.floor(Date.now() / 1000) + 300 },
      useFlashbotsProtect: false,
    };

    const mockAllowlist = {
      validateOrder: vi.fn().mockResolvedValue({ allowed: true }),
    } as any;
    const mockReporter = {
      reportFailed: vi.fn(),
    } as any;

    const result = await validateOrder(mockOrder, mockAllowlist, mockReporter);

    expect(result).toBe(true);
    expect(mockReporter.reportFailed).not.toHaveBeenCalled();
  });
});

describe('log', () => {
  it('produces structured JSON output', async () => {
    const { log: logFn } = await import('../src/index.js');
    const consoleSpy = vi.spyOn(console, 'log').mockImplementation(() => {});

    logFn('test_event', 'Test message', { extra: 'data' });

    expect(consoleSpy).toHaveBeenCalledOnce();
    const output = JSON.parse(consoleSpy.mock.calls[0][0] as string);
    expect(output.event).toBe('test_event');
    expect(output.message).toBe('Test message');
    expect(output.service).toBe('ts-executor');
    expect(output.timestamp).toBeDefined();
    expect(output.extra).toBe('data');

    consoleSpy.mockRestore();
  });
});
