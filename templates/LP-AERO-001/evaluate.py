"""LP-AERO-001 — Aerodrome stable-pair LP yield rotation on Base.

Provide single-asset liquidity (via Aerodrome's stable-pair zap routes)
to the USDC/USDbC sAMM pool. Capture trading fees as yield. Exit when
the trailing fee yield collapses OR the pool's TVL drops below a
safety floor (signalling capital flight, which depresses realized
yield faster than the 7d trailing window can react).

This is a new strategy *shape* — neither pure lending (LEND-001) nor
delta-neutral basis (BASIS-PERP-001). Returns come from AMM trading
fees, not from a venue-published APY or a perp funding rate.

Logic:
  - Read the pool's 24h fees and current TVL from
    `market_data.pool_state["base:aerodrome:usdc-usdbc"]`.
  - Compute the annualized fee yield ≈ `fees_24h * 365 / tvl`.
  - Compute gas cost (enter + exit = 2 txs) in USD via the same
    Base-L2 model as LEND-001.
  - Enter if yield ≥ fee_yield_threshold AND tvl ≥ min_pool_tvl_usd
    AND gas amortizes within `gas_amortization_days` at the current
    yield AND we are not already holding.
  - Exit if (a) yield drops below fee_yield_threshold OR (b) TVL
    falls below min_pool_tvl_usd (capital flight signal).
  - Otherwise hold.

The function is pure: same inputs → same Decision. No I/O.
"""

from decimal import Decimal

from icarus.types import Decision, MarketSnapshot, PortfolioSnapshot

# The single stable-pair pool this v1 template targets. Future revs
# can extend to a small grid of stable-pair pools; for v1 we
# hard-target the deepest USDC pair on Aerodrome.
_POOL_KEY = "base:aerodrome:usdc-usdbc"

# Constants — gas cost model. Mirrors LEND-001 (same Base L2
# environment). LP enter + exit = 2 supply-equivalent txs; we
# model both up front so the strategy doesn't enter a position it
# cannot afford to exit.
_AERO_LP_GAS_UNITS_PER_TX = Decimal("180000")  # zap-in / zap-out is heavier than aave supply
_GAS_TXS_PER_ROUND_TRIP = Decimal("2")
_ETH_PRICE_FALLBACK_USD = Decimal("3500")
_NS_PER_GWEI = Decimal("1e9")
_DAYS_PER_YEAR = Decimal("365")


def _round_trip_gas_cost_usd(gas_gwei: Decimal, eth_price: Decimal) -> Decimal:
    """USD cost of one enter + one exit transaction at current gas."""
    eth_per_gwei = Decimal("1") / _NS_PER_GWEI
    per_tx = _AERO_LP_GAS_UNITS_PER_TX * gas_gwei * eth_per_gwei * eth_price
    return per_tx * _GAS_TXS_PER_ROUND_TRIP


def _annualized_fee_yield(fees_24h: Decimal, tvl: Decimal) -> Decimal:
    """Project 24h fees forward as an annualized rate over current TVL.

    Convention: `fees_24h` is the USD-denominated trailing-day fee
    revenue for the pool. The data adapter SHOULD smooth this with
    a 7d-trailing window before passing it through; here we just
    annualize whatever we get (no smoothing in evaluate.py — it
    stays pure).
    """
    if tvl <= 0:
        return Decimal("0")
    return fees_24h * _DAYS_PER_YEAR / tvl


def evaluate(
    params: dict,
    market_data: MarketSnapshot,
    portfolio_state: PortfolioSnapshot,
) -> Decision:
    fee_yield_threshold = Decimal(str(params.get("fee_yield_threshold", "0.10")))
    min_pool_tvl_usd = Decimal(str(params.get("min_pool_tvl_usd", "5000000")))
    gas_amortization_days = Decimal(str(params.get("gas_amortization_days", "7")))

    pool = market_data.pool_state.get(_POOL_KEY)
    if pool is None:
        # Adapter hasn't published pool state. Don't trade blind.
        return Decision(
            action="hold",
            target_size=Decimal("0"),
            confidence=Decimal("0.2"),
            reasoning=f"no pool_state entry for {_POOL_KEY}; cannot evaluate",
        )

    tvl = pool.tvl
    fees_24h = pool.fees_24h
    yield_apr = _annualized_fee_yield(fees_24h, tvl)

    # Gas amortization check — round-trip (enter + exit).
    eth_price = market_data.prices.get("ETH", _ETH_PRICE_FALLBACK_USD)
    round_trip_gas = _round_trip_gas_cost_usd(market_data.gas_gwei, eth_price)
    # On a $100 position at this yield, daily yield in USD:
    daily_yield_per_100 = Decimal("100") * yield_apr / _DAYS_PER_YEAR
    days_to_amortize = (
        round_trip_gas / daily_yield_per_100
        if daily_yield_per_100 > 0
        else Decimal("1e9")
    )
    gas_ok = days_to_amortize <= gas_amortization_days

    # Are we already in a LP-AERO-001 position?
    in_position = any(
        p.template_id == "LP-AERO-001"
        for p in portfolio_state.positions.values()
    )

    # ── Exit: yield collapse OR capital flight (TVL drop) ──────────
    if in_position and yield_apr < fee_yield_threshold:
        return Decision(
            action="exit",
            target_size=Decimal("0"),
            confidence=Decimal("0.9"),
            reasoning=(
                f"fee yield {yield_apr} below threshold {fee_yield_threshold}; "
                f"exit {_POOL_KEY}"
            ),
        )
    if in_position and tvl < min_pool_tvl_usd:
        return Decision(
            action="exit",
            target_size=Decimal("0"),
            confidence=Decimal("0.95"),
            reasoning=(
                f"pool TVL {tvl} dropped below safety floor {min_pool_tvl_usd}; "
                f"capital-flight signal — exit {_POOL_KEY}"
            ),
        )

    # ── Entry: yield qualifies, pool deep enough, gas amortizes ────
    if (
        not in_position
        and yield_apr >= fee_yield_threshold
        and tvl >= min_pool_tvl_usd
        and gas_ok
    ):
        return Decision(
            action="enter",
            target_size=portfolio_state.cash_usd,
            confidence=Decimal("0.8"),
            reasoning=(
                f"fee yield {yield_apr} >= threshold {fee_yield_threshold}, "
                f"tvl {tvl} >= min {min_pool_tvl_usd}, "
                f"round-trip gas amortizes in {days_to_amortize:.1f} days; "
                f"enter {_POOL_KEY}"
            ),
        )

    # ── Hold: explain why ──────────────────────────────────────────
    if in_position:
        reason = (
            f"fee yield {yield_apr} >= threshold {fee_yield_threshold} and "
            f"tvl {tvl} >= floor {min_pool_tvl_usd}; hold {_POOL_KEY}"
        )
    elif not gas_ok:
        reason = (
            f"round-trip gas amortization {days_to_amortize:.1f}d > "
            f"limit {gas_amortization_days}d"
        )
    elif tvl < min_pool_tvl_usd:
        reason = f"tvl {tvl} < min {min_pool_tvl_usd}"
    else:
        reason = f"fee yield {yield_apr} < threshold {fee_yield_threshold}"

    return Decision(
        action="hold",
        target_size=Decimal("0"),
        confidence=Decimal("0.5"),
        reasoning=reason,
    )
