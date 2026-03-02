# Icarus — PRD & Design Document

**Version:** 2.0 · **Last Updated:** February 2026

---

## 1. Overview

Autonomous multi-strategy DeFi bot. Strategies are defined in `strategy.md` by a human. Claude synthesizes those definitions into executable Python strategy classes and acts as the runtime decision engine. TypeScript handles all blockchain interaction. Communication via Redis.

The system has two AI integration points:

1. **Compile time** — Claude reads `strategy.md` and generates Python strategy classes. When strategies are updated, classes are regenerated and deployed via rolling release.
2. **Runtime** — Python crunches market data into structured insights. Claude API reasons over those insights + active strategy specs to produce trading decisions. Not hardcoded if/else — actual AI reasoning.

| Metric              | Target          | Hard Limit                      |
| ------------------- | --------------- | ------------------------------- |
| Annual Return (APY) | 20–50%          | Risk-adjusted, not nominal      |
| Max Drawdown        | ≤15% target     | 20% circuit breaker             |
| Sharpe Ratio        | >2.0            | Minimum 1.5                     |
| Uptime              | 99.5%           | Graceful degradation on failure |
| Budget              | $250–$1,000 CAD | Infrastructure + tooling        |

---

## 2. Strategies

### Definition

Strategies are authored in `strategy.md` — a human-readable file that defines:

- Strategy name, tier, and risk profile
- Target protocols and chains
- Entry/exit conditions (qualitative or quantitative)
- Capital allocation constraints
- Any special logic (e.g. compound frequency, range width)

Claude reads this file and generates a Python class per strategy. Each class follows a common interface: `evaluate()`, `should_act()`, `generate_orders()`. Generated classes live in `py-engine/strategies/` and are deployed via rolling release.

### Strategy Tiers

**Tier 1 — Low Risk (50–60% of capital)**

- Lending optimization: Aave supply rotation based on utilization rates
- Liquid staking: ETH → stETH via Lido, deploy derivatives into further yield

**Tier 2 — Medium Risk (25–35% of capital)**

- Concentrated liquidity on Uniswap V3 with dynamic range management
- Yield farming with auto-harvest and compounding

**Tier 3 — Higher Risk (10–20% of capital)**

- Flash loan arbitrage (atomic cross-DEX, zero-capital)
- Rate arbitrage across lending protocols

### Chain Support

| Chain                | Protocols                  | Environment     |
| -------------------- | -------------------------- | --------------- |
| Ethereum Mainnet     | Aave, Uniswap V3, Lido    | Sepolia testnet |
| L2s (Arbitrum, Base) | Aave, GMX, Aerodrome      | L2 testnets     |

---

## 3. Architecture

### Design Principles

- Python owns all decisions. TypeScript owns all chain interactions. Neither crosses into the other's domain.
- Claude is the decision engine. Python synthesizes data and translates decisions into orders.
- Strategies are data (`strategy.md`), not hardcoded logic. Adding a strategy means editing a markdown file, not writing a Python class.

```
                        strategy.md
                            │
                      ┌─────▼─────┐
                      │  Claude    │  (compile time)
                      │  code-gen  │
                      └─────┬─────┘
                            │  generates
                            ▼
┌──────────────────────────────────────────────────┐
│                  PYTHON LAYER                     │
│                                                   │
│  Data Pipeline ──► Insight Synthesis ──► Claude API│
│  (prices, gas,     (pandas, numpy)      (runtime  │
│   protocol metrics)                      reasoning)│
│                                              │     │
│  Generated Strategy Classes ◄────────────────┘     │
│  (from strategy.md)                                │
│         │                                          │
│         ▼                                          │
│  Risk Gate ──► Order Emitter                       │
│  (circuit breakers,                                │
│   exposure limits)                                 │
│                                                    │
│  Portfolio Tracker ── Lifecycle Manager             │
│  Monitoring ── State Recovery                      │
└────────────────────────┬─────────────────────────┘
                         │
                    ┌────▼─────────────────────────┐
                    │            REDIS              │
                    │  market:events     (TS → Py)  │
                    │  execution:orders  (Py → TS)  │
                    │  execution:results (TS → Py)  │
                    │  cache: prices, gas, pools    │
                    └────────────────────┬──────────┘
                                        │
┌───────────────────────────────────────▼──────────┐
│                TYPESCRIPT LAYER                    │
│                                                    │
│  Chain Listener ── TX Builder ── Event Reporter    │
│  (Alchemy WS)     (viem)        (results)         │
│                                                    │
│  Safe Wallet ── Protocol Encoders                  │
│  (1-of-2 multisig) (Aave V3, Lido)               │
└────────────────────────────────────────────────────┘
```

### Decision Loop

Each cycle:

