import { describe, it, expect } from 'vitest';
import {
  encodeStake,
  encodeWrap,
  encodeUnwrap,
  STETH_ADDRESS,
  WSTETH_ADDRESS,
  LIDO_STETH_ABI,
  WSTETH_ABI,
} from '../src/execution/lido-adapter.js';
import type { Address } from 'viem';

describe('Lido encode module', () => {
  it('exports contract addresses', () => {
    expect(STETH_ADDRESS).toMatch(/^0x[0-9a-fA-F]{40}$/);
    expect(WSTETH_ADDRESS).toMatch(/^0x[0-9a-fA-F]{40}$/);
  });

  it('exports ABIs', () => {
    expect(LIDO_STETH_ABI).toBeDefined();
    expect(WSTETH_ABI).toBeDefined();
  });

  it('encodes stake call data', () => {
    const data = encodeStake();
    expect(data).toMatch(/^0x/);
    expect(data.length).toBeGreaterThan(10);
  });

  it('encodes stake with custom referral', () => {
    const referral: Address = '0x0000000000000000000000000000000000000042';
    const data = encodeStake(referral);
    expect(data).toMatch(/^0x/);
  });

  it('encodes wrap call data', () => {
    const data = encodeWrap(1000000000000000000n);
    expect(data).toMatch(/^0x/);
    expect(data.length).toBeGreaterThan(10);
  });

  it('encodes unwrap call data', () => {
    const data = encodeUnwrap(500000000000000000n);
    expect(data).toMatch(/^0x/);
    expect(data.length).toBeGreaterThan(10);
  });

  it('produces different call data for wrap vs unwrap', () => {
    const wrapData = encodeWrap(1000000000000000000n);
    const unwrapData = encodeUnwrap(1000000000000000000n);
    expect(wrapData).not.toBe(unwrapData);
  });
});
