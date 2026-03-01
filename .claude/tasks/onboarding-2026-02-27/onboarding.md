# Icarus Onboarding — 2026-02-27

## What Is Icarus?

An **autonomous multi-strategy DeFi trading bot** with a dual-service architecture:

- **ts-executor/** (TypeScript) — all blockchain interactions: chain listeners, transaction execution, protocol adapters
- **py-engine/** (Python) — all decision-making: data pipeline, insight synthesis, Claude API reasoning, risk management, portfolio tracking
- **shared/schemas/** — JSON schemas defining the 3 Redis message channels between services
- Communication via **Redis** (pub/sub + streams + cache)

**Key design principle:** Python owns decisions, TypeScript owns chain interactions. Neither crosses into the other's domain.

### The Claude AI Integration (Two Levels)

1. **Compile time** — Claude reads `strategy.md` and generates Python strategy classes (AI-002, not yet implemented)
2. **Runtime** — Python packages market data into insight snapshots, Claude API reasons over them to produce trading decisions (AI-001, implemented)

Strategies are data (`strategy.md`), not hardcoded logic.

---

## Architecture Diagram

```
strategy.md → [Claude code-gen] → py-engine/strategies/*.py

TS-executor                     py-engine
┌────────────────┐              ┌────────────────────┐
│ Alchemy WS     │──market:     │ Data pipeline      │
│ L2 Listeners   │  events──►   │ (prices, gas, DeFi)│
│                │              │                    │
│ TX Builder     │◄─execution:  │ Insight Synthesis  │
│ Protocol       │  orders────  │ Claude Decision    │
│ Adapters       │              │ Engine             │
│                │──execution:  │ Risk Gate          │
│ Event Reporter │  results──►  │ Portfolio Tracker  │
└────────────────┘              └────────────────────┘
         │                              │
      Redis (pub/sub + streams + cache)
```

### Redis Channels

| Channel              | Direction    | Schema                                   |
| -------------------- | ------------ | ---------------------------------------- |
| `market:events`      | TS → Python  | `shared/schemas/market-events.schema.json`     |
| `execution:orders`   | Python → TS  | `shared/schemas/execution-orders.schema.json`  |
| `execution:results`  | TS → Python  | `shared/schemas/execution-results.schema.json` |

---

## Project Status: 40/62 features passing

### Phase Breakdown

- **P1** = build full system (57 features — 40 passing, 17 remaining)
- **P2** = historical stress testing (1 feature)
- **P3** = Solana chain support (3 features)
- **P4** = Railway production deployment (1 feature)

### What's Implemented (40 features)

**Infrastructure (6):** project scaffolding, Redis communication, JSON schema contracts, Docker Compose, PostgreSQL database layer (SQLAlchemy + SQLite dev/PG prod)

**Chain Listeners (3):** Alchemy WebSocket manager, market event publisher, L2 listeners (Arbitrum/Base)

**Execution (8):** viem TX builder, Smart Wallet (ERC-4337), Flashbots Protect, Aave V3 adapter, Uniswap V3 adapter, Lido adapter, flash loan executor, GMX/Aerodrome L2 adapters, event reporter

**Data Pipeline (4):** price feed, gas monitor, DeFi protocol metrics, on-chain position reconciliation

**AI Engine (2):** Claude API decision engine (AI-001), insight synthesis pipeline (AI-003)

**Strategies (3):** Aave lending optimization, strategy lifecycle manager, strategy ingestion (parses strategy.md)

**Portfolio (2):** allocator, position tracker

**Harness (3):** state persistence, startup recovery, diagnostic mode

**Risk (6):** drawdown circuit breaker, per-position loss limit, gas spike breaker, TX failure monitor, contract allowlist, oracle guard, exposure limits

**Testing (1):** Sepolia integration suite (TEST-001)

**Monitoring (1):** structured logging

### What's Remaining (22 features)

**P1 High Priority:**
- `INFRA-007` — Main decision loop (wires all modules together — **critical integration feature**)
- `AI-002` — Strategy code-gen pipeline (Claude generates Python classes from strategy.md)
- `STRAT-002` — Liquid staking strategy (Lido)
- `STRAT-003` — Uniswap V3 concentrated liquidity
- `STRAT-004` — Yield farming with auto-compound
- `STRAT-005` — Flash loan arbitrage strategy
- `STRAT-006` — Lending rate arbitrage strategy
- `PORT-003` — Rebalancing engine
- `HARNESS-003` — Human-in-the-loop approval gates
- `DATA-005` — L2 data pipeline extension
- `MON-002` — Discord alert system
- `MON-003` — Performance dashboard
- `MON-004` — Anomaly detection
- `RISK-005` — Protocol TVL monitor
- `REPORT-001` — Tax reporting engine
- `REPORT-002` — P&L attribution report
- `TEST-003` — ML gas prediction model

**P2:** TEST-002 (historical stress testing)
**P3:** LISTEN-004, EXEC-008, DATA-006 (Solana support)
**P4:** INFRA-005 (Railway deployment)

---

## Last Session (most recent — unlogged in progress.txt)

The most recent session implemented **8 new features** across 3 parallel agents and merged them to `dev`:

1. **INFRA-006** — PostgreSQL database layer (`py-engine/db/`) using SQLAlchemy async ORM with SQLite for dev, PG-ready for production. Models: Trade, PortfolioSnapshot, RiskEvent, SystemState. Repository pattern with full CRUD + query filters. Migration system. 812 tests.

2. **AI-001** — Claude API decision engine (`py-engine/ai/decision_engine.py`). Structured prompt construction, retry logic, rate limiting, cost tracking, deterministic fallback when Claude unavailable. Actions: hold/enter/exit/rotate/adjust.

3. **AI-003** — Insight synthesis pipeline (`py-engine/ai/insight_synthesis.py`). Packages market data from all sources into compressed snapshots for Claude API. Validates snapshot schema, maintains decision history for context continuity.

4. **STRAT-008** — Strategy ingestion (`py-engine/strategies/ingestion.py`). Parses strategy.md markdown into structured StrategySpec dataclasses. Change detection via content hashing. Flags modified strategies for re-generation.

5. **LISTEN-003** — L2 chain listeners (`ts-executor/src/listeners/l2-listener.ts`). Arbitrum + Base WebSocket connections via viem. GMX event parsing on Arbitrum, Aerodrome on Base. Per-chain enable/disable.

6. **EXEC-005** — Uniswap V3 protocol adapter (mint/burn/collect/swap)
7. **EXEC-006** — Lido staking adapter (stake/wrap/unwrap/query)
8. **EXEC-007** — Flash loan executor (atomic multi-step arbitrage)
9. **EXEC-009** — L2 protocol adapters: GMX (Arbitrum) + Aerodrome (Base)

### Commits on dev HEAD (b8ae84b):
```
b8ae84b merge(icarus): LISTEN-003, EXEC-005, EXEC-006, EXEC-007, EXEC-009 from ts-dev
4edbe61 merge(icarus): AI-001, AI-003, STRAT-008 from py-ai
6f27f68 merge(icarus): INFRA-006 PostgreSQL database layer from py-infra
```

---

## Key File Locations

### TypeScript (ts-executor/)
| Module | File | Purpose |
|--------|------|---------|
| Entry | `src/index.ts` | Service bootstrap (still stub — needs INFRA-007) |
| WebSocket | `src/listeners/websocket-manager.ts` | Alchemy WS with reconnect |
| Event Normalizer | `src/listeners/event-normalizer.ts` | Raw → market-events schema |
| Market Publisher | `src/listeners/market-event-publisher.ts` | Publish to Redis |
| L2 Listener | `src/listeners/l2-listener.ts` | Arbitrum + Base |
| TX Builder | `src/execution/transaction-builder.ts` | viem TX construction + signing |
| Smart Wallet | `src/wallet/smart-wallet.ts` | ERC-4337 with spending limits |
| Flashbots | `src/execution/flashbots-protect.ts` | MEV protection |
| Protocol Adapters | `src/execution/{aave,uniswap,lido,gmx,aerodrome}-*.ts` | Protocol-specific TX encoding |
| Flash Loans | `src/execution/flash-loan-executor.ts` | Atomic arbitrage |
| Event Reporter | `src/execution/event-reporter.ts` | TX result publishing |
| Contract Allowlist | `src/security/contract-allowlist.ts` | Address whitelist |
| Redis | `src/redis/client.ts` | Redis manager with schema validation |
| Schema Validator | `src/validation/schema-validator.ts` | ajv-based validation |

### Python (py-engine/)
| Module | File | Purpose |
|--------|------|---------|
| Entry | `main.py` | Service bootstrap (still stub — needs INFRA-007) |
| Decision Engine | `ai/decision_engine.py` | Claude API reasoning |
| Insight Synthesis | `ai/insight_synthesis.py` | Data → Claude-ready snapshots |
| Strategy Ingestion | `strategies/ingestion.py` | Parse strategy.md |
| Aave Strategy | `strategies/aave_lending.py` | Tier 1 lending optimization |
| Lifecycle Manager | `strategies/lifecycle_manager.py` | Strategy state management |
| Price Feed | `data/price_feed.py` | Multi-source token prices |
| Gas Monitor | `data/gas_monitor.py` | Gas price tracking |
| DeFi Metrics | `data/defi_metrics.py` | Protocol metrics collection |
| Reconciliation | `data/reconciliation.py` | On-chain position verification |
| Redis Client | `data/redis_client.py` | Redis manager (Python side) |
| Database | `db/database.py` | SQLAlchemy async engine |
| Models | `db/models.py` | Trade, PortfolioSnapshot, RiskEvent, SystemState |
| Repository | `db/repository.py` | CRUD operations |
| Migrations | `db/migrations.py` | Schema migration system |
| Allocator | `portfolio/allocator.py` | Capital allocation across tiers |
| Position Tracker | `portfolio/position_tracker.py` | Open positions + P&L |
| Drawdown | `risk/drawdown_breaker.py` | Portfolio drawdown circuit breaker |
| Position Loss | `risk/position_loss_limit.py` | Per-position loss limit |
| Gas Spike | `risk/gas_spike_breaker.py` | Gas price circuit breaker |
| TX Failure | `risk/tx_failure_monitor.py` | Failure rate monitoring |
| Oracle Guard | `risk/oracle_guard.py` | Multi-source price validation |
| Exposure Limits | `risk/exposure_limits.py` | Concentration limits |
| State Manager | `harness/state_manager.py` | Persistent state file |
| Startup Recovery | `harness/startup_recovery.py` | Deterministic recovery |
| Diagnostic Mode | `harness/diagnostic_mode.py` | Safe mode on failures |
| Logger | `monitoring/logger.py` | Structured JSON logging |

---

## Development Environment

- **TS:** pnpm, TypeScript strict, vitest for testing, eslint-plugin-jsdoc
- **Python:** uv (package manager), ruff (linting with D rules for docstrings), pytest
- **Docker:** Redis 7 Alpine, dev targets with hot-reload
- **Schemas:** JSON Schema Draft 2020-12 in shared/schemas/
- **Testing:** `bash harness/verify.sh` runs both services

### Running Tests
```bash
cd ts-executor && pnpm test         # 172+ TS tests (vitest)
cd py-engine && uv run pytest tests/ --tb=short -q  # 560+ Python tests
bash harness/verify.sh              # Full verification
```

---

## Strategy Tiers & Risk

| Tier | Risk | Allocation | Strategies |
|------|------|-----------|------------|
| 1 | Low | 50-60% | Aave lending, Lido staking |
| 2 | Medium | 25-35% | Uniswap V3 liquidity, yield farming |
| 3 | High | 10-20% | Flash loan arb, rate arb |

### Circuit Breakers
- Portfolio drawdown >20% → halt all + unwind
- Position loss >10% → close + 24h cooldown
- Gas >3x average → pause non-urgent
- TX failure >3/hour → diagnostic mode
- Protocol TVL drop >30% → withdraw

### Exposure Limits
- Max 40% single protocol
- Max 60% single asset (excl. stables)
- Min 15% stablecoin reserves

---

## What To Work On Next

The most impactful next features (in order of architectural dependency):

1. **INFRA-007 — Main decision loop** (critical: wires everything together, both services still have stub `main()`)
2. **AI-002 — Strategy code-gen** (enables the strategy-as-data vision)
3. **Remaining strategies** (STRAT-002 through STRAT-006)
4. **PORT-003 — Rebalancing engine** (needed for strategy execution)
5. **HARNESS-003 — Human-in-the-loop** (safety gates)
6. **Monitoring** (MON-002, MON-003, MON-004)
7. **Reporting** (REPORT-001, REPORT-002)
