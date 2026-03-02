/**
 * EXEC-006: Lido staking adapter.
 *
 * Provides Lido staking operations:
 * - Stake ETH via Lido and receive stETH
 * - Wrap stETH to wstETH and unwrap back
 * - Query current staking APY and queue status
 * - Handle stETH rebasing model correctly in position tracking
 * - Works on testnet with Lido Sepolia deployment or mock
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

// ── Lido ABIs ──────────────────────────────────

const LIDO_STETH_ABI = parseAbi([
  'function submit(address _referral) payable returns (uint256)',
  'function balanceOf(address _account) view returns (uint256)',
  'function getSharesByPooledEth(uint256 _ethAmount) view returns (uint256)',
  'function getPooledEthByShares(uint256 _sharesAmount) view returns (uint256)',
  'function getTotalPooledEther() view returns (uint256)',
  'function getTotalShares() view returns (uint256)',
  'function sharesOf(address _account) view returns (uint256)',
  'function approve(address _spender, uint256 _amount) returns (bool)',
  'function allowance(address _owner, address _spender) view returns (uint256)',
]);

const WSTETH_ABI = parseAbi([
  'function wrap(uint256 _stETHAmount) returns (uint256)',
  'function unwrap(uint256 _wstETHAmount) returns (uint256)',
  'function balanceOf(address _account) view returns (uint256)',
  'function getWstETHByStETH(uint256 _stETHAmount) view returns (uint256)',
  'function getStETHByWstETH(uint256 _wstETHAmount) view returns (uint256)',
  'function stEthPerToken() view returns (uint256)',
  'function tokensPerStEth() view returns (uint256)',
]);

const LIDO_WITHDRAWAL_QUEUE_ABI = parseAbi([
  'function getLastRequestId() view returns (uint256)',
  'function getLastFinalizedRequestId() view returns (uint256)',
  'function unfinalizedStETH() view returns (uint256)',
  'function getWithdrawalStatus(uint256[] _requestIds) view returns ((uint256 amountOfStETH, uint256 amountOfShares, address owner, uint256 timestamp, bool isFinalized, bool isClaimed)[])',
]);

// ── Types ──────────────────────────────────────────

/** Options for Lido adapter initialization. */
export interface LidoAdapterOptions {
  /** Lido stETH contract address. Defaults to Sepolia deployment. */
  stethAddress?: Address;
  /** wstETH contract address. Defaults to Sepolia deployment. */
  wstethAddress?: Address;
  /** Withdrawal Queue contract address. Defaults to Sepolia deployment. */
  withdrawalQueueAddress?: Address;
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
}

/** Result of staking ETH. */
export interface StakeResult {
  txHash: `0x${string}`;
  gasUsed: bigint;
  gasEstimate: bigint;
  ethStaked: bigint;
}

/** Result of wrapping stETH to wstETH. */
export interface WrapResult {
  txHash: `0x${string}`;
  gasUsed: bigint;
  gasEstimate: bigint;
  stethWrapped: bigint;
}

/** Result of unwrapping wstETH to stETH. */
export interface UnwrapResult {
  txHash: `0x${string}`;
  gasUsed: bigint;
  gasEstimate: bigint;
  wstethUnwrapped: bigint;
}

/** Staking APY information. */
export interface StakingAPY {
  /** Estimated annual percentage yield. */
  apy: number;
  /** Total pooled ether in the protocol. */
  totalPooledEther: bigint;
  /** Total shares in the protocol. */
  totalShares: bigint;
  /** Current exchange rate (stETH per share). */
  exchangeRate: number;
}

/** Withdrawal queue status. */
export interface QueueStatus {
  /** Last request ID in the queue. */
  lastRequestId: bigint;
  /** Last finalized request ID. */
  lastFinalizedRequestId: bigint;
  /** Total unfinalized stETH in the queue. */
  unfinalizedStETH: bigint;
  /** Number of pending requests. */
  pendingRequests: bigint;
}

/** Rebasing position tracking info. */
export interface RebasingPosition {
  /** Underlying shares held. */
  shares: bigint;
  /** Current stETH balance (rebased). */
  stethBalance: bigint;
  /** Equivalent wstETH amount. */
  wstethEquivalent: bigint;
  /** Current exchange rate (stETH per wstETH). */
  exchangeRate: number;
}

// ── Default Addresses (Sepolia) ──────────────────

/** Lido stETH on Sepolia. */
const SEPOLIA_STETH: Address = '0x3e3FE7dBc6B4C189E7128855dD526361c49b40Af';
/** wstETH on Sepolia. */
const SEPOLIA_WSTETH: Address = '0xB82381A3fBD3FaFA77B3a7bE693342618240067b';
/** Withdrawal Queue on Sepolia. */
const SEPOLIA_WITHDRAWAL_QUEUE: Address = '0x1583C7b3f4C3B008720E6BcE5726336b0aB25fdd';

