/**
 * EXEC-005: Uniswap V3 protocol adapter.
 *
 * Provides concentrated liquidity operations on Uniswap V3:
 * - Mint new positions with specified price ranges
 * - Increase/decrease liquidity in existing positions
 * - Collect accumulated fees from positions
 * - Burn (close) positions entirely
 * - Pool state queries (price, tick, liquidity, volume)
 * - All swaps routed through Flashbots Protect
 */

import {
  type PublicClient,
  type WalletClient,
  type Address,
  type Hex,
  type Chain,
  createPublicClient,
  http,
  encodeFunctionData,
  parseAbi,
} from 'viem';
import { sepolia } from 'viem/chains';
/** @deprecated Transitional — adapters no longer own wallet execution. */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type SmartWalletManager = any;

// ── Uniswap V3 ABIs ──────────────────────────────

const NONFUNGIBLE_POSITION_MANAGER_ABI = parseAbi([
  'function mint((address token0, address token1, uint24 fee, int24 tickLower, int24 tickUpper, uint256 amount0Desired, uint256 amount1Desired, uint256 amount0Min, uint256 amount1Min, address recipient, uint256 deadline)) returns (uint256 tokenId, uint128 liquidity, uint256 amount0, uint256 amount1)',
  'function increaseLiquidity((uint256 tokenId, uint256 amount0Desired, uint256 amount1Desired, uint256 amount0Min, uint256 amount1Min, uint256 deadline)) returns (uint128 liquidity, uint256 amount0, uint256 amount1)',
  'function decreaseLiquidity((uint256 tokenId, uint128 liquidity, uint256 amount0Min, uint256 amount1Min, uint256 deadline)) returns (uint256 amount0, uint256 amount1)',
  'function collect((uint256 tokenId, address recipient, uint128 amount0Max, uint128 amount1Max)) returns (uint256 amount0, uint256 amount1)',
  'function burn(uint256 tokenId)',
  'function positions(uint256 tokenId) view returns (uint96 nonce, address operator, address token0, address token1, uint24 fee, int24 tickLower, int24 tickUpper, uint128 liquidity, uint256 feeGrowthInside0LastX128, uint256 feeGrowthInside1LastX128, uint128 tokensOwed0, uint128 tokensOwed1)',
]);

const UNISWAP_V3_POOL_ABI = parseAbi([
  'function slot0() view returns (uint160 sqrtPriceX96, int24 tick, uint16 observationIndex, uint16 observationCardinality, uint16 observationCardinalityNext, uint8 feeProtocol, bool unlocked)',
  'function liquidity() view returns (uint128)',
  'function ticks(int24 tick) view returns (uint128 liquidityGross, int128 liquidityNet, uint256 feeGrowthOutside0X128, uint256 feeGrowthOutside1X128, int56 tickCumulativeOutside, uint160 secondsPerLiquidityOutsideX128, uint32 secondsOutside, bool initialized)',
  'function fee() view returns (uint24)',
  'function token0() view returns (address)',
  'function token1() view returns (address)',
]);

const UNISWAP_V3_FACTORY_ABI = parseAbi([
  'function getPool(address tokenA, address tokenB, uint24 fee) view returns (address)',
]);

const ERC20_ABI = parseAbi([
  'function approve(address spender, uint256 amount) returns (bool)',
  'function allowance(address owner, address spender) view returns (uint256)',
]);

// ── Types ──────────────────────────────────────────

/** Options for Uniswap V3 adapter initialization. */
export interface UniswapV3AdapterOptions {
  /** NonfungiblePositionManager address. Defaults to Sepolia deployment. */
  positionManagerAddress?: Address;
  /** Uniswap V3 Factory address. Defaults to Sepolia deployment. */
  factoryAddress?: Address;
  /** RPC URL. Defaults to env ALCHEMY_SEPOLIA_HTTP_URL. */
  rpcUrl?: string;
  /** Chain. Defaults to sepolia. */
  chain?: Chain;
  /** Structured log callback. */
  onLog?: (event: string, message: string, extra?: Record<string, unknown>) => void;
  /** Override public client (for testing). */
  publicClient?: PublicClient;
  /** Override wallet client (for testing). */
  walletClient?: WalletClient;
  /** Smart wallet manager (optional). */
  smartWallet?: SmartWalletManager;
  /** Whether to use Flashbots Protect for swaps. Defaults to true. */
  useFlashbots?: boolean;
}

