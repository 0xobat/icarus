/**
 * EXEC-005: Uniswap V3 encode module.
 *
 * Lightweight protocol encoder for Uniswap V3 concentrated liquidity operations.
 * ABI definitions + encode functions only. TransactionBuilder handles execution.
 */

import { type Address, type Hex, encodeFunctionData, parseAbi } from 'viem';

// ── ABIs ──────────────────────────────────────────

/** NonfungiblePositionManager ABI for liquidity management. */
export const POSITION_MANAGER_ABI = parseAbi([
  'function mint((address token0, address token1, uint24 fee, int24 tickLower, int24 tickUpper, uint256 amount0Desired, uint256 amount1Desired, uint256 amount0Min, uint256 amount1Min, address recipient, uint256 deadline)) payable returns (uint256 tokenId, uint128 liquidity, uint256 amount0, uint256 amount1)',
  'function increaseLiquidity((uint256 tokenId, uint256 amount0Desired, uint256 amount1Desired, uint256 amount0Min, uint256 amount1Min, uint256 deadline)) payable returns (uint128 liquidity, uint256 amount0, uint256 amount1)',
  'function decreaseLiquidity((uint256 tokenId, uint128 liquidity, uint256 amount0Min, uint256 amount1Min, uint256 deadline)) payable returns (uint256 amount0, uint256 amount1)',
  'function collect((uint256 tokenId, address recipient, uint128 amount0Max, uint128 amount1Max)) payable returns (uint256 amount0, uint256 amount1)',
  'function burn(uint256 tokenId)',
  'function positions(uint256 tokenId) view returns (uint96 nonce, address operator, address token0, address token1, uint24 fee, int24 tickLower, int24 tickUpper, uint128 liquidity, uint256 feeGrowthInside0LastX128, uint256 feeGrowthInside1LastX128, uint128 tokensOwed0, uint128 tokensOwed1)',
]);

/** UniswapV3Pool ABI for state queries. */
export const POOL_ABI = parseAbi([
  'function slot0() view returns (uint160 sqrtPriceX96, int24 tick, uint16 observationIndex, uint16 observationCardinality, uint16 observationCardinalityNext, uint8 feeProtocol, bool unlocked)',
  'function liquidity() view returns (uint128)',
  'function ticks(int24 tick) view returns (uint128 liquidityGross, int128 liquidityNet, uint256 feeGrowthOutside0X128, uint256 feeGrowthOutside1X128, int56 tickCumulativeOutside, uint160 secondsPerLiquidityOutsideX128, uint32 secondsOutside, bool initialized)',
  'function fee() view returns (uint24)',
  'function token0() view returns (address)',
  'function token1() view returns (address)',
]);

/** UniswapV3Factory ABI. */
export const FACTORY_ABI = parseAbi([
  'function getPool(address tokenA, address tokenB, uint24 fee) view returns (address pool)',
]);

// ── Addresses ──────────────────────────────────────

/** NonfungiblePositionManager on Sepolia. */
export const POSITION_MANAGER_ADDRESS: Address =
  (process.env.UNISWAP_V3_POSITION_MANAGER as Address | undefined)
  ?? '0x1238536071E1c677A632429e3655c799b22cDA52';

/** UniswapV3Factory on Sepolia. */
export const FACTORY_ADDRESS: Address =
  (process.env.UNISWAP_V3_FACTORY as Address | undefined)
  ?? '0x0227628f3F023bb0B980b67D528571c95c6DaC1c';

// ── Encoder param types ──────────────────────────────────────

export interface MintParams {
  token0: Address;
  token1: Address;
  fee: number;
  tickLower: number;
  tickUpper: number;
  amount0Desired: bigint;
  amount1Desired: bigint;
  amount0Min: bigint;
  amount1Min: bigint;
  recipient: Address;
  deadline: bigint;
}

export interface IncreaseLiquidityParams {
  tokenId: bigint;
  amount0Desired: bigint;
  amount1Desired: bigint;
  amount0Min: bigint;
  amount1Min: bigint;
  deadline: bigint;
}

export interface DecreaseLiquidityParams {
  tokenId: bigint;
  liquidity: bigint;
  amount0Min: bigint;
  amount1Min: bigint;
  deadline: bigint;
}

export interface CollectParams {
  tokenId: bigint;
  recipient: Address;
  amount0Max: bigint;
  amount1Max: bigint;
}

// ── Encoders ──────────────────────────────────────

/** Encode a mint call to open a new concentrated liquidity position. */
export function encodeMint(params: MintParams): Hex {
  return encodeFunctionData({
    abi: POSITION_MANAGER_ABI,
    functionName: 'mint',
    args: [
      {
        token0: params.token0,
        token1: params.token1,
        fee: params.fee,
        tickLower: params.tickLower,
        tickUpper: params.tickUpper,
        amount0Desired: params.amount0Desired,
        amount1Desired: params.amount1Desired,
        amount0Min: params.amount0Min,
        amount1Min: params.amount1Min,
        recipient: params.recipient,
        deadline: params.deadline,
      },
    ],
  });
}

/** Encode an increaseLiquidity call to add liquidity to an existing position. */
export function encodeIncreaseLiquidity(params: IncreaseLiquidityParams): Hex {
  return encodeFunctionData({
    abi: POSITION_MANAGER_ABI,
    functionName: 'increaseLiquidity',
    args: [
      {
        tokenId: params.tokenId,
        amount0Desired: params.amount0Desired,
        amount1Desired: params.amount1Desired,
        amount0Min: params.amount0Min,
        amount1Min: params.amount1Min,
        deadline: params.deadline,
      },
    ],
  });
}

/** Encode a decreaseLiquidity call to remove liquidity from a position. */
export function encodeDecreaseLiquidity(params: DecreaseLiquidityParams): Hex {
  return encodeFunctionData({
    abi: POSITION_MANAGER_ABI,
    functionName: 'decreaseLiquidity',
    args: [
      {
        tokenId: params.tokenId,
        liquidity: params.liquidity,
        amount0Min: params.amount0Min,
        amount1Min: params.amount1Min,
        deadline: params.deadline,
      },
    ],
  });
}

/** Encode a collect call to claim accumulated fees from a position. */
export function encodeCollect(params: CollectParams): Hex {
  return encodeFunctionData({
    abi: POSITION_MANAGER_ABI,
    functionName: 'collect',
    args: [
      {
        tokenId: params.tokenId,
        recipient: params.recipient,
        amount0Max: params.amount0Max,
        amount1Max: params.amount1Max,
      },
    ],
  });
}

/** Encode a burn call to destroy a position NFT (must have 0 liquidity and 0 owed tokens). */
export function encodeBurn(tokenId: bigint): Hex {
  return encodeFunctionData({
    abi: POSITION_MANAGER_ABI,
    functionName: 'burn',
    args: [tokenId],
  });
}
