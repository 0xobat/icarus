import { describe, it, expect } from 'vitest';
import {
  encodeSupply,
  encodeWithdraw,
  AAVE_V3_POOL,
  AAVE_POOL_ABI,
  ERC20_ABI,
} from '../src/execution/aave-v3-adapter.js';
import type { Address } from 'viem';

const MOCK_ASSET: Address = '0x0000000000000000000000000000000000000001';
const MOCK_RECIPIENT: Address = '0x0000000000000000000000000000000000000002';

describe('Aave V3 encode module', () => {
  it('exports pool address', () => {
    expect(AAVE_V3_POOL).toMatch(/^0x[0-9a-fA-F]{40}$/);
  });

  it('exports ABIs', () => {
    expect(AAVE_POOL_ABI).toBeDefined();
    expect(ERC20_ABI).toBeDefined();
  });

  it('encodes supply call data', () => {
    const data = encodeSupply(MOCK_ASSET, 1000000n, MOCK_RECIPIENT);
    expect(data).toMatch(/^0x/);
    expect(typeof data).toBe('string');
    expect(data.length).toBeGreaterThan(10);
  });

  it('encodes withdraw call data', () => {
    const data = encodeWithdraw(MOCK_ASSET, 1000000n, MOCK_RECIPIENT);
    expect(data).toMatch(/^0x/);
    expect(typeof data).toBe('string');
    expect(data.length).toBeGreaterThan(10);
  });

  it('produces different call data for supply vs withdraw', () => {
    const supplyData = encodeSupply(MOCK_ASSET, 1000000n, MOCK_RECIPIENT);
    const withdrawData = encodeWithdraw(MOCK_ASSET, 1000000n, MOCK_RECIPIENT);
    expect(supplyData).not.toBe(withdrawData);
  });
});
