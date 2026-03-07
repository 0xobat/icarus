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
  it('creates entries for all registered protocols', async () => {
    const { buildAdapterMap } = await import('../src/index.js');
    const map = buildAdapterMap();

    expect(map.has('aave_v3')).toBe(true);
    expect(map.has('aerodrome')).toBe(true);
    expect(map.size).toBe(2);
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

  it('aerodrome wrapper encodes mint_lp', async () => {
    const { buildAdapterMap } = await import('../src/index.js');
    const map = buildAdapterMap();
    const adapter = map.get('aerodrome')!;

    const result = await adapter.buildTransaction(
      'mint_lp',
      {
        tokenIn: '0x4200000000000000000000000000000000000006',
        tokenOut: '0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913',
        amount: '1000000000000000000',
        amountB: '2500000000',
        stable: 'false',
        recipient: '0x0000000000000000000000000000000000000002',
      },
      { maxGasWei: '500000000000000', maxSlippageBps: 50, deadlineUnix: Math.floor(Date.now() / 1000) + 300 },
    );

    expect(result.to).toMatch(/^0x[0-9a-fA-F]{40}$/);
    expect(result.data).toBeDefined();
  });

  it('aerodrome wrapper encodes collect_fees', async () => {
    const { buildAdapterMap } = await import('../src/index.js');
    const map = buildAdapterMap();
    const adapter = map.get('aerodrome')!;

    const result = await adapter.buildTransaction(
      'collect_fees',
      {
        tokenIn: '0x0000000000000000000000000000000000000001',
        amount: '0',
        gauge: '0x0000000000000000000000000000000000000099',
        recipient: '0x0000000000000000000000000000000000000002',
      },
      { maxGasWei: '500000000000000', maxSlippageBps: 50, deadlineUnix: Math.floor(Date.now() / 1000) + 300 },
    );

    expect(result.to).toBe('0x0000000000000000000000000000000000000099');
    expect(result.data).toBeDefined();
  });

  it('aerodrome wrapper encodes swap', async () => {
    const { buildAdapterMap } = await import('../src/index.js');
    const map = buildAdapterMap();
    const adapter = map.get('aerodrome')!;

    const result = await adapter.buildTransaction(
      'swap',
      {
        tokenIn: '0x4200000000000000000000000000000000000006',
        tokenOut: '0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913',
        amount: '1000000000000000000',
        amountOutMin: '2400000000',
        stable: 'false',
        recipient: '0x0000000000000000000000000000000000000002',
      },
      { maxGasWei: '500000000000000', maxSlippageBps: 50, deadlineUnix: Math.floor(Date.now() / 1000) + 300 },
    );

    expect(result.to).toMatch(/^0x[0-9a-fA-F]{40}$/);
    expect(result.data).toBeDefined();
  });

  it('aerodrome wrapper throws on unsupported action', async () => {
    const { buildAdapterMap } = await import('../src/index.js');
    const map = buildAdapterMap();
    const adapter = map.get('aerodrome')!;

    await expect(adapter.buildTransaction(
      'borrow',
      { tokenIn: '0x0000000000000000000000000000000000000001', amount: '1000', recipient: '0x0000000000000000000000000000000000000002' },
      { maxGasWei: '500000000000000', maxSlippageBps: 50, deadlineUnix: Math.floor(Date.now() / 1000) + 300 },
    )).rejects.toThrow('Unsupported aerodrome action: borrow');
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
