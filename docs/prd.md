# Crypto Asset Management AI Agent — PRD & Design Document

**Version:** 1.0 · **Status:** Pre-Development · **Last Updated:** February 2026

---

## 1. Overview

Autonomous multi-strategy DeFi bot that generates 20–50% risk-adjusted APY through diversified capital deployment. Dual-language architecture: Python (brain) handles all analysis and decisions, TypeScript (hands) handles all blockchain interaction. Communication via Redis.

| Metric              | Target          | Hard Limit                      |
| ------------------- | --------------- | ------------------------------- |
| Annual Return (APY) | 20–50%          | Risk-adjusted, not nominal      |
| Max Drawdown        | ≤15% target     | 20% circuit breaker             |
| Sharpe Ratio        | >2.0            | Minimum 1.5                     |
| Decision Latency    | <500ms          | Signal → execution order        |
| Uptime              | 99.5%           | Graceful degradation on failure |
| Budget              | $250–$1,000 CAD | Infrastructure + tooling        |

---

## 2. Chains & Strategies

### Chain Rollout

| Phase | Chain                | Protocols                  | Dev Environment |
| ----- | -------------------- | -------------------------- | --------------- |
| 1     | Ethereum Mainnet     | Aave, Uniswap V3, Lido     | Sepolia testnet |
| 2     | L2s (Arbitrum, Base) | Aave, GMX, Aerodrome       | L2 testnets     |
| 3     | Solana               | Marinade, Raydium, Jupiter | Devnet          |

### Strategy Tiers

> To be defined in strategy.md

**Tier 1 — Low Risk (50–60% of capital)**

- Lending optimization: Aave supply rotation based on utilization rates
- Liquid staking: ETH → stETH via Lido OR ETH → rETH via RocketPool, deploy derivatives into further yield

**Tier 2 — Medium Risk (25–35% of capital)**

- Concentrated liquidity on Uniswap V3 with dynamic range management
- Yield farming with auto-harvest and compounding

**Tier 3 — Higher Risk (10–20% of capital)**

- Flash loan arbitrage (atomic cross-DEX, zero-capital)
- Rate arbitrage across lending protocols

---

## 3. Architecture

### Design Principle

Python owns all decisions. TypeScript owns all chain interactions. Neither crosses into the other's domain.

```
┌─────────────────────────────────────────────────────┐
│                 TYPESCRIPT LAYER (thin)              │
│                                                     │
│  Chain Listener ──── TX Executor ──── Event Reporter │
│  (Alchemy WS)       (viem, sol/web3)  (results)     │
└────────┬──────────────────▲──────────────┬──────────┘
         │                  │              │
    ┌────▼──────────────────┴──────────────▼────┐
    │                   REDIS                    │
    │  pub/sub + streams + cache                 │
    │                                            │
    │  market:events       (TS → Python)         │
    │  execution:orders    (Python → TS)         │
    │  execution:results   (TS → Python)         │
    │                                            │
    │  cache: prices, gas, pool states           │
    │  TTL-based pruning on all streams          │
    └────────────────────┬──────────────────────┘
                         │
┌────────────────────────▼────────────────────────┐
│                  PYTHON LAYER (primary)          │
│                                                  │
│  Data Pipeline ── Strategy Engine ── Risk Manager│
│  (pandas, numpy)  (signals, portfolio) (limits,  │
│                                        breakers) │
│                                                  │
│  Monitoring ──── Claude API (sentiment, minimal) │
└──────────────────────────────────────────────────┘
```

### Key Decisions

| Decision       | Choice                                     | Rationale                                                      |
| -------------- | ------------------------------------------ | -------------------------------------------------------------- |
| Wallet         | Alchemy Smart Wallet (Account Abstraction) | On-chain spending limits, programmable guardrails              |
| State Recovery | TTL-based pruning on Redis Streams         | Bounded storage, sufficient replay window for crash recovery   |
| MEV Protection | Flashbots Protect                          | Private mempool routing prevents frontrunning/sandwich attacks |
| AI Usage       | Claude API, minimal calls                  | Sentiment analysis only; keep cost proportional to portfolio   |
| Deployment     | Railway                                    | Two services - TS and PY. Managed infra, easy scaling later    |
| Testnet First  | Yes — full Phase 1 on Sepolia              | Validate all strategies before real capital                    |

