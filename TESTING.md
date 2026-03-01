# Icarus — Test Guide

## Quick check (no dependencies needed)

```bash
bash harness/verify.sh
```

Runs everything: TS type check, ESLint, vitest, ruff, pytest, schema validation. Exit 0 = all good.

## Unit tests only

```bash
# TypeScript (297 tests, ~2s)
cd ts-executor && pnpm test

# Python (1,331 tests, ~5s)
cd py-engine && uv run pytest tests/ -q

# Single test file
cd py-engine && uv run pytest tests/test_main_loop.py -v
cd ts-executor && pnpm vitest run tests/index.test.ts
```

## Linting only

```bash
cd ts-executor && pnpm lint            # ESLint
cd py-engine && uv run ruff check .    # ruff
```

## Integration test (needs Redis)

```bash
# Start Redis
docker compose up -d redis

# Run integration tests (tagged with integration markers)
cd py-engine && uv run pytest tests/test_integration*.py -v

# Publish a test event through Redis manually
docker exec -it $(docker compose ps -q redis) redis-cli
> PUBLISH market:events '{"version":"1.0.0","eventType":"new_block","chain":"ethereum","timestamp":"2026-01-01T00:00:00Z","correlationId":"test-1","data":{"blockNumber":1000000,"gasUsed":"15000000","gasLimit":"30000000","baseFeeGwei":30}}'
```

## Full stack smoke test

```bash
# 1. Copy and fill env
cp .env.example .env
# Fill: ALCHEMY_SEPOLIA_API_KEY, WALLET_PRIVATE_KEY, ANTHROPIC_API_KEY

# 2. Start everything
docker compose up

# 3. Watch logs — you should see:
#    ts-executor: "TypeScript executor ready"
#    py-engine:   "Python engine ready — entering decision loop"

# 4. In another terminal, check Redis traffic
docker exec -it $(docker compose ps -q redis) redis-cli MONITOR
```

## What each test file covers

| File | Tests | What |
|------|-------|------|
| `test_main_loop.py` | 24 | Decision loop pipeline, fast-path, risk gate |
| `test_decision_engine.py` | 30 | Claude API integration, retry, cost tracking |
| `test_insight_synthesis.py` | 25 | Market data → insight snapshot |
| `test_drawdown_breaker.py` | 18 | Portfolio drawdown circuit breaker |
| `test_tvl_monitor.py` | 34 | Protocol TVL drop detection |
| `test_rebalancer.py` | 30 | Portfolio drift detection + rebalancing |
| `test_gas_predictor.py` | 27 | ML gas prediction + heuristic fallback |
| `test_l2_data_pipeline.py` | 39 | L2 token mappings, gas, metrics |
| `index.test.ts` | 6 | TS service bootstrap + order validation |
| `transaction-builder.test.ts` | 22 | TX construction, nonce, retries |
| `smart-wallet.test.ts` | 25 | Spending limits, allowlist, UserOps |

## Troubleshooting

**`WALLET_PRIVATE_KEY is not configured`** — Set it in `.env` or run `export WALLET_PRIVATE_KEY=0x...` (any 64-char hex works for tests).

**Redis connection errors in tests** — Most tests mock Redis. Integration tests need `docker compose up -d redis`.

**`scikit-learn` import errors** — Run `cd py-engine && uv sync` to install ML dependencies.
