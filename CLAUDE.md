# Icarus — Agent Instructions

Autonomous multi-strategy DeFi bot. Claude is the decision engine at two levels:

1. **Compile time** — Claude reads `strategy.md` and generates Python strategy classes
2. **Runtime** — Python synthesizes market data into insights, Claude API reasons over them to produce trading decisions

Strategies are data (`strategy.md`), not hardcoded logic. Adding a strategy means editing a markdown file.

## Architecture

- `ts-executor/` — TypeScript service (chain listeners, TX execution, event reporting)
- `py-engine/` — Python service (data pipeline, insight synthesis, risk management, portfolio)
- `py-engine/ai/` — Claude API client, decision engine, strategy code-gen
- `shared/schemas/` — JSON schemas defining the Redis message contracts between services
- `strategy.md` — Human-authored strategy definitions (source of truth for all strategies)
- `docker-compose.yml` — Redis + both services

**Design principle:** Python synthesizes data and translates Claude's decisions into orders. TypeScript owns all chain interactions. Neither crosses into the other's domain.

## Decision Loop

1. **Ingest** — TS publishes chain events to `market:events`
2. **Enrich** — Python crunches raw data into structured insights
3. **Reason** — Insights + strategy specs sent to Claude API; returns structured decisions
4. **Risk gate** — Decisions pass through circuit breakers and exposure limits
5. **Execute** — Approved decisions become `execution:orders` sent to TS
6. **Report** — TS publishes results to `execution:results`, Python updates portfolio

For simple deterministic situations, the strategy class decides without Claude API. Claude API is invoked when reasoning is needed — competing signals, ambiguous conditions, multi-step rebalancing.

## Strategy Tiers

- **Tier 1 — Low Risk (50–60%):** Lending optimization (Aave), liquid staking (Lido)
- **Tier 2 — Medium Risk (25–35%):** Concentrated liquidity (Uniswap V3), yield farming
- **Tier 3 — Higher Risk (10–20%):** Flash loan arbitrage, rate arbitrage

Chains: Ethereum Mainnet (Sepolia testnet), L2s (Arbitrum, Base)

## Risk Management

### Circuit Breakers

| Trigger | Threshold | Action |
|---------|-----------|--------|
| Portfolio drawdown | >20% from peak | Halt all, unwind to stables, alert |
| Single-position loss | >10% of position | Close position, 24h cooldown |
| Gas spike | >3x 24h average | Pause non-urgent ops |
| TX failure rate | >3 failures/hour | Pause execution, diagnostic mode |
| Protocol TVL drop | >30% in 24h | Withdraw from affected protocol |

### Exposure Limits

- Max 40% in any single protocol
- Max 60% in any single asset (excluding stablecoins)
- Min 15% in stablecoins/liquid reserves at all times

### Human-in-the-Loop

- Trades >15% of portfolio require confirmation
- New protocol deployment requires owner approval
- New strategy tier activation requires explicit approval
- Emergency override via Discord: pause all, force-unwind, withdraw

## Running

```bash
# Prerequisites: docker, pnpm, uv
bash harness/init.sh

# Start Redis (required for both services)
docker compose up -d redis

# TS service
cd ts-executor && pnpm dev

# Python service
cd py-engine && uv run python main.py
```

## Testing

```bash
# TypeScript
cd ts-executor && pnpm test

# Python
cd py-engine && uv run pytest tests/ --tb=short -q

# Full verification
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
- Environment: Sepolia testnet in Phase 1 — no mainnet until P2
- Risk limits are environment variables, not hardcoded
- One strategy adjustment per decision cycle
- Risk gate is non-negotiable — all decisions pass through circuit breakers before execution
- Clean state after every action — updated state file, logs, and monitoring before next op
- Strategy status tracking: active / paused / evaluating / retired

## Documentation

- **Python:** Google-style docstrings. Required on modules, public classes, public methods/functions. Include `Args:`, `Returns:`, `Raises:` sections when non-obvious. Use imperative mood for summary lines. Test methods and `__init__.py` files are exempt.
- **TypeScript:** JSDoc `/** */` comments. Required on exported classes, exported functions, and public methods. Include `@param`, `@returns`, `@throws` when non-obvious. Test files are exempt.

## Commit Messages

```
feat(icarus): description
fix(icarus): description
```