---

## 4. Project Structure

```
crypto-agent/
├── ts-executor/                  # TypeScript service
│   ├── src/
│   │   ├── listeners/            # Alchemy WebSocket handlers
│   │   ├── executors/            # TX construction (viem, solana/web3)
│   │   ├── redis/                # Redis client + channel handlers
│   │   └── index.ts
│   ├── package.json
│   └── tsconfig.json
│
├── py-engine/                    # Python service (primary)
│   ├── data/                     # Ingestion & normalization
│   ├── strategies/               # Strategy logic
│   ├── risk/                     # Circuit breakers & guardrails
│   ├── portfolio/                # State & allocation
│   ├── monitoring/               # Logging, metrics, alerts
│   ├── ai/                       # Claude API (sentiment)
│   └── main.py
│
├── shared/schemas/               # JSON schemas for Redis messages
├── docker-compose.yml            # Redis + both services
├── agent-state.json              # Persistent agent state
├── railway.toml                  # Deployment config
└── README.md
```

---

## 5. Tech Stack

| Component              | Technology                                 |
| ---------------------- | ------------------------------------------ |
| RPC Provider           | Alchemy (WebSockets + Enhanced APIs)       |
| ETH Interactions       | viem (TypeScript)                          |
| SOL Interactions       | @solana/web3.js (Phase 3)                  |
| Message Broker / Cache | Redis 7+ (pub/sub + Streams)               |
| Data Processing        | Python — pandas, numpy, scipy              |
| AI                     | Claude API (Anthropic)                     |
| Database               | PostgreSQL (trade history, audit trail)    |
| Deployment             | Docker Compose → Railway                   |
| Wallet                 | Alchemy Smart Wallet (Account Abstraction) |
| MEV Protection         | Flashbots Protect                          |
| Monitoring             | Structured JSON logs + Discord alerts      |

---

## 6. Risk Management

### Circuit Breakers

| Trigger              | Threshold        | Action                                          |
| -------------------- | ---------------- | ----------------------------------------------- |
| Portfolio drawdown   | >20% from peak   | Halt all positions. Unwind to stables. Alert.   |
| Single-position loss | >10% of position | Close position. 24h cooldown for that strategy. |
| Gas spike            | >3x 24h average  | Pause non-urgent ops. Queue for later.          |
| TX failure rate      | >3 failures/hour | Pause execution. Diagnostic mode. Alert.        |
| Protocol TVL drop    | >30% in 24h      | Withdraw all capital from affected protocol.    |

### Exposure Limits

- Max 40% in any single protocol
- Max 60% in any single asset (excluding stablecoins)
- Min 15% in stablecoins/liquid reserves at all times
- Smart contract allowlist enforced at TS executor level
- Flashbots Protect for all swap transactions

### Risk Matrix

| Risk                   | Severity | Mitigation                                           |
| ---------------------- | -------- | ---------------------------------------------------- |
| Smart contract exploit | Critical | Allowlist, TVL monitoring, protocol diversification  |
| Oracle manipulation    | High     | Multi-source prices, reject >2% deviation, TWAP      |
| Liquidity shock        | High     | Pre-trade depth checks, position sizing to liquidity |
| Key compromise         | Critical | Smart wallet spending caps, hot/cold split           |
| Chain halt / reorg     | Medium   | Finality-aware TX confirmation, state reconciliation |
| Strategy crowding      | Medium   | Yield compression monitoring, automatic rotation     |

---

## 7. Agent Harness

