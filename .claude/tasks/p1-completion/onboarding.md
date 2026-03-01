# Onboarding: P1 Completion — 26 Remaining Features

**Task ID:** p1-completion
**Date:** 2026-02-25
**Branch:** `dev` (31/57 P1 features passing, 732 tests)

---

## Project Summary

Icarus is an autonomous multi-strategy DeFi trading bot with two services:
- **ts-executor/** — TypeScript: chain listeners, TX execution, protocol adapters (viem, Alchemy WS, ioredis)
- **py-engine/** — Python: data pipeline, AI decision engine, strategies, risk management, portfolio (pandas, numpy, redis, jsonschema)

Communication is Redis pub/sub with 3 channels (`market:events`, `execution:orders`, `execution:results`), all schema-validated via shared JSON schemas in `shared/schemas/`.

Claude API is used at two levels:
1. **Compile time** — reads `strategy.md` → generates Python strategy classes
2. **Runtime** — reasons over structured insights → produces trading decisions

---

## What's Already Built (31 features passing)

### Infrastructure
- **INFRA-001**: Project scaffolding (monorepo, Docker Compose, both services)
- **INFRA-002**: Redis communication layer (pub/sub, streams, cache, schema validation)
- **INFRA-003**: Schema validation (ajv TS, jsonschema Python)
- **INFRA-004**: Docker Compose polish (hot-reload, dev targets)
- **INFRA-005**: Linting (ruff D rules, eslint-plugin-jsdoc)

### Chain Listeners
- **LISTEN-001**: Alchemy WebSocket manager (reconnect, backpressure, health monitoring)
- **LISTEN-002**: Market event publisher (dedup, latency stats)

### Execution
- **EXEC-001**: viem transaction builder (nonce mgmt, retry, gas ceiling)
- **EXEC-002**: Smart Wallet (ERC-4337, spending limits, contract allowlist)
- **EXEC-003**: Flashbots Protect (private TX routing)
- **EXEC-004**: Aave V3 adapter (supply/withdraw, reserve queries)
- **EXEC-010**: Event reporter (TX result publishing)

### Data Pipeline
- **DATA-001**: Price feeds
- **DATA-002**: Gas monitor
- **DATA-003**: DeFi protocol metrics (Aave, Uniswap, Lido)
- **DATA-004**: On-chain reconciliation

### Strategies
- **STRAT-001**: Aave lending optimization (evaluate, rotate, generate orders)
- **STRAT-007**: Lifecycle manager (evaluating→active→paused→retired)

### Risk Management
- **RISK-001**: Drawdown circuit breaker (15% warning, 20% halt)
- **RISK-002**: Per-position loss limit (10% close + 24h cooldown)
- **RISK-003**: Gas spike breaker (3x average = pause)
- **RISK-004**: TX failure rate monitor (>3/hour = diagnostic mode)
- **RISK-006**: Contract allowlist
- **RISK-007**: Oracle manipulation guard (multi-source, deviation rejection)
- **RISK-008**: Exposure limit enforcement (40% protocol, 60% asset, 15% stablecoin)

### Portfolio
- **PORT-001**: Capital allocator (tier-based allocation)
- **PORT-002**: Position tracker (open/close, P&L, queries)

### Harness
- **HARNESS-001**: State persistence (atomic JSON, schema versioning)
- **HARNESS-002**: Startup recovery (Redis replay, reconciliation)
- **HARNESS-004**: Diagnostic mode (auto-trigger on failures)

### Testing
- **TEST-001**: Integration test suite (e2e lifecycle, circuit breakers, schema validation, startup recovery)

---

## What Remains (26 features)

### Tier 0: Foundation (must build first)
These are infrastructure and cross-cutting concerns that other features depend on.

| ID | Category | Description | Service |
|----|----------|-------------|---------|
| **INFRA-006** | infra | PostgreSQL database (trade history, snapshots, migrations) | py-engine |
| **INFRA-007** | infra | Main decision loop (wires all modules, lifecycle orchestration) | both |
| **STRAT-008** | strategies | Strategy ingestion (parse strategy.md → structured specs) | py-engine |
| **AI-001** | ai | Claude API decision engine (runtime reasoning, cost tracking) | py-engine |
| **AI-002** | ai | Strategy code-gen pipeline (strategy.md → Python classes) | py-engine |
| **AI-003** | ai | Insight synthesis pipeline (data → compressed snapshots) | py-engine |

### Tier 1: Protocol Adapters (TS executor extensions)
Independent of each other; can be parallelized.

| ID | Category | Description | Service |
|----|----------|-------------|---------|
| **EXEC-005** | functional | Uniswap V3 adapter (mint/burn/collect fees/pool queries) | ts-executor |
| **EXEC-006** | functional | Lido staking adapter (stake/wrap/unwrap/APY query) | ts-executor |
| **EXEC-007** | functional | Flash loan executor (Aave V3 flash + multi-swap callback) | ts-executor |
| **EXEC-009** | functional | L2 adapters — GMX (Arbitrum) + Aerodrome (Base) | ts-executor |

### Tier 2: L2 Infrastructure
Can be parallelized with Tier 1.

| ID | Category | Description | Service |
|----|----------|-------------|---------|
| **LISTEN-003** | functional | L2 chain listeners (Arbitrum, Base WebSocket, per-chain config) | ts-executor |
| **DATA-005** | functional | L2 data pipeline (L2 gas models, GMX/Aerodrome metrics) | py-engine |

### Tier 3: Strategies (depend on STRAT-008 + AI-002 + respective adapters)
These are Claude-generated strategy classes. Each defined in strategy.md.

| ID | Tier | Description | Adapter Dep |
|----|------|-------------|-------------|
| **STRAT-002** | T1 | Liquid staking (Lido stETH→wstETH, yield deploy) | EXEC-006 |
| **STRAT-003** | T2 | Uniswap V3 concentrated liquidity (dynamic range) | EXEC-005 |
| **STRAT-004** | T2 | Yield farming auto-compound | EXEC-005 or L2 |
| **STRAT-005** | T3 | Flash loan arbitrage (atomic cross-DEX) | EXEC-007 |
| **STRAT-006** | T3 | Lending rate arbitrage (cross-protocol) | EXEC-004 (done) |

### Tier 4: Portfolio & Risk (depend on strategies existing)

| ID | Category | Description | Service |
|----|----------|-------------|---------|
| **PORT-003** | functional | Rebalancing engine (drift detection, gas-aware, 1 adj/cycle) | py-engine |
| **RISK-005** | security | Protocol TVL monitor (>30% drop = withdraw, DeFi Llama + on-chain) | py-engine |

### Tier 5: Monitoring & Reporting (depend on INFRA-006 for persistence)

| ID | Category | Description | Service |
|----|----------|-------------|---------|
| **MON-002** | observability | Discord alerts (circuit breaker, daily summary, approval buttons) | py-engine |
| **MON-003** | observability | Performance dashboard (P&L, Sharpe, drawdown, gas tracking → PG) | py-engine |
| **MON-004** | observability | Anomaly detection (gas anomalies, balance changes, perf degradation) | py-engine |
| **HARNESS-003** | functional | Human-in-the-loop (Discord approval gates, timeout, emergency override) | py-engine |
| **REPORT-001** | functional | Tax reporting (ACB method, CSV export, DeFi events) | py-engine |
| **REPORT-002** | functional | P&L attribution (by strategy/protocol/chain/period, CSV/JSON) | py-engine |

### Tier 6: ML (independent)

| ID | Category | Description | Service |
|----|----------|-------------|---------|
| **TEST-003** | testing | ML gas prediction model (1h/4h/24h, retrain weekly, fallback) | py-engine |

---

## Logical Dependency Graph

```
                          INFRA-006 (PostgreSQL)
                         /          |           \
                    MON-003    REPORT-001    REPORT-002
                    MON-004    HARNESS-003

            STRAT-008 (parse strategy.md)
               |
            AI-002 (code-gen pipeline)
            AI-003 (insight synthesis)
            AI-001 (decision engine)
               |
         ┌─────┴──────┬──────────┬──────────┐
      STRAT-002    STRAT-003  STRAT-004  STRAT-005  STRAT-006
      (needs       (needs     (needs     (needs     (needs
       EXEC-006)   EXEC-005)  adapters)  EXEC-007)  EXEC-004✓)

            INFRA-007 (main decision loop — needs AI-001, risk, portfolio)
               |
            PORT-003 (rebalancing — needs working portfolio system)
            RISK-005 (TVL monitor — needs DeFi metrics)

      LISTEN-003 ←→ DATA-005 ←→ EXEC-009 (L2 infra — independent chain)

      TEST-003 (ML gas — fully independent)
      MON-002 (Discord — independent, just needs discord.py)
```

---

## Implementation Patterns

### Python Strategy Pattern
```python
@dataclass
class MyStrategyConfig:
    threshold: Decimal = Decimal("0.005")

class MyStrategy:
    def __init__(self, allocator: PortfolioAllocator, tracker: PositionTracker, config: MyStrategyConfig):
        self.allocator = allocator
        self.tracker = tracker
        self.config = config
        self.status = "evaluating"

    def evaluate(self, market_data) -> list:
        """Return ranked candidates."""

    def should_act(self, current, best) -> bool:
        """Determine if action threshold is met."""

    def generate_orders(self, targets, correlation_id=None) -> list[dict]:
        """Return execution:orders-compliant dicts."""
```

### TypeScript Adapter Pattern
```typescript
export class MyProtocolAdapter {
    constructor(opts: { publicClient, walletClient?, flashbotsManager?, ... }) {}
    async query(...): Promise<Data> {}
    async execute(order): Promise<Result> {}
}
```

### Test Patterns
- **Python**: pytest, helper factories (`_make_strategy()`, `_make_market()`), test classes, `Decimal` for precision
- **TypeScript**: vitest, mock factories (`createMockPublicClient()`), `vi.fn()`, `describe/it/expect`

### Config Pattern
- Risk thresholds from env vars with code defaults
- `@dataclass` configs in Python
- Options interfaces in TypeScript
- `.env.example` documents all vars

### Logging Pattern
- Structured JSON: `timestamp`, `service`, `event`, `message`, `data`, `correlationId`
- `get_logger(name)` in Python
- Console-based in TypeScript

---

## Key Files to Know

| File | Purpose |
|------|---------|
| `harness/features.json` | Feature inventory (update `passes` on completion) |
| `harness/progress.txt` | Append-only session log |
| `harness/verify.sh` | Full test suite runner |
| `py-engine/main.py` | Python entry point (needs INFRA-007 wiring) |
| `ts-executor/src/index.ts` | TS entry point (needs INFRA-007 wiring) |
| `py-engine/ai/__init__.py` | Empty — AI module placeholder |
| `py-engine/strategies/aave_lending.py` | Reference strategy implementation |
| `py-engine/strategies/lifecycle_manager.py` | Strategy state machine |
| `py-engine/portfolio/allocator.py` | Capital allocation with tier/protocol/asset limits |
| `py-engine/risk/drawdown_breaker.py` | Reference circuit breaker |
| `shared/schemas/*.schema.json` | Redis message contracts |
| `docker-compose.yml` | Redis + both services |
| `.env.example` | All environment variables |
| `docs/prd.md` | Full PRD with architecture, phases, risk matrix |

---

## Parallelization Strategy

Previous sessions used 3 parallel worktree agents effectively:
- **ts-dev**: All TypeScript features (ts-executor/)
- **py-data**: Python data/infrastructure features
- **py-strat**: Python strategies/risk/portfolio features

For the remaining 26 features, optimal parallelization:

**Wave 1** (Foundation — 6 features, parallel):
- Agent A (py-infra): INFRA-006, INFRA-007
- Agent B (py-ai): AI-001, AI-003, STRAT-008
- Agent C (ts-dev): LISTEN-003, EXEC-005, EXEC-006

**Wave 2** (Protocol + Strategy — 10 features, parallel):
- Agent A (ts-dev): EXEC-007, EXEC-009
- Agent B (py-strat): STRAT-002, STRAT-003, STRAT-004, STRAT-005, STRAT-006
- Agent C (py-ai): AI-002 (needs STRAT-008 from Wave 1)

**Wave 3** (Portfolio + Risk + L2 — 4 features, parallel):
- Agent A (py-engine): PORT-003, RISK-005
- Agent B (py-data): DATA-005

**Wave 4** (Monitoring + Reporting — 6 features, parallel):
- Agent A (py-mon): MON-002, MON-004, HARNESS-003
- Agent B (py-report): MON-003, REPORT-001, REPORT-002

**Wave 5** (ML — 1 feature):
- Agent A: TEST-003

---

## Dependencies to Install

### Python (not yet in pyproject.toml)
- `anthropic` — Claude API client (for AI-001, AI-002)
- `asyncpg` or `psycopg` — PostgreSQL driver (for INFRA-006)
- `alembic` — Database migrations (for INFRA-006)
- `discord.py` — Discord bot/webhooks (for MON-002, HARNESS-003)
- `scikit-learn` — ML gas prediction (for TEST-003)

### TypeScript (not yet in package.json)
- No new dependencies expected — viem covers all Ethereum interactions

---

## Verification

```bash
# Run full test suite
bash harness/verify.sh

# Check specific service
cd ts-executor && pnpm test
cd py-engine && uv run pytest tests/ --tb=short -q

# Check feature count
python3 -c "import json; f=json.load(open('harness/features.json')); print(f'{sum(1 for x in f if x[\"phase\"]==\"P1\" and x[\"passes\"])}/57 P1 features passing')"
```

Done criteria: 57/57 P1 features passing, verify.sh exits 0.

---

## Lessons from Previous Sessions

1. **Worktree scoping**: py-data agent accidentally worked in py-strat worktree — ensure each agent gets its own isolated worktree
2. **Service separation**: TS touches only `ts-executor/`, Python touches only `py-engine/` — merges are clean
3. **Schema compliance**: All Redis messages must validate against shared schemas — test this at boundaries
4. **Ruff strictness**: Python linting is strict (D rules for docstrings, import sorting) — agents must run `ruff check` before committing
5. **One feature per session rule**: The harness convention says one feature per session, but previous sessions parallelized effectively with worktree agents doing multiple features per branch
