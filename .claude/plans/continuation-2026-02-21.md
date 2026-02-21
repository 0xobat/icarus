# Icarus P1 Continuation Plan — 2026-02-21

## Status at Pause

**Branch:** `dev` at `837f9f9` (14 new features on worktree branches, ready to merge)
**Tests:** 671 total (172 TS + 499 Python), 9 skipped (Redis offline)
**Features:** 30/31 P1 implemented (14 new this session), only TEST-001 remains

## What Was Accomplished

Parallel team execution with 3 agents completed all 14 remaining implementation features:

### feat/ts-dev — 5 commits (87 new tests)
| Commit | Feature |
|--------|---------|
| `0ad5fee` | LISTEN-002 — Market event publisher |
| `457b989` | EXEC-003 — Flashbots Protect |
| `b6c9e8a` | EXEC-004 — Aave V3 adapter |
| `e257a5b` | EXEC-010 — Event reporter |
| `4e96a60` | RISK-006 — Contract allowlist |

### feat/py-strat — 9 commits (244 new tests)
| Commit | Feature | Original Agent |
|--------|---------|----------------|
| `f21ed55` | STRAT-007 — Strategy lifecycle manager | py-strat |
| `dc1c600` | RISK-001 — Drawdown circuit breaker | py-strat |
| `e24a7a1` | HARNESS-002 — Startup recovery | py-data |
| `a9a51fd` | HARNESS-004 — Diagnostic mode | py-data |
| `e8ae782` | RISK-007 — Oracle manipulation guard | py-data |
| `a905070` | RISK-002 — Per-position loss limit | py-strat |
| `7b9054b` | RISK-008 — Exposure limit enforcement | py-data |
| `217c40d` | RISK-003 — Gas spike circuit breaker | py-strat |
| `9d3ed52` | RISK-004 — TX failure rate monitor | py-strat |

**Note:** py-data agent worked in the py-strat worktree. `feat/py-data` branch is empty.

### Remaining (1)
| ID | Subsystem | Dependencies |
|----|-----------|-------------|
| TEST-001 | Sepolia integration | all above |

## Next Session: Merge + TEST-001

### Step 1: Merge Worktrees into dev

```bash
cd /home/heresy/Documents/Projects/crypto/icarus

# Merge TS features
git merge --no-ff feat/ts-dev -m "merge(icarus): LISTEN-002, EXEC-003, EXEC-004, EXEC-010, RISK-006 from ts-dev"

# Merge all Python features (both py-data and py-strat work is on this branch)
git merge --no-ff feat/py-strat -m "merge(icarus): STRAT-007, RISK-001-004, HARNESS-002, HARNESS-004, RISK-007, RISK-008 from py-strat"
```

Potential conflicts:
- `py-engine/risk/__init__.py` — both agents may have created it
- No other conflicts expected (ts-dev touches only `ts-executor/`, py-strat touches only `py-engine/`)

### Step 2: Update features.json

Mark these 14 features as `passes: true`:
LISTEN-002, EXEC-003, EXEC-004, EXEC-010, RISK-006, HARNESS-002, HARNESS-004, STRAT-007, RISK-001, RISK-002, RISK-003, RISK-004, RISK-007, RISK-008

### Step 3: Verify

```bash
bash harness/verify.sh   # must exit 0
```

### Step 4: TEST-001 — Sepolia Integration

The final feature. Implement end-to-end tests:
- Full flow: detect opportunity → evaluate → approve → execute → confirm → log
- Aave supply/withdraw cycle on Sepolia (mocked)
- Startup recovery after simulated crash
- Circuit breakers trigger on simulated thresholds
- Redis schema validation catches malformed messages

### Step 5: Finalize

- Update features.json: TEST-001 passes: true
- Update progress.txt
- Commit: `feat(icarus): complete P1 — 31/31 features passing`

## Done Criteria

P1 complete when:
- All 31 features show `passes: true` in features.json
- `harness/verify.sh` exits 0 on `dev`
- `docker compose up` starts all services communicating
- TEST-001 validates at least one full cycle end-to-end

## Worktree Cleanup

After merge, worktrees can be removed:
```bash
git worktree remove .worktrees/ts-dev
git worktree remove .worktrees/py-data
git worktree remove .worktrees/py-strat
```
