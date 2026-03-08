/**
 * Calldata encode/decode validation for Aave V3 adapter.
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
  ROUTER_ABI,
  POOL_FACTORY,
  encodeAddLiquidity,
  encodeRemoveLiquidity,
  encodeSwap,
  type AddLiquidityParams,
  type RemoveLiquidityParams,
  type SwapRoute,
} from '../src/execution/aerodrome-adapter.js';

const DUMMY_ASSET: Address = '0x94a9D9AC8a22534E3FaCa9F4e7F2E2cf85d5E4C8';
const DUMMY_WALLET: Address = '0x1111111111111111111111111111111111111111';
const DUMMY_TOKEN_A: Address = '0xaAaAaAaaAaAaAaaAaAAAAAAAAaaaAaAaAaaAaaAa';
const DUMMY_TOKEN_B: Address = '0xbBbBBBBbbBBBbbbBbbBbbbbBBbBbbbbBbBbbBBbB';
const DUMMY_FACTORY: Address = '0x420DD381b31aEf6683db6B902084cB0FFECe40Da';

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

// ── Aerodrome ──────────────────────────────────────

describe('Aerodrome calldata validation', () => {
  it('addLiquidity encode/decode roundtrip', () => {
    const params: AddLiquidityParams = {
      tokenA: DUMMY_TOKEN_A,
      tokenB: DUMMY_TOKEN_B,
      stable: true,
      amountADesired: 1_000_000n,
      amountBDesired: 2_000_000n,
      amountAMin: 950_000n,
      amountBMin: 1_900_000n,
      to: DUMMY_WALLET,
      deadline: 1_700_000_000n,
    };
    const calldata = encodeAddLiquidity(params);
    const decoded = decodeFunctionData({ abi: ROUTER_ABI, data: calldata });

    expect(decoded.functionName).toBe('addLiquidity');
    expect(decoded.args[0]).toBe(DUMMY_TOKEN_A);       // tokenA
    expect(decoded.args[1]).toBe(DUMMY_TOKEN_B);       // tokenB
    expect(decoded.args[2]).toBe(true);                 // stable
    expect(decoded.args[3]).toBe(1_000_000n);           // amountADesired
    expect(decoded.args[4]).toBe(2_000_000n);           // amountBDesired
    expect(decoded.args[5]).toBe(950_000n);             // amountAMin
    expect(decoded.args[6]).toBe(1_900_000n);           // amountBMin
    expect(decoded.args[7]).toBe(DUMMY_WALLET);         // to
    expect(decoded.args[8]).toBe(1_700_000_000n);       // deadline
  });

  it('removeLiquidity encode/decode roundtrip', () => {
    const params: RemoveLiquidityParams = {
      tokenA: DUMMY_TOKEN_A,
      tokenB: DUMMY_TOKEN_B,
      stable: false,
      liquidity: 500_000n,
      amountAMin: 400_000n,
      amountBMin: 450_000n,
      to: DUMMY_WALLET,
      deadline: 1_700_000_000n,
    };
    const calldata = encodeRemoveLiquidity(params);
    const decoded = decodeFunctionData({ abi: ROUTER_ABI, data: calldata });

    expect(decoded.functionName).toBe('removeLiquidity');
    expect(decoded.args[0]).toBe(DUMMY_TOKEN_A);       // tokenA
    expect(decoded.args[1]).toBe(DUMMY_TOKEN_B);       // tokenB
    expect(decoded.args[2]).toBe(false);                // stable
    expect(decoded.args[3]).toBe(500_000n);             // liquidity
    expect(decoded.args[4]).toBe(400_000n);             // amountAMin
    expect(decoded.args[5]).toBe(450_000n);             // amountBMin
    expect(decoded.args[6]).toBe(DUMMY_WALLET);         // to
    expect(decoded.args[7]).toBe(1_700_000_000n);       // deadline
  });

  it('swap encode/decode roundtrip', () => {
    const routes: SwapRoute[] = [
      { from: DUMMY_TOKEN_A, to: DUMMY_TOKEN_B, stable: true, factory: DUMMY_FACTORY },
    ];
    const calldata = encodeSwap(1_000_000n, 900_000n, routes, DUMMY_WALLET, 1_700_000_000n);
    const decoded = decodeFunctionData({ abi: ROUTER_ABI, data: calldata });

    expect(decoded.functionName).toBe('swapExactTokensForTokens');
    expect(decoded.args[0]).toBe(1_000_000n);           // amountIn
    expect(decoded.args[1]).toBe(900_000n);             // amountOutMin
    const decodedRoutes = decoded.args[2] as readonly { from: Address; to: Address; stable: boolean; factory: Address }[];
    expect(decodedRoutes).toHaveLength(1);
    expect(decodedRoutes[0].from).toBe(DUMMY_TOKEN_A);
    expect(decodedRoutes[0].to).toBe(DUMMY_TOKEN_B);
    expect(decodedRoutes[0].stable).toBe(true);
    expect(decodedRoutes[0].factory).toBe(DUMMY_FACTORY);
    expect(decoded.args[3]).toBe(DUMMY_WALLET);         // to
    expect(decoded.args[4]).toBe(1_700_000_000n);       // deadline
  });

  it('swap with multi-hop route roundtrip', () => {
    const intermediate: Address = '0xCcCCccccCCCCcCCCCCCcCcCccCcCCCcCcccccccC';
    const routes: SwapRoute[] = [
      { from: DUMMY_TOKEN_A, to: intermediate, stable: false, factory: DUMMY_FACTORY },
      { from: intermediate, to: DUMMY_TOKEN_B, stable: true, factory: DUMMY_FACTORY },
    ];
    const calldata = encodeSwap(5_000n, 4_500n, routes, DUMMY_WALLET, 1_700_000_000n);
    const decoded = decodeFunctionData({ abi: ROUTER_ABI, data: calldata });

    const decodedRoutes = decoded.args[2] as readonly { from: Address; to: Address; stable: boolean; factory: Address }[];
    expect(decodedRoutes).toHaveLength(2);
    expect(decodedRoutes[0].from).toBe(DUMMY_TOKEN_A);
    expect(decodedRoutes[0].to).toBe(intermediate);
    expect(decodedRoutes[1].from).toBe(intermediate);
    expect(decodedRoutes[1].to).toBe(DUMMY_TOKEN_B);
  });

  it('addLiquidity vs removeLiquidity vs swap have different selectors', () => {
    const addData = encodeAddLiquidity({
      tokenA: DUMMY_TOKEN_A, tokenB: DUMMY_TOKEN_B, stable: true,
      amountADesired: 1n, amountBDesired: 1n, amountAMin: 0n, amountBMin: 0n,
      to: DUMMY_WALLET, deadline: 1n,
    });
    const removeData = encodeRemoveLiquidity({
      tokenA: DUMMY_TOKEN_A, tokenB: DUMMY_TOKEN_B, stable: true,
      liquidity: 1n, amountAMin: 0n, amountBMin: 0n,
      to: DUMMY_WALLET, deadline: 1n,
    });
    const swapData = encodeSwap(1n, 0n, [
      { from: DUMMY_TOKEN_A, to: DUMMY_TOKEN_B, stable: true, factory: DUMMY_FACTORY },
    ], DUMMY_WALLET, 1n);

    const selectors = new Set([
      addData.slice(0, 10),
      removeData.slice(0, 10),
      swapData.slice(0, 10),
    ]);
    expect(selectors.size).toBe(3);
  });
});

// ── Cross-Adapter Selector Uniqueness ──────────────────────────────────────

describe('Cross-adapter selector uniqueness', () => {
  it('Aave and Aerodrome function selectors do not collide', () => {
    // Collect all Aave selectors
    const aaveSupply = encodeSupply(DUMMY_ASSET, 1n, DUMMY_WALLET).slice(0, 10);
    const aaveWithdraw = encodeWithdraw(DUMMY_ASSET, 1n, DUMMY_WALLET).slice(0, 10);
    const aaveSelectors = [aaveSupply, aaveWithdraw];

    // Collect all Aerodrome Router selectors
    const aeroAdd = encodeAddLiquidity({
      tokenA: DUMMY_TOKEN_A, tokenB: DUMMY_TOKEN_B, stable: true,
      amountADesired: 1n, amountBDesired: 1n, amountAMin: 0n, amountBMin: 0n,
      to: DUMMY_WALLET, deadline: 1n,
    }).slice(0, 10);
    const aeroRemove = encodeRemoveLiquidity({
      tokenA: DUMMY_TOKEN_A, tokenB: DUMMY_TOKEN_B, stable: true,
      liquidity: 1n, amountAMin: 0n, amountBMin: 0n,
      to: DUMMY_WALLET, deadline: 1n,
    }).slice(0, 10);
    const aeroSwap = encodeSwap(1n, 0n, [
      { from: DUMMY_TOKEN_A, to: DUMMY_TOKEN_B, stable: true, factory: DUMMY_FACTORY },
    ], DUMMY_WALLET, 1n).slice(0, 10);
    const aeroSelectors = [aeroAdd, aeroRemove, aeroSwap];

    // Verify no overlap between adapters
    for (const aaveSel of aaveSelectors) {
      for (const aeroSel of aeroSelectors) {
        expect(aaveSel).not.toBe(aeroSel);
      }
    }

    // Also verify all selectors are globally unique
    const allSelectors = [...aaveSelectors, ...aeroSelectors];
    expect(new Set(allSelectors).size).toBe(allSelectors.length);
  });
});
