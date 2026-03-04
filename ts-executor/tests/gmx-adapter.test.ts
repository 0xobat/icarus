/**
 * Calldata encode/decode validation for GMX V2 adapter (Arbitrum).
 */

import { describe, expect, it } from 'vitest';
import { type Address, type Hex, decodeFunctionData } from 'viem';

import {
  DATA_STORE,
  DATA_STORE_ABI,
  DECREASE_POSITION_SWAP_TYPES,
  EXCHANGE_ROUTER,
  EXCHANGE_ROUTER_ABI,
  ORDER_TYPES,
  READER_ABI,
  READER_ADDRESS,
  encodeAdjustMargin,
  encodeCancelOrder,
  encodeClosePosition,
  encodeCreateOrder,
  encodeFundingRateQuery,
  encodeGetMarket,
  encodeGetPosition,
  encodeOpenPosition,
  estimateArbitrumGas,
} from '../src/execution/gmx-adapter.js';

const MOCK_MARKET: Address = '0x70d95587d40A2caf56bd97485aB3Eec10Bee6336';
const MOCK_COLLATERAL: Address = '0xaf88d065e77c8cC2239327C5EDb3A432268e5831';
const MOCK_RECEIVER: Address = '0x1111111111111111111111111111111111111111';

// ── Addresses ──────────────────────────────────────

describe('GMX contract addresses', () => {
  it('exports ExchangeRouter address', () => {
    expect(EXCHANGE_ROUTER).toMatch(/^0x[0-9a-fA-F]{40}$/);
  });

  it('exports Reader address', () => {
    expect(READER_ADDRESS).toMatch(/^0x[0-9a-fA-F]{40}$/);
  });

  it('exports DataStore address', () => {
    expect(DATA_STORE).toMatch(/^0x[0-9a-fA-F]{40}$/);
  });
});

// ── Order Type Constants ──────────────────────────────────────

describe('GMX order type constants', () => {
  it('defines all order types as distinct 32-byte hex values', () => {
    const types = Object.values(ORDER_TYPES);
    expect(types.length).toBe(5);
    const unique = new Set(types);
    expect(unique.size).toBe(5);
    for (const t of types) {
      expect(t).toMatch(/^0x[0-9a-f]{64}$/);
    }
  });

  it('defines decrease swap types', () => {
    const types = Object.values(DECREASE_POSITION_SWAP_TYPES);
    expect(types.length).toBe(3);
    const unique = new Set(types);
    expect(unique.size).toBe(3);
  });
});

// ── encodeCreateOrder ──────────────────────────────────────

describe('encodeCreateOrder', () => {
  it('encode/decode roundtrip', () => {
    const calldata = encodeCreateOrder({
      addresses: [MOCK_MARKET, MOCK_COLLATERAL, MOCK_RECEIVER, '0x0000000000000000000000000000000000000000'],
      numbers: [1000n * 10n ** 30n, 100n * 10n ** 6n, 0n, 2500n * 10n ** 30n, 10n ** 15n, 0n, 0n],
      orderType: ORDER_TYPES.MarketIncrease,
      decreasePositionSwapType: DECREASE_POSITION_SWAP_TYPES.NoSwap,
      isLong: true,
      shouldUnwrapNativeToken: false,
      referralCode: '0x0000000000000000000000000000000000000000000000000000000000000000',
    });

    const decoded = decodeFunctionData({ abi: EXCHANGE_ROUTER_ABI, data: calldata });
    expect(decoded.functionName).toBe('createOrder');
  });

  it('produces valid hex calldata', () => {
    const calldata = encodeCreateOrder({
      addresses: [MOCK_MARKET, MOCK_COLLATERAL, MOCK_RECEIVER, '0x0000000000000000000000000000000000000000'],
      numbers: [1000n * 10n ** 30n, 10n ** 6n, 0n, 0n, 10n ** 15n, 0n, 0n],
      orderType: ORDER_TYPES.MarketIncrease,
      decreasePositionSwapType: DECREASE_POSITION_SWAP_TYPES.NoSwap,
      isLong: true,
      shouldUnwrapNativeToken: false,
      referralCode: '0x0000000000000000000000000000000000000000000000000000000000000000',
    });
    expect(calldata).toMatch(/^0x[0-9a-f]+$/i);
  });
});

// ── encodeOpenPosition ──────────────────────────────────────

