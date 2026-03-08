# Features.json Verification Report — 2026-03-07-06

## Summary

**24/48 passing** (23 verified PASS + 1 deprecated HARNESS-004)
**24/48 failing** — verified against system-design.md v4.2, codebase, and git history

Methodology: 3 parallel primary agents + 3 parallel secondary verifiers in isolated worktrees, each cross-referencing system-design.md → features.json steps → actual code + git log.

### Secondary Verification (spot-check of borderline verdicts)
- 5 borderline PASS features re-examined → all 5 flipped to FAIL (PORT-001, PORT-002, PORT-003, RISK-008, REPORT-001)
- 4 borderline FAIL features re-examined → 1 flipped to PASS (TEST-002), 3 confirmed FAIL
- 5 confident verdicts sanity-checked → all 5 confirmed (DATA-002, AI-002, EXEC-001, LISTEN-001, STRAT-001)

---

## Agent 1: TypeScript Service (14 features) — 12 PASS, 2 FAIL

### INFRA-001: PASS
- **System design ref:** §4 (Project Structure)
- **Files:** ts-executor/src/ (12 files), tsconfig.json, package.json, py-engine/pyproject.toml, shared/schemas/, docker-compose.yml, README.md, .env.example
- **All 6 steps verified.** No deviations.

### INFRA-002: FAIL
- **System design ref:** §3 (Redis Streams everywhere)
- **Files:** ts-executor/src/redis/client.ts
- **Steps verified:** MAXLEN pruning, connection loss callbacks, schema validation, error rejection
- **Steps missing:** Uses pub/sub as primary transport with Streams as secondary durability layer. No consumer group support. Feature spec requires pure Redis Streams (not pub/sub).
- **Deviation:** Hybrid pub/sub + Streams, not pure Streams.

### INFRA-003: PASS
- **System design ref:** §3, §4
- **Files:** shared/schemas/market-events.schema.json, execution-orders.schema.json, execution-results.schema.json, ts-executor/src/validation/schema-validator.ts
- **All 5 steps verified.** market-events includes "system" protocol (reasonable for new_block, large_transfer).

### INFRA-004: PASS
- **System design ref:** §9
- **Files:** docker-compose.yml, ts-executor/Dockerfile, py-engine/Dockerfile
- **All 5 steps verified.** No deviations.

### LISTEN-001: PASS
- **System design ref:** §3 (Chain Listeners)
- **Files:** ts-executor/src/listeners/websocket-manager.ts
- **All 6 steps verified.** Exponential backoff (200ms→30s), health monitoring (60s), backpressure (1000), sequence numbers.

### LISTEN-002: PASS
- **System design ref:** §3 (Data Pipeline)
- **Files:** ts-executor/src/listeners/market-event-publisher.ts, event-normalizer.ts
- **All 4 steps verified.** Also includes deduplication (bonus).

### LISTEN-003: PASS
- **System design ref:** §3 (Chain Listeners)
- **Files:** ts-executor/src/listeners/l2-listener.ts
- **All 5 steps verified.** L2 gas estimation lives in aerodrome-adapter.ts rather than l2-listener.ts.

### EXEC-001: PASS
- **System design ref:** §3 (TX Builder)
- **Files:** ts-executor/src/execution/transaction-builder.ts
- **All 8 steps verified.** Subscribes via pub/sub (same INFRA-002 deviation).

### EXEC-002: PASS
- **System design ref:** §6 (Risk Framework)
- **Files:** ts-executor/src/wallet/safe-wallet.ts
- **All 7 steps verified.** 1-of-2 Safe, allowlist, spending limits, validateOrder(), Safe{Core} SDK.

### EXEC-003: PASS
- **System design ref:** §10 (Extending)
- **Files:** ts-executor/src/execution/aave-v3-adapter.ts
- **All 4 steps verified.** File header says "EXEC-004" (stale numbering).