/** Parameters for minting a new position. */
export interface MintParams {
  token0: Address;
  token1: Address;
  fee: number;
  tickLower: number;
  tickUpper: number;
  amount0Desired: bigint;
  amount1Desired: bigint;
  amount0Min?: bigint;
  amount1Min?: bigint;
  recipient?: Address;
  deadline?: bigint;
}

/** Result of minting a new position. */
export interface MintResult {
  txHash: `0x${string}`;
  gasUsed: bigint;
  gasEstimate: bigint;
  tokenId: bigint;
}

/** Parameters for increasing liquidity. */
export interface IncreaseLiquidityParams {
  tokenId: bigint;
  amount0Desired: bigint;
  amount1Desired: bigint;
  amount0Min?: bigint;
  amount1Min?: bigint;
  deadline?: bigint;
}

/** Parameters for decreasing liquidity. */
export interface DecreaseLiquidityParams {
  tokenId: bigint;
  liquidity: bigint;
  amount0Min?: bigint;
  amount1Min?: bigint;
  deadline?: bigint;
}

/** Result of a liquidity change operation. */
export interface LiquidityResult {
  txHash: `0x${string}`;
  gasUsed: bigint;
  gasEstimate: bigint;
}

/** Result of collecting fees. */
export interface CollectResult {
  txHash: `0x${string}`;
  gasUsed: bigint;
  gasEstimate: bigint;
}

/** Result of burning a position. */
export interface BurnResult {
  txHash: `0x${string}`;
  gasUsed: bigint;
  gasEstimate: bigint;
}

/** Information about a position. */
export interface PositionInfo {
  token0: Address;
  token1: Address;
  fee: number;
  tickLower: number;
  tickUpper: number;
  liquidity: bigint;
  tokensOwed0: bigint;
  tokensOwed1: bigint;
}

/** Pool state data. */
export interface PoolState {
  sqrtPriceX96: bigint;
  currentTick: number;
  totalLiquidity: bigint;
  fee: number;
  token0: Address;
  token1: Address;
  currentPrice: number;
}

/** Tick info returned from queries. */
export interface TickInfo {
  liquidityGross: bigint;
  liquidityNet: bigint;
  initialized: boolean;
}

// ── Default Addresses (Sepolia) ──────────────────

/** Uniswap V3 NonfungiblePositionManager on Sepolia. */
const SEPOLIA_POSITION_MANAGER: Address = '0x1238536071E1c677A632429e3655c799b22cDA52';
/** Uniswap V3 Factory on Sepolia. */
const SEPOLIA_FACTORY: Address = '0x0227628f3F023bb0B980b67D528571c95c6DaC1c';

// ── Adapter ──────────────────────────────────────────

/** Adapter for Uniswap V3 concentrated liquidity operations. */
export class UniswapV3Adapter {
  private readonly positionManagerAddress: Address;
  private readonly factoryAddress: Address;
  private readonly publicClient: PublicClient;
  private readonly walletClient: WalletClient | null;
  private readonly smartWallet: SmartWalletManager | null;
  private readonly log: (event: string, message: string, extra?: Record<string, unknown>) => void;
  private readonly useFlashbots: boolean;

  constructor(opts: UniswapV3AdapterOptions = {}) {
    this.positionManagerAddress = opts.positionManagerAddress
      ?? (process.env.UNISWAP_V3_POSITION_MANAGER as Address | undefined)
      ?? SEPOLIA_POSITION_MANAGER;
    this.factoryAddress = opts.factoryAddress
      ?? (process.env.UNISWAP_V3_FACTORY as Address | undefined)
      ?? SEPOLIA_FACTORY;
    this.smartWallet = opts.smartWallet ?? null;
    this.walletClient = opts.walletClient ?? null;
    this.log = opts.onLog ?? (() => {});
    this.useFlashbots = opts.useFlashbots ?? true;

    const chain = opts.chain ?? sepolia;
    const rpcUrl = opts.rpcUrl ?? process.env.ALCHEMY_SEPOLIA_HTTP_URL;

    this.publicClient = opts.publicClient ?? createPublicClient({
      chain,
      transport: http(rpcUrl),
    });
  }

