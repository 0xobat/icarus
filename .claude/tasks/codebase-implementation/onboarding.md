# Onboarding: Codebase Implementation — Fix 26 Failing Features

## Project Summary

Icarus is an autonomous DeFi asset management bot with two services:
- **ts-executor/** (TypeScript) — chain listeners, TX execution, Safe wallet, encode-only protocol adapters
- **py-engine/** (Python) — data pipeline, AI reasoning via Claude API, risk management, portfolio, strategies

Redis Streams connect them. The authoritative design document is `docs/system-design.md` v4.2.

## Current State

- **Branch:** `dev` at commit `952e695`
- **Features:** 22/48 passing, 26 failing
- **Tests:** 942 passed, 6 failed, 8 skipped (Python) + all TS tests pass
- **verify.sh:** FAILING (6 Python test failures)

### Immediate Bug: 6 Test Failures

All 6 failures are caused by `useFlashbotsProtect` field in strategy-generated orders — it was removed from `execution-orders.schema.json` but both strategy classes (`aave_lending.py`, `aerodrome_lp.py`) and `main.py` still include it:

```
FAILED tests/test_aerodrome_lp.py::TestGenerateOrders::test_orders_are_schema_compliant
FAILED tests/test_integration_e2e.py (3 tests)
FAILED tests/test_integration_schema_validation.py (2 tests)
```

**Fix:** Remove `"useFlashbotsProtect": False` from `aave_lending.py:332`, `aerodrome_lp.py` (similar line), and `main.py:342`.

## Architecture Gaps (5 Systemic Clusters)

### Gap 1: Strategy Pattern Mismatch
**Current:** Strategies generate `execution:orders` directly (are actors)
**Required:** Strategies implement `Strategy` protocol, produce `StrategyReport` with observations/signals/recommendations (are analysts). Decision gate checks for `actionable: true` signals. Claude API makes final trading decisions.

**Files affected:** `strategies/aave_lending.py`, `strategies/aerodrome_lp.py`, `strategies/__init__.py`, `strategies/lifecycle_manager.py`, `main.py`

**New code needed:**
- `Strategy` protocol definition (protocol class with `strategy_id`, `eval_interval`, `data_window`, `evaluate(snapshot) → StrategyReport`)
- `MarketSnapshot` dataclass (`prices`, `gas`, `pools`, `timestamp`)
- `StrategyReport`, `Observation`, `Signal`, `Recommendation` dataclasses
- Auto-discovery from `strategies/` directory
- Rewrite both strategies to implement the protocol

### Gap 2: PostgreSQL Not Operational
**Current:** State in `agent-state.json` (file-based via `StateManager`). DB models and repository exist but aren't wired into the main loop.
**Required:** PostgreSQL for portfolio positions, trade history, strategy statuses, decision audit log. In-memory cache loaded at startup, kept current from `execution:results`.

**Files affected:** `main.py`, `portfolio/position_tracker.py`, `portfolio/allocator.py`, `harness/startup_recovery.py`, `harness/state_manager.py`

**Models already exist:** `db/models.py` has `Trade`, `PortfolioSnapshot`, `StrategyPerformance`, `Alert`, `SchemaVersion`. But missing: `Position` model, `StrategyStatus` model, `DecisionAuditLog` model.

**Repository exists:** `db/repository.py` has CRUD for trades, snapshots, alerts. Missing: position CRUD, strategy status CRUD.

### Gap 3: Circuit Breaker Direct Emission
**Current:** Circuit breakers check thresholds and return state, but don't emit orders with `CB:` prefix directly to Redis.
**Required:** Drawdown, position loss, TVL drop breakers generate schema-compliant orders with `strategy: "CB:drawdown"` etc., publish directly to `execution:orders` stream. Position loss uses Redis TTL keys for 24h cooldowns.

**Files affected:** `risk/drawdown_breaker.py`, `risk/position_loss_limit.py`, `risk/tvl_monitor.py`, `main.py`

### Gap 4: Hold Mode Not Implemented
**Current:** `harness/diagnostic_mode.py` exists — manual-exit only, stores flags in `StateManager` (file-based).
**Required:** Hold mode tracked as `system_status: "normal" | "hold"` in Redis. Two entry paths: Claude API failure, irreconcilable state. Auto-resume when trigger clears. Strategy evaluation continues. Circuit breakers remain active.

**Files affected:** `harness/diagnostic_mode.py` (replace with hold mode), `main.py`, `ai/decision_engine.py`

### Gap 5: Unwired Modules
**Current:** `risk/exposure_limits.py` has `ExposureLimiter` class but it's not integrated into the main decision loop verification gate. `RISK-006` allowlist defaults to empty (bypasses check). `RISK-007` oracle guard was deleted.
**Required:** ExposureLimiter wired into verification gate. Allowlist populated from env vars. Multi-source price deviation check in price_feed.py.

**Files affected:** `main.py`, `ts-executor/src/wallet/safe-wallet.ts`, `data/price_feed.py`

## Feature Clusters & Dependencies

### Cluster A: Foundation (do first)
1. **Fix useFlashbotsProtect bug** — immediate, unblocks verify.sh
2. **INFRA-002** — Redis Streams migration (pub/sub → pure Streams with consumer groups)
3. **INFRA-005** — PostgreSQL models + wiring (Position, StrategyStatus, DecisionAuditLog models)

### Cluster B: Strategy System Rewrite (depends on A)
4. **STRAT-001** — Strategy protocol, MarketSnapshot, StrategyReport, auto-discovery
5. **STRAT-003** — Rewrite LEND-001 to Strategy protocol
6. **STRAT-004** — Rewrite LP-001 to Strategy protocol
7. **STRAT-002** — Strategy lifecycle manager (eval_interval, PG persistence)

### Cluster C: Main Loop + Decision Gate (depends on B)
8. **INFRA-006** — Rewrite DecisionLoop to use StrategyReports, decision gate, Claude API only when actionable
9. **PORT-003** — Rebalancing engine produces signals, not orders

### Cluster D: Circuit Breakers + Hold Mode (depends on A)
10. **RISK-001** — Drawdown CB: direct emission with CB: prefix
11. **RISK-002** — Position loss: CB: prefix + Redis TTL cooldowns
12. **RISK-005** — TVL monitor: CB: prefix direct emission
13. **HARNESS-005** — Hold mode (Redis system_status, auto-resume)
14. **RISK-004** — TX failure monitor enters hold mode

### Cluster E: Portfolio + State (depends on A.3)
15. **PORT-001** — Portfolio allocator with PG persistence
16. **PORT-002** — Position tracker with PG persistence
17. **HARNESS-001** — State persistence (PG + Redis)
18. **HARNESS-002** — Startup recovery (PG + stream replay + reconciliation)
19. **DATA-004** — On-chain reconciliation (compare PG vs chain)

### Cluster F: Risk Wiring (depends on C)
20. **RISK-006** — Allowlist from env vars
21. **RISK-007** — Oracle guard (multi-source deviation check in price_feed.py)
22. **RISK-008** — ExposureLimiter wired into verification gate
23. **DATA-001** — Price feed multi-source validation (>2% rejection)

### Cluster G: Reporting + Testing (depends on E, C)
24. **REPORT-001** — P&L attribution from PG data (by strategy, protocol, asset)
25. **TEST-001** — Integration tests updated for new patterns

### Cluster H: Out of Scope for This Session
26. **RISK-009** — Safe on-chain AllowlistGuard (Solidity contract, separate concern)
27. **DEPLOY-001** — Railway deployment config (after all code is stable)

## Key Files Reference

### Python (py-engine/)
| File | Purpose | Gap |
|------|---------|-----|
| `main.py` | DecisionLoop, main() | Strategy pattern, hold mode, CB direct emission, PG wiring |
| `strategies/aave_lending.py` | LEND-001 | Generates orders, needs Strategy protocol rewrite |
| `strategies/aerodrome_lp.py` | LP-001 | Same as above |
| `strategies/__init__.py` | Exports | Needs auto-discovery |
| `strategies/lifecycle_manager.py` | Strategy lifecycle | Needs PG persistence, eval_interval scheduling |
| `data/redis_client.py` | Redis pub/sub + streams | Needs pure Streams + consumer groups |
| `data/price_feed.py` | Price ingestion | Needs multi-source deviation check |
| `data/reconciliation.py` | On-chain reconciliation | Needs PG integration |
| `db/models.py` | ORM models | Missing Position, StrategyStatus, DecisionAuditLog |
| `db/repository.py` | CRUD | Missing position, strategy status CRUD |
| `risk/drawdown_breaker.py` | Drawdown CB | No CB: prefix emission |
| `risk/position_loss_limit.py` | Position loss CB | No CB: prefix, no Redis TTL cooldown |
| `risk/tvl_monitor.py` | TVL monitor CB | No CB: prefix emission |
| `risk/exposure_limits.py` | Exposure limiter | Exists but unwired from main loop |
| `harness/diagnostic_mode.py` | Diagnostic mode | Needs hold mode replacement |
| `harness/state_manager.py` | File-based state | Needs PG migration |
| `harness/startup_recovery.py` | Startup recovery | Needs PG + stream replay |
| `portfolio/position_tracker.py` | Position tracking | Needs PG persistence |
| `portfolio/allocator.py` | Capital allocation | Needs PG persistence |
| `portfolio/rebalancer.py` | Rebalancing | Needs signal-based output |
| `reporting/pnl_attribution.py` | P&L reports | Needs PG data source |

### TypeScript (ts-executor/)
| File | Purpose | Status |
|------|---------|--------|
| `src/index.ts` | Main entry | Passing |
| `src/wallet/safe-wallet.ts` | Safe wallet | Passing (RISK-006 env vars needed) |
| `src/redis/client.ts` | Redis | INFRA-002 needs Streams migration |

### Shared
| File | Purpose |
|------|---------|
| `shared/schemas/execution-orders.schema.json` | Order schema (useFlashbotsProtect removed) |
| `shared/schemas/market-events.schema.json` | Market events schema |
| `shared/schemas/execution-results.schema.json` | Results schema |
| `docs/system-design.md` | Authoritative design v4.2 |
| `STRATEGY.md` | Strategy definitions |
| `harness/features.json` | Feature tracking |

## Parallelization Strategy

These clusters can run in parallel:
- **Cluster B** (strategy rewrite) — independent Python work
- **Cluster D** (circuit breakers + hold mode) — independent Python work
- **Cluster E** (portfolio + state) — independent Python work (depends on INFRA-005 models)

Sequential dependencies:
- Cluster A must complete first (foundation)
- Cluster C depends on B (main loop needs new strategy pattern)
- Cluster F depends on C (risk wiring needs new main loop)
- Cluster G depends on E + C (testing needs all new patterns)

## Test Counts

- Python: 942 passed, 6 failed, 8 skipped
- TypeScript: 185 tests, all passing
- Total: ~1,135 tests
