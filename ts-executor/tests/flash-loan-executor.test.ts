import { describe, it, expect, vi, beforeEach } from 'vitest';
import { FlashLoanExecutor, type FlashLoanParams, type SwapStep, type ProfitEstimate } from '../src/execution/flash-loan-executor.js';
import { type Address } from 'viem';

// ── Test helpers ──────────────────────────────────

const TEST_ASSET: Address = '0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48'; // USDC
const TEST_POOL: Address = '0x6Ae43d3271ff6888e7Fc43Fd7321a503ff738951';
const TEST_CALLBACK: Address = '0x1234567890abcdef1234567890abcdef12345678';
const TEST_WALLET: Address = '0x70997970C51812dc3A010C7d01b50e0d17dc79C8';
const TEST_DEX_A: Address = '0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45';
const TEST_DEX_B: Address = '0xDef1C0ded9bec7F1a1670819833240f027b25EfF';

function createTestSwapSteps(): SwapStep[] {
  return [
    {
      dexRouter: TEST_DEX_A,
      tokenIn: TEST_ASSET,
      tokenOut: '0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2' as Address,
      callData: '0x1234' as `0x${string}`,
      amountOutMin: 900000n,
    },
    {
      dexRouter: TEST_DEX_B,
      tokenIn: '0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2' as Address,
      tokenOut: TEST_ASSET,
      callData: '0x5678' as `0x${string}`,
      amountOutMin: 1010000n,
    },
  ];
}

function createTestParams(overrides?: Partial<FlashLoanParams>): FlashLoanParams {
  return {
    asset: TEST_ASSET,
    amount: 1000000n, // 1 USDC
    swapSteps: createTestSwapSteps(),
    minProfit: 10000n, // 0.01 USDC profit
    slippageBps: 50,
    ...overrides,
  };
}

