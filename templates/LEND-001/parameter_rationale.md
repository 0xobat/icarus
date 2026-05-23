# LEND-001 Parameter Rationale

This document is consumed by the LLM-as-judge plausibility check (Q8). It
justifies each search-space parameter range so the judge can flag templates
whose ranges look fishy before they enter the backtest queue.

## apy_threshold (continuous, [0.010, 0.100])

- **Lower bound 0.010 (1%):** stablecoin lending below 1% is generally
  worse than US T-bills. Holding USDC below this floor has negative real
  yield in most environments.
- **Upper bound 0.100 (10%):** Aave USDC supply APY on Base in 2024-2025
  briefly peaked near 8-9% during DeFi summer; 10% is a soft ceiling that
  the search should almost never select but lets us catch the edge case
  if it materializes.
- **Search density:** linear with 10 default steps gives ~1% resolution.
  Coarse enough to span the range; fine enough to differentiate between
  blue-chip yields (2-4%) and risk-on yields (5-8%).

## min_liquidity_usd (grid, [500k, 1M, 2M])

- **500k:** wide universe, accepts emerging pools. Higher slippage risk
  on size; matches behaviour expected from a small-AUM portfolio.
- **1M (v4.2 default):** the v4.2 hand-coded constant. Reasonable balance
  between universe and execution quality.
- **2M:** blue-chip only. Trades execution quality for narrower edge.

Three points form a useful interaction with `apy_threshold` — high APYs in
2M pools are rare; low APYs in 500k pools are common.

## gas_amortization_days (grid, [7, 14, 21])

- **7:** aggressive churn — only rotates when the APY delta is fat enough
  to pay back gas in one week. Conservative.
- **14 (v4.2 default):** the v4.2 hand-coded constant. Roughly two weeks
  matches the typical Aave reserve-rate adjustment cadence.
- **21:** sticky — slower to react to APY moves but lower gas drag.

Interacts with the realised Base L2 gas environment — when gas is cheap
(typical for Base) the value of this parameter shrinks; when gas spikes
it dominates the rotation cadence.

## asset_variant (categorical, [USDC, USDbC])

- Both are Base-native stablecoin variants with separate Aave reserves.
- USDC is the canonical Circle stablecoin; USDbC is the legacy bridged
  version. USDbC liquidity has been declining since 2024 but its APY can
  still spike opportunistically.
- The search lets the backtest pick which variant has the better
  historical record; we don't pre-commit at template-authoring time.

## Why no `max_position_size` parameter?

- Sizing is the *allocator's* job (per blueprint §"Allocator").
- This template's `allocation_max: 0.70` in the manifest is a *ceiling*
  applied by the allocator, not a parameter the search can tune. The
  search picks signal thresholds; the allocator decides how much to bet
  on the signal.

## Source provenance

The v4.2 hand-coded strategy used these defaults:
- `apy_threshold = MIN_APY_IMPROVEMENT = 0.005` (rotation threshold delta,
  not absolute APY — v2 simplifies to absolute)
- `MIN_SUPPLY_APY = 0.01` (exit floor)
- `MIN_LIQUIDITY_USD = 1_000_000`
- `GAS_AMORTIZATION_DAYS = 14`

v2 search ranges deliberately straddle these defaults so the backtest
recovers the v4.2 choice if it was actually optimal, or improves on it
if it was not.
