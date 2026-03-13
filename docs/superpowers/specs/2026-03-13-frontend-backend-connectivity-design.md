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
│  └── WebSocket  → /api/ws (real-time event stream)            │
└──────────────┬──────────────────────┬─────────────────────────┘
               │                      │
┌──────────────▼──────────────────────▼─────────────────────────┐
│              Next.js Server (API Routes)                       │
│  ├── Auth middleware (JWT — login/logout/session)              │
│  ├── GET routes → PostgreSQL (positions, trades, decisions)   │
│  ├── GET routes → Redis KV (hold mode, breakers, drawdown)    │
│  ├── POST routes → Redis Stream (dashboard:commands)          │
│  └── WS route → Redis Stream subscriber (dashboard:events)    │
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
- **WebSocket auth:** WS upgrade request includes the same cookie. Middleware validates JWT before accepting the connection.

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
| `dashboard:metrics` | `{portfolio_value, drawdown_current, pnl_today, pnl_today_pct, tx_success_rate, tx_success_count, tx_total_count, portfolio_sparkline}` | Every cycle |
| `dashboard:strategies` | Array of `{id, name, status, allocation, allocation_pct, pnl, pnl_pct, last_eval_ago, active_signals}` | Every cycle |
| `dashboard:breakers` | Array of `{name, current, limit, unit, status, last_triggered}` | Every cycle |
| `dashboard:drawdown` | `{current_pct, peak_value, current_value, level, limit}` | Every cycle |
| `dashboard:exposure` | Array of `{scope, name, current_allocation, current_pct, limit_pct, headroom}` | Every cycle |
| `dashboard:reserve` | `{liquid_reserve, min_reserve_requirement, reserve_pct}` | Every cycle |
| `system_status` | `"normal"` or `"hold"` | On change (already exists) |

Frontend's stale-indicator component handles TTL expiry — if a fetch returns null, it shows the "STALE" badge.

### 3b: `dashboard:events` Stream (py-engine → frontend)

Real-time lifecycle events. Schema: `shared/schemas/dashboard-events.schema.json`.

| Event Type | Payload | Emitted When |
|-----------|---------|-------------|
| `eval_complete` | `{strategy_id, signals_count, actionable, timestamp}` | Strategy evaluation finishes |
| `decision_made` | `{decision_id, action, summary, order_count, timestamp}` | Claude API returns or HOLD decided |
| `order_emitted` | `{order_id, strategy, protocol, action, amount, timestamp}` | Order published to `execution:orders` |
| `execution_result` | `{order_id, tx_hash, status, gas_cost_usd, timestamp}` | Result received from ts-executor |
| `breaker_state` | `{name, status, current, limit, timestamp}` | Any breaker changes state |
| `hold_mode` | `{active, reason, timestamp}` | Hold mode enters or exits |
| `system_health` | `{service, status, latency_ms, timestamp}` | Connection state changes |

MAXLEN pruning at 1000 entries (same pattern as existing streams).

### 3c: `dashboard:commands` Stream (frontend → py-engine)

Control operations. Schema: `shared/schemas/dashboard-commands.schema.json`.

| Command | Payload | Effect |
|---------|---------|--------|
| `strategy:activate` | `{strategy_id}` | py-engine activates strategy |
| `strategy:deactivate` | `{strategy_id}` | py-engine deactivates strategy |
| `system:enter_hold` | `{reason}` | Force hold mode |
| `system:exit_hold` | `{}` | Exit hold mode |
| `breaker:reset` | `{breaker_name}` | Reset tripped circuit breaker |

py-engine subscribes via consumer group for reliable delivery.

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
| `GET` | `/api/dashboard/metrics` | Redis KV | Portfolio value, drawdown, P&L, TX rate |
| `GET` | `/api/dashboard/strategies` | Redis KV | Strategy list with status, allocation, signals |
| `GET` | `/api/dashboard/breakers` | Redis KV | Circuit breaker states |
| `GET` | `/api/dashboard/decisions/recent` | PostgreSQL | Last N Claude decisions |
| `GET` | `/api/dashboard/executions/recent` | PostgreSQL | Last N executions |

### Portfolio

| Method | Route | Source | Description |
|--------|-------|--------|-------------|
| `GET` | `/api/portfolio/positions` | PostgreSQL | Open positions with P&L |
| `GET` | `/api/portfolio/snapshots` | PostgreSQL | Historical snapshots for chart |
| `GET` | `/api/portfolio/reserve` | Redis KV | Current liquid reserve |

### Decisions

| Method | Route | Source | Description |
|--------|-------|--------|-------------|
| `GET` | `/api/decisions` | PostgreSQL | Paginated decision audit log |
| `GET` | `/api/decisions/[id]` | PostgreSQL | Single decision detail |

### Risk

| Method | Route | Source | Description |
|--------|-------|--------|-------------|
| `GET` | `/api/risk/exposure` | Redis KV | Current exposure limits |
| `GET` | `/api/risk/drawdown` | Redis KV | Drawdown state |
| `GET` | `/api/system/status` | Redis KV | Hold mode state |
| `GET` | `/api/system/health` | Redis KV | Service health |

### Commands (writes → Redis Stream)

| Method | Route | Publishes To | Description |
|--------|-------|-------------|-------------|
| `POST` | `/api/commands/strategy/activate` | `dashboard:commands` | Activate strategy |
| `POST` | `/api/commands/strategy/deactivate` | `dashboard:commands` | Deactivate strategy |
| `POST` | `/api/commands/hold/enter` | `dashboard:commands` | Force hold mode |
| `POST` | `/api/commands/hold/exit` | `dashboard:commands` | Exit hold mode |
| `POST` | `/api/commands/breaker/reset` | `dashboard:commands` | Reset circuit breaker |