  /** Get the NonfungiblePositionManager address. */
  get positionManager(): Address {
    return this.positionManagerAddress;
  }

  /** Get the Factory address. */
  get factory(): Address {
    return this.factoryAddress;
  }

  /** Check if Flashbots Protect is enabled. */
  get flashbotsEnabled(): boolean {
    return this.useFlashbots;
  }

  // ── Position Management ──────────────────────────

  /**
   * Mint a new concentrated liquidity position.
   * Handles ERC-20 approvals for both tokens.
   */
  async mint(params: MintParams): Promise<MintResult> {
    const sender = this.getSenderAddress();
    const recipient = params.recipient ?? sender;
    const deadline = params.deadline ?? BigInt(Math.floor(Date.now() / 1000) + 1800);

    this.log('uniswap_mint_start', 'Starting Uniswap V3 position mint', {
      token0: params.token0,
      token1: params.token1,
      fee: params.fee,
      tickLower: params.tickLower,
      tickUpper: params.tickUpper,
      amount0Desired: params.amount0Desired.toString(),
      amount1Desired: params.amount1Desired.toString(),
    });

    // Approve both tokens
    await this.ensureAllowance(params.token0, params.amount0Desired, sender);
    await this.ensureAllowance(params.token1, params.amount1Desired, sender);

    const callData = encodeFunctionData({
      abi: NONFUNGIBLE_POSITION_MANAGER_ABI,
      functionName: 'mint',
      args: [{
        token0: params.token0,
        token1: params.token1,
        fee: params.fee,
        tickLower: params.tickLower,
        tickUpper: params.tickUpper,
        amount0Desired: params.amount0Desired,
        amount1Desired: params.amount1Desired,
        amount0Min: params.amount0Min ?? 0n,
        amount1Min: params.amount1Min ?? 0n,
        recipient,
        deadline,
      }],
    });

    const gasEstimate = await this.estimateGas(this.positionManagerAddress, callData);
    const txHash = await this.sendTransaction(this.positionManagerAddress, callData, gasEstimate);

    const receipt = await this.publicClient.waitForTransactionReceipt({
      hash: txHash,
      timeout: 120_000,
    });

    // Parse tokenId from receipt logs (Transfer event from NFT)
    const tokenId = this.parseTokenIdFromReceipt(receipt.logs);

    this.log('uniswap_mint_complete', 'Uniswap V3 position minted', {
      txHash,
      tokenId: tokenId.toString(),
      gasUsed: receipt.gasUsed.toString(),
      gasEstimate: gasEstimate.toString(),
      blockNumber: Number(receipt.blockNumber),
    });

    return { txHash, gasUsed: receipt.gasUsed, gasEstimate, tokenId };
  }

  /** Increase liquidity in an existing position. */
  async increaseLiquidity(params: IncreaseLiquidityParams): Promise<LiquidityResult> {
    const deadline = params.deadline ?? BigInt(Math.floor(Date.now() / 1000) + 1800);

    this.log('uniswap_increase_start', 'Increasing liquidity', {
      tokenId: params.tokenId.toString(),
      amount0Desired: params.amount0Desired.toString(),
      amount1Desired: params.amount1Desired.toString(),
    });

    const callData = encodeFunctionData({
      abi: NONFUNGIBLE_POSITION_MANAGER_ABI,
      functionName: 'increaseLiquidity',
      args: [{
        tokenId: params.tokenId,
        amount0Desired: params.amount0Desired,
        amount1Desired: params.amount1Desired,
        amount0Min: params.amount0Min ?? 0n,
        amount1Min: params.amount1Min ?? 0n,
        deadline,
      }],
    });

    const gasEstimate = await this.estimateGas(this.positionManagerAddress, callData);
    const txHash = await this.sendTransaction(this.positionManagerAddress, callData, gasEstimate);

    const receipt = await this.publicClient.waitForTransactionReceipt({
      hash: txHash,
      timeout: 120_000,
    });

    this.log('uniswap_increase_complete', 'Liquidity increased', {
      txHash,
      tokenId: params.tokenId.toString(),
      gasUsed: receipt.gasUsed.toString(),
      gasEstimate: gasEstimate.toString(),
    });

    return { txHash, gasUsed: receipt.gasUsed, gasEstimate };
  }

