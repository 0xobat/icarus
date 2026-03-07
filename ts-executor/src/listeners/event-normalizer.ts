/**
 * Normalizes raw blockchain events into the market-events schema format.
 * Each event gets a monotonically increasing sequence number.
 */

export interface MarketEvent {
  version: '1.0.0';
  timestamp: string;
  sequence: number;
  chain: 'ethereum' | 'base';
  eventType: 'swap' | 'liquidity_change' | 'rate_change' | 'large_transfer' | 'new_block' | 'price_update';
  protocol: string;
  blockNumber?: number;
  txHash?: string;
  data?: Record<string, unknown>;
}

export type Chain = MarketEvent['chain'];
export type EventType = MarketEvent['eventType'];

let sequenceCounter = 0;

/** Reset the sequence counter (for testing). */
export function resetSequence(): void {
  sequenceCounter = 0;
}

/** Get the next sequence number. */
function nextSequence(): number {
  return sequenceCounter++;
}

/** Create a base event with common fields. */
function baseEvent(
  chain: Chain,
  eventType: EventType,
  protocol: string,
  blockNumber?: number,
  txHash?: string,
  data?: Record<string, unknown>,
): MarketEvent {
  return {
    version: '1.0.0',
    timestamp: new Date().toISOString(),
    sequence: nextSequence(),
    chain,
    eventType,
    protocol,
    ...(blockNumber !== undefined && { blockNumber }),
    ...(txHash !== undefined && { txHash }),
    ...(data !== undefined && { data }),
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
  return baseEvent(chain, 'new_block', 'system', blockNumber, undefined, {
    blockHash,
    ...(baseFeePerGas !== undefined && { baseFeePerGas: baseFeePerGas.toString() }),
    ...(gasUsed !== undefined && { gasUsed: gasUsed.toString() }),
    ...(timestamp !== undefined && { blockTimestamp: Number(timestamp) }),
  });
}

/** Normalize a contract log event (Aave rate change, swap, etc.). */
export function normalizeContractEvent(
  chain: Chain,
  protocol: string,
  eventType: EventType,
  blockNumber: number,
  txHash: string,
  eventData: Record<string, unknown>,
): MarketEvent {
  return baseEvent(chain, eventType, protocol, blockNumber, txHash, eventData);
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
