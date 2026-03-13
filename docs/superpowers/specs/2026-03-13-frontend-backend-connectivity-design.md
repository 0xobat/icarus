# Frontend-Backend Connectivity — Design Spec

**Date:** 2026-03-13
**Status:** Draft
**Scope:** Wire the Icarus dashboard to live backend data via Next.js API routes, Redis communication channels, and JWT authentication.

---

## 1. Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                         Browser                               │
│  Next.js Client (React)                                       │
│  ├── REST calls → /api/*  (initial load, queries, commands)   │
│  └── SSE stream → /api/events (real-time event stream)        │
└──────────────┬──────────────────────┬─────────────────────────┘
               │                      │
┌──────────────▼──────────────────────▼─────────────────────────┐
│              Next.js Server (API Routes)                       │
│  ├── Auth middleware (JWT — login/logout/session)              │
│  ├── GET routes → PostgreSQL (positions, trades, decisions)   │
│  ├── GET routes → Redis KV (hold mode, breakers, drawdown)    │
│  ├── POST routes → Redis Stream (dashboard:commands)          │
│  └── SSE route → Redis Stream subscriber (dashboard:events)   │
└──────┬──────────────────────┬─────────────────────────────────┘
       │                      │
  ┌────▼────┐          ┌──────▼──────┐
  │PostgreSQL│          │    Redis    │
  │(persist) │          │ (KV+Streams)│
  └────▲────┘          └──────▲──────┘
       │                      │
┌──────┴──────────────────────┴─────────────────────────────────┐
│                       py-engine                                │
│  DecisionLoop (unchanged core logic)                           │
│  ├── Publishes in-memory state → Redis KV (each cycle)        │
│  ├── Publishes lifecycle events → dashboard:events stream     │
│  └── Subscribes to dashboard:commands stream (new listener)   │
└───────────────────────────────────────────────────────────────┘
```

### Key Principles

- py-engine never exposes HTTP. It reads/writes Redis and Postgres only.
- Next.js is the only public-facing surface. All auth lives here.
- Redis is the universal bus: py-engine ↔ ts-executor (existing), py-engine ↔ frontend (new).
- Next.js never writes to Postgres directly — mutations go through `dashboard:commands` so py-engine owns all state changes.
- PostgreSQL is read-only from the Next.js perspective.

### Why SSE Instead of WebSocket

Next.js App Router route handlers do not support WebSocket upgrades natively — `route.ts` files respond via `Response` objects only. Server-Sent Events (SSE) work natively in App Router via `ReadableStream` responses, require no custom server, and are compatible with all deployment targets (Vercel, Railway, self-hosted). SSE is unidirectional (server → client), which matches our use case — the client sends commands via REST POST, and receives events via SSE.

---

## 2. Authentication

### Flow

1. User hits any page → Next.js middleware checks for valid JWT cookie.
2. No valid JWT → redirect to `/login`.
3. Login page sends `POST /api/auth/login` with username + password.
4. Server validates against env vars (`ICARUS_ADMIN_USER`, `ICARUS_ADMIN_PASSWORD_HASH`).
5. On success → sets `httpOnly`, `secure`, `sameSite=strict` cookie containing signed JWT.
6. JWT contains: `{ sub: username, iat, exp }` — expires in 24h.
7. All subsequent API requests validated by middleware reading the cookie.
8. `POST /api/auth/logout` → clears the cookie.

### Details

- **Password storage:** bcrypt hash stored in `ICARUS_ADMIN_PASSWORD_HASH` env var. No database table for users — single operator, single credential.
- **JWT signing:** `ICARUS_JWT_SECRET` env var. HS256 algorithm via `jose` library.
- **Protected routes:** Everything except `/login` and `/api/auth/login`.
- **SSE auth:** The SSE request includes the same cookie. JWT validated before starting the stream.

### Environment Variables

```
ICARUS_JWT_SECRET=<random-256-bit-secret>
ICARUS_ADMIN_USER=<username>
ICARUS_ADMIN_PASSWORD_HASH=<bcrypt-hash>
```

---

## 3. Redis Communication — New Channels

### 3a: Redis KV (py-engine publishes each decision cycle)

py-engine writes these keys at the end of each decision cycle. TTL of 120s ensures auto-expiry if py-engine dies.

| Key | Value | Updated |
|-----|-------|---------|
| `dashboard:metrics` | Full `MetricsData` shape (see below) | Every cycle |
| `dashboard:strategies` | `StrategiesPanelData` envelope: `{strategies: StrategyData[], reserve: {amount, pct}, total_value}` | Every cycle |
| `dashboard:breakers` | Array of `{name, current, limit, unit, status, last_triggered}` | Every cycle |
| `dashboard:drawdown` | `{current_pct, peak_value, current_value, level, limit}` | Every cycle |
| `dashboard:exposure` | Array of `{scope, name, current_allocation, current_pct, limit_pct, headroom}` | Every cycle |
| `dashboard:reserve` | `{liquid_reserve, min_reserve_requirement, reserve_pct}` | Every cycle |
| `dashboard:hold_mode` | `{active, reason, since}` — structured hold mode state | On change |
| `dashboard:health` | Array of `{name, status, latency_ms, last_heartbeat, error_count_24h}` | Every cycle |
| `system_status` | `"normal"` or `"hold"` (already exists, kept for backward compat) | On change |

#### `dashboard:metrics` Full Shape

Matches `MetricsData` from `types.ts` exactly:

```json
{
  "portfolio_value": 847293.42,
  "portfolio_change_24h_pct": 2.89,
  "portfolio_change_24h_abs": 23847,
  "portfolio_sparkline": [820100, 821400, ...],
  "drawdown_current": 4.2,
  "drawdown_limit": 20,
  "pnl_today": 23847,
  "pnl_today_pct": 2.89,
  "pnl_sparkline": [1200, 2400, ...],
  "tx_success_rate": 98.7,
  "tx_success_count": 142,
  "tx_total_count": 144
}
```

**Derived fields:**
- `portfolio_change_24h_pct` and `portfolio_change_24h_abs`: computed by comparing current `portfolio_value` to the most recent `PortfolioSnapshot` from 24h ago in PostgreSQL. If no 24h snapshot exists, defaults to 0.
- `portfolio_sparkline`: last 24 hourly values from `PortfolioSnapshots` table.
- `pnl_sparkline`: last 16 P&L deltas computed from sequential snapshots.
- `drawdown_limit`: read from `DRAWDOWN_LIMIT_PCT` env var (default 20).

#### `dashboard:hold_mode` Shape

Matches `HoldModeData` from `types.ts`:

```json
{
  "active": true,
  "reason": "Claude API unavailable — rate limit exceeded",
  "since": "2026-03-11T09:42:00Z"
}
```

When hold mode is inactive: `{"active": false, "reason": "", "since": ""}`.

#### `dashboard:health` Shape

Matches `ServiceHealth[]` from `types.ts`:

```json
[
  {
    "name": "Redis",
    "status": "connected",
    "latency_ms": 1.2,
    "last_heartbeat": "2026-03-11T14:29:58Z",
    "error_count_24h": 0
  }
]
```

**Sources:**
- `latency_ms`: measured by py-engine's health checks (Redis ping, Postgres query, Alchemy WS latency, Claude API response time).
- `last_heartbeat`: timestamp of last successful health check.
- `error_count_24h`: counted from the `Alerts` table in PostgreSQL, filtered to last 24h per service category.

Frontend's stale-indicator component handles TTL expiry — if a fetch returns null, it shows the "STALE" badge.

### 3b: `dashboard:events` Stream (py-engine → frontend)

Real-time lifecycle events. Schema: `shared/schemas/dashboard-events.schema.json`.

All events include a top-level `version` field (`"1.0.0"`) consistent with existing stream schemas.

| Event Type | Payload | Emitted When |
|-----------|---------|-------------|
| `eval_complete` | `{strategy_id, signals_count, actionable, timestamp}` | Strategy evaluation finishes |
| `decision_made` | `{decision_id, action, summary, order_count, timestamp}` | Claude API returns or HOLD decided |
| `order_emitted` | `{order_id, strategy, protocol, action, amount, timestamp}` | Order published to `execution:orders` |
| `execution_result` | `{order_id, tx_hash, status, gas_used, effective_gas_price, timestamp}` | Result received from ts-executor |
| `breaker_state` | `{name, status, current, limit, timestamp}` | Any breaker changes state |
| `hold_mode` | `{active, reason, timestamp}` | Hold mode enters or exits |
| `system_health` | `{service, status, latency_ms, timestamp}` | Connection state changes |
| `command_ack` | `{command_id, command_type, success, error, timestamp}` | After processing a dashboard command |

`execution_result` uses raw `gas_used` and `effective_gas_price` strings from the upstream `execution-results` schema. The frontend computes `gas_cost_usd` by multiplying these with the ETH price from `dashboard:metrics` if needed for display.

MAXLEN pruning at 1000 entries (same pattern as existing streams).

### 3c: `dashboard:commands` Stream (frontend → py-engine)

Control operations. Schema: `shared/schemas/dashboard-commands.schema.json`.

All commands include a top-level `version` field (`"1.0.0"`) consistent with existing stream schemas, plus a `command_id` (UUID) for acknowledgment tracking and a `timestamp` (ISO8601).

| Command | Payload | Effect |
|---------|---------|--------|
| `strategy:activate` | `{strategy_id}` | py-engine activates strategy |
| `strategy:deactivate` | `{strategy_id}` | py-engine deactivates strategy |
| `system:enter_hold` | `{reason}` | Force hold mode |
| `system:exit_hold` | `{}` | Exit hold mode |
| `breaker:reset` | `{breaker_name}` | Reset tripped circuit breaker (see safety guards below) |

#### Consumer Group Configuration

- **Group name:** `dashboard-cmd-group`
- **Consumer name:** `py-engine-{instance_id}` (instance_id from hostname or env var)
- **Startup:** py-engine creates the group on startup via `XGROUP CREATE dashboard:commands dashboard-cmd-group $ MKSTREAM` (idempotent — ignores if exists, reads only new messages)
- **Acknowledgment:** `XACK` after successful processing of each command
- **Recovery:** On startup, `XAUTOCLAIM` pending messages older than 60s. Commands with `timestamp` older than 5 minutes are discarded (stale command protection) and logged as warnings.

#### `breaker:reset` Safety Guards

- Manual resets are logged to the `Alerts` table with `source: "manual_reset"` and the operator identity.
- A `command_ack` event is emitted to `dashboard:events` with `source: "manual"` for audit trail.
- Cooldown behavior: the reset clears the tripped state but the breaker continues monitoring. If the underlying condition still exceeds the threshold, it will re-trip on the next cycle. This prevents the operator from permanently disabling safety mechanisms.
- The frontend must use the existing `confirm-dialog` component before sending `breaker:reset`, with a warning message explaining the implications.

---

## 4. Next.js API Routes

### Auth

| Method | Route | Description |
|--------|-------|-------------|
| `POST` | `/api/auth/login` | Validate credentials, return JWT cookie |
| `POST` | `/api/auth/logout` | Clear JWT cookie |
| `GET` | `/api/auth/me` | Return current session info |

### Dashboard (Command page)

| Method | Route | Source | Description |
|--------|-------|--------|-------------|
| `GET` | `/api/dashboard/metrics` | Redis KV | Full `MetricsData` object |
| `GET` | `/api/dashboard/strategies` | Redis KV | `StrategiesPanelData` envelope (strategies + reserve + total_value) |
| `GET` | `/api/dashboard/breakers` | Redis KV | Circuit breaker states array |
| `GET` | `/api/dashboard/decisions/recent` | PostgreSQL | Last N Claude decisions (default 10, max 50) |
| `GET` | `/api/dashboard/executions/recent` | PostgreSQL | Last N executions (default 10, max 50) |

### Portfolio

| Method | Route | Source | Description |
|--------|-------|--------|-------------|
| `GET` | `/api/portfolio/positions` | PostgreSQL | Open positions with P&L |
| `GET` | `/api/portfolio/snapshots` | PostgreSQL | Historical snapshots for chart. Query: `?range=24h\|7d\|1m\|3m\|ytd\|all` |
| `GET` | `/api/portfolio/reserve` | Redis KV | Current liquid reserve |

### Decisions

| Method | Route | Source | Description |
|--------|-------|--------|-------------|
| `GET` | `/api/decisions` | PostgreSQL | Paginated decision audit log (see pagination below) |
| `GET` | `/api/decisions/[id]` | PostgreSQL | Single decision detail |

#### Pagination

`GET /api/decisions` supports:
- `?limit=N` — page size (default 20, max 100)
- `?cursor=<decision_id>` — cursor-based pagination (return decisions older than this ID)
- `?strategy=LEND-001` — filter by strategy
- `?action=ENTRY,HOLD` — filter by action type (comma-separated)
- `?source=claude,circuit_breaker` — filter by decision source

Response envelope:
```json
{
  "data": [...],
  "next_cursor": "DEC-8900",
  "has_more": true
}
```

`GET /api/dashboard/decisions/recent` and `GET /api/dashboard/executions/recent` accept `?limit=N` (default 10, max 50) with no cursor (always returns latest).

### Risk

| Method | Route | Source | Description |
|--------|-------|--------|-------------|
| `GET` | `/api/risk/exposure` | Redis KV | Current exposure limits |
| `GET` | `/api/risk/drawdown` | Redis KV | Drawdown state |
| `GET` | `/api/system/status` | Redis KV | `HoldModeData` from `dashboard:hold_mode` key |
| `GET` | `/api/system/health` | Redis KV | `ServiceHealth[]` from `dashboard:health` key |

### Commands (writes → Redis Stream)

| Method | Route | Publishes To | Description |
|--------|-------|-------------|-------------|
| `POST` | `/api/commands/strategy/activate` | `dashboard:commands` | Activate strategy |
| `POST` | `/api/commands/strategy/deactivate` | `dashboard:commands` | Deactivate strategy |
| `POST` | `/api/commands/hold/enter` | `dashboard:commands` | Force hold mode |
| `POST` | `/api/commands/hold/exit` | `dashboard:commands` | Exit hold mode |
| `POST` | `/api/commands/breaker/reset` | `dashboard:commands` | Reset circuit breaker |

All POST routes generate a `command_id` (UUID), publish to the stream, and return `{command_id}` to the client. The client can listen for the `command_ack` event on the SSE stream to confirm processing.

### Server-Sent Events

| Route | Description |
|-------|-------------|
| `GET /api/events` | SSE stream. Subscribes to `dashboard:events` Redis stream via `XREAD BLOCK`, forwards events as `data: {json}\n\n` messages. JWT validated from cookie before starting stream. Auto-reconnect via `EventSource` on the client side. |

---

## 5. py-engine Changes

Three new modules, minimal insertions into existing code. Zero changes to core decision/risk/strategy logic.

### 5a: State Publisher (`monitoring/state_publisher.py`)

A single function called at the end of each decision cycle:

```python
async def publish_dashboard_state(redis, tracker, drawdown, breakers, exposure, strategies, metrics, db_repo):
    """Serialize in-memory state to Redis KV keys with 120s TTL.

    Reads PortfolioSnapshots from db_repo for 24h change and sparkline derivation.
    Reads Alerts table for error_count_24h per service.
    """
```

Collects state from existing modules (PositionTracker, DrawdownBreaker, etc.), serializes to JSON (converting Decimal to float), writes `dashboard:*` KV keys. One call per cycle.

### 5b: Event Emitter (`monitoring/event_emitter.py`)

Publishes to the `dashboard:events` stream:

```python
async def emit_dashboard_event(redis, event_type, payload):
    """Publish a lifecycle event to dashboard:events stream with MAXLEN 1000.

    Adds version '1.0.0' and validates against dashboard-events schema.
    """
```

Called at key points in the DecisionLoop (~7 one-liner insertions):
- After `evaluate()` → `eval_complete`
- After Claude API call → `decision_made`
- After order emission → `order_emitted`
- After execution result received → `execution_result`
- On breaker state change → `breaker_state`
- On hold mode change → `hold_mode`
- After command processing → `command_ack`

### 5c: Command Listener (`harness/command_listener.py`)

Async task running alongside the DecisionLoop:

```python
async def listen_for_commands(redis, strategy_manager, hold_mode, breakers, event_emitter, db_repo):
    """Subscribe to dashboard:commands stream via consumer group, dispatch to handlers.

    Consumer group: dashboard-cmd-group
    On startup: XGROUP CREATE (idempotent), XAUTOCLAIM pending >60s, discard commands >5min old.
    On success: XACK + emit command_ack event.
    On failure: log error + emit command_ack with error details.
    """
```

Dispatches to existing methods:
- `strategy:activate` → `strategy_manager.activate(id)`
- `strategy:deactivate` → `strategy_manager.deactivate(id)`
- `system:enter_hold` → `hold_mode.enter(reason)`
- `system:exit_hold` → `hold_mode.exit()`
- `breaker:reset` → `breaker.reset(name)` + `db_repo.record_alert(source="manual_reset")`

Runs as a separate `asyncio.Task` spawned in `main.py`.

### 5d: Shared Schemas

Two new files in `shared/schemas/`:
- `dashboard-events.schema.json` — includes `version` field, `eventType` enum, per-type payload validation
- `dashboard-commands.schema.json` — includes `version` field, `command_id`, `commandType` enum, per-type payload validation

Both validated at the boundary, same pattern as existing schemas.

---

## 6. Frontend Changes

### 6a: New Dependencies

- `ioredis` — Redis client for API routes (server-side only)
- `postgres` (via `pg` package) — PostgreSQL client for API routes
- `jose` — JWT signing/verification
- `bcryptjs` — Password hash verification (pure JS, no native deps)

### 6b: New Files

**Auth:**
- `src/app/login/page.tsx` — Login page
- `src/app/api/auth/login/route.ts` — POST handler
- `src/app/api/auth/logout/route.ts` — POST handler
- `src/app/api/auth/me/route.ts` — GET handler
- `src/middleware.ts` — JWT validation, redirect to `/login` if invalid

**Server-side clients:**
- `src/lib/server/redis.ts` — Redis client singleton
- `src/lib/server/db.ts` — PostgreSQL client singleton
- `src/lib/server/auth.ts` — JWT sign/verify, password validation

**API routes:** One route handler file per endpoint (Section 4).

**SSE:**
- `src/app/api/events/route.ts` — SSE endpoint, subscribe to `dashboard:events` stream, forward as SSE messages

**Client-side hooks:**
- `src/lib/hooks/use-api.ts` — Fetch wrapper with 401 redirect
- `src/lib/hooks/use-dashboard.ts` — Command page hooks
- `src/lib/hooks/use-portfolio.ts` — Portfolio page hooks
- `src/lib/hooks/use-decisions.ts` — Decisions page hooks
- `src/lib/hooks/use-risk.ts` — Risk page hooks
- `src/lib/hooks/use-event-stream.ts` — SSE connection manager (`EventSource`) with reconnection backoff
- `src/lib/hooks/use-commands.ts` — Write operation hooks (POST + listen for `command_ack`)

### 6c: Component Changes

Every component switches from `mock-data.ts` imports to hooks:

```tsx
// Before
import { metricsData } from "@/lib/mock-data";

// After
const { data, isLoading, error } = useDashboardMetrics();
```

- Loading → existing `LoadingSkeleton` component
- Error → existing connection banner
- Stale → existing stale indicator
- SSE events update local state in real-time between REST fetches

`mock-data.ts` remains in repo for development/testing, no longer imported by components.

### 6d: Frontend Environment Variables

```
DATABASE_URL=postgresql://...
REDIS_URL=redis://...
ICARUS_JWT_SECRET=<random-256-bit-secret>
ICARUS_ADMIN_USER=<username>
ICARUS_ADMIN_PASSWORD_HASH=<bcrypt-hash>
```

All server-side only (no `NEXT_PUBLIC_` prefix).

---

## 7. Error Handling & Resilience

### Frontend

- **SSE disconnects:** `EventSource` auto-reconnects natively. Show connection banner during reconnection. Fall back to REST polling at 10s intervals if SSE fails 3 times consecutively.
- **REST endpoint fails:** Show error state in component, retry on next interval.
- **JWT expires mid-session:** API returns 401 → redirect to login.
- **Redis KV returns null (TTL expired):** Show stale indicator badge. Last successful data remains visible.

### py-engine

- **`dashboard:events` publish fails:** Log warning, continue decision loop. Dashboard events are non-critical.
- **`dashboard:commands` processing fails:** Log error, emit `command_ack` with error details to `dashboard:events`. Do not halt the loop.
- **State publisher fails:** Log warning, continue. KV keys expire via TTL, frontend shows stale indicator.
- **Command listener crash:** Restart as new asyncio task. Consumer group ensures no commands are lost (recovered via `XAUTOCLAIM` on restart).

### Key Principle

Dashboard connectivity is a **secondary concern**. The decision loop, circuit breakers, and execution pipeline must never be affected by dashboard failures. All new code is fire-and-forget with error logging.

---

## 8. Testing Strategy

### py-engine

- `test_state_publisher.py` — Verify all Redis KV keys written with correct shape matching `types.ts` interfaces, verify TTL set, verify Decimal→float serialization, verify 24h change derivation from snapshots.
- `test_event_emitter.py` — Verify events published to `dashboard:events` with correct schema including `version` field.
- `test_command_listener.py` — Verify each command dispatches to correct handler, verify invalid commands rejected gracefully, verify stale command (>5min) discarded, verify `command_ack` emitted, verify `breaker:reset` records audit alert.
- All use mock Redis (existing pattern in test suite).

### Shared Schemas

- `dashboard-events.schema.json` and `dashboard-commands.schema.json` validated by existing `verify.sh`.

### Frontend

- **Auth:** Unit tests for JWT sign/verify, password validation, middleware redirect.
- **API routes:** Integration tests with mock Redis/Postgres, verify JSON shape matches `types.ts`.
- **SSE:** Mock Redis stream, verify events forwarded correctly as SSE messages.
- **Hooks:** Mock API responses, verify loading/error/data states. Test SSE reconnection and polling fallback.

### End-to-End (manual)

1. Start py-engine + Redis + Postgres → verify `dashboard:*` KV keys populated.
2. Start frontend → verify pages load real data.
3. Send command via UI → verify py-engine receives, acts, and emits `command_ack`.
4. Kill py-engine → verify frontend shows stale indicators, SSE reconnects when py-engine returns.

---

## 9. PostgreSQL Schema Reference

py-engine is the sole writer. Next.js reads these tables. Schemas are defined in `py-engine/db/models.py` via SQLAlchemy. Key tables referenced by API routes:

### `portfolio_positions`

| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID | Primary key |
| `strategy_id` | VARCHAR | e.g., "LEND-001" |
| `strategy_name` | VARCHAR | e.g., "Aave V3 Lending Supply" |
| `protocol` | VARCHAR | e.g., "Aave V3" |
| `asset` | VARCHAR | e.g., "USDC" |
| `amount` | NUMERIC | Position size (token units) |
| `entry_price` | NUMERIC | Price at entry |
| `current_value` | NUMERIC | Latest value (USD) |
| `unrealized_pnl` | NUMERIC | Current - entry value |
| `unrealized_pnl_pct` | NUMERIC | PnL as percentage |
| `portfolio_pct` | NUMERIC | % of total portfolio |
| `status` | VARCHAR | "open" or "closed" |
| `entry_timestamp` | TIMESTAMP | When opened |
| `tx_hash` | VARCHAR | Entry transaction hash |

### `portfolio_snapshots`

| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID | Primary key |
| `timestamp` | TIMESTAMP | Snapshot time |
| `total_value` | NUMERIC | Portfolio total USD |
| `positions_json` | JSONB | Serialized position breakdown |

Used for chart data (`/api/portfolio/snapshots`) and deriving `portfolio_change_24h_*` and sparkline fields.

### `decision_audit_log`

| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID | Primary key |
| `correlation_id` | VARCHAR | e.g., "DEC-8942" |
| `timestamp` | TIMESTAMP | Decision time |
| `source` | VARCHAR | "claude" or "circuit_breaker" |
| `action` | VARCHAR | "ENTRY", "EXIT", "REBALANCE", "HOLD" |
| `summary` | TEXT | One-line summary |
| `reasoning` | TEXT | Claude's reasoning (can be long) |
| `trigger_reports` | JSONB | Array of `{strategy_id, signals[]}` |
| `orders` | JSONB | Array of orders with action, protocol, asset, amount (USD float), parameters |
| `verification` | JSONB | `{passed, checks[]}` |

Note: `orders[].amount` is stored as a USD-denominated float (converted from wei by py-engine at recording time using the price feed). This matches the `number` type in `types.ts`.

### `trades`

| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID | Primary key |
| `order_id` | VARCHAR | Correlation to the order |
| `timestamp` | TIMESTAMP | Execution time |
| `strategy_id` | VARCHAR | Strategy that triggered this |
| `type` | VARCHAR | "entry", "exit", "harvest", "rebalance" |
| `description` | TEXT | Human-readable description |
| `tx_hash` | VARCHAR | On-chain TX hash |
| `status` | VARCHAR | "success", "pending", "failed" |
| `value` | NUMERIC | USD value |
| `gas_used` | VARCHAR | Gas used (string, wei) |
| `effective_gas_price` | VARCHAR | Gas price (string, wei) |

### `alerts`

| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID | Primary key |
| `timestamp` | TIMESTAMP | Alert time |
| `category` | VARCHAR | Service or breaker name |
| `severity` | VARCHAR | "info", "warning", "critical" |
| `message` | TEXT | Description |
| `source` | VARCHAR | "automatic", "manual_reset" |
| `acknowledged` | BOOLEAN | Whether operator has seen it |