### WebSocket

| Route | Description |
|-------|-------------|
| `/api/ws` | Upgrade to WebSocket. Subscribes to `dashboard:events` Redis stream, forwards events to client. JWT validated on upgrade. |

---

## 5. py-engine Changes

Three new modules, minimal insertions into existing code. Zero changes to core decision/risk/strategy logic.

### 5a: State Publisher (`monitoring/state_publisher.py`)

A single function called at the end of each decision cycle:

```python
async def publish_dashboard_state(redis, tracker, drawdown, breakers, exposure, strategies, metrics):
    """Serialize in-memory state to Redis KV keys with 120s TTL."""
```

Collects state from existing modules (PositionTracker, DrawdownBreaker, etc.), serializes to JSON, writes `dashboard:*` KV keys. One call per cycle.

### 5b: Event Emitter (`monitoring/event_emitter.py`)

Publishes to the `dashboard:events` stream:

```python
async def emit_dashboard_event(redis, event_type, payload):
    """Publish a lifecycle event to dashboard:events stream with MAXLEN 1000."""
```

Called at key points in the DecisionLoop (~6 one-liner insertions):
- After `evaluate()` → `eval_complete`
- After Claude API call → `decision_made`
- After order emission → `order_emitted`
- After execution result received → `execution_result`
- On breaker state change → `breaker_state`
- On hold mode change → `hold_mode`

### 5c: Command Listener (`harness/command_listener.py`)

Async task running alongside the DecisionLoop:

```python
async def listen_for_commands(redis, strategy_manager, hold_mode, breakers):
    """Subscribe to dashboard:commands stream via consumer group, dispatch to handlers."""
```

Dispatches to existing methods:
- `strategy:activate` → `strategy_manager.activate(id)`
- `strategy:deactivate` → `strategy_manager.deactivate(id)`
- `system:enter_hold` → `hold_mode.enter(reason)`
- `system:exit_hold` → `hold_mode.exit()`
- `breaker:reset` → `breaker.reset(name)`

Runs as a separate `asyncio.Task` spawned in `main.py`.

### 5d: Shared Schemas

Two new files in `shared/schemas/`:
- `dashboard-events.schema.json`
- `dashboard-commands.schema.json`

Both validated at the boundary, same pattern as existing schemas.

---

## 6. Frontend Changes

### 6a: New Dependencies

- `ioredis` — Redis client for API routes (server-side only)
- `pg` or `postgres` — PostgreSQL client for API routes
- `jose` — JWT signing/verification
- `bcrypt` — Password hash verification

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

**WebSocket:**
- `src/app/api/ws/route.ts` — WebSocket upgrade, subscribe to `dashboard:events`, forward to client

**Client-side hooks:**
- `src/lib/hooks/use-api.ts` — Fetch wrapper with 401 redirect
- `src/lib/hooks/use-dashboard.ts` — Command page hooks
- `src/lib/hooks/use-portfolio.ts` — Portfolio page hooks
- `src/lib/hooks/use-decisions.ts` — Decisions page hooks
- `src/lib/hooks/use-risk.ts` — Risk page hooks
- `src/lib/hooks/use-event-stream.ts` — WebSocket manager with reconnection backoff
- `src/lib/hooks/use-commands.ts` — Write operation hooks

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
- WS events update local state in real-time between REST fetches

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

- **WebSocket disconnects:** Show connection banner ("Reconnecting..."), exponential backoff (1s → 2s → 4s → max 30s), fall back to REST polling at 10s intervals until WS reconnects.
- **REST endpoint fails:** Show error state in component, retry on next interval.
- **JWT expires mid-session:** API returns 401 → redirect to login.
- **Redis KV returns null (TTL expired):** Show stale indicator badge. Last successful data remains visible.

### py-engine

- **`dashboard:events` publish fails:** Log warning, continue decision loop. Dashboard events are non-critical.
- **`dashboard:commands` processing fails:** Log error, publish failure event to `dashboard:events`. Do not halt the loop.
- **State publisher fails:** Log warning, continue. KV keys expire via TTL, frontend shows stale indicator.
- **Command listener crash:** Restart as new asyncio task. Consumer group ensures no commands are lost.

### Key Principle

Dashboard connectivity is a **secondary concern**. The decision loop, circuit breakers, and execution pipeline must never be affected by dashboard failures. All new code is fire-and-forget with error logging.

---

## 8. Testing Strategy

### py-engine

- `test_state_publisher.py` — Verify Redis KV keys written with correct shape and TTL, verify Decimal→float serialization.
- `test_event_emitter.py` — Verify events published to `dashboard:events` with correct schema.
- `test_command_listener.py` — Verify each command dispatches to correct handler, verify invalid commands rejected gracefully.
- All use mock Redis (existing pattern in test suite).

### Shared Schemas

- `dashboard-events.schema.json` and `dashboard-commands.schema.json` validated by existing `verify.sh`.

### Frontend

- **Auth:** Unit tests for JWT sign/verify, password validation, middleware redirect.
- **API routes:** Integration tests with mock Redis/Postgres, verify JSON shape matches `types.ts`.
- **WebSocket:** Mock Redis stream, verify events forwarded correctly.
- **Hooks:** Mock API responses, verify loading/error/data states. Test WS reconnection and polling fallback.

### End-to-End (manual)

1. Start py-engine + Redis + Postgres → verify `dashboard:*` KV keys populated.
2. Start frontend → verify pages load real data.
3. Send command via UI → verify py-engine receives and acts.
4. Kill py-engine → verify frontend shows stale indicators, reconnects when py-engine returns.
