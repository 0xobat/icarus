# LP-AERO-001 — Parameter rationale

## Strategy overview

A **stable-pair LP yield rotation** on Base using:
- **Venue:** Aerodrome Finance, the dominant ve(3,3)-style AMM on Base.
- **Pool:** USDC/USDbC stable-pair (sAMM) — flat curve, low impermanent
  loss, single-asset entry/exit via the venue's zap routes.

Returns come from **trading fees**, not from a venue-published APY
(unlike LEND-001) or a perp funding rate (unlike BASIS-PERP-001). The
strategy enters when the trailing fee yield is above a threshold and
exits when fees collapse OR when pool TVL drops below a safety floor
(a capital-flight signal that depresses realized yield faster than the
7d trailing window can react).

## Why a new strategy shape?

The first three reference templates (LEND-001, LEND-KAMINO-001,
BASIS-PERP-001) cover two strategy shapes: lending rotation and basis
trade. LPing is the most-cited "third pillar" of on-chain yield and
exercises parts of the v2 stack the lending and basis templates don't:

- **Composite signal:** `fee_yield = fees_24h × 365 / tvl` — derived
  from two pool-state fields rather than a single APY field. The
  registry contract and the data adapter must serve both.
- **Two exit conditions:** yield collapse OR capital flight. The
  decision tree branches on a *non-yield* signal (TVL flight) — a
  shape the lending templates never exercise.
- **Round-trip gas model:** LP gas is heavier than lending gas, and
  the strategy amortizes both enter AND exit costs up front (lending
  templates amortize only the entry tx). This shakes out the gas
  amortization knob's general-case behavior.

## Pool-state data convention

The strategy reads pool state from:

```python
pool = market_data.pool_state.get("base:aerodrome:usdc-usdbc")
yield_apr = pool.fees_24h * Decimal("365") / pool.tvl
```

`PoolState.fees_24h` is the USD-denominated 24h trailing fee revenue
for the pool. The data adapter SHOULD smooth this with a 7d-trailing
window before passing it through — `evaluate.py` stays pure and uses
whatever it receives, so the smoothing window lives upstream.

`PoolState.tvl` is the same TVL the lending templates read; the units
and semantics are identical.

## Parameter choices

### `fee_yield_threshold` — continuous, log scale, 5% to 30% APR

- **Lower bound 5%:** roughly the floor where LPing beats parking
  USDC in Aave V3 on Base after fees and IL allowance. Below this
  the strategy has no edge.
- **Upper bound 30%:** "the pool is on fire" — typically a transient
  incentive event or a brief volume spike. The search should pick
  these up but log-scale sampling means it doesn't anchor on the
  extreme tail.
- **Log scale:** a 5% pool and a 30% pool are an order of magnitude
  apart in expected return, not a linear ramp. Log sampling spaces
  the search points across the multiplicative range.

### `min_pool_tvl_usd` — grid, {$2M, $5M, $10M}

Three points balancing universe width vs slippage / capital-flight
risk:

- **$2M:** wide universe; small AUM can fit without moving the pool.
- **$5M:** balanced default — Aerodrome's USDC/USDbC pool typically
  sits well above this; the floor only bites during stress.
- **$10M:** blue-chip only.

The same threshold doubles as the **capital-flight safety floor** —
if TVL drops below `min_pool_tvl_usd` *after* entry, the strategy
exits unconditionally. This is the new branch in the decision tree
that lending templates don't have. One parameter, two uses, by design.

### `gas_amortization_days` — grid, {3, 7, 14}

Mirrors LEND-001's gas amortization knob with a shift to lower
values:

- **3:** aggressive churn — only enter when fee yield is fat enough
  to pay back enter + exit gas in three days.
- **7 (default):** balanced — matches a typical "yield-following"
  rotation cadence on Base.
- **14:** sticky — slower to react to yield shifts but lower gas
  drag.

Shifted left vs LEND-001's {7, 14, 21} because LP positions are
expected to turn over faster than lending positions — the strategy
expects fee yields to shift as pool dynamics evolve, and we want
the parameter set to be calibrated to that rotation rhythm.

The amortization model accounts for **both** transactions (enter +
exit) rather than just one, because exiting an LP position is
non-optional (you can't roll an LP forever — eventually fees drop
and you must rotate). Modeling only the entry cost would understate
the true round-trip cost.

### Why no `position_size_pct` parameter?

- Unlike BASIS-PERP-001, this strategy is unleveraged — there's no
  margin volatility to absorb with reserve cash.
- The allocator caps `target_size` to `allocation_max = 0.40` of NAV
  from the manifest; the strategy emits its full available cash and
  lets the allocator size it down per the global risk envelope.
- Adding a sizing parameter here would be redundant with the
  allocator's existing knobs.

### `expected_metrics`

- **sharpe_min: 0.6** — between lending (0.5) and basis (0.8). Stable-
  pair LPing is moderate-Sharpe steady when fees are on, lower when
  the pool dries up.
- **max_dd_max: 0.10** — TVL flight can drag realized yield negative
  for short periods; 10% bound is the empirical worst-case for
  USDC/USDbC during the 2024 incentive-rotation periods.
- **expected_annual_return_pct: 0.06** — 6% annual is a reasonable
  base case for a stable-pair LP on Base. Higher expected return is
  a flag for backtest overfitting (likely fit to a brief incentive
  window).

## Source provenance

No v4.2 equivalent — this is a v2-original strategy shape introduced
to broaden the reference template coverage from {lending, basis} to
{lending, basis, LP}. The defaults are calibrated to Aerodrome
USDC/USDbC pool stats observed in 2024-2025 (typical fee yield 8-15%,
typical TVL $20-50M, occasional incentive spikes to 25%+).