describe('encodeOpenPosition', () => {
  const baseParams = {
    market: MOCK_MARKET,
    collateralToken: MOCK_COLLATERAL,
    sizeDeltaUsd: 1000n * 10n ** 30n,
    collateralAmount: 100n * 10n ** 6n,
    isLong: true,
    acceptablePrice: 2500n * 10n ** 30n,
    executionFee: 10n ** 15n,
    receiver: MOCK_RECEIVER,
  };

  it('encode/decode roundtrip for long position', () => {
    const calldata = encodeOpenPosition(baseParams);
    const decoded = decodeFunctionData({ abi: EXCHANGE_ROUTER_ABI, data: calldata });
    expect(decoded.functionName).toBe('createOrder');

    const args = decoded.args[0] as Record<string, unknown>;
    expect(args.orderType).toBe(ORDER_TYPES.MarketIncrease);
    expect(args.isLong).toBe(true);
  });

  it('encodes short position', () => {
    const calldata = encodeOpenPosition({ ...baseParams, isLong: false });
    const decoded = decodeFunctionData({ abi: EXCHANGE_ROUTER_ABI, data: calldata });
    const args = decoded.args[0] as Record<string, unknown>;
    expect(args.isLong).toBe(false);
  });

  it('long and short produce different calldata', () => {
    const longData = encodeOpenPosition(baseParams);
    const shortData = encodeOpenPosition({ ...baseParams, isLong: false });
    expect(longData).not.toBe(shortData);
  });
});

// ── encodeClosePosition ──────────────────────────────────────

describe('encodeClosePosition', () => {
  const baseParams = {
    market: MOCK_MARKET,
    collateralToken: MOCK_COLLATERAL,
    sizeDeltaUsd: 500n * 10n ** 30n,
    collateralDeltaAmount: 50n * 10n ** 6n,
    isLong: true,
    acceptablePrice: 2400n * 10n ** 30n,
    executionFee: 10n ** 15n,
    receiver: MOCK_RECEIVER,
  };

  it('encode/decode roundtrip', () => {
    const calldata = encodeClosePosition(baseParams);
    const decoded = decodeFunctionData({ abi: EXCHANGE_ROUTER_ABI, data: calldata });
    expect(decoded.functionName).toBe('createOrder');

    const args = decoded.args[0] as Record<string, unknown>;
    expect(args.orderType).toBe(ORDER_TYPES.MarketDecrease);
  });

  it('open and close use different order types', () => {
    const openData = encodeOpenPosition({
      ...baseParams, sizeDeltaUsd: 1000n * 10n ** 30n, collateralAmount: 100n * 10n ** 6n,
      acceptablePrice: 2500n * 10n ** 30n,
    });
    const closeData = encodeClosePosition(baseParams);
    expect(openData).not.toBe(closeData);
  });
});

// ── encodeAdjustMargin ──────────────────────────────────────

describe('encodeAdjustMargin', () => {
  it('encodes deposit margin (add collateral)', () => {
    const calldata = encodeAdjustMargin({
      market: MOCK_MARKET,
      collateralToken: MOCK_COLLATERAL,
      collateralDeltaAmount: 50n * 10n ** 6n,
      isDeposit: true,
      isLong: true,
      executionFee: 10n ** 15n,
      receiver: MOCK_RECEIVER,
    });

    const decoded = decodeFunctionData({ abi: EXCHANGE_ROUTER_ABI, data: calldata });
    const args = decoded.args[0] as Record<string, unknown>;
    expect(args.orderType).toBe(ORDER_TYPES.MarketIncrease);
    // sizeDeltaUsd should be 0 (just adjusting margin)
    expect((args.numbers as bigint[])[0]).toBe(0n);
  });

  it('encodes withdraw margin (remove collateral)', () => {
    const calldata = encodeAdjustMargin({
      market: MOCK_MARKET,
      collateralToken: MOCK_COLLATERAL,
      collateralDeltaAmount: 25n * 10n ** 6n,
      isDeposit: false,
      isLong: true,
      executionFee: 10n ** 15n,
      receiver: MOCK_RECEIVER,
    });

    const decoded = decodeFunctionData({ abi: EXCHANGE_ROUTER_ABI, data: calldata });
    const args = decoded.args[0] as Record<string, unknown>;
    expect(args.orderType).toBe(ORDER_TYPES.MarketDecrease);
  });
});

// ── encodeCancelOrder ──────────────────────────────────────

describe('encodeCancelOrder', () => {
  it('encode/decode roundtrip', () => {
    const orderKey = '0xabcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890' as Hex;
    const calldata = encodeCancelOrder(orderKey);
    const decoded = decodeFunctionData({ abi: EXCHANGE_ROUTER_ABI, data: calldata });

    expect(decoded.functionName).toBe('cancelOrder');
    expect(decoded.args[0]).toBe(orderKey);
  });

  it('different order keys produce different calldata', () => {
    const key1 = '0x0000000000000000000000000000000000000000000000000000000000000001' as Hex;
    const key2 = '0x0000000000000000000000000000000000000000000000000000000000000002' as Hex;
    expect(encodeCancelOrder(key1)).not.toBe(encodeCancelOrder(key2));
  });
});

