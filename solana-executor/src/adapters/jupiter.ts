/**
 * Jupiter v6 swap adapter.
 *
 * Calls the Jupiter v6 quote + swap REST API directly (no SDK). The /swap
 * endpoint returns a fully serialized VersionedTransaction; we deserialize
 * it, walk the compiled message, and lift each instruction out as a plain
 * {@link TransactionInstruction}. The orders processor then re-bundles the
 * instructions into Squads-signed transactions before broadcast.
 *
 * v2 schema fields consumed:
 *  - params.token_in   → input mint
 *  - params.token_out  → output mint
 *  - params.amount     → atomic input amount (stringified)
 *  - limits.max_slippage_bps → forwarded to Jupiter as slippageBps
 *
 * The adapter does NOT sign, simulate, or submit. It only builds.
 */

import {
  AddressLookupTableAccount,
  PublicKey,
  TransactionInstruction,
  TransactionMessage,
  VersionedTransaction,
  type Connection,
} from "@solana/web3.js";
import pino from "pino";

import type { ExecutionOrder, SolanaAdapter } from "./types.js";

const logger = pino({ name: "adapter.jupiter" });

/** Jupiter v6 quote endpoint. */
const QUOTE_URL = "https://quote-api.jup.ag/v6/quote";
/** Jupiter v6 swap endpoint (returns serialized VersionedTransaction). */
const SWAP_URL = "https://quote-api.jup.ag/v6/swap";

/**
 * Pubkey used as the swap `userPublicKey` when building the Jupiter tx. The
 * processor swaps this for the Squads vault PDA before signing — for
 * instruction-building, any 32-byte pubkey works because we drop the
 * compute-budget + signer headers and only keep the swap instructions.
 *
 * The processor MAY override via `JupiterAdapter.userPublicKey` if it wants
 * Jupiter to derive ATAs for the real signer up front.
 */
const PLACEHOLDER_USER = new PublicKey("11111111111111111111111111111111");

/** Subset of the Jupiter /quote response we actually consume. */
interface JupiterQuoteResponse {
  readonly inputMint: string;
  readonly outputMint: string;
  readonly inAmount: string;
  readonly outAmount: string;
  readonly slippageBps: number;
  // …many more fields. We pass the full body through to /swap unchanged.
}

/** Subset of the Jupiter /swap response we actually consume. */
interface JupiterSwapResponse {
  readonly swapTransaction: string;
  readonly addressLookupTableAddresses?: readonly string[];
}

/**
 * Options for constructing a {@link JupiterAdapter}. Mostly for tests:
 * inject `fetchImpl` to mock the network.
 */
export interface JupiterAdapterOptions {
  readonly userPublicKey?: PublicKey;
  readonly fetchImpl?: typeof fetch;
  readonly quoteUrl?: string;
  readonly swapUrl?: string;
}

/**
 * Jupiter v6 swap adapter. Implements {@link SolanaAdapter}.
 */
export class JupiterAdapter implements SolanaAdapter {
  public readonly name = "jupiter";

  private readonly userPublicKey: PublicKey;
  private readonly fetchImpl: typeof fetch;
  private readonly quoteUrl: string;
  private readonly swapUrl: string;

  /**
   * Construct a Jupiter adapter. All options are optional; defaults target
   * the public Jupiter v6 API with a placeholder user pubkey.
   */
  public constructor(opts: JupiterAdapterOptions = {}) {
    this.userPublicKey = opts.userPublicKey ?? PLACEHOLDER_USER;
    // Bind to globalThis so node's fetch retains its receiver.
    this.fetchImpl = opts.fetchImpl ?? fetch.bind(globalThis);
    this.quoteUrl = opts.quoteUrl ?? QUOTE_URL;
    this.swapUrl = opts.swapUrl ?? SWAP_URL;
  }

