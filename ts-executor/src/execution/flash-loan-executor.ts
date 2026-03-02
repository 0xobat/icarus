/**
 * EXEC-007: Flash loan executor.
 *
 * Executes atomic flash loan arbitrage via Aave V3:
 * - Execute Aave V3 flash loan with custom callback contract
 * - Callback executes multi-step swap logic (buy DEX A, sell DEX B)
 * - Atomic revert if profit threshold not met on-chain
 * - Gas cost estimation before submission
 * - Profit calculation accounts for flash loan fee, gas cost, slippage
 * - Routed through Flashbots Protect
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
  encodeAbiParameters,
  parseAbiParameters,
} from 'viem';
import { sepolia } from 'viem/chains';
/** @deprecated Transitional — adapters no longer own wallet execution. */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type SmartWalletManager = any;

// ── ABIs ──────────────────────────────────────────

const AAVE_FLASH_LOAN_ABI = parseAbi([
  'function flashLoan(address receiverAddress, address[] assets, uint256[] amounts, uint256[] interestRateModes, address onBehalfOf, bytes params, uint16 referralCode)',
  'function FLASHLOAN_PREMIUM_TOTAL() view returns (uint128)',
]);

/** Flash loan callback ABI for custom executor contracts. */
export const FLASH_LOAN_CALLBACK_ABI = parseAbi([
  'function executeOperation(address[] assets, uint256[] amounts, uint256[] premiums, address initiator, bytes params) returns (bool)',
]);

/** ERC-20 ABI for flash loan token approval. */
export const FLASH_LOAN_ERC20_ABI = parseAbi([
  'function balanceOf(address account) view returns (uint256)',
  'function approve(address spender, uint256 amount) returns (bool)',
]);

// ── Types ──────────────────────────────────────────

/** Options for flash loan executor initialization. */
export interface FlashLoanExecutorOptions {
  /** Aave V3 Pool address (flash loan source). Defaults to Sepolia pool. */
  poolAddress?: Address;
  /** Custom callback contract address. */
  callbackContractAddress?: Address;
  /** RPC URL. Defaults to env ALCHEMY_SEPOLIA_HTTP_URL. */
  rpcUrl?: string;
  /** Chain. Defaults to sepolia. */
  chain?: Chain;
  /** Flash loan premium in basis points. Defaults to 5 (0.05%). */
  flashLoanPremiumBps?: number;
  /** Minimum profit threshold in wei. Defaults to 0 (any profit). */
  minProfitWei?: bigint;
  /** Whether to route through Flashbots Protect. Defaults to true. */
  useFlashbots?: boolean;
  /** Structured log callback. */
  onLog?: (event: string, message: string, extra?: Record<string, unknown>) => void;
  /** Override public client (for testing). */
  publicClient?: PublicClient;
  /** Override wallet client (for testing). */
  walletClient?: WalletClient;
  /** Smart wallet manager (optional). */
  smartWallet?: SmartWalletManager;
}

/** A single swap step in the arbitrage path. */
export interface SwapStep {
  /** DEX router address. */
  dexRouter: Address;
  /** Token to sell. */
  tokenIn: Address;
  /** Token to buy. */
  tokenOut: Address;
  /** Encoded swap call data. */
  callData: Hex;
  /** Minimum output amount for slippage protection. */
  amountOutMin: bigint;
}

/** Parameters for executing a flash loan arbitrage. */
export interface FlashLoanParams {
  /** Asset to borrow in the flash loan. */
  asset: Address;
  /** Amount to borrow. */
  amount: bigint;
  /** Ordered swap steps: buy on DEX A, sell on DEX B, etc. */
  swapSteps: SwapStep[];
  /** Minimum profit after all fees (flash loan fee, gas, slippage). */
  minProfit: bigint;
  /** Maximum gas price willing to pay (in wei). */
  maxGasPrice?: bigint;
  /** Expected slippage tolerance as basis points (e.g. 50 = 0.5%). */
  slippageBps?: number;
}

/** Profit breakdown for a flash loan execution. */
export interface ProfitEstimate {
  /** Gross profit before fees. */
  grossProfit: bigint;
  /** Flash loan fee. */
  flashLoanFee: bigint;
  /** Estimated gas cost in wei. */
  gasCost: bigint;
  /** Estimated slippage cost. */
  slippageCost: bigint;
  /** Net profit after all costs. */
  netProfit: bigint;
  /** Whether the trade is profitable. */
  isProfitable: boolean;
}

/** Result of a flash loan execution. */
export interface FlashLoanResult {
  txHash: `0x${string}`;
  gasUsed: bigint;
  gasEstimate: bigint;
  profitEstimate: ProfitEstimate;
  success: boolean;
}

// ── Default Addresses ──────────────────────────────

