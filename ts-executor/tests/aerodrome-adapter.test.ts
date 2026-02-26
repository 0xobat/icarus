import { describe, it, expect, vi, beforeEach } from 'vitest';
import { AerodromeAdapter, type PoolInfo, type PoolAPY } from '../src/execution/aerodrome-adapter.js';
import { type Address } from 'viem';

// ── Test helpers ──────────────────────────────────

const TEST_WALLET: Address = '0x70997970C51812dc3A010C7d01b50e0d17dc79C8';
const TEST_TOKEN_A: Address = '0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48';
const TEST_TOKEN_B: Address = '0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2';
const TEST_POOL: Address = '0x8ad599c3A0ff1De082011EFDDc58f1908eb6e6D8';
const TEST_GAUGE: Address = '0x1234567890abcdef1234567890abcdef12345678';
const TEST_ROUTER: Address = '0xcF77a3Ba9A5CA399B7c97c74d54e5b1Beb874E43';

function createMockPublicClient(opts: {
  allowance?: bigint;
  estimateGas?: bigint;
  reserves?: [bigint, bigint, bigint];
  token0?: Address;
  token1?: Address;
  stable?: boolean;
  totalSupply?: bigint;
  rewardRate?: bigint;
  gaugeTotalSupply?: bigint;
  earned?: bigint;
  poolForAddress?: Address;
} = {}) {
  return {
    readContract: vi.fn().mockImplementation(async ({ address, functionName, args }: any) => {
      if (functionName === 'allowance') {
        return opts.allowance ?? 0n;
      }
      if (functionName === 'getReserves') {
        return opts.reserves ?? [1000000000000000000000n, 2000000000n, 1700000000n];
      }
      if (functionName === 'token0') {
        return opts.token0 ?? TEST_TOKEN_A;
      }
      if (functionName === 'token1') {
        return opts.token1 ?? TEST_TOKEN_B;
      }
      if (functionName === 'stable') {
        return opts.stable ?? false;
      }
      if (functionName === 'totalSupply') {
        return opts.totalSupply ?? 500000000000000000000n;
      }
      if (functionName === 'rewardRate') {
        return opts.rewardRate ?? 1000000000000000000n; // 1 token/second
      }
      if (functionName === 'balanceOf') {
        return 100000000000000000000n;
      }
      if (functionName === 'earned') {
        return opts.earned ?? 5000000000000000000n; // 5 tokens earned
      }
      if (functionName === 'poolFor') {
        return opts.poolForAddress ?? TEST_POOL;
      }
      if (functionName === 'claimFees') {
        return [1000000000000000000n, 2000000n];
      }
      return 0n;
    }),
    estimateGas: vi.fn().mockResolvedValue(opts.estimateGas ?? 300000n),
    waitForTransactionReceipt: vi.fn().mockResolvedValue({
      status: 'success',
      blockNumber: 100n,
      gasUsed: 280000n,
    }),
  } as any;
}

function createMockWalletClient() {
  return {
    sendTransaction: vi.fn().mockResolvedValue('0xtx123' as `0x${string}`),
    chain: { id: 8453, name: 'Base' },
    account: { address: TEST_WALLET },
  } as any;
}

// ── Tests ──────────────────────────────────────────

