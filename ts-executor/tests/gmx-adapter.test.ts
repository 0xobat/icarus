import { describe, it, expect, vi, beforeEach } from 'vitest';
import { GmxAdapter, type GmxPosition, type FundingRateInfo } from '../src/execution/gmx-adapter.js';
import { type Address } from 'viem';

// ── Test helpers ──────────────────────────────────

const TEST_WALLET: Address = '0x70997970C51812dc3A010C7d01b50e0d17dc79C8';
const TEST_MARKET: Address = '0x70d95587d40A2Cd56060E0087b6B0A001d5482AF';
const TEST_COLLATERAL: Address = '0xaf88d065e77c8cC2239327C5EDb3A432268e5831';
const TEST_INDEX: Address = '0x82aF49447D8a07e3bd95BD0d56f35241523fBab1';
const TEST_EXCHANGE_ROUTER: Address = '0x7C68C7866A64FA2160F78EEaE12217FFbf871fa8';
const TEST_READER: Address = '0xf60becbba223EEA9495Da3f606753867eC10d139';
const TEST_DATA_STORE: Address = '0xFD70de6b91282D8017aA4E741e9Ae325CAb992d8';

function createMockPublicClient(opts: {
  allowance?: bigint;
  estimateGas?: bigint;
  fundingRate?: bigint;
  positionData?: any;
} = {}) {
  return {
    readContract: vi.fn().mockImplementation(async ({ functionName, args }: any) => {
      if (functionName === 'allowance') {
        return opts.allowance ?? 0n;
      }
      if (functionName === 'getInt') {
        return opts.fundingRate ?? 500000n;
      }
      if (functionName === 'getUint') {
        return 1000000n;
      }
      if (functionName === 'getPosition') {
        return opts.positionData ?? [
          TEST_WALLET,                    // account
          TEST_MARKET,                    // market
          TEST_COLLATERAL,                // collateralToken
          true,                           // isLong
          50000000000000000000000000000000n, // sizeInUsd (50k USD)
          1000000000000000000n,            // sizeInTokens
          10000000000000000000n,           // collateralAmount (10 tokens)
          100000n,                         // borrowingFactor
        ];
      }
      if (functionName === 'getMarket') {
        return {
          marketToken: TEST_MARKET,
          indexToken: TEST_INDEX,
          longToken: TEST_COLLATERAL,
          shortToken: TEST_COLLATERAL,
        };
      }
      return 0n;
    }),
    estimateGas: vi.fn().mockResolvedValue(opts.estimateGas ?? 800000n),
    waitForTransactionReceipt: vi.fn().mockResolvedValue({
      status: 'success',
      blockNumber: 100n,
      gasUsed: 750000n,
    }),
  } as any;
}

function createMockWalletClient() {
  return {
    sendTransaction: vi.fn().mockResolvedValue('0xtx123' as `0x${string}`),
    chain: { id: 42161, name: 'Arbitrum' },
    account: { address: TEST_WALLET },
  } as any;
}

// ── Tests ──────────────────────────────────────────