### EXEC-004: PASS
- **System design ref:** §10 (Extending)
- **Files:** ts-executor/src/execution/aerodrome-adapter.ts
- **All 5 steps verified.** File header says "EXEC-009" (stale numbering).

### EXEC-005: PASS
- **System design ref:** §3 (Event Reporter)
- **Files:** ts-executor/src/execution/event-reporter.ts
- **All 4 steps verified.** File header says "EXEC-010" (stale numbering).

### RISK-006: PASS
- **System design ref:** §6 (Exposure Limits)
- **Files:** ts-executor/src/wallet/safe-wallet.ts
- **All 4 steps verified.** Allowlist passed as Set in constructor (not single env var), functionally equivalent.

### RISK-009: FAIL
- **System design ref:** §6 (Safe on-chain guard module)
- **Files:** None found
- **All 4 steps missing.** Entirely unimplemented — no Solidity contract, no deployment scripts, no setGuard integration.

---

## Agent 2: Python Core (17 features) — 10 PASS, 7 FAIL

### INFRA-005: FAIL
- **System design ref:** §7, §4
- **Files:** py-engine/db/models.py, db/database.py, db/repository.py
- **Steps verified:** Repository layer, SQLAlchemy create_all(), DATABASE_URL config
- **Steps missing:** Portfolio positions model (in agent-state.json, not PG), strategy statuses (in agent-state.json), decision audit log (in-memory deque only), in-memory cache from PG at startup (loads from JSON)
- **Deviation:** DB layer works for trades/snapshots/alerts, but positions and strategy statuses use flat file.

### INFRA-006: PASS
- **System design ref:** §3 (Event-driven decision pipeline)
- **Files:** py-engine/main.py
- **All 6 steps verified.** Decision gate uses signal urgency-based fast paths rather than pure actionable flag, but functional intent matches.

### DATA-001: FAIL
- **System design ref:** §3 (Layer 1), §6 (Oracle manipulation)
- **Files:** py-engine/data/price_feed.py
- **Steps verified:** Alchemy primary, DefiLlama fallback, supported tokens, Redis cache
- **Steps missing:** Multi-source deviation validation (>2% rejection) — explicitly removed in commit aff2ef2. Pause-on-stale not implemented.

### DATA-002: PASS
- **System design ref:** §3, §6 (Gas spike CB)
- **Files:** py-engine/data/gas_monitor.py
- **All 5 steps verified.** Tracks Ethereum L1 gas, derives Base L2 via OP Stack model.

### DATA-003: PASS
- **System design ref:** §3 (Layer 1)
- **Files:** py-engine/data/defi_metrics.py
- **All 5 steps verified.** Data from DeFi Llama yields API (reasonable source choice).

### DATA-004: FAIL
- **System design ref:** §7 (Startup sequence)
- **Files:** py-engine/data/reconciliation.py
- **Steps verified:** Log discrepancies with structured details
- **Steps missing:** No Alchemy integration (dependency injection with no default), compares against agent-state.json not PostgreSQL, auto_reconcile marks resolved but doesn't write, no startup sequence integration.
- **Deviation:** Well-structured skeleton without production wiring.

### AI-001: PASS
- **System design ref:** §2, §3 (Layer 3), §8
- **Files:** py-engine/ai/decision_engine.py
- **All 7 steps verified.** Output schema is simplified decision format; translation to execution orders happens in main.py _decision_to_orders().

### AI-002: PASS
- **System design ref:** §2
- **Files:** py-engine/ai/insight_synthesis.py
- **All 6 steps verified.** Uses InsightSnapshot dataclass with field ordering: market_data, positions, risk_status, strategies, recent_decisions.

