/**
 * Tests for the Jupiter v6 swap adapter.
 *
 * Mocks the Jupiter HTTP API by constructing a real VersionedTransaction in
 * memory (so the deserialize + decompile path runs against authentic bytes)
 * and serving its base64 from a `fetch` stub.
 */

import { describe, expect, it } from "vitest";
import {
  Keypair,
  PublicKey,
  SystemProgram,
  TransactionMessage,
  VersionedTransaction,
  type Connection,
} from "@solana/web3.js";

import { JupiterAdapter } from "../../src/adapters/jupiter.js";
import type { ExecutionOrder } from "../../src/adapters/types.js";

const SOL_MINT = "So11111111111111111111111111111111111111112";
const USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v";

function buildOrder(overrides: Partial<ExecutionOrder> = {}): ExecutionOrder {
  return {
    version: "1.0.0",
    order_id: "ord-jup-test-0001",
    correlation_id: "corr-jup-test-0001",
    timestamp: "2026-05-25T00:00:00.000Z",
    chain: "solana",
    protocol: "jupiter",
    action: "swap",
    strategy: "TPL:CAND",
    params: {
      token_in: SOL_MINT,
      token_out: USDC_MINT,
      amount: "1000000",
    },
    limits: {
      max_slippage_bps: 50,
      deadline_unix: 1_800_000_000,
    },
    ...overrides,
  };
}

/** Build a VersionedTransaction holding `count` System.transfer ixs. */
function buildVtxBase64(count: number): { b64: string; payer: PublicKey } {
  const payer = Keypair.generate().publicKey;
  const ixs = Array.from({ length: count }, () =>
    SystemProgram.transfer({
      fromPubkey: payer,
      toPubkey: Keypair.generate().publicKey,
      lamports: 1,
    }),
  );
  const msg = new TransactionMessage({
    payerKey: payer,
    // Real blockhash isn't required — the bytes just need to round-trip.
    recentBlockhash: "11111111111111111111111111111111",
    instructions: ixs,
  }).compileToV0Message();
  const vtx = new VersionedTransaction(msg);
  return { b64: Buffer.from(vtx.serialize()).toString("base64"), payer };
}

/** Connection stub — never called for these no-LUT transactions. */
const stubConnection = {
  getAddressLookupTable: async () => {
    throw new Error("getAddressLookupTable should not be invoked when no LUTs are referenced");
  },
} as unknown as Connection;

describe("JupiterAdapter", () => {
  it("returns instructions extracted from the swap tx (happy path)", async () => {
    const { b64 } = buildVtxBase64(3);
    let quoteCalls = 0;
    let swapCalls = 0;
    const fetchImpl = (async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.includes("/v6/swap")) {
        swapCalls += 1;
        expect(init?.method).toBe("POST");
        return new Response(JSON.stringify({ swapTransaction: b64 }), {
          status: 200,
          headers: { "content-type": "application/json" },
        });
      }
      if (url.includes("/v6/quote")) {
        quoteCalls += 1;
        return new Response(
          JSON.stringify({
            inputMint: SOL_MINT,
            outputMint: USDC_MINT,
            inAmount: "1000000",
            outAmount: "23456",
            slippageBps: 50,
          }),
          { status: 200, headers: { "content-type": "application/json" } },
        );
      }
      throw new Error(`unexpected fetch url ${url}`);
    }) as typeof fetch;

    const adapter = new JupiterAdapter({ fetchImpl });
    const ixs = await adapter.buildInstructions(buildOrder(), stubConnection);
    expect(ixs).toHaveLength(3);
    expect(quoteCalls).toBe(1);
    expect(swapCalls).toBe(1);
    // Every instruction should target the System program.
    for (const ix of ixs) {
      expect(ix.programId.equals(SystemProgram.programId)).toBe(true);
    }
  });

  it("throws clearly on a non-200 quote response", async () => {
    const fetchImpl = (async () =>
      new Response("upstream rate limited", { status: 429 })) as typeof fetch;
    const adapter = new JupiterAdapter({ fetchImpl });
    await expect(adapter.buildInstructions(buildOrder(), stubConnection)).rejects.toThrow(
      /jupiter: quote HTTP 429/,
    );
  });

  it("throws clearly on a non-200 swap response", async () => {
    const fetchImpl = (async (input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.includes("/v6/quote")) {
        return new Response(
          JSON.stringify({
            inputMint: SOL_MINT,
            outputMint: USDC_MINT,
            inAmount: "1000000",
            outAmount: "23456",
            slippageBps: 50,
          }),
          { status: 200, headers: { "content-type": "application/json" } },
        );
      }
      return new Response("no route", { status: 502 });
    }) as typeof fetch;
    const adapter = new JupiterAdapter({ fetchImpl });
    await expect(adapter.buildInstructions(buildOrder(), stubConnection)).rejects.toThrow(
      /jupiter: swap HTTP 502/,
    );
  });

  it("rejects orders missing required params", async () => {
    const fetchImpl = (async () => {
      throw new Error("fetch must not run for invalid orders");
    }) as typeof fetch;
    const adapter = new JupiterAdapter({ fetchImpl });
    const order = buildOrder({
      params: { token_in: SOL_MINT, token_out: USDC_MINT, amount: null },
    });
    await expect(adapter.buildInstructions(order, stubConnection)).rejects.toThrow(
      /params\.token_in, params\.token_out, and params\.amount/,
    );
  });

  it("rejects non-swap actions", async () => {
    const fetchImpl = (async () => {
      throw new Error("fetch must not run for invalid orders");
    }) as typeof fetch;
    const adapter = new JupiterAdapter({ fetchImpl });
    const order = buildOrder({ action: "deposit" });
    await expect(adapter.buildInstructions(order, stubConnection)).rejects.toThrow(
      /unsupported action deposit/,
    );
  });
});
