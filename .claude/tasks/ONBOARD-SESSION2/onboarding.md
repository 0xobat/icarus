# Icarus — Comprehensive Onboarding

## TL;DR

Icarus is an autonomous multi-strategy DeFi trading bot. **P1 is complete** (31/31 features, 732 tests). P2 has 28 unimplemented features spanning L2 support, advanced strategies, AI decision engine, monitoring, and deployment.

**Mental model:** Think of it as a brain (Python) + hands (TypeScript) connected by a nervous system (Redis). The brain analyzes markets and decides what to do. The hands interact with blockchains to execute those decisions. Everything flows through three Redis channels with strict schema contracts.

---

## Architecture — The Three Layers

### 1. TypeScript Executor (`ts-executor/`) — "The Hands"

Does ONE thing: interact with blockchains. No decision-making.

```
listeners/        → Listens to blockchain events via Alchemy WebSockets
  websocket-manager.ts    → Connection management, reconnection, backpressure
  event-normalizer.ts     → Raw events → standardized MarketEvent format
  market-event-publisher.ts → Deduplication + publish to Redis

execution/        → Builds and sends transactions
  transaction-builder.ts  → Core TX engine: nonce management, retry, gas estimation
  flashbots-protect.ts    → MEV protection (routes TXs through Flashbots private mempool)
  aave-v3-adapter.ts      → Aave V3 supply/withdraw protocol adapter
  event-reporter.ts       → Reports TX results back to Python

wallet/           → Smart wallet (ERC-4337)
  smart-wallet.ts         → Spending limits, contract allowlist, UserOp construction

security/         → Guardrails
  contract-allowlist.ts   → Per-chain whitelist of approved contracts

redis/client.ts   → Redis pub/sub, streams, cache with schema validation
validation/schema-validator.ts → AJV-based JSON schema enforcement
```

**Key pattern:** Everything published to Redis is schema-validated. Invalid messages are rejected loudly.

**Stack:** viem (ETH client), ioredis (Redis), ajv (validation), vitest (testing)

### 2. Python Engine (`py-engine/`) — "The Brain"

Does ONE thing: analyze data and make decisions. No blockchain interaction.

```
data/             → Data ingestion
  redis_client.py       → Mirror of TS Redis client (pub/sub, streams, cache)
  price_feed.py         → Multi-source prices (CoinGecko + DeFi Llama) + oracle guard
  gas_monitor.py        → Gas price tracking
  defi_metrics.py       → Protocol metrics (Aave V3, Uniswap V3, Lido, TVL)
  reconciliation.py     → On-chain vs agent state reconciliation

strategies/       → Trading strategies
  aave_lending.py       → Tier 1: Aave V3 supply rotation by APY
  lifecycle_manager.py  → Strategy FSM: evaluating → active → paused → retired

portfolio/        → Capital management
  allocator.py          → Tier-based allocation (50-60% T1, 25-35% T2, 10-20% T3)
  position_tracker.py   → Position lifecycle, P&L calculation

risk/             → Circuit breakers & limits
  drawdown_breaker.py   → Portfolio-level: 15% warning, 20% critical halt
  position_loss_limit.py → Per-position: 10% stop-loss + 24h cooldown
  gas_spike_breaker.py  → Gas >3x 24h average → pause non-urgent ops
  tx_failure_monitor.py → >3 failures/hour → pause + diagnostic mode
  oracle_guard.py       → >2% price deviation across sources → reject
  exposure_limits.py    → Max 40% protocol, 60% asset, 15% stablecoin reserve

harness/          → Agent infrastructure
  state_manager.py      → Persistent state (JSON, atomic writes)
  startup_recovery.py   → Startup sequence: load state → Redis replay → reconcile → health check
  diagnostic_mode.py    → Recovery & troubleshooting

monitoring/logger.py → Structured JSON logs, correlation IDs, PII redaction
validation/schema_validator.py → jsonschema-based validation
```

**Stack:** redis, pandas, numpy, jsonschema, pytest, ruff

### 3. Shared Schemas (`shared/schemas/`) — "The Nervous System"

Three JSON schemas define ALL communication between services:

