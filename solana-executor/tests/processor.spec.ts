/**
 * End-to-end processor tests.
 *
 * ExecutionOrder → mock adapter returns 1 instruction → mock SquadsSigner
 * returns a fake signature → ResultsPublisher writes an ExecutionResult
 * envelope to the results channel.
 *
 * All Solana RPC + Squads side-effects are mocked; no network is touched.
 */
import { describe, it, expect, beforeEach, vi } from "vitest";
import pino from "pino";
import {
  Keypair,
  SystemProgram,
  type Connection,
  type TransactionInstruction,
} from "@solana/web3.js";
import { OrderProcessor, type AdapterRegistry } from "../src/processor.js";
import { ResultsPublisher, RESULTS_CHANNEL } from "../src/redis/publisher.js";
import { SquadsSigner } from "../src/wallet/squads.js";
import type {
  ExecutionOrder,
  SolanaAdapter,
} from "../src/adapters/types.js";
import type { ClaimedOrder } from "../src/redis/consumer.js";

/** Minimal RPUSH-only fake. */
class FakeRedis {
  lists = new Map<string, string[]>();
  async rpush(key: string, ...values: string[]): Promise<number> {
    const list = this.lists.get(key) ?? [];
    list.push(...values);
    this.lists.set(key, list);
    return list.length;
  }
}

const logger = pino({ level: "silent" });

function makeOrder(overrides: Partial<ExecutionOrder> = {}): ExecutionOrder {
  return {
    version: "1.0.0",
    order_id: "ord-test-001",
    correlation_id: "corr-test-001",
    timestamp: "2026-05-25T12:00:00.000Z",
    chain: "solana",
    protocol: "jupiter",
    action: "swap",
    strategy: "BASIS-PERP-001:cand-1",
    template_id: "BASIS-PERP-001",
    candidate_id: "cand-1",
    priority: "normal",
    params: {
      token_in: "So11111111111111111111111111111111111111112",
      token_out: "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
      amount: "1000000",
      extra: {},
    },
    limits: {
      max_slippage_bps: 50,
      deadline_unix: 9999999999,
      max_priority_fee_lamports: "5000",
    },
    solana_specific: {
      compute_unit_price: 5000,
      compute_unit_limit: 200000,
      lookup_tables: [],
    },
    ...overrides,
  } as ExecutionOrder;
}

function makeMockAdapter(name: string, ix: TransactionInstruction): SolanaAdapter {
  return {
    name,
    buildInstructions: vi
      .fn<(order: ExecutionOrder, conn: Connection) => Promise<TransactionInstruction[]>>()
      .mockResolvedValue([ix]),
  };
}

function makeClaimed(order: ExecutionOrder): ClaimedOrder {
  return { raw: JSON.stringify(order), order };
}

