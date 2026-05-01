# Icarus — Redesign Note (v2)

**Status:** Draft · **Date:** 2026-04-30 · **Supersedes intent of:** `system-design.md` v4.2

---

## Problem

Trade crypto profitably using AI agents. The system must:

1. Ingest strategies from external sources (research papers, on-chain analytics, public alpha).
2. Validate them before risking capital.
3. Allocate capital across the validated set based on market regime and portfolio state.
4. Execute on-chain.
5. Surface results to a single operator.

The hard part of this problem is **finding edge**, not executing trades. Any architecture that doesn't make strategy discovery first-class is solving the wrong problem.

---

## Why v4.2 doesn't get us there

- Strategies are hand-written Python classes. Two exist (Aave supply, Aerodrome LP). The system has no way to generate, compare, or rank strategies — only execute the two it ships with.
- Claude API is invoked at runtime to rubber-stamp deterministic threshold logic. Cost scales linearly with cycles; benefit is near-zero for the deployed strategies.
- No backtest harness. No paper-trading mode. Strategy → production is one human step.
- Dual-service Python/TypeScript split adds Redis bus, dual schema validation, dual test suites — overhead without functional gain.

---

## Approach

A pipeline where strategies flow from research to capital, gated by validation:

```
[Research sources]                 (papers, on-chain analytics, public alpha)
        │
        ▼
[Strategy extractor]               LLM (frontier model, offline) → Strategy DSL
        │
        ▼
[Backtest engine]                  Historical replay → Sharpe, MaxDD, turnover
        │   reject if < threshold
        ▼
[Paper-trade]                      Live data, simulated fills, 2–4 weeks
        │   reject if degraded
        ▼
[Live portfolio]                   N validated strategies, capital allocator
        │
        ▼
[Monitoring]                       Dashboard + alerts
```

Each stage is a quality gate. Capital only meets a strategy after it survives backtest and paper-trade.

---

## Key technical decisions

### 1. Strategies are config, not code

A YAML/JSON DSL replaces hand-generated Python classes:

```yaml
id: LEND-001
sources: [aave_v3.usdc.base]
entry: aave_v3.usdc.supply_apy > position.apy + 0.5
exit:  aave_v3.usdc.supply_apy < 1.0
allocation_max: 0.7
```

Benefits: parseable from papers, version-controllable, A/B testable, no codegen step.

### 2. AI placement: frontier offline, OSS online

| Where | Role | Model class | Cost |
|-------|------|-------------|------|
| Strategy extraction (offline) | Read paper → emit DSL | Frontier (Claude / GPT-4 class) | Pay per paper, infrequent |
| Strategy ranking (offline, weekly) | Score backtest results, recommend portfolio | Frontier | Infrequent |
| Runtime decisions (online, every cycle) | Regime classification, signal scoring | OSS local (Llama 3.x / Qwen 2.5 / DeepSeek) | ~$0 marginal |
| Anomaly explanation (online, on event) | Translate breaker trip into operator-readable text | OSS local | ~$0 marginal |

Open-source models on-host eliminate per-call API cost for the high-frequency runtime path. Frontier APIs are reserved for the slow offline path where quality matters and frequency is low. This makes "AI on every cycle" actually viable.

### 3. Single execution language

Pick one runtime language (Python, given the data/ML ecosystem fit). Drop the Python/TS split and the Redis bus between them. One process, one test suite, one deploy target. Frontend stays separate (different concern).

### 4. Backtest is a hard gate, not optional

No strategy reaches paper-trade without a backtest. No strategy reaches live without paper-trade. The pipeline enforces this — there is no manual "deploy" path.

### 5. Monitoring follows profitability, not precedes it

Replace the bespoke Next.js dashboard with Grafana / Streamlit pointed at Postgres + Discord/Telegram alerts. Custom UI work is deferred until a profitable system exists to monitor.

---

## What stays from v4.2

- Safe multisig wallet pattern (1-of-2 with recovery signer).
- Circuit breakers (drawdown, position loss, gas spike, TVL drop, TX failure rate) — re-purposed for the active strategies the new pipeline produces.
- PostgreSQL for state of record. Redis for cache only.
- Encode-only protocol adapters (good pattern, keeps execution auditable).

---

## Non-goals

- A bespoke real-time UI before the pipeline produces a validated strategy set.
- Cross-chain coverage at v2 launch — Base only until the pipeline is proven.
- Multi-tenant or multi-user support.
- Strategy auto-deployment without human approval at the live-promotion step.

---

## Success criteria

- **Pipeline:** A new strategy can go from a linked paper to backtest results in < 1 hour with no code edits.
- **Cost:** Runtime AI cost per month < 5% of net yield at $100K AUM.
- **Validation:** Zero strategies reach live capital without surviving backtest + paper-trade.
- **Operator load:** Daily monitoring time < 5 minutes under normal conditions.

---

## Open questions

- Which OSS model serves runtime best on available hardware? (Llama 3.1 8B vs Qwen 2.5 7B vs DeepSeek-V2-Lite — needs benchmark on classification/scoring tasks the system actually runs.)
- DSL expressiveness ceiling — when does YAML stop being enough and a real strategy language become necessary?
- Backtest data source — Dune, Allium, self-indexed, or hybrid?
- Capital allocator method — equal-weight, risk-parity, Kelly fractional, or learned?
