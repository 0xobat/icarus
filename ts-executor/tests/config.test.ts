import { describe, it, expect, vi, afterEach } from 'vitest';
import { base, sepolia } from 'viem/chains';

describe('resolveChain', () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.resetModules();
  });

  it('returns sepolia when CHAIN_ID is not set', async () => {
    delete process.env.CHAIN_ID;
    const { resolveChain } = await import('../src/config.js');
    expect(resolveChain()).toBe(sepolia);
  });

  it('returns base for CHAIN_ID=8453', async () => {
    vi.stubEnv('CHAIN_ID', '8453');
    const { resolveChain } = await import('../src/config.js');
    expect(resolveChain()).toBe(base);
  });

  it('returns sepolia for CHAIN_ID=11155111', async () => {
    vi.stubEnv('CHAIN_ID', '11155111');
    const { resolveChain } = await import('../src/config.js');
    expect(resolveChain()).toBe(sepolia);
  });

  it('accepts explicit chainId parameter over env', async () => {
    vi.stubEnv('CHAIN_ID', '11155111');
    const { resolveChain } = await import('../src/config.js');
    expect(resolveChain(8453)).toBe(base);
  });

  it('throws on unsupported chain ID', async () => {
    const { resolveChain } = await import('../src/config.js');
    expect(() => resolveChain(999)).toThrow('Unsupported CHAIN_ID: 999');
  });
});
