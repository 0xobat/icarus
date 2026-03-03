/**
 * EXEC-007: Flash loan encode module.
 *
 * Lightweight protocol encoder for Aave V3 flash loan operations.
 * ABI definitions + encode functions only. TransactionBuilder handles execution.
 */

import { type Address, type Hex, encodeFunctionData, encodeAbiParameters, parseAbi, parseAbiParameters } from 'viem';

// ── ABIs ──────────────────────────────────────

export const FLASH_LOAN_POOL_ABI = parseAbi([
  'function flashLoan(address receiverAddress, address[] assets, uint256[] amounts, uint256[] interestRateModes, address onBehalfOf, bytes params, uint16 referralCode)',
  'function flashLoanSimple(address receiverAddress, address asset, uint256 amount, bytes params, uint16 referralCode)',
]);

export const FLASH_LOAN_RECEIVER_ABI = parseAbi([
  'function executeOperation(address[] assets, uint256[] amounts, uint256[] premiums, address initiator, bytes params) returns (bool)',
]);

// ── Constants ──────────────────────────────────────

/** Aave V3 flash loan fee: 0.05% (5 basis points). */
export const FLASH_LOAN_FEE_BPS = 5n;

/** Aave V3 Pool on Sepolia (same pool as regular supply/withdraw). */
export const AAVE_V3_POOL: Address =
  (process.env.AAVE_V3_POOL_ADDRESS as Address | undefined)
  ?? '0x6Ae43d3271ff6888e7Fc43Fd7321a503ff738951';

// ── Encoders ──────────────────────────────────────

export interface FlashLoanParams {
  /** Address of the contract that will receive the flash-loaned assets and execute the callback. */
  receiverAddress: Address;
  /** Assets to borrow. */
  assets: Address[];
  /** Amounts to borrow (must match assets length). */
  amounts: bigint[];
  /** Interest rate modes per asset (0=none, 1=stable, 2=variable). Typically all 0 for flash loans. */
  interestRateModes: bigint[];
  /** Address that will incur the debt if not repaid (usually same as receiver). */
  onBehalfOf: Address;
  /** Arbitrary data passed through to the callback's executeOperation. */
  params: Hex;
  /** Referral code (default 0). */
  referralCode?: number;
}

export interface FlashLoanSimpleParams {
  /** Address of the contract that will receive the flash-loaned asset. */
  receiverAddress: Address;
  /** Asset to borrow. */
  asset: Address;
  /** Amount to borrow. */
  amount: bigint;
  /** Arbitrary data passed through to the callback. */
  params: Hex;
  /** Referral code (default 0). */
  referralCode?: number;
}

export interface FlashLoanCallbackParams {
  /** Assets received in the flash loan. */
  assets: Address[];
  /** Amounts received. */
  amounts: bigint[];
  /** Premiums (fees) owed on each asset. */
  premiums: bigint[];
  /** Address that initiated the flash loan. */
  initiator: Address;
  /** Arbitrary params forwarded from the flash loan call. */
  params: Hex;
}

/** Encode a multi-asset flashLoan call on Aave V3 Pool. */
export function encodeFlashLoan(p: FlashLoanParams): Hex {
  return encodeFunctionData({
    abi: FLASH_LOAN_POOL_ABI,
    functionName: 'flashLoan',
    args: [
      p.receiverAddress,
      p.assets,
      p.amounts,
      p.interestRateModes,
      p.onBehalfOf,
      p.params,
      p.referralCode ?? 0,
    ],
  });
}

/** Encode a single-asset flashLoanSimple call on Aave V3 Pool. */
export function encodeFlashLoanSimple(p: FlashLoanSimpleParams): Hex {
  return encodeFunctionData({
    abi: FLASH_LOAN_POOL_ABI,
    functionName: 'flashLoanSimple',
    args: [
      p.receiverAddress,
      p.asset,
      p.amount,
      p.params,
      p.referralCode ?? 0,
    ],
  });
}

