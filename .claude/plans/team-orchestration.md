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

## Agent Skill Requirements

Every coding agent (`infra`, `ts-dev`, `py-data`, `py-strat`) **MUST** invoke the **`coding-session` skill** before starting work on each feature. This is non-negotiable. The lead should include this instruction when spawning each agent:

> "You MUST invoke the `coding-session` skill before beginning work on any feature. Follow its ritual exactly: verify → implement ONE feature → verify → update features.json → append progress.txt → commit → notify lead."

The lead does NOT use the coding-session skill — the lead orchestrates, reviews, and merges.

## Git Workflow: Worktrees

Each agent works in an **isolated git worktree** to eliminate merge conflicts, concurrent file edit issues, and broken shared state.

### Setup

The lead creates worktrees before spawning agents. All worktrees live in `.worktrees/` (must be in `.gitignore`).

```bash
# One-time setup (lead runs this)
echo ".worktrees/" >> .gitignore
git add .gitignore && git commit -m "chore(icarus): add .worktrees to gitignore"

# infra agent (Waves 0–2) — works directly on dev branch, no worktree needed
# (sole agent, no conflict risk)

# After Wave 2, create worktrees for parallel agents:
git worktree add .worktrees/ts-dev -b feat/ts-dev
git worktree add .worktrees/py-data -b feat/py-data
git worktree add .worktrees/py-strat -b feat/py-strat
```

### Per-Feature Workflow (each agent)

Each agent works in its own worktree directory and commits to its own branch:

1. Agent starts in its worktree (e.g., `.worktrees/ts-dev/`)
2. Invokes `coding-session` skill
3. Implements ONE feature, commits to its branch (e.g., `feat/ts-dev`)
4. Notifies lead: "LISTEN-001 done, ready for merge"

### Lead Merge Workflow (after each feature or wave)

```bash
# In the main working directory (on dev branch):
git merge --no-ff feat/ts-dev -m "merge(icarus): LISTEN-001 from ts-dev"
git merge --no-ff feat/py-data -m "merge(icarus): DATA-001 from py-data"
git merge --no-ff feat/py-strat -m "merge(icarus): MON-001 from py-strat"

# After merge, update each worktree to include merged changes:
cd .worktrees/ts-dev && git merge dev
cd .worktrees/py-data && git merge dev
cd .worktrees/py-strat && git merge dev
```

### Why Worktrees

| Problem | Worktree Solution |
|---------|-------------------|
| `features.json` concurrent edits | Each agent edits in its own worktree copy. Lead merges. |
| `progress.txt` append collisions | Each agent appends in its own copy. Merges cleanly (append-only). |
| `py-engine/main.py` shared edits | Agents edit separate sections. Lead resolves conflicts at merge. |
| `verify.sh` parallel execution | Each worktree has its own copy — no process contention. |
| Broken build blocks all agents | Isolated — one agent's broken state doesn't affect others. |

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

### Cross-Agent Dependencies

These features depend on another agent's work. Implement with **mocked interfaces** first; real integration validated at TEST-001.

| Feature | Agent | Depends On | Resolution |
|---------|-------|------------|------------|
| STRAT-001 (Aave lending, W7) | py-strat | EXEC-004 (Aave adapter, W8, ts-dev) | py-strat generates order schema; mock executor response. Real e2e deferred to TEST-001. |
| PORT-002 (position tracker, W6) | py-strat | EXEC-010 (event reporter, W10, ts-dev) | Mock `execution:results` events from Redis. Real events arrive after W10. |
| HARNESS-002 (startup recovery, W7) | py-data | TS executor functional | Test recovery with mocked Redis messages. Full recovery test at TEST-001. |
| RISK-001 (drawdown breaker, W9) | py-strat | PORT-002 (W6, py-strat) + DATA-001 (W3, py-data) | PORT-002 is same agent (sequential). DATA-001 price feeds available via merged dev branch. |
| RISK-007 (oracle guard, W9) | py-data | DATA-001 (price feeds, W3, py-data) | Same agent, sequential — no issue. Note dependency for clarity. |

**Rule:** When a feature has a cross-agent dependency, the implementing agent MUST:
1. Define the interface/mock in their worktree
2. Write tests against the mock
3. Add a `# TODO(TEST-001): validate with real {dependency}` comment at integration points

