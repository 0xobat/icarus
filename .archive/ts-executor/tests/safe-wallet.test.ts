import { describe, it, expect, vi, beforeEach } from 'vitest';
import type { Address, Hex } from 'viem';
import { parseEther } from 'viem';

// ── Hoisted mocks (available inside vi.mock factories) ──

const {
  mockCreateTransaction,
  mockExecuteTransaction,
  mockGetAddress,
  mockSafeInit,
  mockWaitForTransactionReceipt,
  mockGetBalance,
  mockReadContract,
} = vi.hoisted(() => ({
  mockCreateTransaction: vi.fn(),
  mockExecuteTransaction: vi.fn(),
  mockGetAddress: vi.fn(),
  mockSafeInit: vi.fn(),
  mockWaitForTransactionReceipt: vi.fn(),
  mockGetBalance: vi.fn(),
  mockReadContract: vi.fn(),
}));

// ── Module mocks ──

vi.mock('@safe-global/protocol-kit', () => ({
  default: { init: mockSafeInit },
}));

vi.mock('@safe-global/types-kit', () => ({
  OperationType: { Call: 0, DelegateCall: 1 },
}));

vi.mock('viem', async () => {
  const actual = await vi.importActual<typeof import('viem')>('viem');
  return {
    ...actual,
    createPublicClient: vi.fn().mockReturnValue({
      waitForTransactionReceipt: mockWaitForTransactionReceipt,
      getBalance: mockGetBalance,
      readContract: mockReadContract,
    }),
  };
});

import { SafeWalletManager, type SafeWalletOptions } from '../src/wallet/safe-wallet.js';

// ── Test Helpers ──────────────────────────────────

const TEST_PRIVATE_KEY = '0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80';
const TEST_SAFE_ADDRESS = '0x70997970C51812dc3A010C7d01b50e0d17dc79C8' as Address;
const TEST_RECOVERY_ADDRESS = '0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC' as Address;
const TEST_CONTRACT = '0x1234567890abcdef1234567890abcdef12345678' as Address;
const TEST_CONTRACT_2 = '0xabcdef1234567890abcdef1234567890abcdef12' as Address;

const MOCK_TX_HASH = '0xdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef' as `0x${string}`;

function setupMocks() {
  mockCreateTransaction.mockResolvedValue({ data: {} });
  mockExecuteTransaction.mockResolvedValue({
    hash: MOCK_TX_HASH,
    transactionResponse: {},
  });
  mockGetAddress.mockResolvedValue(TEST_SAFE_ADDRESS);
  mockSafeInit.mockResolvedValue({
    createTransaction: mockCreateTransaction,
    executeTransaction: mockExecuteTransaction,
    getAddress: mockGetAddress,
  });
  mockWaitForTransactionReceipt.mockResolvedValue({
    transactionHash: MOCK_TX_HASH,
    blockNumber: 100n,
    status: 'success',
  });
  mockGetBalance.mockResolvedValue(parseEther('10'));
  mockReadContract.mockResolvedValue(0n);
}

async function createWallet(opts: Partial<SafeWalletOptions> = {}): Promise<SafeWalletManager> {
  return SafeWalletManager.create({
    privateKey: TEST_PRIVATE_KEY,
    safeAddress: TEST_SAFE_ADDRESS,
    rpcUrl: 'http://localhost:8545',
    onLog: () => {},
    ...opts,
  });
}

// ── Tests ──────────────────────────────────────────

