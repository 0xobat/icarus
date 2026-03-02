/**
 * EXEC-009 (Part 1): GMX V2 protocol adapter for Arbitrum.
 *
 * Provides GMX perpetual trading operations:
 * - Open and close leveraged positions (long/short)
 * - Query funding rates for markets
 * - Manage margin (add/remove collateral)
 * - Same order schema and result reporting as Ethereum adapters
 * - L2 gas estimation accounts for L1 data posting costs
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
import { arbitrum } from 'viem/chains';
/** @deprecated Transitional — adapters no longer own wallet execution. */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type SmartWalletManager = any;

// ── GMX V2 ABIs ──────────────────────────────────

const GMX_EXCHANGE_ROUTER_ABI = parseAbi([
  'function createOrder((address[] addresses, uint256[] numbers, bytes32 orderType, bytes32 decreasePositionSwapType, bool isLong, bool shouldUnwrapNativeToken, bytes32 referralCode)) payable returns (bytes32)',
  'function cancelOrder(bytes32 key)',
  'function updateOrder(bytes32 key, uint256 sizeDeltaUsd, uint256 acceptablePrice, uint256 triggerPrice, uint256 minOutputAmount)',
  'function sendWnt(address receiver, uint256 amount) payable',
  'function sendTokens(address token, address receiver, uint256 amount)',
  'function multicall(bytes[] data) payable returns (bytes[])',
]);

const GMX_READER_ABI = parseAbi([
  'function getMarket(address dataStore, address marketToken) view returns ((address marketToken, address indexToken, address longToken, address shortToken))',
  'function getPosition(address dataStore, bytes32 positionKey) view returns ((address account, address market, address collateralToken, bool isLong, uint256 sizeInUsd, uint256 sizeInTokens, uint256 collateralAmount, uint256 borrowingFactor, uint256 fundingFeeAmountPerSize, uint256 longTokenClaimableFundingAmountPerSize, uint256 shortTokenClaimableFundingAmountPerSize, uint256 increasedAtBlock, uint256 decreasedAtBlock))',
  'function getMarketTokenPrice(address dataStore, (address marketToken, address indexToken, address longToken, address shortToken) market, (uint256 min, uint256 max) indexTokenPrice, (uint256 min, uint256 max) longTokenPrice, (uint256 min, uint256 max) shortTokenPrice, bytes32 pnlFactorType, bool maximize) view returns (int256, (uint256 poolValue, int256 longPnl, int256 shortPnl, int256 netPnl, uint256 longTokenAmount, uint256 shortTokenAmount, uint256 longTokenUsd, uint256 shortTokenUsd, uint256 totalBorrowingFees, uint256 borrowingFeePoolFactor, uint256 impactPoolAmount))',
]);

const GMX_DATA_STORE_ABI = parseAbi([
  'function getUint(bytes32 key) view returns (uint256)',
  'function getInt(bytes32 key) view returns (int256)',
]);

const ERC20_ABI = parseAbi([
  'function approve(address spender, uint256 amount) returns (bool)',
  'function allowance(address owner, address spender) view returns (uint256)',
  'function balanceOf(address account) view returns (uint256)',
]);

// ── Types ──────────────────────────────────────────

