import { describe, it, expect, vi, beforeEach } from 'vitest';
import {
  SmartWalletManager,
  type SmartWalletOptions,
  type UserOperation,
} from '../src/wallet/smart-wallet.js';
import { type Address, parseEther, formatEther } from 'viem';

// ── Test Helpers ──────────────────────────────────

const TEST_PRIVATE_KEY = '0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80';
const TEST_WALLET_ADDRESS = '0x70997970C51812dc3A010C7d01b50e0d17dc79C8' as Address;
const TEST_CONTRACT = '0x1234567890abcdef1234567890abcdef12345678' as Address;
const TEST_CONTRACT_2 = '0xabcdef1234567890abcdef1234567890abcdef12' as Address;

function mockPublicClient(overrides: Record<string, unknown> = {}) {
  return {
    getBalance: vi.fn().mockResolvedValue(parseEther('10')),
    getGasPrice: vi.fn().mockResolvedValue(30_000_000_000n),
    readContract: vi.fn().mockResolvedValue(0n),
    ...overrides,
  } as any;
}

function createWallet(opts: Partial<SmartWalletOptions> = {}) {
  return new SmartWalletManager({
    privateKey: TEST_PRIVATE_KEY,
    walletAddress: TEST_WALLET_ADDRESS,
    publicClient: mockPublicClient(opts.publicClient as any ?? {}),
    onLog: () => {},
    ...opts,
  });
}

// ── Tests ──────────────────────────────────────────

