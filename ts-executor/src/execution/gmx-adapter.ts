/**
 * EXEC-009: GMX V2 encode module (Arbitrum).
 *
 * Lightweight protocol encoder for GMX V2 perpetual positions on Arbitrum.
 * ABI definitions + encode functions only. TransactionBuilder handles execution.
 *
 * GMX V2 uses an asynchronous order system: orders are created via ExchangeRouter,
 * then executed by keeper nodes. Positions are identified by (account, market, collateral, isLong).
 */

import { type Address, type Hex, encodeFunctionData, parseAbi } from 'viem';

// ── ABIs ──────────────────────────────────────────

/** GMX V2 ExchangeRouter for order creation. */
export const EXCHANGE_ROUTER_ABI = parseAbi([
  'function createOrder((address[] addresses, uint256[] numbers, bytes32 orderType, bytes32 decreasePositionSwapType, bool isLong, bool shouldUnwrapNativeToken, bytes32 referralCode) params) payable returns (bytes32)',
  'function cancelOrder(bytes32 key)',
  'function multicall(bytes[] data) payable returns (bytes[])',
]);

/** GMX V2 Reader for querying positions and funding rates. */
export const READER_ABI = parseAbi([
  'function getPosition(address dataStore, bytes32 key) view returns ((address account, address market, address collateralToken, bool isLong, uint256 sizeInUsd, uint256 sizeInTokens, uint256 collateralAmount, uint256 borrowingFactor, uint256 fundingFeeAmountPerSize, uint256 longTokenClaimableFundingAmountPerSize, uint256 shortTokenClaimableFundingAmountPerSize, uint256 increasedAtBlock, uint256 decreasedAtBlock))',
  'function getMarket(address dataStore, address marketAddress) view returns ((address marketToken, address indexToken, address longToken, address shortToken))',
  'function getMarketTokenPrice(address dataStore, address market, (uint256 min, uint256 max) indexTokenPrice, (uint256 min, uint256 max) longTokenPrice, (uint256 min, uint256 max) shortTokenPrice, bytes32 pnlFactorType, bool maximize) view returns (int256, (uint256 poolValue, int256 longPnl, int256 shortPnl, int256 netPnl, uint256 longTokenAmount, uint256 shortTokenAmount, uint256 longTokenUsd, uint256 shortTokenUsd, uint256 totalBorrowingFees, uint256 borrowingFeePoolFactor, uint256 impactPoolAmount))',
]);

/** GMX V2 DataStore for direct reads. */
export const DATA_STORE_ABI = parseAbi([
  'function getUint(bytes32 key) view returns (uint256)',
  'function getInt(bytes32 key) view returns (int256)',
  'function getAddress(bytes32 key) view returns (address)',
]);

// ── Addresses (Arbitrum) ──────────────────────────────────────

/** GMX V2 ExchangeRouter on Arbitrum. */
export const EXCHANGE_ROUTER: Address =
  (process.env.GMX_EXCHANGE_ROUTER as Address | undefined)
  ?? '0x7C68C7866A64FA2160F78EEaE12217FFbf871fa8';

/** GMX V2 Reader on Arbitrum. */
export const READER_ADDRESS: Address =
  (process.env.GMX_READER as Address | undefined)
  ?? '0xf60becbba223EEA9495Da3f606753867eC10d139';

/** GMX V2 DataStore on Arbitrum. */
export const DATA_STORE: Address =
  (process.env.GMX_DATA_STORE as Address | undefined)
  ?? '0xFD70de6b91282D8017aA4E741e9Ae325CAb992d8';

// ── Order Type Constants ──────────────────────────────────────

/** GMX V2 order type hashes. */
export const ORDER_TYPES = {
  MarketIncrease: '0x0000000000000000000000000000000000000000000000000000000000000000' as Hex,
  MarketDecrease: '0x0000000000000000000000000000000000000000000000000000000000000001' as Hex,
  LimitIncrease: '0x0000000000000000000000000000000000000000000000000000000000000002' as Hex,
  LimitDecrease: '0x0000000000000000000000000000000000000000000000000000000000000003' as Hex,
  StopLossDecrease: '0x0000000000000000000000000000000000000000000000000000000000000004' as Hex,
} as const;

