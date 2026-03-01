# Icarus Onboarding — 2026-02-25

## What is Icarus?

Autonomous multi-strategy DeFi bot. **Python (brain)** handles all analysis and decisions. **TypeScript (hands)** handles all blockchain interaction. Redis is the sole communication bus between them.

## Current State

**Branch:** `dev` at `6c26d97`
**P1:** COMPLETE — 31/31 features passing, 732 tests (172 TS + 560 Python)
**P2:** 0/16 features started
**Last commit:** `feat(icarus): complete P1 — 31/31 features passing, 732 tests (TEST-001)` (2026-02-21)

## Architecture Summary

```
ts-executor/    (12 source files)    — Chain listeners, TX execution, event reporting
py-engine/      (29 source files)    — Data pipeline, strategies, risk management, portfolio
shared/schemas/ (3 JSON schemas)     — Redis message contracts
docker-compose.yml                   — Redis + both services
```

### Redis Channels
- `market:events` (TS → Python) — blockchain events, price updates
- `execution:orders` (Python → TS) — trade orders from strategy engine
- `execution:results` (TS → Python) — TX results and confirmations

### TypeScript Service (`ts-executor/src/`)
| File | Subsystem | Feature(s) |
|------|-----------|------------|
| `validation/schema-validator.ts` | infra | INFRA-003 |
| `redis/client.ts` | infra | INFRA-002 |
| `listeners/websocket-manager.ts` | listeners | LISTEN-001 |
| `listeners/event-normalizer.ts` | listeners | LISTEN-001 |
| `listeners/market-event-publisher.ts` | listeners | LISTEN-002 |
| `execution/transaction-builder.ts` | execution | EXEC-001 |
| `execution/flashbots-protect.ts` | execution | EXEC-003 |
| `execution/aave-v3-adapter.ts` | execution | EXEC-004 |
| `execution/event-reporter.ts` | execution | EXEC-010 |
| `wallet/smart-wallet.ts` | wallet | EXEC-002 |
| `security/contract-allowlist.ts` | risk | RISK-006 |
| `index.ts` | entrypoint | — |

### Python Service (`py-engine/`)
| Directory | Modules | Feature(s) |
|-----------|---------|------------|
| `data/` | redis_client, price_feed, gas_monitor, defi_metrics, reconciliation | INFRA-002, DATA-001-004 |
| `validation/` | schema_validator | INFRA-003 |
| `strategies/` | aave_lending, lifecycle_manager | STRAT-001, STRAT-007 |
| `portfolio/` | allocator, position_tracker | PORT-001, PORT-002 |
| `risk/` | drawdown_breaker, position_loss_limit, gas_spike_breaker, tx_failure_monitor, oracle_guard, exposure_limits | RISK-001-004, RISK-007, RISK-008 |
| `harness/` | state_manager, startup_recovery, diagnostic_mode | HARNESS-001-002, HARNESS-004 |
| `monitoring/` | logger | MON-001 |

## Last Implementation (Session 2026-02-21-02)

Merged all 14 remaining features from worktree branches into dev, then implemented TEST-001 (Sepolia integration test suite) with 3 parallel agents:
- **py-e2e:** `test_integration_e2e.py` (14 tests) — full E2E lifecycle + Aave supply/withdraw cycle
- **py-risk:** `test_integration_circuit_breakers.py` (11 tests) — drawdown, gas spike, TX failure, combined
- **py-infra:** `test_integration_startup_recovery.py` (14 tests) + `test_integration_schema_validation.py` (22 tests)

This completed P1 — all 31 features passing.

## README Status

**OUTDATED.** The README was last modified during session 2 (INFRA-001, commit `29b37b6`) and has NOT been updated since. It is missing:
- P1 completion status and feature list
- Subsystem documentation (strategies, risk management, smart wallet, Flashbots, etc.)
- Integration test documentation
- Current test count (732 tests)
- Many modules added since INFRA-001
- P2 roadmap / what's next

The README covers: basic architecture diagram, Redis channels, setup/running/testing commands, environment variables. These are still accurate but incomplete.

## P2 Roadmap (16 features, all `passes: false`)

| ID | Description |
|----|-------------|
| INFRA-006 | PostgreSQL — persistent storage for trade history, audit |
| LISTEN-003 | L2 chain listeners (Arbitrum, Base) |
| EXEC-005 | Uniswap V3 protocol adapter |
| EXEC-006 | Lido staking adapter |
| EXEC-009 | L2 protocol adapters (GMX, Aerodrome) |
| DATA-005 | L2 data pipeline extension |
| STRAT-002 | Liquid staking strategy (Lido, RocketPool) |
| STRAT-003 | Uniswap V3 concentrated liquidity |
| STRAT-004 | Yield farming with auto-compound |
| PORT-003 | Rebalancing engine |
| HARNESS-003 | Human-in-the-loop approval gates |
| MON-002 | Discord alert system |
| MON-003 | Performance dashboard |
| MON-004 | Anomaly detection |
| RISK-005 | Protocol TVL monitor |
| AI-001 | Claude API market sentiment analysis |

## Key Conventions
- Sepolia testnet only in P1 (no mainnet until P2)
- All logs are structured JSON with timestamp, service, event, correlationId
- All Redis messages validated against shared schemas at boundary
- Risk limits are env vars, not hardcoded
- One strategy adjustment per decision cycle
- Commit format: `feat(icarus): description`

## How to Verify
```bash
bash harness/init.sh    # Install deps
bash harness/verify.sh  # Run all tests (must exit 0)
```
