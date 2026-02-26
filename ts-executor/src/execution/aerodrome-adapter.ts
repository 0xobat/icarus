/**
 * EXEC-009 (Part 2): Aerodrome protocol adapter for Base.
 *
 * Provides Aerodrome LP operations:
 * - Provide liquidity to pools
 * - Collect fees and rewards
 * - Query pool APYs and reserves
 * - Same order schema and result reporting as Ethereum adapters
 * - L2 gas estimation accounts for L1 data posting costs (Base specific)
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
import { base } from 'viem/chains';
import { type SmartWalletManager } from '../wallet/smart-wallet.js';

// ── Aerodrome ABIs ──────────────────────────────

const AERODROME_ROUTER_ABI = parseAbi([
  'function addLiquidity(address tokenA, address tokenB, bool stable, uint256 amountADesired, uint256 amountBDesired, uint256 amountAMin, uint256 amountBMin, address to, uint256 deadline) returns (uint256 amountA, uint256 amountB, uint256 liquidity)',
  'function removeLiquidity(address tokenA, address tokenB, bool stable, uint256 liquidity, uint256 amountAMin, uint256 amountBMin, address to, uint256 deadline) returns (uint256 amountA, uint256 amountB)',
  'function getAmountOut(uint256 amountIn, address tokenIn, address tokenOut) view returns (uint256 amount, bool stable)',
  'function getReserves(address tokenA, address tokenB, bool stable) view returns (uint256 reserveA, uint256 reserveB)',
  'function poolFor(address tokenA, address tokenB, bool stable) view returns (address)',
]);

const AERODROME_GAUGE_ABI = parseAbi([
  'function deposit(uint256 amount)',
  'function withdraw(uint256 amount)',
  'function getReward(address account)',
  'function earned(address account) view returns (uint256)',
  'function balanceOf(address account) view returns (uint256)',
  'function rewardRate() view returns (uint256)',
  'function totalSupply() view returns (uint256)',
]);

const AERODROME_POOL_ABI = parseAbi([
  'function getReserves() view returns (uint256 reserve0, uint256 reserve1, uint256 blockTimestampLast)',
  'function token0() view returns (address)',
  'function token1() view returns (address)',
  'function stable() view returns (bool)',
  'function totalSupply() view returns (uint256)',
  'function balanceOf(address account) view returns (uint256)',
  'function claimFees() returns (uint256 claimed0, uint256 claimed1)',
]);

const ERC20_ABI = parseAbi([
  'function approve(address spender, uint256 amount) returns (bool)',
  'function allowance(address owner, address spender) view returns (uint256)',
  'function balanceOf(address account) view returns (uint256)',
]);

// ── Types ──────────────────────────────────────────

/** Options for Aerodrome adapter initialization. */
export interface AerodromeAdapterOptions {
  /** Aerodrome Router address. Defaults to Base deployment. */
  routerAddress?: Address;
  /** RPC URL. Defaults to env ALCHEMY_BASE_HTTP_URL. */
  rpcUrl?: string;
  /** Chain. Defaults to Base. */
  chain?: Chain;
  /** Structured log callback. */
  onLog?: (event: string, message: string, extra?: Record<string, unknown>) => void;
  /** Override public client (for testing). */
  publicClient?: PublicClient;
  /** Override wallet client (for testing). */
  walletClient?: WalletClient;
  /** Smart wallet manager (optional). */
  smartWallet?: SmartWalletManager;
}

/** Parameters for providing liquidity. */
export interface AddLiquidityParams {
  tokenA: Address;
  tokenB: Address;
  stable: boolean;
  amountADesired: bigint;
  amountBDesired: bigint;
  amountAMin?: bigint;
  amountBMin?: bigint;
  recipient?: Address;
  deadline?: bigint;
}

/** Parameters for removing liquidity. */
export interface RemoveLiquidityParams {
  tokenA: Address;
  tokenB: Address;
  stable: boolean;
  liquidity: bigint;
  amountAMin?: bigint;
  amountBMin?: bigint;
  recipient?: Address;
  deadline?: bigint;
}

/** Result of a liquidity operation. */
export interface AerodromeResult {
  txHash: `0x${string}`;
  gasUsed: bigint;
  gasEstimate: bigint;
}

/** Pool information and APY data. */
export interface PoolInfo {
  poolAddress: Address;
  token0: Address;
  token1: Address;
  stable: boolean;
  reserve0: bigint;
  reserve1: bigint;
  totalSupply: bigint;
}

/** Pool APY information. */
export interface PoolAPY {
  poolAddress: Address;
  rewardRate: bigint;
  totalStaked: bigint;
  estimatedAPY: number;
}

// ── Default Addresses (Base) ──────────────────────

