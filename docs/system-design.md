# Icarus — System Design

**Version:** 4.2 · **Last Updated:** March 2026

---

## 1. Overview

Autonomous DeFi asset management bot. Strategies are defined in `STRATEGY.md` by a human — the system executes them autonomously.

The system has two AI integration points:

1. **Strategy authoring** — Human defines a strategy in `STRATEGY.md`, then uses Claude Code to generate the corresponding Python strategy class. The class drops into the `strategies/` directory and is auto-discovered at startup.
2. **Runtime** — Python collects market data, enriches it, and runs each strategy class to produce reports. When actionable signals are present, all reports, portfolio state, and risk context are assembled into a single prompt for Claude API. Claude makes trade and portfolio allocation decisions — it outputs execution orders, which are then verified before execution. Claude is not invoked for "do nothing" cycles (the decision gate handles that) or emergency actions (circuit breakers handle those independently).

| Metric       | Target      | Hard Limit                      |
| ------------ | ----------- | ------------------------------- |
| Max Drawdown | ≤15%        | 20% circuit breaker             |
| TX Success   | >98%        | >95%                            |
| Uptime       | 99.5%       | Graceful degradation on failure |
| Restart      | <60 seconds | <5 minutes                      |

The 15% drawdown target is communicated to Claude in the risk context section of each prompt. Claude can factor approaching-drawdown into its decisions. The 20% circuit breaker is the hard automated cutoff.

Yield targets and capital allocation are defined per-strategy in `STRATEGY.md`, not here.

---

## 2. Strategy System

Strategies are data (`STRATEGY.md`), implemented as loosely-coupled Python classes. The system is strategy-agnostic — it discovers and executes whatever strategy classes are registered.

### How strategies flow through the system

```
STRATEGY.md            Human defines strategy in markdown
      │
      ▼
Claude Code            Human uses Claude Code to generate Python class
      │
      ▼
strategies/            Class implements Strategy protocol, dropped into directory
      │
      ▼
Strategy Registry      Auto-discovered at startup, active by default
```

Strategies are either **active** (evaluated every cycle) or **inactive** (skipped). No intermediate states. Toggle via database (strategy status in PostgreSQL) or by removing the class file.

### Plug-and-play design

Adding a strategy:
1. Define it in `STRATEGY.md`
2. Use Claude Code to generate the Python class
3. Drop the class into `py-engine/strategies/`
4. Auto-discovered and active on next startup

Removing a strategy:
1. Delete the class file (or set to inactive)
2. Remove from `STRATEGY.md`

No wiring changes needed elsewhere — no import edits, no main loop changes, no config files.

### Strategy contract

Every strategy class implements the `Strategy` protocol:

- `strategy_id: str` — unique identifier matching `STRATEGY.md` (e.g. `LEND-001`)
- `eval_interval: timedelta` — how often `evaluate()` runs (e.g. `30s`, `5m`). Strategy-specific, not a system-level tier.
- `data_window: timedelta` — how far back the strategy needs data (e.g. latest snapshot, 1h rolling, 24h). The engine pre-slices cached data to this range before calling `evaluate()`.
- `evaluate(snapshot: MarketSnapshot) → StrategyReport` — analyze market data against strategy conditions, return a structured report

`MarketSnapshot` is a typed dataclass provided by the engine containing pre-sliced market data:

- `prices: list[TokenPrice]` — token prices with source and timestamp
- `gas: GasInfo` — current gas price, 24h average
- `pools: list[PoolState]` — protocol pool metrics (TVL, APY, utilization)
- `timestamp: datetime` — snapshot creation time

Strategies receive exactly the data they requested via `data_window` — they do not access the cache directly.

Strategy classes are **analysts, not actors**. They do not emit execution orders — they produce reports describing what they see and what they recommend. Each strategy controls its own evaluation cadence and data requirements.

### StrategyReport

Each `evaluate()` call returns a `StrategyReport` with:

