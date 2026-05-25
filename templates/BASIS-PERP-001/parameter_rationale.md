# BASIS-PERP-001 — Parameter rationale

## Strategy overview

A **delta-neutral basis (cash-and-carry) trade** on ETH using:
- **Long leg:** ETH spot via Aerodrome (Base DEX).
- **Short leg:** ETH-USD perp via Synthetix Perps Base.

The two legs are sized to match notional, so the net exposure is ≈ zero
(delta-neutral). The strategy captures the **funding rate** the short
perp leg pays/receives. When perp funding is meaningfully positive
(longs paying shorts), the short side accrues funding income. The trade
is closed when funding drops below an exit threshold.

The alpha is *not* in price direction — net price exposure is zero. The
alpha is in the funding-rate stream net of fees, slippage, and gas.

## Multi-leg execution note

The v2 `Decision` dataclass carries a single `target_size`. For this
template, `target_size` represents the **per-leg USD notional** — i.e.
`target_size = 1000` means $1000 long spot ETH + $1000 short perp ETH
(gross exposure $2000, net $0).

The `decision-engine` allocator + executor split this into two orders
when it lands. For the backtest engine (W3), the simulator computes
combined PnL as:

```
trade_pnl = spot_pnl + perp_pnl + funding_received - funding_paid - fees - gas
          ≈ (long ETH price return) - (long ETH price return) + funding net
          = funding net - costs
```

The price-return terms cancel by construction (delta-neutral).
Backtest accuracy hinges on **funding rate history** + **fee/gas
realism**, not on price-path replay.

## Funding-rate data convention

The strategy reads the current 8-hour funding rate from:

```python
funding = market_data.metadata.get("funding_rates", {}).get(
    "synthetix_perps_base:eth-usd", Decimal("0")
)
```

`MarketSnapshot.metadata` is the v2 contract's documented extension
hatch for protocol-specific data the canonical fields don't carry. The
backtest engine (W3) populates `metadata["funding_rates"]` from the
DefiLlama funding-rate adapter or Synthetix's on-chain query.

For the *streak* check (`min_funding_streak`), the strategy reads:

```python
streak = market_data.metadata.get("funding_streaks", {}).get(
    "synthetix_perps_base:eth-usd", 0
)
```

`funding_streaks` is the count of consecutive 8-hour periods that
funding has been above `funding_threshold`. The data adapter
maintains this state across cycles; `evaluate.py` is pure.

## Parameter choices

### `funding_threshold` — continuous, log scale, 0.005% to 0.10% per 8h

The economically meaningful range is multiplicative (5.5% → 110%
annualized). Log-scale search samples densely where the strategy lives
(5–20% APR) and sparsely in the extreme tail (>50% APR is regime-
break territory the bot shouldn't anchor on).

### `exit_funding_threshold` — continuous, linear, 0% to 0.005% per 8h

Bound below `funding_threshold` to avoid flip-flop on a single tick.
Setting this to 0 means "exit only when funding goes to zero/negative";
setting it close to entry threshold means tight bands and high
turnover. The grid will pick the right balance per regime.

### `min_funding_streak` — discrete, {1, 3, 6}

Filters spurious one-tick funding spikes. 1 = no filter (enter
immediately); 6 = wait 48 hours of sustained funding before believing
the regime is real. Discrete because the difference between 3 and 4
periods is not economically meaningful; pick from a small set.

### `position_size_pct` — discrete, {0.25, 0.50, 0.75}

Smaller positions absorb perp margin volatility without forced
liquidation. 0.75 is aggressive — basis trades have been forcibly
unwound during funding-rate inversions; the buffer protects from
that. 0.25 is conservative; 0.5 is the typical balanced choice.

### `walk_forward: [60, 15, 3]` (default)

60-day train, 15-day test, 3-day step. Standard v1 default. Basis
funding regimes typically last weeks-to-months, so 60 days is enough
training data without averaging across two distinct funding regimes.

### `expected_metrics`

- **sharpe_min: 0.8** — basis trades are low-Sharpe steady streams.
  Anything above 1.5 in backtest deserves scrutiny.
- **max_dd_max: 0.08** — funding can flip negative for a few days
  during regime shifts; an 8% drawdown bound matches historical
  worst-cases on ETH perp funding inversions.
- **expected_annual_return_pct: 0.08** — 8% annual is the realistic
  asymptote of an ETH basis trade in normal conditions. Higher
  expected return is a flag for backtest overfitting.
