# Icarus Team Orchestration Plan

## Context

Icarus is a dual-service DeFi trading bot (Python brain + TypeScript hands) with 58 features across 3 phases, all currently `passes: false`. The project is scaffolded with stubs but has zero implementation. This plan creates a parallel team to maximize throughput while respecting the one-feature-per-session coding discipline.

## Team Composition (4 agents + lead)

| Agent | Role | Domain | Lifetime |
|-------|------|--------|----------|
| **lead** (you) | Orchestrator | Cross-cutting | Full project |
| **infra** | Infrastructure bootstrap | Both services | Waves 0–2 only |
| **ts-dev** | TypeScript developer | `ts-executor/` | Waves 3–13 |
| **py-data** | Python data/infra agent | `py-engine/data/`, `monitoring/`, harness | Waves 3–11 |
| **py-strat** | Python strategy/risk agent | `py-engine/strategies/`, `risk/`, `portfolio/` | Waves 3–13 |

**Rationale:** TS/Python service boundary respected. Two Python agents split along data-ingestion vs decision-making seam to avoid file conflicts. `infra` is short-lived — disbands after critical path resolves.

## P1 Dependency Graph

```
INFRA-001 → INFRA-003 → INFRA-002 → INFRA-004
                              |
              +---------------+----------------+
              |                                |
        [TS unlocked]                   [Python unlocked]
        LISTEN-001─→LISTEN-002          DATA-001  DATA-002
        EXEC-001─→EXEC-002              DATA-003  DATA-004
        EXEC-003   EXEC-010             MON-001   HARNESS-001
        RISK-006                              |
        EXEC-004(needs 001+002)         HARNESS-002─→HARNESS-004
                                        PORT-001  PORT-002
                                        STRAT-001 STRAT-007
                                              |
                                        [Risk unlocked]
                                        RISK-001..004, 007, 008
                                              |
                                          TEST-001
```

**Critical path:** INFRA-001 → INFRA-003 → INFRA-002 → DATA-001 → PORT-002 → RISK-001 → TEST-001 (7 deep)

## Wave Execution Plan (P1: 31 features)

### Wave 0–2: Infrastructure (infra agent, sequential)

| Wave | Feature | Agent |
|------|---------|-------|
| 0 | **INFRA-001** — Both services build/run, Dockerfiles, README | infra |
| 1 | **INFRA-003** — Schema validation wired in both services (ajv + jsonschema) | infra |
| 2a | **INFRA-002** — Redis pub/sub, streams, TTL pruning, cache layer | infra |
| 2b | **INFRA-004** — Docker Compose hot-reload, log aggregation, env injection | infra |

After Wave 2: **shutdown `infra` agent**, spawn `ts-dev`, `py-data`, `py-strat`.

### Waves 3–12: Parallel Implementation (3 agents)

| Wave | ts-dev | py-data | py-strat |
|------|--------|---------|----------|
| 3 | LISTEN-001 (WebSocket mgr) | DATA-001 (price feeds) | MON-001 (structured logging) |
| 4 | EXEC-001 (TX builder) | DATA-002 (gas monitor) | HARNESS-001 (state persistence) |
| 5 | EXEC-002 (Smart Wallet) | DATA-003 (DeFi metrics) | PORT-001 (portfolio allocator) |
| 6 | LISTEN-002 (event publisher) | DATA-004 (reconciliation) | PORT-002 (position tracker) |
| 7 | EXEC-003 (Flashbots) | HARNESS-002 (startup recovery) | STRAT-001 (Aave lending) |
| 8 | EXEC-004 (Aave adapter) | HARNESS-004 (diagnostic mode) | STRAT-007 (lifecycle mgr) |
| 9 | RISK-006 (allowlist) | RISK-007 (oracle guard) | RISK-001 (drawdown breaker) |
| 10 | EXEC-010 (event reporter) | RISK-008 (exposure limits) | RISK-002 (position loss limit) |
| 11 | — (done) | — (done) | RISK-003 (gas spike breaker) |
| 12 | — | — | RISK-004 (TX failure monitor) |

