# Task: Update features.json Based on System Design v4.2

## Context

The system has undergone significant simplification from a multi-chain, multi-strategy DeFi bot to a focused **stablecoin-only yield bot on Base**. The system design (`docs/system-design.md`) is now at v4.2, but `features.json` still contains many features from earlier iterations that no longer align with the current design.

## Current State

- **features.json**: 63 features, 44 passing, 19 failing
- **System design**: v4.2 — focused on Base chain, USDC/USDbC/DAI, two strategies (LEND-001 Aave lending, LP-001 Aerodrome LP)
- **Codebase**: Only Aave V3 + Aerodrome adapters exist in ts-executor. Only aave_lending.py + aerodrome_lp.py strategies exist in py-engine.

## Key Changes in System Design v4.2

1. **Strategy system**: Plug-and-play, auto-discovered from `strategies/` directory. Strategy protocol defines `strategy_id`, `eval_interval`, `data_window`, `evaluate(snapshot) → StrategyReport`.
2. **StrategyReport structure**: observations, signals (5 types: entry_met, exit_met, harvest_ready, rebalance_needed, threshold_approaching), recommendation.
3. **Decision gate**: Opens when any report has `actionable: true` signal. No API call for "do nothing."
4. **Event-driven architecture**: Redis Streams (not pub/sub) on all channels. Count-based MAXLEN pruning.
5. **Circuit breakers independent**: Separate execution path, emit directly to Redis. `CB:` prefix in strategy field.
6. **Safe 1-of-2 multisig**: Application-level allowlist. No ERC-4337, no Flashbots in v1.
7. **Encode-only adapters**: Pure functions producing calldata. No classes, no state.
8. **PostgreSQL**: Operational source of truth for portfolio, trades, strategy status, audit log.
9. **Deployment**: Railway with git push → container rebuild.
10. **Monitoring**: Structured JSON logs. Alerting is "v2 concern."

## Divergences Found: features.json vs System Design

### Features that should be REMOVED (code deleted, not in system design)

| Feature | Reason |
|---------|--------|
| EXEC-005 (Uniswap V3) | Code deleted in v1 simplification. No uniswap-v3-adapter.ts exists. |
| EXEC-006 (Lido staking) | Code deleted in v1 simplification. No lido-adapter.ts exists. |
| EXEC-007 (Flash loan) | Code deleted in v1 simplification. No flash-loan-adapter.ts exists. |
| STRAT-002 (Liquid staking/Lido) | Code deleted. No lido_staking.py exists. |
| STRAT-003 (Uniswap V3 LP) | Code deleted. No uniswap_v3_lp.py exists. |
| STRAT-004 (Yield farming) | Code deleted. No corresponding strategy file. |
| STRAT-005 (Flash loan arb) | Code deleted. No flash_loan_arb.py exists. |
| STRAT-006 (Rate arb) | Code deleted. No rate_arb.py exists. |
| STRAT-008 (Strategy ingestion) | Code deleted. Manual process now. |
| AI-002 (Code-gen pipeline) | Code deleted. Manual with Claude Code now. |
| MON-002 (Discord alerts) | Code deleted. Alerting is "v2 concern" per system design. |
| MON-004 (Anomaly detection) | Code deleted. No anomaly_detection.py exists. |
| TEST-003 (ML gas prediction) | Code deleted. No ml/ directory exists. |
| REPORT-001 (Tax reporting) | Code deleted. No tax_engine.py exists. |

### Features with INCORRECT pass status

| Feature | Current | Should be | Reason |
|---------|---------|-----------|--------|
| EXEC-003 (Flashbots) | passes:true | passes:false OR remove | flashbots-protect.ts deleted in v1 simplification (session 2026-03-06-01) |
| LISTEN-003 (L2 Arbitrum, Base) | passes:true | Update description | Arbitrum removed in v1; only Base remains. Description says "Arbitrum, Base." |

### Features with descriptions out of sync with system design

| Feature | Issue |
|---------|-------|
| INFRA-002 | Step 1 says "Pub/sub" but system design says "Redis Streams everywhere" — all channels use Streams |
| LISTEN-003 | References Arbitrum — should be Base only |
| EXEC-009 | Currently describes "GMX on Arbitrum + Aerodrome on Base" — GMX deleted, Arbitrum removed |
| PORT-001 | References "strategy tiers: Tier 1 (70%), Tier 2 (20%), Tier 3 (10%)" — only Tier 1 exists now |
| PORT-003 | May reference multi-tier rebalancing |
| DATA-005 | References "L2 data for Arbitrum, Optimism, Base" — only Base now |

### Solana features (P3) — not in system design

LISTEN-004, EXEC-008, DATA-006 are Solana features. System design §10 has generic "Adding a new chain" instructions but doesn't mention Solana specifically. These are aspirational/future.

### Phase structure questions

System design doesn't define phases. Current phases: P1, P1b, P1c, P2, P3, P4. The P1b/P1c sub-phases were introduced when strategies were tiered — now only Tier 1 exists. Should these collapse back to P1?

## Files to Modify

- `harness/features.json` — Primary target. Remove dead features, fix descriptions, fix pass statuses.

## Decision Points for User

1. **Remove vs mark inactive?** — Dead features (deleted code, not in system design) — remove entirely from features.json, or keep with `passes: false`?
2. **EXEC-003 (Flashbots)** — Remove entirely (code deleted) or keep as future P1b feature?
3. **Phase restructuring** — Collapse P1b/P1c into P1? They were created for tiered strategies that no longer exist.
4. **Solana (P3)** — Keep as aspirational, or remove since system design doesn't mention it?
5. **P2 TEST-002 (stress testing)** — Keep or remove? System design doesn't mention historical stress testing.
6. **P4 INFRA-005 (Railway deploy)** — Keep? System design §9 describes Railway deployment.

## Existing Files Reference

### ts-executor adapters (encode-only)
- `aave-v3-adapter.ts` — Aave V3 supply/withdraw
- `aerodrome-adapter.ts` — Aerodrome DEX on Base

### py-engine strategies
- `aave_lending.py` — LEND-001
- `aerodrome_lp.py` — LP-001

### Schemas
- `execution-orders.schema.json` — chains: [ethereum, base], protocols: [aave_v3, aerodrome]
- `market-events.schema.json` — chains: [ethereum, base], protocols: [aave_v3, aerodrome, system]