| Field | Type | Description |
|-------|------|-------------|
| `strategy_id` | `str` | Which strategy produced this report |
| `timestamp` | `str` | ISO 8601 timestamp |
| `observations` | `list[Observation]` | What the strategy sees in the data (factual, no opinion) |
| `signals` | `list[Signal]` | Conditions met or approaching threshold |
| `recommendation` | `Recommendation | None` | What the strategy suggests, if anything |

**Observation** — factual data point: `{ metric, value, context }` (e.g., "Aave USDC supply APY is 4.2%, up from 3.1% yesterday")

**Signal** — a condition evaluation: `{ type, actionable, details }`. The strategy class sets `actionable` based on its own threshold logic — the decision gate simply checks the flag. Signal types:

- `entry_met` — entry condition satisfied (can be actionable)
- `exit_met` — exit condition satisfied (can be actionable)
- `harvest_ready` — harvest threshold crossed (can be actionable)
- `rebalance_needed` — rebalance condition triggered (can be actionable)
- `threshold_approaching` — condition approaching but not met (always `actionable: false`). Provides advance context for Claude's reasoning but does not open the decision gate on its own.

**Recommendation** — what the strategy suggests: `{ action, reasoning, parameters }`. This is advisory — Claude makes the final call.

The **decision gate** opens when any report contains at least one signal with `actionable: true`.

### What Claude API receives

A single structured prompt is assembled each cycle. The **system prompt** is durable — it defines Claude's role, the `execution-orders` output schema, and risk rules. The **user message** is assembled fresh each cycle with data sections in priority order:

1. **Objectives + strategy guidelines** — Portfolio goals, risk profile, per-strategy entry/exit conditions, constraints, and allocation limits (from `STRATEGY.md`)
2. **Portfolio state** — Current positions, allocations, P&L, available capital
3. **Risk context** — Circuit breaker states, exposure levels, recent TX results
4. **Strategy reports** — Output of each active strategy's `evaluate()` call (observations, signals, recommendations)
5. **Market data** — Prices, gas, protocol metrics, relevant on-chain events

Claude reasons over all of this and outputs schema-compliant execution orders. Token budget management is handled at the API layer, not in prompt assembly.

Strategy classes are auto-discovered from the `strategies/` directory at startup. Any module exporting a class that satisfies the `Strategy` protocol is registered.

### What STRATEGY.md controls

- Which protocols and chains to operate on
- Entry/exit conditions and thresholds
- Capital allocation limits (per-strategy and portfolio-level)
- Risk parameters (position sizing, reserve requirements)
- Harvest/compound logic

### What this document controls

- System architecture (how strategies are executed)
- Risk framework (circuit breakers, exposure limits structure)
- Infrastructure (Redis, wallet, listeners, encoders)
- Operational rules (decision loop, state management, failure modes)

Adding or changing a strategy means editing `STRATEGY.md`. The system, adapters, and risk framework are extended separately when new protocols or chains are needed.

---

## 3. Architecture

### Principles

- **Event-driven data ingestion** — Both services are fully async. The data pipeline reacts to Redis Stream events, caching every tick. Strategy evaluation runs on per-strategy schedules (`eval_interval`), reading from cached data. Circuit breakers run continuously on every event. Claude API calls and data enrichment all run as async tasks.
- **Separation of concerns** — Python owns all decisions. TypeScript owns all chain interactions. Neither crosses into the other's domain.
- **Strategies are loosely coupled** — Adding a strategy means editing `STRATEGY.md`, using Claude Code to generate the class, and dropping it in. No wiring changes needed.
- **Encode-only adapters** — Protocol modules are pure functions that produce calldata. No state, no side effects, no chain calls. The TX builder composes them.
- **Redis Streams everywhere** — All three channels (`market:events`, `execution:orders`, `execution:results`) use Redis Streams, not pub/sub. Streams provide persistent, replayable message delivery with consumer group support. Count-based pruning (`MAXLEN 10000`) bounds storage.
- **Schema-first communication** — All Redis messages validated against shared JSON schemas at the boundary. Invalid messages rejected loudly.
- **Verification gate is non-negotiable** — Every order from the Claude API decision pipeline passes through circuit breakers, exposure limits, and schema validation before execution. No bypass. Circuit breaker emergency orders follow their own validation path (schema validation only — see Section 6).
- **Circuit breakers are independent** — Circuit breakers run continuously and can emit emergency orders directly to Redis, bypassing the decision gate and Claude API entirely. These orders have their own validation path (see Section 6).
- **Nonce management** — Safe's built-in nonce tracking handles TX sequencing. No custom nonce manager needed.

