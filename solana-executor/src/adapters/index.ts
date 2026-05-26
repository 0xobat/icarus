/**
 * Adapter registry for solana-executor.
 *
 * The orders processor (Stream B) looks an adapter up by name and calls
 * `buildInstructions(order, connection)`. Adding a new venue is a one-line
 * change to {@link ADAPTERS} below — the processor stays agnostic.
 *
 * v1 venues: Jupiter (swap), Kamino (deposit/withdraw).
 * v2+ (deferred per blueprint W10): Drift, MarginFi.
 */

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

/**
 * Default lazily-constructed adapter map. Built once on first access so that
 * tests can `import { ADAPTERS }` without paying for adapter construction
 * at module-load time.
 *
 * Note: KaminoAdapter is constructed without an `owner` here — the processor
 * is expected to inject its own KaminoAdapter instance once it knows the
 * Squads vault PDA. This default exists only so a smoke-test routing call
 * (`loadAdapter('kamino')`) returns something rather than throwing.
 */
export const ADAPTERS: Readonly<Record<string, SolanaAdapter>> = Object.freeze({
  jupiter: new JupiterAdapter(),
  kamino: new KaminoAdapter(),
});

/**
 * Look up an adapter by name. Throws if `name` is not a known v1 adapter.
 * Use lowercase identifiers (`jupiter`, `kamino`); these match the
 * `protocol` field on v2 ExecutionOrders.
 */
export function loadAdapter(name: string): SolanaAdapter {
  const adapter = ADAPTERS[name];
  if (!adapter) {
    throw new Error(
      `loadAdapter: unknown adapter '${name}' (known: ${Object.keys(ADAPTERS).join(", ")})`,
    );
  }
  return adapter;
}