## Wave Execution Plan (P1: 31 features)

### Wave 0–2: Infrastructure (infra agent, sequential, on dev branch)

| Wave | Feature | Agent |
|------|---------|-------|
| 0 | **INFRA-001** — Both services build/run, Dockerfiles, README | infra |
| 1 | **INFRA-003** — Schema validation wired in both services (ajv + jsonschema) | infra |
| 2a | **INFRA-002** — Redis pub/sub, streams, TTL pruning, cache layer | infra |
| 2b | **INFRA-004** — Docker Compose hot-reload, log aggregation, env injection | infra |

After Wave 2: **shutdown `infra` agent**. Lead creates worktrees off `dev`, spawns `ts-dev`, `py-data`, `py-strat`.

### Waves 3–12: Parallel Implementation (3 agents in worktrees)

| Wave | ts-dev | py-data | py-strat | Cross-Deps |
|------|--------|---------|----------|------------|
| 3 | LISTEN-001 (WebSocket mgr) | DATA-001 (price feeds) | MON-001 (structured logging) | — |
| 4 | EXEC-001 (TX builder) | DATA-002 (gas monitor) | HARNESS-001 (state persistence) | — |
| 5 | EXEC-002 (Smart Wallet) | DATA-003 (DeFi metrics) | PORT-001 (portfolio allocator) | — |
| 6 | LISTEN-002 (event publisher) | DATA-004 (reconciliation) | PORT-002 (position tracker) | PORT-002 mocks execution:results |
| 7 | EXEC-003 (Flashbots) | HARNESS-002 (startup recovery) | STRAT-001 (Aave lending) | STRAT-001 mocks Aave adapter; HARNESS-002 mocks TS executor |
| 8 | EXEC-004 (Aave adapter) | HARNESS-004 (diagnostic mode) | STRAT-007 (lifecycle mgr) | — |
| 9 | RISK-006 (allowlist) | RISK-007 (oracle guard) | RISK-001 (drawdown breaker) | — |
| 10 | EXEC-010 (event reporter) | RISK-008 (exposure limits) | RISK-002 (position loss limit) | — |
| 11 | *idle — assist TEST-001 prep* | *idle — assist TEST-001 prep* | RISK-003 (gas spike breaker) | — |
| 12 | *TEST-001 TS harness* | *TEST-001 data pipeline tests* | RISK-004 (TX failure monitor) | — |

**Idle agent utilization (Waves 11–12):** ts-dev and py-data begin TEST-001 preparation in their worktrees instead of sitting idle. ts-dev writes the TS-side test harness and Sepolia fixtures. py-data writes data pipeline e2e test scaffolding. This reduces Wave 13 to a merge + final validation rather than a full implementation wave.

### Wave 13: Integration (TEST-001)

TEST-001 is a collaborative feature. The lead coordinates:

| Sub-task | Agent | Scope |
|----------|-------|-------|
| TS test harness (Sepolia TX lifecycle) | ts-dev | Mock Alchemy responses, test TX build → submit → confirm → report |
| Data pipeline e2e (price → cache → strategy input) | py-data | Test DATA-001 through HARNESS-002 recovery path |
| Strategy e2e (signal → order → result → portfolio update) | py-strat | Test STRAT-001 through full decision cycle with mocked chain |
| Cross-service integration | lead (merges all) | Merge all worktrees, run full `verify.sh`, docker compose up, e2e smoke test |

**TEST-001 is complete when:**
- [ ] All three sub-harnesses pass independently in their worktrees
- [ ] Lead merges all branches to `dev` cleanly
- [ ] `verify.sh` exits 0 on merged `dev`
- [ ] `docker compose up` starts all services, Redis messages flow between them
- [ ] At least one full signal→order→execution→result cycle completes on Sepolia (or mock)

**Total: ~13 wave-sessions for 31 P1 features** (vs 31 sequential — ~2.4x improvement)

## Agent Assignment Rules

Each agent invokes the **`coding-session` skill** per feature and follows its ritual exactly:
1. Invoke `coding-session` skill (loads the full ritual)
2. Read `progress.txt` in their worktree
3. `verify.sh` before coding (baseline in their worktree)
4. Implement ONE feature
5. `verify.sh` after coding (must exit 0)
6. Update `features.json` → `passes: true`
7. Append to `progress.txt`
8. Commit to their feature branch: `feat(icarus): description`
9. Notify lead via SendMessage: "FEATURE-ID done, ready for merge"

