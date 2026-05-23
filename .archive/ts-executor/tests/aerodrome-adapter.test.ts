/**
 * Calldata encode/decode validation for Aerodrome adapter (Base).
 */

import { describe, expect, it } from 'vitest';
import { type Address, decodeFunctionData } from 'viem';

import {
  GAUGE_ABI,
  POOL_ABI,
  POOL_FACTORY,
  ROUTER_ABI,
  ROUTER_ADDRESS,
  encodeAddLiquidity,
  encodeApprove,
  encodeEarnedQuery,
  encodeGaugeDeposit,
  encodeGaugeWithdraw,
  encodeGetAmountsOut,
  encodeGetReservesQuery,
  encodeGetReward,
  encodePoolForQuery,
  encodeRemoveLiquidity,
  encodeRewardRateQuery,
  encodeSwap,
  encodeTotalSupplyQuery,
  estimateBaseGas,
  type AddLiquidityParams,
  type RemoveLiquidityParams,
  type SwapRoute,
} from '../src/execution/aerodrome-adapter.js';

const TOKEN_A: Address = '0x4200000000000000000000000000000000000006'; // WETH on Base
const TOKEN_B: Address = '0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913'; // USDC on Base
const RECIPIENT: Address = '0x1111111111111111111111111111111111111111';
const MOCK_GAUGE: Address = '0x2222222222222222222222222222222222222222';

// ── Addresses ──────────────────────────────────────

describe('Aerodrome contract addresses', () => {
  it('exports Router address', () => {
    expect(ROUTER_ADDRESS).toMatch(/^0x[0-9a-fA-F]{40}$/);
  });

  it('exports Pool Factory address', () => {
    expect(POOL_FACTORY).toMatch(/^0x[0-9a-fA-F]{40}$/);
  });
});

// ── Add Liquidity ──────────────────────────────────────

describe('encodeAddLiquidity', () => {
  const baseParams: AddLiquidityParams = {
    tokenA: TOKEN_A,
    tokenB: TOKEN_B,
    stable: false,
    amountADesired: 10n ** 18n,
    amountBDesired: 2500n * 10n ** 6n,
    amountAMin: 9n * 10n ** 17n,
    amountBMin: 2400n * 10n ** 6n,
    to: RECIPIENT,
    deadline: BigInt(Math.floor(Date.now() / 1000) + 3600),
  };

  it('encode/decode roundtrip for volatile pool', () => {
    const calldata = encodeAddLiquidity(baseParams);
    const decoded = decodeFunctionData({ abi: ROUTER_ABI, data: calldata });

    expect(decoded.functionName).toBe('addLiquidity');
    expect(decoded.args[0]).toBe(TOKEN_A);
    expect(decoded.args[1]).toBe(TOKEN_B);
    expect(decoded.args[2]).toBe(false); // stable
    expect(decoded.args[3]).toBe(10n ** 18n);
    expect(decoded.args[4]).toBe(2500n * 10n ** 6n);
  });

  it('encode/decode roundtrip for stable pool', () => {
    const calldata = encodeAddLiquidity({ ...baseParams, stable: true });
    const decoded = decodeFunctionData({ abi: ROUTER_ABI, data: calldata });

    expect(decoded.args[2]).toBe(true); // stable
  });

  it('stable and volatile produce different calldata', () => {
    const volatile = encodeAddLiquidity(baseParams);
    const stable = encodeAddLiquidity({ ...baseParams, stable: true });
    expect(volatile).not.toBe(stable);
  });

  it('calldata is ABI-decodable', () => {
    const calldata = encodeAddLiquidity(baseParams);
    expect(() =>
      decodeFunctionData({ abi: ROUTER_ABI, data: calldata }),
    ).not.toThrow();
  });
});

// ── Remove Liquidity ──────────────────────────────────────

describe('encodeRemoveLiquidity', () => {
  const baseParams: RemoveLiquidityParams = {
    tokenA: TOKEN_A,
    tokenB: TOKEN_B,
    stable: false,
    liquidity: 10n ** 18n,
    amountAMin: 0n,
    amountBMin: 0n,
    to: RECIPIENT,
    deadline: BigInt(Math.floor(Date.now() / 1000) + 3600),
  };

  it('encode/decode roundtrip', () => {
    const calldata = encodeRemoveLiquidity(baseParams);
    const decoded = decodeFunctionData({ abi: ROUTER_ABI, data: calldata });

    expect(decoded.functionName).toBe('removeLiquidity');
    expect(decoded.args[0]).toBe(TOKEN_A);
    expect(decoded.args[1]).toBe(TOKEN_B);
    expect(decoded.args[3]).toBe(10n ** 18n); // liquidity
  });

  it('calldata is ABI-decodable', () => {
    expect(() =>
      decodeFunctionData({ abi: ROUTER_ABI, data: encodeRemoveLiquidity(baseParams) }),
    ).not.toThrow();
  });
});