/** Aerodrome Router on Base. */
const BASE_AERODROME_ROUTER: Address = '0xcF77a3Ba9A5CA399B7c97c74d54e5b1Beb874E43';

// ── Adapter ──────────────────────────────────────────

/** Adapter for Aerodrome LP operations on Base. */
export class AerodromeAdapter {
  private readonly routerAddress: Address;
  private readonly publicClient: PublicClient;
  private readonly walletClient: WalletClient | null;
  private readonly smartWallet: SmartWalletManager | null;
  private readonly log: (event: string, message: string, extra?: Record<string, unknown>) => void;

  constructor(opts: AerodromeAdapterOptions = {}) {
    this.routerAddress = opts.routerAddress
      ?? (process.env.AERODROME_ROUTER_ADDRESS as Address | undefined)
      ?? BASE_AERODROME_ROUTER;
    this.smartWallet = opts.smartWallet ?? null;
    this.walletClient = opts.walletClient ?? null;
    this.log = opts.onLog ?? (() => {});

    const chain = opts.chain ?? base;
    const rpcUrl = opts.rpcUrl ?? process.env.ALCHEMY_BASE_HTTP_URL;

    this.publicClient = opts.publicClient ?? createPublicClient({
      chain,
      transport: http(rpcUrl),
    }) as PublicClient;
  }

  /** Get the Router address. */
  get router(): Address {
    return this.routerAddress;
  }

  // ── Liquidity Operations ──────────────────────────

  /** Provide liquidity to an Aerodrome pool. */
  async addLiquidity(params: AddLiquidityParams): Promise<AerodromeResult> {
    const sender = this.getSenderAddress();
    const recipient = params.recipient ?? sender;
    const deadline = params.deadline ?? BigInt(Math.floor(Date.now() / 1000) + 1800);

    this.log('aerodrome_add_liquidity_start', 'Adding liquidity to Aerodrome pool', {
      tokenA: params.tokenA,
      tokenB: params.tokenB,
      stable: params.stable,
      amountADesired: params.amountADesired.toString(),
      amountBDesired: params.amountBDesired.toString(),
    });

    // Approve both tokens for the router
    await this.ensureAllowance(params.tokenA, params.amountADesired, sender);
    await this.ensureAllowance(params.tokenB, params.amountBDesired, sender);

    const callData = encodeFunctionData({
      abi: AERODROME_ROUTER_ABI,
      functionName: 'addLiquidity',
      args: [
        params.tokenA,
        params.tokenB,
        params.stable,
        params.amountADesired,
        params.amountBDesired,
        params.amountAMin ?? 0n,
        params.amountBMin ?? 0n,
        recipient,
        deadline,
      ],
    });

    const gasEstimate = await this.estimateL2Gas(this.routerAddress, callData);
    const txHash = await this.sendTransaction(this.routerAddress, callData, gasEstimate);

    const receipt = await this.publicClient.waitForTransactionReceipt({
      hash: txHash,
      timeout: 120_000,
    });

    this.log('aerodrome_add_liquidity_complete', 'Liquidity added', {
      txHash,
      gasUsed: receipt.gasUsed.toString(),
      gasEstimate: gasEstimate.toString(),
      blockNumber: Number(receipt.blockNumber),
    });

    return { txHash, gasUsed: receipt.gasUsed, gasEstimate };
  }

  /** Remove liquidity from an Aerodrome pool. */
  async removeLiquidity(params: RemoveLiquidityParams): Promise<AerodromeResult> {
    const sender = this.getSenderAddress();
    const recipient = params.recipient ?? sender;
    const deadline = params.deadline ?? BigInt(Math.floor(Date.now() / 1000) + 1800);

    this.log('aerodrome_remove_liquidity_start', 'Removing liquidity from Aerodrome pool', {
      tokenA: params.tokenA,
      tokenB: params.tokenB,
      stable: params.stable,
      liquidity: params.liquidity.toString(),
    });

    // Approve LP token for the router
    const poolAddress = await this.getPoolAddress(params.tokenA, params.tokenB, params.stable);
    await this.ensurePoolAllowance(poolAddress, params.liquidity, sender);

    const callData = encodeFunctionData({
      abi: AERODROME_ROUTER_ABI,
      functionName: 'removeLiquidity',
      args: [
        params.tokenA,
        params.tokenB,
        params.stable,
        params.liquidity,
        params.amountAMin ?? 0n,
        params.amountBMin ?? 0n,
        recipient,
        deadline,
      ],
    });

    const gasEstimate = await this.estimateL2Gas(this.routerAddress, callData);
    const txHash = await this.sendTransaction(this.routerAddress, callData, gasEstimate);

    const receipt = await this.publicClient.waitForTransactionReceipt({
      hash: txHash,
      timeout: 120_000,
    });

    this.log('aerodrome_remove_liquidity_complete', 'Liquidity removed', {
      txHash,
      gasUsed: receipt.gasUsed.toString(),
      gasEstimate: gasEstimate.toString(),
    });

    return { txHash, gasUsed: receipt.gasUsed, gasEstimate };
  }

