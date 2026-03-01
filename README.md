# Icarus

Autonomous multi-strategy DeFi bot. Python (brain) handles analysis, AI reasoning, and decisions. TypeScript (hands) handles all blockchain interaction. Communication via Redis.

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
# Fill in ALCHEMY_SEPOLIA_API_KEY, WALLET_PRIVATE_KEY, ANTHROPIC_API_KEY
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
cd ts-executor && pnpm test          # vitest
cd py-engine && uv run pytest tests/ # pytest
bash harness/verify.sh               # both + lint + schema checks
```

## Architecture

```
┌─────────────┐    Redis    ┌──────────────┐
│  py-engine   │◄──────────►│ ts-executor   │
│  (brain)     │            │ (hands)       │
│              │            │               │
│  ai/         │  market:   │  listeners/   │
│  strategies/ │  events    │  execution/   │
│  risk/       │ ──────────►│  wallet/      │
│  portfolio/  │            │  security/    │
│  data/       │  exec:     │               │
│  ml/         │  orders    │  6 protocol   │
│  reporting/  │◄───────────│  adapters     │
│              │  exec:     │               │
│              │  results   │  L1 + L2      │
│              │───────────►│  listeners    │
└─────────────┘            └──────────────┘
```

Chains: Ethereum (Sepolia), Arbitrum, Base.

All Redis messages validated against JSON schemas in `shared/schemas/`.

## Environment

See `.env.example`. Key variables:

| Variable | Purpose |
|----------|---------|
| `ALCHEMY_SEPOLIA_API_KEY` | Alchemy API access |
| `WALLET_PRIVATE_KEY` | Sepolia testnet wallet |
| `ANTHROPIC_API_KEY` | Claude API for AI decision engine |
| `REDIS_URL` | Redis connection (default `redis://localhost:6379`) |
| `DATABASE_URL` | PostgreSQL connection |
| `TOTAL_CAPITAL` | Portfolio capital in USD (default 10000) |