// ── Swap ──────────────────────────────────────

describe('encodeSwap', () => {
  const route: SwapRoute = {
    from: TOKEN_A,
    to: TOKEN_B,
    stable: false,
    factory: POOL_FACTORY,
  };

  it('encode/decode roundtrip for single-hop swap', () => {
    const calldata = encodeSwap(
      10n ** 18n, 2400n * 10n ** 6n, [route], RECIPIENT,
      BigInt(Math.floor(Date.now() / 1000) + 1800),
    );
    const decoded = decodeFunctionData({ abi: ROUTER_ABI, data: calldata });

    expect(decoded.functionName).toBe('swapExactTokensForTokens');
    expect(decoded.args[0]).toBe(10n ** 18n); // amountIn
    expect(decoded.args[1]).toBe(2400n * 10n ** 6n); // amountOutMin
  });

  it('supports multi-hop routes', () => {
    const intermediateToken: Address = '0x3333333333333333333333333333333333333333';
    const multiRoute: SwapRoute[] = [
      { from: TOKEN_A, to: intermediateToken, stable: false, factory: POOL_FACTORY },
      { from: intermediateToken, to: TOKEN_B, stable: true, factory: POOL_FACTORY },
    ];

    const calldata = encodeSwap(
      10n ** 18n, 2300n * 10n ** 6n, multiRoute, RECIPIENT,
      BigInt(Math.floor(Date.now() / 1000) + 1800),
    );

    expect(() =>
      decodeFunctionData({ abi: ROUTER_ABI, data: calldata }),
    ).not.toThrow();
  });
});

// ── Gauge Operations ──────────────────────────────────────

describe('Gauge operations', () => {
  it('encodeGaugeDeposit produces valid calldata', () => {
    const calldata = encodeGaugeDeposit(10n ** 18n);
    const decoded = decodeFunctionData({ abi: GAUGE_ABI, data: calldata });

    expect(decoded.functionName).toBe('deposit');
    expect(decoded.args[0]).toBe(10n ** 18n);
  });

  it('encodeGaugeWithdraw produces valid calldata', () => {
    const calldata = encodeGaugeWithdraw(5n * 10n ** 17n);
    const decoded = decodeFunctionData({ abi: GAUGE_ABI, data: calldata });

    expect(decoded.functionName).toBe('withdraw');
    expect(decoded.args[0]).toBe(5n * 10n ** 17n);
  });

  it('encodeGetReward produces valid calldata', () => {
    const calldata = encodeGetReward(RECIPIENT);
    const decoded = decodeFunctionData({ abi: GAUGE_ABI, data: calldata });

    expect(decoded.functionName).toBe('getReward');
    expect(decoded.args[0]).toBe(RECIPIENT);
  });

  it('deposit and withdraw have different selectors', () => {
    const deposit = encodeGaugeDeposit(1n);
    const withdraw = encodeGaugeWithdraw(1n);
    expect(deposit.slice(0, 10)).not.toBe(withdraw.slice(0, 10));
  });
});

// ── Approve ──────────────────────────────────────

describe('encodeApprove', () => {
  it('encode/decode roundtrip', () => {
    const calldata = encodeApprove(ROUTER_ADDRESS, 10n ** 18n);
    expect(calldata).toMatch(/^0x[0-9a-f]+$/i);
    // approve selector: 0x095ea7b3
    expect(calldata.slice(0, 10)).toBe('0x095ea7b3');
  });
});

// ── Query Encoders ──────────────────────────────────────

