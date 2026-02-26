import { describe, it, expect, vi, beforeEach } from 'vitest';
import { LidoAdapter, type StakingAPY, type QueueStatus, type RebasingPosition } from '../src/execution/lido-adapter.js';
import { type Address } from 'viem';

// ── Test helpers ──────────────────────────────────

const TEST_WALLET: Address = '0x70997970C51812dc3A010C7d01b50e0d17dc79C8';
const TEST_STETH: Address = '0x3e3FE7dBc6B4C189E7128855dD526361c49b40Af';
const TEST_WSTETH: Address = '0xB82381A3fBD3FaFA77B3a7bE693342618240067b';
const TEST_WITHDRAWAL_QUEUE: Address = '0x1583C7b3f4C3B008720E6BcE5726336b0aB25fdd';

function createMockPublicClient(opts: {
  allowance?: bigint;
  estimateGas?: bigint;
  totalPooledEther?: bigint;
  totalShares?: bigint;
  shares?: bigint;
  stethBalance?: bigint;
  wstethBalance?: bigint;
  stEthPerToken?: bigint;
  lastRequestId?: bigint;
  lastFinalizedRequestId?: bigint;
  unfinalizedStETH?: bigint;
  getPooledEthByShares?: bigint;
  getSharesByPooledEth?: bigint;
  getStETHByWstETH?: bigint;
  getWstETHByStETH?: bigint;
} = {}) {
  return {
    readContract: vi.fn().mockImplementation(async ({ functionName, args }: any) => {
      if (functionName === 'allowance') {
        return opts.allowance ?? 0n;
      }
      if (functionName === 'getTotalPooledEther') {
        return opts.totalPooledEther ?? 9000000000000000000000000n; // 9M ETH
      }
      if (functionName === 'getTotalShares') {
        return opts.totalShares ?? 8500000000000000000000000n; // 8.5M shares
      }
      if (functionName === 'sharesOf') {
        return opts.shares ?? 1000000000000000000n; // 1 share
      }
      if (functionName === 'balanceOf') {
        return opts.stethBalance ?? opts.wstethBalance ?? 1058823529411764706n;
      }
      if (functionName === 'stEthPerToken') {
        return opts.stEthPerToken ?? 1058823529411764706n; // ~1.0588 stETH per wstETH
      }
      if (functionName === 'tokensPerStEth') {
        return 944444444444444444n;
      }
      if (functionName === 'getLastRequestId') {
        return opts.lastRequestId ?? 1000n;
      }
      if (functionName === 'getLastFinalizedRequestId') {
        return opts.lastFinalizedRequestId ?? 990n;
      }
      if (functionName === 'unfinalizedStETH') {
        return opts.unfinalizedStETH ?? 500000000000000000000n; // 500 stETH
      }
      if (functionName === 'getPooledEthByShares') {
        return opts.getPooledEthByShares ?? 1058823529411764706n;
      }
      if (functionName === 'getSharesByPooledEth') {
        return opts.getSharesByPooledEth ?? 944444444444444444n;
      }
      if (functionName === 'getStETHByWstETH') {
        return opts.getStETHByWstETH ?? 1058823529411764706n;
      }
      if (functionName === 'getWstETHByStETH') {
        return opts.getWstETHByStETH ?? 944444444444444444n;
      }
      return 0n;
    }),
    estimateGas: vi.fn().mockResolvedValue(opts.estimateGas ?? 200000n),
    waitForTransactionReceipt: vi.fn().mockResolvedValue({
      status: 'success',
      blockNumber: 100n,
      gasUsed: 180000n,
    }),
    getGasPrice: vi.fn().mockResolvedValue(30000000000n),
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

describe('LidoAdapter', () => {
  describe('constructor', () => {
    it('creates with default Sepolia addresses', () => {
      const publicClient = createMockPublicClient();
      const adapter = new LidoAdapter({ publicClient });

      expect(adapter.steth).toBe(TEST_STETH);
      expect(adapter.wsteth).toBe(TEST_WSTETH);
      expect(adapter.withdrawalQueue).toBe(TEST_WITHDRAWAL_QUEUE);
    });

    it('accepts custom addresses', () => {
      const customSteth = '0xCustomStETH12345678901234567890abcdef1234' as Address;
      const publicClient = createMockPublicClient();
      const adapter = new LidoAdapter({ publicClient, stethAddress: customSteth });

      expect(adapter.steth).toBe(customSteth);
    });
  });

  describe('stake', () => {
    it('stakes ETH via Lido and receives stETH', async () => {
      const publicClient = createMockPublicClient();
      const walletClient = createMockWalletClient();
      const logs: Array<{ event: string }> = [];

      const adapter = new LidoAdapter({
        publicClient,
        walletClient,
        onLog: (event) => logs.push({ event }),
      });

      const result = await adapter.stake(1000000000000000000n); // 1 ETH

      expect(result.txHash).toBe('0xtx123');
      expect(result.ethStaked).toBe(1000000000000000000n);
      expect(result.gasUsed).toBeDefined();
      expect(result.gasEstimate).toBeDefined();
      expect(logs.some((l) => l.event === 'lido_stake_start')).toBe(true);
      expect(logs.some((l) => l.event === 'lido_stake_complete')).toBe(true);

      // Verify ETH value was passed in the transaction
      expect(walletClient.sendTransaction).toHaveBeenCalledWith(
        expect.objectContaining({
          value: 1000000000000000000n,
        })
      );
    });
  });

  describe('wrap', () => {
    it('wraps stETH to wstETH with approval', async () => {
      const publicClient = createMockPublicClient({ allowance: 0n });
      const walletClient = createMockWalletClient();
      const logs: Array<{ event: string }> = [];

      const adapter = new LidoAdapter({
        publicClient,
        walletClient,
        onLog: (event) => logs.push({ event }),
      });

      const result = await adapter.wrap(1000000000000000000n);

      expect(result.txHash).toBe('0xtx123');
      expect(result.stethWrapped).toBe(1000000000000000000n);
      // approve + wrap = 2 calls
      expect(walletClient.sendTransaction).toHaveBeenCalledTimes(2);
      expect(logs.some((l) => l.event === 'lido_wrap_start')).toBe(true);
      expect(logs.some((l) => l.event === 'lido_wrap_complete')).toBe(true);
    });

    it('skips approval when allowance sufficient', async () => {
      const publicClient = createMockPublicClient({ allowance: 2000000000000000000n });
      const walletClient = createMockWalletClient();

      const adapter = new LidoAdapter({ publicClient, walletClient });

      await adapter.wrap(1000000000000000000n);

      // Only 1 call (wrap, no approve)
      expect(walletClient.sendTransaction).toHaveBeenCalledTimes(1);
    });
  });

  describe('unwrap', () => {
    it('unwraps wstETH back to stETH', async () => {
      const publicClient = createMockPublicClient();
      const walletClient = createMockWalletClient();
      const logs: Array<{ event: string }> = [];

      const adapter = new LidoAdapter({
        publicClient,
        walletClient,
        onLog: (event) => logs.push({ event }),
      });

      const result = await adapter.unwrap(1000000000000000000n);

      expect(result.txHash).toBe('0xtx123');
      expect(result.wstethUnwrapped).toBe(1000000000000000000n);
      expect(logs.some((l) => l.event === 'lido_unwrap_start')).toBe(true);
      expect(logs.some((l) => l.event === 'lido_unwrap_complete')).toBe(true);
    });
  });

  describe('getStakingAPY', () => {
    it('queries staking APY and protocol stats', async () => {
      const publicClient = createMockPublicClient();
      const logs: Array<{ event: string }> = [];

      const adapter = new LidoAdapter({
        publicClient,
        onLog: (event) => logs.push({ event }),
      });

      const apy = await adapter.getStakingAPY();

      expect(apy.totalPooledEther).toBeGreaterThan(0n);
      expect(apy.totalShares).toBeGreaterThan(0n);
      expect(apy.exchangeRate).toBeGreaterThan(0);
      expect(typeof apy.apy).toBe('number');
      expect(logs.some((l) => l.event === 'lido_apy_query')).toBe(true);
    });
  });

  describe('getQueueStatus', () => {
    it('queries withdrawal queue status', async () => {
      const publicClient = createMockPublicClient();
      const logs: Array<{ event: string }> = [];

      const adapter = new LidoAdapter({
        publicClient,
        onLog: (event) => logs.push({ event }),
      });

      const status = await adapter.getQueueStatus();

      expect(status.lastRequestId).toBe(1000n);
      expect(status.lastFinalizedRequestId).toBe(990n);
      expect(status.pendingRequests).toBe(10n);
      expect(status.unfinalizedStETH).toBe(500000000000000000000n);
      expect(logs.some((l) => l.event === 'lido_queue_status')).toBe(true);
    });
  });

  describe('rebasing position tracking', () => {
    it('gets rebasing position with shares and balances', async () => {
      const publicClient = createMockPublicClient({
        shares: 1000000000000000000n,
        stethBalance: 1058823529411764706n,
        wstethBalance: 500000000000000000n,
        stEthPerToken: 1058823529411764706n,
      });
      const logs: Array<{ event: string }> = [];

      const adapter = new LidoAdapter({
        publicClient,
        onLog: (event) => logs.push({ event }),
      });

      const position = await adapter.getRebasingPosition(TEST_WALLET);

      expect(position.shares).toBe(1000000000000000000n);
      expect(position.stethBalance).toBe(1058823529411764706n);
      expect(position.exchangeRate).toBeGreaterThan(0);
      expect(logs.some((l) => l.event === 'lido_rebasing_position')).toBe(true);
    });

    it('converts shares to stETH correctly', async () => {
      const publicClient = createMockPublicClient({
        getPooledEthByShares: 1058823529411764706n,
      });

      const adapter = new LidoAdapter({ publicClient });
      const steth = await adapter.sharesToSteth(1000000000000000000n);

      expect(steth).toBe(1058823529411764706n);
    });

    it('converts stETH to shares correctly', async () => {
      const publicClient = createMockPublicClient({
        getSharesByPooledEth: 944444444444444444n,
      });

      const adapter = new LidoAdapter({ publicClient });
      const shares = await adapter.stethToShares(1000000000000000000n);

      expect(shares).toBe(944444444444444444n);
    });

    it('converts wstETH to stETH correctly', async () => {
      const publicClient = createMockPublicClient({
        getStETHByWstETH: 1058823529411764706n,
      });

      const adapter = new LidoAdapter({ publicClient });
      const steth = await adapter.wstethToSteth(1000000000000000000n);

      expect(steth).toBe(1058823529411764706n);
    });

    it('converts stETH to wstETH correctly', async () => {
      const publicClient = createMockPublicClient({
        getWstETHByStETH: 944444444444444444n,
      });

      const adapter = new LidoAdapter({ publicClient });
      const wsteth = await adapter.stethToWsteth(1000000000000000000n);

      expect(wsteth).toBe(944444444444444444n);
    });
  });

  describe('gas estimation', () => {
    it('adds 10% buffer to gas estimate', async () => {
      const publicClient = createMockPublicClient({ estimateGas: 200000n });
      const walletClient = createMockWalletClient();

      const adapter = new LidoAdapter({ publicClient, walletClient });

      const result = await adapter.stake(1000000000000000000n);
      expect(result.gasEstimate).toBe(220000n);
    });

    it('falls back to default gas on estimation failure', async () => {
      const publicClient = createMockPublicClient();
      publicClient.estimateGas = vi.fn().mockRejectedValue(new Error('estimation failed'));
      const walletClient = createMockWalletClient();

      const adapter = new LidoAdapter({ publicClient, walletClient });

      const result = await adapter.stake(1000000000000000000n);
      expect(result.gasEstimate).toBe(300000n);
    });
  });

  describe('encoding helpers', () => {
    it('encodes stake call data', () => {
      const publicClient = createMockPublicClient();
      const adapter = new LidoAdapter({ publicClient });

      const data = adapter.encodeStake();
      expect(data).toBeDefined();
      expect(data.startsWith('0x')).toBe(true);
    });

    it('encodes wrap call data', () => {
      const publicClient = createMockPublicClient();
      const adapter = new LidoAdapter({ publicClient });

      const data = adapter.encodeWrap(1000000000000000000n);
      expect(data).toBeDefined();
      expect(data.startsWith('0x')).toBe(true);
    });

    it('encodes unwrap call data', () => {
      const publicClient = createMockPublicClient();
      const adapter = new LidoAdapter({ publicClient });

      const data = adapter.encodeUnwrap(1000000000000000000n);
      expect(data).toBeDefined();
      expect(data.startsWith('0x')).toBe(true);
    });
  });

  describe('smart wallet integration', () => {
    it('uses smart wallet for staking when available', async () => {
      const publicClient = createMockPublicClient();
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
        sendUserOp: vi.fn().mockResolvedValue('0xuserop789' as `0x${string}`),
      } as any;

      const adapter = new LidoAdapter({
        publicClient,
        smartWallet: mockSmartWallet,
      });

      const result = await adapter.stake(1000000000000000000n);

      expect(mockSmartWallet.buildUserOp).toHaveBeenCalled();
      expect(result.txHash).toBe('0xuserop789');
    });
  });

  describe('error handling', () => {
    it('throws when no wallet configured', async () => {
      const publicClient = createMockPublicClient();
      const adapter = new LidoAdapter({ publicClient });

      await expect(adapter.stake(1000000000000000000n)).rejects.toThrow('No wallet');
    });
  });

  describe('testnet compatibility', () => {
    it('uses Sepolia addresses by default', () => {
      const publicClient = createMockPublicClient();
      const adapter = new LidoAdapter({ publicClient });

      // Sepolia addresses for Lido
      expect(adapter.steth).toBe(TEST_STETH);
      expect(adapter.wsteth).toBe(TEST_WSTETH);
      expect(adapter.withdrawalQueue).toBe(TEST_WITHDRAWAL_QUEUE);
    });
  });
});