### System diagram

```
  STRATEGY.md ──► Claude Code ──► Strategy class file
  (human-authored)  (human-triggered)  (dropped into strategies/)

┌────────────────────────────────────────────────────┐
│                  PYTHON LAYER                      │
│                                                    │
│  ┌─ CONTINUOUS (every event) ───────────────────┐  │
│  │  Data Pipeline    Circuit Breakers            │  │
│  │  (cache all ticks) (independent, can unwind)  │  │
│  │       │                                       │  │
│  │       ▼                                       │  │
│  │  Strategy Classes (async, produce reports)    │  │
│  └───────────────────────┬───────────────────────┘  │
│                          │                           │
│  ┌─ DECISION GATE ───────▼───────────────────────┐  │
│  │  Actionable signals? NO → stop (no API call)  │  │
│  │                      YES → continue           │  │
│  └───────────────────────┬───────────────────────┘  │
│                          │                           │
│  ┌─ CLAUDE API (gated) ──▼───────────────────────┐  │
│  │  Prompt Assembly → Claude API → Orders         │  │
│  │       │                                        │  │
│  │       ▼                                        │  │
│  │  Verification Gate (exposure, schema check)    │  │
│  └───────────────────────┬───────────────────────┘  │
│                          │                           │
│  Portfolio Tracker ── Lifecycle Manager               │
│  Monitoring ── State Recovery ── Database            │
└────────────────────────┬─────────────────────────────┘
                         │
                    ┌────▼──────────────────────────┐
                    │            REDIS              │
                    │  market:events     (TS → Py)  │
                    │  execution:orders  (Py → TS)  │
                    │  execution:results (TS → Py)  │
                    │  cache: prices, gas, pools    │
                    └────────────────────┬──────────┘
                                        │
┌───────────────────────────────────────▼────────────┐
│                TYPESCRIPT LAYER                    │
│                                                    │
│  Chain Listeners ── TX Builder ── Event Reporter   │
│  (Alchemy WS)      (viem)        (results)        │
│                                                    │
│  Safe Wallet ── Protocol Encoders (encode-only)    │
│  (1-of-2 multisig)                                 │
└────────────────────────────────────────────────────┘
```

### Event-driven decision pipeline

The Python engine runs on `asyncio`. The data pipeline and circuit breakers react to Redis Stream events continuously. Strategy evaluation runs on per-strategy schedules. Three layers run independently:

```
market:events (continuous, ~2/sec)
      │
      ├──► Data Pipeline (always runs — caches every tick)
      │
      ├──► Circuit Breakers ──── emergency? ──► execution:orders (direct to Redis)
      │    (always runs)
      │
      └──► Strategy Evaluation (per-strategy eval_interval)
                  │
                  ▼
            Decision Gate
            (any report has actionable: true signal?)
                  │
            NO: stop ── no API call
                  │
            YES: continue
                  │
                  ▼
            Prompt Assembly + Claude API
                  │
                  ▼
            Parse (validate against schema)
                  │
                  ▼
            Verification Gate → Execute → Update State
```

**Layer 1: Continuous processing** (runs on every event)