/** Options for GMX adapter initialization. */
export interface GmxAdapterOptions {
  /** GMX ExchangeRouter address. Defaults to Arbitrum deployment. */
  exchangeRouterAddress?: Address;
  /** GMX Reader address. Defaults to Arbitrum deployment. */
  readerAddress?: Address;
  /** GMX DataStore address. Defaults to Arbitrum deployment. */
  dataStoreAddress?: Address;
  /** RPC URL. Defaults to env ALCHEMY_ARBITRUM_HTTP_URL. */
  rpcUrl?: string;
  /** Chain. Defaults to Arbitrum. */
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

/** Parameters for opening a position. */
export interface OpenPositionParams {
  /** Market token address. */
  market: Address;
  /** Collateral token address. */
  collateralToken: Address;
  /** Index token address (the asset being traded). */
  indexToken: Address;
  /** Whether this is a long position. */
  isLong: boolean;
  /** Size delta in USD (scaled by 1e30). */
  sizeDeltaUsd: bigint;
  /** Collateral amount to deposit. */
  collateralAmount: bigint;
  /** Acceptable price for the position (scaled by 1e30). */
  acceptablePrice: bigint;
  /** Execution fee in native token (ETH on Arbitrum). */
  executionFee: bigint;
}

/** Parameters for closing a position. */
export interface ClosePositionParams {
  /** Market token address. */
  market: Address;
  /** Collateral token address. */
  collateralToken: Address;
  /** Index token address. */
  indexToken: Address;
  /** Whether this is a long position. */
  isLong: boolean;
  /** Size delta in USD to decrease (scaled by 1e30). */
  sizeDeltaUsd: bigint;
  /** Acceptable price for closing. */
  acceptablePrice: bigint;
  /** Execution fee in native token. */
  executionFee: bigint;
}

/** Parameters for adjusting margin. */
export interface AdjustMarginParams {
  /** Market token address. */
  market: Address;
  /** Collateral token address. */
  collateralToken: Address;
  /** Index token address. */
  indexToken: Address;
  /** Whether this is a long position. */
  isLong: boolean;
  /** Collateral delta (positive = add, negative = remove). */
  collateralDelta: bigint;
  /** Execution fee in native token. */
  executionFee: bigint;
}

/** Result of a GMX operation. */
export interface GmxResult {
  txHash: `0x${string}`;
  gasUsed: bigint;
  gasEstimate: bigint;
  orderKey?: string;
}

/** Position information. */
export interface GmxPosition {
  account: Address;
  market: Address;
  collateralToken: Address;
  isLong: boolean;
  sizeInUsd: bigint;
  sizeInTokens: bigint;
  collateralAmount: bigint;
  borrowingFactor: bigint;
}

/** Funding rate information. */
export interface FundingRateInfo {
  longFundingRate: bigint;
  shortFundingRate: bigint;
  market: Address;
}

// ── Default Addresses (Arbitrum) ──────────────────

/** GMX V2 ExchangeRouter on Arbitrum. */
const ARBITRUM_EXCHANGE_ROUTER: Address = '0x7C68C7866A64FA2160F78EEaE12217FFbf871fa8';
/** GMX V2 Reader on Arbitrum. */
const ARBITRUM_READER: Address = '0xf60becbba223EEA9495Da3f606753867eC10d139';
/** GMX V2 DataStore on Arbitrum. */
const ARBITRUM_DATA_STORE: Address = '0xFD70de6b91282D8017aA4E741e9Ae325CAb992d8';

// ── Adapter ──────────────────────────────────────────

/** Adapter for GMX V2 perpetual trading on Arbitrum. */
export class GmxAdapter {
  private readonly exchangeRouterAddress: Address;
  private readonly readerAddress: Address;
  private readonly dataStoreAddress: Address;
  private readonly publicClient: PublicClient;
  private readonly walletClient: WalletClient | null;
  private readonly smartWallet: SmartWalletManager | null;
  private readonly log: (event: string, message: string, extra?: Record<string, unknown>) => void;

  constructor(opts: GmxAdapterOptions = {}) {
    this.exchangeRouterAddress = opts.exchangeRouterAddress
      ?? (process.env.GMX_EXCHANGE_ROUTER_ADDRESS as Address | undefined)
      ?? ARBITRUM_EXCHANGE_ROUTER;
    this.readerAddress = opts.readerAddress
      ?? (process.env.GMX_READER_ADDRESS as Address | undefined)
      ?? ARBITRUM_READER;
    this.dataStoreAddress = opts.dataStoreAddress
      ?? (process.env.GMX_DATA_STORE_ADDRESS as Address | undefined)
      ?? ARBITRUM_DATA_STORE;
    this.smartWallet = opts.smartWallet ?? null;
    this.walletClient = opts.walletClient ?? null;
    this.log = opts.onLog ?? (() => {});

    const chain = opts.chain ?? arbitrum;
    const rpcUrl = opts.rpcUrl ?? process.env.ALCHEMY_ARBITRUM_HTTP_URL;

    this.publicClient = opts.publicClient ?? createPublicClient({
      chain,
      transport: http(rpcUrl),
    }) as PublicClient;
  }

  /** Get the ExchangeRouter address. */
  get exchangeRouter(): Address {
    return this.exchangeRouterAddress;
  }

  /** Get the Reader address. */
  get reader(): Address {
    return this.readerAddress;
  }

  /** Get the DataStore address. */
  get dataStore(): Address {
    return this.dataStoreAddress;
  }

  // ── Position Management ──────────────────────────