### Wave 13: Integration

| Feature | Agent(s) |
|---------|----------|
| **TEST-001** (Sepolia e2e suite) | All 3 agents collaborate. ts-dev writes TS test harness, py-strat writes Python test orchestration, py-data ensures data pipeline e2e. |

**Total: ~15 wave-sessions for 31 P1 features** (vs 31 sequential — ~2x improvement)

## Agent Assignment Rules

Each agent follows the **coding-session skill** ritual per feature:
1. `verify.sh` before coding (baseline)
2. Implement ONE feature
3. `verify.sh` after coding (must exit 0)
4. Update `features.json` → `passes: true`
5. Append to `progress.txt`
6. Commit: `feat(icarus): description`
7. Notify lead via SendMessage

## File Ownership (Conflict Prevention)

| Directory | Owner | Notes |
|-----------|-------|-------|
| `ts-executor/` | ts-dev | Exclusive — no Python agents touch |
| `py-engine/data/` | py-data | Data pipeline, metrics |
| `py-engine/monitoring/` | py-data | Logging, alerts |
| `py-engine/strategies/` | py-strat | All strategy implementations |
| `py-engine/risk/` | py-strat | Circuit breakers, limits |
| `py-engine/portfolio/` | py-strat | Allocation, tracking |
| `py-engine/main.py` | py-data (startup), py-strat (registration) | Coordinate via lead if conflict |
| `shared/schemas/` | Read-only after INFRA-003 | No modifications without lead approval |
| `harness/features.json` | Any agent (own features only) | Each agent updates only the feature they completed |
| `harness/progress.txt` | Any agent (append-only) | Never edit previous entries |

## Risk Mitigation

| Risk | Impact | Mitigation |
|------|--------|------------|
| Infrastructure fails (Wave 0–2) | All blocked | infra agent works solo, no wasted resources. Lead debugs immediately. |
| Schema mismatch between services | Runtime failures | INFRA-003 establishes validation first. Schemas are read-only after. |
| Python agents file conflict | Merge errors | Clear subdirectory ownership above. Lead resolves edge cases. |
| verify.sh fails post-implementation | Wave stalls | Agent retries in same session. If stuck, lead reassigns or unblocks. |
| Testnet unavailable | Chain features can't test | Unit tests use mocked Alchemy responses. Real testnet deferred to TEST-001. |

## P2/P3 Outline (High Level)

**P2 (16 features)** — Same team, adjusted roles:
- ts-dev: LISTEN-003, EXEC-005/006/009 (L2 + Uniswap + Lido adapters)
- py-data: DATA-005, INFRA-006 (PostgreSQL), MON-002/003/004
- py-strat: STRAT-002/003/004, PORT-003, RISK-005, HARNESS-003, AI-001
- Estimate: ~6–8 waves

**P3 (11 features)** — Consider adding a `solana-dev` agent:
- Solana chain: LISTEN-004, EXEC-008, DATA-006
- Flash loans: EXEC-007, STRAT-005
- Advanced: STRAT-006, REPORT-001/002, TEST-002/003
- Estimate: ~5–6 waves

## Verification

After each wave completes:
1. Lead runs `harness/verify.sh` to confirm all passing
2. Lead checks `features.json` — completed features should show `passes: true`
3. Lead reviews `progress.txt` for completeness
4. Lead runs `docker compose up` to smoke-test inter-service communication
5. After all P1: run TEST-001 Sepolia integration suite end-to-end

## Critical Files

- `harness/features.json` — Feature tracking (all agents read/update)
- `harness/verify.sh` — Verification gate (run before/after every feature)
- `harness/progress.txt` — Session handoff log (append-only)
- `shared/schemas/*.schema.json` — Inter-service contracts (read-only after INFRA-003)
- `ts-executor/src/redis/client.ts` — Redis client (INFRA-002 expands this)
- `py-engine/main.py` — Python entry point (features register into startup)
- `docker-compose.yml` — Service orchestration (INFRA-004 finalizes)