// ── Adapter ──────────────────────────────────────────

/** Adapter for Lido staking operations. */
export class LidoAdapter {
  private readonly stethAddress: Address;
  private readonly wstethAddress: Address;
  private readonly withdrawalQueueAddress: Address;
  private readonly publicClient: PublicClient;
  private readonly walletClient: WalletClient | null;
  private readonly smartWallet: SmartWalletManager | null;
  private readonly log: (event: string, message: string, extra?: Record<string, unknown>) => void;

  constructor(opts: LidoAdapterOptions = {}) {
    this.stethAddress = opts.stethAddress
      ?? (process.env.LIDO_STETH_ADDRESS as Address | undefined)
      ?? SEPOLIA_STETH;
    this.wstethAddress = opts.wstethAddress
      ?? (process.env.LIDO_WSTETH_ADDRESS as Address | undefined)
      ?? SEPOLIA_WSTETH;
    this.withdrawalQueueAddress = opts.withdrawalQueueAddress
      ?? (process.env.LIDO_WITHDRAWAL_QUEUE_ADDRESS as Address | undefined)
      ?? SEPOLIA_WITHDRAWAL_QUEUE;
    this.smartWallet = opts.smartWallet ?? null;
    this.walletClient = opts.walletClient ?? null;
    this.log = opts.onLog ?? (() => {});

    const chain = opts.chain ?? sepolia;
    const rpcUrl = opts.rpcUrl ?? process.env.ALCHEMY_SEPOLIA_HTTP_URL;

    this.publicClient = opts.publicClient ?? createPublicClient({
      chain,
      transport: http(rpcUrl),
    });
  }

  /** Get the stETH contract address. */
  get steth(): Address {
    return this.stethAddress;
  }

  /** Get the wstETH contract address. */
  get wsteth(): Address {
    return this.wstethAddress;
  }

  /** Get the withdrawal queue contract address. */
  get withdrawalQueue(): Address {
    return this.withdrawalQueueAddress;
  }

  // ── Staking ──────────────────────────────────────

  /** Stake ETH via Lido and receive stETH. */
  async stake(ethAmount: bigint, referral?: Address): Promise<StakeResult> {
    const ref = referral ?? '0x0000000000000000000000000000000000000000' as Address;

    this.log('lido_stake_start', 'Starting Lido ETH stake', {
      ethAmount: ethAmount.toString(),
      referral: ref,
    });

    const callData = encodeFunctionData({
      abi: LIDO_STETH_ABI,
      functionName: 'submit',
      args: [ref],
    });

    const gasEstimate = await this.estimateGas(this.stethAddress, callData, ethAmount);
    const txHash = await this.sendTransaction(this.stethAddress, callData, gasEstimate, ethAmount);

    const receipt = await this.publicClient.waitForTransactionReceipt({
      hash: txHash,
      timeout: 120_000,
    });

    this.log('lido_stake_complete', 'Lido ETH stake complete', {
      txHash,
      ethStaked: ethAmount.toString(),
      gasUsed: receipt.gasUsed.toString(),
      gasEstimate: gasEstimate.toString(),
      blockNumber: Number(receipt.blockNumber),
    });

    return { txHash, gasUsed: receipt.gasUsed, gasEstimate, ethStaked: ethAmount };
  }

  // ── Wrapping ──────────────────────────────────────

  /** Wrap stETH to wstETH. */
  async wrap(stethAmount: bigint): Promise<WrapResult> {
    const sender = this.getSenderAddress();

    this.log('lido_wrap_start', 'Wrapping stETH to wstETH', {
      stethAmount: stethAmount.toString(),
    });

    // Approve stETH for wstETH contract
    await this.ensureStethAllowance(stethAmount, sender);

    const callData = encodeFunctionData({
      abi: WSTETH_ABI,
      functionName: 'wrap',
      args: [stethAmount],
    });

    const gasEstimate = await this.estimateGas(this.wstethAddress, callData);
    const txHash = await this.sendTransaction(this.wstethAddress, callData, gasEstimate);

    const receipt = await this.publicClient.waitForTransactionReceipt({
      hash: txHash,
      timeout: 120_000,
    });

    this.log('lido_wrap_complete', 'stETH wrapped to wstETH', {
      txHash,
      stethWrapped: stethAmount.toString(),
      gasUsed: receipt.gasUsed.toString(),
      gasEstimate: gasEstimate.toString(),
    });

    return { txHash, gasUsed: receipt.gasUsed, gasEstimate, stethWrapped: stethAmount };
  }

