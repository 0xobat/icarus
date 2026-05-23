/**
 * Chain configuration resolver.
 *
 * Maps CHAIN_ID env var to a viem chain object.
 * Supports Base (8453) and Sepolia (11155111). Defaults to sepolia.
 */

import { type Chain } from 'viem';
import { base, sepolia } from 'viem/chains';

const CHAIN_MAP: Record<number, Chain> = {
  8453: base,
  11155111: sepolia,
};

/** Resolve CHAIN_ID env var to a viem Chain. Defaults to sepolia. */
export function resolveChain(chainId?: string | number): Chain {
  const id = Number(chainId ?? process.env.CHAIN_ID ?? '11155111');
  const chain = CHAIN_MAP[id];
  if (!chain) {
    throw new Error(
      `Unsupported CHAIN_ID: ${id}. Supported: ${Object.keys(CHAIN_MAP).join(', ')}`,
    );
  }
  return chain;
}
