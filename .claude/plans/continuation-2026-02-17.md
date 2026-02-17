# Icarus P1 Continuation Plan — 2026-02-17

## Status at Pause

**Branch:** `dev` (all work merged)
**Tests:** 280 passing (68 TS + 212 Python)
**Features:** 13/31 P1 passing

### Completed (13)
| ID | Subsystem | Agent |
|----|-----------|-------|
| INFRA-001 | Project scaffolding | lead |
| INFRA-002 | Redis communication | lead |
| INFRA-003 | Schema validation | lead |
| INFRA-004 | Docker Compose polish | lead |
| LISTEN-001 | WebSocket manager | ts-dev |
| EXEC-001 | Transaction builder | ts-dev |
| DATA-001 | Price feeds | py-data |
| DATA-002 | Gas monitor | py-data |
| DATA-003 | DeFi metrics | py-data |
| MON-001 | Structured logging | py-strat |
| HARNESS-001 | State persistence | py-strat |
| PORT-001 | Portfolio allocator | py-strat |
| PORT-002 | Position tracker | py-strat |

### Remaining (18)
| ID | Subsystem | Assign To | Dependencies |
|----|-----------|-----------|-------------|
| EXEC-002 | Smart Wallet (ERC-4337) | ts-dev | — |
| DATA-004 | Position reconciliation | py-data | — |
| STRAT-001 | Aave lending strategy | py-strat | — |
| LISTEN-002 | Market event publisher | ts-dev | LISTEN-001 |
| EXEC-003 | Flashbots Protect | ts-dev | EXEC-001 |
| EXEC-004 | Aave V3 adapter | ts-dev | EXEC-001 |
| EXEC-010 | Event reporter | ts-dev | EXEC-001 |
| HARNESS-002 | Startup recovery | py-data | HARNESS-001 |
| HARNESS-004 | Diagnostic mode | py-data | MON-001 |
| STRAT-007 | Strategy lifecycle mgr | py-strat | STRAT-001 |
| RISK-006 | Contract allowlist | ts-dev | — |
| RISK-007 | Oracle guard | py-data | DATA-001 |
| RISK-008 | Exposure limits | py-data | PORT-001 |
| RISK-001 | Drawdown breaker | py-strat | PORT-002, DATA-001 |
| RISK-002 | Position loss limit | py-strat | PORT-002 |
| RISK-003 | Gas spike breaker | py-strat | DATA-002 |
| RISK-004 | TX failure monitor | py-strat | — |
| TEST-001 | Sepolia integration | all | all above |

## Execution Plan

### Step 1: Worktree Refresh

Worktrees exist at `.worktrees/{ts-dev,py-data,py-strat}` on branches `feat/{ts-dev,py-data,py-strat}`. Before spawning agents:

```bash
# Update each worktree to match current dev
cd .worktrees/ts-dev && git merge dev && cd -
cd .worktrees/py-data && git merge dev && cd -
cd .worktrees/py-strat && git merge dev && cd -
```

### Step 2: Restart In-Flight Features (Wave 5 redo)

These 3 features were in-flight when agents were shut down. No commits exist — restart from scratch:

| Feature | Agent | Worktree |
|---------|-------|----------|
| EXEC-002 | ts-dev | .worktrees/ts-dev |
| DATA-004 | py-data | .worktrees/py-data |
| STRAT-001 | py-strat | .worktrees/py-strat |

### Step 3: Continue Waves 6-12

After merging Wave 5 redo into dev and refreshing worktrees:

| Wave | ts-dev | py-data | py-strat |
|------|--------|---------|----------|
| 6 | LISTEN-002 | HARNESS-002 | STRAT-007 |
| 7 | EXEC-003 | HARNESS-004 | RISK-001 |
| 8 | EXEC-004 | RISK-007 | RISK-002 |
| 9 | EXEC-010 | RISK-008 | RISK-003 |
| 10 | RISK-006 | *idle* | RISK-004 |

**Merge after each wave** — same pattern as before:
```bash
git merge --no-ff feat/ts-dev -m "merge(icarus): FEATURE from ts-dev"
# repeat for py-data, py-strat
# then refresh worktrees
```

### Step 4: Wave 13 — TEST-001 Sepolia Integration

All 3 agents collaborate:
- **ts-dev:** TS test harness (Sepolia TX lifecycle with mocked Alchemy)
- **py-data:** Data pipeline e2e (price -> cache -> strategy input)
- **py-strat:** Strategy e2e (signal -> order -> result -> portfolio update)
- **Lead:** Merge all, run verify.sh, docker compose up, e2e smoke test

### Cross-Agent Dependencies (Mock-First)

Same strategy: agents mock dependencies from other services rather than waiting.

| Feature | Needs | Mock Strategy |
|---------|-------|---------------|
| STRAT-001 | EXEC-004 response | Mock execution:results |
| STRAT-007 | Multiple strategies | Mock strategy configs |
| RISK-001 | PORT-002 + DATA-001 | Both already merged to dev |
| HARNESS-002 | All services | Mock Redis messages |

## Done Criteria

P1 complete when:
- All 31 features show `passes: true` in features.json
- `harness/verify.sh` exits 0 on `dev`
- `docker compose up` starts all services communicating
- TEST-001 validates at least one full cycle end-to-end
