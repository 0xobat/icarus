/**
 * Squads multisig signer for the Solana executor.
 *
 * Two modes:
 *  - **Multisig mode** (production target): wraps a list of instructions
 *    into a Squads vault transaction + proposal + approval, then executes.
 *    Activated when SOLANA_MULTISIG_PDA is set in env.
 *  - **Single-signer fallback** (operator scaffold / local dev / tests):
 *    the member keypair signs and submits a plain `Transaction` directly.
 *    Activated when SOLANA_MULTISIG_PDA is unset.
 *
 * For W7 v1 the Squads path is marked `TODO(operator)` — operators must
 * supply a real multisig PDA and confirm the @sqds/multisig version pins
 * against the on-chain program before going live. The single-signer path
 * is exercised by tests and by the local docker-compose flow.
 *
 * Env contract (read at construct time, not at sign time):
 *  - SOLANA_RPC_URL           — required.
 *  - SOLANA_MULTISIG_PDA      — optional. Present ⇒ multisig mode.
 *  - SOLANA_MEMBER_KEYPAIR_PATH — required. Path to a JSON keypair file
 *                                 (the Solana CLI standard format).
 *  - SOLANA_PRIORITY_FEE_MICROLAMPORTS — optional. Added as a
 *                                         ComputeBudget priority fee.
 */
import { readFileSync } from "node:fs";
import {
  ComputeBudgetProgram,
  Connection,
  Keypair,
  PublicKey,
  Transaction,
  type TransactionInstruction,
} from "@solana/web3.js";
import type { Logger } from "pino";

export interface SquadsSignerEnv {
  rpcUrl: string;
  multisigPda?: string | undefined;
  memberKeypairPath: string;
  priorityFeeMicrolamports?: string | undefined;
}

/**
 * Read and validate the env contract for SquadsSigner. Throws if a required
 * variable is missing. Returns an explicit struct so the construction site
 * can audit what was actually loaded.
 */
export function loadSquadsEnv(env: NodeJS.ProcessEnv = process.env): SquadsSignerEnv {
  const rpcUrl = env.SOLANA_RPC_URL;
  if (!rpcUrl) {
    throw new Error("SOLANA_RPC_URL is required");
  }
  const memberKeypairPath = env.SOLANA_MEMBER_KEYPAIR_PATH;
  if (!memberKeypairPath) {
    throw new Error("SOLANA_MEMBER_KEYPAIR_PATH is required");
  }
  return {
    rpcUrl,
    multisigPda: env.SOLANA_MULTISIG_PDA,
    memberKeypairPath,
    priorityFeeMicrolamports: env.SOLANA_PRIORITY_FEE_MICROLAMPORTS,
  };
}

/**
 * Load a Solana CLI–style JSON keypair from disk (array of 64 bytes).
 */
export function loadKeypair(path: string): Keypair {
  const raw = readFileSync(path, "utf-8");
  const parsed = JSON.parse(raw) as unknown;
  if (!Array.isArray(parsed) || parsed.length !== 64) {
    throw new Error(
      `Invalid keypair file at ${path}: expected JSON array of 64 bytes`,
    );
  }
  return Keypair.fromSecretKey(Uint8Array.from(parsed as number[]));
}

export interface SquadsSignerOptions {
  connection: Connection;
  memberKeypair: Keypair;
  logger: Logger;
  /** Squads multisig PDA. If omitted, single-signer fallback is used. */
  multisigPda?: PublicKey;
  /** Optional ComputeBudget priority fee, in microlamports per CU. */
  priorityFeeMicrolamports?: bigint;
}

export interface ExecuteOutcome {
  signature: string;
  slot: number | null;
}

/**
 * Wraps Squads proposal + execute (or a direct single-signer send).
 * One instance per process.
 */
export class SquadsSigner {
  readonly connection: Connection;
  readonly memberKeypair: Keypair;
  readonly multisigPda: PublicKey | null;
  private readonly priorityFee: bigint | null;
  private readonly logger: Logger;

