/**
 * EXEC-009: Aerodrome encode module (Base).
 *
 * Lightweight protocol encoder for Aerodrome (Velodrome V2 fork) on Base.
 * ABI definitions + encode functions only. TransactionBuilder handles execution.
 *
 * Aerodrome is a ve(3,3) DEX: LPs provide liquidity in pools, stake LP tokens
 * in Gauges to earn AERO rewards, and can also collect trading fees separately.
 */

import { type Address, type Hex, encodeFunctionData, parseAbi } from 'viem';

// ── ABIs ──────────────────────────────────────────

/** Aerodrome Router for add/remove liquidity and swaps. */
export const ROUTER_ABI = parseAbi([
  'function addLiquidity(address tokenA, address tokenB, bool stable, uint256 amountADesired, uint256 amountBDesired, uint256 amountAMin, uint256 amountBMin, address to, uint256 deadline) returns (uint256 amountA, uint256 amountB, uint256 liquidity)',
  'function removeLiquidity(address tokenA, address tokenB, bool stable, uint256 liquidity, uint256 amountAMin, uint256 amountBMin, address to, uint256 deadline) returns (uint256 amountA, uint256 amountB)',
  'function swapExactTokensForTokens(uint256 amountIn, uint256 amountOutMin, (address from, address to, bool stable, address factory)[] routes, address to, uint256 deadline) returns (uint256[] amounts)',
  'function getAmountsOut(uint256 amountIn, (address from, address to, bool stable, address factory)[] routes) view returns (uint256[] amounts)',
  'function poolFor(address tokenA, address tokenB, bool stable, address factory) view returns (address pool)',
]);

/** Aerodrome Pool for reserve queries. */
export const POOL_ABI = parseAbi([
  'function getReserves() view returns (uint256 reserve0, uint256 reserve1, uint256 blockTimestampLast)',
  'function token0() view returns (address)',
  'function token1() view returns (address)',
  'function stable() view returns (bool)',
  'function getAmountOut(uint256 amountIn, address tokenIn) view returns (uint256)',
]);

/** Aerodrome Gauge for LP staking and reward collection. */
export const GAUGE_ABI = parseAbi([
  'function deposit(uint256 amount)',
  'function withdraw(uint256 amount)',
  'function getReward(address account)',
  'function earned(address account) view returns (uint256)',
  'function balanceOf(address account) view returns (uint256)',
  'function rewardRate() view returns (uint256)',
  'function totalSupply() view returns (uint256)',
]);

/** ERC-20 approve for token approvals before liquidity operations. */
export const ERC20_ABI = parseAbi([
  'function approve(address spender, uint256 amount) returns (bool)',
]);

// ── Addresses (Base) ──────────────────────────────────────

/** Aerodrome Router on Base. */
export const ROUTER_ADDRESS: Address =
  (process.env.AERODROME_ROUTER as Address | undefined)
  ?? '0xcF77a3Ba9A5CA399B7c97c74d54e5b1Beb874E43';

/** Aerodrome default pool factory on Base. */
export const POOL_FACTORY: Address =
  (process.env.AERODROME_POOL_FACTORY as Address | undefined)
  ?? '0x420DD381b31aEf6683db6B902084cB0FFECe40Da';

// ── Encoder Param Types ──────────────────────────────────────

export interface AddLiquidityParams {
  /** First token in the pair. */
  tokenA: Address;
  /** Second token in the pair. */
  tokenB: Address;
  /** True for stable (correlated) pool, false for volatile pool. */
  stable: boolean;
  /** Desired amount of token A. */
  amountADesired: bigint;
  /** Desired amount of token B. */
  amountBDesired: bigint;
  /** Minimum acceptable amount of token A. */
  amountAMin: bigint;
  /** Minimum acceptable amount of token B. */
  amountBMin: bigint;
  /** Recipient of LP tokens. */
  to: Address;
  /** Unix deadline. */
  deadline: bigint;
}

export interface RemoveLiquidityParams {
  /** First token in the pair. */
  tokenA: Address;
  /** Second token in the pair. */
  tokenB: Address;
  /** True for stable pool. */
  stable: boolean;
  /** Amount of LP tokens to burn. */
  liquidity: bigint;
  /** Minimum token A to receive. */
  amountAMin: bigint;
  /** Minimum token B to receive. */
  amountBMin: bigint;
  /** Recipient of underlying tokens. */
  to: Address;
  /** Unix deadline. */
  deadline: bigint;
}

export interface SwapRoute {
  /** Source token. */
  from: Address;
  /** Destination token. */
  to: Address;
  /** True for stable pool hop. */
  stable: boolean;
  /** Factory address for this hop. */
  factory: Address;
}

// ── Encoders ──────────────────────────────────────

/** Encode an addLiquidity call on the Router. */
export function encodeAddLiquidity(p: AddLiquidityParams): Hex {
  return encodeFunctionData({
    abi: ROUTER_ABI,
    functionName: 'addLiquidity',
    args: [
      p.tokenA, p.tokenB, p.stable,
      p.amountADesired, p.amountBDesired,
      p.amountAMin, p.amountBMin,
      p.to, p.deadline,
    ],
  });
}

/** Encode a removeLiquidity call on the Router. */
export function encodeRemoveLiquidity(p: RemoveLiquidityParams): Hex {
  return encodeFunctionData({
    abi: ROUTER_ABI,
    functionName: 'removeLiquidity',
    args: [
      p.tokenA, p.tokenB, p.stable,
      p.liquidity, p.amountAMin, p.amountBMin,
      p.to, p.deadline,
    ],
  });
}

