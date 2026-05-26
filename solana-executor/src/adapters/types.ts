/**
 * Adapter contract for solana-executor.
 *
 * Each protocol adapter (Jupiter, Kamino, future Drift / MarginFi) takes a
 * v2 ExecutionOrder and a Solana RPC Connection and returns the list of
 * TransactionInstruction objects required to execute the order.
 *
 * Adapters DO NOT sign or submit. The orders processor (Stream B) wraps the
 * returned instructions into a transaction, runs the Squads multisig + on-chain
 * allowlist guard, and only then broadcasts. Keeping adapters instruction-only
 * preserves the verification gate (risk → allowlist → broadcast).
 *
 * Note: Stream B owns the live `SolanaAdapter` contract consumed by the orders
 * processor. If their definition differs at merge (e.g. async iterator, extra
 * context arg), align this file to theirs rather than duplicating.
 */

import type { Connection, TransactionInstruction } from "@solana/web3.js";

/**
 * Minimal v2 ExecutionOrder shape relevant to Solana adapters.
 *
 * Mirrors `shared/schemas/execution-order.schema.json` (chain = "solana").
 * Adapters read `params` + `limits.max_slippage_bps` only; the orders
 * processor handles ordering, deduplication, and budget enforcement.
 */
export interface ExecutionOrder {
  readonly version: string;
  readonly order_id: string;
  readonly correlation_id: string;
  readonly timestamp: string;
  readonly chain: "base" | "solana";
  readonly protocol: string;
  readonly action: string;
  readonly strategy: string;
  readonly template_id?: string | null;
  readonly candidate_id?: string | null;
  readonly priority?: "urgent" | "normal" | "low";
  readonly params: ExecutionOrderParams;
  readonly limits: ExecutionOrderLimits;
  readonly solana_specific?: ExecutionOrderSolanaSpecific | null;
}

/**
 * SVM tx knobs — present when `chain === "solana"`. Adapters do NOT
 * apply these; the orders processor (Stream B) wraps the adapter's
 * instruction list with ComputeBudget + lookup-table accounts before
 * Squads signing. The block is exposed here so adapters can validate
 * or reference it (e.g. for logging) without bypassing the processor.
 */
export interface ExecutionOrderSolanaSpecific {
  readonly compute_unit_price?: number | null;
  readonly compute_unit_limit?: number | null;
  readonly lookup_tables?: readonly string[];
}

/**
 * Params block from the v2 schema. `extra` is the open envelope for
 * protocol-specific fields (e.g. Kamino's `reserve_address`).
 */
export interface ExecutionOrderParams {
  readonly token_in?: string | null;
  readonly token_out?: string | null;
  readonly amount?: string | null;
  readonly recipient?: string | null;
  readonly pool_id?: string | null;
  readonly venue?: string | null;
  readonly extra?: Record<string, unknown>;
}

/** Limits block from the v2 schema. */
export interface ExecutionOrderLimits {
  readonly max_gas_wei?: string | null;
  readonly max_priority_fee_lamports?: string | null;
  readonly max_slippage_bps: number;
  readonly deadline_unix: number;
}

/**
 * Common contract every Solana protocol adapter implements.
 */
export interface SolanaAdapter {
  /** Stable adapter identifier; matches the `protocol` field on orders it claims. */
  readonly name: string;
  /**
   * Build the unsigned instruction list for `order`. Throws on invalid input
   * or upstream failure — the processor converts thrown errors into an
   * execution-result with success=false.
   */
  buildInstructions(
    order: ExecutionOrder,
    connection: Connection,
  ): Promise<TransactionInstruction[]>;
}
