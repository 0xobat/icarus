/**
 * Orchestrates one ExecutionOrder through the full Solana-side pipeline:
 *
 *   order → adapter.buildInstructions → SquadsSigner.proposeAndExecute
 *         → ResultsPublisher.publish
 *
 * The processor never throws to its caller (the OrdersConsumer loop). On
 * any failure it logs, publishes a `failed` ExecutionResult, and returns.
 * The consumer ACKs either way.
 */
import type { Logger } from "pino";
import type { ClaimedOrder } from "./redis/consumer.js";
import type { ExecutionOrder, SolanaAdapter } from "./adapters/types.js";
import type { ResultsPublisher, ExecutionResult } from "./redis/publisher.js";
import type { SquadsSigner } from "./wallet/squads.js";

/**
 * Read-only registry mapping `order.protocol` → adapter instance.
 * Built once at boot (see `main.ts buildAdapterRegistry`).
 */
export type AdapterRegistry = ReadonlyMap<string, SolanaAdapter>;

export interface ProcessorOptions {
  adapters: AdapterRegistry;
  signer: SquadsSigner;
  publisher: ResultsPublisher;
  logger: Logger;
}

/**
 * Stateless per-order orchestrator. Safe to share across the consumer loop.
 */
export class OrderProcessor {
  private readonly adapters: AdapterRegistry;
  private readonly signer: SquadsSigner;
  private readonly publisher: ResultsPublisher;
  private readonly logger: Logger;

  constructor(opts: ProcessorOptions) {
    this.adapters = opts.adapters;
    this.signer = opts.signer;
    this.publisher = opts.publisher;
    this.logger = opts.logger;
  }

  /**
   * Handle a claimed order end-to-end. Always publishes a result.
   */
  async handle(claimed: ClaimedOrder): Promise<void> {
    const { order } = claimed;
    this.logger.info(
      {
        order_id: order.order_id,
        correlation_id: order.correlation_id,
        protocol: order.protocol,
        action: order.action,
      },
      "execution_order_claimed",
    );

    const adapter = this.adapters.get(order.protocol);
    if (!adapter) {
      await this.fail(
        order,
        `No adapter registered for protocol "${order.protocol}"`,
      );
      return;
    }

    // Build instructions (adapter may throw on malformed params).
    let instructions;
    try {
      instructions = await adapter.buildInstructions(
        order,
        this.signer.connection,
      );
    } catch (err) {
      await this.fail(
        order,
        `adapter.buildInstructions failed: ${err instanceof Error ? err.message : String(err)}`,
      );
      return;
    }

    if (instructions.length === 0) {
      await this.fail(order, "adapter returned zero instructions");
      return;
    }

    // Sign + broadcast via Squads (or single-signer fallback).
    let outcome;
    try {
      outcome = await this.signer.proposeAndExecute(instructions);
    } catch (err) {
      await this.fail(
        order,
        `signer.proposeAndExecute failed: ${err instanceof Error ? err.message : String(err)}`,
      );
      return;
    }

    const result: ExecutionResult = {
      version: "1.0.0",
      order_id: order.order_id,
      correlation_id: order.correlation_id,
      timestamp: new Date().toISOString(),
      chain: "solana",
      status: "confirmed",
      template_id: order.template_id ?? null,
      candidate_id: order.candidate_id ?? null,
      solana_specific: {
        signature: outcome.signature,
        slot: outcome.slot,
        compute_units_consumed: null,
        // Schema is "total priority fee paid in lamports". Computing
        // that needs `compute_units_consumed` (which we don't fetch
        // post-confirm yet) multiplied by `compute_unit_price`
        // (microlamports/CU) divided by 1e6. Writing the rate here
        // misreports the field by ~6 orders of magnitude, so we leave
        // it null until a post-confirm tx parse lands.
        priority_fee_lamports: null,
      },
      fill_price: null,
      amount_out: null,
      error: null,
      retry_count: 0,
    };

    try {
      await this.publisher.publish(result);
    } catch (err) {
      this.logger.error(
        {
          err: err instanceof Error ? err.message : String(err),
          order_id: order.order_id,
        },
        "execution_result_publish_failed",
      );
    }
  }

  private async fail(order: ExecutionOrder, message: string): Promise<void> {
    this.logger.error(
      { order_id: order.order_id, message },
      "execution_order_failed",
    );
    const result: ExecutionResult = {
      version: "1.0.0",
      order_id: order.order_id,
      correlation_id: order.correlation_id,
      timestamp: new Date().toISOString(),
      chain: "solana",
      status: "failed",
      template_id: order.template_id ?? null,
      candidate_id: order.candidate_id ?? null,
      solana_specific: {
        signature: null,
        slot: null,
        compute_units_consumed: null,
        priority_fee_lamports: null,
      },
      fill_price: null,
      amount_out: null,
      error: message,
      retry_count: 0,
    };
    try {
      await this.publisher.publish(result);
    } catch (err) {
      this.logger.error(
        {
          err: err instanceof Error ? err.message : String(err),
          order_id: order.order_id,
        },
        "execution_result_publish_failed_on_failure_path",
      );
    }
  }
}