- **Data pipeline** — Prices, gas, protocol metrics cached and enriched on every tick. Feeds rolling averages, trend detection.
- **Circuit breakers** — Drawdown, gas spike, TX failure, TVL monitors run independently on every tick. Can trigger emergency actions directly — emitting unwind orders to `execution:orders` without passing through the decision gate or Claude API. This is a **separate path to Redis** for safety-critical actions only.
- **Strategy evaluation** — Each active strategy's `evaluate()` runs as a concurrent async task, producing a `StrategyReport`. Each strategy defines its own `eval_interval` (how often it runs) and `data_window` (what time range of cached data it receives). There is no system-level frequency tier — strategies control their own evaluation cadence based on their domain requirements.

**Layer 2: Decision gate** (filters before Claude API)

The gate inspects strategy reports for actionable signals. If no strategy has anything worth acting on, the cycle stops — no API call is made. Claude is not used to confirm "do nothing."

The gate opens when any strategy report contains at least one signal with `actionable: true`. When the gate opens, **all available reports** are included in the prompt assembly — even if some are from earlier evaluation cycles. Each report carries a timestamp so Claude can judge freshness. Strategies are not force-evaluated when another strategy triggers the gate.

Actionability is determined by each strategy class based on its own threshold logic. Only `entry_met`, `exit_met`, `harvest_ready`, and `rebalance_needed` signals can be actionable — `threshold_approaching` is always informational.

**Layer 3: Claude API decision** (only when gate opens)

1. **Assemble prompt** — Single structured prompt: system prompt (role, output schema, risk rules) + user message with data sections in priority order (objectives → portfolio → risk → reports → market data).
2. **Claude API** — Reasons over everything, outputs schema-compliant execution orders. Can act on multiple strategies in a single call.
3. **Parse** — A parser function validates Claude's response against the schema and extracts the orders. Malformed responses are rejected and logged.
4. **Verify** — Orders pass through the verification gate (exposure limits, circuit breaker state). Invalid or risky orders rejected.
5. **Execute** — Approved orders published to `execution:orders`
6. **Update** — Portfolio state updated, decision recorded

Claude makes trade and allocation decisions when action is needed. Strategy classes prepare and present the data — Claude reasons over it and decides what to do. "Do nothing" is handled by the decision gate; emergencies are handled by circuit breakers.

### Key decisions

| Decision        | Choice                             | Rationale                                      |
| --------------- | ---------------------------------- | ---------------------------------------------- |
| Decision Engine | Claude API (trade/allocation decisions) | Strategies report, Claude decides. Gate handles "do nothing", circuit breakers handle emergencies |
| Strategy Def    | `STRATEGY.md` → Claude Code → class | Human-readable specs, human-triggered generation |
| Wallet          | Safe 1-of-2 Multisig               | Battle-tested, agent EOA + human recovery      |
| State Recovery  | Count-based pruning on Redis Streams (`MAXLEN 10000`) | Bounded storage, replay unprocessed messages on startup |
| Adapter pattern | Encode-only pure functions         | Composable, testable, no hidden state          |
| Multi-step orders | Independent orders, next cycle resolves partial state | Simplest. Python owns sequencing decisions. No batch coordination in TS |
| Deployment      | Git push → Railway container rebuild | No hot-reload; new strategies require deploy. Rollback via Railway built-in |

---

## 4. Project Structure

```
icarus/
├── STRATEGY.md                    # Human-authored strategy definitions
│
├── ts-executor/                   # TypeScript service — chain interaction
│   └── src/
│       ├── listeners/             # Chain event listeners (WS, L2)
│       ├── execution/             # TX builder + encode-only protocol adapters
│       ├── wallet/                # Safe 1-of-2 multisig
│       ├── redis/                 # Redis client
│       ├── validation/            # Schema validation
│       └── index.ts
│
├── py-engine/                     # Python service — brain
│   ├── ai/                        # Claude API client, decision engine, insight synthesis
│   ├── data/                      # Market data ingestion & enrichment
│   ├── db/                        # PostgreSQL models, repository, migrations
│   ├── strategies/                # Strategy classes (auto-discovered, implement Strategy protocol)
│   ├── risk/                      # Circuit breakers & exposure limits
│   ├── portfolio/                 # Position tracker, capital allocator, rebalancer
│   ├── reporting/                 # PnL attribution
│   ├── harness/                   # State recovery, diagnostics, approval gates
│   ├── monitoring/                # Structured logging, dashboard
│   ├── validation/                # Schema validation
│   └── main.py                   # Main decision loop
│
├── shared/schemas/                # JSON schemas for Redis messages
├── docs/                          # System design docs
├── docker-compose.yml             # Redis + PostgreSQL + both services
└── harness/                       # Init, verify, features tracking
```

