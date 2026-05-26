/**
 * Kamino Lend (klend) adapter.
 *
 * Builds deposit / withdraw instructions for Kamino Lend reserves. The
 * intended implementation calls `@kamino-finance/klend-sdk`'s KaminoMarket +
 * KaminoAction helpers, which return the canonical instruction list (init
 * obligation if missing, ATA setup, deposit/withdraw).
 *
 * TODO(operator): The Kamino SDK (`@kamino-finance/klend-sdk`) is not yet
 * pinned in `solana-executor/package.json`. Once Stream B confirms the wider
 * service tree is stable, add the dep:
 *
 *   pnpm --filter solana-executor add @kamino-finance/klend-sdk
 *
 * Until then `buildInstructions` raises {@link NotImplementedError} so calling
 * code fails loudly instead of silently no-op'ing. The order-shape parsing
 * and dispatch (deposit/withdraw/unknown-action) are exercised by tests so
 * the wiring is locked in.
 *
 * v2 schema fields consumed:
 *  - action                       → "deposit" | "withdraw"
 *  - params.amount                → atomic amount (stringified)
 *  - params.token_in              → reserve liquidity mint
 *  - params.pool_id               → KaminoMarket address (lending market)
 *  - params.extra.reserve_address → reserve PDA (preferred over symbol resolution)
 */

import { PublicKey, type Connection, type TransactionInstruction } from "@solana/web3.js";
import pino from "pino";

import type { ExecutionOrder, SolanaAdapter } from "./types.js";

const logger = pino({ name: "adapter.kamino" });

/**
 * Thrown when the Kamino SDK is not installed. Distinct from input-validation
 * errors so the orders processor can surface a clear "operator must install
 * dep" message in execution-results.
 */
export class NotImplementedError extends Error {
  public override readonly name = "NotImplementedError";
  /** Construct with an operator-facing message. */
  public constructor(message: string) {
    super(message);
  }
}

/** Options for {@link KaminoAdapter}. Mostly for tests (SDK + owner injection). */
export interface KaminoAdapterOptions {
  /**
   * Pubkey that owns the obligation (the Squads vault PDA in production).
   * Required for any real instruction build. Optional only so tests can
   * default it.
   */
  readonly owner?: PublicKey;
  /**
   * Test seam: an in-memory Kamino SDK shim. Production code leaves this
   * unset and the adapter dynamically imports `@kamino-finance/klend-sdk`.
   */
  readonly sdk?: KaminoSdkShim;
}

/**
 * Minimal surface of the Kamino SDK the adapter calls. Real type:
 * `import('@kamino-finance/klend-sdk')`. Kept as an internal shim so tests
 * can mock without dragging the real SDK in.
 */
export interface KaminoSdkShim {
  buildDepositInstructions(args: KaminoActionArgs): Promise<TransactionInstruction[]>;
  buildWithdrawInstructions(args: KaminoActionArgs): Promise<TransactionInstruction[]>;
}

/** Common args passed to both deposit and withdraw shim methods. */
export interface KaminoActionArgs {
  readonly connection: Connection;
  readonly market: PublicKey;
  readonly reserve: PublicKey;
  readonly mint: PublicKey;
  readonly owner: PublicKey;
  readonly amount: bigint;
}

/** Kamino Lend adapter. Implements {@link SolanaAdapter}. */
export class KaminoAdapter implements SolanaAdapter {
  public readonly name = "kamino";

  private readonly owner: PublicKey | undefined;
  private readonly sdk: KaminoSdkShim | undefined;

  /** Construct a Kamino adapter. Pass `sdk` in tests; production leaves it unset. */
  public constructor(opts: KaminoAdapterOptions = {}) {
    this.owner = opts.owner;
    this.sdk = opts.sdk;
  }

  /**
   * Build the unsigned deposit/withdraw instructions for `order`.
   *
   * Throws {@link NotImplementedError} if the Kamino SDK is not installed.
   */
  public async buildInstructions(
    order: ExecutionOrder,
    connection: Connection,
  ): Promise<TransactionInstruction[]> {
    if (order.chain !== "solana") {
      throw new Error(`kamino: unsupported chain ${order.chain}`);
    }

    const args = this.parseOrder(order, connection);
    const sdk = await this.resolveSdk();

    let ixs: TransactionInstruction[];
    switch (order.action) {
      case "deposit":
      case "supply":
        ixs = await sdk.buildDepositInstructions(args);
        break;
      case "withdraw":
        ixs = await sdk.buildWithdrawInstructions(args);
        break;
      default:
        throw new Error(
          `kamino: unsupported action ${order.action} (expected 'deposit' | 'supply' | 'withdraw')`,
        );
    }

    logger.info(
      {
        order_id: order.order_id,
        correlation_id: order.correlation_id,
        action: order.action,
        ix_count: ixs.length,
        reserve: args.reserve.toBase58(),
      },
      "kamino_build_instructions_ok",
    );
    return ixs;
  }

  /** Parse and validate the order into typed action args. */
  private parseOrder(order: ExecutionOrder, connection: Connection): KaminoActionArgs {
    const { token_in, amount, pool_id, extra } = order.params;
    if (!amount) throw new Error("kamino: params.amount is required");
    if (!token_in) throw new Error("kamino: params.token_in (reserve mint) is required");
    if (!pool_id) throw new Error("kamino: params.pool_id (KaminoMarket address) is required");

    const reserveAddress =
      typeof extra?.reserve_address === "string" ? extra.reserve_address : undefined;
    if (!reserveAddress) {
      throw new Error(
        "kamino: params.extra.reserve_address is required (symbol resolution not yet supported)",
      );
    }

    const owner = this.owner;
    if (!owner) {
      throw new Error(
        "kamino: adapter constructed without `owner` pubkey (Squads vault PDA required)",
      );
    }

    let amountBig: bigint;
    try {
      amountBig = BigInt(amount);
    } catch {
      throw new Error(`kamino: params.amount ${amount} is not a valid integer`);
    }
    if (amountBig <= 0n) throw new Error(`kamino: params.amount must be > 0, got ${amount}`);

    return {
      connection,
      market: new PublicKey(pool_id),
      reserve: new PublicKey(reserveAddress),
      mint: new PublicKey(token_in),
      owner,
      amount: amountBig,
    };
  }

  /** Resolve the SDK shim, dynamically importing the real package if needed. */
  private async resolveSdk(): Promise<KaminoSdkShim> {
    if (this.sdk) return this.sdk;
    throw new NotImplementedError(
      "kamino: @kamino-finance/klend-sdk is not installed. Run " +
        "`pnpm --filter solana-executor add @kamino-finance/klend-sdk` " +
        "and wire the real SDK in adapters/kamino.ts (resolveSdk).",
    );
  }
}
