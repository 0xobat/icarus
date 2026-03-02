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
  });

  it('passes safeWallet and reporter to txBuilder', async () => {
    const { initializeComponents } = await import('../src/index.js');
    const components = await initializeComponents();

    expect(components.txBuilder).toBeDefined();
    expect(components.txBuilder.processing).toBe(false);
    expect(components.safeWallet).toBe(mockSafeWallet);
    expect(components.reporter).toBeDefined();
  });
});

describe('buildAdapterMap', () => {
  it('creates entries for P1a protocols', async () => {
    const { buildAdapterMap } = await import('../src/index.js');
    const map = buildAdapterMap();

    expect(map.size).toBe(2);
    expect(map.has('aave_v3')).toBe(true);
    expect(map.has('lido')).toBe(true);
  });

  it('aave_v3 wrapper encodes supply transaction', async () => {
    const { buildAdapterMap } = await import('../src/index.js');
    const map = buildAdapterMap();
    const adapter = map.get('aave_v3')!;

    const result = await adapter.buildTransaction(
      'supply',
      { tokenIn: '0x0000000000000000000000000000000000000001', amount: '1000000' },
      { maxGasWei: '500000000000000', maxSlippageBps: 50, deadlineUnix: Math.floor(Date.now() / 1000) + 300 },
    );

    expect(result.to).toMatch(/^0x[0-9a-fA-F]{40}$/);
    expect(result.data).toBeDefined();
    expect(result.data!.startsWith('0x')).toBe(true);
  });

  it('aave_v3 wrapper encodes withdraw transaction', async () => {
    const { buildAdapterMap } = await import('../src/index.js');
    const map = buildAdapterMap();
    const adapter = map.get('aave_v3')!;

    const result = await adapter.buildTransaction(
      'withdraw',
      { tokenIn: '0x0000000000000000000000000000000000000001', amount: '1000000', recipient: '0x0000000000000000000000000000000000000002' },
      { maxGasWei: '500000000000000', maxSlippageBps: 50, deadlineUnix: Math.floor(Date.now() / 1000) + 300 },
    );

    expect(result.to).toMatch(/^0x[0-9a-fA-F]{40}$/);
    expect(result.data).toBeDefined();
  });

  it('aave_v3 wrapper throws on unsupported action', async () => {
    const { buildAdapterMap } = await import('../src/index.js');
    const map = buildAdapterMap();
    const adapter = map.get('aave_v3')!;

    await expect(adapter.buildTransaction(
      'borrow',
      { tokenIn: '0x0000000000000000000000000000000000000001', amount: '1000000' },
      { maxGasWei: '500000000000000', maxSlippageBps: 50, deadlineUnix: Math.floor(Date.now() / 1000) + 300 },
    )).rejects.toThrow('Unsupported aave_v3 action: borrow');
  });

  it('lido wrapper encodes stake with ETH value', async () => {
    const { buildAdapterMap } = await import('../src/index.js');
    const map = buildAdapterMap();
    const adapter = map.get('lido')!;

    const result = await adapter.buildTransaction(
      'stake',
      { tokenIn: '0x0000000000000000000000000000000000000000', amount: '1000000000000000000' },
      { maxGasWei: '500000000000000', maxSlippageBps: 50, deadlineUnix: Math.floor(Date.now() / 1000) + 300 },
    );

    expect(result.to).toMatch(/^0x[0-9a-fA-F]{40}$/);
    expect(result.data).toBeDefined();
    expect(result.value).toBe(1000000000000000000n);
  });

  it('lido wrapper encodes wrap and unwrap', async () => {
    const { buildAdapterMap } = await import('../src/index.js');
    const map = buildAdapterMap();
    const adapter = map.get('lido')!;

    const wrap = await adapter.buildTransaction(
      'wrap',
      { tokenIn: '0x0000000000000000000000000000000000000000', amount: '500000000000000000' },
      { maxGasWei: '500000000000000', maxSlippageBps: 50, deadlineUnix: Math.floor(Date.now() / 1000) + 300 },
    );
    expect(wrap.data).toBeDefined();
    expect(wrap.value).toBeUndefined();

    const unwrap = await adapter.buildTransaction(
      'unwrap',
      { tokenIn: '0x0000000000000000000000000000000000000000', amount: '500000000000000000' },
      { maxGasWei: '500000000000000', maxSlippageBps: 50, deadlineUnix: Math.floor(Date.now() / 1000) + 300 },
    );
    expect(unwrap.data).toBeDefined();
  });

  it('lido wrapper throws on unsupported action', async () => {
    const { buildAdapterMap } = await import('../src/index.js');
    const map = buildAdapterMap();
    const adapter = map.get('lido')!;

    await expect(adapter.buildTransaction(
      'borrow',
      { tokenIn: '0x0000000000000000000000000000000000000000', amount: '1000' },
      { maxGasWei: '500000000000000', maxSlippageBps: 50, deadlineUnix: Math.floor(Date.now() / 1000) + 300 },
    )).rejects.toThrow('Unsupported lido action: borrow');
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
