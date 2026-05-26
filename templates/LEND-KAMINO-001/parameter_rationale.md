# LEND-KAMINO-001 Parameter Rationale

This document is consumed by the LLM-as-judge plausibility check (Q8). It
justifies each search-space parameter range so the judge can flag templates
whose ranges look fishy before they enter the backtest queue.

The template is the Solana / Kamino analog of LEND-001 (Aave V3 / Base).
Same risk class — blue-chip stablecoin supply rotation on a major money
market. Cross-chain risk is the variable under test here; the strategy
logic is held fixed.

## apy_threshold (continuous, [0.005, 0.100], log scale)

- **Lower bound 0.005 (0.5%):** lower than LEND-001's 1% floor because
  Kamino USDC/USDT supply APYs spend more of the year in the 0.5-2% band
  than Aave Base does. Pulling the floor down lets the backtest discover
  a "stay deployed at low yield" optimum if that's what the data supports.
- **Upper bound 0.100 (10%):** same as LEND-001. Kamino reserves have
  briefly spiked to 8-9% during Solana-DeFi-summer windows (Jito/MEV-
  driven liquidity squeezes); 10% is the soft ceiling.
- **Log scale (vs LEND-001's linear):** Solana stablecoin yields cluster
  in the low end of the range almost all the time. Linear sampling would
  waste most of the 10-step grid on the rarely-realized upper half.
  Log scale gives roughly equal coverage per order of magnitude.

## min_liquidity_usd (grid, [500k, 1M, 2.5M])

- **500k:** widest universe — includes Kamino's emerging reserves
  (LSTs, smaller stables). Higher withdrawal-queue risk during
  congestion. Matches small-AUM portfolio behaviour.
- **1M:** the standard tier. Aligns with LEND-001's middle bucket so
  cross-chain Sharpe comparisons aren't confounded by universe size.
- **2.5M (vs LEND-001's 2M):** Kamino's main pool reserves (USDC/USDT)
  are typically deeper than Aave Base's, so the "blue-chip only" tier
  shifts higher. Trades execution quality for narrower edge.

## priority_fee_amortization_days (grid, [3, 7, 14])

LEND-001 uses [7, 14, 21] for Base L2 gas. The whole grid shifts left
on Solana because priority fees are cheap (typical p50 ~50_000
microlamports ≈ $0.0000075 per tx at SOL=$150) and amortize in literally
zero days even on tiny positions. The grid still spans 3-14 days so the
backtest catches:

- **3:** aggressive churn — rotate on small APY deltas. Almost no cost
  penalty because fees are so cheap, but the value of `priority_fee_*`
  as a search dimension drops to noise in this regime.
- **7:** middle — matches typical 1-week APY reassessment cadence on
  Kamino reserves.
- **14:** sticky — slower reaction to APY moves. Mostly indistinguishable
  from `7` in low-congestion regimes; differentiates during congestion
  spikes where priority fees rise 100-1000×.

The parameter exists primarily so the contract shape matches LEND-001 —
that lets the W4 search engine treat both templates identically. If the
backtest finds the parameter has zero discriminative power, that's a
useful negative result.

## asset_variant (categorical, [USDC, USDT, SOL])

- **USDC:** Circle-native, deepest reserve on Kamino. The canonical
  default.
- **USDT:** Tether — second-deepest. APY occasionally diverges from
  USDC by 50-200bps; the search picks whichever historical record won.
- **SOL:** non-stable, included because Kamino's SOL reserve APY is
  occasionally competitive with stable yields when JitoSOL/MEV dynamics
  drive supply demand. Carries SOL price risk — `risk_profile: low` in
  the manifest is still appropriate because the strategy is short-
  duration and the allocator caps exposure via `allocation_max: 0.70`,
  but SOL-variant runs should be flagged as price-sensitive.

### Why USDT here when LEND-001 used USDbC?

LEND-001 is on Base where the two canonical stables are USDC (native)
and USDbC (legacy bridged). On Solana there is no equivalent legacy
bridged USDC — the bridged-USDC.so variant has been deprecated. USDT
is the natural second stable for the Solana variant. SOL fills the
third slot because Kamino's SOL reserve is a real product (it's not
just a placeholder asset), and including it tests whether the cross-
asset rotation logic survives a non-stable being in the universe.

## Why no `max_position_size` parameter?

- Sizing is the *allocator's* job (per blueprint §"Allocator").
- This template's `allocation_max: 0.70` in the manifest is a *ceiling*
  applied by the allocator, not a parameter the search can tune. The
  search picks signal thresholds; the allocator decides how much to bet
  on the signal.

## Cross-chain provenance

This template is structurally identical to LEND-001:

| Field                            | LEND-001 (Base)            | LEND-KAMINO-001 (Solana)             |
| -------------------------------- | -------------------------- | ------------------------------------ |
| APY key                          | `aave_v3.{asset}.base`     | `kamino.{asset}.solana`              |
| Pool key                         | `base:aave_v3:{asset}`     | `solana:kamino:{asset}`              |
| Cost source                      | `market_data.gas_gwei`     | `metadata.priority_fees.solana_p50_microlamports` |
| Cost model                       | gas units × gwei × ETH/USD | microlamports / 1e15 × SOL/USD       |
| allocation_max                   | 0.70                       | 0.70                                 |
| risk_profile                     | low                        | low                                  |
| expected_metrics                 | identical                  | identical                            |

Cross-chain risk is the variable under test. If LEND-KAMINO-001's OOS
Sharpe diverges sharply from LEND-001's, that's the v2 DSL surfacing a
real cross-chain effect — exactly the W7 milestone the blueprint asks
for ("third template: Solana-targeted").