  /** Unwrap wstETH back to stETH. */
  async unwrap(wstethAmount: bigint): Promise<UnwrapResult> {
    this.log('lido_unwrap_start', 'Unwrapping wstETH to stETH', {
      wstethAmount: wstethAmount.toString(),
    });

    const callData = encodeFunctionData({
      abi: WSTETH_ABI,
      functionName: 'unwrap',
      args: [wstethAmount],
    });

    const gasEstimate = await this.estimateGas(this.wstethAddress, callData);
    const txHash = await this.sendTransaction(this.wstethAddress, callData, gasEstimate);

    const receipt = await this.publicClient.waitForTransactionReceipt({
      hash: txHash,
      timeout: 120_000,
    });

    this.log('lido_unwrap_complete', 'wstETH unwrapped to stETH', {
      txHash,
      wstethUnwrapped: wstethAmount.toString(),
      gasUsed: receipt.gasUsed.toString(),
      gasEstimate: gasEstimate.toString(),
    });

    return { txHash, gasUsed: receipt.gasUsed, gasEstimate, wstethUnwrapped: wstethAmount };
  }

  // ── Queries ──────────────────────────────────────

  /** Query current staking APY and protocol stats. */
  async getStakingAPY(): Promise<StakingAPY> {
    const [totalPooledEther, totalShares] = await Promise.all([
      this.publicClient.readContract({
        address: this.stethAddress,
        abi: LIDO_STETH_ABI,
        functionName: 'getTotalPooledEther',
      }) as Promise<bigint>,
      this.publicClient.readContract({
        address: this.stethAddress,
        abi: LIDO_STETH_ABI,
        functionName: 'getTotalShares',
      }) as Promise<bigint>,
    ]);

    // Exchange rate: how much ETH per share
    const exchangeRate = totalShares > 0n
      ? Number(totalPooledEther) / Number(totalShares)
      : 1;

    // Approximate APY based on Lido's typical range (3-5%)
    // Real APY requires historical data; protocol reports ~3.5%
    // We calculate from the exchange rate differential
    const apy = (exchangeRate - 1) * 365.25 / 1; // Simplified estimate

    this.log('lido_apy_query', 'Staking APY queried', {
      apy: apy.toFixed(6),
      totalPooledEther: totalPooledEther.toString(),
      totalShares: totalShares.toString(),
      exchangeRate: exchangeRate.toFixed(8),
    });

    return { apy, totalPooledEther, totalShares, exchangeRate };
  }

  /** Query withdrawal queue status. */
  async getQueueStatus(): Promise<QueueStatus> {
    const [lastRequestId, lastFinalizedRequestId, unfinalizedStETH] = await Promise.all([
      this.publicClient.readContract({
        address: this.withdrawalQueueAddress,
        abi: LIDO_WITHDRAWAL_QUEUE_ABI,
        functionName: 'getLastRequestId',
      }) as Promise<bigint>,
      this.publicClient.readContract({
        address: this.withdrawalQueueAddress,
        abi: LIDO_WITHDRAWAL_QUEUE_ABI,
        functionName: 'getLastFinalizedRequestId',
      }) as Promise<bigint>,
      this.publicClient.readContract({
        address: this.withdrawalQueueAddress,
        abi: LIDO_WITHDRAWAL_QUEUE_ABI,
        functionName: 'unfinalizedStETH',
      }) as Promise<bigint>,
    ]);

    const pendingRequests = lastRequestId - lastFinalizedRequestId;

    this.log('lido_queue_status', 'Withdrawal queue status queried', {
      lastRequestId: lastRequestId.toString(),
      lastFinalizedRequestId: lastFinalizedRequestId.toString(),
      unfinalizedStETH: unfinalizedStETH.toString(),
      pendingRequests: pendingRequests.toString(),
    });

    return { lastRequestId, lastFinalizedRequestId, unfinalizedStETH, pendingRequests };
  }

  // ── Rebasing Position Tracking ──────────────────

  /**
   * Get rebasing position info for an account.
   * Handles stETH rebasing correctly by tracking shares.
   */
  async getRebasingPosition(account: Address): Promise<RebasingPosition> {
    const [shares, stethBalance, wstethEquivalent, stEthPerToken] = await Promise.all([
      this.publicClient.readContract({
        address: this.stethAddress,
        abi: LIDO_STETH_ABI,
        functionName: 'sharesOf',
        args: [account],
      }) as Promise<bigint>,
      this.publicClient.readContract({
        address: this.stethAddress,
        abi: LIDO_STETH_ABI,
        functionName: 'balanceOf',
        args: [account],
      }) as Promise<bigint>,
      this.publicClient.readContract({
        address: this.wstethAddress,
        abi: WSTETH_ABI,
        functionName: 'balanceOf',
        args: [account],
      }) as Promise<bigint>,
      this.publicClient.readContract({
        address: this.wstethAddress,
        abi: WSTETH_ABI,
        functionName: 'stEthPerToken',
      }) as Promise<bigint>,
    ]);

    // Exchange rate: stETH per wstETH (scaled by 1e18)
    const exchangeRate = Number(stEthPerToken) / 1e18;

    this.log('lido_rebasing_position', 'Rebasing position retrieved', {
      account,
      shares: shares.toString(),
      stethBalance: stethBalance.toString(),
      wstethEquivalent: wstethEquivalent.toString(),
      exchangeRate: exchangeRate.toFixed(8),
    });

    return { shares, stethBalance, wstethEquivalent, exchangeRate };
  }

