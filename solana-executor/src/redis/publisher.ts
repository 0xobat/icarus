/**
 * ExecutionResult publisher for the Solana executor.
 *
 * Writes JSON envelopes to `execution:results:solana` (Redis list, RPUSH).
 * The webapp + lake-governor tail this list. Every result is AJV-validated
 * against execution-result.schema.json before being pushed — invalid
 * envelopes throw, since "could not encode our own result" is a programmer
 * bug, not a runtime expectation.
 */
import type Redis from "ioredis";
import type { Logger } from "pino";
import { validateOrThrow } from "../validation/schema-validator.js";

export const RESULTS_CHANNEL = "execution:results:solana";

export type ExecutionStatus =
  | "confirmed"
  | "failed"
  | "reverted"
  | "timeout"
  | "rejected_by_guard";

/**
 * SVM tx receipt detail. The schema requires this block (even if all
 * fields are null) when `chain === "solana"`.
 */
export interface SolanaResultSpecific {
  signature?: string | null;
  slot?: number | null;
  compute_units_consumed?: number | null;
  priority_fee_lamports?: number | null;
}

export interface ExecutionResult {
  version: "1.0.0";
  order_id: string;
  correlation_id: string;
  timestamp: string;
  chain: "solana";
  status: ExecutionStatus;
  template_id?: string | null;
  candidate_id?: string | null;
  solana_specific: SolanaResultSpecific | null;
  fill_price?: string | null;
  amount_out?: string | null;
  error?: string | null;
  retry_count?: number;
}

export interface ResultsPublisherOptions {
  redis: Redis;
  logger: Logger;
}

/**
 * Validates and publishes ExecutionResult envelopes to the Solana results
 * channel. One instance per process; safe to share across the processor and
 * any timeout sweeper.
 */
export class ResultsPublisher {
  private readonly redis: Redis;
  private readonly logger: Logger;

  constructor(opts: ResultsPublisherOptions) {
    this.redis = opts.redis;
    this.logger = opts.logger;
  }

  /**
   * Publish a result. Throws if the envelope fails schema validation —
   * the caller is responsible for assembling a valid one.
   */
  async publish(result: ExecutionResult): Promise<void> {
    validateOrThrow("execution-result", result);
    const payload = JSON.stringify(result);
    await this.redis.rpush(RESULTS_CHANNEL, payload);
    this.logger.info(
      {
        order_id: result.order_id,
        status: result.status,
        signature: result.solana_specific?.signature ?? null,
      },
      "execution_result_published",
    );
  }
}
