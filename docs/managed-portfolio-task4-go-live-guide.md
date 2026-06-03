# Managed Portfolio — Task 4 (Live Switchover) Operator Guide

**Audience:** the operator running the first live exercise.
**Date:** 2026-06-03
**Prerequisite:** P1.1–P1.5c Tasks 1–3 are merged on `daedalus` (the resolver, pricing, planner, managed cycle, holdings provider, results consumer, config, gas tracker — all unit-tested). This guide covers the **live switchover** (`__main__` rewire) and the **first observed rebalance round-trip on Base Sepolia**.

> **What this step is:** flip the decision-engine from the (now-dead) lake path to the managed-portfolio path, point it at real Base Sepolia infrastructure, and watch one real rebalance happen end-to-end. This is the P1 exit criterion from `docs/design-managed-portfolio.md` §7 — "prove the substrate."

---

## 0. Before you start — the testnet posture

We run on **Base Sepolia first** (decision #1). Nothing here touches mainnet capital. You need:

- A **Base Sepolia RPC endpoint** (Alchemy/Infura) → `ALCHEMY_BASE_HTTP_URL`.
- A **funded test wallet / Safe** on Base Sepolia holding some test **WETH** and test **USDC** (so there's a portfolio to rebalance). Get testnet ETH from a Base Sepolia faucet; wrap to WETH; acquire test USDC via a faucet or a testnet swap.
- The Safe address → `SAFE_ADDRESS`.
- `CHAIN=base` (Base Sepolia uses the same `base` chain logic; the RPC URL is what points at Sepolia).

> **Note on token addresses:** the P1.1 registry currently holds **Base mainnet** addresses for USDC/WETH. **Base Sepolia has different token addresses.** Before the testnet run, either (a) point the registry at Sepolia addresses via a small env/registry override, or (b) accept that the first run is a **dry observation** (read balances, see the order get built and gated) and do the real swap once addresses are confirmed. **Decide this first** — it's the one place mainnet/testnet diverge in the code. (Recommended: add a `CHAIN_ID`-keyed registry entry for Sepolia so the resolver emits Sepolia addresses.)

---

## 1. What Task 4's code does (the `__main__` rewire)

Edit `decision-engine/src/decision_engine/__main__.py`:

**Remove** (stop wiring — leave the modules in the tree): `RosterListener`, `TemplateRegistry` + `build_db_verdict_lookup`, `_LiveAllocator`/`ComposedAllocator`/`_empty_returns_lookup`, `RulesRegimeClassifier`, the `DecisionCycle` construction, the `_Stub*` classes.

**Keep**: the risk-module construction (`DrawdownBreaker`, `ExposureLimiter` + the `DECISION_ENGINE_TOTAL_CAPITAL_USD` fail-loud guard, `GasSpikeBreaker`, `PositionLossLimit`, `TxFailureMonitor`), `_build_risk_gate`, Redis client, `DatabaseManager` + `db.create_tables()` (now also creates the `trades` table).

**Add** construction:
```python
config = load_managed_config(os.environ.get("MANAGED_CONFIG_PATH", "/app/config/managed.toml"))
adapter = RpcAdapter()                       # reads ALCHEMY_BASE_HTTP_URL (point at Sepolia)
w3 = adapter._w3  # or build a shared AsyncWeb3 from the same URL
holdings = RpcHoldingsProvider(
    w3=w3, adapter=adapter, safe_address=config.safe_address,
    crypto_symbol=config.crypto_symbol, stable_symbol=config.stable_symbol, chain=config.chain,
)
cycle = ManagedPortfolioCycle(
    adapter=adapter, holdings=holdings, target=config.rebalance_target(),
    risk_gate=risk_gate, publisher=RedisExecutorPublisher(redis_client),
    config=config.cycle_config(),
)
gas_tracker = GasAverageTracker()
consumer = ResultsConsumer(tx_failure=tx_failure, trade_sink=_make_db_trade_sink(db))
```

**Replace the worker loop** (`DecisionEngine`) with a managed loop that, per tick (`config.interval_seconds`):
1. `market = await adapter.fetch_live(config.chain)`
2. `gas_spike.update(market.gas_gwei, gas_tracker.update(market.gas_gwei))`  ← live gas feed
3. `crypto_usd, stable_usd = await holdings.current_usd_holdings()`
4. `drawdown.update(crypto_usd + stable_usd)`  ← live NAV feed
5. (optional) write/refresh a pending `Trade` on publish — see §2
6. `await cycle.run_one()`
7. structured tick log (action, nav, weights)

…and a **background task** that subscribes Redis to `execution:results:{chain}` and runs `consumer.run(pubsub)`. Keep SIGTERM/SIGINT handling.

---

## 2. The trade log (pending-on-publish + update-on-result)

The append-only trade log reuses the existing `Trade` model (`icarus.db.models.Trade`, table `trades`). **An `ExecutionResult` alone can't fill a `Trade` row** — it lacks order-side fields (`asset_in`, `amount_in`, `action`). So use the standard two-write pattern:

**On publish** (in the managed loop, right after `cycle.run_one()` reports a `rebalance` — or have the cycle expose the built order): write a `Trade` with `status="pending"`:

| Trade column | Source |
|---|---|
| `trade_id` | `order.order_id` |
| `correlation_id` | `order.correlation_id` |
| `strategy` | `"REBAL:base"` (constant in P1) |
| `protocol` | `config` protocol (`"aerodrome"`) |
| `chain` | `config.chain` |
| `action` | `"swap"` |
| `asset_in` | plan `from_symbol` |
| `asset_out` | plan `to_symbol` |
| `amount_in` | plan `usd_amount` (USD notional; or token amount if preferred) |
| `slippage_bps` | `config.slippage_bps` |
| `status` | `"pending"` |

**On result** (the `trade_sink` the consumer calls): **UPDATE** the row matched by `trade_id == result.order_id`:

| Trade column | Source (ExecutionResult) |
|---|---|
| `status` | `result.status` (confirmed/reverted/…) |
| `tx_hash` | `result.tx_hash` |
| `amount_out` | `result.amount_out` |
| `price_at_execution` | `result.fill_price` (note: the consumer's projection key is `fill_price`; the column is `price_at_execution`) |
| `gas_used` | `result.gas_used_wei` (if present) |
| `error_message` | `result.error` or `result.revert_reason` |

`_make_db_trade_sink(db)` returns a `Callable[[dict], None]` that opens a session and applies the UPDATE. **The consumer already swallows sink exceptions** (P1.5c fix) so a transient DB error logs `trade_sink_failed` and the consumer keeps running.

> **Simpler P1 fallback:** if pending-on-publish is more wiring than you want for the first run, skip it and have the sink INSERT a result-only row (the order-side columns are constants in P1 except `asset_in`/`amount_in`, which you can leave null by relaxing those columns or recording them as the swap's from-symbol/USD). The pending+update pattern is cleaner and recommended, but balance-truth-from-chain means the trade log is **history/audit only — not correctness** — so a lean version is acceptable for the first observation.

---

## 3. Environment & config

`.env` (secrets + per-deployment):
```
ALCHEMY_BASE_HTTP_URL=https://base-sepolia.g.alchemy.com/v2/<key>
SAFE_ADDRESS=0x<your Sepolia Safe>
CHAIN=base
DECISION_ENGINE_TOTAL_CAPITAL_USD=<your test NAV, e.g. 100>
REDIS_URL=redis://localhost:6379/0
# ts-executor side (Base): WALLET_PRIVATE_KEY / SAFE / CONTRACT_ALLOWLIST / AERODROME_ROUTER etc.
MANAGED_CONFIG_PATH=/app/config/managed.toml   # or a local path
```

`config/managed.toml` (strategy dials — already committed; tune if desired):
```
[allocation]  crypto_symbol="WETH" stable_symbol="USDC" crypto_weight=0.6 band=0.10
[rebalance]   slippage_bps=50 cost_gate_margin=4 gas_units=200000 deadline_seconds=60
[cadence]     interval_seconds=3600
```
The decision-engine **refuses to boot** without `SAFE_ADDRESS` or `DECISION_ENGINE_TOTAL_CAPITAL_USD`, or with an invalid weight/band/chain (fail-loud by design).

---

## 4. Deploy & run (Base Sepolia)

```bash
# 1. Bring up infra + the managed decision-engine + ts-executor
docker compose up -d --wait postgres redis ts-executor decision-engine

# 2. Watch the decision-engine logs (structured JSON)
docker compose logs -f decision-engine
```

To **force a rebalance** for the first observation, set the wallet's WETH:USDC split deliberately **outside** the 50–70% band (e.g. fund it ~80% WETH / 20% USDC) so the first tick must act.

---

## 5. Verify the round-trip (the acceptance checklist)

Watch the logs tick-by-tick:

- [ ] **Real data:** the tick log shows a **non-zero ETH price** and **non-zero gas** (the old DefiLlama-zeros bug is gone), and `holdings_read` shows real WETH/USDC USD values.
- [ ] **Live breakers:** `drawdown.update` and `gas_spike.update` are called each tick — the breakers show live (non-zero) state, not `peak=0`.
- [ ] **One correct order:** with the deliberately-skewed split, the cycle logs `managed_order_published` once, with a real `token_in`/`token_out` (Sepolia addresses), a smallest-unit `amount`, `recipient` = your Safe, and a slippage-bounded `amount_out_min`.
- [ ] **Gate cleared:** the order was not dropped (`managed_order_dropped` absent).
- [ ] **Real swap:** ts-executor logs the Safe transaction; a tx hash appears on Base Sepolia explorer.
- [ ] **Result consumed:** `execution_result` log with `status=confirmed`; the tx-failure monitor recorded a success; a `Trade` row exists (status confirmed, tx_hash, amount_out).
- [ ] **Settles to hold:** the **next tick** reads the post-swap balances, the portfolio is now within the 50–70% band, and the cycle logs `managed_hold`.
- [ ] **Cost gate works:** nudge the split to a *tiny* drift past the band; confirm the cycle logs a **`cost-gated`** hold rather than churning.

When all boxes are checked, **P1 is complete** — the substrate is proven end-to-end on real (testnet) infrastructure.

---

## 6. Troubleshooting

| Symptom | Likely cause |
|---|---|
| Engine won't boot, `SAFE_ADDRESS`/capital error | required env missing — fail-loud working as designed |
| All ticks `managed_hold`, holdings look wrong / zero | token addresses are mainnet, not Sepolia (see §0 note); or the wallet isn't funded |
| Order built but ts-executor rejects "recipient"/encode | Sepolia token addresses or `AERODROME_ROUTER` not set for Sepolia |
| `managed_order_dropped` every tick | a breaker is tripped (check drawdown/gas/tx-failure state) or exposure cap; inspect the `checker` field |
| No `execution_result` ever arrives | results consumer not subscribed, or ts-executor publishing to a different channel — confirm `execution:results:base` |
| `trade_sink_failed` warnings | DB sink error — non-fatal (consumer keeps running); check the DB session/`trades` table |

---

## 7. Rollback / stop

- **Stop trading immediately:** `docker compose stop decision-engine` (no new orders emitted; in-flight orders settle).
- **Revert to the lake path:** `git revert` the Task 4 `__main__` commit (the lake modules were left intact in the tree, so the old wiring still works).
- **Mainnet promotion (later):** only after the testnet round-trip passes cleanly and you've reviewed the per-position multisig posture (Squads/Safe) and swapped the registry to mainnet addresses. The blueprint's rollback section covers position unwinding.

---

## 8. After P1

Once the testnet round-trip is observed: **P1 is done.** Natural next steps (later phases):
- **Cleanup:** remove the now-dead lake modules (extractor/backtest/lake-governor/roster/DecisionCycle) from the tree.
- **P2:** yield routing (lending for stable, liquid staking for ETH/SOL), real `RebalanceTarget` multi-asset (add SOL, wBTC), per-venue caps.
- **P3:** LP overlay (≤15% cap), depeg/LST-health monitor, real Squads multisig, MEV-protected execution.
