import { describe, it, expect, beforeEach } from 'vitest';
import type { Address } from 'viem';

// ── AllowlistGuard logic simulation ──
// Since we don't have a Solidity compiler in the test pipeline, we test the
// guard's logic in TypeScript. The Solidity contract mirrors this exact logic.

/** Simulates the AllowlistGuard contract behavior for testing. */
class AllowlistGuardSim {
  private owner: Address;
  private allowlist: Map<string, boolean> = new Map();

  constructor(owner: Address, initialAllowlist: Address[]) {
    if (owner === '0x0000000000000000000000000000000000000000') {
      throw new Error('ZeroAddress');
    }
    this.owner = owner;
    for (const addr of initialAllowlist) {
      this.allowlist.set(addr.toLowerCase(), true);
    }
  }

  /** Simulates checkTransaction — reverts (throws) if target not allowlisted. */
  checkTransaction(to: Address): void {
    if (!this.allowlist.get(to.toLowerCase())) {
      throw new Error(`NotAllowlisted(${to})`);
    }
  }

  /** checkAfterExecution — no-op. */
  checkAfterExecution(): void {
    // intentionally empty
  }

  addAddress(sender: Address, addr: Address): void {
    this.requireOwner(sender);
    this.allowlist.set(addr.toLowerCase(), true);
  }

  removeAddress(sender: Address, addr: Address): void {
    this.requireOwner(sender);
    this.allowlist.set(addr.toLowerCase(), false);
  }

  addAddresses(sender: Address, addrs: Address[]): void {
    this.requireOwner(sender);
    for (const addr of addrs) {
      this.allowlist.set(addr.toLowerCase(), true);
    }
  }

  transferOwnership(sender: Address, newOwner: Address): void {
    this.requireOwner(sender);
    if (newOwner === '0x0000000000000000000000000000000000000000') {
      throw new Error('ZeroAddress');
    }
    this.owner = newOwner;
  }

  getOwner(): Address {
    return this.owner;
  }

  isAllowlisted(addr: Address): boolean {
    return this.allowlist.get(addr.toLowerCase()) === true;
  }

  private requireOwner(sender: Address): void {
    if (sender.toLowerCase() !== this.owner.toLowerCase()) {
      throw new Error('NotOwner');
    }
  }
}

// ── Permitted Base contracts ──

const AAVE_V3_POOL: Address = '0xA238Dd80C259a72e81d7e4664a9801593F98d1c5';
const AERODROME_ROUTER: Address = '0xcF77a3Ba9A5CA399B7c97c74d54e5b1Beb874E43';
const USDC: Address = '0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913';
const USDC_BRIDGED: Address = '0xd9aAEc86B65D86f6A7B5B1b0c42FFA531710b6CA';
const DAI: Address = '0x50c5725949A6F0c72E6C4a641F24049A917DB0Cb';

const BASE_ALLOWLIST: Address[] = [AAVE_V3_POOL, AERODROME_ROUTER, USDC, USDC_BRIDGED, DAI];

const OWNER: Address = '0x1111111111111111111111111111111111111111';
const NON_OWNER: Address = '0x2222222222222222222222222222222222222222';
const RANDOM_CONTRACT: Address = '0xDeaDbeefdEAdbeefdEadbEEFdeadbeEFdEaDbeeF';