  /** Decrease liquidity in an existing position. */
  async decreaseLiquidity(params: DecreaseLiquidityParams): Promise<LiquidityResult> {
    const deadline = params.deadline ?? BigInt(Math.floor(Date.now() / 1000) + 1800);

    this.log('uniswap_decrease_start', 'Decreasing liquidity', {
      tokenId: params.tokenId.toString(),
      liquidity: params.liquidity.toString(),
    });

    const callData = encodeFunctionData({
      abi: NONFUNGIBLE_POSITION_MANAGER_ABI,
      functionName: 'decreaseLiquidity',
      args: [{
        tokenId: params.tokenId,
        liquidity: params.liquidity,
        amount0Min: params.amount0Min ?? 0n,
        amount1Min: params.amount1Min ?? 0n,
        deadline,
      }],
    });

    const gasEstimate = await this.estimateGas(this.positionManagerAddress, callData);
    const txHash = await this.sendTransaction(this.positionManagerAddress, callData, gasEstimate);

    const receipt = await this.publicClient.waitForTransactionReceipt({
      hash: txHash,
      timeout: 120_000,
    });

    this.log('uniswap_decrease_complete', 'Liquidity decreased', {
      txHash,
      tokenId: params.tokenId.toString(),
      gasUsed: receipt.gasUsed.toString(),
      gasEstimate: gasEstimate.toString(),
    });

    return { txHash, gasUsed: receipt.gasUsed, gasEstimate };
  }

  /** Collect accumulated fees from a position. */
  async collectFees(tokenId: bigint, recipient?: Address): Promise<CollectResult> {
    const sender = this.getSenderAddress();
    const to = recipient ?? sender;

    this.log('uniswap_collect_start', 'Collecting fees', {
      tokenId: tokenId.toString(),
      recipient: to,
    });

    const callData = encodeFunctionData({
      abi: NONFUNGIBLE_POSITION_MANAGER_ABI,
      functionName: 'collect',
      args: [{
        tokenId,
        recipient: to,
        amount0Max: BigInt('340282366920938463463374607431768211455'), // uint128 max
        amount1Max: BigInt('340282366920938463463374607431768211455'),
      }],
    });

    const gasEstimate = await this.estimateGas(this.positionManagerAddress, callData);
    const txHash = await this.sendTransaction(this.positionManagerAddress, callData, gasEstimate);

    const receipt = await this.publicClient.waitForTransactionReceipt({
      hash: txHash,
      timeout: 120_000,
    });

    this.log('uniswap_collect_complete', 'Fees collected', {
      txHash,
      tokenId: tokenId.toString(),
      gasUsed: receipt.gasUsed.toString(),
      gasEstimate: gasEstimate.toString(),
    });

    return { txHash, gasUsed: receipt.gasUsed, gasEstimate };
  }

  /**
   * Burn (close) a position entirely.
   * First collects any remaining fees, then decreases all liquidity, then burns.
   */
  async burn(tokenId: bigint): Promise<BurnResult> {
    this.log('uniswap_burn_start', 'Burning position', {
      tokenId: tokenId.toString(),
    });

    const callData = encodeFunctionData({
      abi: NONFUNGIBLE_POSITION_MANAGER_ABI,
      functionName: 'burn',
      args: [tokenId],
    });

    const gasEstimate = await this.estimateGas(this.positionManagerAddress, callData);
    const txHash = await this.sendTransaction(this.positionManagerAddress, callData, gasEstimate);

    const receipt = await this.publicClient.waitForTransactionReceipt({
      hash: txHash,
      timeout: 120_000,
    });

    this.log('uniswap_burn_complete', 'Position burned', {
      txHash,
      tokenId: tokenId.toString(),
      gasUsed: receipt.gasUsed.toString(),
      gasEstimate: gasEstimate.toString(),
    });

    return { txHash, gasUsed: receipt.gasUsed, gasEstimate };
  }