### STRAT-001: FAIL
- **System design ref:** §2 (Strategy contract)
- **Files:** py-engine/strategies/__init__.py, aave_lending.py, aerodrome_lp.py
- **Steps verified:** None fully
- **Steps missing:** No Strategy protocol/ABC, no eval_interval/data_window, no evaluate(MarketSnapshot)→StrategyReport, no MarketSnapshot dataclass, no StrategyReport structure, no signal types, no auto-discovery. Strategies generate orders directly (v1 pattern).

### STRAT-002: FAIL
- **System design ref:** §2 (active/inactive)
- **Files:** py-engine/strategies/lifecycle_manager.py, harness/state_manager.py
- **Steps verified:** Has status per strategy (but 4 states, not binary)
- **Steps missing:** eval_interval scheduling, PostgreSQL persistence (uses JSON), concurrent async evaluation. Uses evaluating/active/paused/retired vs design's active/inactive.

### STRAT-003: FAIL
- **System design ref:** §2 (Strategy contract)
- **Files:** py-engine/strategies/aave_lending.py
- **Steps verified:** strategy_id='LEND-001', min_apy_improvement=0.005, gas_amortization_days=14
- **Steps missing:** Strategy protocol conformance, StrategyReport production, $1M liquidity check, active exit condition checking, TVL circuit breaker integration. Generates orders, not reports.

### STRAT-004: FAIL
- **System design ref:** §2 (Strategy contract)
- **Files:** py-engine/strategies/aerodrome_lp.py
- **Steps verified:** strategy_id='LP-001', min_emission_apr=0.03, min_tvl_usd=500000
- **Steps missing:** Strategy protocol conformance, StrategyReport production, pool TVL exit check ($200K), AERO swap liquidity check. Generates orders, not reports.

### PORT-001: FAIL (flipped from PASS in secondary verification)
- **System design ref:** §3, §6
- **Files:** py-engine/portfolio/allocator.py
- **Steps verified:** Capital tracking, allocation data for prompt
- **Steps missing:** PostgreSQL persistence (step 3 — zero DB interaction, purely in-memory), per-strategy named limits (step 1 — uses tier-based bounds, no concept of strategy names)

### PORT-002: FAIL (flipped from PASS in secondary verification)
- **System design ref:** §3, §7
- **Files:** py-engine/portfolio/position_tracker.py
- **Steps verified:** Open/close with P&L, query by filters, execution result handling, prompt data
- **Steps missing:** PostgreSQL persistence (step 4 — backup_to_postgres() is explicit stub at line 294-307)

### PORT-003: FAIL (flipped from PASS in secondary verification)
- **System design ref:** §2 (Signal types)
- **Files:** py-engine/portfolio/rebalancer.py
- **Steps verified:** Drift detection, configurable threshold
- **Steps missing:** rebalance_needed signal (step 3 — produces RebalanceAction objects, not signals), Claude decision pipeline (step 4 — generate_orders() at line 259-318 emits execution orders directly with strategy:"rebalancer", bypassing Claude)

### MON-001: PASS
- **System design ref:** §7 (Monitoring)
- **Files:** py-engine/monitoring/logger.py
- **All 5 steps verified.** correlationId via ContextVar — included when set, omitted when not.

### MON-002: PASS
- **System design ref:** §7 (Monitoring)
- **Files:** py-engine/monitoring/dashboard.py
- **All 5 steps verified.** Data computation layer (no visual UI). Sharpe ratio with 7d/30d/all windows.

---

## Agent 3: Risk + Harness + Test + Deploy (16 features) — 5 PASS, 11 FAIL

### RISK-001: FAIL
- **System design ref:** §6
- **Files:** py-engine/risk/drawdown_breaker.py, main.py
- **Steps verified:** Peak tracking, >20% threshold
- **Steps missing:** No CB: prefix orders (uses "reason":"drawdown_circuit_breaker"), orders not schema-validated against execution-orders schema, runs in decision loop not independently.

### RISK-002: FAIL
- **System design ref:** §6
- **Files:** py-engine/risk/position_loss_limit.py
- **Steps verified:** Monitors position value, >10% threshold, 24h cooldown concept
- **Steps missing:** No order generation at all (detection only), cooldowns in-memory Python dict (not Redis TTL keys), don't survive restarts.

