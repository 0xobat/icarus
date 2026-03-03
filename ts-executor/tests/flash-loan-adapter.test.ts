/**
 * Flash loan encode module tests.
 *
 * Encode/decode roundtrip validation for Aave V3 flash loan adapter.
 */

import { describe, expect, it } from 'vitest';
import { type Address, type Hex, decodeFunctionData } from 'viem';

import {
  AAVE_V3_POOL,
  FLASH_LOAN_FEE_BPS,
  FLASH_LOAN_POOL_ABI,
  FLASH_LOAN_RECEIVER_ABI,
  calculateFlashLoanFee,
  encodeFlashLoan,
  encodeFlashLoanCallback,
  encodeFlashLoanSimple,
  estimateFlashLoanGas,
} from '../src/execution/flash-loan-adapter.js';

const MOCK_RECEIVER: Address = '0x1111111111111111111111111111111111111111';
const MOCK_ASSET_A: Address = '0x2222222222222222222222222222222222222222';
const MOCK_ASSET_B: Address = '0x3333333333333333333333333333333333333333';
const MOCK_INITIATOR: Address = '0x4444444444444444444444444444444444444444';
const EMPTY_PARAMS: Hex = '0x';

// ── Constants ──────────────────────────────────────

describe('Flash loan constants', () => {
  it('exports pool address matching Sepolia', () => {
    expect(AAVE_V3_POOL.toLowerCase()).toBe(
      '0x6Ae43d3271ff6888e7Fc43Fd7321a503ff738951'.toLowerCase(),
    );
  });

  it('exports flash loan fee as 5 basis points', () => {
    expect(FLASH_LOAN_FEE_BPS).toBe(5n);
  });

  it('exports ABIs', () => {
    expect(FLASH_LOAN_POOL_ABI).toBeDefined();
    expect(FLASH_LOAN_RECEIVER_ABI).toBeDefined();
  });
});

// ── encodeFlashLoan ──────────────────────────────────────

describe('encodeFlashLoan', () => {
  it('encode/decode roundtrip — single asset', () => {
    const calldata = encodeFlashLoan({
      receiverAddress: MOCK_RECEIVER,
      assets: [MOCK_ASSET_A],
      amounts: [10n ** 18n],
      interestRateModes: [0n],
      onBehalfOf: MOCK_RECEIVER,
      params: EMPTY_PARAMS,
    });

    const decoded = decodeFunctionData({ abi: FLASH_LOAN_POOL_ABI, data: calldata });
    expect(decoded.functionName).toBe('flashLoan');
    expect(decoded.args[0]).toBe(MOCK_RECEIVER);     // receiverAddress
    expect(decoded.args[1]).toEqual([MOCK_ASSET_A]);  // assets
    expect(decoded.args[2]).toEqual([10n ** 18n]);     // amounts
    expect(decoded.args[3]).toEqual([0n]);             // interestRateModes
    expect(decoded.args[4]).toBe(MOCK_RECEIVER);       // onBehalfOf
    expect(decoded.args[5]).toBe(EMPTY_PARAMS);        // params
    expect(decoded.args[6]).toBe(0);                   // referralCode
  });

  it('encode/decode roundtrip — multi asset', () => {
    const calldata = encodeFlashLoan({
      receiverAddress: MOCK_RECEIVER,
      assets: [MOCK_ASSET_A, MOCK_ASSET_B],
      amounts: [10n ** 18n, 5n * 10n ** 6n],
      interestRateModes: [0n, 0n],
      onBehalfOf: MOCK_RECEIVER,
      params: '0xdeadbeef',
    });

    const decoded = decodeFunctionData({ abi: FLASH_LOAN_POOL_ABI, data: calldata });
    expect(decoded.functionName).toBe('flashLoan');
    expect(decoded.args[1]).toEqual([MOCK_ASSET_A, MOCK_ASSET_B]);
    expect(decoded.args[2]).toEqual([10n ** 18n, 5n * 10n ** 6n]);
    expect(decoded.args[5]).toBe('0xdeadbeef');
  });

  it('custom referral code is preserved', () => {
    const calldata = encodeFlashLoan({
      receiverAddress: MOCK_RECEIVER,
      assets: [MOCK_ASSET_A],
      amounts: [1n],
      interestRateModes: [0n],
      onBehalfOf: MOCK_RECEIVER,
      params: EMPTY_PARAMS,
      referralCode: 42,
    });

    const decoded = decodeFunctionData({ abi: FLASH_LOAN_POOL_ABI, data: calldata });
    expect(decoded.args[6]).toBe(42);
  });

  it('produces valid hex calldata', () => {
    const calldata = encodeFlashLoan({
      receiverAddress: MOCK_RECEIVER,
      assets: [MOCK_ASSET_A],
      amounts: [1n],
      interestRateModes: [0n],
      onBehalfOf: MOCK_RECEIVER,
      params: EMPTY_PARAMS,
    });

    expect(calldata).toMatch(/^0x[0-9a-f]+$/i);
  });
});

// ── encodeFlashLoanSimple ──────────────────────────────────────