**Lead receives notification → merges to dev → updates worktrees → assigns next wave.**

## File Ownership (Conflict Prevention)

With worktrees, each agent has an isolated copy. Ownership still matters for **merge conflict resolution** — the owner's version wins.

| Directory | Owner | Merge Rule |
|-----------|-------|------------|
| `ts-executor/` | ts-dev | ts-dev's version always wins |
| `py-engine/data/` | py-data | py-data's version wins |
| `py-engine/monitoring/` | py-data | py-data's version wins |
| `py-engine/strategies/` | py-strat | py-strat's version wins |
| `py-engine/risk/` | py-strat | py-strat's version wins |
| `py-engine/portfolio/` | py-strat | py-strat's version wins |
| `py-engine/main.py` | py-data (startup), py-strat (registration) | Lead manually merges — both agents may add imports/registrations |
| `shared/schemas/` | Read-only after INFRA-003 | No modifications without lead approval |
| `harness/features.json` | Any agent (own features only) | Auto-mergeable — agents touch different JSON entries |
| `harness/progress.txt` | Any agent (append-only) | Auto-mergeable — append-only means no conflicts |

## Verification

### Per-Feature (agent responsibility)
Each agent runs `verify.sh` in their worktree before and after implementation. Since worktrees are isolated, no contention.

### Per-Wave (lead responsibility)
After merging all agent branches for a wave:
1. Lead runs `harness/verify.sh` on merged `dev` to confirm all passing
2. Lead checks `features.json` — completed features should show `passes: true`
3. Lead reviews `progress.txt` for completeness
4. Lead runs `docker compose up` to smoke-test inter-service communication (Waves 6+ when both services have real logic)
5. Lead updates all worktrees with merged `dev`

### P1 Completion (TEST-001)
After all P1 features merged: run TEST-001 Sepolia integration suite end-to-end.

## Risk Mitigation

| Risk | Impact | Mitigation |
|------|--------|------------|
| Infrastructure fails (Wave 0–2) | All blocked | infra agent works solo, no wasted resources. Lead debugs immediately. |
| Schema mismatch between services | Runtime failures | INFRA-003 establishes validation first. Schemas are read-only after. |
| Merge conflicts on `py-engine/main.py` | Blocked merge | Lead manually resolves. Agents add to different sections (imports vs registrations). |
| verify.sh fails post-merge | Wave stalls | Lead identifies which agent's changes broke it, reverts that merge, agent fixes. |
| Testnet unavailable | Chain features can't test | Unit tests use mocked Alchemy responses. Real testnet deferred to TEST-001. |
| Cross-agent dep not mocked properly | Integration fails at TEST-001 | Mocks defined against shared schemas. Schema conformance guarantees compatibility. |
| Worktree diverges too far from dev | Painful merge | Lead merges after every wave (not batched). Frequent small merges > rare big ones. |

## P2/P3 Outline (High Level)

**P2 (16 features)** — Same team, same worktree structure, adjusted roles:
- ts-dev: LISTEN-003, EXEC-005/006/009 (L2 + Uniswap + Lido adapters)
- py-data: DATA-005, INFRA-006 (PostgreSQL), MON-002/003/004
- py-strat: STRAT-002/003/004, PORT-003, RISK-005, HARNESS-003, AI-001
- Estimate: ~6–8 waves

**P3 (11 features)** — Consider adding a `solana-dev` agent (new worktree):
- Solana chain: LISTEN-004, EXEC-008, DATA-006
- Flash loans: EXEC-007, STRAT-005
- Advanced: STRAT-006, REPORT-001/002, TEST-002/003
- Estimate: ~5–6 waves

## Critical Files

- `harness/features.json` — Feature tracking (all agents read/update in their worktrees)
- `harness/verify.sh` — Verification gate (run before/after every feature)
- `harness/progress.txt` — Session handoff log (append-only)
- `shared/schemas/*.schema.json` — Inter-service contracts (read-only after INFRA-003)
- `ts-executor/src/redis/client.ts` — Redis client (INFRA-002 expands this)
- `py-engine/main.py` — Python entry point (features register into startup — merge-sensitive)
- `docker-compose.yml` — Service orchestration (INFRA-004 finalizes)
