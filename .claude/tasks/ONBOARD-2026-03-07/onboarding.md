# Icarus Onboarding — 2026-03-07

## TL;DR

Icarus is an autonomous DeFi asset management bot with a **dual-service architecture**: TypeScript executor (chain interactions) and Python engine (decision-making). Services communicate via Redis pub/sub. The system is scoped to **Base chain, stablecoins only, 2 strategies** (Aave V3 lending + Aerodrome stable LP).

**Current state:** 45/62 features passing, 955 Python + 188 TS tests = 1,143 total, all passing. `verify.sh` passes (after `uv sync --extra dev`). Branch: `dev` (clean).

---

## Architecture

```
STRATEGY.md (human-authored)
    │
    ▼
py-engine (Python 3.12, uv)                ts-executor (TypeScript, pnpm, viem)
┌────────────────────────────┐              ┌──────────────────────────────┐
│ DecisionLoop (main.py)     │              │ index.ts (boot + main)       │
│  ├─ PriceFeedManager       │              │  ├─ AlchemyWebSocketManager  │
│  ├─ GasMonitor             │              │  ├─ MarketEventPublisher     │
│  ├─ DeFiMetricsCollector   │◄─market:     │  ├─ L2ListenerManager (Base) │
│  ├─ InsightSynthesizer     │  events──────│  │                           │
│  ├─ DecisionEngine (Claude)│              │  ├─ TransactionBuilder       │
│  ├─ Risk Gate (5 breakers) │──execution:  │  ├─ SafeWalletManager        │
│  ├─ PortfolioAllocator     │  orders─────►│  ├─ EventReporter            │
│  ├─ PositionTracker        │              │  ├─ aave-v3-adapter (encode) │
│  └─ LifecycleManager       │◄─execution:  │  └─ aerodrome-adapter (encode│
│                            │  results─────│                              │
│ Strategies:                │              └──────────────────────────────┘
│  ├─ aave_lending.py (LEND-001)
│  └─ aerodrome_lp.py (LP-001)
│
│ Risk breakers:
│  ├─ DrawdownBreaker (>20% from peak)
│  ├─ GasSpikeBreaker (>3x 24h avg)
│  ├─ TxFailureMonitor (>3/hour)
│  ├─ PositionLossLimit (>10%)
│  └─ TvlMonitor (>30% TVL drop)
│
│ DB: SQLAlchemy (SQLite dev / PG prod)
└────────────────────────────┘
         │
    Redis 7 (pub/sub + streams + cache)
```

### Key Design Principles

1. **Python owns all decisions, TypeScript owns all chain interactions** — neither crosses domains
2. **Protocol adapters are encode-only pure functions** (calldata in, no state)
3. **Safe 1-of-2 multisig wallet** — agent hot key + human recovery key
4. **Decision fast-path** — simple threshold crossings bypass Claude API; Claude API for ambiguous conditions
5. **Risk gate is non-negotiable** — all decisions pass through circuit breakers before execution
6. **One strategy adjustment per decision cycle**
7. **Strategies are data** (STRATEGY.md), not hardcoded logic

### Redis Channels (3 total)

| Channel | Direction | Schema File |
|---------|-----------|-------------|
| `market:events` | TS → Python | `shared/schemas/market-events.schema.json` |
| `execution:orders` | Python → TS | `shared/schemas/execution-orders.schema.json` |
| `execution:results` | TS → Python | `shared/schemas/execution-results.schema.json` |

All messages validated against JSON schemas at the boundary (both publish and subscribe sides).

---

## Source File Map

### py-engine/ (Python service)

| Directory | Files | Purpose |
|-----------|-------|---------|
| `ai/` | `decision_engine.py`, `insight_synthesis.py` | Claude API decision engine + insight packaging |
| `data/` | `price_feed.py`, `gas_monitor.py`, `defi_metrics.py`, `reconciliation.py`, `redis_client.py` | Market data pipeline + Redis client |
| `db/` | `database.py`, `models.py`, `repository.py` | SQLAlchemy database layer |
| `harness/` | `approval_gates.py`, `diagnostic_mode.py`, `startup_recovery.py`, `state_manager.py` | Operational tooling |
| `monitoring/` | `dashboard.py`, `logger.py` | Monitoring + structured JSON logging |
| `portfolio/` | `allocator.py`, `position_tracker.py`, `rebalancer.py` | Portfolio management |
| `reporting/` | `pnl_attribution.py` | P&L reporting |
| `risk/` | `drawdown_breaker.py`, `exposure_limits.py`, `gas_spike_breaker.py`, `position_loss_limit.py`, `tvl_monitor.py`, `tx_failure_monitor.py` | Circuit breakers + risk limits |
| `strategies/` | `aave_lending.py`, `aerodrome_lp.py`, `lifecycle_manager.py` | Strategy implementations |
| `validation/` | `schema_validator.py` | JSON schema validation |
| `tests/` | 30 test files | 955 tests, all passing |

### ts-executor/ (TypeScript service)

| Directory | Files | Purpose |
|-----------|-------|---------|
| `src/execution/` | `aave-v3-adapter.ts`, `aerodrome-adapter.ts`, `transaction-builder.ts`, `event-reporter.ts` | Protocol encode modules + TX builder |
| `src/listeners/` | `websocket-manager.ts`, `market-event-publisher.ts`, `l2-listener.ts`, `event-normalizer.ts` | Chain event listening |
| `src/redis/` | `client.ts` | Redis pub/sub + streams + cache |
| `src/wallet/` | `safe-wallet.ts` | Safe 1-of-2 multisig wallet |
| `src/validation/` | `schema-validator.ts` | JSON schema validation (ajv) |
| `tests/` | 13 test files | 188 tests (+3 skipped Sepolia live), all passing |

