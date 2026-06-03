# Icarus — Managed Portfolio Design

**Date:** 2026-06-03
**Status:** Approved direction, pre-implementation. Supersedes the
strategy-*discovery* portions of `docs/blueprint.md` (see [Supersession
map](#supersession-map)). The blueprint is retained as history.
**Companion:** `docs/architecture-critique-2026-06-03.md` (why we pivoted).

---

## 1. Mandate

Icarus is a **DeFi-native managed portfolio for a single operator**, optimizing
for **capital preservation first, growth second**. It holds **stablecoins +
blue-chip crypto** and grows the portfolio through (a) blue-chip appreciation and
(b) yield harvested on both sleeves. The operator's edge — and therefore the
system's reason to exist — is **execution quality, rebalancing discipline, and
risk management**, not strategy discovery.

### Objective function
Maximize long-run risk-adjusted portfolio value subject to hard capital-
preservation constraints (protocol allowlist, per-venue caps, depeg/health exits,
custody controls). The system should be *boring on purpose*: it does nothing most
of the time, and acts only when allocation drift is both material and
cost-justified.

### Non-goals (explicit scope boundaries)
- **No strategy discovery** (deferred — "discovery-last"). No LLM strategy
  extraction, no paper/blog ingestion, no strategy-validation pipeline.
- **No market timing.** Allocation is *strategic* (fixed target + bands), never
  tactical. The system does not predict regimes or try to buy low / sell high.
  This is a deliberate safety choice, not a limitation.
- **No advisory LLM in the capital path** (none needed; the inference/Ollama
  service is dropped entirely).

---

## 2. Portfolio model

Three buckets, in priority order, against a fixed strategic target.

```
Target portfolio (tunable):
  60%  CRYPTO sleeve   (blue-chip, mostly yield-bearing)
  40%  STABLE sleeve   (stablecoin lending)
  ── of which up to 15% of total portfolio may sit in an LP overlay ──
```

### Strategic allocation (the master dials)

| Dial | Value | Notes |
|---|---|---|
| Crypto : Stable split | **60 / 40** | Growth tilt; the primary risk dial |
| Rebalancing band | **±10 absolute %** | Crypto sleeve floats 50–70% before any action; wider band = fewer trades, lower cost, more drift (chosen for efficiency) |
| LP overlay cap | **≤15% of total portfolio** | Hard ceiling; LP is a bounded yield add-on, never the core |
| Cost-gate margin | **rebalance only if correction value ≥ ~4× est.(gas + slippage)** | Tunable; this is where "efficient" lives |
| Check cadence | **hourly read; act only on band breach** | Reads are cheap; trades are gated |

### Asset set & yield mechanics

| Asset | Sleeve | Yield mechanic | Notes |
|---|---|---|---|
| **ETH** | Crypto | **Liquid staking** (wstETH-class) | Yield while staying liquid for rebalancing |
| **SOL** | Crypto | **Liquid staking** (jitoSOL/mSOL-class) | Same |
| **wBTC** | Crypto | **Lending** (cbBTC/WBTC on Aave) or held | **No staking exists for BTC** — yields via lending or simply held |
| **USDC** | Stable | **Lending** (Aave on Base, Kamino on Solana) | Best net yield, per-venue caps |
| LP positions | Overlay | DEX fees + incentives | Stable-pair or blue-chip/stable pairs only, blue-chip DEX, capped 15% |

**Crypto-sleeve sub-allocation (tunable default):** ETH 40% / wBTC 35% / SOL 25%
of the crypto sleeve. Adjustable; flagged as a parameter, not a commitment.

**Why liquid staking, not native:** native staking locks/unbonds the asset, which
would freeze it every time the rebalancer needs to trade it. Liquid staking
tokens (LSTs) keep the position tradeable while earning staking yield. The cost:
LSTs carry a small **de-peg tail risk** (see §6).

**Chains:** Base (ETH, wBTC/cbBTC, USDC, Aerodrome) and Solana (SOL, USDC,
Kamino, Orca). USDC is the cross-chain stable. The portfolio spans both chains;
the executor substrate already supports both.

---

## 3. The decision loop (the simplified brain)

The decision-engine collapses from "regime-classify → allocate-across-a-lake →
gate" to a single strategic-rebalancing loop:

```
every cadence tick:
  portfolio  ← read on-chain truth (balances, LST exchange rates, lent positions, LP positions)
  current_w  ← value each holding in USD → current sleeve weights
  target_w   ← fixed strategic target (60/40, asset sub-weights)
  drift      ← current_w − target_w
  if no sleeve outside its ±10% band:
        → HOLD (log)                                     # the common case
  else:
        trades ← minimal set to bring breached sleeve(s) back inside band
        cost   ← estimate gas + slippage for `trades`
        if correction_value < cost_gate_margin × cost:
              → HOLD, log suppressed_rebalance           # too small to be worth it
        else:
              → for each trade: risk gate → exposure caps → breakers → executor
```

No regime model, no inference, no lake allocator, no promotion gate. The yield
routing (which lending venue, acquiring/maintaining LSTs, LP overlay placement)
runs as a separate, slower maintenance pass — see §7 P2/P3.

---

## 4. Architecture — kept / dissolved / built-new

The pivot is a **refocus, not a rewrite**: the sound execution/risk substrate
stays and becomes central; the discovery/lake machinery dissolves.

### Keep (sound; now the heart of the system)
- `ts-executor` (Base) and `solana-executor` (Solana) — real, tested executors.
  Kept as **separate processes** (the one justified split: blast-radius isolation
  for capital safety + the Python↔Node boundary is forced anyway).
- decision-engine's **risk pre-trade gate, exposure limiter, circuit breakers** —
  but now actually fed live state (the critique found `.update()` was never
  called).
- Multisig (Safe / Squads) + contract **allowlist** (fail-closed).
- **Postgres** as state of record + audit trail; **Redis** as the brain→executor
  order bus.

### Dissolve (was the discovery apex)
- `extractor-worker` — entire service (no discovery).
- `backtest-worker` — entire service (no strategy validation). *(A lightweight
  allocation-policy sanity backtest may return later; not in scope now.)*
- `lake-governor`'s paper-trade harness, state machine, promotion gate, Discord
  approve/reject flow — gone. **Its decay/health-monitoring concept is
  repurposed** (see built-new).
- The 5 strategy templates + the template DSL/registry.
- `inference` (Ollama) — dropped.
- Regime classifier in decision-engine — dropped.

### Build new (the foundation that was stubbed)
1. **Allocation engine** — fixed target weights, on-chain current weights, drift,
   band check. *(Replaces the lake allocator.)*
2. **Cost-gated rebalancer** — minimal-trade computation + cost estimate + gate.
3. **Order resolver** — `(chain, protocol, asset) → contract addresses +
   decimals`; `USD amount → token smallest-unit` via price feed; `recipient =
   Safe/Squads address`. *This is critique finding #3 (~150 lines + a static
   address book) — now a central component, not a gap.*
4. **Real yield/price data layer** — replaces the zeroed adapter (critique #4):
   real lending APY, real spot prices, real **LST exchange rates**, real
   gas/slippage quotes. This is the foundation; everything else sits on it.
5. **Portfolio health monitor** — stablecoin depeg, **LST depeg**, venue
   TVL/health; triggers a de-risk action (route to safest venue / unwind). Reuses
   the Page-Hinkley/breaker concept from `lib/lake_metrics`, repointed at
   net-yield + peg signals, and **actually wired into the runtime tick** (the
   critique found it never ran).
6. **Yield router** — stable sleeve: pick best net lending venue under per-venue
   caps; crypto sleeve: acquire/maintain LSTs (ETH, SOL) and lend wBTC; LP
   overlay: place up to the 15% cap.

### Resulting topology (~5 containers, down from 12)
```
icarus-brain (Python)   decision/allocation/rebalance + risk gate + health monitor
ts-executor (Node)      Base execution            ┐ kept separate for
solana-executor (Node)  Solana execution          ┘ blast-radius isolation
postgres                state of record + audit
redis                   brain → executor order bus
[webapp or grafana]     read-only portfolio dashboard (keep one, not both)
```

---

## 5. Data layer (the foundation — build first)

The critique's root finding was that the data layer returns zeros and mismatched
keys, so nothing downstream can be real. This layer is **P1's prerequisite**:

- **Spot prices** — ETH, SOL, BTC, in USD, from a reliable feed (RPC oracle /
  pricing API). Drives all USD valuation and the USD→smallest-unit conversion.
- **LST exchange rates** — wstETH/ETH, jitoSOL/SOL, etc. The crypto sleeve is
  valued at LST × rate, not 1:1.
- **Lending APY** — Aave (Base), Kamino (Solana), net of any fees. *(Fix the
  key-format mismatch from critique #4 here once and for all — one canonical key
  scheme, validated at the adapter↔consumer seam by a real test, not a fixture.)*
- **Gas / priority-fee + slippage quotes** — real, per-chain, for the cost gate.
  No more `gas_gwei=0` / `minOut=0`.

Every value must be exercised by a test that uses the **production adapter**, not
an injected fixture (the critique found the green e2e manufactured its inputs).

---

## 6. Risk & safety model

Capital preservation is the primary constraint, enforced structurally:

- **Protocol allowlist (fail-closed)** — only blue-chip venues (Aave, Kamino,
  Lido/wstETH, Jito, Aerodrome, Orca). Empty allowlist rejects all (kept from v2).
- **Per-venue exposure caps** — no single lending/LP/staking venue exceeds a cap
  (e.g. ≤25% of portfolio per venue); the exposure limiter, now fed live
  positions (critique found it ran against empty positions).
- **Circuit breakers, wired to live state** — drawdown + per-venue health, now
  driven from the runtime tick (critique #2 found they never ran). These can pull
  capital to the safest venue / to stables.
- **Depeg / health monitor** — the one non-obvious tail in this otherwise-
  conservative design:
  - **Stablecoin depeg** (USDC off-peg) → halt deploys, optionally rotate.
  - **LST depeg** (wstETH/jitoSOL discount under stress) → the crypto sleeve is
    held as LSTs, so a sustained LST discount is real markdown risk. Monitor the
    LST/underlying ratio; on a threshold breach, **stop treating the LST as 1:1
    and flag/de-risk** rather than rebalancing into a distorted price.
- **Custody** — Safe (Base) / Squads (Solana) multisig. The Squads path is
  currently a single-signer stub (critique); real multisig is required before
  meaningful capital and is a P3 item.
- **Cost-gating** — the rebalancer's cost gate is itself a capital-protection
  control: it prevents bleeding the portfolio to gas/slippage via over-trading
  (the critique's largest economic risk).

---

## 7. Phased implementation plan

Each phase ends with a **real, observed** result on real data — not a fixture.

### P1 — Prove the substrate (one real rebalance round-trip)
Goal: a single correct rebalance executes end-to-end on the existing executor,
with real prices and correct order construction. This retires the critique's
fatal findings #3 and #4 and proves the brain→executor→chain path.
- Build the **order resolver** (addresses, decimals, USD→smallest-unit, recipient).
- Build the **spot-price + gas/slippage** slice of the data layer.
- Hardcode a trivial target (e.g. hold **USDC + ETH at 60/40, no staking, no LP**).
- Implement **allocation engine + cost-gated rebalancer**.
- Wire breakers + exposure limiter to **live state**.
- Add an **execution-results consumer** that reconciles positions to on-chain truth.
- **Real end-to-end test** using production adapter + real order construction.
- **Exit criterion:** observe one real rebalance: correct asset, size, route,
  settlement, and reconciled position — net of real cost.

### P2 — Yield routing + liquid staking
- Lending APY data (Aave/Kamino), net-yield venue selection under caps.
- Stable sleeve → lending; wBTC → lending; **ETH/SOL → liquid staking** (acquire
  + value via LST exchange rate).
- Add wBTC and SOL to the asset set; full 60/40 + sub-weights.
- **Exit criterion:** portfolio holds the full target, all yield-bearing,
  rebalances correctly across LSTs and lent positions.

### P3 — LP overlay + full safety
- LP overlay (≤15% cap), stable/blue-chip pairs, blue-chip DEX, with `minOut`
  from real quotes (no naked `minOut=0`).
- **Portfolio health monitor** live in the tick: stablecoin + LST depeg, venue
  health → de-risk actions.
- Real **Squads multisig** (retire single-signer); MEV-protected execution.
- **Exit criterion:** full design running with all safety controls active; a
  simulated depeg/health event triggers the correct de-risk.

---

## 8. Open parameters (tunable; defaults set)

| Parameter | Default | Owner decision |
|---|---|---|
| Crypto : Stable | 60 / 40 | set ✓ |
| Crypto sub-weights (ETH/wBTC/SOL) | 40 / 35 / 25 | default, tunable |
| Rebalancing band | ±10% | set ✓ |
| LP cap | 15% | set ✓ |
| Cost-gate margin | 4× | default, tunable |
| Per-venue cap | 25% | default, tunable |
| Check cadence | hourly | default, tunable |
| Stablecoin depeg threshold | TBD (e.g. 0.5%) | needs operator input |
| LST depeg threshold | TBD (e.g. 1–2% sustained) | needs operator input |

---

## 9. Supersession map

Relative to `docs/blueprint.md` (retained as history):

| Blueprint area | Status |
|---|---|
| Strategy Lake / extractor / backtest / templates / promotion gate | **Superseded** by this doc (dissolved) |
| Regime classification + inference advisor | **Superseded** (dropped) |
| 3-cluster / 9-service decomposition | **Superseded** by §4 (~5 containers) |
| Execution cluster (executors, risk gate, breakers, multisig, allowlist) | **Retained**, refocused per §4/§6 |
| Postgres state-of-record + Redis bus | **Retained** |
| Capital-preservation invariants (gates fail-closed, allowlist, caps) | **Retained + strengthened** |

> Follow-up (not done in this doc): annotate the superseded sections of
> `blueprint.md` and `CLAUDE.md` with a pointer here, so future readers aren't
> misled by the old architecture summary.

---

## 10. The one hard part

Everything in this design is deliberately boring except **LST de-peg risk**. The
crypto sleeve earns yield by holding liquid staking tokens, which normally track
their underlying within a few bps but can trade at a sustained discount under
stress (validator slashing, mass-exit queues, liquidity crunches). Because the
sleeve is 60% of the portfolio, a mishandled LST discount is the one place this
conservative design carries a real tail. The mitigation (§6) — value LSTs by
their actual exchange rate, monitor the ratio, and de-risk rather than rebalance
through a distorted price — is the item that deserves genuine thought rather than
a default, and it is the most important thing P3 gets right.