describe('Aerodrome query encoders', () => {
  it('encodeGetReservesQuery produces valid calldata', () => {
    const calldata = encodeGetReservesQuery();
    expect(calldata).toMatch(/^0x[0-9a-f]+$/i);
    // getReserves() selector: 0x0902f1ac
    expect(calldata.slice(0, 10)).toBe('0x0902f1ac');
  });

  it('encodeEarnedQuery produces valid calldata', () => {
    const calldata = encodeEarnedQuery(RECIPIENT);
    const decoded = decodeFunctionData({ abi: GAUGE_ABI, data: calldata });
    expect(decoded.functionName).toBe('earned');
    expect(decoded.args[0]).toBe(RECIPIENT);
  });

  it('encodeRewardRateQuery produces valid calldata', () => {
    const calldata = encodeRewardRateQuery();
    expect(calldata).toMatch(/^0x[0-9a-f]+$/i);
  });

  it('encodeTotalSupplyQuery produces valid calldata', () => {
    const calldata = encodeTotalSupplyQuery();
    expect(calldata).toMatch(/^0x[0-9a-f]+$/i);
  });

  it('encodeGetAmountsOut produces valid calldata', () => {
    const route: SwapRoute = { from: TOKEN_A, to: TOKEN_B, stable: false, factory: POOL_FACTORY };
    const calldata = encodeGetAmountsOut(10n ** 18n, [route]);
    const decoded = decodeFunctionData({ abi: ROUTER_ABI, data: calldata });
    expect(decoded.functionName).toBe('getAmountsOut');
    expect(decoded.args[0]).toBe(10n ** 18n);
  });

  it('encodePoolForQuery encodes factory lookup', () => {
    const calldata = encodePoolForQuery(TOKEN_A, TOKEN_B, false);
    const decoded = decodeFunctionData({ abi: ROUTER_ABI, data: calldata });
    expect(decoded.functionName).toBe('poolFor');
    expect(decoded.args[0]).toBe(TOKEN_A);
    expect(decoded.args[1]).toBe(TOKEN_B);
    expect(decoded.args[2]).toBe(false);
    expect(decoded.args[3]).toBe(POOL_FACTORY);
  });

  it('all query selectors are unique', () => {
    const selectors = [
      encodeGetReservesQuery().slice(0, 10),
      encodeEarnedQuery(RECIPIENT).slice(0, 10),
      encodeRewardRateQuery().slice(0, 10),
      encodeTotalSupplyQuery().slice(0, 10),
    ];
    const unique = new Set(selectors);
    expect(unique.size).toBe(selectors.length);
  });
});

// ── Selector Uniqueness ──────────────────────────────────────

describe('Aerodrome selector uniqueness', () => {
  it('all Router function selectors are distinct', () => {
    const route: SwapRoute = { from: TOKEN_A, to: TOKEN_B, stable: false, factory: POOL_FACTORY };
    const selectors = [
      encodeAddLiquidity({
        tokenA: TOKEN_A, tokenB: TOKEN_B, stable: false,
        amountADesired: 1n, amountBDesired: 1n, amountAMin: 0n, amountBMin: 0n,
        to: RECIPIENT, deadline: 999999n,
      }).slice(0, 10),
      encodeRemoveLiquidity({
        tokenA: TOKEN_A, tokenB: TOKEN_B, stable: false,
        liquidity: 1n, amountAMin: 0n, amountBMin: 0n,
        to: RECIPIENT, deadline: 999999n,
      }).slice(0, 10),
      encodeSwap(1n, 0n, [route], RECIPIENT, 999999n).slice(0, 10),
      encodeGetAmountsOut(1n, [route]).slice(0, 10),
      encodePoolForQuery(TOKEN_A, TOKEN_B, false).slice(0, 10),
    ];

    const unique = new Set(selectors);
    expect(unique.size).toBe(selectors.length);
  });

  it('ABIs are importable and non-empty', () => {
    expect(ROUTER_ABI.length).toBeGreaterThan(0);
    expect(POOL_ABI.length).toBeGreaterThan(0);
    expect(GAUGE_ABI.length).toBeGreaterThan(0);
  });
});

// ── L2 Gas Estimation ──────────────────────────────────────

describe('estimateBaseGas', () => {
  it('calculates L2 + L1 data cost with fixed overhead', () => {
    const result = estimateBaseGas({
      l2GasEstimate: 200_000n,
      l2GasPriceWei: 50_000_000n,        // 0.05 gwei
      calldataBytes: 300n,
      l1BaseFeeWei: 20_000_000_000n,      // 20 gwei
    });

    // L2 cost: 200k * 0.05 gwei = 10,000 gwei
    expect(result.l2CostWei).toBe(10_000_000_000_000n);
    // L1 data cost: (300 * 16 + 2100) * 20 gwei = 6900 * 20 gwei = 138,000 gwei
    expect(result.l1DataCostWei).toBe(138_000_000_000_000n);
    expect(result.totalCostWei).toBe(result.l2CostWei + result.l1DataCostWei);
  });

  it('includes fixed overhead even with zero calldata', () => {
    const result = estimateBaseGas({
      l2GasEstimate: 100_000n,
      l2GasPriceWei: 50_000_000n,
      calldataBytes: 0n,
      l1BaseFeeWei: 20_000_000_000n,
    });

    // Fixed overhead: 2100 * 20 gwei = 42,000 gwei
    expect(result.l1DataCostWei).toBe(42_000_000_000_000n);
    expect(result.l1DataCostWei).toBeGreaterThan(0n);
  });

  it('L1 data cost dominates for large calldata on Base', () => {
    const result = estimateBaseGas({
      l2GasEstimate: 100_000n,
      l2GasPriceWei: 50_000_000n,         // cheap L2
      calldataBytes: 5_000n,              // 5KB calldata
      l1BaseFeeWei: 40_000_000_000n,      // 40 gwei L1
    });

    expect(result.l1DataCostWei).toBeGreaterThan(result.l2CostWei);
  });
});