describe('AerodromeAdapter', () => {
  describe('constructor', () => {
    it('creates with default Base Router address', () => {
      const publicClient = createMockPublicClient();
      const adapter = new AerodromeAdapter({ publicClient });

      expect(adapter.router).toBe(TEST_ROUTER);
    });

    it('accepts custom router address', () => {
      const customRouter = '0xCustomRouter123456789012345678901234567890' as Address;
      const publicClient = createMockPublicClient();
      const adapter = new AerodromeAdapter({ publicClient, routerAddress: customRouter });

      expect(adapter.router).toBe(customRouter);
    });
  });

  describe('addLiquidity', () => {
    it('provides liquidity to a pool with approvals', async () => {
      const publicClient = createMockPublicClient({ allowance: 0n });
      const walletClient = createMockWalletClient();
      const logs: Array<{ event: string }> = [];

      const adapter = new AerodromeAdapter({
        publicClient,
        walletClient,
        onLog: (event) => logs.push({ event }),
      });

      const result = await adapter.addLiquidity({
        tokenA: TEST_TOKEN_A,
        tokenB: TEST_TOKEN_B,
        stable: false,
        amountADesired: 1000000000000000000n,
        amountBDesired: 2000000000n,
      });

      expect(result.txHash).toBe('0xtx123');
      expect(result.gasUsed).toBeDefined();
      expect(result.gasEstimate).toBeDefined();
      // 2 approvals + 1 addLiquidity = 3 calls
      expect(walletClient.sendTransaction).toHaveBeenCalledTimes(3);
      expect(logs.some((l) => l.event === 'aerodrome_add_liquidity_start')).toBe(true);
      expect(logs.some((l) => l.event === 'aerodrome_add_liquidity_complete')).toBe(true);
    });

    it('skips approval when allowance sufficient', async () => {
      const publicClient = createMockPublicClient({ allowance: 10000000000000000000n });
      const walletClient = createMockWalletClient();

      const adapter = new AerodromeAdapter({ publicClient, walletClient });

      await adapter.addLiquidity({
        tokenA: TEST_TOKEN_A,
        tokenB: TEST_TOKEN_B,
        stable: false,
        amountADesired: 1000000000000000000n,
        amountBDesired: 2000000000n,
      });

      // Only 1 call (addLiquidity, no approvals)
      expect(walletClient.sendTransaction).toHaveBeenCalledTimes(1);
    });
  });

  describe('removeLiquidity', () => {
    it('removes liquidity from a pool', async () => {
      const publicClient = createMockPublicClient({ allowance: 0n });
      const walletClient = createMockWalletClient();
      const logs: Array<{ event: string }> = [];

      const adapter = new AerodromeAdapter({
        publicClient,
        walletClient,
        onLog: (event) => logs.push({ event }),
      });

      const result = await adapter.removeLiquidity({
        tokenA: TEST_TOKEN_A,
        tokenB: TEST_TOKEN_B,
        stable: false,
        liquidity: 100000000000000000000n,
      });

      expect(result.txHash).toBe('0xtx123');
      expect(logs.some((l) => l.event === 'aerodrome_remove_liquidity_start')).toBe(true);
      expect(logs.some((l) => l.event === 'aerodrome_remove_liquidity_complete')).toBe(true);
    });
  });

  describe('collectFees', () => {
    it('collects trading fees from a pool', async () => {
      const publicClient = createMockPublicClient();
      const walletClient = createMockWalletClient();
      const logs: Array<{ event: string }> = [];

      const adapter = new AerodromeAdapter({
        publicClient,
        walletClient,
        onLog: (event) => logs.push({ event }),
      });

      const result = await adapter.collectFees(TEST_POOL);

      expect(result.txHash).toBe('0xtx123');
      expect(logs.some((l) => l.event === 'aerodrome_collect_fees_start')).toBe(true);
      expect(logs.some((l) => l.event === 'aerodrome_collect_fees_complete')).toBe(true);
    });
  });

  describe('collectRewards', () => {
    it('collects gauge rewards for staked LP tokens', async () => {
      const publicClient = createMockPublicClient();
      const walletClient = createMockWalletClient();
      const logs: Array<{ event: string }> = [];

      const adapter = new AerodromeAdapter({
        publicClient,
        walletClient,
        onLog: (event) => logs.push({ event }),
      });

      const result = await adapter.collectRewards(TEST_GAUGE);

      expect(result.txHash).toBe('0xtx123');
      expect(logs.some((l) => l.event === 'aerodrome_collect_rewards_start')).toBe(true);
      expect(logs.some((l) => l.event === 'aerodrome_collect_rewards_complete')).toBe(true);
    });
  });

  describe('getPoolInfo', () => {
    it('queries pool reserves and info', async () => {
      const publicClient = createMockPublicClient();
      const logs: Array<{ event: string }> = [];

      const adapter = new AerodromeAdapter({
        publicClient,
        onLog: (event) => logs.push({ event }),
      });

      const info = await adapter.getPoolInfo(TEST_POOL);

      expect(info.poolAddress).toBe(TEST_POOL);
      expect(info.token0).toBe(TEST_TOKEN_A);
      expect(info.token1).toBe(TEST_TOKEN_B);
      expect(info.stable).toBe(false);
      expect(info.reserve0).toBe(1000000000000000000000n);
      expect(info.reserve1).toBe(2000000000n);
      expect(info.totalSupply).toBe(500000000000000000000n);
      expect(logs.some((l) => l.event === 'aerodrome_pool_info')).toBe(true);
    });
  });

  describe('getPoolAPY', () => {
    it('queries pool APY from gauge rewards', async () => {
      const publicClient = createMockPublicClient({
        rewardRate: 1000000000000000000n,    // 1 token/sec
        gaugeTotalSupply: 1000000000000000000000n, // 1000 staked
      });
      const logs: Array<{ event: string }> = [];

      const adapter = new AerodromeAdapter({
        publicClient,
        onLog: (event) => logs.push({ event }),
      });

      const apy = await adapter.getPoolAPY(TEST_GAUGE, TEST_POOL);

      expect(apy.poolAddress).toBe(TEST_POOL);
      expect(apy.rewardRate).toBe(1000000000000000000n);
      expect(apy.estimatedAPY).toBeGreaterThan(0);
      expect(logs.some((l) => l.event === 'aerodrome_pool_apy')).toBe(true);
    });

    it('returns zero APY when no stakers', async () => {
      const publicClient = createMockPublicClient();
      // Override totalSupply for gauge to return 0
      const originalReadContract = publicClient.readContract;
      publicClient.readContract = vi.fn().mockImplementation(async (opts: any) => {
        if (opts.functionName === 'totalSupply' && opts.address === TEST_GAUGE) {
          return 0n;
        }
        return originalReadContract(opts);
      });

      const adapter = new AerodromeAdapter({ publicClient });
      const apy = await adapter.getPoolAPY(TEST_GAUGE, TEST_POOL);

      expect(apy.estimatedAPY).toBe(0);
    });
  });

  describe('getEarnedRewards', () => {
    it('queries earned rewards for an account', async () => {
      const publicClient = createMockPublicClient({ earned: 5000000000000000000n });
      const walletClient = createMockWalletClient();

      const adapter = new AerodromeAdapter({ publicClient, walletClient });

      const earned = await adapter.getEarnedRewards(TEST_GAUGE);
      expect(earned).toBe(5000000000000000000n);
    });
  });

  describe('getPoolAddress', () => {
    it('queries pool address from router', async () => {
      const publicClient = createMockPublicClient({ poolForAddress: TEST_POOL });

      const adapter = new AerodromeAdapter({ publicClient });
      const poolAddr = await adapter.getPoolAddress(TEST_TOKEN_A, TEST_TOKEN_B, false);

      expect(poolAddr).toBe(TEST_POOL);
    });
  });

  describe('L2 gas estimation', () => {
    it('accounts for L1 data posting costs on Base', async () => {
      const publicClient = createMockPublicClient({
        estimateGas: 300000n,
        allowance: 10000000000000000000n,
      });
      const walletClient = createMockWalletClient();

      const adapter = new AerodromeAdapter({ publicClient, walletClient });

      const result = await adapter.addLiquidity({
        tokenA: TEST_TOKEN_A,
        tokenB: TEST_TOKEN_B,
        stable: false,
        amountADesired: 1000000000000000000n,
        amountBDesired: 2000000000n,
      });

      // Gas estimate should be > 300000 * 1.1 due to L1 data cost
      expect(result.gasEstimate).toBeGreaterThan(330000n);
    });

    it('falls back to default gas on estimation failure', async () => {
      const publicClient = createMockPublicClient({ allowance: 10000000000000000000n });
      publicClient.estimateGas = vi.fn().mockRejectedValue(new Error('failed'));
      const walletClient = createMockWalletClient();

      const adapter = new AerodromeAdapter({ publicClient, walletClient });

      const result = await adapter.addLiquidity({
        tokenA: TEST_TOKEN_A,
        tokenB: TEST_TOKEN_B,
        stable: false,
        amountADesired: 1000000000000000000n,
        amountBDesired: 2000000000n,
      });

      // Default: 500_000
      expect(result.gasEstimate).toBe(500000n);
    });
  });

  describe('order schema compatibility', () => {
    it('returns same result shape as Ethereum adapters', async () => {
      const publicClient = createMockPublicClient({ allowance: 10000000000000000000n });
      const walletClient = createMockWalletClient();

      const adapter = new AerodromeAdapter({ publicClient, walletClient });

      const result = await adapter.addLiquidity({
        tokenA: TEST_TOKEN_A,
        tokenB: TEST_TOKEN_B,
        stable: false,
        amountADesired: 1000000000000000000n,
        amountBDesired: 2000000000n,
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

  describe('encoding helpers', () => {
    it('encodes addLiquidity call data', () => {
      const publicClient = createMockPublicClient();
      const adapter = new AerodromeAdapter({ publicClient });

      const data = adapter.encodeAddLiquidity({
        tokenA: TEST_TOKEN_A,
        tokenB: TEST_TOKEN_B,
        stable: false,
        amountADesired: 1000000000000000000n,
        amountBDesired: 2000000000n,
      });

      expect(data).toBeDefined();
      expect(data.startsWith('0x')).toBe(true);
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
          callGasLimit: 300000n,
          verificationGasLimit: 100000n,
          preVerificationGas: 50000n,
          maxFeePerGas: 30000000000n,
          maxPriorityFeePerGas: 3000000000n,
          signature: '0x' as `0x${string}`,
        }),
        sendUserOp: vi.fn().mockResolvedValue('0xuseropaero' as `0x${string}`),
      } as any;

      const adapter = new AerodromeAdapter({
        publicClient,
        smartWallet: mockSmartWallet,
      });

      const result = await adapter.addLiquidity({
        tokenA: TEST_TOKEN_A,
        tokenB: TEST_TOKEN_B,
        stable: false,
        amountADesired: 1000000000000000000n,
        amountBDesired: 2000000000n,
      });

      expect(mockSmartWallet.buildUserOp).toHaveBeenCalled();
      expect(result.txHash).toBe('0xuseropaero');
    });
  });

  describe('error handling', () => {
    it('throws when no wallet configured', async () => {
      const publicClient = createMockPublicClient({ allowance: 10000000000000000000n });
      const adapter = new AerodromeAdapter({ publicClient });

      await expect(adapter.addLiquidity({
        tokenA: TEST_TOKEN_A,
        tokenB: TEST_TOKEN_B,
        stable: false,
        amountADesired: 1000000000000000000n,
        amountBDesired: 2000000000n,
      })).rejects.toThrow('No wallet');
    });
  });
});