describe("OrderProcessor", () => {
  let fakeRedis: FakeRedis;
  let publisher: ResultsPublisher;
  let signer: SquadsSigner;
  let keypair: Keypair;
  let ix: TransactionInstruction;

  beforeEach(() => {
    fakeRedis = new FakeRedis();
    publisher = new ResultsPublisher({
      redis: fakeRedis as unknown as import("ioredis").default,
      logger,
    });
    keypair = Keypair.generate();
    ix = SystemProgram.transfer({
      fromPubkey: keypair.publicKey,
      toPubkey: Keypair.generate().publicKey,
      lamports: 1,
    });
    signer = new SquadsSigner({
      connection: {} as Connection,
      memberKeypair: keypair,
      logger,
    });
    vi.spyOn(signer, "proposeAndExecute").mockResolvedValue({
      signature: "fake-sig-XYZ",
      slot: 42,
    });
  });

  it("happy path: dispatches to adapter, signs, publishes confirmed result", async () => {
    const adapter = makeMockAdapter("jupiter", ix);
    const registry: AdapterRegistry = new Map([["jupiter", adapter]]);
    const processor = new OrderProcessor({
      adapters: registry,
      signer,
      publisher,
      logger,
    });

    const order = makeOrder();
    await processor.handle(makeClaimed(order));

    expect(adapter.buildInstructions).toHaveBeenCalledTimes(1);
    expect(signer.proposeAndExecute).toHaveBeenCalledWith([ix]);

    const results = fakeRedis.lists.get(RESULTS_CHANNEL) ?? [];
    expect(results).toHaveLength(1);
    const published = JSON.parse(results[0]) as {
      status: string;
      order_id: string;
      chain: string;
      template_id: string;
      solana_specific: { signature: string; slot: number };
    };
    expect(published.status).toBe("confirmed");
    expect(published.order_id).toBe("ord-test-001");
    expect(published.solana_specific.signature).toBe("fake-sig-XYZ");
    expect(published.solana_specific.slot).toBe(42);
    expect(published.chain).toBe("solana");
    expect(published.template_id).toBe("BASIS-PERP-001");
  });

  it("publishes failed result when no adapter is registered", async () => {
    const registry: AdapterRegistry = new Map();
    const processor = new OrderProcessor({
      adapters: registry,
      signer,
      publisher,
      logger,
    });

    await processor.handle(makeClaimed(makeOrder({ protocol: "drift" })));

    const results = fakeRedis.lists.get(RESULTS_CHANNEL) ?? [];
    expect(results).toHaveLength(1);
    const published = JSON.parse(results[0]) as Record<string, unknown>;
    expect(published.status).toBe("failed");
    expect(published.error).toMatch(/No adapter registered/);
    expect(signer.proposeAndExecute).not.toHaveBeenCalled();
  });

  it("publishes failed result when adapter throws", async () => {
    const adapter: SolanaAdapter = {
      name: "jupiter",
      buildInstructions: vi.fn().mockRejectedValue(new Error("quote API down")),
    };
    const registry: AdapterRegistry = new Map([["jupiter", adapter]]);
    const processor = new OrderProcessor({
      adapters: registry,
      signer,
      publisher,
      logger,
    });

    await processor.handle(makeClaimed(makeOrder()));

    const results = fakeRedis.lists.get(RESULTS_CHANNEL) ?? [];
    expect(results).toHaveLength(1);
    const published = JSON.parse(results[0]) as Record<string, unknown>;
    expect(published.status).toBe("failed");
    expect(published.error).toMatch(/buildInstructions failed.*quote API down/);
  });

  it("publishes failed result when signer throws", async () => {
    vi.mocked(signer.proposeAndExecute).mockRejectedValueOnce(
      new Error("RPC unreachable"),
    );
    const adapter = makeMockAdapter("jupiter", ix);
    const registry: AdapterRegistry = new Map([["jupiter", adapter]]);
    const processor = new OrderProcessor({
      adapters: registry,
      signer,
      publisher,
      logger,
    });

    await processor.handle(makeClaimed(makeOrder()));

    const results = fakeRedis.lists.get(RESULTS_CHANNEL) ?? [];
    expect(results).toHaveLength(1);
    const published = JSON.parse(results[0]) as Record<string, unknown>;
    expect(published.status).toBe("failed");
    expect(published.error).toMatch(/proposeAndExecute failed.*RPC unreachable/);
  });

  it("publishes failed result when adapter returns zero instructions", async () => {
    const adapter: SolanaAdapter = {
      name: "jupiter",
      buildInstructions: vi.fn().mockResolvedValue([]),
    };
    const registry: AdapterRegistry = new Map([["jupiter", adapter]]);
    const processor = new OrderProcessor({
      adapters: registry,
      signer,
      publisher,
      logger,
    });

    await processor.handle(makeClaimed(makeOrder()));

    const results = fakeRedis.lists.get(RESULTS_CHANNEL) ?? [];
    expect(JSON.parse(results[0]).status).toBe("failed");
    expect(JSON.parse(results[0]).error).toMatch(/zero instructions/);
  });
});