function createMockPublicClient(opts: {
  estimateGas?: bigint;
  gasPrice?: bigint;
  flashLoanPremium?: bigint;
  receiptStatus?: string;
} = {}) {
  return {
    readContract: vi.fn().mockImplementation(async ({ functionName }: any) => {
      if (functionName === 'FLASHLOAN_PREMIUM_TOTAL') {
        return opts.flashLoanPremium ?? 5n;
      }
      if (functionName === 'balanceOf') {
        return 1000000000000n;
      }
      return 0n;
    }),
    estimateGas: vi.fn().mockResolvedValue(opts.estimateGas ?? 500000n),
    getGasPrice: vi.fn().mockResolvedValue(opts.gasPrice ?? 30000000000n),
    waitForTransactionReceipt: vi.fn().mockResolvedValue({
      status: opts.receiptStatus ?? 'success',
      blockNumber: 100n,
      gasUsed: 480000n,
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

describe('FlashLoanExecutor', () => {
  describe('constructor', () => {
    it('creates with default addresses', () => {
      const publicClient = createMockPublicClient();
      const walletClient = createMockWalletClient();
      const executor = new FlashLoanExecutor({ publicClient, walletClient });

      expect(executor.pool).toBe(TEST_POOL);
    });

    it('accepts custom callback contract address', () => {
      const publicClient = createMockPublicClient();
      const walletClient = createMockWalletClient();
      const executor = new FlashLoanExecutor({
        publicClient,
        walletClient,
        callbackContractAddress: TEST_CALLBACK,
      });

      expect(executor.callback).toBe(TEST_CALLBACK);
    });

    it('defaults Flashbots to enabled', () => {
      const publicClient = createMockPublicClient();
      const walletClient = createMockWalletClient();
      const executor = new FlashLoanExecutor({ publicClient, walletClient });

      expect(executor.flashbotsEnabled).toBe(true);
    });
  });

  describe('estimateProfit', () => {
    it('calculates profit with flash loan fee, gas cost, and slippage', async () => {
      const publicClient = createMockPublicClient({
        estimateGas: 500000n,
        gasPrice: 30000000000n, // 30 gwei
      });
      const walletClient = createMockWalletClient();

      const executor = new FlashLoanExecutor({
        publicClient,
        walletClient,
        callbackContractAddress: TEST_CALLBACK,
      });

      const params = createTestParams();
      const estimate = await executor.estimateProfit(params);

      expect(estimate.flashLoanFee).toBeGreaterThan(0n);
      expect(estimate.gasCost).toBeGreaterThan(0n);
      expect(estimate.slippageCost).toBeGreaterThan(0n);
      expect(typeof estimate.isProfitable).toBe('boolean');
    });

    it('flash loan fee is amount * premium / 10000', async () => {
      const publicClient = createMockPublicClient();
      const walletClient = createMockWalletClient();

      const executor = new FlashLoanExecutor({
        publicClient,
        walletClient,
        callbackContractAddress: TEST_CALLBACK,
        flashLoanPremiumBps: 5,
      });

      const params = createTestParams({ amount: 10000n });
      const estimate = await executor.estimateProfit(params);

      // 10000 * 5 / 10000 = 5
      expect(estimate.flashLoanFee).toBe(5n);
    });

    it('accounts for slippage in basis points', async () => {
      const publicClient = createMockPublicClient();
      const walletClient = createMockWalletClient();

      const executor = new FlashLoanExecutor({
        publicClient,
        walletClient,
        callbackContractAddress: TEST_CALLBACK,
      });

      const params = createTestParams({ slippageBps: 100, amount: 10000n }); // 1%
      const estimate = await executor.estimateProfit(params);

      // 10000 * 100 / 10000 = 100
      expect(estimate.slippageCost).toBe(100n);
    });

    it('logs profit estimation', async () => {
      const publicClient = createMockPublicClient();
      const walletClient = createMockWalletClient();
      const logs: Array<{ event: string; extra?: Record<string, unknown> }> = [];

      const executor = new FlashLoanExecutor({
        publicClient,
        walletClient,
        callbackContractAddress: TEST_CALLBACK,
        onLog: (event, _msg, extra) => logs.push({ event, extra }),
      });

      await executor.estimateProfit(createTestParams());

      expect(logs.some((l) => l.event === 'flash_loan_profit_estimate')).toBe(true);
    });
  });

  describe('execute', () => {
    it('executes flash loan when profitable', async () => {
      const publicClient = createMockPublicClient();
      const walletClient = createMockWalletClient();
      const logs: Array<{ event: string }> = [];

      const executor = new FlashLoanExecutor({
        publicClient,
        walletClient,
        callbackContractAddress: TEST_CALLBACK,
        onLog: (event) => logs.push({ event }),
      });

      const result = await executor.execute(createTestParams());

      expect(result.txHash).toBe('0xtx123');
      expect(result.success).toBe(true);
      expect(result.gasUsed).toBeDefined();
      expect(result.gasEstimate).toBeDefined();
      expect(result.profitEstimate).toBeDefined();
      expect(logs.some((l) => l.event === 'flash_loan_start')).toBe(true);
      expect(logs.some((l) => l.event === 'flash_loan_complete')).toBe(true);
    });

    it('aborts when unprofitable (minProfitWei threshold)', async () => {
      const publicClient = createMockPublicClient({
        gasPrice: 1000000000000n, // Very high gas (1000 gwei)
      });
      const walletClient = createMockWalletClient();
      const logs: Array<{ event: string }> = [];

      const executor = new FlashLoanExecutor({
        publicClient,
        walletClient,
        callbackContractAddress: TEST_CALLBACK,
        minProfitWei: 999999999999999999999999999999n, // Unreasonably high minimum
        onLog: (event) => logs.push({ event }),
      });

      const result = await executor.execute(createTestParams({ minProfit: 1n }));

      expect(result.success).toBe(false);
      expect(result.txHash).toBe('0x0');
      expect(logs.some((l) => l.event === 'flash_loan_unprofitable')).toBe(true);
    });

    it('reports failed receipt status', async () => {
      const publicClient = createMockPublicClient({ receiptStatus: 'reverted' });
      const walletClient = createMockWalletClient();

      const executor = new FlashLoanExecutor({
        publicClient,
        walletClient,
        callbackContractAddress: TEST_CALLBACK,
      });

      const result = await executor.execute(createTestParams());

      expect(result.success).toBe(false);
    });

    it('routes transaction through sendTransaction', async () => {
      const publicClient = createMockPublicClient();
      const walletClient = createMockWalletClient();

      const executor = new FlashLoanExecutor({
        publicClient,
        walletClient,
        callbackContractAddress: TEST_CALLBACK,
      });

      await executor.execute(createTestParams());

      expect(walletClient.sendTransaction).toHaveBeenCalledTimes(1);
    });
  });

  describe('getFlashLoanPremium', () => {
    it('queries premium from pool contract', async () => {
      const publicClient = createMockPublicClient({ flashLoanPremium: 5n });
      const walletClient = createMockWalletClient();

      const executor = new FlashLoanExecutor({ publicClient, walletClient });
      const premium = await executor.getFlashLoanPremium();

      expect(premium).toBe(5);
    });

    it('falls back to configured premium on error', async () => {
      const publicClient = createMockPublicClient();
      publicClient.readContract = vi.fn().mockRejectedValue(new Error('not deployed'));
      const walletClient = createMockWalletClient();

      const executor = new FlashLoanExecutor({
        publicClient,
        walletClient,
        flashLoanPremiumBps: 9,
      });

      const premium = await executor.getFlashLoanPremium();
      expect(premium).toBe(9);
    });
  });

  describe('calculateFlashLoanFee', () => {
    it('calculates fee correctly', () => {
      const publicClient = createMockPublicClient();
      const walletClient = createMockWalletClient();

      const executor = new FlashLoanExecutor({
        publicClient,
        walletClient,
        flashLoanPremiumBps: 5,
      });

      const fee = executor.calculateFlashLoanFee(10000n);
      expect(fee).toBe(5n); // 10000 * 5 / 10000
    });

    it('handles large amounts', () => {
      const publicClient = createMockPublicClient();
      const walletClient = createMockWalletClient();

      const executor = new FlashLoanExecutor({
        publicClient,
        walletClient,
        flashLoanPremiumBps: 5,
      });

      const fee = executor.calculateFlashLoanFee(1000000000000n); // 1M USDC
      expect(fee).toBe(500000000n); // 500 USDC
    });
  });

  describe('gas estimation', () => {
    it('estimates gas before submission', async () => {
      const publicClient = createMockPublicClient({ estimateGas: 500000n });
      const walletClient = createMockWalletClient();

      const executor = new FlashLoanExecutor({
        publicClient,
        walletClient,
        callbackContractAddress: TEST_CALLBACK,
      });

      const result = await executor.execute(createTestParams());

      // 500000 * 110 / 100 = 550000
      expect(result.gasEstimate).toBe(550000n);
    });

    it('falls back to 800k gas default on estimation failure', async () => {
      const publicClient = createMockPublicClient();
      publicClient.estimateGas = vi.fn().mockRejectedValue(new Error('complex TX'));
      const walletClient = createMockWalletClient();

      const executor = new FlashLoanExecutor({
        publicClient,
        walletClient,
        callbackContractAddress: TEST_CALLBACK,
      });

      const result = await executor.execute(createTestParams());

      expect(result.gasEstimate).toBe(800000n);
    });
  });

  describe('encoding helpers', () => {
    it('encodes callback parameters', () => {
      const publicClient = createMockPublicClient();
      const walletClient = createMockWalletClient();

      const executor = new FlashLoanExecutor({
        publicClient,
        walletClient,
        callbackContractAddress: TEST_CALLBACK,
      });

      const params = createTestParams();
      const encoded = executor.encodeCallbackParams(params);

      expect(encoded).toBeDefined();
      expect(encoded.startsWith('0x')).toBe(true);
    });

    it('encodes full flash loan call', () => {
      const publicClient = createMockPublicClient();
      const walletClient = createMockWalletClient();

      const executor = new FlashLoanExecutor({
        publicClient,
        walletClient,
        callbackContractAddress: TEST_CALLBACK,
      });

      const params = createTestParams();
      const encoded = executor.encodeFlashLoan(params);

      expect(encoded).toBeDefined();
      expect(encoded.startsWith('0x')).toBe(true);
    });
  });

  describe('smart wallet integration', () => {
    it('uses smart wallet for flash loan execution', async () => {
      const publicClient = createMockPublicClient();
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
        sendUserOp: vi.fn().mockResolvedValue('0xuseropflash' as `0x${string}`),
      } as any;

      const executor = new FlashLoanExecutor({
        publicClient,
        smartWallet: mockSmartWallet,
        callbackContractAddress: TEST_CALLBACK,
      });

      const result = await executor.execute(createTestParams());

      expect(mockSmartWallet.buildUserOp).toHaveBeenCalled();
      expect(result.txHash).toBe('0xuseropflash');
    });
  });

  describe('error handling', () => {
    it('throws when no wallet configured', async () => {
      const publicClient = createMockPublicClient();
      const executor = new FlashLoanExecutor({
        publicClient,
        callbackContractAddress: TEST_CALLBACK,
      });

      await expect(executor.execute(createTestParams())).rejects.toThrow('No wallet');
    });
  });

  describe('atomicity', () => {
    it('transaction reverts on failed receipt (atomic protection)', async () => {
      const publicClient = createMockPublicClient({ receiptStatus: 'reverted' });
      const walletClient = createMockWalletClient();

      const executor = new FlashLoanExecutor({
        publicClient,
        walletClient,
        callbackContractAddress: TEST_CALLBACK,
      });

      const result = await executor.execute(createTestParams());

      // TX was submitted but reverted on-chain (atomic revert)
      expect(result.txHash).toBe('0xtx123');
      expect(result.success).toBe(false);
    });
  });
});
