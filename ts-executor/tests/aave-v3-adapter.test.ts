import { describe, it, expect, vi, beforeEach } from 'vitest';
import { AaveV3Adapter, type ReserveData } from '../src/execution/aave-v3-adapter.js';
import { type Address } from 'viem';

// ── Test helpers ──────────────────────────────────

const TEST_ASSET: Address = '0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48'; // USDC
const TEST_POOL: Address = '0x6Ae43d3271ff6888e7Fc43Fd7321a503ff738951';
const TEST_ATOKEN: Address = '0x1234567890abcdef1234567890abcdef12345678';
const TEST_WALLET: Address = '0x70997970C51812dc3A010C7d01b50e0d17dc79C8';

const RAY = BigInt('1000000000000000000000000000');

function createMockReserveData() {
  return {
    configuration: 0n,
    liquidityIndex: RAY,
    currentLiquidityRate: RAY * 3n / 100n, // 3% APY
    variableBorrowIndex: RAY,
    currentVariableBorrowRate: RAY * 5n / 100n, // 5%
    currentStableBorrowRate: RAY * 7n / 100n, // 7%
    lastUpdateTimestamp: 1700000000,
    id: 1,
    aTokenAddress: TEST_ATOKEN,
    stableDebtTokenAddress: '0x0000000000000000000000000000000000000001' as Address,
    variableDebtTokenAddress: '0x0000000000000000000000000000000000000002' as Address,
    interestRateStrategyAddress: '0x0000000000000000000000000000000000000003' as Address,
    accruedToTreasury: 0n,
    unbacked: 0n,
    isolationModeTotalDebt: 0n,
  };
}

function createMockPublicClient(opts: {
  reserveData?: any;
  balanceOf?: bigint;
  allowance?: bigint;
  estimateGas?: bigint;
  waitReceipt?: any;
} = {}) {
  const readContractFn = vi.fn().mockImplementation(async ({ functionName, args }: any) => {
    if (functionName === 'getReserveData') {
      return opts.reserveData ?? createMockReserveData();
    }
    if (functionName === 'balanceOf') {
      return opts.balanceOf ?? BigInt('1000000000000');
    }
    if (functionName === 'allowance') {
      return opts.allowance ?? 0n;
    }
    if (functionName === 'decimals') {
      return 6;
    }
    return 0n;
  });

  return {
    readContract: readContractFn,
    estimateGas: vi.fn().mockResolvedValue(opts.estimateGas ?? 200000n),
    waitForTransactionReceipt: vi.fn().mockResolvedValue(opts.waitReceipt ?? {
      status: 'success',
      blockNumber: 100n,
      gasUsed: 180000n,
      effectiveGasPrice: 30000000000n,
      transactionHash: '0xtx123',
    }),
  } as any;
}

function createMockWalletClient() {
  return {
    sendTransaction: vi.fn().mockResolvedValue('0xtx123' as `0x${string}`),
    chain: { id: 11155111, name: 'Sepolia' },
    account: { address: TEST_WALLET },
  } as any;
}

// ── Tests ──────────────────────────────────────────

