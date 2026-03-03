/**
 * Calldata encode/decode validation for Uniswap V3 adapter.
 *
 * Roundtrips encoded calldata through viem's decodeFunctionData to verify
 * correctness — catches ABI mismatches, wrong argument ordering, and
 * silent truncation before they cause on-chain reverts.
 */

import { describe, expect, it } from 'vitest';
import { type Address, decodeFunctionData } from 'viem';

import {
  FACTORY_ABI,
  FACTORY_ADDRESS,
  POSITION_MANAGER_ABI,
  POSITION_MANAGER_ADDRESS,
  encodeBurn,
  encodeCollect,
  encodeDecreaseLiquidity,
  encodeIncreaseLiquidity,
  encodeMint,
  type MintParams,
  type IncreaseLiquidityParams,
  type DecreaseLiquidityParams,
  type CollectParams,
} from '../src/execution/uniswap-v3-adapter.js';

const TOKEN_A: Address = '0x94a9D9AC8a22534E3FaCa9F4e7F2E2cf85d5E4C8';
const TOKEN_B: Address = '0xC558DBdd856501FCd9aaF1E62eae57A9F0629a3c';
const RECIPIENT: Address = '0x1111111111111111111111111111111111111111';
const MAX_UINT128 = (2n ** 128n) - 1n;

// ── Addresses ──────────────────────────────────────

describe('Uniswap V3 contract addresses', () => {
  it('PositionManager matches Sepolia deployment', () => {
    expect(POSITION_MANAGER_ADDRESS.toLowerCase()).toBe(
      '0x1238536071E1c677A632429e3655c799b22cDA52'.toLowerCase(),
    );
  });

  it('Factory matches Sepolia deployment', () => {
    expect(FACTORY_ADDRESS.toLowerCase()).toBe(
      '0x0227628f3F023bb0B980b67D528571c95c6DaC1c'.toLowerCase(),
    );
  });
});

// ── Mint ──────────────────────────────────────

describe('Uniswap V3 mint', () => {
  const baseMintParams: MintParams = {
    token0: TOKEN_A,
    token1: TOKEN_B,
    fee: 3000,
    tickLower: -887220,
    tickUpper: 887220,
    amount0Desired: 10n ** 18n,
    amount1Desired: 10n ** 18n,
    amount0Min: 0n,
    amount1Min: 0n,
    recipient: RECIPIENT,
    deadline: BigInt(Math.floor(Date.now() / 1000) + 3600),
  };

  it('encode/decode roundtrip', () => {
    const calldata = encodeMint(baseMintParams);
    const decoded = decodeFunctionData({ abi: POSITION_MANAGER_ABI, data: calldata });

    expect(decoded.functionName).toBe('mint');
    const args = decoded.args[0] as Record<string, unknown>;
    expect(args.token0).toBe(TOKEN_A);
    expect(args.token1).toBe(TOKEN_B);
    expect(args.fee).toBe(3000);
    expect(args.tickLower).toBe(-887220);
    expect(args.tickUpper).toBe(887220);
    expect(args.amount0Desired).toBe(10n ** 18n);
    expect(args.amount1Desired).toBe(10n ** 18n);
    expect(args.amount0Min).toBe(0n);
    expect(args.amount1Min).toBe(0n);
    expect(args.recipient).toBe(RECIPIENT);
  });

  it('preserves various fee tiers', () => {
    const feeTiers = [500, 3000, 10000];
    for (const fee of feeTiers) {
      const calldata = encodeMint({ ...baseMintParams, fee });
      const decoded = decodeFunctionData({ abi: POSITION_MANAGER_ABI, data: calldata });
      const args = decoded.args[0] as Record<string, unknown>;
      expect(args.fee).toBe(fee);
    }
  });

  it('preserves negative tick values', () => {
    const calldata = encodeMint({ ...baseMintParams, tickLower: -100, tickUpper: 100 });
    const decoded = decodeFunctionData({ abi: POSITION_MANAGER_ABI, data: calldata });
    const args = decoded.args[0] as Record<string, unknown>;
    expect(args.tickLower).toBe(-100);
    expect(args.tickUpper).toBe(100);
  });

  it('calldata is ABI-decodable', () => {
    const calldata = encodeMint(baseMintParams);
    expect(() =>
      decodeFunctionData({ abi: POSITION_MANAGER_ABI, data: calldata }),
    ).not.toThrow();
  });
});

// ── Increase Liquidity ──────────────────────────────────────

describe('Uniswap V3 increaseLiquidity', () => {
  const baseParams: IncreaseLiquidityParams = {
    tokenId: 12345n,
    amount0Desired: 5n * 10n ** 17n,
    amount1Desired: 5n * 10n ** 17n,
    amount0Min: 0n,
    amount1Min: 0n,
    deadline: BigInt(Math.floor(Date.now() / 1000) + 3600),
  };

  it('encode/decode roundtrip', () => {
    const calldata = encodeIncreaseLiquidity(baseParams);
    const decoded = decodeFunctionData({ abi: POSITION_MANAGER_ABI, data: calldata });

    expect(decoded.functionName).toBe('increaseLiquidity');
    const args = decoded.args[0] as Record<string, unknown>;
    expect(args.tokenId).toBe(12345n);
    expect(args.amount0Desired).toBe(5n * 10n ** 17n);
    expect(args.amount1Desired).toBe(5n * 10n ** 17n);
  });

  it('calldata is ABI-decodable', () => {
    const calldata = encodeIncreaseLiquidity(baseParams);
    expect(() =>
      decodeFunctionData({ abi: POSITION_MANAGER_ABI, data: calldata }),
    ).not.toThrow();
  });
});