Patterns from [Anthropic's long-running agent research](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents).

### Startup Sequence (every restart)

1. Read `agent-state.json` to restore portfolio knowledge
2. Check Redis Streams for unprocessed execution orders
3. Query on-chain state via Alchemy to verify positions match records
4. Reconcile discrepancies (e.g., TX confirmed while offline)
5. Run health checks on all connected protocols
6. Resume normal operation or enter diagnostic mode

### Operational Rules

- **One strategy adjustment per cycle** — never rebalance everything at once
- **Clean state after every action** — updated state file, logs, and monitoring before next op
- **Strategy status tracking** — structured JSON with status (active / paused / evaluating / retired)
- **All messages validated** against shared JSON schemas; violations rejected loudly

### Human-in-the-Loop

- New protocol deployment requires owner approval
- Trades >15% of portfolio require confirmation
- New strategy tier activation requires explicit approval
- Emergency override via Discord: pause all, force-unwind, withdraw

---

## 8. Development Phases

### Phase 1 — Foundation (Weeks 1–4)

**Environment:** Sepolia testnet. No real capital.

- [ ] Project setup: Docker Compose with Redis, Python, TypeScript services
- [ ] Redis pub/sub + Streams communication with JSON schema validation
- [ ] TS chain listener with Alchemy WebSocket subscriptions
- [ ] TS executor with viem — Aave supply/withdraw only
- [ ] Alchemy Smart Wallet integration with spending limits
- [ ] Python data pipeline for real-time price and rate data
- [ ] Aave lending optimization strategy
- [ ] Risk manager with drawdown circuit breaker
- [ ] `agent-state.json` persistence and startup recovery
- [ ] Flashbots Protect integration for TX submission

**Done when:** Agent autonomously supplies to Aave, monitors rates, rotates markets, recovers from restart. All on Sepolia.

### Phase 2 — Strategy Expansion (Weeks 5–8)

- [ ] Uniswap V3 concentrated liquidity with dynamic range management
- [ ] Lido staking integration
- [ ] Portfolio allocation engine with rebalancing
- [ ] Full circuit breaker suite
- [ ] Claude API integration (sentiment, minimal usage)
- [ ] Discord alert system
- [ ] Performance monitoring + Sharpe ratio tracking
- [ ] L2 support (Arbitrum/Base) — listeners and executors
- [ ] Mainnet deployment with small real capital ($50 CAD test)

**Done when:** Multiple strategies running concurrently with risk isolation. L2 basic ops functional. Alerts working.

### Phase 3 — Scale & Harden (Weeks 9–12)

- [ ] Flash loan arbitrage
- [ ] Solana listener and executor
- [ ] ML gas prediction model
- [ ] Mobile approval flows (Discord inline keyboards)
- [ ] Stress test against historical crashes (Mar 2020, May 2021, FTX)
- [ ] Tax reporting and P&L attribution
- [ ] Railway production deployment with monitoring
- [ ] Full capital deployment ($250–$1,000 CAD)

**Done when:** System survives simulated crashes within drawdown limits. Full audit trail. Production-grade on Railway.

---

## 9. Event Flow Example

How an arbitrage opportunity flows through the system:

1. **TS Listener** detects large Uniswap swap via Alchemy WebSocket → publishes to `market:events`
2. **Python Data Pipeline** enriches with cached prices from other venues, computes spread
3. **Python Strategy Engine** checks profit threshold after gas + slippage
4. **Python Risk Manager** validates exposure limits, drawdown, contract allowlist
5. **Python** publishes structured order to `execution:orders` (token, amount, slippage, gas ceiling, deadline)
6. **TS Executor** constructs TX via viem, routes through Flashbots Protect, submits
7. **TS Reporter** publishes result to `execution:results` (hash, status, fill price, gas)
8. **Python Monitoring** logs everything, updates portfolio state, adjusts risk params

---

## 10. Success Criteria

| Metric           | Target      | Minimum    |
| ---------------- | ----------- | ---------- |
| APY (30-day)     | 35%         | 20%        |
| Sharpe Ratio     | >2.0        | >1.5       |
| Max Drawdown     | <15%        | <20%       |
| TX Success Rate  | >98%        | >95%       |
| Uptime           | >99%        | >95%       |
| Restart Recovery | <60 seconds | <5 minutes |

The project is successful when the agent consistently generates returns above the S&P 500 benchmark (7–10% annualized) with controlled drawdowns, running autonomously on Railway with Discord alerts and human-in-the-loop controls for high-stakes decisions.
