import { describe, it, expect, vi, beforeAll, afterAll } from 'vitest';
import { CHANNELS } from '../src/redis/client.js';

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
    const components = initializeComponents();

    expect(components.redis).toBeDefined();
    expect(components.wsManager).toBeDefined();
    expect(components.publisher).toBeDefined();
    expect(components.l2Manager).toBeDefined();
    expect(components.txBuilder).toBeDefined();
    expect(components.reporter).toBeDefined();
    expect(components.flashbots).toBeDefined();
    expect(components.wallet).toBeDefined();
    expect(components.allowlist).toBeDefined();
    expect(components.adapters).toBeDefined();
  });

  it('creates all protocol adapters', async () => {
    const { initializeComponents } = await import('../src/index.js');
    const { adapters } = initializeComponents();

    expect(adapters.aave_v3).toBeDefined();
    expect(adapters.lido).toBeDefined();
    expect(adapters.uniswap_v3).toBeDefined();
    expect(adapters.flash_loan).toBeDefined();
    expect(adapters.aerodrome).toBeDefined();
    expect(adapters.gmx).toBeDefined();
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
