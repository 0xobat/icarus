/**
 * EXEC-004: Aave V3 encode module.
 *
 * Lightweight protocol encoder for Aave V3 supply/withdraw operations.
 * ABI definitions + encode functions only. TransactionBuilder handles execution.
 */

import { type Address, type Hex, encodeFunctionData, parseAbi } from 'viem';

// ── ABIs ──────────────────────────────────────────

export const AAVE_POOL_ABI = parseAbi([
  'function supply(address asset, uint256 amount, address onBehalfOf, uint16 referralCode)',
  'function withdraw(address asset, uint256 amount, address to) returns (uint256)',
  'function getReserveData(address asset) view returns ((uint256 configuration, uint128 liquidityIndex, uint128 currentLiquidityRate, uint128 variableBorrowIndex, uint128 currentVariableBorrowRate, uint128 currentStableBorrowRate, uint40 lastUpdateTimestamp, uint16 id, address aTokenAddress, address stableDebtTokenAddress, address variableDebtTokenAddress, address interestRateStrategyAddress, uint128 accruedToTreasury, uint128 unbacked, uint128 isolationModeTotalDebt))',
]);

export const ERC20_ABI = parseAbi([
  'function approve(address spender, uint256 amount) returns (bool)',
  'function allowance(address owner, address spender) view returns (uint256)',
  'function balanceOf(address account) view returns (uint256)',
  'function decimals() view returns (uint8)',
]);

// ── Addresses ──────────────────────────────────────

/** Aave V3 Pool on Sepolia. */
export const AAVE_V3_POOL: Address =
  (process.env.AAVE_V3_POOL_ADDRESS as Address | undefined)
  ?? '0x6Ae43d3271ff6888e7Fc43Fd7321a503ff738951';

// ── Encoders ──────────────────────────────────────

export function encodeSupply(asset: Address, amount: bigint, onBehalfOf: Address): Hex {
  return encodeFunctionData({
    abi: AAVE_POOL_ABI,
    functionName: 'supply',
    args: [asset, amount, onBehalfOf, 0],
  });
}

export function encodeWithdraw(asset: Address, amount: bigint, to: Address): Hex {
  return encodeFunctionData({
    abi: AAVE_POOL_ABI,
    functionName: 'withdraw',
    args: [asset, amount, to],
  });
}