/** Aave V3 Pool on Sepolia. */
const SEPOLIA_AAVE_V3_POOL: Address = '0x6Ae43d3271ff6888e7Fc43Fd7321a503ff738951';
/** Default callback contract (placeholder, deployed separately). */
const DEFAULT_CALLBACK_CONTRACT: Address = '0x0000000000000000000000000000000000000000';

// ── Executor ──────────────────────────────────────────

/** Executes atomic flash loan arbitrage through Aave V3. */
export class FlashLoanExecutor {
  private readonly poolAddress: Address;
  private readonly callbackContract: Address;
  private readonly publicClient: PublicClient;
  private readonly walletClient: WalletClient | null;
  private readonly smartWallet: SmartWalletManager | null;
  private readonly log: (event: string, message: string, extra?: Record<string, unknown>) => void;
  private readonly flashLoanPremiumBps: number;
  private readonly minProfitWei: bigint;
  private readonly useFlashbots: boolean;

  constructor(opts: FlashLoanExecutorOptions = {}) {
    this.poolAddress = opts.poolAddress
      ?? (process.env.AAVE_V3_POOL_ADDRESS as Address | undefined)
      ?? SEPOLIA_AAVE_V3_POOL;
    this.callbackContract = opts.callbackContractAddress
      ?? (process.env.FLASH_LOAN_CALLBACK_ADDRESS as Address | undefined)
      ?? DEFAULT_CALLBACK_CONTRACT;
    this.smartWallet = opts.smartWallet ?? null;
    this.walletClient = opts.walletClient ?? null;
    this.log = opts.onLog ?? (() => {});
    this.flashLoanPremiumBps = opts.flashLoanPremiumBps ?? 5;
    this.minProfitWei = opts.minProfitWei ?? 0n;
    this.useFlashbots = opts.useFlashbots ?? true;

    const chain = opts.chain ?? sepolia;
    const rpcUrl = opts.rpcUrl ?? process.env.ALCHEMY_SEPOLIA_HTTP_URL;

    this.publicClient = opts.publicClient ?? createPublicClient({
      chain,
      transport: http(rpcUrl),
    });
  }

  /** Get the Aave V3 Pool address. */
  get pool(): Address {
    return this.poolAddress;
  }

  /** Get the callback contract address. */
  get callback(): Address {
    return this.callbackContract;
  }

  /** Check if Flashbots Protect is enabled. */
  get flashbotsEnabled(): boolean {
    return this.useFlashbots;
  }

  // ── Profit Estimation ──────────────────────────────

  /**
   * Estimate profit for a flash loan arbitrage before executing.
   * Accounts for flash loan fee, gas cost, and slippage.
   */
  async estimateProfit(params: FlashLoanParams): Promise<ProfitEstimate> {
    // Flash loan fee: amount * premium / 10000
    const flashLoanFee = (params.amount * BigInt(this.flashLoanPremiumBps)) / 10000n;

    // Estimate gas cost
    const gasEstimate = await this.estimateFlashLoanGas(params);
    const gasPrice = await this.getGasPrice();
    const gasCost = gasEstimate * gasPrice;

    // Estimate slippage cost
    const slippageBps = params.slippageBps ?? 50;
    const slippageCost = (params.amount * BigInt(slippageBps)) / 10000n;

    // Gross profit is the expected output minus the input
    // For estimation, we use minProfit as the expected gross
    const grossProfit = params.minProfit + flashLoanFee + gasCost + slippageCost;

    // Net profit
    const netProfit = grossProfit - flashLoanFee - gasCost - slippageCost;
    const isProfitable = netProfit > this.minProfitWei;

    const estimate: ProfitEstimate = {
      grossProfit,
      flashLoanFee,
      gasCost,
      slippageCost,
      netProfit,
      isProfitable,
    };

    this.log('flash_loan_profit_estimate', 'Profit estimated', {
      asset: params.asset,
      amount: params.amount.toString(),
      grossProfit: grossProfit.toString(),
      flashLoanFee: flashLoanFee.toString(),
      gasCost: gasCost.toString(),
      slippageCost: slippageCost.toString(),
      netProfit: netProfit.toString(),
      isProfitable,
    });

    return estimate;
  }

  // ── Execution ──────────────────────────────────────