// ── Decrease Liquidity ──────────────────────────────────────

describe('Uniswap V3 decreaseLiquidity', () => {
  const baseParams: DecreaseLiquidityParams = {
    tokenId: 12345n,
    liquidity: 10n ** 18n,
    amount0Min: 0n,
    amount1Min: 0n,
    deadline: BigInt(Math.floor(Date.now() / 1000) + 3600),
  };

  it('encode/decode roundtrip', () => {
    const calldata = encodeDecreaseLiquidity(baseParams);
    const decoded = decodeFunctionData({ abi: POSITION_MANAGER_ABI, data: calldata });

    expect(decoded.functionName).toBe('decreaseLiquidity');
    const args = decoded.args[0] as Record<string, unknown>;
    expect(args.tokenId).toBe(12345n);
    expect(args.liquidity).toBe(10n ** 18n);
    expect(args.amount0Min).toBe(0n);
    expect(args.amount1Min).toBe(0n);
  });

  it('calldata is ABI-decodable', () => {
    const calldata = encodeDecreaseLiquidity(baseParams);
    expect(() =>
      decodeFunctionData({ abi: POSITION_MANAGER_ABI, data: calldata }),
    ).not.toThrow();
  });
});

// ── Collect ──────────────────────────────────────

describe('Uniswap V3 collect', () => {
  const baseParams: CollectParams = {
    tokenId: 12345n,
    recipient: RECIPIENT,
    amount0Max: MAX_UINT128,
    amount1Max: MAX_UINT128,
  };

  it('encode/decode roundtrip', () => {
    const calldata = encodeCollect(baseParams);
    const decoded = decodeFunctionData({ abi: POSITION_MANAGER_ABI, data: calldata });

    expect(decoded.functionName).toBe('collect');
    const args = decoded.args[0] as Record<string, unknown>;
    expect(args.tokenId).toBe(12345n);
    expect(args.recipient).toBe(RECIPIENT);
    expect(args.amount0Max).toBe(MAX_UINT128);
    expect(args.amount1Max).toBe(MAX_UINT128);
  });

  it('calldata is ABI-decodable', () => {
    const calldata = encodeCollect(baseParams);
    expect(() =>
      decodeFunctionData({ abi: POSITION_MANAGER_ABI, data: calldata }),
    ).not.toThrow();
  });
});

// ── Burn ──────────────────────────────────────

describe('Uniswap V3 burn', () => {
  it('encode/decode roundtrip', () => {
    const calldata = encodeBurn(12345n);
    const decoded = decodeFunctionData({ abi: POSITION_MANAGER_ABI, data: calldata });

    expect(decoded.functionName).toBe('burn');
    expect(decoded.args[0]).toBe(12345n);
  });

  it('preserves various tokenId values', () => {
    const ids = [0n, 1n, 999999n, (2n ** 64n) - 1n];
    for (const id of ids) {
      const calldata = encodeBurn(id);
      const decoded = decodeFunctionData({ abi: POSITION_MANAGER_ABI, data: calldata });
      expect(decoded.args[0]).toBe(id);
    }
  });

  it('calldata is ABI-decodable', () => {
    const calldata = encodeBurn(1n);
    expect(() =>
      decodeFunctionData({ abi: POSITION_MANAGER_ABI, data: calldata }),
    ).not.toThrow();
  });
});

// ── Selector uniqueness ──────────────────────────────────────

describe('Uniswap V3 selector uniqueness', () => {
  it('all functions have distinct 4-byte selectors', () => {
    const mintParams: MintParams = {
      token0: TOKEN_A, token1: TOKEN_B, fee: 3000,
      tickLower: -100, tickUpper: 100,
      amount0Desired: 1n, amount1Desired: 1n,
      amount0Min: 0n, amount1Min: 0n,
      recipient: RECIPIENT, deadline: 9999999999n,
    };

    const selectors = [
      encodeMint(mintParams).slice(0, 10),
      encodeIncreaseLiquidity({
        tokenId: 1n, amount0Desired: 1n, amount1Desired: 1n,
        amount0Min: 0n, amount1Min: 0n, deadline: 9999999999n,
      }).slice(0, 10),
      encodeDecreaseLiquidity({
        tokenId: 1n, liquidity: 1n,
        amount0Min: 0n, amount1Min: 0n, deadline: 9999999999n,
      }).slice(0, 10),
      encodeCollect({
        tokenId: 1n, recipient: RECIPIENT,
        amount0Max: MAX_UINT128, amount1Max: MAX_UINT128,
      }).slice(0, 10),
      encodeBurn(1n).slice(0, 10),
    ];

    const unique = new Set(selectors);
    expect(unique.size).toBe(selectors.length);
  });

  it('ABIs are importable and non-empty', () => {
    expect(POSITION_MANAGER_ABI.length).toBeGreaterThan(0);
    expect(FACTORY_ABI.length).toBeGreaterThan(0);
  });
});