export const DECREASE_POSITION_SWAP_TYPES = {
  NoSwap: '0x0000000000000000000000000000000000000000000000000000000000000000' as Hex,
  SwapPnlTokenToCollateralToken: '0x0000000000000000000000000000000000000000000000000000000000000001' as Hex,
  SwapCollateralTokenToPnlToken: '0x0000000000000000000000000000000000000000000000000000000000000002' as Hex,
} as const;

// ── Encoder Param Types ──────────────────────────────────────

export interface CreateOrderParams {
  /** [market, initialCollateralToken, swapPath[0]..., receiver, callbackContract]. */
  addresses: Address[];
  /**
   * Order numbers array:
   * [sizeDeltaUsd, initialCollateralDeltaAmount, triggerPrice,
   *  acceptablePrice, executionFee, callbackGasLimit, minOutputAmount].
   */
  numbers: bigint[];
  /** Order type (MarketIncrease, MarketDecrease, etc.). */
  orderType: Hex;
  /** Decrease position swap type. */
  decreasePositionSwapType: Hex;
  /** True for long, false for short. */
  isLong: boolean;
  /** Whether to unwrap native token on output. */
  shouldUnwrapNativeToken: boolean;
  /** Referral code. */
  referralCode: Hex;
}

export interface OpenPositionParams {
  /** GMX market address. */
  market: Address;
  /** Collateral token address. */
  collateralToken: Address;
  /** Position size in USD (30 decimals). */
  sizeDeltaUsd: bigint;
  /** Collateral amount to deposit. */
  collateralAmount: bigint;
  /** True for long, false for short. */
  isLong: boolean;
  /** Acceptable execution price (30 decimals). */
  acceptablePrice: bigint;
  /** Execution fee for keeper (in ETH). */
  executionFee: bigint;
  /** Receiver of the position output. */
  receiver: Address;
}

export interface ClosePositionParams {
  /** GMX market address. */
  market: Address;
  /** Collateral token address. */
  collateralToken: Address;
  /** Size to close in USD (30 decimals). */
  sizeDeltaUsd: bigint;
  /** Collateral to withdraw. */
  collateralDeltaAmount: bigint;
  /** True for long, false for short. */
  isLong: boolean;
  /** Acceptable execution price (30 decimals). */
  acceptablePrice: bigint;
  /** Execution fee for keeper (in ETH). */
  executionFee: bigint;
  /** Receiver of the output. */
  receiver: Address;
}

export interface AdjustMarginParams {
  /** GMX market address. */
  market: Address;
  /** Collateral token address. */
  collateralToken: Address;
  /** Amount to add or remove. */
  collateralDeltaAmount: bigint;
  /** True to add margin, false to remove. */
  isDeposit: boolean;
  /** True for long, false for short. */
  isLong: boolean;
  /** Execution fee for keeper (in ETH). */
  executionFee: bigint;
  /** Receiver of the output. */
  receiver: Address;
}

// ── Encoders ──────────────────────────────────────

/** Encode a createOrder call on the ExchangeRouter. */
export function encodeCreateOrder(params: CreateOrderParams): Hex {
  return encodeFunctionData({
    abi: EXCHANGE_ROUTER_ABI,
    functionName: 'createOrder',
    args: [{
      addresses: params.addresses,
      numbers: params.numbers,
      orderType: params.orderType,
      decreasePositionSwapType: params.decreasePositionSwapType,
      isLong: params.isLong,
      shouldUnwrapNativeToken: params.shouldUnwrapNativeToken,
      referralCode: params.referralCode,
    }],
  });
}

/**
 * Encode an order to open (or increase) a perpetual position.
 * @param p - Position open parameters.
 */
export function encodeOpenPosition(p: OpenPositionParams): Hex {
  return encodeCreateOrder({
    addresses: [p.market, p.collateralToken, p.receiver, '0x0000000000000000000000000000000000000000'],
    numbers: [p.sizeDeltaUsd, p.collateralAmount, 0n, p.acceptablePrice, p.executionFee, 0n, 0n],
    orderType: ORDER_TYPES.MarketIncrease,
    decreasePositionSwapType: DECREASE_POSITION_SWAP_TYPES.NoSwap,
    isLong: p.isLong,
    shouldUnwrapNativeToken: false,
    referralCode: '0x0000000000000000000000000000000000000000000000000000000000000000',
  });
}