1. **Ingest** — TS publishes chain events to `market:events`
2. **Enrich** — Python pipeline crunches raw data into structured insights (rates, spreads, utilization, anomalies)
3. **Reason** — Insights + active strategy specs sent to Claude API. Claude returns structured decisions (hold, enter, exit, rotate, adjust).
4. **Risk gate** — Decisions pass through circuit breakers and exposure limits
5. **Execute** — Approved decisions become `execution:orders` sent to TS
6. **Report** — TS publishes results to `execution:results`, Python updates portfolio state

For simple, well-defined situations (e.g. "APY on market A is higher than market B by X%"), the generated strategy class can make the decision deterministically without calling Claude API. Claude API is invoked when the situation requires reasoning — multiple competing signals, ambiguous conditions, or complex multi-step rebalancing.

### Key Decisions

| Decision       | Choice                                     | Rationale                                                        |
| -------------- | ------------------------------------------ | ---------------------------------------------------------------- |
| Decision Engine| Claude API                                 | AI reasoning over synthesized data, not hardcoded rules          |
| Strategy Def   | Markdown (`strategy.md`) → generated code  | Human-readable specs, Claude generates implementations           |
| Wallet         | Safe 1-of-2 Multisig (Safe{Core} SDK)      | ethskills: battle-tested ($100B+ secured), agent EOA + human recovery |
| State Recovery | TTL-based pruning on Redis Streams         | Bounded storage, sufficient replay window for crash recovery     |
| MEV Protection | Flashbots Protect RPC (P1b)                | Private mempool routing for swaps; not needed for P1a supply/withdraw |
| Deployment     | Rolling release via Railway                | Always running, strategies hot-reloaded on update                |
| Testnet First  | Sepolia until validated                    | Validate all strategies before real capital                      |

---

## 4. Project Structure

```
icarus/
├── strategy.md                    # Human-authored strategy definitions
│
├── ts-executor/                   # TypeScript service — chain interaction
│   └── src/
│       ├── listeners/             # Alchemy WebSocket handlers
│       ├── execution/             # TX builder, protocol encoders (Aave, Lido)
│       ├── wallet/                # Safe 1-of-2 multisig
│       ├── redis/                 # Redis client
│       ├── validation/            # Schema validation
│       └── index.ts
│
├── py-engine/                     # Python service — brain
│   ├── ai/                        # Claude API client, decision engine, code-gen
│   ├── data/                      # Market data ingestion & enrichment
│   ├── strategies/                # Generated strategy classes (from strategy.md)
│   ├── risk/                      # Circuit breakers & exposure limits
│   ├── portfolio/                 # Position tracker, capital allocator
│   ├── harness/                   # State recovery, diagnostics
│   ├── monitoring/                # Structured logging
│   └── main.py                   # Main decision loop
│
├── shared/schemas/                # JSON schemas for Redis messages
├── docs/                          # PRD, architecture docs
├── docker-compose.yml             # Redis + both services
└── harness/                       # Init, verify, features tracking
```

---

## 5. Tech Stack

| Component              | Technology                                 |
| ---------------------- | ------------------------------------------ |
| Decision Engine        | Claude API (Anthropic)                     |
| Strategy Code-Gen      | Claude API + strategy.md                   |
| RPC Provider           | Alchemy (WebSockets + Enhanced APIs)       |
| ETH Interactions       | viem (TypeScript)                          |
| Message Broker / Cache | Redis 7+ (pub/sub + Streams)               |
| Data Processing        | Python — pandas, numpy                     |
| Database               | PostgreSQL (trade history, audit trail)    |
| Deployment             | Docker Compose → Railway                   |
| Wallet                 | Safe 1-of-2 Multisig (Safe{Core} SDK)      |
| MEV Protection         | Flashbots Protect RPC (P1b+)               |
| Monitoring             | Structured JSON logs + Discord alerts      |

---

## 6. Risk Management

### Circuit Breakers

| Trigger              | Threshold        | Action                                          |
| -------------------- | ---------------- | ----------------------------------------------- |
| Portfolio drawdown   | >20% from peak   | Halt all positions. Unwind to stables. Alert.   |
| Single-position loss | >10% of position | Close position. 24h cooldown for that strategy. |
| Gas spike            | >3x 24h average  | Pause non-urgent ops. Queue for later.          |
| TX failure rate      | >3 failures/hour | Pause execution. Diagnostic mode. Alert.        |
| Protocol TVL drop    | >30% in 24h      | Withdraw all capital from affected protocol.    |

### Exposure Limits

- Max 40% in any single protocol
- Max 60% in any single asset (excluding stablecoins)
- Min 15% in stablecoins/liquid reserves at all times
- Smart contract allowlist enforced at TS executor level
- Flashbots Protect for all swap transactions

### Risk Matrix