  // ── Fee Collection ──────────────────────────────

  /** Collect trading fees from a pool position. */
  async collectFees(poolAddress: Address): Promise<AerodromeResult> {
    this.log('aerodrome_collect_fees_start', 'Collecting fees from pool', {
      poolAddress,
    });

    const callData = encodeFunctionData({
      abi: AERODROME_POOL_ABI,
      functionName: 'claimFees',
    });

    const gasEstimate = await this.estimateL2Gas(poolAddress, callData);
    const txHash = await this.sendTransaction(poolAddress, callData, gasEstimate);

    const receipt = await this.publicClient.waitForTransactionReceipt({
      hash: txHash,
      timeout: 120_000,
    });

    this.log('aerodrome_collect_fees_complete', 'Fees collected', {
      txHash,
      poolAddress,
      gasUsed: receipt.gasUsed.toString(),
      gasEstimate: gasEstimate.toString(),
    });

    return { txHash, gasUsed: receipt.gasUsed, gasEstimate };
  }

  /** Collect gauge rewards for staked LP tokens. */
  async collectRewards(gaugeAddress: Address): Promise<AerodromeResult> {
    const sender = this.getSenderAddress();

    this.log('aerodrome_collect_rewards_start', 'Collecting gauge rewards', {
      gaugeAddress,
    });

    const callData = encodeFunctionData({
      abi: AERODROME_GAUGE_ABI,
      functionName: 'getReward',
      args: [sender],
    });

    const gasEstimate = await this.estimateL2Gas(gaugeAddress, callData);
    const txHash = await this.sendTransaction(gaugeAddress, callData, gasEstimate);

    const receipt = await this.publicClient.waitForTransactionReceipt({
      hash: txHash,
      timeout: 120_000,
    });

    this.log('aerodrome_collect_rewards_complete', 'Rewards collected', {
      txHash,
      gaugeAddress,
      gasUsed: receipt.gasUsed.toString(),
      gasEstimate: gasEstimate.toString(),
    });

    return { txHash, gasUsed: receipt.gasUsed, gasEstimate };
  }

  // ── Queries ──────────────────────────────────────

  /** Query pool info including reserves. */
  async getPoolInfo(poolAddress: Address): Promise<PoolInfo> {
    const [reserves, token0, token1, stable, totalSupply] = await Promise.all([
      this.publicClient.readContract({
        address: poolAddress,
        abi: AERODROME_POOL_ABI,
        functionName: 'getReserves',
      }) as Promise<readonly [bigint, bigint, bigint]>,
      this.publicClient.readContract({
        address: poolAddress,
        abi: AERODROME_POOL_ABI,
        functionName: 'token0',
      }) as Promise<Address>,
      this.publicClient.readContract({
        address: poolAddress,
        abi: AERODROME_POOL_ABI,
        functionName: 'token1',
      }) as Promise<Address>,
      this.publicClient.readContract({
        address: poolAddress,
        abi: AERODROME_POOL_ABI,
        functionName: 'stable',
      }) as Promise<boolean>,
      this.publicClient.readContract({
        address: poolAddress,
        abi: AERODROME_POOL_ABI,
        functionName: 'totalSupply',
      }) as Promise<bigint>,
    ]);

    const poolInfo: PoolInfo = {
      poolAddress,
      token0,
      token1,
      stable,
      reserve0: reserves[0],
      reserve1: reserves[1],
      totalSupply,
    };

    this.log('aerodrome_pool_info', 'Pool info queried', {
      poolAddress,
      token0,
      token1,
      stable,
      reserve0: reserves[0].toString(),
      reserve1: reserves[1].toString(),
      totalSupply: totalSupply.toString(),
    });

    return poolInfo;
  }

  /** Query pool APY from gauge rewards. */
  async getPoolAPY(gaugeAddress: Address, poolAddress: Address): Promise<PoolAPY> {
    const [rewardRate, totalStaked] = await Promise.all([
      this.publicClient.readContract({
        address: gaugeAddress,
        abi: AERODROME_GAUGE_ABI,
        functionName: 'rewardRate',
      }) as Promise<bigint>,
      this.publicClient.readContract({
        address: gaugeAddress,
        abi: AERODROME_GAUGE_ABI,
        functionName: 'totalSupply',
      }) as Promise<bigint>,
    ]);

    // Estimate APY: (rewardRate * seconds_per_year / totalStaked) * 100
    const secondsPerYear = 365n * 24n * 60n * 60n;
    let estimatedAPY = 0;
    if (totalStaked > 0n) {
      const annualRewards = rewardRate * secondsPerYear;
      estimatedAPY = Number(annualRewards * 100n) / Number(totalStaked);
    }

    this.log('aerodrome_pool_apy', 'Pool APY queried', {
      gaugeAddress,
      poolAddress,
      rewardRate: rewardRate.toString(),
      totalStaked: totalStaked.toString(),
      estimatedAPY,
    });

    return { poolAddress, rewardRate, totalStaked, estimatedAPY };
  }

