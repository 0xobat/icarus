# Icarus — PRD & Design Document

**Version:** 3.0 · **Last Updated:** March 2026

---

## 1. Overview

Autonomous DeFi asset management bot. Strategies are defined in `STRATEGY.md` by a human — the system reads them, generates executable code, and runs them autonomously.

The system has one active AI integration point, with a second planned:

1. **Runtime** — Python crunches market data into structured insights. Claude API reasons over those insights to produce trading decisions.
2. **Compile time (planned)** — Claude reads `STRATEGY.md` and generates Python strategy classes. Currently, strategy classes are written manually.

| Metric         | Target         | Hard Limit                      |
| -------------- | -------------- | ------------------------------- |
| Max Drawdown   | ≤15%           | 20% circuit breaker             |
| TX Success     | >98%           | >95%                            |
| Uptime         | 99.5%          | Graceful degradation on failure |
| Restart        | <60 seconds    | <5 minutes                      |

Yield targets and capital allocation are defined per-strategy in `STRATEGY.md`, not here.

---

## 2. Strategy System

Strategies are data, not code. The system is strategy-agnostic — it executes whatever `STRATEGY.md` defines.

### How strategies flow through the system

```
STRATEGY.md  →  Manual Python class    (v1 — human writes class per strategy)
                (evaluate, should_act,
                 generate_orders)
                      │
                Lifecycle Manager
                (evaluating → active → paused → retired)
```

> **Planned (not yet implemented):** Automated pipeline where Claude reads `STRATEGY.md`, parses via ingestion, and generates strategy classes. Currently, adding a strategy requires writing the Python class manually.

### Strategy contract

Every strategy class implements:

- `evaluate(markets)` — filter and rank opportunities from market data
- `should_act(context)` — decide whether to act given current state
- `generate_orders(markets, correlation_id)` — emit execution orders

### What STRATEGY.md controls

- Which protocols and chains to operate on
- Entry/exit conditions and thresholds
- Capital allocation limits (per-strategy and portfolio-level)
- Risk parameters (position sizing, reserve requirements)
- Harvest/compound logic

### What the PRD controls

- System architecture (how strategies are executed)
- Risk framework (circuit breakers, exposure limits structure)
- Infrastructure (Redis, wallet, listeners, encoders)
- Operational rules (decision loop, state management, human-in-the-loop)

Adding or changing a strategy means editing `STRATEGY.md`. The system, adapters, and risk framework are extended separately when new protocols or chains are needed.

---

## 3. Architecture

### Principles

- **Separation of concerns** — Python owns all decisions. TypeScript owns all chain interactions. Neither crosses into the other's domain.
- **Strategies are data** — Adding a strategy means editing a markdown file, not writing code. Claude generates the implementation.
- **Encode-only adapters** — Protocol modules are pure functions that produce calldata. No state, no side effects, no chain calls. The TX builder composes them.
- **Schema-first communication** — All Redis messages validated against shared JSON schemas at the boundary. Invalid messages rejected loudly.
- **Risk gate is non-negotiable** — Every decision passes through circuit breakers before execution. No bypass.

### System diagram

```
                        STRATEGY.md
                            │
                      ┌─────▼─────┐
                      │  Claude   │  (compile time)
                      │  code-gen │
                      └─────┬─────┘
                            │  generates
                            ▼
┌────────────────────────────────────────────────────┐
│                  PYTHON LAYER                      │
│                                                    │
│  Data Pipeline ──► Insight Synthesis ──► Claude API│
│  (prices, gas,     (pandas, numpy)      (runtime   │
│   protocol metrics)                      reasoning)│
│                                              │     │
│  Strategy Classes ◄──────────────────────────┘     │
│  (generated from STRATEGY.md)                      │
│         │                                          │
│         ▼                                          │
│  Risk Gate ──► Order Emitter                       │
│  (circuit breakers,                                │
│   exposure limits)                                 │
│                                                    │
│  Portfolio Tracker ── Lifecycle Manager             │
│  Monitoring ── State Recovery                      │
└────────────────────────┬───────────────────────────┘
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
│  (Alchemy WS)      (viem)        (results)         │
│                                                    │
│  Safe Wallet ── Protocol Encoders (encode-only)    │
│  (1-of-2 multisig)                                 │
└────────────────────────────────────────────────────┘
```

### Decision loop

Each cycle:

1. **Ingest** — TS publishes chain events to `market:events`
2. **Enrich** — Python crunches raw data into structured insights
3. **Reason** — Simple thresholds handled deterministically. Claude API invoked for ambiguous conditions or multi-strategy decisions.
4. **Risk gate** — Decisions pass through circuit breakers and exposure limits
5. **Execute** — Approved decisions become `execution:orders` sent to TS
6. **Report** — TS publishes results to `execution:results`, Python updates portfolio state

### Key decisions