| Schema | Channel | Direction | Purpose |
|--------|---------|-----------|---------|
| `market-events.schema.json` | `market:events` | TS → Python | Blockchain events (blocks, swaps, rate changes, prices) |
| `execution-orders.schema.json` | `execution:orders` | Python → TS | Trade instructions (supply, withdraw, swap, stake) |
| `execution-results.schema.json` | `execution:results` | TS → Python | TX outcomes (confirmed, failed, reverted, timeout) |

**Traceability:** `correlationId` threads through entire lifecycle: market event → order → result.

---

## The Decision Loop

```
1. INGEST   → TS listens to blockchain events via Alchemy WebSocket
2. PUBLISH  → TS normalizes events and publishes to market:events
3. ENRICH   → Python enriches with prices, gas, protocol metrics
4. DECIDE   → Strategy evaluates → (future: Claude API reasons over insights)
5. RISK     → Circuit breakers + exposure limits approve/reject
6. ORDER    → Python publishes approved order to execution:orders
7. EXECUTE  → TS builds TX (viem), routes through Flashbots, submits
8. REPORT   → TS publishes result to execution:results
9. UPDATE   → Python updates portfolio state, feeds back to next cycle
```

---

## Current State (as of 2026-02-25)

**Branch:** `dev` at `b1fe018`
**P1:** COMPLETE — 31/31 features passing
**Tests:** 732 total (172 TS + 560 Python), all passing
**Verification:** `bash harness/verify.sh` exits 0

### P1 Features (all ✓)

| Category | Features |
|----------|----------|
| Infrastructure | INFRA-001 (scaffolding), INFRA-002 (Redis), INFRA-003 (schema validation), INFRA-004 (Docker Compose) |
| Listeners | LISTEN-001 (Alchemy WS), LISTEN-002 (market event publisher) |
| Execution | EXEC-001 (TX builder), EXEC-002 (smart wallet), EXEC-003 (Flashbots), EXEC-004 (Aave V3), EXEC-010 (event reporter) |
| Data | DATA-001 (price feeds), DATA-002 (gas monitor), DATA-003 (DeFi metrics), DATA-004 (reconciliation) |
| Strategies | STRAT-001 (Aave lending), STRAT-007 (lifecycle manager) |
| Portfolio | PORT-001 (allocator), PORT-002 (position tracker) |
| Risk | RISK-001 (drawdown), RISK-002 (position loss), RISK-003 (gas spike), RISK-004 (TX failure), RISK-006 (allowlist), RISK-007 (oracle guard), RISK-008 (exposure limits) |
| Harness | HARNESS-001 (state manager), HARNESS-002 (startup recovery), HARNESS-004 (diagnostic mode) |
| Monitoring | MON-001 (structured logging) |
| Testing | TEST-001 (integration test suite) |

---

## P2 Roadmap — What Needs to Be Done

28 features remaining across these categories. Not yet prioritized into waves.

### Infrastructure
- **INFRA-005** — Main decision loop (orchestrate full pipeline: ingest → enrich → decide → risk → execute → report)
- **INFRA-006** — PostgreSQL persistent storage (trade history, audit trail, migrations)
- **INFRA-007** — Railway deployment (Docker → production, health checks, CI/CD)

### AI / Decision Engine
- **AI-001** — Claude API integration (market sentiment analysis, structured decisions)
- **AI-002** — Strategy code generation (Claude reads strategy.md → generates Python classes)
- **AI-003** — Insight synthesis pipeline (structured snapshots for Claude API)

### Listeners & Execution
- **LISTEN-003** — L2 chain listeners (Arbitrum, Base — different finality, faster blocks)
- **EXEC-005** — Uniswap V3 adapter (concentrated liquidity management)
- **EXEC-006** — Lido staking adapter (stETH rebasing)
- **EXEC-007** — Flash loan executor (atomic cross-DEX arb)
- **EXEC-009** — L2 protocol adapters (GMX on Arbitrum, Aerodrome on Base)

### Strategies
- **STRAT-002** — Liquid staking strategy (Lido, RocketPool)
- **STRAT-003** — Uniswap V3 concentrated liquidity (range management)
- **STRAT-004** — Yield farming with auto-compound
- **STRAT-005** — Flash loan arbitrage
- **STRAT-006** — Rate arbitrage across lending protocols
- **STRAT-008** — Strategy.md file watcher + code-gen trigger

