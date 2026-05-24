import { describe, it, expect } from 'vitest';
import { validate, validateOrThrow } from '../src/validation/schema-validator.js';

describe('schema-validator', () => {
  describe('market-event', () => {
    const validEvent = {
      version: '1.0.0',
      timestamp: '2026-02-16T12:00:00Z',
      chain: 'base',
      sequence: 1,
      event_type: 'price_update',
      protocol: 'aave_v3',
      correlation_id: 'corr-001',
      base_specific: { block_number: 100 },
    };

    it('accepts a valid market event', () => {
      const result = validate('market-event', validEvent);
      expect(result.valid).toBe(true);
      expect(result.errors).toBeNull();
    });

    it('rejects missing required fields', () => {
      const result = validate('market-event', { version: '1.0.0' });
      expect(result.valid).toBe(false);
      expect(result.errors).not.toBeNull();
    });

    it('rejects invalid chain', () => {
      const result = validate('market-event', { ...validEvent, chain: 'polygon' });
      expect(result.valid).toBe(false);
    });

    it('rejects invalid event_type', () => {
      const result = validate('market-event', { ...validEvent, event_type: 'unknown' });
      expect(result.valid).toBe(false);
    });

    it('rejects wrong version', () => {
      const result = validate('market-event', { ...validEvent, version: '2.0.0' });
      expect(result.valid).toBe(false);
    });

    it('rejects additional properties', () => {
      const result = validate('market-event', { ...validEvent, extra: 'field' });
      expect(result.valid).toBe(false);
    });

    it('requires base_specific when chain is base', () => {
      const { base_specific: _, ...rest } = validEvent;
      const result = validate('market-event', rest);
      expect(result.valid).toBe(false);
    });
  });

  describe('execution-order', () => {
    const validOrder = {
      version: '1.0.0',
      order_id: 'order-123',
      correlation_id: 'corr-456',
      timestamp: '2026-02-16T12:00:00Z',
      chain: 'base',
      protocol: 'aave_v3',
      action: 'supply',
      strategy: 'LEND-001:cand-1',
      params: { token_in: '0xabc', amount: '1000000000000000000' },
      limits: {
        max_gas_wei: '50000000000000',
        max_slippage_bps: 50,
        deadline_unix: 1739700000,
      },
    };

    it('accepts a valid order', () => {
      const result = validate('execution-order', validOrder);
      expect(result.valid).toBe(true);
    });

    it('accepts an order with template_id and candidate_id', () => {
      const result = validate('execution-order', {
        ...validOrder,
        template_id: 'LEND-001',
        candidate_id: 'cand-1',
      });
      expect(result.valid).toBe(true);
    });

    it('rejects missing limits', () => {
      const { limits: _, ...noLimits } = validOrder;
      const result = validate('execution-order', noLimits);
      expect(result.valid).toBe(false);
    });

    it('rejects slippage over 1000 bps', () => {
      const result = validate('execution-order', {
        ...validOrder,
        limits: { ...validOrder.limits, max_slippage_bps: 1500 },
      });
      expect(result.valid).toBe(false);
    });

    it('rejects unknown action', () => {
      const result = validate('execution-order', { ...validOrder, action: 'liquidate' });
      expect(result.valid).toBe(false);
    });
  });

  describe('execution-result', () => {
    const validResult = {
      version: '1.0.0',
      order_id: 'order-123',
      correlation_id: 'corr-456',
      timestamp: '2026-02-16T12:00:00Z',
      chain: 'base',
      status: 'confirmed',
      tx_hash: '0xabc123',
      block_number: 12345,
      gas_used_wei: '21000',
    };

    it('accepts a valid result', () => {
      const result = validate('execution-result', validResult);
      expect(result.valid).toBe(true);
    });

    it('accepts a result echoing template_id and candidate_id', () => {
      const result = validate('execution-result', {
        ...validResult,
        template_id: 'LEND-001',
        candidate_id: 'cand-1',
      });
      expect(result.valid).toBe(true);
    });

    it('rejects invalid status', () => {
      const result = validate('execution-result', { ...validResult, status: 'pending' });
      expect(result.valid).toBe(false);
    });

    it('rejects missing order_id', () => {
      const { order_id: _, ...noOrderId } = validResult;
      const result = validate('execution-result', noOrderId);
      expect(result.valid).toBe(false);
    });
  });

  describe('validateOrThrow', () => {
    it('does not throw for valid data', () => {
      expect(() =>
        validateOrThrow('market-event', {
          version: '1.0.0',
          timestamp: '2026-02-16T12:00:00Z',
          chain: 'base',
          sequence: 0,
          event_type: 'new_block',
          protocol: 'system',
          correlation_id: 'corr-001',
          base_specific: { block_number: 100 },
        })
      ).not.toThrow();
    });

    it('throws with descriptive message for invalid data', () => {
      expect(() => validateOrThrow('market-event', {})).toThrow(
        /Schema validation failed/
      );
    });
  });
});