  // ── Queries ──────────────────────────────────────

  /** Query position info by tokenId. */
  async getPosition(tokenId: bigint): Promise<PositionInfo> {
    const result = await this.publicClient.readContract({
      address: this.positionManagerAddress,
      abi: NONFUNGIBLE_POSITION_MANAGER_ABI,
      functionName: 'positions',
      args: [tokenId],
    }) as unknown as unknown[];

    const position: PositionInfo = {
      token0: result[2] as Address,
      token1: result[3] as Address,
      fee: Number(result[4]),
      tickLower: Number(result[5]),
      tickUpper: Number(result[6]),
      liquidity: BigInt(result[7] as bigint),
      tokensOwed0: BigInt(result[10] as bigint),
      tokensOwed1: BigInt(result[11] as bigint),
    };

    this.log('uniswap_position_query', 'Position info retrieved', {
      tokenId: tokenId.toString(),
      token0: position.token0,
      token1: position.token1,
      fee: position.fee,
      tickLower: position.tickLower,
      tickUpper: position.tickUpper,
      liquidity: position.liquidity.toString(),
    });

    return position;
  }

  /** Query pool state (price, tick, liquidity). */
  async getPoolState(poolAddress: Address): Promise<PoolState> {
    const [slot0Result, liquidityResult, feeResult, token0Result, token1Result] = await Promise.all([
      this.publicClient.readContract({
        address: poolAddress,
        abi: UNISWAP_V3_POOL_ABI,
        functionName: 'slot0',
      }),
      this.publicClient.readContract({
        address: poolAddress,
        abi: UNISWAP_V3_POOL_ABI,
        functionName: 'liquidity',
      }),
      this.publicClient.readContract({
        address: poolAddress,
        abi: UNISWAP_V3_POOL_ABI,
        functionName: 'fee',
      }),
      this.publicClient.readContract({
        address: poolAddress,
        abi: UNISWAP_V3_POOL_ABI,
        functionName: 'token0',
      }),
      this.publicClient.readContract({
        address: poolAddress,
        abi: UNISWAP_V3_POOL_ABI,
        functionName: 'token1',
      }),
    ]);

    const slot0 = slot0Result as unknown as unknown[];
    const sqrtPriceX96 = BigInt(slot0[0] as bigint);
    const currentTick = Number(slot0[1]);
    const totalLiquidity = BigInt(liquidityResult as bigint);
    const fee = Number(feeResult as number);
    const token0 = token0Result as Address;
    const token1 = token1Result as Address;

    // Convert sqrtPriceX96 to human-readable price
    // price = (sqrtPriceX96 / 2^96)^2
    const Q96 = 2n ** 96n;
    const priceX192 = sqrtPriceX96 * sqrtPriceX96;
    const Q192 = Q96 * Q96;
    const currentPrice = Number(priceX192) / Number(Q192);

    this.log('uniswap_pool_state', 'Pool state queried', {
      poolAddress,
      currentPrice,
      currentTick,
      totalLiquidity: totalLiquidity.toString(),
      fee,
    });

    return {
      sqrtPriceX96,
      currentTick,
      totalLiquidity,
      fee,
      token0,
      token1,
      currentPrice,
    };
  }

  /** Query tick info for a specific tick. */
  async getTickInfo(poolAddress: Address, tick: number): Promise<TickInfo> {
    const result = await this.publicClient.readContract({
      address: poolAddress,
      abi: UNISWAP_V3_POOL_ABI,
      functionName: 'ticks',
      args: [tick],
    }) as unknown as unknown[];

    return {
      liquidityGross: BigInt(result[0] as bigint),
      liquidityNet: BigInt(result[1] as bigint),
      initialized: Boolean(result[7]),
    };
  }

  /** Get the pool address for a token pair and fee tier. */
  async getPoolAddress(token0: Address, token1: Address, fee: number): Promise<Address> {
    const poolAddress = await this.publicClient.readContract({
      address: this.factoryAddress,
      abi: UNISWAP_V3_FACTORY_ABI,
      functionName: 'getPool',
      args: [token0, token1, fee],
    });

    return poolAddress as Address;
  }

