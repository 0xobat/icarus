# Icarus — Agent Instructions

Autonomous multi-strategy DeFi bot. Python (brain) handles all analysis and decisions. TypeScript (hands) handles all blockchain interaction. Communication via Redis.

## Architecture

- `ts-executor/` — TypeScript service (chain listeners, TX execution, event reporting)
- `py-engine/` — Python service (data pipeline, strategies, risk management, portfolio)
- `shared/schemas/` — JSON schemas defining the Redis message contracts between services
- `docker-compose.yml` — Redis + both services

**Design principle:** Python owns all decisions. TypeScript owns all chain interactions. Neither crosses into the other's domain.

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

## Commit Messages

```
feat(icarus): description
fix(icarus): description
```
