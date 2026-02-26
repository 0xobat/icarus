# Icarus

Autonomous multi-strategy DeFi bot. Python (brain) handles all analysis and decisions. TypeScript (hands) handles all blockchain interaction. Communication via Redis.

Sepolia testnet only.

## Prerequisites

- Docker with Docker Compose
- [pnpm](https://pnpm.io/installation) (v9+)
- [uv](https://docs.astral.sh/uv/) — Python package manager
- Node.js 22+ / Python 3.12+

## Setup

```bash
bash harness/init.sh
cp .env.example .env
# Fill in ALCHEMY_SEPOLIA_API_KEY, WALLET_PRIVATE_KEY
```

## Running

```bash
# All services
docker compose up

# Local dev (3 terminals)
docker compose up -d redis
cd ts-executor && pnpm dev
cd py-engine && uv run python main.py
```

## Testing

```bash
cd ts-executor && pnpm test          # 172 tests, vitest
cd py-engine && uv run pytest tests/ # 560 tests, pytest
bash harness/verify.sh               # both + schema checks
```

## Architecture

```
┌─────────────┐    Redis    ┌──────────────┐
│  py-engine   │◄──────────►│ ts-executor   │
│  (brain)     │            │ (hands)       │
│              │            │               │
│  strategies  │  market:   │  listeners    │
│  risk mgmt   │  events    │  tx builder   │
│  portfolio   │ ──────────►│  smart wallet │
│  data pipe   │            │  flashbots    │
│              │  exec:     │  protocol     │
│              │  orders    │  adapters     │
│              │◄───────────│               │
│              │  exec:     │               │
│              │  results   │               │
│              │───────────►│               │
└─────────────┘            └──────────────┘
```

All Redis messages are validated against JSON schemas in `shared/schemas/` at the boundary.

## Codebase

### `ts-executor/src/` — Chain interaction

| Module | What it does |
|--------|-------------|
| `listeners/websocket-manager.ts` | Alchemy WebSocket subscriptions with reconnect, backpressure |
| `listeners/event-normalizer.ts` | Raw chain events → normalized market event schema |
| `listeners/market-event-publisher.ts` | Publishes normalized events to Redis `market:events` |
| `execution/transaction-builder.ts` | Builds, signs, sends TXs via viem; nonce management, retries |
| `execution/flashbots-protect.ts` | MEV-protected TX submission via Flashbots |
| `execution/aave-v3-adapter.ts` | Aave V3 supply/withdraw/borrow encoding |
| `execution/event-reporter.ts` | Publishes TX results to Redis `execution:results` |
| `wallet/smart-wallet.ts` | ERC-4337 smart wallet: spending limits, allowlist, UserOp signing |
| `security/contract-allowlist.ts` | Rejects TXs to non-allowlisted contracts |
| `redis/client.ts` | Redis pub/sub, streams, cache with schema validation |
| `validation/schema-validator.ts` | AJV-based JSON schema validation |

### `py-engine/` — Decision engine

| Module | What it does |
|--------|-------------|
| `data/price_feed.py` | Price data ingestion and caching |
| `data/gas_monitor.py` | Gas price tracking |
| `data/defi_metrics.py` | Aave V3, Uniswap V3, Lido protocol metrics |
| `data/reconciliation.py` | On-chain vs local position reconciliation |
| `data/redis_client.py` | Redis pub/sub, streams, cache with schema validation |
| `strategies/aave_lending.py` | Aave lending optimization (supply rate, utilization) |
| `strategies/lifecycle_manager.py` | Strategy registration, activation, cooldowns |
| `portfolio/allocator.py` | Capital allocation across strategies |
| `portfolio/position_tracker.py` | Open/close positions, P&L tracking |
| `risk/drawdown_breaker.py` | Portfolio-level drawdown circuit breaker |
| `risk/position_loss_limit.py` | Per-position loss limit enforcement |
| `risk/gas_spike_breaker.py` | Pauses execution during gas spikes |
| `risk/tx_failure_monitor.py` | Consecutive TX failure rate detection |
| `risk/oracle_guard.py` | Oracle manipulation detection |
| `risk/exposure_limits.py` | Per-protocol and per-asset concentration limits |
| `harness/state_manager.py` | Persistent state across restarts |
| `harness/startup_recovery.py` | Graceful recovery after crash |
| `harness/diagnostic_mode.py` | Read-only diagnostic mode for debugging |
| `monitoring/logger.py` | Structured JSON logging |

### `shared/schemas/`

| Schema | Channel | Direction |
|--------|---------|-----------|
| `market-events.schema.json` | `market:events` | TS → Python |
| `execution-orders.schema.json` | `execution:orders` | Python → TS |
| `execution-results.schema.json` | `execution:results` | TS → Python |

## Environment

See `.env.example`. Key variables:

| Variable | Purpose |
|----------|---------|
| `ALCHEMY_SEPOLIA_API_KEY` | Alchemy API access |
| `WALLET_PRIVATE_KEY` | Sepolia testnet wallet |
| `REDIS_URL` | Redis connection (default `redis://localhost:6379`) |
| `MAX_DRAWDOWN_PERCENT` | Portfolio drawdown limit (default 20) |
| `GAS_SPIKE_MULTIPLIER` | Gas price circuit breaker threshold (default 3x) |
| `TX_FAILURE_RATE_THRESHOLD` | Consecutive TX failures before halt (default 3) |
