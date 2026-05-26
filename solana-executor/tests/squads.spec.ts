/**
 * SquadsSigner tests.
 *
 * - Single-signer fallback path (no multisig PDA): mock the @solana/web3.js
 *   Connection, assert signature returned, slot best-effort handled.
 * - Multisig path: clear error so an operator cannot accidentally ship a
 *   half-wired Squads integration.
 */
import { describe, it, expect, beforeEach, vi } from "vitest";
import pino from "pino";
import {
  Keypair,
  PublicKey,
  SystemProgram,
  type Connection,
} from "@solana/web3.js";
import { SquadsSigner, loadSquadsEnv } from "../src/wallet/squads.js";

const logger = pino({ level: "silent" });

function makeFakeConnection(overrides: Partial<Connection> = {}): Connection {
  const base = {
    getLatestBlockhash: vi.fn().mockResolvedValue({
      blockhash: "11111111111111111111111111111111",
      lastValidBlockHeight: 1000,
    }),
    sendRawTransaction: vi
      .fn()
      .mockResolvedValue("fake-signature-base58"),
    confirmTransaction: vi.fn().mockResolvedValue({ value: { err: null } }),
    getTransaction: vi.fn().mockResolvedValue({ slot: 12345 }),
  };
  return { ...base, ...overrides } as unknown as Connection;
}

function makeTransferIx(from: PublicKey, to: PublicKey) {
  return SystemProgram.transfer({
    fromPubkey: from,
    toPubkey: to,
    lamports: 1,
  });
}

describe("SquadsSigner single-signer fallback", () => {
  let keypair: Keypair;

  beforeEach(() => {
    keypair = Keypair.generate();
  });

  it("signs and broadcasts a single-signer tx when no multisigPda is set", async () => {
    const conn = makeFakeConnection();
    const signer = new SquadsSigner({
      connection: conn,
      memberKeypair: keypair,
      logger,
    });
    expect(signer.isMultisig).toBe(false);

    const ix = makeTransferIx(keypair.publicKey, Keypair.generate().publicKey);
    const outcome = await signer.proposeAndExecute([ix]);

    expect(outcome.signature).toBe("fake-signature-base58");
    expect(outcome.slot).toBe(12345);
    expect(conn.sendRawTransaction).toHaveBeenCalledTimes(1);
    expect(conn.confirmTransaction).toHaveBeenCalledTimes(1);
  });

  it("throws when given zero instructions", async () => {
    const conn = makeFakeConnection();
    const signer = new SquadsSigner({
      connection: conn,
      memberKeypair: keypair,
      logger,
    });
    await expect(signer.proposeAndExecute([])).rejects.toThrow(
      /at least one instruction/,
    );
  });

  it("propagates on-chain errors from confirmTransaction", async () => {
    const conn = makeFakeConnection({
      confirmTransaction: vi
        .fn()
        .mockResolvedValue({ value: { err: { InstructionError: [0, "Custom"] } } }),
    } as Partial<Connection>);
    const signer = new SquadsSigner({
      connection: conn,
      memberKeypair: keypair,
      logger,
    });
    const ix = makeTransferIx(keypair.publicKey, Keypair.generate().publicKey);
    await expect(signer.proposeAndExecute([ix])).rejects.toThrow(/failed on-chain/);
  });

  it("tolerates getTransaction failure (slot becomes null)", async () => {
    const conn = makeFakeConnection({
      getTransaction: vi.fn().mockRejectedValue(new Error("not indexed yet")),
    } as Partial<Connection>);
    const signer = new SquadsSigner({
      connection: conn,
      memberKeypair: keypair,
      logger,
    });
    const ix = makeTransferIx(keypair.publicKey, Keypair.generate().publicKey);
    const outcome = await signer.proposeAndExecute([ix]);
    expect(outcome.signature).toBe("fake-signature-base58");
    expect(outcome.slot).toBeNull();
  });
});

describe("SquadsSigner multisig mode (operator scaffold)", () => {
  it("throws a clear error when SOLANA_MULTISIG_PDA is set", async () => {
    const conn = makeFakeConnection();
    const signer = new SquadsSigner({
      connection: conn,
      memberKeypair: Keypair.generate(),
      multisigPda: new PublicKey("11111111111111111111111111111111"),
      logger,
    });
    expect(signer.isMultisig).toBe(true);

    const ix = makeTransferIx(
      Keypair.generate().publicKey,
      Keypair.generate().publicKey,
    );
    await expect(signer.proposeAndExecute([ix])).rejects.toThrow(
      /Squads multisig path is not implemented in W7 v1/,
    );
  });
});

describe("loadSquadsEnv", () => {
  it("throws when SOLANA_RPC_URL is missing", () => {
    expect(() =>
      loadSquadsEnv({ SOLANA_MEMBER_KEYPAIR_PATH: "/tmp/k.json" }),
    ).toThrow(/SOLANA_RPC_URL/);
  });

  it("throws when SOLANA_MEMBER_KEYPAIR_PATH is missing", () => {
    expect(() =>
      loadSquadsEnv({ SOLANA_RPC_URL: "https://example.com" }),
    ).toThrow(/SOLANA_MEMBER_KEYPAIR_PATH/);
  });

  it("returns a struct with optional multisig + priority fee", () => {
    const env = loadSquadsEnv({
      SOLANA_RPC_URL: "https://example.com",
      SOLANA_MEMBER_KEYPAIR_PATH: "/tmp/k.json",
      SOLANA_MULTISIG_PDA: "11111111111111111111111111111111",
      SOLANA_PRIORITY_FEE_MICROLAMPORTS: "5000",
    });
    expect(env.rpcUrl).toBe("https://example.com");
    expect(env.multisigPda).toBe("11111111111111111111111111111111");
    expect(env.priorityFeeMicrolamports).toBe("5000");
  });
});
