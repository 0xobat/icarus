# Icarus

Autonomous multi-strategy DeFi bot. Python (brain) handles all analysis and decisions. TypeScript (hands) handles all blockchain interaction. Communication via Redis.

## Prerequisites

- [Docker](https://docs.docker.com/get-docker/) with Docker Compose
- [pnpm](https://pnpm.io/installation) (v9+)
- [uv](https://docs.astral.sh/uv/) (Python package manager)
- Node.js 22+
- Python 3.12+

## Setup

```bash
# 1. Clone and install dependencies
bash harness/init.sh

# 2. Configure environment
cp .env.example .env
# Edit .env with your Alchemy API key and wallet config
```

## Running

### Docker (recommended)

```bash
# Start all services (Redis + both services)
docker compose up

# Background mode
docker compose up -d

# View logs
docker compose logs -f

# Stop
docker compose down
```

### Local development

```bash
# Start Redis
docker compose up -d redis

# TS service (separate terminal)
cd ts-executor && pnpm dev

# Python service (separate terminal)
cd py-engine && uv run python main.py
```

## Testing

```bash
# TypeScript
cd ts-executor && pnpm test

# Python
cd py-engine && uv run pytest tests/ --tb=short -q

# Full verification (both services + schemas)
bash harness/verify.sh
```

## Architecture

```
┌─────────────┐    Redis    ┌──────────────┐
│  py-engine   │◄──────────►│ ts-executor   │
│  (brain)     │            │ (hands)       │
│              │            │               │
│ - Strategies │  market:   │ - WebSocket   │
│ - Risk mgmt  │  events    │   listener    │
│ - Portfolio  │ ──────────►│ - TX builder  │
│ - Data pipe  │            │ - Smart Wallet│
│              │  exec:     │ - Flashbots   │
│              │  orders    │ - Protocol    │
│              │◄───────────│   adapters    │
│              │  exec:     │               │
│              │  results   │               │
│              │───────────►│               │
└─────────────┘            └──────────────┘
```

### Redis Channels

| Channel | Direction | Purpose |
|---------|-----------|---------|
| `market:events` | TS → Python | Blockchain events, price updates |
| `execution:orders` | Python → TS | Trade orders from strategy engine |
| `execution:results` | TS → Python | Transaction results and confirmations |

### Shared Schemas

All Redis messages validated against JSON schemas in `shared/schemas/`.

## Environment Variables

See `.env.example` for all required configuration. Key variables:

- `ALCHEMY_SEPOLIA_API_KEY` — Alchemy API access
- `WALLET_PRIVATE_KEY` — Sepolia testnet wallet (never mainnet)
- `REDIS_URL` — Redis connection string
- Risk limits (`MAX_DRAWDOWN_PERCENT`, etc.)

## Network

Phase 1 operates exclusively on **Sepolia testnet**. No mainnet until P2.