describe('SafeWalletManager', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setupMocks();
  });

  describe('factory', () => {
    it('creates SafeWalletManager with correct config', async () => {
      const wallet = await createWallet();

      expect(wallet.address).toBe(TEST_SAFE_ADDRESS);
      expect(wallet.signerAddress).toMatch(/^0x[0-9a-fA-F]{40}$/);
      expect(mockSafeInit).toHaveBeenCalledWith({
        provider: 'http://localhost:8545',
        signer: TEST_PRIVATE_KEY,
        safeAddress: TEST_SAFE_ADDRESS,
      });
    });

    it('throws when WALLET_PRIVATE_KEY is not set', async () => {
      await expect(SafeWalletManager.create({
        privateKey: '',
        safeAddress: TEST_SAFE_ADDRESS,
        rpcUrl: 'http://localhost:8545',
      })).rejects.toThrow('WALLET_PRIVATE_KEY is not configured');
    });

    it('deploys new Safe when no safeAddress provided', async () => {
      await SafeWalletManager.create({
        privateKey: TEST_PRIVATE_KEY,
        safeAddress: undefined,
        recoveryAddress: TEST_RECOVERY_ADDRESS,
        rpcUrl: 'http://localhost:8545',
        onLog: () => {},
      });

      expect(mockSafeInit).toHaveBeenCalledWith(expect.objectContaining({
        predictedSafe: expect.objectContaining({
          safeAccountConfig: expect.objectContaining({
            threshold: 1,
          }),
        }),
      }));
    });

    it('throws when deploying without recovery address', async () => {
      await expect(SafeWalletManager.create({
        privateKey: TEST_PRIVATE_KEY,
        safeAddress: undefined,
        recoveryAddress: undefined,
        rpcUrl: 'http://localhost:8545',
      })).rejects.toThrow('SAFE_RECOVERY_ADDRESS is required');
    });
  });

  describe('executeTransaction', () => {
    it('calls protocolKit.createTransaction + executeTransaction', async () => {
      const wallet = await createWallet();

      const result = await wallet.executeTransaction({
        to: TEST_CONTRACT,
        value: parseEther('0.1'),
        data: '0xdeadbeef' as Hex,
      });

      expect(mockCreateTransaction).toHaveBeenCalledWith({
        transactions: [{
          to: TEST_CONTRACT,
          value: String(parseEther('0.1')),
          data: '0xdeadbeef',
          operation: 0,
        }],
      });
      expect(mockExecuteTransaction).toHaveBeenCalled();
      expect(result.hash).toBe(MOCK_TX_HASH);
      expect(result.receipt).toBeDefined();
    });

    it('defaults value to 0 and data to 0x', async () => {
      const wallet = await createWallet();

      await wallet.executeTransaction({ to: TEST_CONTRACT });

      expect(mockCreateTransaction).toHaveBeenCalledWith({
        transactions: [{
          to: TEST_CONTRACT,
          value: '0',
          data: '0x',
          operation: 0,
        }],
      });
    });
  });

  describe('executeBatch', () => {
    it('creates multi-transaction and executes', async () => {
      const wallet = await createWallet();

      const result = await wallet.executeBatch([
        { to: TEST_CONTRACT, value: parseEther('0.1'), data: '0xaa' as Hex },
        { to: TEST_CONTRACT_2, value: 0n, data: '0xbb' as Hex },
      ]);

      expect(mockCreateTransaction).toHaveBeenCalledWith({
        transactions: [
          { to: TEST_CONTRACT, value: String(parseEther('0.1')), data: '0xaa', operation: 0 },
          { to: TEST_CONTRACT_2, value: '0', data: '0xbb', operation: 0 },
        ],
      });
      expect(mockExecuteTransaction).toHaveBeenCalled();
      expect(result.hash).toBe(MOCK_TX_HASH);
      expect(result.receipt).toBeDefined();
    });
  });

  describe('spending limits', () => {
    it('rejects per-tx cap exceeded', async () => {
      const wallet = await createWallet({
        allowlist: new Set([TEST_CONTRACT.toLowerCase() as Address]),
        perTxCapWei: parseEther('1'),
        dailyCapWei: parseEther('10'),
      });

      const result = await wallet.validateOrder(TEST_CONTRACT, parseEther('1.5'));
      expect(result.allowed).toBe(false);
      expect(result.reason).toContain('per-tx cap');
    });

    it('rejects daily cap exceeded', async () => {
      const wallet = await createWallet({
        allowlist: new Set([TEST_CONTRACT.toLowerCase() as Address]),
        perTxCapWei: parseEther('5'),
        dailyCapWei: parseEther('2'),
      });

      // First spend: 1.5 ETH
      await wallet.recordSpend(parseEther('1.5'));

      // Second would exceed daily: 1.5 + 1 = 2.5 > 2
      const result = await wallet.validateOrder(TEST_CONTRACT, parseEther('1'));
      expect(result.allowed).toBe(false);
      expect(result.reason).toContain('Daily spend');
    });

    it('resets daily counter on new UTC day', async () => {
      const wallet = await createWallet({
        allowlist: new Set([TEST_CONTRACT.toLowerCase() as Address]),
        perTxCapWei: parseEther('5'),
        dailyCapWei: parseEther('2'),
      });

      // Spend today
      await wallet.recordSpend(parseEther('1.5'));

      // Simulate day change
      const tomorrow = new Date();
      tomorrow.setUTCDate(tomorrow.getUTCDate() + 1);
      const originalToISOString = Date.prototype.toISOString;
      Date.prototype.toISOString = vi.fn().mockReturnValue(tomorrow.toISOString());

      // Should be allowed again after reset
      const result = await wallet.validateOrder(TEST_CONTRACT, parseEther('1.5'));
      expect(result.allowed).toBe(true);

      // Restore
      Date.prototype.toISOString = originalToISOString;
    });
  });

  describe('allowlist', () => {
    it('rejects when not allowlisted', async () => {
      const wallet = await createWallet({
        allowlist: new Set([TEST_CONTRACT.toLowerCase() as Address]),
        perTxCapWei: parseEther('10'),
        dailyCapWei: parseEther('100'),
      });

      const result = await wallet.validateOrder(TEST_CONTRACT_2, parseEther('1'));
      expect(result.allowed).toBe(false);
      expect(result.reason).toContain('not on the allowlist');
    });

    it('passes when allowlisted (case-insensitive)', async () => {
      const wallet = await createWallet({
        allowlist: new Set([TEST_CONTRACT.toLowerCase() as Address]),
        perTxCapWei: parseEther('10'),
        dailyCapWei: parseEther('100'),
      });

      // Pass the mixed-case version — should still match
      const result = await wallet.validateOrder(TEST_CONTRACT, parseEther('1'));
      expect(result.allowed).toBe(true);
    });

    it('rejects all when allowlist is empty (fail-closed)', async () => {
      const wallet = await createWallet({
        allowlist: new Set<Address>(),
        perTxCapWei: parseEther('10'),
        dailyCapWei: parseEther('100'),
      });

      const result = await wallet.validateOrder(TEST_CONTRACT, parseEther('1'));
      expect(result.allowed).toBe(false);
      expect(result.reason).toContain('fail-closed');

      const result2 = await wallet.validateOrder(TEST_CONTRACT_2, parseEther('1'));
      expect(result2.allowed).toBe(false);
      expect(result2.reason).toContain('CONTRACT_ALLOWLIST');
    });
  });

  describe('validateOrder combined', () => {
    it('checks allowlist then spending limits', async () => {
      const wallet = await createWallet({
        allowlist: new Set([TEST_CONTRACT.toLowerCase() as Address]),
        perTxCapWei: parseEther('0.5'),
        dailyCapWei: parseEther('100'),
      });

      // Allowlisted but exceeds per-tx cap
      const result = await wallet.validateOrder(TEST_CONTRACT, parseEther('1'));
      expect(result.allowed).toBe(false);
      expect(result.reason).toContain('per-tx cap');
    });

    it('allows when both allowlist and limits pass', async () => {
      const wallet = await createWallet({
        allowlist: new Set([TEST_CONTRACT.toLowerCase() as Address]),
        perTxCapWei: parseEther('5'),
        dailyCapWei: parseEther('100'),
      });

      const result = await wallet.validateOrder(TEST_CONTRACT, parseEther('1'));
      expect(result.allowed).toBe(true);
    });
  });

  describe('recordSpend', () => {
    it('tracks daily total', async () => {
      const logs: Array<{ event: string; extra?: Record<string, unknown> }> = [];
      const wallet = await createWallet({
        allowlist: new Set([TEST_CONTRACT.toLowerCase() as Address]),
        perTxCapWei: parseEther('10'),
        dailyCapWei: parseEther('5'),
        onLog: (event, _msg, extra) => logs.push({ event, extra }),
      });

      await wallet.recordSpend(parseEther('1'));
      await wallet.recordSpend(parseEther('2'));

      // Third spend would push over daily limit
      const result = await wallet.validateOrder(TEST_CONTRACT, parseEther('3'));
      expect(result.allowed).toBe(false);
      expect(result.reason).toContain('Daily spend');

      // Log events were recorded
      const spendLogs = logs.filter(l => l.event === 'safe_spend_recorded');
      expect(spendLogs).toHaveLength(2);
    });
  });

  describe('getters', () => {
    it('address returns the Safe contract address', async () => {
      const wallet = await createWallet();
      expect(wallet.address).toBe(TEST_SAFE_ADDRESS);
    });

    it('signerAddress returns the agent EOA address', async () => {
      const wallet = await createWallet();
      expect(wallet.signerAddress).toMatch(/^0x[0-9a-fA-F]{40}$/);
      // Derived from TEST_PRIVATE_KEY (Hardhat #0)
      expect(wallet.signerAddress.toLowerCase()).toBe(
        '0xf39fd6e51aad88f6f4ce6ab8827279cfffb92266',
      );
    });
  });

  describe('CONTRACT_ALLOWLIST env var', () => {
    it('loads allowlist from env var (comma-separated)', async () => {
      process.env.CONTRACT_ALLOWLIST = `${TEST_CONTRACT},${TEST_CONTRACT_2}`;
      try {
        const wallet = await createWallet({
          allowlist: undefined,
          perTxCapWei: parseEther('10'),
          dailyCapWei: parseEther('100'),
        });

        const result = await wallet.validateOrder(TEST_CONTRACT, parseEther('1'));
        expect(result.allowed).toBe(true);

        const result2 = await wallet.validateOrder(TEST_CONTRACT_2, parseEther('1'));
        expect(result2.allowed).toBe(true);
      } finally {
        delete process.env.CONTRACT_ALLOWLIST;
      }
    });

    it('rejects non-listed contract when env var is set', async () => {
      process.env.CONTRACT_ALLOWLIST = TEST_CONTRACT;
      try {
        const wallet = await createWallet({
          allowlist: undefined,
          perTxCapWei: parseEther('10'),
          dailyCapWei: parseEther('100'),
        });

        const result = await wallet.validateOrder(TEST_CONTRACT_2, parseEther('1'));
        expect(result.allowed).toBe(false);
        expect(result.reason).toContain('not on the allowlist');
      } finally {
        delete process.env.CONTRACT_ALLOWLIST;
      }
    });

    it('rejects all when env var is empty (fail-closed)', async () => {
      process.env.CONTRACT_ALLOWLIST = '';
      try {
        const wallet = await createWallet({
          allowlist: undefined,
          perTxCapWei: parseEther('10'),
          dailyCapWei: parseEther('100'),
        });

        const result = await wallet.validateOrder(TEST_CONTRACT, parseEther('1'));
        expect(result.allowed).toBe(false);
        expect(result.reason).toContain('fail-closed');
      } finally {
        delete process.env.CONTRACT_ALLOWLIST;
      }
    });

    it('rejects all when env var is unset (fail-closed)', async () => {
      delete process.env.CONTRACT_ALLOWLIST;
      const wallet = await createWallet({
        allowlist: undefined,
        perTxCapWei: parseEther('10'),
        dailyCapWei: parseEther('100'),
      });

      const result = await wallet.validateOrder(TEST_CONTRACT, parseEther('1'));
      expect(result.allowed).toBe(false);
      expect(result.reason).toContain('fail-closed');
    });

    it('handles spaces in env var gracefully', async () => {
      process.env.CONTRACT_ALLOWLIST = ` ${TEST_CONTRACT} , ${TEST_CONTRACT_2} `;
      try {
        const wallet = await createWallet({
          allowlist: undefined,
          perTxCapWei: parseEther('10'),
          dailyCapWei: parseEther('100'),
        });

        const result = await wallet.validateOrder(TEST_CONTRACT, parseEther('1'));
        expect(result.allowed).toBe(true);
      } finally {
        delete process.env.CONTRACT_ALLOWLIST;
      }
    });
  });

  describe('logging security', () => {
    it('private key never appears in logs', async () => {
      const logs: Array<{ event: string; message: string; extra?: Record<string, unknown> }> = [];
      const wallet = await createWallet({
        onLog: (event, message, extra) => logs.push({ event, message, extra }),
      });

      // Trigger some log events
      await wallet.recordSpend(parseEther('1'));
      await wallet.validateOrder(TEST_CONTRACT, parseEther('0.1'));

      const allLogText = JSON.stringify(logs);
      expect(allLogText).not.toContain(TEST_PRIVATE_KEY);
      expect(allLogText).not.toContain(TEST_PRIVATE_KEY.slice(2)); // Without 0x prefix
    });
  });
});
