import { describe, it, expect, vi, beforeEach } from 'vitest';
import { ContractAllowlist, type AllowlistConfig, type ChainId } from '../src/security/contract-allowlist.js';
import { type ExecutionOrder } from '../src/execution/transaction-builder.js';
import { CHANNELS } from '../src/redis/client.js';

// ── Test helpers ──────────────────────────────────

function createTestConfig(): AllowlistConfig {
  return {
    version: '1.0.0',
    updatedAt: '2024-01-01T00:00:00Z',
    chains: {
      ethereum: [
        { address: '0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2', name: 'Aave V3 Pool', protocol: 'aave_v3' },
        { address: '0xE592427A0AEce92De3Edee1F18E0157C05861564', name: 'Uniswap V3 Router', protocol: 'uniswap_v3' },
      ],
      arbitrum: [
        { address: '0x794a61358D6845594F94dc1DB02A252b5b4814aD', name: 'Aave V3 Pool', protocol: 'aave_v3' },
      ],
      base: [],
      sepolia: [
        { address: '0x6Ae43d3271ff6888e7Fc43Fd7321a503ff738951', name: 'Aave V3 Pool (Sepolia)', protocol: 'aave_v3' },
      ],
    },
  };
}

function createMockOrder(overrides: Partial<ExecutionOrder> = {}): ExecutionOrder {
  return {
    version: '1.0.0',
    orderId: 'order-123',
    correlationId: 'corr-456',
    timestamp: new Date().toISOString(),
    chain: 'ethereum',
    protocol: 'aave_v3',
    action: 'supply',
    params: { tokenIn: '0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2', amount: '1000000' },
    limits: { maxGasWei: '1000000000000', maxSlippageBps: 50, deadlineUnix: Math.floor(Date.now() / 1000) + 3600 },
    ...overrides,
  };
}

function createMockRedis(opts: { publishFn?: (...args: any[]) => Promise<void> } = {}) {
  return {
    publish: opts.publishFn ?? vi.fn().mockResolvedValue(undefined),
  } as any;
}

// ── Tests ──────────────────────────────────────────

