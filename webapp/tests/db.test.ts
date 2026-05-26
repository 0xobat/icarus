/**
 * Smoke tests for the webapp's Postgres helper.
 *
 * We deliberately use node:test (already shipped with Node 22, the runtime
 * in webapp/Dockerfile) + tsx as the loader — keeps zero new heavyweight
 * test-framework dependencies. The tests cover two pieces of load-bearing
 * logic in `lib/db.ts`:
 *
 *   1. `query<T>(sql, params)` delegates to the underlying pg pool with
 *      sql + params unchanged. The whole webapp is read-only — if this
 *      contract slips, every page breaks.
 *   2. `normaliseDatabaseUrl` strips the SQLAlchemy `+driver` suffix so the
 *      single shared `DATABASE_URL` env var works for both the Python and
 *      Node stacks. Regression here would silently break startup.
 */

import { strict as assert } from "node:assert";
import { afterEach, describe, it } from "node:test";

import { __internals, __setPoolForTest, query } from "../src/lib/db";

describe("normaliseDatabaseUrl", () => {
  it("strips +asyncpg suffix used by SQLAlchemy", () => {
    assert.equal(
      __internals.normaliseDatabaseUrl(
        "postgresql+asyncpg://u:p@h:5432/db"
      ),
      "postgresql://u:p@h:5432/db"
    );
  });

  it("strips +psycopg suffix too", () => {
    assert.equal(
      __internals.normaliseDatabaseUrl("postgresql+psycopg://u:p@h/db"),
      "postgresql://u:p@h/db"
    );
  });

  it("leaves a plain postgresql:// URL untouched", () => {
    const plain = "postgresql://u:p@h:5432/db";
    assert.equal(__internals.normaliseDatabaseUrl(plain), plain);
  });

  it("leaves a postgres:// short-form URL untouched", () => {
    const plain = "postgres://u:p@h:5432/db";
    assert.equal(__internals.normaliseDatabaseUrl(plain), plain);
  });
});

describe("query", () => {
  afterEach(() => {
    __setPoolForTest(null);
  });

  it("delegates sql + params to the pool's query method", async () => {
    const calls: Array<{ sql: string; params: unknown[] }> = [];
    const fakePool = {
      query: async (sql: string, params: unknown[]) => {
        calls.push({ sql, params });
        return { rows: [{ ok: true }], rowCount: 1 };
      },
    };
    // Cast through unknown: we're injecting a structural-typed fake that
    // implements only the surface area `query` touches.
    __setPoolForTest(fakePool as unknown as import("pg").Pool);

    const result = await query<{ ok: boolean }>(
      "SELECT * FROM lake_roster WHERE state = $1",
      ["live_capped"]
    );

    assert.equal(calls.length, 1);
    assert.equal(calls[0]!.sql, "SELECT * FROM lake_roster WHERE state = $1");
    assert.deepEqual(calls[0]!.params, ["live_capped"]);
    assert.deepEqual(result.rows, [{ ok: true }]);
  });

  it("defaults params to an empty array when omitted", async () => {
    const calls: Array<{ sql: string; params: unknown[] }> = [];
    const fakePool = {
      query: async (sql: string, params: unknown[]) => {
        calls.push({ sql, params });
        return { rows: [], rowCount: 0 };
      },
    };
    __setPoolForTest(fakePool as unknown as import("pg").Pool);

    await query("SELECT 1");

    assert.deepEqual(calls[0]!.params, []);
  });
});