| Decision        | Choice                             | Rationale                                      |
| --------------- | ---------------------------------- | ---------------------------------------------- |
| Decision Engine | Claude API                         | AI reasoning, not hardcoded rules              |
| Strategy Def    | `STRATEGY.md` → generated code     | Human-readable specs, Claude generates classes |
| Wallet          | Safe 1-of-2 Multisig               | Battle-tested, agent EOA + human recovery      |
| State Recovery  | TTL-based pruning on Redis Streams | Bounded storage, crash recovery replay window  |
| Adapter pattern | Encode-only pure functions         | Composable, testable, no hidden state          |
| Deployment      | Rolling release via Railway         | Always running, hot-reload on strategy update  |

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
│   ├── strategies/                # Strategy classes (manually written per STRATEGY.md)
│   ├── risk/                      # Circuit breakers & exposure limits
│   ├── portfolio/                 # Position tracker, capital allocator
│   ├── harness/                   # State recovery, diagnostics
│   ├── monitoring/                # Structured logging
│   └── main.py                   # Main decision loop
│
├── shared/schemas/                # JSON schemas for Redis messages
├── docs/                          # PRD, design docs
├── docker-compose.yml             # Redis + PostgreSQL + both services
└── harness/                       # Init, verify, features tracking
```

---

## 5. Tech Stack

| Component         | Technology                           |
| ----------------- | ------------------------------------ |
| Decision Engine   | Claude API (Anthropic)               |
| Strategy Code-Gen | Manual (planned: Claude API + STRATEGY.md) |
| RPC Provider      | Alchemy (WebSockets + Enhanced APIs) |
| Chain Interactions| viem (TypeScript)                    |
| Message Broker    | Redis 7+ (pub/sub + Streams)         |
| Data Processing   | Python — pandas, numpy               |
| Database          | PostgreSQL (trade history)           |
| Deployment        | Docker Compose → Railway             |
| Wallet            | Safe 1-of-2 Multisig (Safe{Core})    |
| Monitoring        | Structured JSON logs                 |

---

## 6. Risk Framework

Risk management is structural — it applies regardless of which strategies are active.

### Circuit breakers

| Trigger              | Threshold        | Action                                |
| -------------------- | ---------------- | ------------------------------------- |
| Portfolio drawdown   | >20% from peak   | Halt all. Unwind to stables. Alert.   |
| Single-position loss | >10% of position | Close position. 24h cooldown.         |
| Gas spike            | >3x 24h average  | Pause non-urgent ops. Queue for later.|
| TX failure rate      | >3 failures/hour | Pause execution. Diagnostic mode.     |
| Protocol TVL drop    | >30% in 24h      | Withdraw from affected protocol.      |

### Exposure limits

Defined as environment variables, not hardcoded. The framework enforces:

- Per-protocol max allocation
- Per-asset max allocation
- Minimum liquid reserve requirement
- Contract allowlist at wallet level

Specific limits (e.g. "max 70% in protocol X") are set via `STRATEGY.md` portfolio rules and env vars.

### Risk matrix

| Risk                   | Severity | Mitigation                                                  |
| ---------------------- | -------- | ----------------------------------------------------------- |
| Smart contract exploit | Critical | Allowlist, TVL monitoring, protocol diversification         |
| Oracle manipulation    | High     | Multi-source prices, reject >2% deviation                   |
| Key compromise         | Critical | Safe 1-of-2 multisig, spending caps, human recovery signer |
| AI hallucination       | High     | Risk gate validates all decisions, schema enforcement       |
| Chain halt / reorg     | Medium   | Finality-aware TX confirmation, state reconciliation        |

---

## 7. Agent Harness

### Startup sequence

1. Read `agent-state.json` to restore portfolio knowledge
2. Check Redis Streams for unprocessed execution orders
3. Query on-chain state to verify positions match records
4. Reconcile discrepancies
5. Health check connected protocols
6. Resume normal operation or enter diagnostic mode

### Operational rules

- One strategy adjustment per cycle
- Clean state after every action
- Strategy status tracking: active / paused / evaluating / retired
- All messages validated against shared JSON schemas
- Risk gate is non-negotiable

### Human-in-the-loop

- Trades >15% of portfolio require confirmation
- Emergency override: pause all, force-unwind, withdraw (notification channel TBD)
- Strategy updates in `STRATEGY.md` require corresponding Python class update; new strategies enter as "evaluating"

---

## 8. Extending the System

### Adding a new strategy

1. Edit `STRATEGY.md` — define name, tier, protocols, chains, entry/exit conditions, constraints
2. Write Python strategy class implementing `evaluate()`, `should_act()`, `generate_orders()`
3. Register class with lifecycle manager; enters as "evaluating"
4. After validation, transitions to "active"

### Adding a new protocol

1. Add encode-only adapter in `ts-executor/src/execution/` (pure functions → calldata)
2. Wire adapter into `buildAdapterMap()` in `index.ts`
3. Add protocol to `shared/schemas/execution-orders.schema.json` enum
4. Strategies in `STRATEGY.md` can now reference the new protocol

### Adding a new chain

1. Add chain listener in `ts-executor/src/listeners/`
2. Add chain to `py-engine/data/` pipeline modules (price feed, gas monitor, defi metrics)
3. Add chain to `shared/schemas/` enums
4. Strategies in `STRATEGY.md` can now target the new chain
