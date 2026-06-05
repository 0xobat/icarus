# Managed Portfolio — Testnet Go-Live Runbook (Base Sepolia)

**Audience:** the operator running the first live exercise.
**Date:** 2026-06-05
**Status:** Task 4 (A network-aware registry, B DB trade-log, C `__main__` switchover) is **merged and wired** on `daedalus`. The decision-engine already runs the managed-portfolio path — there is no code to edit to do this run. This guide is the **operator runbook**: set the env, fund a testnet wallet, and observe one rebalance round-trip on Base Sepolia.

> **What this step is:** the decision-engine is already flipped from the (now-dead) lake path to the managed-portfolio path. Your job is to point it at real Base Sepolia infrastructure and watch one real rebalance happen end-to-end. This is the P1 exit criterion from `docs/design-managed-portfolio.md` §7 — "prove the substrate."

---

## 0. Before you start — the testnet posture

We run on **Base Sepolia first** (decision #1). Nothing here touches mainnet capital. You need:

- A **Base Sepolia RPC endpoint** (Alchemy/Infura) → `ALCHEMY_BASE_HTTP_URL`.
- A **funded test wallet / Safe** on Base Sepolia holding some test **WETH** and test **USDC** (so there's a portfolio to rebalance). Get testnet ETH from a Base Sepolia faucet; wrap to WETH; acquire test USDC via a faucet or a testnet swap.
- The Safe address → `SAFE_ADDRESS`.
- `CHAIN=base` and **`CHAIN_ID=84532`** (Base Sepolia). `CHAIN` selects the chain logic; `CHAIN_ID` selects the token registry.

### Token addresses — already handled (one env var)

Task 4A made the resolver registry **chain-id-keyed**. Setting `CHAIN_ID=84532` makes it emit the **pre-registered Base Sepolia addresses** automatically:

| Symbol | Base Sepolia (`84532`) | Base mainnet (`8453`) |
|---|---|---|
| WETH | `0x4200000000000000000000000000000000000006` (same predeploy) | `0x4200000000000000000000000000000000000006` |
| USDC | `0x036CbD53842c5426634e7929541eC2318f3dCF7e` | `0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913` |

> **Verify, then override only if stale.** The Sepolia USDC entry is marked *VERIFY before live use* in `order_resolver.py`. Confirm it against the current Base Sepolia USDC; if it has changed, override at boot **without a code change** by setting `USDC_ADDRESS` (and optionally `USDC_DECIMALS`) in env — `_apply_token_overrides` re-registers it for the active `CHAIN_ID`. Same hook exists for `WETH_ADDRESS`/`WETH_DECIMALS`.

### The one real testnet caveat — the swap venue

**Aerodrome (the P1 swap DEX) is mainnet-only.** It is not deployed on Base Sepolia. So on Sepolia the decision-engine will read balances, build the order, pass it through the risk gate, and publish it — but ts-executor has **no Aerodrome router to fill against**. Two honest options:

- **(a) Dry observation (default first run):** verify everything up to and including the *published, gated order* on Sepolia. The swap does not actually fill. This proves the decision/risk/holdings/trade-log substrate end-to-end minus the on-chain fill.
- **(b) Mainnet fork for a real fill:** run a Base **mainnet fork** (Anvil/Tenderly) with `CHAIN_ID=8453`, fund the forked Safe, and let the real Aerodrome swap execute against forked liquidity. This is the only way to observe a true fill before touching real mainnet capital.

Decide (a) vs (b) before you start. The §5 checklist flags which boxes apply to which.

---

## 1. What the engine already does (no edits required)

`decision-engine/src/decision_engine/__main__.py` runs `ManagedEngine`. Per tick (`config.interval_seconds`), `ManagedEngine._tick`:

1. `market = await adapter.fetch_live(config.chain)` — real ETH price + gas.
2. `gas_spike.update(market.gas_gwei, gas_tracker.update(market.gas_gwei))` — **live gas feed** (EMA average via `GasAverageTracker`).
3. `crypto_usd, stable_usd = await holdings.current_usd_holdings()` — on-chain `balanceOf` for WETH+USDC.
4. `drawdown.update(crypto_usd + stable_usd)` — **live NAV feed**.
5. `result = await cycle.run_one()` — plan → resolve → risk gate → publish.
6. On a published rebalance: best-effort `record_pending_trade(...)` writes a `Trade(status="pending")` (§2). A DB error here logs `pending_trade_record_failed` and the tick continues — the trade log is audit, not correctness.
7. `managed_tick` structured log (action, reason, published, nav_usd, gas_gwei).

A **background task** subscribes Redis to `execution:results:{chain}` and runs `consumer.run(pubsub)`, feeding the tx-failure breaker and the DB trade sink. It is **fail-closed**: if that task dies (not a clean cancel), `_on_consumer_done` logs `consumer_task_died` and halts the engine — a dead results feed must not silently disable the breaker.

Wiring facts worth knowing (so logs make sense):
- **One shared `AsyncWeb3`** built from `ALCHEMY_BASE_HTTP_URL` feeds both `RpcAdapter` (prices/gas) and `RpcHoldingsProvider` (balances).
- The engine **refuses to boot** without `ALCHEMY_BASE_HTTP_URL`, `SAFE_ADDRESS`, or `DECISION_ENGINE_TOTAL_CAPITAL_USD` (the exposure limiter divides by it), or with an invalid `CHAIN`/weight/band — fail-loud by design.

---

## 2. The trade log — already wired (pending-on-publish + update-on-result)

The trade log reuses the existing `Trade` model (`icarus.db.models.Trade`, table `trades`); `db.create_tables()` creates it at boot. An `ExecutionResult` alone can't fill a `Trade` row (it lacks order-side fields), so the engine uses a two-write pattern that is **already coded** — nothing to add:

**On publish** — `record_pending_trade(db, ...)` (called from `_tick` via `_make_pending_recorder`) inserts `status="pending"` from the order details the cycle now exposes on `ManagedCycleResult`:

| Trade column | Source |
|---|---|
| `trade_id` | `result.order_id` |
| `correlation_id` | `result.correlation_id` |
| `strategy` | `"REBAL:{chain}"` |
| `protocol` | `"aerodrome"` |
| `chain` | `config.chain` |
| `action` | `"swap"` |
| `asset_in` | `result.from_symbol` |
| `asset_out` | `result.to_symbol` |
| `amount_in` | `result.usd_amount` (USD notional) |
| `slippage_bps` | `config.slippage_bps` |
| `status` | `"pending"` |

**On result** — `make_db_trade_sink(db)` returns the sink the consumer calls; it **UPDATEs** the row matched by `trade_id == result.order_id`:

| Trade column | Source (ExecutionResult projection) |
|---|---|
| `status` | `rec["status"]` (confirmed/reverted/…) |
| `tx_hash` | `rec["tx_hash"]` |
| `amount_out` | `rec["amount_out"]` (if present) |
| `price_at_execution` | `rec["fill_price"]` (note: projection key is `fill_price`; column is `price_at_execution`) |

If no pending row matches, the sink logs `trade_update_no_pending_row` and returns. The **consumer swallows sink exceptions** (logs `trade_sink_failed`) so a transient DB error never kills the results loop. All trade-log writes are best-effort: **a DB outage cannot halt trading.**

---

## 3. Environment & config

`.env` (secrets + per-deployment):
```
ALCHEMY_BASE_HTTP_URL=https://base-sepolia.g.alchemy.com/v2/<key>
SAFE_ADDRESS=0x<your Sepolia Safe>
CHAIN=base
CHAIN_ID=84532                 # 84532 = Base Sepolia; 8453 = mainnet/fork
DECISION_ENGINE_TOTAL_CAPITAL_USD=<your test NAV, e.g. 100>
REDIS_URL=redis://localhost:6379/0
MANAGED_CONFIG_PATH=/app/config/managed.toml   # or a local path

# Optional — only if the registered Sepolia token address is stale (§0):
# USDC_ADDRESS=0x<sepolia usdc>   USDC_DECIMALS=6
# WETH_ADDRESS=0x<sepolia weth>   WETH_DECIMALS=18

# ts-executor side (Base): WALLET_PRIVATE_KEY / SAFE / CONTRACT_ALLOWLIST / AERODROME_ROUTER etc.
# (Aerodrome router only exists on mainnet/fork — see §0 caveat.)
```

`config/managed.toml` (strategy dials — already committed; tune if desired):
```toml
[allocation]
crypto_symbol = "WETH"
stable_symbol = "USDC"
crypto_weight = 0.6
band = 0.10

[rebalance]
slippage_bps = 50
cost_gate_margin = 4
gas_units = 200000
deadline_seconds = 60

[cadence]
interval_seconds = 3600
```
The decision-engine **refuses to boot** without `SAFE_ADDRESS` / `DECISION_ENGINE_TOTAL_CAPITAL_USD` / `ALCHEMY_BASE_HTTP_URL`, or with an invalid weight/band/chain (fail-loud by design). `crypto_weight` must be in (0,1); `band` in [0,0.5]; `slippage_bps` in [0,1000].

---

## 4. Deploy & run (Base Sepolia)

```bash
# 1. Bring up infra + the managed decision-engine + ts-executor
docker compose up -d --wait postgres redis ts-executor decision-engine

# 2. Watch the decision-engine logs (structured JSON)
docker compose logs -f decision-engine
```

To **force a rebalance** for the first observation, set the wallet's WETH:USDC split deliberately **outside** the 50–70% band (e.g. fund it ~80% WETH / 20% USDC) so the first tick must act.

> For a **real fill** rather than dry observation, run against a Base **mainnet fork** with `CHAIN_ID=8453` and a forked-funded Safe + Aerodrome router (§0 option b).

---

## 5. Verify the round-trip (the acceptance checklist)

Watch the logs tick-by-tick. Boxes marked **(fill)** require a real swap venue — they apply to the mainnet-fork run (§0 b), not the Sepolia dry observation (§0 a).

- [ ] **Real data:** the `managed_tick` log shows a **non-zero ETH price** and **non-zero gas** (the old DefiLlama-zeros bug is gone), and `nav_usd` reflects real WETH/USDC USD values.
- [ ] **Live breakers:** `drawdown.update` and `gas_spike.update` run each tick — the breakers show live (non-zero) state, not `peak=0`.
- [ ] **One correct order:** with the deliberately-skewed split, the cycle logs `managed_order_published` once, with real `token_in`/`token_out` (Sepolia addresses for `CHAIN_ID=84532`), a smallest-unit `amount`, `recipient` = your Safe, and a slippage-bounded `amount_out_min`.
- [ ] **Gate cleared:** the order was not dropped (`managed_order_dropped` absent).
- [ ] **Pending trade logged:** a `trades` row appears with `status="pending"`, `asset_in`/`asset_out`, `amount_in` (USD), `trade_id` = the order id.
- [ ] **(fill) Real swap:** ts-executor logs the Safe transaction; a tx hash appears on the explorer.
- [ ] **(fill) Result consumed:** an `execution_result` arrives with `status=confirmed`; the tx-failure monitor records a success; the pending `trades` row **updates** to confirmed (tx_hash, amount_out, price_at_execution).
- [ ] **(fill) Settles to hold:** the **next tick** reads the post-swap balances, the portfolio is now within the 50–70% band, and the cycle logs `managed_hold`.
- [ ] **Cost gate works:** nudge the split to a *tiny* drift past the band; confirm the cycle logs a **`cost-gated`** hold (reason on `managed_hold`) rather than churning.

When the applicable boxes are checked, **P1 is complete** — the substrate is proven end-to-end on real (testnet) infrastructure (dry observation proves everything but the on-chain fill; the fork run proves the fill too).

---

## 6. Troubleshooting

| Symptom | Likely cause |
|---|---|
| Engine won't boot, `SAFE_ADDRESS`/capital/RPC error | required env missing — fail-loud working as designed |
| All ticks `managed_hold`, holdings look wrong / zero | `CHAIN_ID` not `84532` (resolver using mainnet addresses), a stale Sepolia token address (set `USDC_ADDRESS` override), or the wallet isn't funded |
| Order built but ts-executor rejects / can't encode | no Aerodrome router on Sepolia (expected — §0 caveat); for a real fill use the mainnet fork |
| `managed_order_dropped` every tick | a breaker is tripped (check drawdown/gas/tx-failure state) or exposure cap; inspect the `checker` field |
| Engine halts with `consumer_task_died` | the results-consumer task died (Redis blip) — fail-closed by design; restart after confirming Redis |
| No `execution_result` ever arrives | results consumer not subscribed, or ts-executor publishing to a different channel — confirm `execution:results:base` |
| `trade_update_no_pending_row` warnings | a result arrived with no matching pending row (publish-side write failed or order id mismatch) — non-fatal |
| `trade_sink_failed` / `pending_trade_record_failed` warnings | DB write error — non-fatal (trade log is audit, not correctness); check the DB session/`trades` table |

---

## 7. Rollback / stop

- **Stop trading immediately:** `docker compose stop decision-engine` (no new orders emitted; in-flight orders settle).
- **Revert to the lake path:** `git revert` the Task 4C `__main__` commit (the lake modules were left intact in the tree, so the old wiring still works).
- **Mainnet promotion (later):** only after the testnet round-trip passes cleanly and you've reviewed the per-position multisig posture (Squads/Safe). Set `CHAIN_ID=8453` to emit mainnet addresses. The blueprint's rollback section covers position unwinding.

---

## 8. After P1

Once the round-trip is observed: **P1 is done.** Natural next steps (later phases):
- **Cleanup:** remove the now-dead lake modules (extractor/backtest/lake-governor/roster/DecisionCycle) from the tree.
- **P2:** yield routing (lending for stable, liquid staking for ETH/SOL), real `RebalanceTarget` multi-asset (add SOL, wBTC), per-venue caps.
- **P3:** LP overlay (≤15% cap), depeg/LST-health monitor, real Squads multisig, MEV-protected execution.
