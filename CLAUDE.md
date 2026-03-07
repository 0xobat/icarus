# Icarus — Agent Instructions

Autonomous multi-strategy DeFi bot. Claude is the decision engine at two levels:

1. **Compile time** — Claude reads `STRATEGY.md` and generates Python strategy classes
2. **Runtime** — Python synthesizes market data into insights, Claude API reasons over them to produce trading decisions

Strategies are data (`STRATEGY.md`), not hardcoded logic. Adding a strategy means editing a markdown file.

## Architecture

- `ts-executor/` — TypeScript service (chain listeners, TX execution, protocol adapters)
- `py-engine/` — Python service (data pipeline, AI reasoning, risk management, portfolio)
- `shared/schemas/` — JSON schemas defining Redis message contracts between services
- `STRATEGY.md` — Human-authored strategy definitions (source of truth)
- `docker-compose.yml` — Redis + PostgreSQL + both services

**Design principle:** Python synthesizes data and translates Claude's decisions into orders. TypeScript owns all chain interactions. Neither crosses into the other's domain.

### Key entry points

- `py-engine/main.py` — `DecisionLoop` class: enrich → synthesize → decide → risk gate → emit orders
- `ts-executor/src/index.ts` — Bootstraps listeners, wallet, adapters, TX builder; subscribes to `execution:orders`

### Decision fast-path

Simple threshold crossings (single clear signal, no competing strategies) bypass the Claude API entirely. Claude API is invoked for ambiguous conditions, competing signals, multi-strategy reasoning.

## Strategy Tiers

- **Tier 1 — Low Risk (50–60%):** Lending optimization (Aave), liquid staking (Lido)
- **Tier 2 — Medium Risk (25–35%):** Concentrated liquidity (Uniswap V3), yield farming
- **Tier 3 — Higher Risk (10–20%):** Flash loan arbitrage, rate arbitrage

Chains: Ethereum Mainnet (Sepolia testnet), L2s (Arbitrum, Base)

## Risk Management

### Circuit Breakers

| Trigger | Threshold | Action |
|---------|-----------|--------|
| Portfolio drawdown | >20% from peak | Halt all, unwind to stables |
| Single-position loss | >10% of position | Close position, 24h cooldown |
| Gas spike | >3x 24h average | Pause non-urgent ops |
| TX failure rate | >3 failures/hour | Pause execution, diagnostic mode |
| Protocol TVL drop | >30% in 24h | Withdraw from affected protocol |

### Exposure Limits

- Max 40% in any single protocol
- Max 60% in any single asset (excluding stablecoins)
- Min 15% in stablecoins/liquid reserves at all times

## Running

```bash
bash harness/init.sh
docker compose up -d redis
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
- Sepolia testnet only until P2
- Risk limits are environment variables, not hardcoded
- One strategy adjustment per decision cycle
- Risk gate is non-negotiable — all decisions pass through circuit breakers before execution
- Strategy status tracking: active / paused / evaluating / retired

## Documentation

- **Python:** Google-style docstrings on modules, public classes, public methods. `Args:`, `Returns:`, `Raises:` when non-obvious.
- **TypeScript:** JSDoc `/** */` on exported classes, functions, public methods. `@param`, `@returns`, `@throws` when non-obvious.

## Commit Messages

```
feat(icarus): description
fix(icarus): description
```