describe('SmartWalletManager', () => {
  it('throws when WALLET_PRIVATE_KEY is not set', () => {
    expect(() => new SmartWalletManager({
      privateKey: '',
      publicClient: mockPublicClient(),
    })).toThrow('WALLET_PRIVATE_KEY is not configured');
  });

  it('initializes with correct addresses', () => {
    const wallet = createWallet();
    expect(wallet.address).toBe(TEST_WALLET_ADDRESS);
    expect(wallet.signerAddress).toBeDefined();
    // Signer is derived from the private key
    expect(wallet.signerAddress).toMatch(/^0x[0-9a-fA-F]{40}$/);
  });

  it('never includes private key in logs', () => {
    const logs: Array<{ event: string; extra?: Record<string, unknown> }> = [];
    createWallet({
      onLog: (event, _msg, extra) => logs.push({ event, extra }),
    });

    // Check that no log entry contains the private key
    const allLogText = JSON.stringify(logs);
    expect(allLogText).not.toContain(TEST_PRIVATE_KEY);
    expect(allLogText).not.toContain(TEST_PRIVATE_KEY.slice(2)); // Without 0x prefix
  });

  describe('spending limits', () => {
    it('allows transactions within per-tx cap', () => {
      const wallet = createWallet({
        perTxCapWei: parseEther('1'),
        dailyCapWei: parseEther('10'),
      });

      const result = wallet.checkSpendingLimit(parseEther('0.5'));
      expect(result.allowed).toBe(true);
      expect(result.reason).toBeUndefined();
    });

    it('rejects transactions exceeding per-tx cap', () => {
      const wallet = createWallet({
        perTxCapWei: parseEther('1'),
        dailyCapWei: parseEther('10'),
      });

      const result = wallet.checkSpendingLimit(parseEther('1.5'));
      expect(result.allowed).toBe(false);
      expect(result.reason).toContain('per-tx cap');
    });

    it('tracks daily spending and rejects when daily cap exceeded', () => {
      const wallet = createWallet({
        perTxCapWei: parseEther('5'),
        dailyCapWei: parseEther('2'),
      });

      // First spend: 1.5 ETH — within limits
      wallet.recordSpend(parseEther('1.5'));
      expect(wallet.dailySpentTotal).toBe(parseEther('1.5'));

      // Second spend: 1 ETH — would exceed daily cap
      const result = wallet.checkSpendingLimit(parseEther('1'));
      expect(result.allowed).toBe(false);
      expect(result.reason).toContain('Daily spend');
    });

    it('tracks remaining daily allowance', () => {
      const wallet = createWallet({
        perTxCapWei: parseEther('5'),
        dailyCapWei: parseEther('10'),
      });

      expect(wallet.dailyRemaining).toBe(parseEther('10'));

      wallet.recordSpend(parseEther('3'));
      expect(wallet.dailyRemaining).toBe(parseEther('7'));

      wallet.recordSpend(parseEther('7'));
      expect(wallet.dailyRemaining).toBe(0n);
    });

    it('resets daily counter at UTC midnight', () => {
      const wallet = createWallet({
        perTxCapWei: parseEther('5'),
        dailyCapWei: parseEther('2'),
      });

      // Spend on "today"
      wallet.recordSpend(parseEther('1.5'));
      expect(wallet.dailySpentTotal).toBe(parseEther('1.5'));

      // Simulate day change by mocking Date
      const tomorrow = new Date();
      tomorrow.setUTCDate(tomorrow.getUTCDate() + 1);
      const originalToISOString = Date.prototype.toISOString;
      Date.prototype.toISOString = vi.fn().mockReturnValue(tomorrow.toISOString());

      // Daily counter should reset
      expect(wallet.dailySpentTotal).toBe(0n);
      const result = wallet.checkSpendingLimit(parseEther('1.5'));
      expect(result.allowed).toBe(true);

      // Restore
      Date.prototype.toISOString = originalToISOString;
    });

    it('defaults to reasonable caps from env or fallback', () => {
      const wallet = createWallet();
      // Default per-tx: 0.5 ETH, daily: 2 ETH
      const withinPerTx = wallet.checkSpendingLimit(parseEther('0.4'));
      expect(withinPerTx.allowed).toBe(true);

      const exceedsPerTx = wallet.checkSpendingLimit(parseEther('0.6'));
      expect(exceedsPerTx.allowed).toBe(false);
    });
  });

  describe('contract allowlist', () => {
    it('rejects non-allowlisted contracts', () => {
      const wallet = createWallet({
        allowlist: new Set([TEST_CONTRACT.toLowerCase() as Address]),
      });

      const result = wallet.checkAllowlist(TEST_CONTRACT_2);
      expect(result).toContain('not on the allowlist');
    });

    it('accepts allowlisted contracts (case-insensitive)', () => {
      const wallet = createWallet({
        allowlist: new Set([TEST_CONTRACT.toLowerCase() as Address]),
      });

      const result = wallet.checkAllowlist(TEST_CONTRACT);
      expect(result).toBeNull();
    });

    it('allows all when allowlist is empty (no restrictions)', () => {
      const wallet = createWallet({ allowlist: new Set() });

      expect(wallet.isAllowlisted(TEST_CONTRACT)).toBe(true);
      expect(wallet.isAllowlisted(TEST_CONTRACT_2)).toBe(true);
    });

    it('supports dynamic allowlist additions', () => {
      const wallet = createWallet({
        allowlist: new Set([TEST_CONTRACT.toLowerCase() as Address]),
      });

      expect(wallet.isAllowlisted(TEST_CONTRACT_2)).toBe(false);
      wallet.addToAllowlist(TEST_CONTRACT_2);
      expect(wallet.isAllowlisted(TEST_CONTRACT_2)).toBe(true);
      expect(wallet.allowlistSize).toBe(2);
    });
  });

  describe('combined validation', () => {
    it('rejects if target is not allowlisted', () => {
      const wallet = createWallet({
        allowlist: new Set([TEST_CONTRACT.toLowerCase() as Address]),
        perTxCapWei: parseEther('10'),
        dailyCapWei: parseEther('100'),
      });

      const result = wallet.validateOrder(TEST_CONTRACT_2, parseEther('1'));
      expect(result.allowed).toBe(false);
      expect(result.reason).toContain('not on the allowlist');
    });

    it('rejects if spending limit exceeded even if allowlisted', () => {
      const wallet = createWallet({
        allowlist: new Set([TEST_CONTRACT.toLowerCase() as Address]),
        perTxCapWei: parseEther('0.5'),
        dailyCapWei: parseEther('100'),
      });

      const result = wallet.validateOrder(TEST_CONTRACT, parseEther('1'));
      expect(result.allowed).toBe(false);
      expect(result.reason).toContain('per-tx cap');
    });

    it('allows when both allowlist and limits pass', () => {
      const wallet = createWallet({
        allowlist: new Set([TEST_CONTRACT.toLowerCase() as Address]),
        perTxCapWei: parseEther('5'),
        dailyCapWei: parseEther('100'),
      });

      const result = wallet.validateOrder(TEST_CONTRACT, parseEther('1'));
      expect(result.allowed).toBe(true);
    });
  });

  describe('balance and nonce queries', () => {
    it('queries ETH balance', async () => {
      const client = mockPublicClient({
        getBalance: vi.fn().mockResolvedValue(parseEther('5.5')),
      });

      const wallet = createWallet({ publicClient: client as any });
      const balance = await wallet.getBalance();
      expect(balance).toBe(parseEther('5.5'));
      expect(client.getBalance).toHaveBeenCalledWith({ address: TEST_WALLET_ADDRESS });
    });

    it('queries ERC-20 token balance', async () => {
      const tokenBalance = 1_000_000n * 10n ** 6n; // 1M USDC (6 decimals)
      const client = mockPublicClient({
        readContract: vi.fn().mockResolvedValue(tokenBalance),
      });

      const wallet = createWallet({ publicClient: client as any });
      const balance = await wallet.getTokenBalance(TEST_CONTRACT);
      expect(balance).toBe(tokenBalance);
    });

    it('queries nonce from EntryPoint', async () => {
      const client = mockPublicClient({
        readContract: vi.fn().mockResolvedValue(42n),
      });

      const wallet = createWallet({ publicClient: client as any });
      const nonce = await wallet.getNonce();
      expect(nonce).toBe(42n);
    });
  });

  describe('UserOp construction', () => {
    it('builds a valid UserOperation', async () => {
      const client = mockPublicClient({
        readContract: vi.fn().mockResolvedValue(5n), // nonce
        getGasPrice: vi.fn().mockResolvedValue(30_000_000_000n),
      });

      const wallet = createWallet({ publicClient: client as any });
      const userOp = await wallet.buildUserOp({
        target: TEST_CONTRACT,
        value: parseEther('0.1'),
        callData: '0x' as `0x${string}`,
      });

      expect(userOp.sender).toBe(TEST_WALLET_ADDRESS);
      expect(userOp.nonce).toBe(5n);
      expect(userOp.callData).toBeDefined();
      expect(userOp.callData.startsWith('0x')).toBe(true);
      expect(userOp.callGasLimit).toBeGreaterThan(0n);
      expect(userOp.verificationGasLimit).toBeGreaterThan(0n);
      expect(userOp.preVerificationGas).toBeGreaterThan(0n);
      expect(userOp.maxFeePerGas).toBeGreaterThan(0n);
      expect(userOp.maxPriorityFeePerGas).toBeGreaterThan(0n);
      expect(userOp.signature).toBeDefined();
      expect(userOp.signature.length).toBeGreaterThan(2); // More than just "0x"
    });

    it('encodes execute call in callData', async () => {
      const client = mockPublicClient({
        readContract: vi.fn().mockResolvedValue(0n),
        getGasPrice: vi.fn().mockResolvedValue(30_000_000_000n),
      });

      const wallet = createWallet({ publicClient: client as any });
      const userOp = await wallet.buildUserOp({
        target: TEST_CONTRACT,
        value: 0n,
        callData: '0xdeadbeef' as `0x${string}`,
      });

      // execute(address,uint256,bytes) selector = 0xb61d27f6
      expect(userOp.callData.startsWith('0xb61d27f6')).toBe(true);
    });

    it('uses fallback gas values when RPC fails', async () => {
      const client = mockPublicClient({
        readContract: vi.fn().mockRejectedValue(new Error('RPC error')),
        getGasPrice: vi.fn().mockRejectedValue(new Error('RPC error')),
      });

      const wallet = createWallet({ publicClient: client as any });
      const userOp = await wallet.buildUserOp({
        target: TEST_CONTRACT,
        value: 0n,
        callData: '0x' as `0x${string}`,
      });

      // Should use fallback values, not throw
      expect(userOp.nonce).toBe(0n);
      expect(userOp.maxFeePerGas).toBe(30_000_000_000n);
      expect(userOp.maxPriorityFeePerGas).toBe(3_000_000_000n);
    });
  });

  describe('getUserOpHash', () => {
    it('produces a deterministic hash for the same inputs', () => {
      const client = mockPublicClient();
      // Need chain on the client for getUserOpHash
      (client as any).chain = { id: 11155111 };
      const wallet = createWallet({ publicClient: client as any });

      const userOp: UserOperation = {
        sender: TEST_WALLET_ADDRESS,
        nonce: 5n,
        callData: '0xdeadbeef' as `0x${string}`,
        callGasLimit: 200_000n,
        verificationGasLimit: 100_000n,
        preVerificationGas: 50_000n,
        maxFeePerGas: 30_000_000_000n,
        maxPriorityFeePerGas: 3_000_000_000n,
        signature: '0x' as `0x${string}`,
      };

      const hash1 = wallet.getUserOpHash(userOp);
      const hash2 = wallet.getUserOpHash(userOp);

      expect(hash1).toBe(hash2);
      expect(hash1).toMatch(/^0x[0-9a-f]{64}$/);
    });

    it('produces different hashes for different nonces', () => {
      const client = mockPublicClient();
      (client as any).chain = { id: 11155111 };
      const wallet = createWallet({ publicClient: client as any });

      const baseOp: UserOperation = {
        sender: TEST_WALLET_ADDRESS,
        nonce: 0n,
        callData: '0xdeadbeef' as `0x${string}`,
        callGasLimit: 200_000n,
        verificationGasLimit: 100_000n,
        preVerificationGas: 50_000n,
        maxFeePerGas: 30_000_000_000n,
        maxPriorityFeePerGas: 3_000_000_000n,
        signature: '0x' as `0x${string}`,
      };

      const hash0 = wallet.getUserOpHash({ ...baseOp, nonce: 0n });
      const hash1 = wallet.getUserOpHash({ ...baseOp, nonce: 1n });

      expect(hash0).not.toBe(hash1);
    });

    it('produces different hashes for different chain IDs', () => {
      const client1 = mockPublicClient();
      (client1 as any).chain = { id: 11155111 };
      const wallet1 = createWallet({ publicClient: client1 as any });

      const client2 = mockPublicClient();
      (client2 as any).chain = { id: 1 };
      const wallet2 = createWallet({ publicClient: client2 as any });

      const userOp: UserOperation = {
        sender: TEST_WALLET_ADDRESS,
        nonce: 0n,
        callData: '0xdeadbeef' as `0x${string}`,
        callGasLimit: 200_000n,
        verificationGasLimit: 100_000n,
        preVerificationGas: 50_000n,
        maxFeePerGas: 30_000_000_000n,
        maxPriorityFeePerGas: 3_000_000_000n,
        signature: '0x' as `0x${string}`,
      };

      const hashSepolia = wallet1.getUserOpHash(userOp);
      const hashMainnet = wallet2.getUserOpHash(userOp);

      expect(hashSepolia).not.toBe(hashMainnet);
    });

    it('handles initCode and paymasterAndData fields', () => {
      const client = mockPublicClient();
      (client as any).chain = { id: 11155111 };
      const wallet = createWallet({ publicClient: client as any });

      const baseOp: UserOperation = {
        sender: TEST_WALLET_ADDRESS,
        nonce: 0n,
        callData: '0xdeadbeef' as `0x${string}`,
        callGasLimit: 200_000n,
        verificationGasLimit: 100_000n,
        preVerificationGas: 50_000n,
        maxFeePerGas: 30_000_000_000n,
        maxPriorityFeePerGas: 3_000_000_000n,
        signature: '0x' as `0x${string}`,
      };

      const hashNoExtras = wallet.getUserOpHash(baseOp);
      const hashWithInitCode = wallet.getUserOpHash({
        ...baseOp,
        initCode: '0xabcdef' as `0x${string}`,
      });
      const hashWithPaymaster = wallet.getUserOpHash({
        ...baseOp,
        paymasterAndData: '0x112233' as `0x${string}`,
      });

      expect(hashNoExtras).not.toBe(hashWithInitCode);
      expect(hashNoExtras).not.toBe(hashWithPaymaster);
      expect(hashWithInitCode).not.toBe(hashWithPaymaster);
    });
  });

  describe('signUserOp (ERC-4337 compliant)', () => {
    it('produces a valid ECDSA signature over the UserOp hash', async () => {
      const client = mockPublicClient({
        readContract: vi.fn().mockResolvedValue(0n),
        getGasPrice: vi.fn().mockResolvedValue(30_000_000_000n),
      });
      (client as any).chain = { id: 11155111 };

      const wallet = createWallet({ publicClient: client as any });
      const userOp = await wallet.buildUserOp({
        target: TEST_CONTRACT,
        value: 0n,
        callData: '0x' as `0x${string}`,
      });

      // Signature should be 65 bytes (130 hex chars + 0x prefix = 132 chars)
      expect(userOp.signature).toMatch(/^0x[0-9a-f]{130}$/);
    });

    it('produces different signatures for different UserOps', async () => {
      const client = mockPublicClient({
        readContract: vi.fn()
          .mockResolvedValueOnce(0n) // nonce for first op
          .mockResolvedValueOnce(1n), // nonce for second op
        getGasPrice: vi.fn().mockResolvedValue(30_000_000_000n),
      });
      (client as any).chain = { id: 11155111 };

      const wallet = createWallet({ publicClient: client as any });

      const op1 = await wallet.buildUserOp({
        target: TEST_CONTRACT,
        value: 0n,
        callData: '0xaa' as `0x${string}`,
      });

      const op2 = await wallet.buildUserOp({
        target: TEST_CONTRACT,
        value: 0n,
        callData: '0xbb' as `0x${string}`,
      });

      expect(op1.signature).not.toBe(op2.signature);
    });
  });

  describe('sendUserOp', () => {
    it('throws when bundler URL is not configured', async () => {
      const wallet = createWallet({ bundlerUrl: '' });

      const mockOp: UserOperation = {
        sender: TEST_WALLET_ADDRESS,
        nonce: 0n,
        callData: '0x' as `0x${string}`,
        callGasLimit: 200_000n,
        verificationGasLimit: 100_000n,
        preVerificationGas: 50_000n,
        maxFeePerGas: 30_000_000_000n,
        maxPriorityFeePerGas: 3_000_000_000n,
        signature: '0xsig' as `0x${string}`,
      };

      await expect(wallet.sendUserOp(mockOp)).rejects.toThrow('BUNDLER_URL is not configured');
    });
  });

  describe('logging security', () => {
    it('logs initialization without private key', () => {
      const logs: Array<{ event: string; extra?: Record<string, unknown> }> = [];
      createWallet({
        onLog: (event, _msg, extra) => logs.push({ event, extra }),
      });

      const initLog = logs.find((l) => l.event === 'wallet_initialized');
      expect(initLog).toBeDefined();
      expect(initLog!.extra?.signerAddress).toBeDefined();
      expect(initLog!.extra?.walletAddress).toBeDefined();

      // Ensure private key is nowhere in logs
      const serialized = JSON.stringify(logs);
      expect(serialized).not.toContain(TEST_PRIVATE_KEY);
    });

    it('logs spend recording with amounts', () => {
      const logs: Array<{ event: string; extra?: Record<string, unknown> }> = [];
      const wallet = createWallet({
        perTxCapWei: parseEther('10'),
        dailyCapWei: parseEther('100'),
        onLog: (event, _msg, extra) => logs.push({ event, extra }),
      });

      wallet.recordSpend(parseEther('1'));

      const spendLog = logs.find((l) => l.event === 'wallet_spend_recorded');
      expect(spendLog).toBeDefined();
      expect(spendLog!.extra?.amount).toBe('1');
    });
  });
});
