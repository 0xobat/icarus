# Onboarding: Wave 4-6 Feature Implementation

## Project Summary
Icarus is an autonomous DeFi asset management bot on Base (stablecoins only). Dual-service architecture:
- **py-engine/** — Python: data pipeline, AI reasoning, risk management, portfolio
- **ts-executor/** — TypeScript: chain listeners, TX execution, protocol adapters
- **shared/schemas/** — JSON schemas for Redis message contracts
- Communication via Redis Streams (market:events, execution:orders, execution:results)

## Current State (2026-03-08)
- **Branch:** `dev`
- **Last commit:** `538d7d7` (docs checkpoint)
- **Tests:** 1,214 passing (190 TS + 1,024 Python), 15 skipped
- **verify.sh:** PASS
- **Features:** 36/48 passing

## What Was Just Completed (Team Session 2026-03-08-01, Waves 1-3)
12 features implemented across 3 waves:
- **Wave 1:** INFRA-005 (PostgreSQL), INFRA-002 (Redis Streams), DATA-001 (Price feed), RISK-006 (Allowlist env vars)
- **Wave 2:** RISK-008 (Exposure limits), STRAT-002 (Lifecycle manager), PORT-001 (Allocator), PORT-002 (Position tracker)
- **Wave 3:** RISK-004 (TX failure monitor), RISK-007 (Oracle guard), STRAT-003 (LEND-001), STRAT-004 (LP-001)

## Remaining 12 Features (Planned Waves 4-6)

### Wave 4 — Circuit Breakers + Rebalancing (4 features)
| ID | Description | Key Requirement |
|----|-------------|-----------------|
| RISK-001 | Drawdown circuit breaker | >20% drawdown → CB:drawdown orders direct to Redis |
| RISK-002 | Per-position loss limit | >10% loss → CB:position_loss close + 24h Redis TTL cooldown |
| RISK-005 | Protocol TVL monitor | >30% TVL drop → CB:tvl_drop withdraw from affected protocol |
| PORT-003 | Rebalancing engine | Drift detection → rebalance_needed signal (goes through Claude) |

**Critical pattern for RISK-001/002/005:** Circuit breakers that can unwind use a **separate execution path** — they emit orders directly to Redis `execution:orders`, bypassing the decision gate and Claude API. Orders use `CB:` prefix in the `strategy` field. Cooldowns tracked via Redis TTL keys.

### Wave 5 — Data + State + Reporting (4 features)
| ID | Description | Key Requirement |
|----|-------------|-----------------|
| DATA-004 | On-chain position reconciliation | Query on-chain → compare DB → trust on-chain |
| HARNESS-001 | State persistence | PostgreSQL for positions/trades/strategy; Redis for cache/cooldowns/status |
| REPORT-001 | P&L attribution | Breakdown by strategy, protocol, asset, time period |
| INFRA-006 | Main decision loop | Wires all modules: enrich → eval → gate → Claude → verify → emit |

### Wave 6 — Integration + Deploy (4 features)
| ID | Description | Key Requirement |
|----|-------------|-----------------|
| HARNESS-002 | Startup recovery | Load PG → replay Redis → on-chain reconcile → health check → resume/hold |
| TEST-001 | Integration test suite | E2E lifecycle, CB integration, schema validation, startup recovery |
| DEPLOY-001 | Railway deployment | Dockerfiles + Railway config + persistent volumes |
| RISK-009 | Safe on-chain guard | AllowlistGuard Solidity contract deployed to Safe |

## 5 Systemic Gap Clusters (from verification audit)
1. **Strategy pattern:** v1 code generated orders directly; v4.2 design requires report-producing analysts → **RESOLVED in Waves 1-3** (STRAT-001-004 now use Strategy protocol)
2. **PostgreSQL:** State was in agent-state.json → **PARTIALLY RESOLVED** (INFRA-005 done, HARNESS-001 pending)
3. **Circuit breaker direct emission:** No CB: prefix orders to Redis yet → **Wave 4**
4. **Hold mode:** Now implemented (HARNESS-005 done in Wave 1 recovery)
5. **Unwired modules:** RISK-008 wired in Wave 2; others pending

## Key Architecture Decisions
- Python owns all decisions, TypeScript owns all chain interactions
- Protocol adapters are encode-only pure functions
- Strategies are analysts (produce StrategyReports), not executors
- Circuit breakers bypass Claude — emit directly to Redis
- Safe 1-of-2 multisig: agent EOA + human recovery signer
- MarketSnapshot dataclass pre-sliced by strategy's data_window

## Key Files
- `py-engine/main.py` — DecisionLoop class
- `py-engine/risk/` — Circuit breakers, exposure limits
- `py-engine/portfolio/` — Allocator, position tracker
- `py-engine/strategies/` — Auto-discovered strategy classes
- `py-engine/ai/decision_engine.py` — Claude API integration
- `py-engine/ai/insight_synthesis.py` — Prompt assembly
- `ts-executor/src/index.ts` — Bootstrap, order subscription
- `ts-executor/src/execution/transaction-builder.ts` — TX construction + retry
- `docs/system-design.md` — System design v4.2 (source of truth)
- `STRATEGY.md` — Strategy definitions (LEND-001, LP-001)

## Testing
```bash
cd ts-executor && pnpm test
cd py-engine && uv run pytest tests/ --tb=short -q
bash harness/verify.sh
```

## Next Action
Resume with **Wave 4**: RISK-001, RISK-002, RISK-005, PORT-003
These are 4 independent features that can be parallelized across agents in worktrees.