  constructor(opts: SquadsSignerOptions) {
    this.connection = opts.connection;
    this.memberKeypair = opts.memberKeypair;
    this.multisigPda = opts.multisigPda ?? null;
    this.priorityFee = opts.priorityFeeMicrolamports ?? null;
    this.logger = opts.logger;
  }

  /** True when running against a real Squads multisig. */
  get isMultisig(): boolean {
    return this.multisigPda !== null;
  }

  /**
   * Build a Squads proposal (or single-signer tx) containing `instructions`,
   * sign with the member keypair, broadcast, and wait for confirmation.
   *
   * Returns the broadcast signature + (best-effort) slot. Throws on
   * pre-flight failure or broadcast error.
   */
  async proposeAndExecute(
    instructions: TransactionInstruction[],
  ): Promise<ExecuteOutcome> {
    if (instructions.length === 0) {
      throw new Error("proposeAndExecute requires at least one instruction");
    }

    const enriched = this.withPriorityFee(instructions);

    if (this.multisigPda === null) {
      // ── Single-signer fallback ───────────────────────────────────────
      // For local dev + tests; mirrors what the operator sees before they
      // configure the real Squads multisig. NOT a long-term path.
      this.logger.warn(
        { mode: "single_signer_fallback" },
        "squads_signer_single_signer_mode",
      );
      return this.executeSingleSigner(enriched);
    }

    // ── Multisig mode ────────────────────────────────────────────────────
    // TODO(operator): replace with real Squads multisig program ID +
    // member keypair from env. The @sqds/multisig SDK exposes
    // `transactions.vaultTransactionCreate`, `proposals.proposalCreate`,
    // `proposals.proposalApprove`, and `transactions.vaultTransactionExecute`.
    // The exact call sequence depends on the on-chain program version
    // pinned at deploy time; do not assume the SDK README is current.
    //
    // For W7 v1 we surface a clear error rather than half-implement, so the
    // operator cannot accidentally ship a broken Squads path.
    throw new Error(
      "Squads multisig path is not implemented in W7 v1. " +
        "Operator scaffold: set SOLANA_MULTISIG_PDA only after wiring " +
        "@sqds/multisig vault-tx-create + proposal-approve + execute. " +
        `Multisig PDA was: ${this.multisigPda.toBase58()}`,
    );
  }

  private withPriorityFee(
    instructions: TransactionInstruction[],
  ): TransactionInstruction[] {
    if (this.priorityFee === null) return instructions;
    const fee = ComputeBudgetProgram.setComputeUnitPrice({
      microLamports: this.priorityFee,
    });
    return [fee, ...instructions];
  }

  private async executeSingleSigner(
    instructions: TransactionInstruction[],
  ): Promise<ExecuteOutcome> {
    const { blockhash, lastValidBlockHeight } =
      await this.connection.getLatestBlockhash();
    const tx = new Transaction({
      feePayer: this.memberKeypair.publicKey,
      blockhash,
      lastValidBlockHeight,
    });
    for (const ix of instructions) tx.add(ix);
    tx.sign(this.memberKeypair);

    const signature = await this.connection.sendRawTransaction(tx.serialize(), {
      skipPreflight: false,
      maxRetries: 3,
    });

    const confirmation = await this.connection.confirmTransaction(
      { signature, blockhash, lastValidBlockHeight },
      "confirmed",
    );
    if (confirmation.value.err) {
      throw new Error(
        `Single-signer tx failed on-chain: ${JSON.stringify(confirmation.value.err)}`,
      );
    }

    // Slot is best-effort — getTransaction may not be indexed yet on some RPCs.
    let slot: number | null = null;
    try {
      const fetched = await this.connection.getTransaction(signature, {
        commitment: "confirmed",
        maxSupportedTransactionVersion: 0,
      });
      slot = fetched?.slot ?? null;
    } catch {
      // Swallow — slot is informational.
    }

    return { signature, slot };
  }
}
