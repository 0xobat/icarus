# Managed Portfolio P1.5 — Live Integration & Reconciliation (Design + Build Brief)

> **Status: OPERATOR-REVIEW GATE — not auto-executed.** Unlike P1.1–P1.4 (pure/additive code, fully unit-tested autonomously), P1.5 changes what the running service *does*, requires live resources (funded wallet, RPC keys, a running stack), and carries several architectural choices. This document is precise on **what changes, which files, the open decisions, and the acceptance criteria**; the line-level TDD sub-plans (P1.5a…) are finalized after operator review, since some steps depend on decisions below and on reading runtime state.

**Goal:** Flip the decision-engine over to the `ManagedPortfolioCycle`, feed it real on-chain holdings and prices, wire the circuit breakers to live state, reconcile positions from `execution:results`, and **observe one real rebalance round-trip** (the P1 exit criterion).

**Retires from the critique:** #2 (breakers never fed live state), the no-results-consumer finding, and the `build_default_adapter()`-returns-zeros data gap.

---

## What changes (mapped to the real `__main__.py`)

The current `decision-engine/src/decision_engine/__main__.py` wires the **lake** path: `RosterListener` + `TemplateRegistry` + `_LiveAllocator` + `RulesRegimeClassifier` + `DecisionCycle` (lines 179–276), driven by a roster-coupled `DecisionEngine` worker loop (lines 76–122). P1.5 replaces that path with the managed path while **keeping** the risk-module construction (lines 186–210), Redis, DB, and the `DECISION_ENGINE_TOTAL_CAPITAL_USD` fail-loud guard.

| Concern | Current (lake) | P1.5 (managed) |
|---|---|---|
| Cycle | `DecisionCycle` (roster→evaluate→allocate) | `ManagedPortfolioCycle` (P1.4) |
| Worker loop | `DecisionEngine` (bootstraps `RosterListener`) | a managed worker loop with no roster (`run_one` on an interval) |
| Data adapter | `build_default_adapter()` → DefiLlama (`prices={}`) | **`RpcAdapter`** (real Chainlink ETH/USD + gas) |
| Holdings | Postgres `PortfolioPosition` rows (lake) | **on-chain balance reads** (new `HoldingsProvider`) |
| Regime/allocator/registry/roster | wired | **removed** (managed path needs none) |
| Risk gate | built (lines 204–210) | **kept**, but breakers now fed live state |

Leave the lake modules in the tree (don't delete `DecisionCycle`/roster yet); just stop wiring them in `__main__`. A follow-up cleanup phase removes the dead lake code once the managed path is proven.

---

## New components to build (with their homes)

1. **`RpcHoldingsProvider`** — `decision-engine/src/decision_engine/holdings.py` (new).
   Implements the P1.4 `HoldingsProvider` Protocol (`async current_usd_holdings() -> (crypto_usd, stable_usd)`). Reads ERC-20 `balanceOf(SAFE_ADDRESS)` for WETH and USDC on Base via web3 (the `RpcAdapter` already holds an `AsyncWeb3`; reuse that provider or a shared one), normalizes by decimals (from the P1.1 token registry), and prices via `pricing.price_usd` against a `MarketSnapshot`.
   - **Unit-testable** with a mock web3 returning fixed balances (mirror `rpc.py`'s `_Web3Like` test seam). The live read is the only non-unit part.

2. **Managed worker loop** — fold into `__main__.py` (replace `DecisionEngine`).
   A small async loop: `while not stop: await cycle.run_one(); await wait(interval)`. Reuse the existing signal-handling + structlog setup verbatim. No roster bootstrap.

3. **Execution-results consumer** — `decision-engine/src/decision_engine/results_consumer.py` (new).
   Subscribes to `execution:results:base` (Redis), validates each into an `icarus.envelopes.results.ExecutionResult`, and:
   - feeds the **tx-failure breaker** (`.update()` on failed/reverted/timeout statuses) — closes critique #2 for that breaker;
   - **reconciles holdings** — on a `confirmed` swap, the next tick's on-chain read already reflects truth, so for P1 the consumer's job is (a) breaker feed and (b) a structured audit log of every result. Full position-table reconciliation (writing `PortfolioPosition` rows) is only needed once we track per-lot PnL — flag as P2, keep P1 to balance-truth-from-chain.

4. **Breaker live-state feeds** — in the worker loop / consumer.
   - `gas_spike` breaker: `.update(<gas_gwei from the tick's MarketSnapshot>)` each tick.
   - `drawdown` breaker: `.update(<nav_usd from holdings>)` each tick.
   - `tx_failure` monitor: fed by the results consumer on failures.
   (Read each breaker's exact `.update()` signature in `decision-engine/src/decision_engine/risk/*.py` at execution time and match it — do not guess the argument shapes.)

---

## Open design decisions (resolve with operator before line-level plan)

1. **Holdings source for P1 first run:** real on-chain `balanceOf` (mainnet/testnet) vs a config-seeded holdings stub for the very first dry observation. Recommendation: testnet (Base Sepolia) on-chain reads first, then mainnet.
2. **Worker-loop refactor scope:** minimal managed loop in `__main__` vs generalizing `DecisionEngine` to be cycle-agnostic. Recommendation: minimal new loop; don't over-abstract.
3. **Reconciliation depth for P1:** balance-truth-from-chain only (recommended) vs full `PortfolioPosition` lot tracking (defer to P2).
4. **Target + config source:** env vars (`MANAGED_CRYPTO_WEIGHT=0.6`, `MANAGED_BAND=0.10`, `MANAGED_LP_CAP=0.15`, `SAFE_ADDRESS`, slippage, cost-gate margin) vs a config file. Recommendation: env vars, matching the existing `DECISION_ENGINE_TOTAL_CAPITAL_USD` pattern.
5. **Single vs both chains for P1:** Base-only first (recommended — Solana executor still single-signer/stubbed per the critique) then Solana in P2.

---

## Acceptance criteria (the P1 exit criterion — operator-observed)

A successful P1.5 demonstrates, on a real (testnet-first) deployment:
- [ ] `docker compose up -d --wait` brings the managed decision-engine + ts-executor + postgres + redis healthy.
- [ ] The engine reads **real** WETH+USDC balances and a **real** ETH price (non-zero, non-empty — the old DefiLlama-zeros bug is gone).
- [ ] With holdings deliberately set outside the ±10% band, the cycle emits **one** correctly-constructed `ExecutionOrder` (real token addresses, smallest-unit amount, recipient=Safe, slippage-bounded `amount_out_min`).
- [ ] The order clears the risk gate and the ts-executor **executes a real swap** (testnet), and the result lands on `execution:results:base`.
- [ ] The next tick reads the post-swap balances and the portfolio is **within band** → the cycle holds.
- [ ] A deliberately tiny drift is **cost-gated** (held, with a `cost-gated` log line).
- [ ] Breakers show **live state** (gas/nav updated each tick; a simulated failed tx trips the tx-failure monitor).

That observed round-trip — enter, size, route, settle, reconcile, then hold — is the P1 "prove the substrate" milestone from `docs/design-managed-portfolio.md` §7.

---

## Why this is the checkpoint

P1.1–P1.4 are pure logic, fully unit-tested with no live dependency — safe to build autonomously. P1.5 (a) changes live service behavior, (b) needs funded wallets + RPC keys + a running stack that only the operator holds, and (c) has the five open decisions above. The design doc itself flags the P1 exit criterion as the operator's "first live exercise." So P1.5 is handed back for review + the live run, not auto-executed.
