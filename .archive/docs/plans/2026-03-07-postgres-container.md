# PostgreSQL Container Setup — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add PostgreSQL to docker-compose so py-engine uses it instead of falling back to SQLite.

**Architecture:** Add a postgres:16-alpine service with named volume and healthcheck. Wire `DATABASE_URL` into py-engine's environment. Add `psycopg2-binary` to py-engine dependencies (currently missing). No application code changes needed — SQLAlchemy already handles both dialects.

**Tech Stack:** PostgreSQL 16, SQLAlchemy, psycopg2-binary, Docker Compose

---

### Task 1: Add psycopg2-binary to py-engine dependencies

**Files:**
- Modify: `py-engine/pyproject.toml:6-17`

**Context:** The code at `py-engine/db/database.py:7` references `postgresql+psycopg2://` as the SQLAlchemy driver, but `psycopg2` or `psycopg2-binary` is not in `pyproject.toml`. Without it, py-engine will crash on import when `DATABASE_URL` points to PostgreSQL.

**Step 1: Add the dependency**

In `py-engine/pyproject.toml`, add `psycopg2-binary>=2.9` to the `dependencies` list (after `sqlalchemy`):

```toml
dependencies = [
    "redis>=5.2.1",
    "pandas>=2.2.3",
    "numpy>=2.2.2",
    "python-dotenv>=1.0.1",
    "jsonschema>=4.23.0",
    "watchfiles>=1.0.4",
    "sqlalchemy>=2.0",
    "psycopg2-binary>=2.9",
    "aiosqlite>=0.20",
    "anthropic>=0.40.0",
]
```

**Step 2: Re-lock dependencies**

Run: `cd /home/heresy/Documents/Projects/crypto/icarus/py-engine && uv lock`
Expected: `uv.lock` updates with psycopg2-binary and its transitive deps.

**Step 3: Verify it installs**

Run: `cd /home/heresy/Documents/Projects/crypto/icarus/py-engine && uv sync`
Expected: psycopg2-binary installs without errors.

**Step 4: Verify existing tests still pass**

Run: `cd /home/heresy/Documents/Projects/crypto/icarus/py-engine && uv run pytest tests/test_database.py -v --tb=short`
Expected: All tests PASS (they use `sqlite:///:memory:`, unaffected by new dep).

**Step 5: Commit**

```bash
git add py-engine/pyproject.toml py-engine/uv.lock
git commit -m "feat(icarus): add psycopg2-binary for PostgreSQL support"
```

---

### Task 2: Add PostgreSQL service to docker-compose

**Files:**
- Modify: `docker-compose.yml`

**Context:** Currently `docker-compose.yml` has 3 services: redis, ts-executor, py-engine. py-engine only `depends_on` redis. We need to add a postgres service and make py-engine depend on it.

**Step 1: Add the postgres service and update py-engine**

Replace the entire `docker-compose.yml` with:

```yaml
services:
  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    volumes:
      - redis-data:/data
    command: redis-server --appendonly yes --maxmemory 256mb --maxmemory-policy allkeys-lru
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 5s
      retries: 3

  postgres:
    image: postgres:16-alpine
    ports:
      - "5432:5432"
    volumes:
      - postgres-data:/var/lib/postgresql/data
    environment:
      - POSTGRES_USER=icarus
      - POSTGRES_PASSWORD=icarus
      - POSTGRES_DB=icarus
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U icarus -d icarus"]
      interval: 10s
      timeout: 5s
      retries: 5

  ts-executor:
    build:
      context: ./ts-executor
      dockerfile: Dockerfile
      target: dev
    depends_on:
      redis:
        condition: service_healthy
    env_file: .env
    environment:
      - REDIS_URL=redis://redis:6379
    volumes:
      - ./ts-executor/src:/app/src
      - ./shared:/app/shared
    restart: unless-stopped

  py-engine:
    build:
      context: ./py-engine
      dockerfile: Dockerfile
      target: dev
    depends_on:
      redis:
        condition: service_healthy
      postgres:
        condition: service_healthy
    env_file: .env
    environment:
      - REDIS_URL=redis://redis:6379
      - DATABASE_URL=postgresql+psycopg2://icarus:icarus@postgres:5432/icarus
    volumes:
      - ./py-engine:/app
      - ./shared:/app/shared
    restart: unless-stopped

volumes:
  redis-data:
  postgres-data:
```

**Key changes from current file:**
- Added `postgres` service (lines after redis, before ts-executor)
- Added `postgres-data` to named volumes
- py-engine `depends_on` now includes `postgres: condition: service_healthy`
- py-engine `environment` now includes `DATABASE_URL`

**Step 2: Verify compose config is valid**

Run: `cd /home/heresy/Documents/Projects/crypto/icarus && docker compose config --quiet`
Expected: No errors (exit 0).

**Step 3: Commit**

```bash
git add docker-compose.yml
git commit -m "feat(icarus): add PostgreSQL service to docker-compose"
```

---

### Task 3: Update .env.example and .gitignore

**Files:**
- Modify: `.env.example:11-13`
- Modify: `.gitignore` (append)

**Step 1: Add DATABASE_URL to .env.example**

In `.env.example`, update the "Price Feed" section (line 11) to add DATABASE_URL before it:

```
# ── Database ──────────────────────────────────────
DATABASE_URL=postgresql+psycopg2://icarus:icarus@localhost:5432/icarus

# ── Price Feed ─────────────────────────────────────
```

Note: The `.env.example` value uses `localhost` (for running py-engine outside Docker). Inside Docker, `docker-compose.yml` overrides this with `postgres` hostname.

**Step 2: Add *.db to .gitignore**

Append to `.gitignore`:

```
# Database
*.db
```

**Step 3: Remove the existing icarus.db if present**

Run: `cd /home/heresy/Documents/Projects/crypto/icarus && rm -f py-engine/data/icarus.db`

**Step 4: Commit**

```bash
git add .env.example .gitignore
git commit -m "feat(icarus): add DATABASE_URL to .env.example, gitignore *.db files"
```

---

### Task 4: Integration test — docker compose up

**Step 1: Build and start all services**

Run: `cd /home/heresy/Documents/Projects/crypto/icarus && docker compose up --build -d`
Expected: All 4 services start (redis, postgres, ts-executor, py-engine).

**Step 2: Verify postgres is healthy**

Run: `docker compose ps`
Expected: postgres shows "healthy" status.

**Step 3: Verify py-engine connected to PostgreSQL (not SQLite)**

Run: `docker compose logs py-engine 2>&1 | grep -i "database\|engine\|postgres\|sqlite"`
Expected: Log line showing `Database engine created` with `postgresql` URL (not sqlite). No SQLite-related errors.

**Step 4: Verify tables were created**

Run: `docker compose exec postgres psql -U icarus -d icarus -c "\dt"`
Expected: Lists tables: trades, portfolio_snapshots, strategy_performance, alerts, schema_versions.

**Step 5: Verify no icarus.db created inside container**

Run: `docker compose exec py-engine ls -la data/ 2>/dev/null || echo "No data dir (good)"`
Expected: No `icarus.db` file.

**Step 6: Tear down**

Run: `docker compose down`
(Named volumes persist for next startup.)
