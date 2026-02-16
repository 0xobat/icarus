import Redis from 'ioredis';

export const CHANNELS = {
  MARKET_EVENTS: 'market:events',
  EXECUTION_ORDERS: 'execution:orders',
  EXECUTION_RESULTS: 'execution:results',
} as const;

export function createRedisClient(): Redis {
  const url = process.env.REDIS_URL ?? 'redis://localhost:6379';
  return new Redis(url, {
    retryStrategy(times: number) {
      const delay = Math.min(times * 200, 5000);
      return delay;
    },
    maxRetriesPerRequest: 3,
  });
}