---

## 5. Tech Stack

| Component          | Technology                                 |
| ------------------ | ------------------------------------------ |
| Decision Engine    | Claude API (Anthropic)                     |
| Strategy Authoring | Claude Code (human-triggered from STRATEGY.md) |
| RPC Provider       | Alchemy (WebSockets + Enhanced APIs)       |
| Chain Interactions | viem (TypeScript)                          |
| Message Broker     | Redis 7+ (Streams on all channels, TTL keys for breaker state) |
| Data Processing    | Python — pandas, numpy                     |
| Database           | PostgreSQL (trade history, portfolio state, strategy state) |
| Deployment         | Docker Compose → Railway                   |
| Wallet             | Safe 1-of-2 Multisig (Safe{Core})          |
| Monitoring         | Structured JSON logs                       |

---

## 6. Risk Framework

Risk management is structural — it applies regardless of which strategies are active.

### Circuit breakers

| Trigger              | Threshold        | Action                                 |
| -------------------- | ---------------- | -------------------------------------- |
| Portfolio drawdown   | >20% from peak   | Halt all. Unwind to stables. Alert.    |
| Single-position loss | >10% of position | Close position. 24h cooldown.          |
| Gas spike            | >3x 24h average  | Pause non-urgent ops. Queue for later. |
| TX failure rate      | >3 failures/hour | Pause execution. Enter hold mode.      |
| Protocol TVL drop    | >30% in 24h      | Withdraw from affected protocol.       |

### Circuit breaker execution path

Circuit breakers operate on a **separate execution path** from Claude's decision pipeline. They do not pass through the decision gate or Claude API.

```
Circuit Breaker (continuous)
      │
      ├── Threshold NOT crossed → no action
      │
      └── Threshold crossed
              │
              ▼
      Emergency Order Generation
      (breaker produces orders directly)
              │
              ▼
      Schema Validation
      (orders validated against execution-orders schema)
              │
              ▼
      execution:orders (direct to Redis)
```

This is the only path to `execution:orders` that bypasses Claude. It exists because safety-critical actions (portfolio unwind on 20% drawdown, withdrawal on protocol TVL collapse) cannot wait for an API call. Circuit breaker orders are still schema-validated before publishing.

**Which breakers can emit directly:**

| Breaker | Direct emission | Behavior |
|---------|----------------|----------|
| Portfolio drawdown | Yes — unwind all to stables | Halt + close all positions |
| Single-position loss | Yes — close affected position | Close + 24h cooldown |
| Protocol TVL drop | Yes — withdraw from protocol | Withdraw affected positions |
| Gas spike | No — gates only | Pauses non-urgent operations, queues for later |
| TX failure rate | No — gates only | Pauses execution, enters hold mode |

Breakers that "gate only" do not produce orders — they block the normal pipeline from executing until the condition clears.

Circuit breaker orders use the `strategy` field with a `CB:` prefix (e.g. `CB:drawdown`, `CB:tvl_drop`) to identify their source. Claude-originated orders use the strategy ID directly (e.g. `LEND-001`).

**Cooldown tracking:** Circuit breaker cooldowns (e.g. 24h position cooldown) are stored as Redis TTL keys (e.g. `cooldown:LEND-001 EX 86400`). TTL handles automatic expiration — no cleanup logic needed. Survives restarts via Redis persistent volume.