  /** Open a new leveraged position (long or short). */
  async openPosition(params: OpenPositionParams): Promise<GmxResult> {
    this.log('gmx_open_start', 'Opening GMX position', {
      market: params.market,
      isLong: params.isLong,
      sizeDeltaUsd: params.sizeDeltaUsd.toString(),
      collateralAmount: params.collateralAmount.toString(),
    });

    // Approve collateral token
    await this.ensureAllowance(
      params.collateralToken,
      params.collateralAmount,
      this.getSenderAddress(),
    );

    // Build createOrder call via multicall (sendWnt + sendTokens + createOrder)
    const sendWntData = encodeFunctionData({
      abi: GMX_EXCHANGE_ROUTER_ABI,
      functionName: 'sendWnt',
      args: [this.exchangeRouterAddress, params.executionFee],
    });

    const sendTokensData = encodeFunctionData({
      abi: GMX_EXCHANGE_ROUTER_ABI,
      functionName: 'sendTokens',
      args: [params.collateralToken, this.exchangeRouterAddress, params.collateralAmount],
    });

    // Order type for market increase
    const orderType = '0x0000000000000000000000000000000000000000000000000000000000000002' as `0x${string}`; // MarketIncrease
    const noSwapType = '0x0000000000000000000000000000000000000000000000000000000000000000' as `0x${string}`;
    const referralCode = '0x0000000000000000000000000000000000000000000000000000000000000000' as `0x${string}`;

    const createOrderData = encodeFunctionData({
      abi: GMX_EXCHANGE_ROUTER_ABI,
      functionName: 'createOrder',
      args: [{
        addresses: [
          this.getSenderAddress(), // receiver
          this.getSenderAddress(), // callbackContract (none)
          params.market,
          params.collateralToken,
          '0x0000000000000000000000000000000000000000' as Address, // uiFeeReceiver
        ],
        numbers: [
          params.sizeDeltaUsd,
          params.collateralAmount,
          0n, // triggerPrice
          params.acceptablePrice,
          params.executionFee,
          0n, // callbackGasLimit
          0n, // minOutputAmount
        ],
        orderType,
        decreasePositionSwapType: noSwapType,
        isLong: params.isLong,
        shouldUnwrapNativeToken: false,
        referralCode,
      }],
    });

    const multicallData = encodeFunctionData({
      abi: GMX_EXCHANGE_ROUTER_ABI,
      functionName: 'multicall',
      args: [[sendWntData, sendTokensData, createOrderData]],
    });

    const gasEstimate = await this.estimateL2Gas(this.exchangeRouterAddress, multicallData, params.executionFee);
    const txHash = await this.sendTransaction(
      this.exchangeRouterAddress,
      multicallData,
      gasEstimate,
      params.executionFee,
    );

    const receipt = await this.publicClient.waitForTransactionReceipt({
      hash: txHash,
      timeout: 120_000,
    });

    this.log('gmx_open_complete', 'GMX position opened', {
      txHash,
      gasUsed: receipt.gasUsed.toString(),
      gasEstimate: gasEstimate.toString(),
      blockNumber: Number(receipt.blockNumber),
    });

    return { txHash, gasUsed: receipt.gasUsed, gasEstimate };
  }

  /** Close an existing position. */
  async closePosition(params: ClosePositionParams): Promise<GmxResult> {
    this.log('gmx_close_start', 'Closing GMX position', {
      market: params.market,
      isLong: params.isLong,
      sizeDeltaUsd: params.sizeDeltaUsd.toString(),
    });

    const sendWntData = encodeFunctionData({
      abi: GMX_EXCHANGE_ROUTER_ABI,
      functionName: 'sendWnt',
      args: [this.exchangeRouterAddress, params.executionFee],
    });

    // Order type for market decrease
    const orderType = '0x0000000000000000000000000000000000000000000000000000000000000004' as `0x${string}`; // MarketDecrease
    const noSwapType = '0x0000000000000000000000000000000000000000000000000000000000000000' as `0x${string}`;
    const referralCode = '0x0000000000000000000000000000000000000000000000000000000000000000' as `0x${string}`;

    const createOrderData = encodeFunctionData({
      abi: GMX_EXCHANGE_ROUTER_ABI,
      functionName: 'createOrder',
      args: [{
        addresses: [
          this.getSenderAddress(),
          this.getSenderAddress(),
          params.market,
          params.collateralToken,
          '0x0000000000000000000000000000000000000000' as Address,
        ],
        numbers: [
          params.sizeDeltaUsd,
          0n, // initialCollateralDeltaAmount
          0n, // triggerPrice
          params.acceptablePrice,
          params.executionFee,
          0n,
          0n,
        ],
        orderType,
        decreasePositionSwapType: noSwapType,
        isLong: params.isLong,
        shouldUnwrapNativeToken: false,
        referralCode,
      }],
    });

    const multicallData = encodeFunctionData({
      abi: GMX_EXCHANGE_ROUTER_ABI,
      functionName: 'multicall',
      args: [[sendWntData, createOrderData]],
    });

    const gasEstimate = await this.estimateL2Gas(this.exchangeRouterAddress, multicallData, params.executionFee);
    const txHash = await this.sendTransaction(
      this.exchangeRouterAddress,
      multicallData,
      gasEstimate,
      params.executionFee,
    );

    const receipt = await this.publicClient.waitForTransactionReceipt({
      hash: txHash,
      timeout: 120_000,
    });

    this.log('gmx_close_complete', 'GMX position closed', {
      txHash,
      gasUsed: receipt.gasUsed.toString(),
      gasEstimate: gasEstimate.toString(),
    });

    return { txHash, gasUsed: receipt.gasUsed, gasEstimate };
  }

