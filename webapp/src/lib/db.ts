/**
 * Postgres connection — singleton pool for the read-only webapp.
 *
 * The Daedalus blueprint puts Postgres as the state of record (see CLAUDE.md
 * §"Conventions"). Cross-cluster reads happen through the database, never via
 * service-to-service HTTP. The webapp lives in the Curation cluster and is
 * strictly read-only: it presents bot reasoning (lake roster, recent template
 * extractions, decision audit log, pending Discord promotion tokens) for the
 * operator's Saturday-morning investigation pass.
 *
 * DATABASE_URL convention: the Python services use the SQLAlchemy URL form
 * (`postgresql+asyncpg://…`). Node's `pg` driver expects plain
 * `postgres://` or `postgresql://`. We normalise the driver suffix here so a
 * single env var works for both stacks.
 */

import { Pool, type QueryResult, type QueryResultRow } from "pg";

const SQLALCHEMY_DRIVER_PATTERN = /^postgres(?:ql)?\+[a-z0-9]+:\/\//i;

function normaliseDatabaseUrl(raw: string): string {
  // Convert e.g. `postgresql+asyncpg://…` → `postgresql://…` for the pg driver.
  return raw.replace(SQLALCHEMY_DRIVER_PATTERN, "postgresql://");
}

let pool: Pool | null = null;

export function getPool(): Pool {
  if (pool) return pool;
  const url = process.env.DATABASE_URL;
  if (!url) {
    throw new Error(
      "DATABASE_URL is not set. The webapp needs read access to the Icarus Postgres state of record."
    );
  }
  pool = new Pool({
    connectionString: normaliseDatabaseUrl(url),
    // Generous timeout so an idle Saturday-morning operator still gets a
    // useful error message instead of a hung tab if Postgres is down.
    connectionTimeoutMillis: 5_000,
    max: 5,
  });
  return pool;
}

/**
 * Thin typed wrapper around `pool.query`.
 *
 * Callers pass a parameterised SQL string + values; we hand back the rows
 * already typed. Kept intentionally minimal — no query builder, no migrations,
 * no ORM. The webapp does ~5 small SELECTs and that's it.
 */
export async function query<T extends QueryResultRow>(
  sql: string,
  params: ReadonlyArray<unknown> = []
): Promise<QueryResult<T>> {
  const p = getPool();
  return p.query<T>(sql, params as unknown[]);
}

// Test seam: lets the unit test inject a fake pool without leaking a real
// connection into the production code path.
export function __setPoolForTest(p: Pool | null): void {
  pool = p;
}

// Test seam exposing the URL normaliser without exporting it as a public API.
export const __internals = { normaliseDatabaseUrl };
