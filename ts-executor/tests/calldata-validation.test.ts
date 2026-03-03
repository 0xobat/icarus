/**
 * Calldata encode/decode validation for Aave V3 and Lido adapters.
 *
 * Roundtrips encoded calldata through viem's decodeFunctionData to verify
 * correctness — catches ABI mismatches, wrong argument ordering, and
 * silent truncation before they cause on-chain reverts.
 */

import { describe, expect, it } from 'vitest';
import { type Address, decodeFunctionData } from 'viem';

import {
  AAVE_POOL_ABI,
  AAVE_V3_POOL,
  encodeSupply,
  encodeWithdraw,
} from '../src/execution/aave-v3-adapter.js';

import {
  LIDO_STETH_ABI,
  STETH_ADDRESS,
  WSTETH_ABI,
  WSTETH_ADDRESS,
  encodeStake,
  encodeUnwrap,
  encodeWrap,
} from '../src/execution/lido-adapter.js';

const DUMMY_ASSET: Address = '0x94a9D9AC8a22534E3FaCa9F4e7F2E2cf85d5E4C8';
const DUMMY_WALLET: Address = '0x1111111111111111111111111111111111111111';
const ZERO_ADDR: Address = '0x0000000000000000000000000000000000000000';

// ── Aave V3 ──────────────────────────────────────

describe('Aave V3 calldata validation', () => {
  it('AAVE_V3_POOL matches Sepolia address', () => {
    expect(AAVE_V3_POOL.toLowerCase()).toBe(
      '0x6Ae43d3271ff6888e7Fc43Fd7321a503ff738951'.toLowerCase(),
    );
  });

  it('supply encode/decode roundtrip', () => {
    const calldata = encodeSupply(DUMMY_ASSET, 1_000_000n, DUMMY_WALLET);
    const decoded = decodeFunctionData({ abi: AAVE_POOL_ABI, data: calldata });

    expect(decoded.functionName).toBe('supply');
    expect(decoded.args[0]).toBe(DUMMY_ASSET);           // asset
    expect(decoded.args[1]).toBe(1_000_000n);             // amount
    expect(decoded.args[2]).toBe(DUMMY_WALLET);           // onBehalfOf
    expect(decoded.args[3]).toBe(0);                      // referralCode
  });

  it('supply preserves various amounts', () => {
    const amounts = [1n, 1_000_000n, 10n ** 18n, (2n ** 128n) - 1n];

    for (const amount of amounts) {
      const calldata = encodeSupply(DUMMY_ASSET, amount, DUMMY_WALLET);
      const decoded = decodeFunctionData({ abi: AAVE_POOL_ABI, data: calldata });
      expect(decoded.args[1]).toBe(amount);
    }
  });

  it('withdraw encode/decode roundtrip', () => {
    const calldata = encodeWithdraw(DUMMY_ASSET, 5_000_000n, DUMMY_WALLET);
    const decoded = decodeFunctionData({ abi: AAVE_POOL_ABI, data: calldata });

    expect(decoded.functionName).toBe('withdraw');
    expect(decoded.args[0]).toBe(DUMMY_ASSET);            // asset
    expect(decoded.args[1]).toBe(5_000_000n);             // amount
    expect(decoded.args[2]).toBe(DUMMY_WALLET);           // to
  });

  it('withdraw max uint256 for full withdrawal', () => {
    const maxUint = (2n ** 256n) - 1n;
    const calldata = encodeWithdraw(DUMMY_ASSET, maxUint, DUMMY_WALLET);
    const decoded = decodeFunctionData({ abi: AAVE_POOL_ABI, data: calldata });
    expect(decoded.args[1]).toBe(maxUint);
  });

  it('supply vs withdraw have different selectors', () => {
    const supplyData = encodeSupply(DUMMY_ASSET, 1n, DUMMY_WALLET);
    const withdrawData = encodeWithdraw(DUMMY_ASSET, 1n, DUMMY_WALLET);
    expect(supplyData.slice(0, 10)).not.toBe(withdrawData.slice(0, 10));
  });

  it('supply calldata is ABI-decodable', () => {
    const calldata = encodeSupply(DUMMY_ASSET, 10n ** 18n, DUMMY_WALLET);
    expect(() =>
      decodeFunctionData({ abi: AAVE_POOL_ABI, data: calldata }),
    ).not.toThrow();
  });
});

// ── Lido ──────────────────────────────────────

describe('Lido calldata validation', () => {
  it('stETH address matches Sepolia', () => {
    expect(STETH_ADDRESS.toLowerCase()).toBe(
      '0x3e3FE7dBc6B4C189E7128855dD526361c49b40Af'.toLowerCase(),
    );
  });

  it('wstETH address matches Sepolia', () => {
    expect(WSTETH_ADDRESS.toLowerCase()).toBe(
      '0xB82381A3fBD3FaFA77B3a7bE693342618240067b'.toLowerCase(),
    );
  });

  it('stake encode/decode with default zero referral', () => {
    const calldata = encodeStake();
    const decoded = decodeFunctionData({ abi: LIDO_STETH_ABI, data: calldata });

    expect(decoded.functionName).toBe('submit');
    expect(decoded.args[0]).toBe(ZERO_ADDR);
  });

  it('stake encode/decode with custom referral', () => {
    const referral: Address = '0x2222222222222222222222222222222222222222';
    const calldata = encodeStake(referral);
    const decoded = decodeFunctionData({ abi: LIDO_STETH_ABI, data: calldata });

    expect(decoded.functionName).toBe('submit');
    expect(decoded.args[0]).toBe(referral);
  });

  it('wrap encode/decode roundtrip', () => {
    const amount = 10n ** 18n;
    const calldata = encodeWrap(amount);
    const decoded = decodeFunctionData({ abi: WSTETH_ABI, data: calldata });

    expect(decoded.functionName).toBe('wrap');
    expect(decoded.args[0]).toBe(amount);
  });

  it('unwrap encode/decode roundtrip', () => {
    const amount = 5n * 10n ** 17n;
    const calldata = encodeUnwrap(amount);
    const decoded = decodeFunctionData({ abi: WSTETH_ABI, data: calldata });

    expect(decoded.functionName).toBe('unwrap');
    expect(decoded.args[0]).toBe(amount);
  });

  it('wrap vs unwrap have different selectors', () => {
    const wrapData = encodeWrap(1n);
    const unwrapData = encodeUnwrap(1n);
    expect(wrapData.slice(0, 10)).not.toBe(unwrapData.slice(0, 10));
  });

  it('stake calldata is ABI-decodable', () => {
    const calldata = encodeStake();
    expect(() =>
      decodeFunctionData({ abi: LIDO_STETH_ABI, data: calldata }),
    ).not.toThrow();
  });

  it('wrap calldata is ABI-decodable', () => {
    const calldata = encodeWrap(10n ** 18n);
    expect(() =>
      decodeFunctionData({ abi: WSTETH_ABI, data: calldata }),
    ).not.toThrow();
  });
});
