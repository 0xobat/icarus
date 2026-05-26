/**
 * Tests for the Kamino Lend adapter.
 *
 * Mocks the SDK by injecting a {@link KaminoSdkShim} that records calls and
 * returns synthetic instructions. Also verifies the NotImplementedError
 * path when no SDK is wired (default constructor).
 */

import { describe, expect, it, vi } from "vitest";
import {
  Keypair,
  PublicKey,
  SystemProgram,
  TransactionInstruction,
  type Connection,
} from "@solana/web3.js";

import {
  KaminoAdapter,
  NotImplementedError,
  type KaminoSdkShim,
} from "../../src/adapters/kamino.js";
import type { ExecutionOrder } from "../../src/adapters/types.js";

const USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v";
const MAIN_MARKET = "7u3HeHxYDLhnCoErrtycNokbQYbWGzLs6JSDqGAv5K58";
const USDC_RESERVE = "D6q6wuQSrifJKZYpR1M8R4YawnLDtDsMmWM1NbBmgJ59";

function makeIx(): TransactionInstruction {
  return SystemProgram.transfer({
    fromPubkey: Keypair.generate().publicKey,
    toPubkey: Keypair.generate().publicKey,
    lamports: 1,
  });
}

function buildOrder(overrides: Partial<ExecutionOrder> = {}): ExecutionOrder {
  return {
    version: "1.0.0",
    order_id: "ord-kamino-test-0001",
    correlation_id: "corr-kamino-test-0001",
    timestamp: "2026-05-25T00:00:00.000Z",
    chain: "solana",
    protocol: "kamino",
    action: "deposit",
    strategy: "TPL:CAND",
    params: {
      token_in: USDC_MINT,
      amount: "1000000",
      pool_id: MAIN_MARKET,
      extra: { reserve_address: USDC_RESERVE },
    },
    limits: {
      max_slippage_bps: 50,
      deadline_unix: 1_800_000_000,
    },
    ...overrides,
  };
}

const stubConnection = {} as Connection;

describe("KaminoAdapter", () => {
  it("dispatches deposit orders to the SDK shim and returns its instructions", async () => {
    const ixs = [makeIx(), makeIx()];
    const sdk: KaminoSdkShim = {
      buildDepositInstructions: vi.fn(async () => ixs),
      buildWithdrawInstructions: vi.fn(async () => {
        throw new Error("withdraw should not be called");
      }),
    };
    const adapter = new KaminoAdapter({ owner: Keypair.generate().publicKey, sdk });

    const out = await adapter.buildInstructions(buildOrder(), stubConnection);
    expect(out).toEqual(ixs);
    expect(sdk.buildDepositInstructions).toHaveBeenCalledOnce();
    const call = vi.mocked(sdk.buildDepositInstructions).mock.calls[0]?.[0];
    expect(call?.market.toBase58()).toBe(MAIN_MARKET);
    expect(call?.reserve.toBase58()).toBe(USDC_RESERVE);
    expect(call?.mint.toBase58()).toBe(USDC_MINT);
    expect(call?.amount).toBe(1_000_000n);
  });

  it("also accepts the `supply` action alias for deposits", async () => {
    const ixs = [makeIx()];
    const sdk: KaminoSdkShim = {
      buildDepositInstructions: vi.fn(async () => ixs),
      buildWithdrawInstructions: vi.fn(async () => []),
    };
    const adapter = new KaminoAdapter({ owner: Keypair.generate().publicKey, sdk });
    const out = await adapter.buildInstructions(buildOrder({ action: "supply" }), stubConnection);
    expect(out).toHaveLength(1);
    expect(sdk.buildDepositInstructions).toHaveBeenCalledOnce();
  });

  it("dispatches withdraw orders to the SDK shim", async () => {
    const ixs = [makeIx(), makeIx(), makeIx()];
    const sdk: KaminoSdkShim = {
      buildDepositInstructions: vi.fn(async () => {
        throw new Error("deposit should not be called");
      }),
      buildWithdrawInstructions: vi.fn(async () => ixs),
    };
    const adapter = new KaminoAdapter({ owner: Keypair.generate().publicKey, sdk });
    const out = await adapter.buildInstructions(
      buildOrder({ action: "withdraw" }),
      stubConnection,
    );
    expect(out).toEqual(ixs);
    expect(sdk.buildWithdrawInstructions).toHaveBeenCalledOnce();
  });

  it("rejects unknown actions with a clear message", async () => {
    const sdk: KaminoSdkShim = {
      buildDepositInstructions: vi.fn(),
      buildWithdrawInstructions: vi.fn(),
    };
    const adapter = new KaminoAdapter({ owner: Keypair.generate().publicKey, sdk });
    const order = buildOrder({ action: "open_perp" });
    await expect(adapter.buildInstructions(order, stubConnection)).rejects.toThrow(
      /unsupported action open_perp/,
    );
  });

  it("rejects when params.extra.reserve_address is missing", async () => {
    const sdk: KaminoSdkShim = {
      buildDepositInstructions: vi.fn(),
      buildWithdrawInstructions: vi.fn(),
    };
    const adapter = new KaminoAdapter({ owner: Keypair.generate().publicKey, sdk });
    const order = buildOrder({
      params: { token_in: USDC_MINT, amount: "1", pool_id: MAIN_MARKET, extra: {} },
    });
    await expect(adapter.buildInstructions(order, stubConnection)).rejects.toThrow(
      /reserve_address is required/,
    );
  });

  it("rejects amount <= 0", async () => {
    const sdk: KaminoSdkShim = {
      buildDepositInstructions: vi.fn(),
      buildWithdrawInstructions: vi.fn(),
    };
    const adapter = new KaminoAdapter({ owner: Keypair.generate().publicKey, sdk });
    const order = buildOrder({
      params: {
        token_in: USDC_MINT,
        amount: "0",
        pool_id: MAIN_MARKET,
        extra: { reserve_address: USDC_RESERVE },
      },
    });
    await expect(adapter.buildInstructions(order, stubConnection)).rejects.toThrow(
      /amount must be > 0/,
    );
  });

  it("rejects when constructed without an owner pubkey", async () => {
    const sdk: KaminoSdkShim = {
      buildDepositInstructions: vi.fn(),
      buildWithdrawInstructions: vi.fn(),
    };
    const adapter = new KaminoAdapter({ sdk });
    await expect(adapter.buildInstructions(buildOrder(), stubConnection)).rejects.toThrow(
      /owner` pubkey/,
    );
  });

  it("raises NotImplementedError when SDK is not installed", async () => {
    const adapter = new KaminoAdapter({ owner: Keypair.generate().publicKey });
    await expect(adapter.buildInstructions(buildOrder(), stubConnection)).rejects.toBeInstanceOf(
      NotImplementedError,
    );
  });
});

describe("adapters/index registry", () => {
  it("exposes both adapters via loadAdapter and rejects unknown names", async () => {
    const mod = await import("../../src/adapters/index.js");
    expect(mod.loadAdapter("jupiter").name).toBe("jupiter");
    expect(mod.loadAdapter("kamino").name).toBe("kamino");
    expect(() => mod.loadAdapter("drift")).toThrow(/unknown adapter 'drift'/);
    expect(Object.keys(mod.ADAPTERS).sort()).toEqual(["jupiter", "kamino"]);
    // Construct a placeholder so PublicKey isn't dead-code-eliminated.
    expect(new PublicKey("11111111111111111111111111111111")).toBeDefined();
  });
});