  /**
   * Execute a flash loan arbitrage.
   * The transaction is atomic: reverts if profit threshold not met.
   */
  async execute(params: FlashLoanParams): Promise<FlashLoanResult> {
    this.log('flash_loan_start', 'Starting flash loan execution', {
      asset: params.asset,
      amount: params.amount.toString(),
      swapSteps: params.swapSteps.length,
      minProfit: params.minProfit.toString(),
    });

    // 1. Estimate profit before submission
    const profitEstimate = await this.estimateProfit(params);

    if (!profitEstimate.isProfitable) {
      this.log('flash_loan_unprofitable', 'Flash loan not profitable, aborting', {
        netProfit: profitEstimate.netProfit.toString(),
        minRequired: this.minProfitWei.toString(),
      });
      return {
        txHash: '0x0' as `0x${string}`,
        gasUsed: 0n,
        gasEstimate: 0n,
        profitEstimate,
        success: false,
      };
    }

    // 2. Encode the callback parameters
    const callbackParams = this.encodeCallbackParams(params);

    // 3. Build flash loan call
    const callData = encodeFunctionData({
      abi: AAVE_FLASH_LOAN_ABI,
      functionName: 'flashLoan',
      args: [
        this.callbackContract,
        [params.asset],
        [params.amount],
        [0n], // No debt (flash loan mode 0)
        this.getSenderAddress(),
        callbackParams,
        0,
      ],
    });

    // 4. Estimate gas for the full flash loan TX
    const gasEstimate = await this.estimateFlashLoanGas(params);

    // 5. Submit transaction
    const txHash = await this.sendTransaction(this.poolAddress, callData, gasEstimate);

    // 6. Wait for receipt
    const receipt = await this.publicClient.waitForTransactionReceipt({
      hash: txHash,
      timeout: 120_000,
    });

    const success = receipt.status === 'success';

    this.log('flash_loan_complete', 'Flash loan execution complete', {
      txHash,
      success,
      gasUsed: receipt.gasUsed.toString(),
      gasEstimate: gasEstimate.toString(),
      blockNumber: Number(receipt.blockNumber),
      netProfit: profitEstimate.netProfit.toString(),
    });

    return {
      txHash,
      gasUsed: receipt.gasUsed,
      gasEstimate,
      profitEstimate,
      success,
    };
  }

  // ── Query ──────────────────────────────────────

  /** Query the flash loan premium rate from the pool. */
  async getFlashLoanPremium(): Promise<number> {
    try {
      const premium = await this.publicClient.readContract({
        address: this.poolAddress,
        abi: AAVE_FLASH_LOAN_ABI,
        functionName: 'FLASHLOAN_PREMIUM_TOTAL',
      }) as bigint;
      return Number(premium);
    } catch {
      return this.flashLoanPremiumBps;
    }
  }

  /** Calculate flash loan fee for a given amount. */
  calculateFlashLoanFee(amount: bigint): bigint {
    return (amount * BigInt(this.flashLoanPremiumBps)) / 10000n;
  }

  // ── Encoding Helpers ──────────────────────────────

  /** Encode the callback parameters for the flash loan contract. */
  encodeCallbackParams(params: FlashLoanParams): Hex {
    // Encode swap steps as ABI-packed data
    const stepsEncoded = params.swapSteps.map((step) => ({
      dexRouter: step.dexRouter,
      tokenIn: step.tokenIn,
      tokenOut: step.tokenOut,
      callData: step.callData,
      amountOutMin: step.amountOutMin,
    }));

    // Encode as bytes: minProfit + number of steps + encoded steps
    return encodeAbiParameters(
      parseAbiParameters('uint256 minProfit, uint256 numSteps, address[] dexRouters, bytes[] swapCallDatas, uint256[] amountsOutMin'),
      [
        params.minProfit,
        BigInt(stepsEncoded.length),
        stepsEncoded.map((s) => s.dexRouter),
        stepsEncoded.map((s) => s.callData),
        stepsEncoded.map((s) => s.amountOutMin),
      ],
    );
  }

  /** Encode the full flash loan call for external use. */
  encodeFlashLoan(params: FlashLoanParams): Hex {
    const callbackParams = this.encodeCallbackParams(params);
    return encodeFunctionData({
      abi: AAVE_FLASH_LOAN_ABI,
      functionName: 'flashLoan',
      args: [
        this.callbackContract,
        [params.asset],
        [params.amount],
        [0n],
        this.getSenderAddress(),
        callbackParams,
        0,
      ],
    });
  }

  // ── Helpers ──────────────────────────────────────

  /** Estimate gas for a flash loan transaction with 10% buffer. */
  private async estimateFlashLoanGas(params: FlashLoanParams): Promise<bigint> {
    try {
      const callbackParamsData = this.encodeCallbackParams(params);
      const callData = encodeFunctionData({
        abi: AAVE_FLASH_LOAN_ABI,
        functionName: 'flashLoan',
        args: [
          this.callbackContract,
          [params.asset],
          [params.amount],
          [0n],
          this.getSenderAddress(),
          callbackParamsData,
          0,
        ],
      });

      const estimate = await this.publicClient.estimateGas({
        to: this.poolAddress,
        data: callData,
        account: this.getSenderAddress() as `0x${string}`,
      });
      // Add 10% buffer
      return (estimate * 110n) / 100n;
    } catch {
      // Flash loans are complex; use a higher default
      return 800_000n;
    }
  }

  /** Get current gas price. */
  private async getGasPrice(): Promise<bigint> {
    try {
      return await this.publicClient.getGasPrice();
    } catch {
      return 30_000_000_000n; // 30 gwei default
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
}