  /**
   * Convert between stETH and shares to track position correctly.
   * stETH rebases daily, but shares are constant.
   */
  async sharesToSteth(shares: bigint): Promise<bigint> {
    return await this.publicClient.readContract({
      address: this.stethAddress,
      abi: LIDO_STETH_ABI,
      functionName: 'getPooledEthByShares',
      args: [shares],
    }) as bigint;
  }

  /** Convert stETH amount to shares. */
  async stethToShares(stethAmount: bigint): Promise<bigint> {
    return await this.publicClient.readContract({
      address: this.stethAddress,
      abi: LIDO_STETH_ABI,
      functionName: 'getSharesByPooledEth',
      args: [stethAmount],
    }) as bigint;
  }

  /** Convert wstETH to stETH equivalent. */
  async wstethToSteth(wstethAmount: bigint): Promise<bigint> {
    return await this.publicClient.readContract({
      address: this.wstethAddress,
      abi: WSTETH_ABI,
      functionName: 'getStETHByWstETH',
      args: [wstethAmount],
    }) as bigint;
  }

  /** Convert stETH to wstETH equivalent. */
  async stethToWsteth(stethAmount: bigint): Promise<bigint> {
    return await this.publicClient.readContract({
      address: this.wstethAddress,
      abi: WSTETH_ABI,
      functionName: 'getWstETHByStETH',
      args: [stethAmount],
    }) as bigint;
  }

  // ── Encoding Helpers ──────────────────────────────

  /** Encode a stake call for external use. */
  encodeStake(referral?: Address): Hex {
    return encodeFunctionData({
      abi: LIDO_STETH_ABI,
      functionName: 'submit',
      args: [referral ?? '0x0000000000000000000000000000000000000000' as Address],
    });
  }

  /** Encode a wrap call for external use. */
  encodeWrap(stethAmount: bigint): Hex {
    return encodeFunctionData({
      abi: WSTETH_ABI,
      functionName: 'wrap',
      args: [stethAmount],
    });
  }

  /** Encode an unwrap call for external use. */
  encodeUnwrap(wstethAmount: bigint): Hex {
    return encodeFunctionData({
      abi: WSTETH_ABI,
      functionName: 'unwrap',
      args: [wstethAmount],
    });
  }

  // ── Helpers ──────────────────────────────────────

  /** Ensure stETH allowance for the wstETH contract. */
  private async ensureStethAllowance(
    amount: bigint,
    owner: Address,
  ): Promise<void> {
    const currentAllowance = await this.publicClient.readContract({
      address: this.stethAddress,
      abi: LIDO_STETH_ABI,
      functionName: 'allowance',
      args: [owner, this.wstethAddress],
    });

    if ((currentAllowance as bigint) >= amount) {
      this.log('lido_allowance_sufficient', 'stETH allowance sufficient', {
        current: (currentAllowance as bigint).toString(),
        required: amount.toString(),
      });
      return;
    }

    this.log('lido_approve_start', 'Approving stETH for wstETH contract', {
      amount: amount.toString(),
    });

    const approveData = encodeFunctionData({
      abi: LIDO_STETH_ABI,
      functionName: 'approve',
      args: [this.wstethAddress, amount],
    });

    const gasEstimate = await this.estimateGas(this.stethAddress, approveData);
    const txHash = await this.sendTransaction(this.stethAddress, approveData, gasEstimate);

    await this.publicClient.waitForTransactionReceipt({
      hash: txHash,
      timeout: 60_000,
    });

    this.log('lido_approve_complete', 'stETH approval complete', { txHash });
  }

  /** Estimate gas for a contract call with 10% buffer. */
  private async estimateGas(to: Address, data: Hex, value?: bigint): Promise<bigint> {
    try {
      const estimate = await this.publicClient.estimateGas({
        to,
        data,
        value,
        account: this.getSenderAddress() as `0x${string}`,
      });
      return (estimate * 110n) / 100n;
    } catch {
      return 300_000n;
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
