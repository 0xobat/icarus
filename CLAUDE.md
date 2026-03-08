# Icarus — Agent Instructions

Autonomous DeFi asset management bot. Strategies are defined in `STRATEGY.md` — the system executes them. Claude is the decision engine at runtime.

1. **Strategy authoring** — Human defines a strategy in `STRATEGY.md`, uses Claude Code to generate the Python class, drops it into `py-engine/strategies/`. Auto-discovered at startup.
2. **Runtime** — Python collects market data, runs strategy classes to produce reports. When actionable signals are present, Claude API reasons over all reports and portfolio state to produce trading decisions.

Strategies are data (`STRATEGY.md`), not hardcoded logic. Adding a strategy means editing the markdown file, generating the class with Claude Code, and dropping it in. Full design: `docs/system-design.md`.

## Architecture

- `ts-executor/` — TypeScript service (chain listeners, TX execution, encode-only protocol adapters)
- `py-engine/` — Python service (data pipeline, AI reasoning, risk management, portfolio)
- `shared/schemas/` — JSON schemas defining Redis message contracts between services
- `STRATEGY.md` — Human-authored strategy definitions (source of truth for what to trade)
- `docker-compose.yml` — Redis + PostgreSQL + both services

**Design principle:** Python owns all decisions, TypeScript owns all chain interactions. Neither crosses into the other's domain. Protocol adapters are encode-only pure functions (calldata in, no state).

### Key entry points

- `py-engine/main.py` — `DecisionLoop` class: enrich → synthesize → decide → risk gate → emit orders
- `ts-executor/src/index.ts` — Bootstraps listeners, wallet, adapters, TX builder; subscribes to `execution:orders`

## Risk Management

### Circuit Breakers

| Trigger | Threshold | Action |
|---------|-----------|--------|
| Portfolio drawdown | >20% from peak | Halt all, unwind to stables |
| Single-position loss | >10% of position | Close position, 24h cooldown |
| Gas spike | >3x 24h average | Pause non-urgent ops |
| TX failure rate | >3 failures/hour | Pause execution, enter hold mode |
| Protocol TVL drop | >30% in 24h | Withdraw from affected protocol |

Circuit breakers that can unwind (drawdown, single-position, TVL drop) operate on a **separate execution path** — they emit orders directly to Redis, bypassing the decision gate and Claude API. Orders use `CB:` prefix in the `strategy` field. Cooldowns tracked via Redis TTL keys.

### Hold Mode

Triggered when Claude API is unavailable. Tracked as `system_status: "normal" | "hold"` in Redis. No new positions; existing positions maintained; circuit breakers remain active; strategy evaluation continues.

### Exposure Limits

Per-strategy allocation limits are defined in `STRATEGY.md`. The framework enforces:
- Per-protocol and per-asset max allocation
- Minimum liquid reserve requirement
- Contract allowlist enforced by Safe on-chain guard module
- Risk limits are environment variables, not hardcoded

## Running

```bash
bash harness/init.sh
docker compose up -d redis postgres
cd ts-executor && pnpm dev
cd py-engine && uv run python main.py
```

## Testing

```bash
cd ts-executor && pnpm test
cd py-engine && uv run pytest tests/ --tb=short -q
bash harness/verify.sh
```

## Redis Channels

| Channel | Direction | Schema |
|---------|-----------|--------|
| `market:events` | TS → Python | `shared/schemas/market-events.schema.json` |
| `execution:orders` | Python → TS | `shared/schemas/execution-orders.schema.json` |
| `execution:results` | TS → Python | `shared/schemas/execution-results.schema.json` |

## Conventions

- All logs are structured JSON with: timestamp, service, event, correlationId
- All Redis messages validated against shared schemas at the boundary
- Risk limits are environment variables, not hardcoded
- One strategy adjustment per decision cycle
- Verification gate is non-negotiable — Claude's orders pass through circuit breakers, exposure limits, and schema validation before execution
- Strategy status: active / inactive

## Documentation

- **Python:** Google-style docstrings on modules, public classes, public methods. `Args:`, `Returns:`, `Raises:` when non-obvious.
- **TypeScript:** JSDoc `/** */` on exported classes, functions, public methods. `@param`, `@returns`, `@throws` when non-obvious.

## Commit Messages

```
feat(icarus): description
fix(icarus): description
```
