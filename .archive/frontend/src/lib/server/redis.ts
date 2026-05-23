import Redis from "ioredis";

let redis: Redis | null = null;

/**
 * Returns a lazily-initialized Redis singleton.
 * Reads REDIS_URL from environment. Returns null if not configured.
 */
export function getRedis(): Redis | null {
  if (redis) return redis;

  const url = process.env.REDIS_URL;
  if (!url) {
    console.warn("[icarus] REDIS_URL not set — Redis client unavailable");
    return null;
  }

  redis = new Redis(url, {
    maxRetriesPerRequest: 3,
    lazyConnect: true,
  });

  redis.on("error", (err) => {
    console.error("[icarus] Redis connection error:", err.message);
  });

  return redis;
}

/**
 * Fetch a JSON value from Redis by key.
 * Returns the parsed value, or null on error / missing key.
 */
export async function getRedisValue<T = unknown>(
  key: string,
): Promise<T | null> {
  try {
    const client = getRedis();
    if (!client) return null;

    const raw = await client.get(key);
    if (raw === null) return null;

    return JSON.parse(raw) as T;
  } catch (err) {
    console.error(`[icarus] Redis get error for key "${key}":`, err);
    return null;
  }
}