  /**
   * Build the unsigned swap instructions for `order` using Jupiter v6.
   */
  public async buildInstructions(
    order: ExecutionOrder,
    connection: Connection,
  ): Promise<TransactionInstruction[]> {
    if (order.chain !== "solana") {
      throw new Error(`jupiter: unsupported chain ${order.chain}`);
    }
    if (order.action !== "swap") {
      throw new Error(`jupiter: unsupported action ${order.action} (expected 'swap')`);
    }
    const { token_in, token_out, amount } = order.params;
    if (!token_in || !token_out || !amount) {
      throw new Error(
        "jupiter: params.token_in, params.token_out, and params.amount are required",
      );
    }
    const slippageBps = order.limits.max_slippage_bps;
    if (!Number.isInteger(slippageBps) || slippageBps < 0) {
      throw new Error(`jupiter: invalid limits.max_slippage_bps ${slippageBps}`);
    }

    const quote = await this.fetchQuote(token_in, token_out, amount, slippageBps, order.order_id);
    const swap = await this.fetchSwap(quote, order.order_id);
    const ixs = await this.decodeSwapTransaction(swap, connection);

    logger.info(
      {
        order_id: order.order_id,
        correlation_id: order.correlation_id,
        ix_count: ixs.length,
        in_amount: quote.inAmount,
        out_amount: quote.outAmount,
      },
      "jupiter_build_instructions_ok",
    );
    return ixs;
  }

  /** Fetch a Jupiter v6 quote. */
  private async fetchQuote(
    inputMint: string,
    outputMint: string,
    amount: string,
    slippageBps: number,
    orderId: string,
  ): Promise<JupiterQuoteResponse> {
    const url = new URL(this.quoteUrl);
    url.searchParams.set("inputMint", inputMint);
    url.searchParams.set("outputMint", outputMint);
    url.searchParams.set("amount", amount);
    url.searchParams.set("slippageBps", String(slippageBps));
    url.searchParams.set("onlyDirectRoutes", "false");

    const res = await this.fetchImpl(url.toString(), { method: "GET" });
    if (!res.ok) {
      const body = await safeReadText(res);
      logger.error(
        { order_id: orderId, status: res.status, body: body.slice(0, 256) },
        "jupiter_quote_http_error",
      );
      throw new Error(`jupiter: quote HTTP ${res.status}: ${body.slice(0, 256)}`);
    }
    return (await res.json()) as JupiterQuoteResponse;
  }

  /** POST quote body to /swap and return the serialized swap transaction. */
  private async fetchSwap(
    quote: JupiterQuoteResponse,
    orderId: string,
  ): Promise<JupiterSwapResponse> {
    const body = JSON.stringify({
      quoteResponse: quote,
      userPublicKey: this.userPublicKey.toBase58(),
      wrapAndUnwrapSol: true,
      // We do not let Jupiter add a compute-budget ix; the processor manages
      // priority fees centrally so it can respect limits.max_priority_fee_lamports.
      computeUnitPriceMicroLamports: 0,
    });

    const res = await this.fetchImpl(this.swapUrl, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body,
    });
    if (!res.ok) {
      const errBody = await safeReadText(res);
      logger.error(
        { order_id: orderId, status: res.status, body: errBody.slice(0, 256) },
        "jupiter_swap_http_error",
      );
      throw new Error(`jupiter: swap HTTP ${res.status}: ${errBody.slice(0, 256)}`);
    }
    return (await res.json()) as JupiterSwapResponse;
  }

  /**
   * Deserialize Jupiter's swap transaction and lift the instructions out.
   * Resolves address-lookup-table accounts via `connection` so the decoded
   * instructions reference concrete pubkeys, not table indices.
   */
  private async decodeSwapTransaction(
    swap: JupiterSwapResponse,
    connection: Connection,
  ): Promise<TransactionInstruction[]> {
    const txBytes = Buffer.from(swap.swapTransaction, "base64");
    const vtx = VersionedTransaction.deserialize(txBytes);

    const lutAddrs = vtx.message.addressTableLookups.map((l) => l.accountKey);
    const luts: AddressLookupTableAccount[] = [];
    for (const addr of lutAddrs) {
      const fetched = await connection.getAddressLookupTable(addr);
      if (!fetched.value) {
        throw new Error(`jupiter: address lookup table ${addr.toBase58()} not found`);
      }
      luts.push(fetched.value);
    }

    const decoded = TransactionMessage.decompile(vtx.message, {
      addressLookupTableAccounts: luts,
    });
    return decoded.instructions;
  }
}

/** Read response body as text without throwing if the stream is already consumed. */
async function safeReadText(res: Response): Promise<string> {
  try {
    return await res.text();
  } catch {
    return "";
  }
}