### RISK-003: PASS
- **System design ref:** §6
- **Files:** py-engine/risk/gas_spike_breaker.py, main.py
- **All 5 steps verified.** Gate-only, urgent bypass, auto-clear. No deviations.

### RISK-004: FAIL
- **System design ref:** §6
- **Files:** py-engine/risk/tx_failure_monitor.py, main.py
- **Steps verified:** Rolling 1-hour window, >3 failures threshold, gate-only pause
- **Steps missing:** Enters "diagnostic mode" (manual resume) not "hold mode" (auto-resume). Does NOT auto-clear — requires manual_resume(). Fundamental behavioral difference.

### RISK-005: FAIL
- **System design ref:** §6
- **Files:** py-engine/risk/tvl_monitor.py
- **Steps verified:** TVL tracking, >30% threshold
- **Steps missing:** No order generation (detection/alerting only), no CB: prefix orders, no schema validation, doesn't filter by active positions.

### RISK-007: FAIL
- **System design ref:** §6 (Risk matrix)
- **Files:** py-engine/data/price_feed.py
- **All 4 steps missing.** Deviation guard was implemented then deliberately removed in commit aff2ef2 ("remove CoinGecko code, deviation guard, and L2 price fetcher").

### RISK-008: FAIL (flipped from PASS in secondary verification)
- **System design ref:** §6
- **Files:** py-engine/risk/exposure_limits.py
- **Steps verified:** Steps 1-4 (per-protocol, per-asset, liquid reserve, env vars) — module fully implemented
- **Steps missing:** Step 5 (checked before order execution) — ExposureLimiter is never imported or called in main.py. _apply_risk_gate() checks drawdown, gas spike, TX failures but NOT exposure limits. Module is completely unwired from runtime.

### HARNESS-001: FAIL
- **System design ref:** §7
- **Files:** py-engine/harness/state_manager.py, db/models.py, db/repository.py, data/redis_client.py
- **Steps verified:** Trade history in PG, market cache in Redis
- **Steps missing:** Portfolio positions in PG (uses agent-state.json), strategy statuses in PG (uses agent-state.json), CB cooldowns in Redis TTL (in-memory Python dicts), system status in Redis (uses flat-file operational_flags).

### HARNESS-002: FAIL
- **System design ref:** §7
- **Files:** py-engine/harness/startup_recovery.py
- **Steps verified:** State load (from flat file), health checks, resume/diagnostic on errors
- **Steps missing:** Loads from agent-state.json not PostgreSQL, stream check counts but doesn't replay, on-chain reconciliation framework exists but not wired with real queries.

### HARNESS-003: PASS
- **System design ref:** §7
- **Files:** py-engine/harness/approval_gates.py
- **All 4 steps verified.** Discord notification, configurable thresholds, PENDING/APPROVED/REJECTED/EXPIRED states.

### HARNESS-005: FAIL
- **System design ref:** §7
- **Files:** py-engine/harness/diagnostic_mode.py, main.py
- **Steps verified:** Blocks trading, CB remain active, diagnostic logging
- **Steps missing:** Not tracked in Redis (uses flat-file state), no Claude API unavailability entry path, no auto-resume (requires manual exit), named "diagnostic mode" not "hold mode".

### TEST-001: FAIL
- **System design ref:** N/A (testing)
- **Files:** py-engine/tests/test_integration_e2e.py, test_integration_circuit_breakers.py, test_integration_schema_validation.py, test_integration_startup_recovery.py
- **Steps verified:** Schema validation tests, startup recovery tests (partial)
- **Steps missing:** E2E doesn't go through full DecisionLoop (calls strategy.generate_orders() directly), circuit breaker tests don't verify actual unwind→position closed.