  // ── Encoding Helpers ──────────────────────────────

  /** Encode a mint call for external use. */
  encodeMint(params: MintParams): Hex {
    const deadline = params.deadline ?? BigInt(Math.floor(Date.now() / 1000) + 1800);
    return encodeFunctionData({
      abi: NONFUNGIBLE_POSITION_MANAGER_ABI,
      functionName: 'mint',
      args: [{
        token0: params.token0,
        token1: params.token1,
        fee: params.fee,
        tickLower: params.tickLower,
        tickUpper: params.tickUpper,
        amount0Desired: params.amount0Desired,
        amount1Desired: params.amount1Desired,
        amount0Min: params.amount0Min ?? 0n,
        amount1Min: params.amount1Min ?? 0n,
        recipient: params.recipient ?? '0x0000000000000000000000000000000000000000',
        deadline,
      }],
    });
  }

  // ── Helpers ──────────────────────────────────────

  /** Ensure ERC-20 allowance for the PositionManager. */
  private async ensureAllowance(
    token: Address,
    amount: bigint,
    owner: Address,
  ): Promise<void> {
    if (amount === 0n) return;

    const currentAllowance = await this.publicClient.readContract({
      address: token,
      abi: ERC20_ABI,
      functionName: 'allowance',
      args: [owner, this.positionManagerAddress],
    });

    if (currentAllowance >= amount) {
      this.log('uniswap_allowance_sufficient', 'ERC-20 allowance sufficient', {
        token,
        current: currentAllowance.toString(),
        required: amount.toString(),
      });
      return;
    }

    this.log('uniswap_approve_start', 'Approving ERC-20 for Uniswap V3', {
      token,
      amount: amount.toString(),
    });

    const approveData = encodeFunctionData({
      abi: ERC20_ABI,
      functionName: 'approve',
      args: [this.positionManagerAddress, amount],
    });

    const gasEstimate = await this.estimateGas(token, approveData);
    const txHash = await this.sendTransaction(token, approveData, gasEstimate);

    await this.publicClient.waitForTransactionReceipt({
      hash: txHash,
      timeout: 60_000,
    });

    this.log('uniswap_approve_complete', 'ERC-20 approval complete', {
      token,
      txHash,
    });
  }

  /** Estimate gas for a contract call with 10% buffer. */
  private async estimateGas(to: Address, data: Hex): Promise<bigint> {
    try {
      const estimate = await this.publicClient.estimateGas({
        to,
        data,
        account: this.getSenderAddress() as `0x${string}`,
      });
      return (estimate * 110n) / 100n;
    } catch {
      return 500_000n;
    }
  }

  /** Send a transaction via wallet client or smart wallet. */
  private async sendTransaction(
    to: Address,
    data: Hex,
    gas: bigint,
  ): Promise<`0x${string}`> {
    if (this.smartWallet) {
      const userOp = await this.smartWallet.buildUserOp({
        target: to,
        value: 0n,
        callData: data,
      });
      return await this.smartWallet.sendUserOp(userOp);
    }

    if (!this.walletClient) {
      throw new Error('No wallet client or smart wallet configured');
    }

    return await this.walletClient.sendTransaction({
      to,
      data,
      gas,
      chain: this.walletClient.chain,
      account: this.walletClient.account!,
    });
  }

  /** Get the sender address (smart wallet or EOA). */
  private getSenderAddress(): Address {
    if (this.smartWallet) {
      return this.smartWallet.address;
    }
    if (this.walletClient?.account) {
      return this.walletClient.account.address;
    }
    throw new Error('No wallet configured');
  }

  /** Parse tokenId from mint receipt logs. */
  private parseTokenIdFromReceipt(logs: readonly { topics: readonly string[]; data: string }[]): bigint {
    // The Transfer event from ERC-721 has tokenId as topic[3]
    for (const log of logs) {
      if (log.topics.length >= 4) {
        try {
          return BigInt(log.topics[3]);
        } catch {
          continue;
        }
      }
    }
    // Fallback: return 0 if no tokenId found
    return 0n;
  }
}