### Data
- **DATA-005** — L2 data pipeline (Arbitrum, Base protocol metrics)

### Portfolio
- **PORT-003** — Rebalancing engine (cross-tier capital reallocation)

### Risk
- **RISK-005** — Protocol TVL monitor (>30% drop → emergency withdrawal)

### Monitoring
- **MON-002** — Discord alert system
- **MON-003** — Performance dashboard
- **MON-004** — Anomaly detection

### Human-in-the-Loop
- **HARNESS-003** — Approval gates (large trades, new protocols, tier activation)

### Testing
- **TEST-002** — Historical stress testing (COVID crash, FTX collapse replay)

### Future Phases (P3+)
- **LISTEN-004/EXEC-008/DATA-006** — Solana integration
- **PORT-004** — DeFi tax accounting
- **PORT-005** — Performance attribution
- **MON-005** — ML-based execution optimization

---

## Mental Model

### "Brain + Hands" Separation
The single most important design principle: **Python decides, TypeScript executes**. Neither crosses the boundary. This is enforced structurally — they can ONLY communicate via Redis messages that must pass JSON schema validation.

### Risk is Non-Negotiable
Every order passes through a gauntlet of circuit breakers before reaching the TS executor. There are 7 distinct risk checks: drawdown, position loss, gas spikes, TX failure rate, oracle manipulation, exposure limits, and contract allowlist. The 20% drawdown breaker requires **manual restart** — it cannot be auto-recovered.

### Strategies are Data, Not Code
The vision (P2) is that strategies live in `strategy.md` as human-readable specs. Claude generates the Python implementation. Currently only one strategy exists (Aave lending rotation), hardcoded. P2 features AI-001/AI-002/AI-003 will make Claude the actual decision engine.

### One Adjustment Per Cycle
The lifecycle manager enforces that only ONE strategy can make ONE adjustment per decision cycle. This prevents cascading rebalances that could amplify market movements.

### Schema-Driven Communication
All three Redis channels have strict JSON schemas. Both services validate at the boundary. Invalid messages are **rejected loudly** (logged, not silently dropped). The `correlationId` field threads through the entire lifecycle for auditability.

---

## How to Work on This Project

### Verify before and after
```bash
bash harness/init.sh    # Install deps (idempotent)
bash harness/verify.sh  # Run all tests
```

### Running locally
```bash
docker compose up -d redis  # Start Redis
cd ts-executor && pnpm dev  # Start TS service
cd py-engine && uv run python main.py  # Start Python service
```

### Key rules
- Never remove features from `features.json` — only add or update `passes`
- One feature per session — pick next `passes: false` and focus
- Always update `progress.txt` at session end
- Commit format: `feat(icarus): description`
- Sepolia testnet only — no mainnet until P2 deployment
- Risk limits are env vars, not hardcoded

### Testing
```bash
cd ts-executor && pnpm test        # 172 TS tests
cd py-engine && uv run pytest tests/ --tb=short -q  # 560 Python tests
```

---

## Implementation History (Session Log)

| Session | Features | Tests Added |
|---------|----------|-------------|
| 2026-02-16-01 | Harness initialization | - |
| 2026-02-16-02 | INFRA-001 (scaffolding) | - |
| 2026-02-16-03 | INFRA-003 (schema validation) | 33 |
| 2026-02-16-04 | INFRA-002 (Redis) | 16 |
| 2026-02-16-05 | INFRA-004 (Docker Compose) | - |
| 2026-02-16-06 | LISTEN-001 (Alchemy WS) | 22 |
| 2026-02-16-07 | EXEC-001 (TX builder) | 22 |
| 2026-02-16-08 | DATA-003 (DeFi metrics) | 21 |
| 2026-02-16-09 | PORT-002 (position tracker) | 32 |
| 2026-02-16-10 | EXEC-002 (smart wallet) | 25 |
| 2026-02-16-11 | Merge checkpoint (16/31 P1) | - |
| 2026-02-21-01 | 14 features via 3 parallel agents | 331 |
| 2026-02-21-02 | Merge + TEST-001 (31/31 P1 COMPLETE) | 61 |
| 2026-02-25 | Docstring/JSDoc linter rules, PRD redesign | - |
