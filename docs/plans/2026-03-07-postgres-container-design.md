# Add PostgreSQL to Docker Compose

Date: 2026-03-07

## Problem

py-engine has a full SQLAlchemy ORM layer (trades, portfolio snapshots, strategy performance,
alerts, schema versions) designed for PostgreSQL, but docker-compose only includes Redis.
Without `DATABASE_URL` set, py-engine silently falls back to SQLite (`data/icarus.db`) inside
the container — data is lost on rebuild, and the setup doesn't match the production target
(Railway PostgreSQL).

## Decision

Add a PostgreSQL 16 service to docker-compose with a named volume for persistence.
Wire `DATABASE_URL` into py-engine's environment block. No application code changes needed.

## Changes

### `docker-compose.yml`

- Add `postgres` service (postgres:16-alpine)
- Named volume `postgres-data` for data persistence across restarts
- Healthcheck via `pg_isready`
- py-engine `depends_on` includes `postgres` with `service_healthy` condition
- py-engine environment gets `DATABASE_URL=postgresql+psycopg2://icarus:icarus@postgres:5432/icarus`
- Postgres credentials set via `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` env vars

### `.env.example`

- Add `DATABASE_URL=` with comment showing the compose default

### `.gitignore`

- Add `*.db` to prevent SQLite fallback files from being committed

## No changes needed

- `py-engine/db/database.py` — already reads `DATABASE_URL`, handles PostgreSQL pooling
- `py-engine/main.py` — already does `os.environ.get("DATABASE_URL")`
- `ts-executor` — stateless, no database dependency
- Tests — use `sqlite:///:memory:`, unaffected

## Not in scope

- Alembic migrations — `create_tables()` is sufficient
- Seed data — empty DB on first boot is fine
- Production config — Railway provides its own `DATABASE_URL`
