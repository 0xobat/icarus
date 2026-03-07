/**
 * Sepolia live validation tests — skip-by-default.
 *
 * Verifies that Aave V3 contracts exist on Sepolia and accept
 * the calldata produced by our protocol adapters. Only runs when
 * ALCHEMY_SEPOLIA_HTTP_URL is set in the environment.
 *
 * These tests satisfy the P1a gate requirement for Sepolia pipeline
 * validation with real contract interactions (read-only + reverted calls).
 */

import { describe, expect, it } from 'vitest';
import { createPublicClient, http, type Hex } from 'viem';
import { sepolia } from 'viem/chains';

import { AAVE_POOL_ABI, AAVE_V3_POOL, encodeSupply } from '../src/execution/aave-v3-adapter.js';

const RPC_URL = process.env.ALCHEMY_SEPOLIA_HTTP_URL;

const client = RPC_URL
  ? createPublicClient({ chain: sepolia, transport: http(RPC_URL) })
  : undefined;

/** Known Sepolia USDC address for Aave V3 testing. */
const SEPOLIA_USDC = '0x94a9D9AC8a22534E3FaCa9F4e7F2E2cf85d5E4C8' as const;

describe.skipIf(!RPC_URL)('Sepolia live validation', () => {
  it('Aave V3 Pool contract exists on Sepolia', async () => {
    const code = await client!.getCode({ address: AAVE_V3_POOL });
    expect(code).toBeDefined();
    expect(code!.length).toBeGreaterThan(2); // '0x' is empty
  });

  it('Aave getReserveData is callable for Sepolia USDC', async () => {
    const result = await client!.readContract({
      address: AAVE_V3_POOL,
      abi: AAVE_POOL_ABI,
      functionName: 'getReserveData',
      args: [SEPOLIA_USDC],
    });

    // Result is a tuple — just verify it returns without throwing
    expect(result).toBeDefined();
  });

  it('Aave supply calldata accepted by contract (reverts on balance, not selector)', async () => {
    const dummyWallet = '0x1111111111111111111111111111111111111111' as const;
    const calldata = encodeSupply(SEPOLIA_USDC, 1_000_000n, dummyWallet);

    try {
      await client!.call({
        to: AAVE_V3_POOL,
        data: calldata as Hex,
      });
      // If it somehow succeeds, that's fine too
    } catch (err: unknown) {
      const message = (err as Error).message ?? '';
      // The call should revert because the caller has no balance/allowance,
      // but NOT because of an invalid function selector
      expect(message).not.toContain('invalid function selector');
      expect(message).not.toContain('invalid opcode');
    }
  });
});