  /** Query earned rewards for an account at a gauge. */
  async getEarnedRewards(gaugeAddress: Address, account?: Address): Promise<bigint> {
    const addr = account ?? this.getSenderAddress();
    return await this.publicClient.readContract({
      address: gaugeAddress,
      abi: AERODROME_GAUGE_ABI,
      functionName: 'earned',
      args: [addr],
    }) as bigint;
  }

  /** Get pool address for a token pair. */
  async getPoolAddress(tokenA: Address, tokenB: Address, stable: boolean): Promise<Address> {
    return await this.publicClient.readContract({
      address: this.routerAddress,
      abi: AERODROME_ROUTER_ABI,
      functionName: 'poolFor',
      args: [tokenA, tokenB, stable],
    }) as Address;
  }

  // ── Encoding Helpers ──────────────────────────────

  /** Encode an addLiquidity call for external use. */
  encodeAddLiquidity(params: AddLiquidityParams): Hex {
    const deadline = params.deadline ?? BigInt(Math.floor(Date.now() / 1000) + 1800);
    return encodeFunctionData({
      abi: AERODROME_ROUTER_ABI,
      functionName: 'addLiquidity',
      args: [
        params.tokenA,
        params.tokenB,
        params.stable,
        params.amountADesired,
        params.amountBDesired,
        params.amountAMin ?? 0n,
        params.amountBMin ?? 0n,
        params.recipient ?? '0x0000000000000000000000000000000000000000' as Address,
        deadline,
      ],
    });
  }

  // ── Helpers ──────────────────────────────────────

  /** Ensure ERC-20 allowance for the Router. */
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
      args: [owner, this.routerAddress],
    }) as bigint;

    if (currentAllowance >= amount) {
      return;
    }

    const approveData = encodeFunctionData({
      abi: ERC20_ABI,
      functionName: 'approve',
      args: [this.routerAddress, amount],
    });

    const gasEstimate = await this.estimateL2Gas(token, approveData);
    const txHash = await this.sendTransaction(token, approveData, gasEstimate);

    await this.publicClient.waitForTransactionReceipt({
      hash: txHash,
      timeout: 60_000,
    });
  }

  /** Ensure LP token allowance for the Router. */
  private async ensurePoolAllowance(
    poolAddress: Address,
    amount: bigint,
    owner: Address,
  ): Promise<void> {
    const currentAllowance = await this.publicClient.readContract({
      address: poolAddress,
      abi: ERC20_ABI,
      functionName: 'allowance',
      args: [owner, this.routerAddress],
    }) as bigint;

    if (currentAllowance >= amount) {
      return;
    }

    const approveData = encodeFunctionData({
      abi: ERC20_ABI,
      functionName: 'approve',
      args: [this.routerAddress, amount],
    });

    const gasEstimate = await this.estimateL2Gas(poolAddress, approveData);
    const txHash = await this.sendTransaction(poolAddress, approveData, gasEstimate);

    await this.publicClient.waitForTransactionReceipt({
      hash: txHash,
      timeout: 60_000,
    });
  }

  /**
   * Estimate gas for an L2 transaction.
   * Accounts for L1 data posting costs on Base.
   */
  private async estimateL2Gas(to: Address, data: Hex, value?: bigint): Promise<bigint> {
    try {
      const estimate = await this.publicClient.estimateGas({
        to,
        data,
        value,
        account: this.getSenderAddress() as `0x${string}`,
      });
      // 10% buffer + L1 data posting cost for Base (OP Stack)
      // Base L1 data cost: ~16 gas per non-zero calldata byte
      const l1DataCost = BigInt(data.length / 2) * 16n;
      return ((estimate + l1DataCost) * 110n) / 100n;
    } catch {
      return 500_000n; // Default for Aerodrome operations on Base
    }
  }

  /** Send a transaction via wallet client or smart wallet. */
  private async sendTransaction(
    to: Address,
    data: Hex,
    gas: bigint,
    value?: bigint,
  ): Promise<`0x${string}`> {
    if (this.smartWallet) {
      const userOp = await this.smartWallet.buildUserOp({
        target: to,
        value: value ?? 0n,
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
      value,
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
}
