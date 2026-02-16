import { describe, it, expect } from 'vitest';
import { CHANNELS } from '../src/redis/client.js';

describe('ts-executor', () => {
  it('defines all required Redis channels', () => {
    expect(CHANNELS.MARKET_EVENTS).toBe('market:events');
    expect(CHANNELS.EXECUTION_ORDERS).toBe('execution:orders');
    expect(CHANNELS.EXECUTION_RESULTS).toBe('execution:results');
  });
});