  /** Adjust margin for an existing position (add or remove collateral). */
  async adjustMargin(params: AdjustMarginParams): Promise<GmxResult> {
    const isAdding = params.collateralDelta > 0n;

    this.log('gmx_margin_start', `${isAdding ? 'Adding' : 'Removing'} margin`, {
      market: params.market,
      isLong: params.isLong,
      collateralDelta: params.collateralDelta.toString(),
    });

    if (isAdding) {
      await this.ensureAllowance(
        params.collateralToken,
        params.collateralDelta,
        this.getSenderAddress(),
      );
    }

    const sendWntData = encodeFunctionData({
      abi: GMX_EXCHANGE_ROUTER_ABI,
      functionName: 'sendWnt',
      args: [this.exchangeRouterAddress, params.executionFee],
    });

    const multicallArgs: Hex[] = [sendWntData];

    if (isAdding) {
      const sendTokensData = encodeFunctionData({
        abi: GMX_EXCHANGE_ROUTER_ABI,
        functionName: 'sendTokens',
        args: [params.collateralToken, this.exchangeRouterAddress, params.collateralDelta],
      });
      multicallArgs.push(sendTokensData);
    }

    // MarketIncrease with zero size = add collateral; MarketDecrease with zero size = remove collateral
    const orderType = isAdding
      ? '0x0000000000000000000000000000000000000000000000000000000000000002' as `0x${string}`
      : '0x0000000000000000000000000000000000000000000000000000000000000004' as `0x${string}`;
    const noSwapType = '0x0000000000000000000000000000000000000000000000000000000000000000' as `0x${string}`;
    const referralCode = '0x0000000000000000000000000000000000000000000000000000000000000000' as `0x${string}`;

    const absCollateral = isAdding ? params.collateralDelta : -params.collateralDelta;

    const createOrderData = encodeFunctionData({
      abi: GMX_EXCHANGE_ROUTER_ABI,
      functionName: 'createOrder',
      args: [{
        addresses: [
          this.getSenderAddress(),
          this.getSenderAddress(),
          params.market,
          params.collateralToken,
          '0x0000000000000000000000000000000000000000' as Address,
        ],
        numbers: [
          0n, // sizeDeltaUsd = 0 for margin adjustment
          absCollateral,
          0n,
          0n,
          params.executionFee,
          0n,
          0n,
        ],
        orderType,
        decreasePositionSwapType: noSwapType,
        isLong: params.isLong,
        shouldUnwrapNativeToken: false,
        referralCode,
      }],
    });

    multicallArgs.push(createOrderData);

    const multicallData = encodeFunctionData({
      abi: GMX_EXCHANGE_ROUTER_ABI,
      functionName: 'multicall',
      args: [multicallArgs],
    });

    const gasEstimate = await this.estimateL2Gas(this.exchangeRouterAddress, multicallData, params.executionFee);
    const txHash = await this.sendTransaction(
      this.exchangeRouterAddress,
      multicallData,
      gasEstimate,
      params.executionFee,
    );

    const receipt = await this.publicClient.waitForTransactionReceipt({
      hash: txHash,
      timeout: 120_000,
    });

    this.log('gmx_margin_complete', 'Margin adjusted', {
      txHash,
      isAdding,
      gasUsed: receipt.gasUsed.toString(),
      gasEstimate: gasEstimate.toString(),
    });

    return { txHash, gasUsed: receipt.gasUsed, gasEstimate };
  }