describe('ContractAllowlist', () => {
  describe('constructor', () => {
    it('loads default config when no options given', () => {
      const allowlist = new ContractAllowlist();
      expect(allowlist.totalEntries).toBeGreaterThan(0);
      expect(allowlist.version).toBe('1.0.0');
    });

    it('loads inline config', () => {
      const config = createTestConfig();
      const allowlist = new ContractAllowlist({ config });
      expect(allowlist.totalEntries).toBe(4); // 2 + 1 + 0 + 1
    });

    it('falls back to default config on file read error', () => {
      const logs: Array<{ event: string }> = [];
      const allowlist = new ContractAllowlist({
        configPath: '/nonexistent/path.json',
        onLog: (event) => logs.push({ event }),
      });
      expect(allowlist.totalEntries).toBeGreaterThan(0);
      expect(logs.some((l) => l.event === 'allowlist_config_error')).toBe(true);
    });
  });

  describe('check', () => {
    it('allows allowlisted addresses', () => {
      const config = createTestConfig();
      const allowlist = new ContractAllowlist({ config });

      const result = allowlist.check('ethereum', '0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2');
      expect(result.allowed).toBe(true);
      expect(result.entry?.name).toBe('Aave V3 Pool');
      expect(result.entry?.protocol).toBe('aave_v3');
    });

    it('rejects non-allowlisted addresses', () => {
      const config = createTestConfig();
      const allowlist = new ContractAllowlist({ config });

      const result = allowlist.check('ethereum', '0xDeadBeef1234567890abcdef1234567890abcdef');
      expect(result.allowed).toBe(false);
      expect(result.reason).toContain('not on the allowlist');
    });

    it('is case-insensitive for addresses', () => {
      const config = createTestConfig();
      const allowlist = new ContractAllowlist({ config });

      const lower = allowlist.check('ethereum', '0x87870bca3f3fd6335c3f4ce8392d69350b4fa4e2');
      const upper = allowlist.check('ethereum', '0x87870BCA3F3FD6335C3F4CE8392D69350B4FA4E2');

      expect(lower.allowed).toBe(true);
      expect(upper.allowed).toBe(true);
    });

    it('enforces per-chain separation', () => {
      const config = createTestConfig();
      const allowlist = new ContractAllowlist({ config });

      // Aave V3 on Ethereum uses a different address than Arbitrum
      const ethResult = allowlist.check('ethereum', '0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2');
      const arbResult = allowlist.check('arbitrum', '0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2');

      expect(ethResult.allowed).toBe(true);
      expect(arbResult.allowed).toBe(false); // Different address on Arbitrum
    });

    it('rejects addresses on chains with empty allowlists', () => {
      const config = createTestConfig();
      const allowlist = new ContractAllowlist({ config });

      const result = allowlist.check('base', '0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2');
      expect(result.allowed).toBe(false);
    });
  });

  describe('isAllowed', () => {
    it('returns boolean true for allowed', () => {
      const config = createTestConfig();
      const allowlist = new ContractAllowlist({ config });
      expect(allowlist.isAllowed('ethereum', '0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2')).toBe(true);
    });

    it('returns boolean false for rejected', () => {
      const config = createTestConfig();
      const allowlist = new ContractAllowlist({ config });
      expect(allowlist.isAllowed('ethereum', '0xbadaddress')).toBe(false);
    });
  });

  describe('validateOrder', () => {
    it('allows order with allowlisted target', async () => {
      const config = createTestConfig();
      const allowlist = new ContractAllowlist({ config });
      const redis = createMockRedis();
      allowlist.attach(redis);

      const order = createMockOrder();
      const result = await allowlist.validateOrder(order);

      expect(result.allowed).toBe(true);
      expect(redis.publish).not.toHaveBeenCalled();
    });

    it('rejects order and publishes error to execution:results', async () => {
      const config = createTestConfig();
      const publishFn = vi.fn().mockResolvedValue(undefined);
      const redis = createMockRedis({ publishFn });
      const allowlist = new ContractAllowlist({ config });
      allowlist.attach(redis);

      const order = createMockOrder({
        params: { tokenIn: '0xMaliciousContract', amount: '1000000' },
      });
      const result = await allowlist.validateOrder(order);

      expect(result.allowed).toBe(false);
      expect(publishFn).toHaveBeenCalledWith(
        CHANNELS.EXECUTION_RESULTS,
        expect.objectContaining({
          status: 'failed',
          orderId: 'order-123',
          error: expect.stringContaining('not on the allowlist'),
        }),
      );
    });

    it('still rejects without Redis (but does not publish)', async () => {
      const config = createTestConfig();
      const allowlist = new ContractAllowlist({ config });

      const order = createMockOrder({
        params: { tokenIn: '0xMaliciousContract', amount: '1000000' },
      });
      const result = await allowlist.validateOrder(order);

      expect(result.allowed).toBe(false);
    });
  });

  describe('getChainEntries', () => {
    it('returns entries for a valid chain', () => {
      const config = createTestConfig();
      const allowlist = new ContractAllowlist({ config });

      const entries = allowlist.getChainEntries('ethereum');
      expect(entries.length).toBe(2);
      expect(entries[0].name).toBe('Aave V3 Pool');
    });

    it('returns empty array for chain with no entries', () => {
      const config = createTestConfig();
      const allowlist = new ContractAllowlist({ config });

      const entries = allowlist.getChainEntries('base');
      expect(entries.length).toBe(0);
    });
  });

  describe('default entries', () => {
    it('includes Aave V3 in default config', () => {
      const allowlist = new ContractAllowlist();
      expect(allowlist.isAllowed('ethereum', '0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2')).toBe(true);
    });

    it('includes Uniswap V3 in default config', () => {
      const allowlist = new ContractAllowlist();
      expect(allowlist.isAllowed('ethereum', '0xE592427A0AEce92De3Edee1F18E0157C05861564')).toBe(true);
    });

    it('includes Lido in default config', () => {
      const allowlist = new ContractAllowlist();
      expect(allowlist.isAllowed('ethereum', '0xae7ab96520DE3A18E5e111B5EaAb095312D7fE84')).toBe(true);
    });

    it('includes Sepolia Aave V3 in default config', () => {
      const allowlist = new ContractAllowlist();
      expect(allowlist.isAllowed('sepolia', '0x6Ae43d3271ff6888e7Fc43Fd7321a503ff738951')).toBe(true);
    });

    it('includes multiple chains in default config', () => {
      const allowlist = new ContractAllowlist();
      expect(allowlist.getChainEntries('ethereum').length).toBeGreaterThan(0);
      expect(allowlist.getChainEntries('arbitrum').length).toBeGreaterThan(0);
      expect(allowlist.getChainEntries('base').length).toBeGreaterThan(0);
      expect(allowlist.getChainEntries('sepolia').length).toBeGreaterThan(0);
    });
  });

  describe('stats', () => {
    it('tracks check, allowed, and rejected counts', () => {
      const config = createTestConfig();
      const allowlist = new ContractAllowlist({ config });

      allowlist.check('ethereum', '0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2');
      allowlist.check('ethereum', '0xbadaddress');
      allowlist.check('ethereum', '0xE592427A0AEce92De3Edee1F18E0157C05861564');

      expect(allowlist.stats.checked).toBe(3);
      expect(allowlist.stats.allowed).toBe(2);
      expect(allowlist.stats.rejected).toBe(1);
    });
  });

  describe('logging', () => {
    it('logs on allowlist load', () => {
      const logs: Array<{ event: string; extra?: Record<string, unknown> }> = [];
      const config = createTestConfig();
      new ContractAllowlist({
        config,
        onLog: (event, _msg, extra) => logs.push({ event, extra }),
      });

      expect(logs.some((l) => l.event === 'allowlist_loaded')).toBe(true);
      const loadLog = logs.find((l) => l.event === 'allowlist_loaded');
      expect(loadLog?.extra?.totalEntries).toBe(4);
    });

    it('logs on allowed check', () => {
      const logs: Array<{ event: string }> = [];
      const config = createTestConfig();
      const allowlist = new ContractAllowlist({
        config,
        onLog: (event) => logs.push({ event }),
      });

      allowlist.check('ethereum', '0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2');
      expect(logs.some((l) => l.event === 'allowlist_allowed')).toBe(true);
    });

    it('logs on rejected check', () => {
      const logs: Array<{ event: string }> = [];
      const config = createTestConfig();
      const allowlist = new ContractAllowlist({
        config,
        onLog: (event) => logs.push({ event }),
      });

      allowlist.check('ethereum', '0xbadaddress');
      expect(logs.some((l) => l.event === 'allowlist_rejected')).toBe(true);
    });
  });

  describe('immutability (restart-required)', () => {
    it('config is loaded once at construction and cannot be modified', () => {
      const config = createTestConfig();
      const allowlist = new ContractAllowlist({ config });
      const initialEntries = allowlist.totalEntries;

      // Mutating the original config should not affect the allowlist
      config.chains.ethereum.push({
        address: '0xNewAddress',
        name: 'New Contract',
        protocol: 'test',
      });

      // The allowlist's internal lookup map was built at construction time
      // Adding to config.chains doesn't rebuild the map
      expect(allowlist.check('ethereum', '0xNewAddress').allowed).toBe(false);
    });
  });
});