describe('AaveV3Adapter', () => {
  describe('constructor', () => {
    it('creates with default Sepolia pool address', () => {
      const publicClient = createMockPublicClient();
      const adapter = new AaveV3Adapter({ publicClient });
      expect(adapter.pool).toBe(TEST_POOL);
    });

    it('accepts custom pool address', () => {
      const customPool = '0xCustomPoolAddress1234567890abcdef12345678' as Address;
      const publicClient = createMockPublicClient();
      const adapter = new AaveV3Adapter({ publicClient, poolAddress: customPool });
      expect(adapter.pool).toBe(customPool);
    });
  });

  describe('getReserveData', () => {
    it('fetches and parses reserve data', async () => {
      const publicClient = createMockPublicClient();
      const adapter = new AaveV3Adapter({ publicClient });

      const data = await adapter.getReserveData(TEST_ASSET);

      expect(data.aTokenAddress).toBe(TEST_ATOKEN);
      expect(data.supplyAPY).toBeGreaterThan(0);
      expect(data.supplyAPY).toBeLessThan(1); // Should be a decimal like 0.03
      expect(data.utilizationRate).toBeGreaterThanOrEqual(0);
      expect(data.utilizationRate).toBeLessThanOrEqual(1);
      expect(data.liquidityRate).toBeGreaterThan(0n);
    });

    it('returns zero utilization when no borrows', async () => {
      const reserveData = createMockReserveData();
      reserveData.currentVariableBorrowRate = 0n;
      const publicClient = createMockPublicClient({ reserveData });
      const adapter = new AaveV3Adapter({ publicClient });

      const data = await adapter.getReserveData(TEST_ASSET);
      expect(data.utilizationRate).toBe(0);
    });

    it('logs reserve data fetch', async () => {
      const logs: Array<{ event: string; extra?: Record<string, unknown> }> = [];
      const publicClient = createMockPublicClient();
      const adapter = new AaveV3Adapter({
        publicClient,
        onLog: (event, _msg, extra) => logs.push({ event, extra }),
      });

      await adapter.getReserveData(TEST_ASSET);

      expect(logs.some((l) => l.event === 'aave_reserve_data')).toBe(true);
    });
  });

  describe('getSupplyAPY', () => {
    it('returns supply APY as a decimal', async () => {
      const publicClient = createMockPublicClient();
      const adapter = new AaveV3Adapter({ publicClient });

      const apy = await adapter.getSupplyAPY(TEST_ASSET);
      expect(apy).toBeCloseTo(0.03, 2);
    });
  });

  describe('getUtilizationRate', () => {
    it('returns utilization rate as a decimal', async () => {
      const publicClient = createMockPublicClient();
      const adapter = new AaveV3Adapter({ publicClient });

      const rate = await adapter.getUtilizationRate(TEST_ASSET);
      expect(rate).toBeGreaterThanOrEqual(0);
      expect(rate).toBeLessThanOrEqual(1);
    });
  });

  describe('getAvailableLiquidity', () => {
    it('returns available liquidity', async () => {
      const publicClient = createMockPublicClient({ balanceOf: 5000000000n });
      const adapter = new AaveV3Adapter({ publicClient });

      const liquidity = await adapter.getAvailableLiquidity(TEST_ASSET);
      expect(liquidity).toBe(5000000000n);
    });
  });

  describe('supply', () => {
    it('approves and supplies to Aave V3', async () => {
      const publicClient = createMockPublicClient({ allowance: 0n });
      const walletClient = createMockWalletClient();
      const logs: Array<{ event: string }> = [];

      const adapter = new AaveV3Adapter({
        publicClient,
        walletClient,
        onLog: (event) => logs.push({ event }),
      });

      const result = await adapter.supply(TEST_ASSET, 1000000n);

      expect(result.txHash).toBe('0xtx123');
      expect(result.asset).toBe(TEST_ASSET);
      expect(result.amount).toBe(1000000n);
      expect(result.gasUsed).toBeDefined();
      expect(result.gasEstimate).toBeDefined();
      // Should have called approve then supply (2 sendTransaction calls)
      expect(walletClient.sendTransaction).toHaveBeenCalledTimes(2);
      expect(logs.some((l) => l.event === 'aave_supply_start')).toBe(true);
      expect(logs.some((l) => l.event === 'aave_supply_complete')).toBe(true);
    });

    it('skips approve if allowance is sufficient', async () => {
      const publicClient = createMockPublicClient({ allowance: 2000000n });
      const walletClient = createMockWalletClient();
      const logs: Array<{ event: string }> = [];

      const adapter = new AaveV3Adapter({
        publicClient,
        walletClient,
        onLog: (event) => logs.push({ event }),
      });

      await adapter.supply(TEST_ASSET, 1000000n);

      // Only 1 call (supply, no approve needed)
      expect(walletClient.sendTransaction).toHaveBeenCalledTimes(1);
      expect(logs.some((l) => l.event === 'aave_allowance_sufficient')).toBe(true);
    });
  });

  describe('withdraw', () => {
    it('withdraws from Aave V3', async () => {
      const publicClient = createMockPublicClient();
      const walletClient = createMockWalletClient();
      const logs: Array<{ event: string }> = [];

      const adapter = new AaveV3Adapter({
        publicClient,
        walletClient,
        onLog: (event) => logs.push({ event }),
      });

      const result = await adapter.withdraw(TEST_ASSET, 500000n);

      expect(result.txHash).toBe('0xtx123');
      expect(result.asset).toBe(TEST_ASSET);
      expect(result.amountWithdrawn).toBe(500000n);
      expect(walletClient.sendTransaction).toHaveBeenCalledTimes(1);
      expect(logs.some((l) => l.event === 'aave_withdraw_start')).toBe(true);
      expect(logs.some((l) => l.event === 'aave_withdraw_complete')).toBe(true);
    });

    it('withdraws to a custom recipient', async () => {
      const publicClient = createMockPublicClient();
      const walletClient = createMockWalletClient();

      const adapter = new AaveV3Adapter({ publicClient, walletClient });
      const customTo = '0xdead000000000000000000000000000000000001' as Address;

      const result = await adapter.withdraw(TEST_ASSET, 500000n, customTo);
      expect(result.txHash).toBe('0xtx123');
    });
  });

  describe('gas estimation', () => {
    it('estimates gas within 10% accuracy (adds 10% buffer)', async () => {
      const publicClient = createMockPublicClient({ estimateGas: 200000n });
      const adapter = new AaveV3Adapter({ publicClient });

      // The adapter adds 10% buffer to the estimate
      // We check via the supply result
      const walletClient = createMockWalletClient();
      const adapter2 = new AaveV3Adapter({
        publicClient: createMockPublicClient({ estimateGas: 200000n, allowance: 1000000000n }),
        walletClient,
      });

      const result = await adapter2.supply(TEST_ASSET, 1000n);
      // Gas estimate should be 200000 * 110% = 220000
      expect(result.gasEstimate).toBe(220000n);
    });

    it('falls back to default gas when estimation fails', async () => {
      const publicClient = createMockPublicClient();
      publicClient.estimateGas = vi.fn().mockRejectedValue(new Error('estimation failed'));
      const walletClient = createMockWalletClient();
      // Need sufficient allowance to avoid approve call
      publicClient.readContract = vi.fn().mockImplementation(async ({ functionName }: any) => {
        if (functionName === 'getReserveData') return createMockReserveData();
        if (functionName === 'balanceOf') return 1000000000n;
        if (functionName === 'allowance') return 1000000000n;
        return 0n;
      });

      const adapter = new AaveV3Adapter({ publicClient, walletClient });
      const result = await adapter.supply(TEST_ASSET, 1000n);
      // Should use fallback of 300_000n
      expect(result.gasEstimate).toBe(300000n);
    });
  });

  describe('encoding helpers', () => {
    it('encodes supply call data', () => {
      const publicClient = createMockPublicClient();
      const adapter = new AaveV3Adapter({ publicClient });

      const data = adapter.encodeSupply(TEST_ASSET, 1000000n, TEST_WALLET);
      expect(data).toBeDefined();
      expect(data.startsWith('0x')).toBe(true);
    });

    it('encodes withdraw call data', () => {
      const publicClient = createMockPublicClient();
      const adapter = new AaveV3Adapter({ publicClient });

      const data = adapter.encodeWithdraw(TEST_ASSET, 1000000n, TEST_WALLET);
      expect(data).toBeDefined();
      expect(data.startsWith('0x')).toBe(true);
    });
  });

  describe('smart wallet integration', () => {
    it('uses smart wallet for transactions when available', async () => {
      const publicClient = createMockPublicClient({ allowance: 1000000000n });
      const mockSmartWallet = {
        address: TEST_WALLET,
        buildUserOp: vi.fn().mockResolvedValue({
          sender: TEST_WALLET,
          nonce: 0n,
          callData: '0x' as `0x${string}`,
          callGasLimit: 200000n,
          verificationGasLimit: 100000n,
          preVerificationGas: 50000n,
          maxFeePerGas: 30000000000n,
          maxPriorityFeePerGas: 3000000000n,
          signature: '0x' as `0x${string}`,
        }),
        sendUserOp: vi.fn().mockResolvedValue('0xuserop123' as `0x${string}`),
      } as any;

      const adapter = new AaveV3Adapter({
        publicClient,
        smartWallet: mockSmartWallet,
      });

      const result = await adapter.supply(TEST_ASSET, 1000000n);

      expect(mockSmartWallet.buildUserOp).toHaveBeenCalled();
      expect(mockSmartWallet.sendUserOp).toHaveBeenCalled();
      expect(result.txHash).toBe('0xuserop123');
    });
  });

  describe('error handling', () => {
    it('throws when no wallet is configured', async () => {
      const publicClient = createMockPublicClient({ allowance: 1000000000n });
      const adapter = new AaveV3Adapter({ publicClient });

      await expect(adapter.supply(TEST_ASSET, 1000000n)).rejects.toThrow('No wallet');
    });
  });
});