/** Encode the executeOperation callback data for IFlashLoanReceiver. */
export function encodeFlashLoanCallback(p: FlashLoanCallbackParams): Hex {
  return encodeFunctionData({
    abi: FLASH_LOAN_RECEIVER_ABI,
    functionName: 'executeOperation',
    args: [p.assets, p.amounts, p.premiums, p.initiator, p.params],
  });
}

/**
 * Calculate the flash loan premium (fee) for a given amount.
 * @returns The premium in the same units as the borrowed amount.
 */
export function calculateFlashLoanFee(amount: bigint): bigint {
  return (amount * FLASH_LOAN_FEE_BPS) / 10_000n;
}

/**
 * Estimate gas for a flash loan transaction.
 * Base cost covers the flash loan overhead (pool interactions, callback dispatch).
 * Caller should add gas for the callback logic (swaps, approvals, etc.).
 * @param callbackGasEstimate - Estimated gas for the callback logic.
 * @returns Total estimated gas for the composite TX.
 */
export function estimateFlashLoanGas(callbackGasEstimate: bigint): bigint {
  const FLASH_LOAN_BASE_GAS = 300_000n;
  return FLASH_LOAN_BASE_GAS + callbackGasEstimate;
}

// ── Profit Calculation ──────────────────────────────────────

export interface ProfitCalculation {
  /** Revenue from the arbitrage trade. */
  grossProfit: bigint;
  /** Flash loan fee paid to Aave. */
  flashLoanFee: bigint;
  /** Estimated gas cost in Wei. */
  gasCostWei: bigint;
  /** Estimated slippage cost in Wei. */
  slippageCostWei: bigint;
  /** Net profit after all costs. */
  netProfit: bigint;
  /** Whether the trade is profitable. */
  profitable: boolean;
}

/**
 * Calculate net profit for a flash loan arbitrage, accounting for
 * flash loan fee, gas cost, and slippage.
 * @param params - Profit calculation inputs.
 * @param params.grossProfit - Expected revenue from the arb.
 * @param params.borrowAmount - Amount borrowed via flash loan.
 * @param params.gasCostWei - Estimated gas cost (from estimateFlashLoanGas * gasPrice).
 * @param params.slippageBps - Expected slippage in basis points.
 * @param params.tradeAmount - Total amount traded (for slippage calculation).
 */
export function calculateProfit(params: {
  grossProfit: bigint;
  borrowAmount: bigint;
  gasCostWei: bigint;
  slippageBps: bigint;
  tradeAmount: bigint;
}): ProfitCalculation {
  const flashLoanFee = calculateFlashLoanFee(params.borrowAmount);
  const slippageCostWei = (params.tradeAmount * params.slippageBps) / 10_000n;
  const netProfit = params.grossProfit - flashLoanFee - params.gasCostWei - slippageCostWei;

  return {
    grossProfit: params.grossProfit,
    flashLoanFee,
    gasCostWei: params.gasCostWei,
    slippageCostWei,
    netProfit,
    profitable: netProfit > 0n,
  };
}

// ── Arbitrage Callback Params ──────────────────────────────────────

export interface ArbitrageCallbackData {
  /** DEX A router address (buy side). */
  dexARouter: Address;
  /** DEX B router address (sell side). */
  dexBRouter: Address;
  /** Intermediate token (the one we arb through). */
  tokenIntermediate: Address;
  /** Minimum profit in Wei — TX reverts if not met on-chain. */
  minProfitWei: bigint;
  /** Encoded swap calldata for DEX A (buy). */
  swapAData: Hex;
  /** Encoded swap calldata for DEX B (sell). */
  swapBData: Hex;
}

/**
 * Encode arbitrage callback parameters for the flash loan receiver contract.
 * The on-chain contract decodes these params and executes multi-step swap logic:
 * buy on DEX A, sell on DEX B, check profit >= minProfitWei or revert.
 */
export function encodeArbitrageParams(data: ArbitrageCallbackData): Hex {
  return encodeAbiParameters(
    parseAbiParameters('address, address, address, uint256, bytes, bytes'),
    [
      data.dexARouter,
      data.dexBRouter,
      data.tokenIntermediate,
      data.minProfitWei,
      data.swapAData,
      data.swapBData,
    ],
  );
}