describe('GmxAdapter', () => {
  describe('constructor', () => {
    it('creates with default Arbitrum addresses', () => {
      const publicClient = createMockPublicClient();
      const adapter = new GmxAdapter({ publicClient });

      expect(adapter.exchangeRouter).toBe(TEST_EXCHANGE_ROUTER);
      expect(adapter.reader).toBe(TEST_READER);
      expect(adapter.dataStore).toBe(TEST_DATA_STORE);
    });

    it('accepts custom addresses', () => {
      const custom = '0xCustomExchangeRouter1234567890abcdef123456' as Address;
      const publicClient = createMockPublicClient();
      const adapter = new GmxAdapter({ publicClient, exchangeRouterAddress: custom });

      expect(adapter.exchangeRouter).toBe(custom);
    });
  });

  describe('openPosition', () => {
    it('opens a long position', async () => {
      const publicClient = createMockPublicClient({ allowance: 0n });
      const walletClient = createMockWalletClient();
      const logs: Array<{ event: string; extra?: Record<string, unknown> }> = [];

      const adapter = new GmxAdapter({
        publicClient,
        walletClient,
        onLog: (event, _msg, extra) => logs.push({ event, extra }),
      });

      const result = await adapter.openPosition({
        market: TEST_MARKET,
        collateralToken: TEST_COLLATERAL,
        indexToken: TEST_INDEX,
        isLong: true,
        sizeDeltaUsd: 50000000000000000000000000000000n,
        collateralAmount: 10000000000000000000n,
        acceptablePrice: 2000000000000000000000000000000000n,
        executionFee: 100000000000000n, // 0.0001 ETH
      });

      expect(result.txHash).toBe('0xtx123');
      expect(result.gasUsed).toBeDefined();
      expect(result.gasEstimate).toBeDefined();
      expect(logs.some((l) => l.event === 'gmx_open_start')).toBe(true);
      expect(logs.some((l) => l.event === 'gmx_open_complete')).toBe(true);
    });

    it('opens a short position', async () => {
      const publicClient = createMockPublicClient({ allowance: 10000000000000000000n });
      const walletClient = createMockWalletClient();
      const logs: Array<{ event: string; extra?: Record<string, unknown> }> = [];

      const adapter = new GmxAdapter({
        publicClient,
        walletClient,
        onLog: (event, _msg, extra) => logs.push({ event, extra }),
      });

      const result = await adapter.openPosition({
        market: TEST_MARKET,
        collateralToken: TEST_COLLATERAL,
        indexToken: TEST_INDEX,
        isLong: false,
        sizeDeltaUsd: 30000000000000000000000000000000n,
        collateralAmount: 5000000000000000000n,
        acceptablePrice: 1800000000000000000000000000000000n,
        executionFee: 100000000000000n,
      });

      expect(result.txHash).toBe('0xtx123');
      expect(logs.some((l) => l.event === 'gmx_open_start' && l.extra?.isLong === false)).toBe(true);
    });

    it('approves collateral token before opening', async () => {
      const publicClient = createMockPublicClient({ allowance: 0n });
      const walletClient = createMockWalletClient();

      const adapter = new GmxAdapter({ publicClient, walletClient });

      await adapter.openPosition({
        market: TEST_MARKET,
        collateralToken: TEST_COLLATERAL,
        indexToken: TEST_INDEX,
        isLong: true,
        sizeDeltaUsd: 50000000000000000000000000000000n,
        collateralAmount: 10000000000000000000n,
        acceptablePrice: 2000000000000000000000000000000000n,
        executionFee: 100000000000000n,
      });

      // approve + openPosition = 2 calls
      expect(walletClient.sendTransaction).toHaveBeenCalledTimes(2);
    });
  });

  describe('closePosition', () => {
    it('closes an existing position', async () => {
      const publicClient = createMockPublicClient();
      const walletClient = createMockWalletClient();
      const logs: Array<{ event: string }> = [];

      const adapter = new GmxAdapter({
        publicClient,
        walletClient,
        onLog: (event) => logs.push({ event }),
      });

      const result = await adapter.closePosition({
        market: TEST_MARKET,
        collateralToken: TEST_COLLATERAL,
        indexToken: TEST_INDEX,
        isLong: true,
        sizeDeltaUsd: 50000000000000000000000000000000n,
        acceptablePrice: 1900000000000000000000000000000000n,
        executionFee: 100000000000000n,
      });

      expect(result.txHash).toBe('0xtx123');
      expect(logs.some((l) => l.event === 'gmx_close_start')).toBe(true);
      expect(logs.some((l) => l.event === 'gmx_close_complete')).toBe(true);
    });
  });

  describe('adjustMargin', () => {
    it('adds margin to a position', async () => {
      const publicClient = createMockPublicClient({ allowance: 0n });
      const walletClient = createMockWalletClient();
      const logs: Array<{ event: string; extra?: Record<string, unknown> }> = [];

      const adapter = new GmxAdapter({
        publicClient,
        walletClient,
        onLog: (event, _msg, extra) => logs.push({ event, extra }),
      });

      const result = await adapter.adjustMargin({
        market: TEST_MARKET,
        collateralToken: TEST_COLLATERAL,
        indexToken: TEST_INDEX,
        isLong: true,
        collateralDelta: 5000000000000000000n, // Adding collateral
        executionFee: 100000000000000n,
      });

      expect(result.txHash).toBe('0xtx123');
      expect(logs.some((l) => l.event === 'gmx_margin_start')).toBe(true);
      expect(logs.some((l) => l.event === 'gmx_margin_complete' && l.extra?.isAdding === true)).toBe(true);
    });

    it('removes margin from a position', async () => {
      const publicClient = createMockPublicClient();
      const walletClient = createMockWalletClient();
      const logs: Array<{ event: string; extra?: Record<string, unknown> }> = [];

      const adapter = new GmxAdapter({
        publicClient,
        walletClient,
        onLog: (event, _msg, extra) => logs.push({ event, extra }),
      });

      const result = await adapter.adjustMargin({
        market: TEST_MARKET,
        collateralToken: TEST_COLLATERAL,
        indexToken: TEST_INDEX,
        isLong: true,
        collateralDelta: -2000000000000000000n, // Removing collateral
        executionFee: 100000000000000n,
      });

      expect(result.txHash).toBe('0xtx123');
      expect(logs.some((l) => l.event === 'gmx_margin_complete' && l.extra?.isAdding === false)).toBe(true);
    });
  });

  describe('getFundingRates', () => {
    it('queries funding rates for a market', async () => {
      const publicClient = createMockPublicClient({ fundingRate: 500000n });
      const logs: Array<{ event: string }> = [];

      const adapter = new GmxAdapter({
        publicClient,
        onLog: (event) => logs.push({ event }),
      });

      const rates = await adapter.getFundingRates(TEST_MARKET);

      expect(rates.market).toBe(TEST_MARKET);
      expect(rates.longFundingRate).toBe(500000n);
      expect(rates.shortFundingRate).toBe(500000n);
      expect(logs.some((l) => l.event === 'gmx_funding_rates')).toBe(true);
    });

    it('returns zero rates on error', async () => {
      const publicClient = createMockPublicClient();
      publicClient.readContract = vi.fn().mockRejectedValue(new Error('not deployed'));
      const logs: Array<{ event: string }> = [];

      const adapter = new GmxAdapter({
        publicClient,
        onLog: (event) => logs.push({ event }),
      });

      const rates = await adapter.getFundingRates(TEST_MARKET);

      expect(rates.longFundingRate).toBe(0n);
      expect(rates.shortFundingRate).toBe(0n);
    });
  });

  describe('getPosition', () => {
    it('queries position by key', async () => {
      const publicClient = createMockPublicClient();
      const logs: Array<{ event: string }> = [];

      const adapter = new GmxAdapter({
        publicClient,
        onLog: (event) => logs.push({ event }),
      });

      const posKey = '0x1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef' as `0x${string}`;
      const position = await adapter.getPosition(posKey);

      expect(position.account).toBe(TEST_WALLET);
      expect(position.market).toBe(TEST_MARKET);
      expect(position.isLong).toBe(true);
      expect(position.sizeInUsd).toBeGreaterThan(0n);
      expect(position.collateralAmount).toBeGreaterThan(0n);
      expect(logs.some((l) => l.event === 'gmx_position_query')).toBe(true);
    });
  });

  describe('L2 gas estimation', () => {
    it('accounts for L1 data posting costs on Arbitrum', async () => {
      const publicClient = createMockPublicClient({
        estimateGas: 800000n,
        allowance: 10000000000000000000n,
      });
      const walletClient = createMockWalletClient();

      const adapter = new GmxAdapter({ publicClient, walletClient });

      const result = await adapter.closePosition({
        market: TEST_MARKET,
        collateralToken: TEST_COLLATERAL,
        indexToken: TEST_INDEX,
        isLong: true,
        sizeDeltaUsd: 50000000000000000000000000000000n,
        acceptablePrice: 1900000000000000000000000000000000n,
        executionFee: 100000000000000n,
      });

      // Gas estimate should be > 800000 * 1.1 due to L1 data cost
      expect(result.gasEstimate).toBeGreaterThan(880000n);
    });

    it('falls back to high default gas on estimation failure', async () => {
      const publicClient = createMockPublicClient({ allowance: 10000000000000000000n });
      publicClient.estimateGas = vi.fn().mockRejectedValue(new Error('failed'));
      const walletClient = createMockWalletClient();

      const adapter = new GmxAdapter({ publicClient, walletClient });

      const result = await adapter.closePosition({
        market: TEST_MARKET,
        collateralToken: TEST_COLLATERAL,
        indexToken: TEST_INDEX,
        isLong: true,
        sizeDeltaUsd: 50000000000000000000000000000000n,
        acceptablePrice: 1900000000000000000000000000000000n,
        executionFee: 100000000000000n,
      });

      // Default is 1_500_000 for GMX operations
      expect(result.gasEstimate).toBe(1500000n);
    });
  });

  describe('order schema compatibility', () => {
    it('returns same result shape as Ethereum adapters', async () => {
      const publicClient = createMockPublicClient({ allowance: 10000000000000000000n });
      const walletClient = createMockWalletClient();

      const adapter = new GmxAdapter({ publicClient, walletClient });

      const result = await adapter.openPosition({
        market: TEST_MARKET,
        collateralToken: TEST_COLLATERAL,
        indexToken: TEST_INDEX,
        isLong: true,
        sizeDeltaUsd: 50000000000000000000000000000000n,
        collateralAmount: 10000000000000000000n,
        acceptablePrice: 2000000000000000000000000000000000n,
        executionFee: 100000000000000n,
      });

      // Same shape as other adapters: txHash, gasUsed, gasEstimate
      expect(result).toHaveProperty('txHash');
      expect(result).toHaveProperty('gasUsed');
      expect(result).toHaveProperty('gasEstimate');
      expect(typeof result.txHash).toBe('string');
      expect(typeof result.gasUsed).toBe('bigint');
      expect(typeof result.gasEstimate).toBe('bigint');
    });
  });

  describe('smart wallet integration', () => {
    it('uses smart wallet when available', async () => {
      const publicClient = createMockPublicClient({ allowance: 10000000000000000000n });
      const mockSmartWallet = {
        address: TEST_WALLET,
        buildUserOp: vi.fn().mockResolvedValue({
          sender: TEST_WALLET,
          nonce: 0n,
          callData: '0x' as `0x${string}`,
          callGasLimit: 800000n,
          verificationGasLimit: 100000n,
          preVerificationGas: 50000n,
          maxFeePerGas: 30000000000n,
          maxPriorityFeePerGas: 3000000000n,
          signature: '0x' as `0x${string}`,
        }),
        sendUserOp: vi.fn().mockResolvedValue('0xuseropgmx' as `0x${string}`),
      } as any;

      const adapter = new GmxAdapter({
        publicClient,
        smartWallet: mockSmartWallet,
      });

      const result = await adapter.openPosition({
        market: TEST_MARKET,
        collateralToken: TEST_COLLATERAL,
        indexToken: TEST_INDEX,
        isLong: true,
        sizeDeltaUsd: 50000000000000000000000000000000n,
        collateralAmount: 10000000000000000000n,
        acceptablePrice: 2000000000000000000000000000000000n,
        executionFee: 100000000000000n,
      });

      expect(mockSmartWallet.buildUserOp).toHaveBeenCalled();
      expect(result.txHash).toBe('0xuseropgmx');
    });
  });

  describe('error handling', () => {
    it('throws when no wallet configured', async () => {
      const publicClient = createMockPublicClient({ allowance: 10000000000000000000n });
      const adapter = new GmxAdapter({ publicClient });

      await expect(adapter.openPosition({
        market: TEST_MARKET,
        collateralToken: TEST_COLLATERAL,
        indexToken: TEST_INDEX,
        isLong: true,
        sizeDeltaUsd: 50000000000000000000000000000000n,
        collateralAmount: 10000000000000000000n,
        acceptablePrice: 2000000000000000000000000000000000n,
        executionFee: 100000000000000n,
      })).rejects.toThrow('No wallet');
    });
  });
});