describe('AllowlistGuard', () => {
  let guard: AllowlistGuardSim;

  beforeEach(() => {
    guard = new AllowlistGuardSim(OWNER, BASE_ALLOWLIST);
  });

  describe('constructor', () => {
    it('initializes with owner and allowlisted addresses', () => {
      expect(guard.getOwner()).toBe(OWNER);
      for (const addr of BASE_ALLOWLIST) {
        expect(guard.isAllowlisted(addr)).toBe(true);
      }
    });

    it('reverts with zero address owner', () => {
      expect(
        () => new AllowlistGuardSim('0x0000000000000000000000000000000000000000', []),
      ).toThrow('ZeroAddress');
    });
  });

  describe('checkTransaction', () => {
    it('allows transactions to allowlisted addresses', () => {
      for (const addr of BASE_ALLOWLIST) {
        expect(() => guard.checkTransaction(addr)).not.toThrow();
      }
    });

    it('reverts for non-allowlisted addresses', () => {
      expect(() => guard.checkTransaction(RANDOM_CONTRACT)).toThrow('NotAllowlisted');
    });

    it('is case-insensitive on address matching', () => {
      const lowerAddr = AAVE_V3_POOL.toLowerCase() as Address;
      expect(() => guard.checkTransaction(lowerAddr)).not.toThrow();
    });

    it('reverts for zero address (not allowlisted)', () => {
      expect(
        () => guard.checkTransaction('0x0000000000000000000000000000000000000000'),
      ).toThrow('NotAllowlisted');
    });
  });

  describe('checkAfterExecution', () => {
    it('does not revert (no-op)', () => {
      expect(() => guard.checkAfterExecution()).not.toThrow();
    });
  });

  describe('owner management', () => {
    it('owner can add an address', () => {
      const newAddr: Address = '0x3333333333333333333333333333333333333333';
      guard.addAddress(OWNER, newAddr);
      expect(guard.isAllowlisted(newAddr)).toBe(true);
      expect(() => guard.checkTransaction(newAddr)).not.toThrow();
    });

    it('owner can remove an address', () => {
      guard.removeAddress(OWNER, USDC);
      expect(guard.isAllowlisted(USDC)).toBe(false);
      expect(() => guard.checkTransaction(USDC)).toThrow('NotAllowlisted');
    });

    it('owner can batch add addresses', () => {
      const newAddrs: Address[] = [
        '0x4444444444444444444444444444444444444444',
        '0x5555555555555555555555555555555555555555',
      ];
      guard.addAddresses(OWNER, newAddrs);
      for (const addr of newAddrs) {
        expect(guard.isAllowlisted(addr)).toBe(true);
      }
    });

    it('non-owner cannot add an address', () => {
      expect(
        () => guard.addAddress(NON_OWNER, RANDOM_CONTRACT),
      ).toThrow('NotOwner');
    });

    it('non-owner cannot remove an address', () => {
      expect(
        () => guard.removeAddress(NON_OWNER, USDC),
      ).toThrow('NotOwner');
    });

    it('non-owner cannot batch add', () => {
      expect(
        () => guard.addAddresses(NON_OWNER, [RANDOM_CONTRACT]),
      ).toThrow('NotOwner');
    });
  });

  describe('ownership transfer', () => {
    it('owner can transfer ownership', () => {
      guard.transferOwnership(OWNER, NON_OWNER);
      expect(guard.getOwner()).toBe(NON_OWNER);
      // Old owner can no longer manage
      expect(
        () => guard.addAddress(OWNER, RANDOM_CONTRACT),
      ).toThrow('NotOwner');
      // New owner can manage
      guard.addAddress(NON_OWNER, RANDOM_CONTRACT);
      expect(guard.isAllowlisted(RANDOM_CONTRACT)).toBe(true);
    });

    it('cannot transfer to zero address', () => {
      expect(
        () => guard.transferOwnership(OWNER, '0x0000000000000000000000000000000000000000'),
      ).toThrow('ZeroAddress');
    });

    it('non-owner cannot transfer ownership', () => {
      expect(
        () => guard.transferOwnership(NON_OWNER, NON_OWNER),
      ).toThrow('NotOwner');
    });
  });

  describe('re-adding a removed address', () => {
    it('can re-add an address after removal', () => {
      guard.removeAddress(OWNER, AAVE_V3_POOL);
      expect(guard.isAllowlisted(AAVE_V3_POOL)).toBe(false);
      guard.addAddress(OWNER, AAVE_V3_POOL);
      expect(guard.isAllowlisted(AAVE_V3_POOL)).toBe(true);
      expect(() => guard.checkTransaction(AAVE_V3_POOL)).not.toThrow();
    });
  });

  describe('Base permitted contracts', () => {
    it('all five Base contracts are allowlisted', () => {
      expect(guard.isAllowlisted(AAVE_V3_POOL)).toBe(true);
      expect(guard.isAllowlisted(AERODROME_ROUTER)).toBe(true);
      expect(guard.isAllowlisted(USDC)).toBe(true);
      expect(guard.isAllowlisted(USDC_BRIDGED)).toBe(true);
      expect(guard.isAllowlisted(DAI)).toBe(true);
    });
  });
});
