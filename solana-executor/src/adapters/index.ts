/**
 * Adapter registry for solana-executor.
 *
 * The orders processor (Stream B) looks an adapter up by name and calls
 * `buildInstructions(order, connection)`. Adding a new venue is a one-line
 * change to {@link buildDefaultAdapters} below — the processor stays agnostic.
 *
 * v1 venues: Jupiter (swap), Kamino (deposit/withdraw).
 * v2+ (deferred per blueprint W10): Drift, MarginFi.
 */

import type { PublicKey } from "@solana/web3.js";

import { JupiterAdapter } from "./jupiter.js";
import { KaminoAdapter } from "./kamino.js";
import type { SolanaAdapter } from "./types.js";

export { JupiterAdapter } from "./jupiter.js";
export { KaminoAdapter, NotImplementedError } from "./kamino.js";
export type {
  SolanaAdapter,
  ExecutionOrder,
  ExecutionOrderParams,
  ExecutionOrderLimits,
} from "./types.js";

/** Options for {@link buildDefaultAdapters}. */
export interface BuildAdaptersOptions {
  /**
   * Pubkey that owns the on-chain side effects of every order built by
   * the resulting adapters — the Squads vault PDA in production, the
   * member keypair pubkey in single-signer fallback. Required because
   * Jupiter bakes it into the ATAs it derives and Kamino bakes it into
   * the obligation owner field; either built with a placeholder would
   * fail on-chain.
   */
  readonly signer: PublicKey;
}

/**
 * Build the v1 adapter map, injecting the runtime signer pubkey into
 * every adapter that needs it. The processor's {@link AdapterRegistry}
 * is constructed from the entries of this object.
 *
 * Replaces the module-level `ADAPTERS` constant — that earlier shape
 * created Jupiter with a SystemProgram placeholder and Kamino without
 * an owner, both of which guaranteed on-chain failure. There is no
 * useful "default-constructed" adapter; construction requires the
 * signer pubkey.
 */
export function buildDefaultAdapters(
  opts: BuildAdaptersOptions,
): Readonly<Record<string, SolanaAdapter>> {
  return Object.freeze({
    jupiter: new JupiterAdapter({ userPublicKey: opts.signer }),
    kamino: new KaminoAdapter({ owner: opts.signer }),
  });
}
