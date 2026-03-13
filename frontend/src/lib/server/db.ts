import { Pool, type QueryResultRow } from "pg";

let pool: Pool | null = null;

/**
 * Returns a lazily-initialized PostgreSQL connection pool singleton.
 * Reads DATABASE_URL from environment. Returns null if not configured.
 */
export function getPool(): Pool | null {
  if (pool) return pool;

  const connectionString = process.env.DATABASE_URL_PG || process.env.DATABASE_URL;
  if (!connectionString) {
    console.warn("[icarus] DATABASE_URL_PG/DATABASE_URL not set — PostgreSQL client unavailable");
    return null;
  }

  pool = new Pool({
    connectionString,
    max: 10,
  });

  pool.on("error", (err) => {
    console.error("[icarus] PostgreSQL pool error:", err.message);
  });

  return pool;
}

/**
 * Execute a parameterized SQL query and return the rows.
 * Returns an empty array on error or if the pool is unavailable.
 */
export async function query<T extends QueryResultRow = QueryResultRow>(
  text: string,
  params?: unknown[],
): Promise<T[]> {
  try {
    const db = getPool();
    if (!db) return [];

    const result = await db.query<T>(text, params);
    return result.rows;
  } catch (err) {
    console.error("[icarus] PostgreSQL query error:", err);
    return [];
  }
}