  // ── Queries ──────────────────────────────────────

  /** Query funding rates for a market from the DataStore. */
  async getFundingRates(market: Address): Promise<FundingRateInfo> {
    // Funding rate keys in GMX DataStore are computed as keccak256 hashes
    // For simplicity, query the cumulative funding values
    try {
      const [longRate, shortRate] = await Promise.all([
        this.publicClient.readContract({
          address: this.dataStoreAddress,
          abi: GMX_DATA_STORE_ABI,
          functionName: 'getInt',
          args: [this.computeFundingKey(market, true)],
        }) as Promise<bigint>,
        this.publicClient.readContract({
          address: this.dataStoreAddress,
          abi: GMX_DATA_STORE_ABI,
          functionName: 'getInt',
          args: [this.computeFundingKey(market, false)],
        }) as Promise<bigint>,
      ]);

      this.log('gmx_funding_rates', 'Funding rates queried', {
        market,
        longFundingRate: longRate.toString(),
        shortFundingRate: shortRate.toString(),
      });

      return { longFundingRate: longRate, shortFundingRate: shortRate, market };
    } catch {
      this.log('gmx_funding_rates_error', 'Failed to query funding rates', { market });
      return { longFundingRate: 0n, shortFundingRate: 0n, market };
    }
  }

  /** Query a position by key. */
  async getPosition(positionKey: `0x${string}`): Promise<GmxPosition> {
    const result = await this.publicClient.readContract({
      address: this.readerAddress,
      abi: GMX_READER_ABI,
      functionName: 'getPosition',
      args: [this.dataStoreAddress, positionKey],
    }) as unknown as unknown[];

    const position: GmxPosition = {
      account: result[0] as Address,
      market: result[1] as Address,
      collateralToken: result[2] as Address,
      isLong: result[3] as boolean,
      sizeInUsd: BigInt(result[4] as bigint),
      sizeInTokens: BigInt(result[5] as bigint),
      collateralAmount: BigInt(result[6] as bigint),
      borrowingFactor: BigInt(result[7] as bigint),
    };

    this.log('gmx_position_query', 'Position queried', {
      positionKey,
      market: position.market,
      isLong: position.isLong,
      sizeInUsd: position.sizeInUsd.toString(),
      collateralAmount: position.collateralAmount.toString(),
    });

    return position;
  }

  // ── Helpers ──────────────────────────────────────

  /** Compute the DataStore key for funding rate. */
  private computeFundingKey(market: Address, isLong: boolean): `0x${string}` {
    // Simplified key computation; real implementation uses GMX key hash
    const side = isLong ? '01' : '00';
    return `0x${market.slice(2).padEnd(64, '0')}${side.padStart(64, '0')}` as `0x${string}`;
  }

  /** Ensure ERC-20 allowance for the ExchangeRouter. */
  private async ensureAllowance(
    token: Address,
    amount: bigint,
    owner: Address,
  ): Promise<void> {
    const currentAllowance = await this.publicClient.readContract({
      address: token,
      abi: ERC20_ABI,
      functionName: 'allowance',
      args: [owner, this.exchangeRouterAddress],
    });

    if ((currentAllowance as bigint) >= amount) {
      return;
    }

    const approveData = encodeFunctionData({
      abi: ERC20_ABI,
      functionName: 'approve',
      args: [this.exchangeRouterAddress, amount],
    });

    const gasEstimate = await this.estimateL2Gas(token, approveData);
    const txHash = await this.sendTransaction(token, approveData, gasEstimate);

    await this.publicClient.waitForTransactionReceipt({
      hash: txHash,
      timeout: 60_000,
    });
  }

  /**
   * Estimate gas for an L2 transaction.
   * Accounts for L1 data posting costs on Arbitrum.
   */
  private async estimateL2Gas(to: Address, data: Hex, value?: bigint): Promise<bigint> {
    try {
      const estimate = await this.publicClient.estimateGas({
        to,
        data,
        value,
        account: this.getSenderAddress() as `0x${string}`,
      });
      // 10% buffer + additional buffer for L1 data posting cost
      // Arbitrum L1 data cost is approximately 16 gas per non-zero byte
      const l1DataCost = BigInt(data.length / 2) * 16n;
      return ((estimate + l1DataCost) * 110n) / 100n;
    } catch {
      return 1_500_000n; // Higher default for GMX operations on Arbitrum
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