// ── Query Encoders ──────────────────────────────────────

describe('GMX query encoders', () => {
  it('encodeGetPosition produces valid calldata', () => {
    const posKey = '0xabcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890' as Hex;
    const calldata = encodeGetPosition(DATA_STORE, posKey);
    const decoded = decodeFunctionData({ abi: READER_ABI, data: calldata });

    expect(decoded.functionName).toBe('getPosition');
    expect(decoded.args[0]).toBe(DATA_STORE);
    expect(decoded.args[1]).toBe(posKey);
  });

  it('encodeGetMarket produces valid calldata', () => {
    const calldata = encodeGetMarket(DATA_STORE, MOCK_MARKET);
    const decoded = decodeFunctionData({ abi: READER_ABI, data: calldata });

    expect(decoded.functionName).toBe('getMarket');
    expect(decoded.args[0]).toBe(DATA_STORE);
    expect(decoded.args[1]).toBe(MOCK_MARKET);
  });

  it('encodeFundingRateQuery produces valid calldata', () => {
    const key = '0x0000000000000000000000000000000000000000000000000000000000000abc' as Hex;
    const calldata = encodeFundingRateQuery(key);
    const decoded = decodeFunctionData({ abi: DATA_STORE_ABI, data: calldata });

    expect(decoded.functionName).toBe('getInt');
    expect(decoded.args[0]).toBe(key);
  });
});

// ── Selector uniqueness ──────────────────────────────────────

describe('GMX selector uniqueness', () => {
  it('all ExchangeRouter selectors are distinct', () => {
    const orderData = encodeOpenPosition({
      market: MOCK_MARKET, collateralToken: MOCK_COLLATERAL,
      sizeDeltaUsd: 1n, collateralAmount: 1n, isLong: true,
      acceptablePrice: 1n, executionFee: 1n, receiver: MOCK_RECEIVER,
    });
    const cancelData = encodeCancelOrder(
      '0x0000000000000000000000000000000000000000000000000000000000000001',
    );

    const selectors = [orderData.slice(0, 10), cancelData.slice(0, 10)];
    expect(new Set(selectors).size).toBe(selectors.length);
  });

  it('ABIs are importable and non-empty', () => {
    expect(EXCHANGE_ROUTER_ABI.length).toBeGreaterThan(0);
    expect(READER_ABI.length).toBeGreaterThan(0);
    expect(DATA_STORE_ABI.length).toBeGreaterThan(0);
  });
});

// ── L2 Gas Estimation ──────────────────────────────────────

describe('estimateArbitrumGas', () => {
  it('calculates L2 + L1 data cost', () => {
    const result = estimateArbitrumGas({
      l2GasEstimate: 500_000n,
      l2GasPriceWei: 100_000_000n,    // 0.1 gwei
      calldataBytes: 500n,
      l1GasPriceWei: 30_000_000_000n,  // 30 gwei
    });

    // L2 cost: 500k * 0.1 gwei = 50,000 gwei = 5e13 wei
    expect(result.l2CostWei).toBe(50_000_000_000_000n);
    // L1 data cost: 500 bytes * 16 gas/byte * 30 gwei = 240,000 gwei = 2.4e14 wei
    expect(result.l1DataCostWei).toBe(240_000_000_000_000n);
    // Total
    expect(result.totalCostWei).toBe(result.l2CostWei + result.l1DataCostWei);
  });

  it('returns zero L1 cost when calldata is empty', () => {
    const result = estimateArbitrumGas({
      l2GasEstimate: 100_000n,
      l2GasPriceWei: 100_000_000n,
      calldataBytes: 0n,
      l1GasPriceWei: 30_000_000_000n,
    });

    expect(result.l1DataCostWei).toBe(0n);
    expect(result.totalCostWei).toBe(result.l2CostWei);
  });

  it('L1 data cost dominates for large calldata', () => {
    const result = estimateArbitrumGas({
      l2GasEstimate: 100_000n,
      l2GasPriceWei: 100_000_000n,       // 0.1 gwei (cheap L2)
      calldataBytes: 10_000n,             // 10KB calldata
      l1GasPriceWei: 50_000_000_000n,     // 50 gwei (expensive L1)
    });

    expect(result.l1DataCostWei).toBeGreaterThan(result.l2CostWei);
  });
});