describe('encodeFlashLoanSimple', () => {
  it('encode/decode roundtrip', () => {
    const calldata = encodeFlashLoanSimple({
      receiverAddress: MOCK_RECEIVER,
      asset: MOCK_ASSET_A,
      amount: 10n ** 18n,
      params: EMPTY_PARAMS,
    });

    const decoded = decodeFunctionData({ abi: FLASH_LOAN_POOL_ABI, data: calldata });
    expect(decoded.functionName).toBe('flashLoanSimple');
    expect(decoded.args[0]).toBe(MOCK_RECEIVER);   // receiverAddress
    expect(decoded.args[1]).toBe(MOCK_ASSET_A);     // asset
    expect(decoded.args[2]).toBe(10n ** 18n);        // amount
    expect(decoded.args[3]).toBe(EMPTY_PARAMS);      // params
    expect(decoded.args[4]).toBe(0);                 // referralCode
  });

  it('preserves callback params data', () => {
    const swapData: Hex = '0xabcdef0123456789';
    const calldata = encodeFlashLoanSimple({
      receiverAddress: MOCK_RECEIVER,
      asset: MOCK_ASSET_A,
      amount: 1_000_000n,
      params: swapData,
    });

    const decoded = decodeFunctionData({ abi: FLASH_LOAN_POOL_ABI, data: calldata });
    expect(decoded.args[3]).toBe(swapData);
  });

  it('custom referral code is preserved', () => {
    const calldata = encodeFlashLoanSimple({
      receiverAddress: MOCK_RECEIVER,
      asset: MOCK_ASSET_A,
      amount: 1n,
      params: EMPTY_PARAMS,
      referralCode: 100,
    });

    const decoded = decodeFunctionData({ abi: FLASH_LOAN_POOL_ABI, data: calldata });
    expect(decoded.args[4]).toBe(100);
  });
});

// ── encodeFlashLoanCallback ──────────────────────────────────────

describe('encodeFlashLoanCallback', () => {
  it('encode/decode roundtrip', () => {
    const calldata = encodeFlashLoanCallback({
      assets: [MOCK_ASSET_A],
      amounts: [10n ** 18n],
      premiums: [5n * 10n ** 14n],
      initiator: MOCK_INITIATOR,
      params: EMPTY_PARAMS,
    });

    const decoded = decodeFunctionData({ abi: FLASH_LOAN_RECEIVER_ABI, data: calldata });
    expect(decoded.functionName).toBe('executeOperation');
    expect(decoded.args[0]).toEqual([MOCK_ASSET_A]);         // assets
    expect(decoded.args[1]).toEqual([10n ** 18n]);             // amounts
    expect(decoded.args[2]).toEqual([5n * 10n ** 14n]);        // premiums
    expect(decoded.args[3]).toBe(MOCK_INITIATOR);              // initiator
    expect(decoded.args[4]).toBe(EMPTY_PARAMS);                // params
  });

  it('multi-asset callback roundtrip', () => {
    const calldata = encodeFlashLoanCallback({
      assets: [MOCK_ASSET_A, MOCK_ASSET_B],
      amounts: [10n ** 18n, 5n * 10n ** 6n],
      premiums: [5n * 10n ** 14n, 250n],
      initiator: MOCK_INITIATOR,
      params: '0xcafe',
    });

    const decoded = decodeFunctionData({ abi: FLASH_LOAN_RECEIVER_ABI, data: calldata });
    expect(decoded.args[0]).toEqual([MOCK_ASSET_A, MOCK_ASSET_B]);
    expect(decoded.args[2]).toEqual([5n * 10n ** 14n, 250n]);
    expect(decoded.args[4]).toBe('0xcafe');
  });
});

// ── Selector uniqueness ──────────────────────────────────────

describe('selector uniqueness', () => {
  it('flashLoan vs flashLoanSimple have different selectors', () => {
    const flashLoanData = encodeFlashLoan({
      receiverAddress: MOCK_RECEIVER,
      assets: [MOCK_ASSET_A],
      amounts: [1n],
      interestRateModes: [0n],
      onBehalfOf: MOCK_RECEIVER,
      params: EMPTY_PARAMS,
    });

    const simpleData = encodeFlashLoanSimple({
      receiverAddress: MOCK_RECEIVER,
      asset: MOCK_ASSET_A,
      amount: 1n,
      params: EMPTY_PARAMS,
    });

    expect(flashLoanData.slice(0, 10)).not.toBe(simpleData.slice(0, 10));
  });
});

// ── Fee calculation ──────────────────────────────────────

describe('calculateFlashLoanFee', () => {
  it('calculates 0.05% fee for 1 ETH', () => {
    const oneEth = 10n ** 18n;
    const fee = calculateFlashLoanFee(oneEth);
    // 0.05% of 1e18 = 5e14
    expect(fee).toBe(5n * 10n ** 14n);
  });

  it('calculates fee for 1M USDC (6 decimals)', () => {
    const amount = 1_000_000n * 10n ** 6n;
    const fee = calculateFlashLoanFee(amount);
    // 0.05% of 1e12 = 5e8 = 500 USDC
    expect(fee).toBe(500n * 10n ** 6n);
  });

  it('returns 0 for 0 amount', () => {
    expect(calculateFlashLoanFee(0n)).toBe(0n);
  });

  it('rounds down for small amounts', () => {
    // 9999 * 5 / 10000 = 4.9995 → 4
    expect(calculateFlashLoanFee(9_999n)).toBe(4n);
  });
});

// ── Gas estimation ──────────────────────────────────────

describe('estimateFlashLoanGas', () => {
  it('adds base gas to callback estimate', () => {
    const callbackGas = 200_000n;
    const total = estimateFlashLoanGas(callbackGas);
    expect(total).toBe(500_000n); // 300k base + 200k callback
  });

  it('returns base gas when callback is 0', () => {
    expect(estimateFlashLoanGas(0n)).toBe(300_000n);
  });
});