### Key entry points

- `py-engine/main.py` — `DecisionLoop` class: enrich → synthesize → decide → risk gate → emit orders
- `ts-executor/src/index.ts` — Bootstraps listeners, wallet, adapters, TX builder; subscribes to `execution:orders`

---

## Current Strategy Scope (v1)

Only 2 strategies, both Tier 1, both on Base, stablecoins only:

### LEND-001: Aave V3 Lending Supply
- Supply USDC/USDbC to Aave V3 on Base
- Rotate to highest supply APY when differential > 0.5% after gas
- Max 70% of portfolio
- Exit when APY < 1.0%

### LP-001: Aerodrome Stable LP
- Provide liquidity to stable pools (USDC/USDbC, USDC/DAI)
- Stake LP in gauges for AERO emissions
- Harvest when pending AERO > $0.50, swap to USDC, re-deposit
- Max 30% of portfolio
- Exit when emission APR < 1.5% or AERO crashes >50%

---

## Feature Status Summary

| Phase | Passing | Total | Description |
|-------|---------|-------|-------------|
| P1 | 41 | 47 | Core system (Base + Aave/Aerodrome) |
| P1b | 1 | 4 | Tier 2 strategies + Flashbots (deferred) |
| P1c | 3 | 6 | Tier 3 strategies + L2 expansion (deferred) |
| P2 | 0 | 1 | Historical stress testing |
| P3 | 0 | 3 | Solana chain support |
| P4 | 0 | 1 | Railway production deployment |
| **Total** | **45** | **62** | |

### Failing P1 features (6 items — the actionable ones)

| ID | Description | Why failing |
|----|-------------|-------------|
| EXEC-006 | Lido staking adapter | Code deleted in v1 simplification (not needed for Base stablecoins) |
| AI-002 | Strategy code-gen pipeline | Code deleted (strategies are now manually authored) |
| STRAT-008 | Strategy ingestion (parse STRATEGY.md) | Code deleted (ingestion removed in simplification) |
| MON-002 | Discord alert system | Code deleted (user requested removal) |
| MON-004 | Anomaly detection | Code deleted (user requested removal) |
| TEST-003 | ML gas prediction model | Code deleted (ml/ package removed) |

These 6 features represent **intentionally deleted code** from the v1 simplification. They should either be:
- Removed from features.json entirely
- Moved to a future phase (P1b/P1c/P2)
- Marked as `"retired": true`

---

## Development Workflow

```bash
# Setup
bash harness/init.sh

# Run services
docker compose up -d redis
cd ts-executor && pnpm dev     # TS service
cd py-engine && uv run python main.py  # Python service

# Test
cd ts-executor && pnpm test    # 188 TS tests
cd py-engine && uv run pytest tests/ --tb=short -q  # 955 Python tests
bash harness/verify.sh         # Full verification (tsc, eslint, ruff, pytest, schemas, docker)

# Commit convention
feat(icarus): description
fix(icarus): description
```

### Important notes

- `uv sync --extra dev` must be run before verify.sh works (installs ruff + pytest)
- TS tests use vitest
- Python tests use pytest with structured JSON logging mocked
- Redis is mocked in most tests (no live Redis needed)
- Sepolia live tests (5) are skip-by-default (`ALCHEMY_SEPOLIA_HTTP_URL` env var)

---

## Session History (Key Milestones)

| Date | Key achievement |
|------|-----------------|
| 2026-02-16 | P1 built: 31/31 features, 732 tests, all infra + strategies + risk |
| 2026-02-25 | PRD redesigned: Claude as decision engine, features 31→62 |
| 2026-02-25 | Parallel sprint (6 agents) — canceled mid-execution |
| 2026-02-28 | Recovery: rescued orphaned worktrees, completed remaining P1 → 57/62, 1,628 tests |
| 2026-03-01 | TS integration fixes + Safe 1-of-2 wallet refactor |
| 2026-03-06 | **v1 simplification**: 6 strategies → 2 (LEND-001 + LP-001), Base only, ~7.3K lines deleted |
| 2026-03-06 | Cross-service sync: schemas, adapters, data pipeline all aligned to v1 scope |

### Key refactoring decisions

1. **ERC-4337 → Safe 1-of-2** (2026-03-01): Per ethskills.com, Safe is battle-tested ($100B+ secured), native batch via MultiSend
2. **6 strategies → 2** (2026-03-06): Simplified to conservative stablecoin-only on Base (LEND-001 + LP-001)
3. **Deleted code**: Lido, Uniswap V3, GMX, Flash loan, Flashbots, ML gas predictor, Discord alerts, anomaly detection, code-gen, strategy ingestion

---

## What's Next

Per progress.txt, the immediate next steps are:
1. **Update prd.md for v1 scope** — last progress entry says "Next: Update prd.md for v1 scope"
2. **Clean up features.json** — 6 P1 features are failing because their code was intentionally deleted
3. **P2: TEST-002** — Historical stress testing with real market data
4. **P3: Solana** — LISTEN-004, EXEC-008, DATA-006
5. **P4: Railway deployment** — INFRA-005

---

## Environment & Tools

- **Python:** 3.12, uv, ruff, pytest, SQLAlchemy, anthropic SDK, redis-py, pandas
- **TypeScript:** Node 22, pnpm, vitest, viem, Safe Protocol Kit, ajv, ioredis
- **Infrastructure:** Docker Compose (Redis 7 + both services), PostgreSQL (prod), SQLite (dev)
- **Git:** Branch `dev`, clean working tree, monorepo convention (`feat(icarus):` commits)