/**
 * Encode a swapExactTokensForTokens call on the Router.
 * @param amountIn - Input amount.
 * @param amountOutMin - Minimum output amount (slippage protection).
 * @param routes - Swap path hops.
 * @param to - Recipient of output tokens.
 * @param deadline - Unix deadline.
 */
export function encodeSwap(
  amountIn: bigint,
  amountOutMin: bigint,
  routes: SwapRoute[],
  to: Address,
  deadline: bigint,
): Hex {
  return encodeFunctionData({
    abi: ROUTER_ABI,
    functionName: 'swapExactTokensForTokens',
    args: [amountIn, amountOutMin, routes, to, deadline],
  });
}

/** Encode a deposit call on a Gauge (stake LP tokens). */
export function encodeGaugeDeposit(amount: bigint): Hex {
  return encodeFunctionData({
    abi: GAUGE_ABI,
    functionName: 'deposit',
    args: [amount],
  });
}

/** Encode a withdraw call on a Gauge (unstake LP tokens). */
export function encodeGaugeWithdraw(amount: bigint): Hex {
  return encodeFunctionData({
    abi: GAUGE_ABI,
    functionName: 'withdraw',
    args: [amount],
  });
}

/** Encode a getReward call on a Gauge (claim AERO rewards). */
export function encodeGetReward(account: Address): Hex {
  return encodeFunctionData({
    abi: GAUGE_ABI,
    functionName: 'getReward',
    args: [account],
  });
}

/** Encode an ERC-20 approve call (needed before addLiquidity/deposit). */
export function encodeApprove(spender: Address, amount: bigint): Hex {
  return encodeFunctionData({
    abi: ERC20_ABI,
    functionName: 'approve',
    args: [spender, amount],
  });
}

// ── Query Encoders ──────────────────────────────────────

/** Encode a getReserves query on a Pool. */
export function encodeGetReservesQuery(): Hex {
  return encodeFunctionData({ abi: POOL_ABI, functionName: 'getReserves' });
}

/** Encode an earned query on a Gauge (check pending rewards). */
export function encodeEarnedQuery(account: Address): Hex {
  return encodeFunctionData({
    abi: GAUGE_ABI,
    functionName: 'earned',
    args: [account],
  });
}

/** Encode a rewardRate query on a Gauge (tokens per second). */
export function encodeRewardRateQuery(): Hex {
  return encodeFunctionData({ abi: GAUGE_ABI, functionName: 'rewardRate' });
}

/** Encode a totalSupply query on a Gauge (total staked LP). */
export function encodeTotalSupplyQuery(): Hex {
  return encodeFunctionData({ abi: GAUGE_ABI, functionName: 'totalSupply' });
}

/**
 * Encode a getAmountsOut query on the Router (preview swap output).
 * @param amountIn - Input amount.
 * @param routes - Swap route hops.
 */
export function encodeGetAmountsOut(amountIn: bigint, routes: SwapRoute[]): Hex {
  return encodeFunctionData({
    abi: ROUTER_ABI,
    functionName: 'getAmountsOut',
    args: [amountIn, routes],
  });
}

/**
 * Encode a poolFor query on the Router (find pool address for a pair).
 * @param tokenA - First token.
 * @param tokenB - Second token.
 * @param stable - True for stable pool.
 * @param factory - Factory address (defaults to POOL_FACTORY).
 */
export function encodePoolForQuery(
  tokenA: Address,
  tokenB: Address,
  stable: boolean,
  factory?: Address,
): Hex {
  return encodeFunctionData({
    abi: ROUTER_ABI,
    functionName: 'poolFor',
    args: [tokenA, tokenB, stable, factory ?? POOL_FACTORY],
  });
}

// ── L2 Gas Estimation ──────────────────────────────────────

/**
 * Estimate total gas cost for an Aerodrome transaction on Base (OP Stack).
 * Base uses the OP Stack L1 data fee model: L1 fee = l1BaseFee * (calldataGas + overhead).
 * @param params - Gas estimation inputs.
 * @param params.l2GasEstimate - Estimated L2 execution gas units.
 * @param params.l2GasPriceWei - Current Base L2 gas price in Wei.
 * @param params.calldataBytes - Size of transaction calldata in bytes.
 * @param params.l1BaseFeeWei - Current Ethereum L1 base fee in Wei.
 * @returns Gas cost breakdown.
 */
export function estimateBaseGas(params: {
  l2GasEstimate: bigint;
  l2GasPriceWei: bigint;
  calldataBytes: bigint;
  l1BaseFeeWei: bigint;
}): { l2CostWei: bigint; l1DataCostWei: bigint; totalCostWei: bigint } {
  const l2CostWei = params.l2GasEstimate * params.l2GasPriceWei;
  // OP Stack L1 data cost: 16 gas per non-zero byte * L1 base fee
  // Plus fixed overhead (~2100 gas for rlp encoding, signature verification)
  const L1_GAS_PER_BYTE = 16n;
  const FIXED_OVERHEAD = 2100n;
  const l1DataGas = params.calldataBytes * L1_GAS_PER_BYTE + FIXED_OVERHEAD;
  const l1DataCostWei = l1DataGas * params.l1BaseFeeWei;
  return {
    l2CostWei,
    l1DataCostWei,
    totalCostWei: l2CostWei + l1DataCostWei,
  };
}
