# Icarus v2 (Daedalus) — First-Principles Architecture Critique

**Date:** 2026-06-03
**Branch:** `daedalus` (post-W12, "structural GO")
**Method:** Adversarial deep code dive (4 parallel domain agents) → personal
verification of the load-bearing findings → red-team rebuttal (defense counsel)
→ synthesis. Every structural claim is anchored to `file:line`.
**Posture:** Commissioned as an *adversarial* critique ("will it make money,
is it right-sized, re-derive from scratch"), then pressure-tested by a
charitable red-team. This doc presents both and a balanced verdict.

> Scope note: `py-engine/` and `frontend/` are **untracked legacy v4.2-style
> dirs**, not part of the committed v2 build. All findings below concern the
> tracked v2 tree (`decision-engine/`, `lake-governor/`, `backtest-worker/`,
> `extractor-worker/`, `ts-executor/`, `solana-executor/`, `webapp/`, `lib/`,
> `templates/`).

---

## Headline verdict

**Unfinished but sound, oversold at the finish line.**

This is a competently-architected, genuinely unit-tested *skeleton* whose
money-moving runtime seams are not yet connected. It has 1,019 passing tests
and a "build-complete / GO" rating, but **it has never executed a correct
trade or a real promotion** — because the integration seams between its
well-tested components are stubbed, TODO'd, or fed zeroed data.

Crucially (the red-team's correction to the prosecution): **three of the four
breaks fail _closed_** — the present-tense failure is *inaction*, not capital
loss. And three of four are 1–25 line fixes against already-tested building
blocks. This is not a doomed design.

But (the prosecution's surviving case): even fully wired, the **economic
premise is unproven** — the strategies are modest/decaying/regime-conditional,
the backtest optimizes the wrong return variable, cost drag plausibly exceeds
gross yield, and the one safety system that matters for incentive-harvest DeFi
(decay exit) is the very thing not running. The architecture is over-built for
*current* scale (defensible for *target* scale).

The single most actionable conclusion: **the W12 go/no-go's "GO — structurally"
is the wrong label.** It should read "NO-GO until the four money-path seams are
connected and exercised end-to-end on real data." See [The W12
contradiction](#the-w12-contradiction).

---

## The four verified findings (facts)

All four were personally re-verified at `file:line`; the red-team attempted
refutation and conceded all four as fact. Annotated with failure direction and
fix size (red-team's estimates, sanity-checked).

| # | Finding | Evidence | Fails | Fix size |
|---|---|---|---|---|
| 1 | `oos_sharpe` is always `None`; promotion gate skips every candidate | `search.py:288` (`oos_sharpe=None`), `runner.py:359-365` (join is a TODO), `promotion_gate.py:489` (`if … oos_sharpe is None: continue`) | **closed** (no promotion) | ~10–20 lines, `runner.py` — `oos_gate()` already exists + tested |
| 2 | Decay detector + per-template breaker never run; nothing pulls capital from a rotting strategy | zero non-test `PageHinkleyDetector(`; no callers of `demote_decay`/`demote_template_breaker`; `lake_governor/__main__.py:13-16` admits it | **open-ish** (a decaying live strategy is held) | ~20–30 lines in `_tick` |
| 3 | Orders carry a USD scalar, no token/recipient/decimals | `cycle.py:481` `OrderParams(amount=target_usd.copy_abs())` | **closed** (executor preflight rejects) | **~150 lines** + static address/decimals book |
| 4 | Data adapter ↔ template key-format mismatch → APY always 0 → strategy never enters | adapter emits `base:aave_v3:usdc` (`defillama.py:70`); LEND-001 reads `aave_v3.usdc.base` (`evaluate.py:48`) — yet the *same* file already uses colon-format correctly at `:52` | **closed** (HOLD) | ~1 line (template is self-inconsistent) |

**Why the "1,019 tests + GO" is misleading:** the e2e smoke test
*manufactures* the values the real pipeline fails to produce — it injects
**both** key formats (`harness/e2e_smoke.py:127-153`) and hand-stamps a literal
`oos_sharpe=Decimal("0.95")` (`e2e_smoke.py:320`). So the green e2e is a
fixture, not proof the seams compose. Component coverage is real; **seam
coverage is absent.**

---

## Lens 1 — Will it make money?

### 1A. As it stands today: no — it cannot trade at all.

The four breaks above sit on the single happy path signal→settlement:
strategies never enter (#4), so every decision is HOLD; if one fired the order
is malformed (#3) and bounces at preflight; if it executed the breakers
wouldn't protect it (breakers constructed but `.update()` never called —
`peak=current=0`, drawdown always 0); and state never reconciles (nothing
consumes `execution:results`). *(The Mar-2026 E2E notes' "all decisions are
HOLD, expected — no funded wallet" was a misdiagnosis; #4 is the cause.)*

**Charitable correction (red-team, accepted):** three of these fail *closed*.
The system today does *nothing*, it does not lose money wrongly.

### 1B. If every wire were connected: the premise is still weak.

This is the prosecution's surviving case — the red-team defended the *posture*
here, not the *economics*, and the economics stand.

**The strategies** (reasoned 2026 estimates):

| Template | Real net edge | Character |
|---|---|---|
| LEND-001 / LEND-KAMINO (stablecoin supply) | ~2–4%, at/below ~4.3% T-bill after gas; USDbC leg deprecated | Sub-risk-free |
| LP-AERO-001 (Aerodrome sAMM) | base fees 1–3%; headline is decaying AERO emissions; deprecated pair | Decaying incentive harvest |
| BASIS-PERP / BASIS-SOL-DRIFT (funding carry) | genuine but episodic/regime-conditional; thin Synthetix-Base liquidity | Real, tail-risky |

**The backtest optimizes the wrong variable.** NAV is marked to
`position_size × first-price-in-snapshot` (spot drift) while every template's
edge is *yield* (APY/funding/fees) that never touches NAV. The grid search
ranks lending-threshold params by ETH's price path; the (correctly
implemented) deflated-Sharpe machinery then deflates a meaningless number into
a column no one reads.

**Cost drag plausibly exceeds gross yield.** No slippage modeling;
`amountOutMin=0` on the Aerodrome swap path (open sandwiching); no cost-aware
suppression of small rebalances; no minimum-trade-size; 30s cycle. Order-of-
magnitude estimate: ~$75–150/round-trip dominated by MEV/slippage — on thin
stablecoin spreads, `minOut=0` alone can hand a swap's full slippage to a bot.

**The gate can't catch edgeless strategies.** 14 days ≈ 13 daily observations
(enormous Sharpe standard error); the filter `paper_sharpe ≥ 0.8 ×
backtest_oos_sharpe` has **no absolute floor** (≈0.24 bar for a barely-cleared
candidate); and it validates against a paper harness that models a single spot
asset (no funding/IL/fees). Plus the risk-parity allocator is dead code in prod
(`__main__` hardwires an empty returns lookup → always equal-weight), and it's
blind to correlated clusters (the two stablecoin templates are one economic bet;
the per-`template_id` cap doesn't bind them).

**Red-team's strongest counter (granted as a real but unproven strength):** for
a crypto-native operator already holding crypto, the benchmark is
crypto-denominated, not USD — idle-crypto yield + disciplined incentive harvest
+ bull-regime carry can beat the friction of off-ramping to T-bills. And the
*strategy-lake as a search process* (cheap ~$0.30–1 frontier-API lottery
tickets behind a rigorous fail-closed validator) is a genuine flywheel concept.
**Verdict: a real conceptual strength, entirely unvalidated by anything that
runs today.**

---

## Lens 2 — Is it right-sized?

**Genuinely contested; net: over-built for current scale, defensible for target
scale.**

**Prosecution:** 9 app services + 3 infra, **zero** declaring
`replicas`/`cpus`/`deploy:`; every Python service a single `asyncio.run()`
loop; a **16,716-LOC shared `lib/`** (larger than any service) imported by all
five Python entrypoints, which are 210–333-line `__main__` shells over it —
i.e. a monolith's `src/` deployed as N containers. The "Postgres-only
cross-cluster seam" is **2 NOTIFY channels** (`lib/.../db/notify.py`) while the
actual capital path uses **Redis** pub/sub (`cycle.py:103`), contradicting the
stated rule. The seam adds an asyncpg-listener-with-reconnect failure class to a
Python↔Python boundary that, in one process, is a function call.

**Defense (partly granted):**
- **Blast-radius isolation** — a crashing `extractor-worker` (flaky frontier
  API + agentic repair) physically cannot take down the live signer. Real
  capital-safety value.
- **The Python↔Node split is forced anyway** (viem/Safe SDK vs vectorbt/ML), so
  a wire boundary is mandatory; clean service boundaries on each side are
  cheaper at the margin than claimed.
- **Independent deploy cadence** — ship a new extractor without restarting the
  key-holding executor.
- **Postgres state-of-record + LISTEN/NOTIFY = durable, replayable, auditable
  forensic trail** — valuable for a money-moving system.

**Synthesis:** blast-radius and audit-trail are real and worth keeping. But the
audit trail needs *Postgres writes*, not *separate processes* — a monolith can
write the same rows. The forced Python↔Node boundary justifies **2 processes**,
not 9. The honest minimal design that keeps every stated goal:

- `icarus-brain` (Python, 1 proc): decision-engine + lake-governor + extractor
  + backtest as coroutines/workers; roster/executions cross-talk → shared
  in-memory state (delete `notify.py` + roster-listener reconnect machinery).
- `icarus-signer` (Node, 1 proc) — or keep Base/Solana split for blast-radius
  (the one defensible reason for a 3rd process).
- `postgres` (state + audit) + `redis` (the one necessary brain→signer wire).
- Drop Ollama (advisory, swallowed on timeout) and webapp (Grafana already
  reads Postgres).

The defense's own concession is the fair verdict: **"right architecture for a
mature, multi-operator, real-AUM system; premature for one operator pre-live."**

---

## Lens 3 — Re-derived from scratch

Start from the goal: *one operator, ~$100K, grow it on-chain, minimal daily
effort, strong downside protection.* The binding constraints, in order:

1. **Accurate net-of-cost yield data per venue** (currently faked with zeros —
   `fees_24h=0`, `prices={}`, wrong key format).
2. **Execution quality** — thin yields die to slippage/MEV/churn.
3. **Decay/depeg/exploit exit** — incentive yields collapse; this is the real
   risk, not Sharpe drawdown.
4. *Then*, far down: which strategies.

The current system **inverts this** — sophistication into #4 (LLM extraction,
deflated Sharpe), stubs for #1/#2/#3. That is the core architectural error.

What I'd build:

- **No LLM strategy discovery in v1.** Academic-paper strategies are mostly
  TradFi factor bets that don't map on-chain; DeFi-yield edge is timing +
  execution, not discovery. Hand-encode 3–5 known yield sources. Keep the LLM
  *offline* as a research assistant to the operator — never in the capital path
  (kills the Ollama container).
- **Build the net-yield-after-cost data feed first** — it's the product. Real
  APY, real incentive schedules with decay curves, real gas/slippage from live
  quotes.
- **Execution: minimize round-trips, MEV-protect, cost-gate.** Private relay /
  `minOut` from real quotes; hard rule: don't rebalance unless `Δyield ×
  horizon > round-trip cost`. This one gate likely matters more to PnL than the
  entire lake.
- **Make decay-exit the core safety system, day one** — Page-Hinkley on
  *realized net yield* + depeg/TVL-collapse triggers that actually pull capital;
  cluster risk caps by *economic bet*, not `template_id`.
- **Shape:** ~2 processes (Python brain + Node signer) + Postgres. No Redis
  seam, no LISTEN/NOTIFY between Python modules, no advisory LLM, no separate
  dashboard.
- **Honest expected return:** T-bill + a few points of harvested
  incentive/carry *if* execution and decay-exit are excellent — with explicit
  acknowledgment that the tail (depeg/exploit/funding inversion) is where the
  money is made or lost.

**What the red-team changes here:** if the operator is crypto-denominated and
values the discovery flywheel, the strategy-lake is not *wrong* — but it should
be built *after* #1–#3 are solid, not before. The flywheel is a v2 luxury; the
data feed + execution + decay exit are the v1 product.

---

## What's genuinely good (fairness)

The craft is frequently excellent; the failures are integration + premise, not
competence:

- Deflated-Sharpe is correct Bailey/López de Prado (expected-max-of-N,
  non-normality SE, kurtosis handling).
- **Fail-closed everywhere:** empty allowlist rejects all txs; risk-gate throws
  → reject; empty Discord operator list → no promotion; missing `oos_sharpe` →
  no promotion (finding #1 is itself a safety stop); decision-engine refuses to
  boot without an explicit capital denominator.
- Extractor **AST-lints smoke tests before disk** — closes a real
  prompt-injection RCE.
- ts-executor serializes orders + refuses re-broadcast once a tx hash exists —
  genuine double-spend protection.
- The one *necessary* boundary (Python brain → Node signer) is correctly
  identified and contract-validated (the 3×-maintained `OrderEnvelope` schema
  is the irreducible tax of the one justified split).
- Human-in-the-loop promotion (manual Discord APPROVE, 0.5× live cap, criteria
  as code not env vars) is a real, under-weighted strength.

---

## The W12 contradiction

The most concrete, citable problem with the *current* project state: the W12
go/no-go doc is internally inconsistent.

- Criterion 7 "Decay removal — ✅ GO" claims a "90s from detector trip to
  allocation→0" worst case (`docs/W12-go-no-go.md:124-133`)…
- …directly contradicted by the same doc's note #4
  (`docs/W12-go-no-go.md:244-248`): the decay/breaker loops "Currently they
  don't actually run."
- Findings #1 (oos_sharpe) and #3 (order params) are **not disclosed anywhere**
  in W12; criterion 5 is "✅ GO / Cannot bypass" without noting the gate never
  fires for real candidates because `oos_sharpe` is always null.

"GO — structurally" is defensible only if "structurally" means "blocks exist,
seams untested" — but the doc elsewhere asserts *runtime behavior* ("runs
per-tick") that is false. The bug is the verdict label, not (mostly) the
building.

---

## Recommended next actions (decision framing)

Two coherent paths; pick based on whether the **premise** is something you want
to validate or replace.

**Path A — Finish v2 to a true end-to-end (honest "can it trade?" test).**
Smallest set of changes to make one real round-trip happen and be observable:
1. Fix #4 (~1 line) — template key format.
2. Make one adapter emit *real* APY/fees/prices (not zeros) for one venue.
3. Fix #3 (~150 lines) — order resolver: (chain, protocol, asset)→address +
   decimals + USD→smallest-unit + recipient=Safe/Squads.
4. Wire #1 (~15 lines) — call existing `oos_gate()` in `runner.py`, stamp
   `oos_sharpe` on top-K.
5. Wire #2 (~25 lines) — drive `PageHinkleyDetector`/`TemplateBreaker` +
   `demote_*` from the lake-governor `_tick`.
6. Feed the breakers live state (`.update()` from the runtime) + add an
   `execution:results` consumer that reconciles positions.
7. Add a real end-to-end test that uses the *production* adapter and order
   construction — no injected fixtures.
Then run *one* paper-trade on real data and measure: does it enter, size, route,
and exit correctly, net of real cost?

**Path B — Rethink the premise before more wiring** (Lens 3). If the honest
expected return is "T-bill + a few points with tail risk," decide whether the
strategy-lake machinery earns its complexity, or whether a far smaller
data-feed-first / execution-first / decay-exit-first design (≈2 processes) is
the right v1, with the lake as a later flywheel.

A pragmatic sequence: **A first** (it's cheap and produces the first real
evidence either way), then use that evidence to decide **B**. Don't do more
than the 7 steps above until one real round-trip has been observed.

---

## Method & confidence

- 4 parallel read-only deep-dive agents (edge generation, execution economics,
  right-sizing, capital lifecycle), each grounding claims in `file:line`.
- The 4 load-bearing findings personally re-verified by direct grep/read.
- 1 red-team defense agent attempted refutation (conceded all 4 facts) and
  supplied the charitable reframes + fix-size estimates incorporated above.
- **High confidence:** the 4 verified findings; the seam-coverage gap; the W12
  contradiction; the right-sizing measurements (LOC, channel counts).
- **Reasoned judgment (medium confidence):** 2026 strategy economics; cost-drag
  magnitude; the from-scratch redesign. These are arguments, not measurements —
  the honest way to settle them is Path A's one real round-trip.