### Exposure limits

Defined as environment variables, not hardcoded. The framework enforces:

- Per-protocol max allocation
- Per-asset max allocation
- Minimum liquid reserve requirement
- Contract allowlist enforced by Safe on-chain guard module — even if the application is compromised, the wallet cannot interact with non-allowlisted contracts

Specific limits (e.g. "max 70% in protocol X") are set via `STRATEGY.md` portfolio rules and env vars.

### Risk matrix

| Risk                   | Severity | Mitigation                                                 |
| ---------------------- | -------- | ---------------------------------------------------------- |
| Smart contract exploit | Critical | Allowlist, TVL monitoring, protocol diversification        |
| Oracle manipulation    | High     | Multi-source prices, reject >2% deviation                  |
| Key compromise         | Critical | Safe 1-of-2 multisig, spending caps, human recovery signer |
| AI hallucination       | High     | Verification gate validates all orders, schema enforcement |
| Chain halt / reorg     | Medium   | Finality-aware TX confirmation, state reconciliation       |

---

## 7. Agent Harness

### Startup sequence

1. Load portfolio state, positions, and strategy statuses from PostgreSQL
2. Check Redis Streams for unprocessed messages (replay any published after last acknowledged)
3. Query on-chain state to verify positions match database records
4. Reconcile discrepancies (trust on-chain state, update database to match)
5. Health check connected protocols
6. Resume normal operation, or log discrepancies and enter hold mode if reconciliation fails

### Operational rules

- **Stateless Claude prompts** — Each Claude API call is a fresh prompt with no conversation memory. The engine maintains an in-memory cache of portfolio state (loaded from PostgreSQL at startup, updated from `execution:results`), strategy reports, and market data. Claude never remembers previous decisions.
- Strategy status: active / inactive
- All messages validated against shared JSON schemas
- Verification gate is non-negotiable — Claude's orders are verified, not trusted blindly

### Hold mode

Triggered when Claude API is unavailable (down, timeout after retries, budget exhausted). Tracked as explicit state: `system_status: "normal" | "hold"` in Redis.

In hold mode:
- No new positions opened, no rebalances, no harvests
- Existing positions maintained as-is
- Strategy evaluation continues (reports stay fresh for when Claude returns)
- Circuit breakers remain fully active (independent of Claude)
- Decision gate stays closed regardless of actionable signals
- System exits hold mode automatically when Claude API responds / budget resets

### Monitoring

Structured JSON logs to stdout. Railway captures and aggregates service logs. Alerting (Slack/Discord webhook) is a v2 concern.

### State storage

PostgreSQL is the operational source of truth for application state:

| Data | Storage | Notes |
|------|---------|-------|
| Portfolio positions | PostgreSQL | Current holdings, entry prices, timestamps |
| Strategy statuses | PostgreSQL | Active / inactive per strategy |
| Trade history | PostgreSQL | All executed trades, results, P&L |
| Decision audit log | PostgreSQL | Claude prompts, responses, reasoning |
| Market data cache | Redis | Ephemeral, rebuilt on restart |
| Circuit breaker cooldowns | Redis (TTL keys) | Auto-expire, no cleanup needed |
| System status | Redis | `normal` / `hold` |
| Message replay | Redis Streams | Count-based pruning (`MAXLEN 10000`), replay unprocessed on startup |

On-chain state is the ultimate source of truth for positions — PostgreSQL maintains a cached view that the startup sequence reconciles against on-chain data, correcting any drift.

---

## 8. Failure Modes