/**
 * Encode an order to close (or decrease) a perpetual position.
 * @param p - Position close parameters.
 */
export function encodeClosePosition(p: ClosePositionParams): Hex {
  return encodeCreateOrder({
    addresses: [p.market, p.collateralToken, p.receiver, '0x0000000000000000000000000000000000000000'],
    numbers: [p.sizeDeltaUsd, p.collateralDeltaAmount, 0n, p.acceptablePrice, p.executionFee, 0n, 0n],
    orderType: ORDER_TYPES.MarketDecrease,
    decreasePositionSwapType: DECREASE_POSITION_SWAP_TYPES.NoSwap,
    isLong: p.isLong,
    shouldUnwrapNativeToken: false,
    referralCode: '0x0000000000000000000000000000000000000000000000000000000000000000',
  });
}

/**
 * Encode a margin adjustment (add or remove collateral without changing position size).
 * @param p - Margin adjustment parameters.
 */
export function encodeAdjustMargin(p: AdjustMarginParams): Hex {
  const orderType = p.isDeposit ? ORDER_TYPES.MarketIncrease : ORDER_TYPES.MarketDecrease;
  return encodeCreateOrder({
    addresses: [p.market, p.collateralToken, p.receiver, '0x0000000000000000000000000000000000000000'],
    numbers: [0n, p.collateralDeltaAmount, 0n, 0n, p.executionFee, 0n, 0n],
    orderType,
    decreasePositionSwapType: DECREASE_POSITION_SWAP_TYPES.NoSwap,
    isLong: p.isLong,
    shouldUnwrapNativeToken: false,
    referralCode: '0x0000000000000000000000000000000000000000000000000000000000000000',
  });
}

/** Encode a cancelOrder call. */
export function encodeCancelOrder(orderKey: Hex): Hex {
  return encodeFunctionData({
    abi: EXCHANGE_ROUTER_ABI,
    functionName: 'cancelOrder',
    args: [orderKey],
  });
}

// ── Query Encoders ──────────────────────────────────────

/** Encode a getPosition query on the Reader. */
export function encodeGetPosition(dataStore: Address, positionKey: Hex): Hex {
  return encodeFunctionData({
    abi: READER_ABI,
    functionName: 'getPosition',
    args: [dataStore, positionKey],
  });
}

/** Encode a getMarket query on the Reader. */
export function encodeGetMarket(dataStore: Address, market: Address): Hex {
  return encodeFunctionData({
    abi: READER_ABI,
    functionName: 'getMarket',
    args: [dataStore, market],
  });
}

/**
 * Encode a DataStore uint read for funding rate lookups.
 * @param key - The storage key for the funding rate.
 */
export function encodeFundingRateQuery(key: Hex): Hex {
  return encodeFunctionData({
    abi: DATA_STORE_ABI,
    functionName: 'getInt',
    args: [key],
  });
}

// ── L2 Gas Estimation ──────────────────────────────────────

/**
 * Estimate total gas cost for a GMX transaction on Arbitrum.
 * Arbitrum L2 gas = L2 execution gas + L1 data posting cost.
 * @param params - Gas estimation inputs.
 * @param params.l2GasEstimate - Estimated L2 execution gas units.
 * @param params.l2GasPriceWei - L2 gas price in Wei.
 * @param params.calldataBytes - Size of transaction calldata in bytes.
 * @param params.l1GasPriceWei - Current L1 gas price in Wei.
 * @returns Total estimated gas cost in Wei (L2 execution + L1 data).
 */
export function estimateArbitrumGas(params: {
  l2GasEstimate: bigint;
  l2GasPriceWei: bigint;
  calldataBytes: bigint;
  l1GasPriceWei: bigint;
}): { l2CostWei: bigint; l1DataCostWei: bigint; totalCostWei: bigint } {
  const l2CostWei = params.l2GasEstimate * params.l2GasPriceWei;
  // Arbitrum L1 data cost: ~16 gas per non-zero byte of calldata
  const L1_GAS_PER_BYTE = 16n;
  const l1DataCostWei = params.calldataBytes * L1_GAS_PER_BYTE * params.l1GasPriceWei;
  return {
    l2CostWei,
    l1DataCostWei,
    totalCostWei: l2CostWei + l1DataCostWei,
  };
}