### TEST-002: PASS (flipped from FAIL in secondary verification)
- **System design ref:** N/A (testing)
- **Files:** ts-executor/tests/calldata-validation.test.ts, aerodrome-adapter.test.ts
- **All 4 steps verified.** Primary audit missed the estimateBaseGas test suite in aerodrome-adapter.test.ts (lines 305-344) covering L2+L1 data cost, fixed overhead, and L1 dominance. Cross-adapter selector uniqueness at lines 273-294.

### TEST-003: PASS
- **System design ref:** N/A (testing)
- **Files:** ts-executor/tests/sepolia-live.test.ts
- **All 5 steps verified.** Skip-by-default, contract verification, getReserveData, calldata acceptance.

### REPORT-001: FAIL (flipped from PASS in secondary verification)
- **System design ref:** §7 (monitoring)
- **Files:** py-engine/reporting/pnl_attribution.py
- **Steps verified:** By-strategy, by-protocol, time-series, PostgreSQL sourced
- **Steps missing:** Step 3 (by-asset USDC/USDbC/DAI) — code has get_attribution_by_chain() but no get_attribution_by_asset(). Groups by trade.chain, not asset.

### DEPLOY-001: FAIL
- **System design ref:** §9
- **Files:** ts-executor/Dockerfile, py-engine/Dockerfile, docker-compose.yml
- **Steps verified:** Both Dockerfiles (multi-stage, dev/prod targets)
- **Steps missing:** No Railway config (no railway.toml), no deploy trigger, no persistent volumes config, no rollback config. Docker Compose is local dev only.

---

## Systemic Gap Analysis

### Gap 0: PostgreSQL Integration (INFRA-005, PORT-001, PORT-002, HARNESS-001, HARNESS-002) — LARGE
Positions, strategy statuses, allocation state, and operational state use agent-state.json flat file.
Design requires PostgreSQL for durable state + Redis TTL keys for cooldowns/system_status.
PORT-001 allocator has zero DB interaction. PORT-002 backup_to_postgres() is a stub.

### Gap 1: Strategy System (STRAT-001, 002, 003, 004) — LARGE
v1 code has strategies as order generators. v4.2 design requires:
- Strategy protocol (strategy_id, eval_interval, data_window, evaluate)
- MarketSnapshot dataclass
- StrategyReport structure with signal types
- Auto-discovery from strategies/ directory
- Strategies produce reports, Claude decides actions

### Gap 2: Portfolio Pipeline (PORT-003, RISK-008, REPORT-001) — MEDIUM
PORT-003 rebalancer generates orders directly, bypassing Claude.
RISK-008 exposure limiter is fully implemented but unwired from main.py.
REPORT-001 missing by-asset breakdown (has by-chain instead).

### Gap 3: Circuit Breaker Direct Emission (RISK-001, 002, 005) — MEDIUM
CB modules are detection-only or emit non-schema-compliant orders.
Design requires: CB: prefix in strategy field, schema-validated orders, separate emission path bypassing decision gate.

### Gap 4: Hold Mode (RISK-004, HARNESS-005) — SMALL
Code has "diagnostic mode" with manual exit. Design has "hold mode" with auto-resume when trigger clears.
Requires: Redis system_status key, auto-resume logic, Claude API unavailability entry path.

### Gap 5: Redis Pure Streams (INFRA-002) — MEDIUM
Code uses pub/sub as real-time transport + Streams as durability layer.
Design requires: XREADGROUP consumer groups, no pub/sub, proper stream acknowledgment.

### Standalone Items
- DATA-001: Re-implement >2% deviation guard (was removed in aff2ef2)
- DATA-004: Wire Alchemy integration + PostgreSQL comparison
- RISK-007: Re-implement oracle guard (depends on DATA-001 deviation)
- RISK-009: New Solidity contract + deployment scripts
- TEST-001: Route E2E through DecisionLoop
- DEPLOY-001: Railway configuration