| Failure | Behavior | Recovery |
|---------|----------|----------|
| **Claude API down / timeout** | Retry with exponential backoff (3 attempts). If exhausted, enter hold mode — no new positions, existing positions maintained. Alert logged. | Automatic resume when API responds |
| **Claude API budget exhausted** | Hold mode — same as API down. No new decisions until budget resets. | Automatic resume on budget reset (monthly) |
| **Redis disconnected** | Halt all decisions and execution. Listeners stay alive and buffer events. | Reconnect with backoff. Replay buffered events on reconnection |
| **Alchemy WebSocket drops** | Reconnect with backoff. Data pipeline stales during downtime. If no update received for >60 seconds, pause strategy evaluation. | Automatic resume on reconnection |
| **PostgreSQL down** | Decision loop continues using in-memory cached state (portfolio positions, strategy statuses loaded at startup and kept current from execution results). Trade recording and state updates queued in memory, flushed when DB recovers. If the system restarts while PostgreSQL is down, in-memory state is lost — on-chain reconciliation rebuilds position state, but audit log entries during the outage are lost. | Automatic flush on reconnection |
| **Stale price data** | If price feeds haven't updated beyond staleness threshold (configurable, default 60s), pause non-urgent operations. Critical operations (stop-loss, emergency unwind) still execute using last known prices. | Automatic resume when fresh data arrives |
| **Partial execution** | Multi-step operations (e.g. withdraw → swap → deposit) are emitted as independent orders. If an order fails midway through a sequence, remaining orders execute or fail independently. Partial state is recorded in the database. | Next decision cycle evaluates the partial state and decides how to proceed. No automatic retry of the sequence. |

| **Redis Stream overflow** | If the system is offline longer than the stream buffer (~80 minutes at ~2 events/sec), pruned messages are lost. | On-chain reconciliation at startup corrects position state. Decision audit log gaps during the outage are accepted. |

In all failure cases, the system degrades toward safety — holding existing positions is always safer than taking new action with incomplete information.

---

## 9. Deployment

### Infrastructure

- **Platform:** Railway
- **Deploy trigger:** Git push to main → container rebuild and deploy
- **Rollback:** Railway's built-in rollback to previous deployment
- **No hot-reload** — new strategies or code changes require a deploy

### Persistent storage

Redis and PostgreSQL use Railway persistent volumes. Data survives deploys and container restarts.

| Service | Persistence | Notes |
|---------|-------------|-------|
| PostgreSQL | Persistent volume | Trade history, portfolio state, strategy state |
| Redis | Persistent volume | Stream replay data, cache (rebuildable) |
| ts-executor | Stateless | All state in Redis/PostgreSQL |
| py-engine | Stateless | All state in Redis/PostgreSQL |

### Deploy sequence

1. Push to main branch
2. Railway rebuilds containers from Dockerfiles
3. New containers start → startup sequence runs (Section 7)
4. Reconciliation verifies state consistency
5. Normal operation resumes

Both services are stateless — all persistent state lives in PostgreSQL and Redis. A fresh container connects, reconciles, and resumes.

---

## 10. Extending the System

### Adding a new strategy

1. Edit `STRATEGY.md` — define name, ID, risk profile, protocols, chains, entry/exit conditions, constraints
2. Use Claude Code to generate the Python strategy class (implements `Strategy` protocol)
3. Drop the class file into `py-engine/strategies/`
4. Auto-discovered and active on next startup

No other files need editing — no imports, no config, no main loop changes.

### Adding a new protocol

Unlike strategies, protocols are not plug-and-play — they introduce new on-chain interaction patterns that the TX builder must know about. Adding a protocol requires wiring changes:

1. Add encode-only adapter in `ts-executor/src/execution/` (pure functions → calldata)
2. Wire adapter into `buildAdapterMap()` in `index.ts`
3. Add protocol to `shared/schemas/execution-orders.schema.json` enum
4. Update Safe guard module allowlist with the new protocol's contract addresses
5. Strategies in `STRATEGY.md` can now reference the new protocol

### Adding a new chain

1. Add chain listener in `ts-executor/src/listeners/`
2. Add chain to `py-engine/data/` pipeline modules (price feed, gas monitor, defi metrics)
3. Add chain to `shared/schemas/` enums
4. Strategies in `STRATEGY.md` can now target the new chain
