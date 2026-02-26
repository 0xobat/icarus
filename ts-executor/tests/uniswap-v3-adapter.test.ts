import { describe, it, expect, vi, beforeEach } from 'vitest';
import { UniswapV3Adapter, type PoolState, type PositionInfo } from '../src/execution/uniswap-v3-adapter.js';
import { type Address } from 'viem';

// ── Test helpers ──────────────────────────────────

const TEST_TOKEN0: Address = '0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48';
const TEST_TOKEN1: Address = '0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2';
const TEST_POOL: Address = '0x8ad599c3A0ff1De082011EFDDc58f1908eb6e6D8';
const TEST_WALLET: Address = '0x70997970C51812dc3A010C7d01b50e0d17dc79C8';
const TEST_POSITION_MANAGER: Address = '0x1238536071E1c677A632429e3655c799b22cDA52';
const TEST_FACTORY: Address = '0x0227628f3F023bb0B980b67D528571c95c6DaC1c';

const Q96 = 2n ** 96n;
// sqrtPriceX96 for price ~1.0
const TEST_SQRT_PRICE = Q96;

function createMockPublicClient(opts: {
  allowance?: bigint;
  estimateGas?: bigint;
  slot0?: any;
  liquidity?: bigint;
  fee?: number;
  token0?: Address;
  token1?: Address;
  positionData?: any;
  poolAddress?: Address;
} = {}) {
  return {
    readContract: vi.fn().mockImplementation(async ({ functionName, args }: any) => {
      if (functionName === 'allowance') {
        return opts.allowance ?? 0n;
      }
      if (functionName === 'slot0') {
        return opts.slot0 ?? [TEST_SQRT_PRICE, -100, 0, 0, 0, 0, true];
      }
      if (functionName === 'liquidity') {
        return opts.liquidity ?? 1000000000000n;
      }
      if (functionName === 'fee') {
        return opts.fee ?? 3000;
      }
      if (functionName === 'token0') {
        return opts.token0 ?? TEST_TOKEN0;
      }
      if (functionName === 'token1') {
        return opts.token1 ?? TEST_TOKEN1;
      }
      if (functionName === 'positions') {
        return opts.positionData ?? [
          0n, // nonce
          '0x0000000000000000000000000000000000000000', // operator
          TEST_TOKEN0, // token0
          TEST_TOKEN1, // token1
          3000, // fee
          -887220, // tickLower
          887220, // tickUpper
          500000000n, // liquidity
          0n, // feeGrowthInside0
          0n, // feeGrowthInside1
          1000n, // tokensOwed0
          2000n, // tokensOwed1
        ];
      }
      if (functionName === 'ticks') {
        return [1000000n, 500000n, 0n, 0n, 0n, 0n, 0, true];
      }
      if (functionName === 'getPool') {
        return opts.poolAddress ?? TEST_POOL;
      }
      return 0n;
    }),
    estimateGas: vi.fn().mockResolvedValue(opts.estimateGas ?? 300000n),
    waitForTransactionReceipt: vi.fn().mockResolvedValue({
      status: 'success',
      blockNumber: 100n,
      gasUsed: 280000n,
      logs: [{
        topics: [
          '0xddf252ad',
          '0x0000000000000000000000000000000000000000000000000000000000000000',
          '0x00000000000000000000000070997970c51812dc3a010c7d01b50e0d17dc79c8',
          '0x0000000000000000000000000000000000000000000000000000000000000042',
        ],
        data: '0x',
      }],
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

describe('UniswapV3Adapter', () => {
  describe('constructor', () => {
    it('creates with default Sepolia addresses', () => {
      const publicClient = createMockPublicClient();
      const adapter = new UniswapV3Adapter({ publicClient });

      expect(adapter.positionManager).toBe(TEST_POSITION_MANAGER);
      expect(adapter.factory).toBe(TEST_FACTORY);
    });

    it('accepts custom addresses', () => {
      const customPM = '0xCustomPositionManager1234567890abcdef123456' as Address;
      const publicClient = createMockPublicClient();
      const adapter = new UniswapV3Adapter({ publicClient, positionManagerAddress: customPM });

      expect(adapter.positionManager).toBe(customPM);
    });

    it('defaults Flashbots to enabled', () => {
      const publicClient = createMockPublicClient();
      const adapter = new UniswapV3Adapter({ publicClient });
      expect(adapter.flashbotsEnabled).toBe(true);
    });
  });

  describe('mint', () => {
    it('approves tokens and mints a position', async () => {
      const publicClient = createMockPublicClient({ allowance: 0n });
      const walletClient = createMockWalletClient();
      const logs: Array<{ event: string }> = [];

      const adapter = new UniswapV3Adapter({
        publicClient,
        walletClient,
        onLog: (event) => logs.push({ event }),
      });

      const result = await adapter.mint({
        token0: TEST_TOKEN0,
        token1: TEST_TOKEN1,
        fee: 3000,
        tickLower: -887220,
        tickUpper: 887220,
        amount0Desired: 1000000n,
        amount1Desired: 1000000000000000000n,
      });

      expect(result.txHash).toBe('0xtx123');
      expect(result.gasUsed).toBeDefined();
      expect(result.gasEstimate).toBeDefined();
      expect(result.tokenId).toBeDefined();
      // 2 approvals + 1 mint = 3 sendTransaction calls
      expect(walletClient.sendTransaction).toHaveBeenCalledTimes(3);
      expect(logs.some((l) => l.event === 'uniswap_mint_start')).toBe(true);
      expect(logs.some((l) => l.event === 'uniswap_mint_complete')).toBe(true);
    });

    it('skips approval if allowance sufficient', async () => {
      const publicClient = createMockPublicClient({ allowance: 2000000000000000000n });
      const walletClient = createMockWalletClient();

      const adapter = new UniswapV3Adapter({ publicClient, walletClient });

      await adapter.mint({
        token0: TEST_TOKEN0,
        token1: TEST_TOKEN1,
        fee: 3000,
        tickLower: -887220,
        tickUpper: 887220,
        amount0Desired: 1000000n,
        amount1Desired: 1000000000000000000n,
      });

      // Only 1 call for mint (no approvals)
      expect(walletClient.sendTransaction).toHaveBeenCalledTimes(1);
    });
  });

  describe('increaseLiquidity', () => {
    it('increases liquidity in an existing position', async () => {
      const publicClient = createMockPublicClient({ allowance: 10000000000n });
      const walletClient = createMockWalletClient();
      const logs: Array<{ event: string }> = [];

      const adapter = new UniswapV3Adapter({
        publicClient,
        walletClient,
        onLog: (event) => logs.push({ event }),
      });

      const result = await adapter.increaseLiquidity({
        tokenId: 42n,
        amount0Desired: 500000n,
        amount1Desired: 500000000000000000n,
      });

      expect(result.txHash).toBe('0xtx123');
      expect(logs.some((l) => l.event === 'uniswap_increase_start')).toBe(true);
      expect(logs.some((l) => l.event === 'uniswap_increase_complete')).toBe(true);
    });
  });

  describe('decreaseLiquidity', () => {
    it('decreases liquidity in an existing position', async () => {
      const publicClient = createMockPublicClient();
      const walletClient = createMockWalletClient();
      const logs: Array<{ event: string }> = [];

      const adapter = new UniswapV3Adapter({
        publicClient,
        walletClient,
        onLog: (event) => logs.push({ event }),
      });

      const result = await adapter.decreaseLiquidity({
        tokenId: 42n,
        liquidity: 250000n,
      });

      expect(result.txHash).toBe('0xtx123');
      expect(logs.some((l) => l.event === 'uniswap_decrease_start')).toBe(true);
      expect(logs.some((l) => l.event === 'uniswap_decrease_complete')).toBe(true);
    });
  });

  describe('collectFees', () => {
    it('collects accumulated fees from a position', async () => {
      const publicClient = createMockPublicClient();
      const walletClient = createMockWalletClient();
      const logs: Array<{ event: string }> = [];

      const adapter = new UniswapV3Adapter({
        publicClient,
        walletClient,
        onLog: (event) => logs.push({ event }),
      });

      const result = await adapter.collectFees(42n);

      expect(result.txHash).toBe('0xtx123');
      expect(logs.some((l) => l.event === 'uniswap_collect_start')).toBe(true);
      expect(logs.some((l) => l.event === 'uniswap_collect_complete')).toBe(true);
    });
  });

  describe('burn', () => {
    it('burns (closes) a position', async () => {
      const publicClient = createMockPublicClient();
      const walletClient = createMockWalletClient();
      const logs: Array<{ event: string }> = [];

      const adapter = new UniswapV3Adapter({
        publicClient,
        walletClient,
        onLog: (event) => logs.push({ event }),
      });

      const result = await adapter.burn(42n);

      expect(result.txHash).toBe('0xtx123');
      expect(logs.some((l) => l.event === 'uniswap_burn_start')).toBe(true);
      expect(logs.some((l) => l.event === 'uniswap_burn_complete')).toBe(true);
    });
  });

  describe('getPoolState', () => {
    it('queries pool state with price, tick, and liquidity', async () => {
      const publicClient = createMockPublicClient();
      const adapter = new UniswapV3Adapter({ publicClient });

      const state = await adapter.getPoolState(TEST_POOL);

      expect(state.sqrtPriceX96).toBe(TEST_SQRT_PRICE);
      expect(state.currentTick).toBe(-100);
      expect(state.totalLiquidity).toBe(1000000000000n);
      expect(state.fee).toBe(3000);
      expect(state.token0).toBe(TEST_TOKEN0);
      expect(state.token1).toBe(TEST_TOKEN1);
      expect(state.currentPrice).toBeGreaterThan(0);
    });

    it('logs pool state query', async () => {
      const publicClient = createMockPublicClient();
      const logs: Array<{ event: string; extra?: Record<string, unknown> }> = [];

      const adapter = new UniswapV3Adapter({
        publicClient,
        onLog: (event, _msg, extra) => logs.push({ event, extra }),
      });

      await adapter.getPoolState(TEST_POOL);

      expect(logs.some((l) => l.event === 'uniswap_pool_state')).toBe(true);
    });
  });

  describe('getPosition', () => {
    it('queries position info by tokenId', async () => {
      const publicClient = createMockPublicClient();
      const adapter = new UniswapV3Adapter({ publicClient });

      const position = await adapter.getPosition(42n);

      expect(position.token0).toBe(TEST_TOKEN0);
      expect(position.token1).toBe(TEST_TOKEN1);
      expect(position.fee).toBe(3000);
      expect(position.tickLower).toBe(-887220);
      expect(position.tickUpper).toBe(887220);
      expect(position.liquidity).toBe(500000000n);
      expect(position.tokensOwed0).toBe(1000n);
      expect(position.tokensOwed1).toBe(2000n);
    });
  });

  describe('getTickInfo', () => {
    it('queries tick liquidity info', async () => {
      const publicClient = createMockPublicClient();
      const adapter = new UniswapV3Adapter({ publicClient });

      const tick = await adapter.getTickInfo(TEST_POOL, -100);

      expect(tick.liquidityGross).toBe(1000000n);
      expect(tick.liquidityNet).toBe(500000n);
      expect(tick.initialized).toBe(true);
    });
  });

  describe('getPoolAddress', () => {
    it('queries pool address from factory', async () => {
      const publicClient = createMockPublicClient({ poolAddress: TEST_POOL });
      const adapter = new UniswapV3Adapter({ publicClient });

      const pool = await adapter.getPoolAddress(TEST_TOKEN0, TEST_TOKEN1, 3000);
      expect(pool).toBe(TEST_POOL);
    });
  });

  describe('gas estimation', () => {
    it('adds 10% buffer to gas estimate', async () => {
      const publicClient = createMockPublicClient({
        estimateGas: 300000n,
        allowance: 10000000000n,
      });
      const walletClient = createMockWalletClient();

      const adapter = new UniswapV3Adapter({ publicClient, walletClient });

      const result = await adapter.mint({
        token0: TEST_TOKEN0,
        token1: TEST_TOKEN1,
        fee: 3000,
        tickLower: -887220,
        tickUpper: 887220,
        amount0Desired: 1000000n,
        amount1Desired: 1000000000000000000n,
      });

      expect(result.gasEstimate).toBe(330000n);
    });

    it('falls back to default gas on estimation failure', async () => {
      const publicClient = createMockPublicClient({ allowance: 10000000000n });
      publicClient.estimateGas = vi.fn().mockRejectedValue(new Error('estimation failed'));
      const walletClient = createMockWalletClient();

      const adapter = new UniswapV3Adapter({ publicClient, walletClient });

      const result = await adapter.mint({
        token0: TEST_TOKEN0,
        token1: TEST_TOKEN1,
        fee: 3000,
        tickLower: -887220,
        tickUpper: 887220,
        amount0Desired: 1000000n,
        amount1Desired: 1000000000000000000n,
      });

      expect(result.gasEstimate).toBe(500000n);
    });
  });

  describe('encoding helpers', () => {
    it('encodes mint call data', () => {
      const publicClient = createMockPublicClient();
      const adapter = new UniswapV3Adapter({ publicClient });

      const data = adapter.encodeMint({
        token0: TEST_TOKEN0,
        token1: TEST_TOKEN1,
        fee: 3000,
        tickLower: -887220,
        tickUpper: 887220,
        amount0Desired: 1000000n,
        amount1Desired: 1000000000000000000n,
      });

      expect(data).toBeDefined();
      expect(data.startsWith('0x')).toBe(true);
    });
  });

  describe('smart wallet integration', () => {
    it('uses smart wallet when available', async () => {
      const publicClient = createMockPublicClient({ allowance: 10000000000n });
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
        sendUserOp: vi.fn().mockResolvedValue('0xuserop456' as `0x${string}`),
      } as any;

      const adapter = new UniswapV3Adapter({
        publicClient,
        smartWallet: mockSmartWallet,
      });

      const result = await adapter.mint({
        token0: TEST_TOKEN0,
        token1: TEST_TOKEN1,
        fee: 3000,
        tickLower: -887220,
        tickUpper: 887220,
        amount0Desired: 1000000n,
        amount1Desired: 1000000000000000000n,
      });

      expect(mockSmartWallet.buildUserOp).toHaveBeenCalled();
      expect(result.txHash).toBe('0xuserop456');
    });
  });

  describe('error handling', () => {
    it('throws when no wallet configured', async () => {
      const publicClient = createMockPublicClient({ allowance: 10000000000n });
      const adapter = new UniswapV3Adapter({ publicClient });

      await expect(adapter.mint({
        token0: TEST_TOKEN0,
        token1: TEST_TOKEN1,
        fee: 3000,
        tickLower: -887220,
        tickUpper: 887220,
        amount0Desired: 1000000n,
        amount1Desired: 1000000000000000000n,
      })).rejects.toThrow('No wallet');
    });
  });
});
