import { describe, it, expect } from 'vitest';
import { validate, validateOrThrow } from '../src/validation/schema-validator.js';

describe('schema-validator', () => {
  describe('market-events', () => {
    const validEvent = {
      version: '1.0.0',
      timestamp: '2026-02-16T12:00:00Z',
      sequence: 1,
      chain: 'ethereum',
      eventType: 'price_update',
      protocol: 'aave_v3',
    };

    it('accepts a valid market event', () => {
      const result = validate('market-events', validEvent);
      expect(result.valid).toBe(true);
      expect(result.errors).toBeNull();
    });

    it('rejects missing required fields', () => {
      const result = validate('market-events', { version: '1.0.0' });
      expect(result.valid).toBe(false);
      expect(result.errors).not.toBeNull();
    });

    it('rejects invalid chain', () => {
      const result = validate('market-events', { ...validEvent, chain: 'polygon' });
      expect(result.valid).toBe(false);
    });

    it('rejects invalid eventType', () => {
      const result = validate('market-events', { ...validEvent, eventType: 'unknown' });
      expect(result.valid).toBe(false);
    });

    it('rejects wrong version', () => {
      const result = validate('market-events', { ...validEvent, version: '2.0.0' });
      expect(result.valid).toBe(false);
    });

    it('rejects additional properties', () => {
      const result = validate('market-events', { ...validEvent, extra: 'field' });
      expect(result.valid).toBe(false);
    });
  });

  describe('execution-orders', () => {
    const validOrder = {
      version: '1.0.0',
      orderId: 'order-123',
      correlationId: 'corr-456',
      timestamp: '2026-02-16T12:00:00Z',
      chain: 'ethereum',
      protocol: 'aave_v3',
      action: 'supply',
      params: { tokenIn: '0xabc', amount: '1000000000000000000' },
      limits: {
        maxGasWei: '50000000000000',
        maxSlippageBps: 50,
        deadlineUnix: 1739700000,
      },
    };

    it('accepts a valid order', () => {
      const result = validate('execution-orders', validOrder);
      expect(result.valid).toBe(true);
    });

    it('rejects missing limits', () => {
      const { limits: _, ...noLimits } = validOrder;
      const result = validate('execution-orders', noLimits);
      expect(result.valid).toBe(false);
    });

    it('rejects slippage over 1000 bps', () => {
      const result = validate('execution-orders', {
        ...validOrder,
        limits: { ...validOrder.limits, maxSlippageBps: 1500 },
      });
      expect(result.valid).toBe(false);
    });

    it('rejects unknown action', () => {
      const result = validate('execution-orders', { ...validOrder, action: 'liquidate' });
      expect(result.valid).toBe(false);
    });
  });

  describe('execution-results', () => {
    const validResult = {
      version: '1.0.0',
      orderId: 'order-123',
      correlationId: 'corr-456',
      timestamp: '2026-02-16T12:00:00Z',
      status: 'confirmed',
      txHash: '0xabc123',
      blockNumber: 12345,
      gasUsed: '21000',
    };

    it('accepts a valid result', () => {
      const result = validate('execution-results', validResult);
      expect(result.valid).toBe(true);
    });

    it('rejects invalid status', () => {
      const result = validate('execution-results', { ...validResult, status: 'pending' });
      expect(result.valid).toBe(false);
    });

    it('rejects missing orderId', () => {
      const { orderId: _, ...noOrderId } = validResult;
      const result = validate('execution-results', noOrderId);
      expect(result.valid).toBe(false);
    });
  });

  describe('validateOrThrow', () => {
    it('does not throw for valid data', () => {
      expect(() =>
        validateOrThrow('market-events', {
          version: '1.0.0',
          timestamp: '2026-02-16T12:00:00Z',
          sequence: 0,
          chain: 'ethereum',
          eventType: 'new_block',
          protocol: 'system',
        })
      ).not.toThrow();
    });

    it('throws with descriptive message for invalid data', () => {
      expect(() => validateOrThrow('market-events', {})).toThrow(
        /Schema validation failed/
      );
    });
  });
});
