/**
 * Normalizes raw blockchain events into the v2 market-event schema format.
 * Each event gets a monotonically increasing sequence number.
 *
 * v2 shape: snake_case, chain discriminator ("base" only for this executor),
 * required correlation_id, and base_specific sub-object for Base-chain metadata
 * (block_number, tx_hash, log_index, gas_price_wei).
 */

import { randomUUID } from 'node:crypto';

export interface MarketEventBaseSpecific {
  block_number: number;
  tx_hash?: string | null;
  log_index?: number | null;
  gas_price_wei?: number | null;
}

export interface MarketEvent {
  version: '1.0.0';
  timestamp: string;
  chain: 'base';
  sequence: number;
  event_type: 'swap' | 'liquidity_change' | 'rate_change' | 'large_transfer' | 'new_block' | 'price_update' | 'oracle_update' | 'new_slot';
  protocol: string;
  correlation_id: string;
  symbol?: string | null;
  payload?: Record<string, unknown>;
  base_specific?: MarketEventBaseSpecific;
  solana_specific?: null;
}

export type Chain = MarketEvent['chain'];
export type EventType = MarketEvent['event_type'];

let sequenceCounter = 0;

/** Reset the sequence counter (for testing). */
export function resetSequence(): void {
  sequenceCounter = 0;
}

/** Get the next sequence number. */
function nextSequence(): number {
  return sequenceCounter++;
}

/** Build the base_specific sub-object for a Base-chain event. */
function baseSpecific(
  blockNumber: number,
  txHash?: string,
  logIndex?: number,
  gasPriceWei?: bigint,
): MarketEventBaseSpecific {
  return {
    block_number: blockNumber,
    tx_hash: txHash ?? null,
    log_index: logIndex ?? null,
    gas_price_wei: gasPriceWei !== undefined ? Number(gasPriceWei) : null,
  };
}

/** Create a base event with common fields. */
function baseEvent(
  chain: Chain,
  eventType: EventType,
  protocol: string,
  blockNumber: number,
  txHash?: string,
  payload?: Record<string, unknown>,
  logIndex?: number,
): MarketEvent {
  return {
    version: '1.0.0',
    timestamp: new Date().toISOString(),
    sequence: nextSequence(),
    chain,
    event_type: eventType,
    protocol,
    correlation_id: randomUUID(),
    ...(payload !== undefined && { payload }),
    base_specific: baseSpecific(blockNumber, txHash, logIndex),
  };
}

/** Normalize a new block into a market event. */
export function normalizeNewBlock(
  chain: Chain,
  blockNumber: number,
  blockHash: string,
  baseFeePerGas?: bigint,
  gasUsed?: bigint,
  timestamp?: bigint,
): MarketEvent {
  const payload: Record<string, unknown> = { block_hash: blockHash };
  if (baseFeePerGas !== undefined) payload.base_fee_per_gas = baseFeePerGas.toString();
  if (gasUsed !== undefined) payload.gas_used = gasUsed.toString();
  if (timestamp !== undefined) payload.block_timestamp = Number(timestamp);
  return baseEvent(chain, 'new_block', 'system', blockNumber, undefined, payload);
}

/** Normalize a contract log event (Aave rate change, swap, etc.). */
export function normalizeContractEvent(
  chain: Chain,
  protocol: string,
  eventType: EventType,
  blockNumber: number,
  txHash: string,
  eventData: Record<string, unknown>,
  logIndex?: number,
): MarketEvent {
  return baseEvent(chain, eventType, protocol, blockNumber, txHash, eventData, logIndex);
}

/** Normalize a large transfer event (ERC-20 Transfer exceeding threshold). */
export function normalizeLargeTransfer(
  chain: Chain,
  blockNumber: number,
  txHash: string,
  from: string,
  to: string,
  token: string,
  amount: string,
): MarketEvent {
  return baseEvent(chain, 'large_transfer', 'system', blockNumber, txHash, {
    from,
    to,
    token,
    amount,
  });
}
