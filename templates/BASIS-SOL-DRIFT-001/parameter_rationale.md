# BASIS-SOL-DRIFT-001 — Parameter rationale

## Strategy overview

A **delta-neutral basis (cash-and-carry) trade** on Solana using:
- **Long leg:** SOL or ETH spot via Jupiter (Solana DEX aggregator).
- **Short leg:** SOL-perp or ETH-perp via Drift v2.

The two legs are sized to match notional, so the net exposure is ≈ zero
(delta-neutral). The strategy captures the **funding rate** the short
perp leg pays/receives. When perp funding is meaningfully positive
(longs paying shorts), the short side accrues funding income. The trade
is closed when funding drops below an exit threshold.

The alpha is *not* in price direction — net price exposure is zero. The
alpha is in the funding-rate stream net of fees, slippage, and Solana
priority fees.

## Why Solana / Drift instead of Base / Synthetix?

BASIS-PERP-001 already runs this shape on Base/Synthetix. Putting the
same shape on Solana/Drift exposes the strategy to a different funding
microstructure:

- **Drift funds hourly** (24x/day) on a 1-hour TWAP, vs Synthetix's
  8-hour cadence. Faster funding cadence means faster mean-reversion;
  fewer slow funding-rich regimes but more frequent short-lived
  opportunities.
- **Different liquidity:** Drift SOL-perp routinely tops $30M open
  interest; ETH-perp is roughly half that. Both well below Binance
  perp depth, well above smaller on-chain venues.
- **Different cost model:** Solana priority fees in microlamports
  rather than Base L2 gas. Per-tx cost is similar in USD; tx cadence
  is the differentiator (rebalances are cheap on Solana).

The two templates together let the backtest engine attribute alpha to
chain-specific microstructure vs strategy-shape.

## Multi-leg execution note

The v2 `Decision` dataclass carries a single `target_size`. For this
template, `target_size` represents the **per-leg USD notional** — i.e.
`target_size = 1000` means $1000 long spot + $1000 short Drift perp
(gross exposure $2000, net $0).

The `decision-engine` allocator + solana-executor split this into two
orders when it lands. For the backtest engine (W3), the simulator
computes combined PnL as:

```
trade_pnl = spot_pnl + perp_pnl + funding_received - funding_paid - fees - priority_fees
          ≈ (long price return) - (long price return) + funding net
          = funding net - costs
```

The price-return terms cancel by construction (delta-neutral).
Backtest accuracy hinges on **funding-rate history** + **fee/priority-
fee realism**, not on price-path replay.

## Funding-rate data convention

The strategy reads the current hourly funding rate from:

```python
funding = market_data.metadata.get("funding_rates", {}).get(
    "drift:sol-perp", Decimal("0")   # or "drift:eth-perp"
)
```

`MarketSnapshot.metadata` is the v2 contract's documented extension
hatch for protocol-specific data the canonical fields don't carry.
The backtest engine (W3) populates `metadata["funding_rates"]` from
Drift's on-chain `getFundingRate` query (one row per market per
hourly settlement).

For the *streak* check (`min_funding_streak`), the strategy reads:

```python
streak = market_data.metadata.get("funding_streaks", {}).get(
    "drift:sol-perp", 0
)
```

`funding_streaks` is the count of consecutive **hourly** periods that
funding has been above `funding_threshold`. The data adapter maintains
this state across cycles; `evaluate.py` is pure.

## Parameter choices

### `funding_threshold` — continuous, log scale, 0.002% to 0.05% per hour

Drift funds hourly so per-period thresholds are roughly 1/8 of an
8h-cadence venue's. Translated to annualized:
- low (0.00002/hr) ≈ 17.5% APR — fits the "barely profitable after
  costs" floor.
- high (0.0005/hr) ≈ 440% APR — regime-break territory we sample
  sparsely so the search doesn't anchor on it.

Log-scale search samples densely where the strategy lives (20-60%
APR) and sparsely in the extreme tail.

### `exit_funding_threshold` — continuous, linear, 0% to 0.002% per hour

Bound strictly below `funding_threshold` to avoid flip-flop on a single
hourly tick. Setting this to 0 means "exit only when funding goes to
zero/negative"; setting it close to entry threshold means tight bands
and high turnover. The grid will pick the right balance per regime.

### `min_funding_streak` — discrete, {3, 6, 12}

Filters spurious one-hour funding spikes. Shifted up vs BASIS-PERP-001's
{1, 3, 6}: on a 1-hour cadence, "3 streaks" is only 3 hours, so the
minimum filter has to be wider to provide comparable signal validation.
12 = wait 12 hours of sustained funding before believing the regime is
real.

### `position_size_pct` — discrete, {0.25, 0.50, 0.75}

Smaller positions absorb perp margin volatility without forced
liquidation. Drift's margin engine is more conservative than Synthetix's
(higher initial margin on perp), so the same fraction is safer here
than on Base — but the grid is unchanged for cross-template
comparability.

### `asset_variant` — categorical, {SOL, ETH}

Drift's two deepest perp markets. SOL-perp is the venue's flagship
market by open interest; ETH-perp is the second tier. Letting the
search pick lets the backtest discover which market historically
delivers the better funding-yield per regime — SOL funding tends to
spike harder on memecoin/staking flows, ETH funding tracks broader
crypto sentiment.

### `walk_forward: [60, 15, 3]` (default)

60-day train, 15-day test, 3-day step. Standard v1 default. Drift
funding regimes are noisier than Synthetix's (hourly cadence amplifies
short-lived spikes); 60 days is long enough to span multiple regimes
without averaging across only one.

### `expected_metrics`

- **sharpe_min: 0.8** — Drift basis trades are low-Sharpe steady
  streams when funding pays. Anything above 1.5 in backtest deserves
  scrutiny (likely an overfit hourly-spike pocket).
- **max_dd_max: 0.08** — funding can flip sharply negative on Drift
  during oracle-divergence minutes; an 8% drawdown bound matches
  historical worst-cases on SOL-perp funding inversions.
- **expected_annual_return_pct: 0.08** — 8% annual is the realistic
  asymptote of a Drift basis trade in normal conditions. Higher
  expected return is a flag for backtest overfitting.

## Cross-template comparability

This template intentionally mirrors BASIS-PERP-001's manifest shape
(same expected_metrics targets, same allocation_max, same sizing).
Differences are exclusively in the funding-period semantics. That
mirror lets the LLM-as-judge plausibility check (Q8) flag this
template if its acceptance hints drift away from the cross-chain
peer — a chain-specific edge case worth a manual review.