| Risk                   | Severity | Mitigation                                           |
| ---------------------- | -------- | ---------------------------------------------------- |
| Smart contract exploit | Critical | Allowlist, TVL monitoring, protocol diversification  |
| Oracle manipulation    | High     | Multi-source prices, reject >2% deviation, TWAP      |
| Liquidity shock        | High     | Pre-trade depth checks, position sizing to liquidity |
| Key compromise         | Critical | Smart wallet spending caps, hot/cold split           |
| Chain halt / reorg     | Medium   | Finality-aware TX confirmation, state reconciliation |
| Strategy crowding      | Medium   | Yield compression monitoring, automatic rotation     |
| AI hallucination       | High     | Risk gate validates all AI decisions, schema enforcement, position size limits |

---

## 7. Agent Harness

Patterns from [Anthropic's long-running agent research](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents).

### Startup Sequence (every restart)

1. Read `agent-state.json` to restore portfolio knowledge
2. Check Redis Streams for unprocessed execution orders
3. Query on-chain state via Alchemy to verify positions match records
4. Reconcile discrepancies (e.g., TX confirmed while offline)
5. Run health checks on all connected protocols
6. Resume normal operation or enter diagnostic mode

### Operational Rules

- **One strategy adjustment per cycle** — never rebalance everything at once
- **Clean state after every action** — updated state file, logs, and monitoring before next op
- **Strategy status tracking** — structured JSON with status (active / paused / evaluating / retired)
- **All messages validated** against shared JSON schemas; violations rejected loudly
- **Risk gate is non-negotiable** — Claude's decisions always pass through circuit breakers before execution

### Human-in-the-Loop

- New protocol deployment requires owner approval
- Trades >15% of portfolio require confirmation
- New strategy tier activation requires explicit approval
- Emergency override via Discord: pause all, force-unwind, withdraw
- Strategy updates in `strategy.md` trigger regeneration, enter as "evaluating"

### Rolling Release

The bot is always running. Updates flow as:

1. Human edits `strategy.md`
2. Claude regenerates affected strategy classes
3. New classes deploy, lifecycle manager picks them up as "evaluating"
4. After validation period, strategies transition to "active"
5. Old strategy versions gracefully retire

---

## 8. Event Flow

How a lending rotation flows through the system:

1. **TS Listener** detects Aave rate change via Alchemy WebSocket → publishes to `market:events`
2. **Python Data Pipeline** enriches with cached prices, gas costs, protocol metrics
3. **Python Insight Synthesis** packages structured snapshot: current positions, market state, active strategies, risk status
4. **Claude API** receives insight data + strategy specs, reasons about optimal action, returns structured decision
5. **Python Risk Gate** validates decision against circuit breakers, exposure limits, contract allowlist
6. **Python** publishes approved order to `execution:orders` (token, amount, slippage, gas ceiling, deadline)
7. **TS Executor** constructs TX via viem, routes through Flashbots Protect, submits
8. **TS Reporter** publishes result to `execution:results` (hash, status, fill price, gas)
9. **Python** updates portfolio state, logs performance, feeds result back into next cycle's insights

---

## 9. Success Criteria

| Metric           | Target      | Minimum    |
| ---------------- | ----------- | ---------- |
| APY (30-day)     | 35%         | 20%        |
| Sharpe Ratio     | >2.0        | >1.5       |
| Max Drawdown     | <15%        | <20%       |
| TX Success Rate  | >98%        | >95%       |
| Uptime           | >99%        | >95%       |
| Restart Recovery | <60 seconds | <5 minutes |

The project is successful when the agent consistently generates returns above the S&P 500 benchmark with controlled drawdowns, running autonomously on Railway with Discord alerts and human-in-the-loop controls for high-stakes decisions.

---

## 10. Phases

### P1 — Build Full System

All infrastructure, strategies, AI decision engine, risk management, monitoring, and L2 support for Ethereum + Arbitrum + Base. The bot is feature-complete and running on Sepolia testnet.

Includes: infrastructure (Redis, PostgreSQL, Docker, main loop), all chain listeners (Ethereum + L2), all protocol adapters (Aave, Uniswap V3, Lido, GMX, Aerodrome, flash loans), all 6 strategies, Claude AI engine (runtime reasoning + code-gen + insight synthesis), full risk management suite, portfolio rebalancing, Discord alerts, performance dashboard, anomaly detection, human-in-the-loop gates, tax/P&L reporting, and ML gas prediction.

### P2 — Historical Stress Testing

Replay historical market crises (March 2020 COVID crash, May 2021 crypto crash, November 2022 FTX collapse) through the system to validate circuit breakers hold and the bot survives extreme conditions.

### P3 — Solana Chain Support

Extend listeners, executors, and data pipeline to Solana's account-based model (Marinade, Raydium, Jupiter).

### P4 — Production Deployment

Deploy to Railway with managed Redis, secrets management, auto-deploy from main, health checks, and cost monitoring within budget.
