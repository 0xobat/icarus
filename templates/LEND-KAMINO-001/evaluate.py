"""LEND-KAMINO-001 — Kamino stablecoin supply rotation on Solana.

Solana analog of LEND-001 (Aave V3 / Base). Same risk class, different
chain + venue. The shape of the evaluate is identical to LEND-001's; the
differences are the market-data keys it reads (Kamino reserves on Solana
instead of Aave reserves on Base) and the cost model (Solana priority
fees in microlamports instead of Base L2 gas in gwei).

Logic:
  - For the chosen asset (USDC, USDT, or SOL), look up the current
    supply APY from market_data.apys (key format:
    `kamino.{asset_lower}.solana`).
  - Look up the reserve's TVL from market_data.pool_state under
    `solana:kamino:{asset_lower}`.
  - Compute the priority-fee cost in USD for one supply transaction
    using the Solana p50 fee from metadata; reject if that cost can
    not be amortized within `priority_fee_amortization_days` at the
    current APY.
  - Enter if APY > apy_threshold AND TVL >= min_liquidity_usd AND
    priority fee amortizes AND we are not already holding.
  - Exit if our open position's APY drops below apy_threshold.
  - Otherwise hold.

The function is pure: same inputs → same Decision. No I/O.
"""

from decimal import Decimal

from icarus.types import Decision, MarketSnapshot, PortfolioSnapshot

# Constants — Solana cost model. Not tunable per search; these are physics.
#
# A Kamino supply tx is roughly one compute-budget instruction (the
# priority fee) plus the base 5_000 lamport signature fee. Priority fee
# dominates by orders of magnitude under congestion, so we model only it.
# 1 SOL = 1e9 lamports = 1e15 microlamports.
_DEFAULT_SOL_PRICE_USD = Decimal("150")  # used if SOL/USD not in prices
_MICROLAMPORTS_PER_SOL = Decimal("1e15")
_DEFAULT_PRIORITY_FEE_MICROLAMPORTS = Decimal("50000")  # ~p50 when uncongested


def _priority_fee_cost_usd(
    priority_fee_microlamports: Decimal, sol_price_usd: Decimal
) -> Decimal:
    """Convert one supply tx's priority fee to USD.

    Kamino supply ix is ~1 signed transaction; priority fee = unit
    price times compute units used. We use the p50 priority fee as a
    direct USD estimate per tx (the compute-unit count is roughly
    fixed across reserves, so it folds into the p50 number the
    adapter publishes).
    """
    sol_cost = priority_fee_microlamports / _MICROLAMPORTS_PER_SOL
    return sol_cost * sol_price_usd


def evaluate(
    params: dict,
    market_data: MarketSnapshot,
    portfolio_state: PortfolioSnapshot,
) -> Decision:
    apy_threshold = Decimal(str(params.get("apy_threshold", "0.03")))
    min_liquidity_usd = Decimal(str(params.get("min_liquidity_usd", "1000000")))
    priority_fee_amortization_days = Decimal(
        str(params.get("priority_fee_amortization_days", "7"))
    )
    asset = str(params.get("asset_variant", "USDC"))

    # Resolve current APY for the chosen asset on Kamino.
    apy_key = f"kamino.{asset.lower()}.solana"
    apy = market_data.apys.get(apy_key, Decimal("0"))

    # Reserve TVL — required for eligibility.
    pool_key = f"solana:kamino:{asset.lower()}"
    pool = market_data.pool_state.get(pool_key)
    tvl = pool.tvl if pool is not None else Decimal("0")

    # Priority-fee amortization check. Stream A is extending the
    # MarketSnapshot metadata convention with a `priority_fees` map for
    # Solana; default to a sensible p50 if the adapter hasn't populated it.
    priority_fees_meta = market_data.metadata.get("priority_fees", {})
    priority_fee_microlamports = Decimal(
        str(
            priority_fees_meta.get(
                "solana_p50_microlamports", _DEFAULT_PRIORITY_FEE_MICROLAMPORTS
            )
        )
    )
    sol_price = market_data.prices.get("SOL", _DEFAULT_SOL_PRICE_USD)
    fee_cost = _priority_fee_cost_usd(priority_fee_microlamports, sol_price)
    # Yield per day on a $100 position at this APY:
    daily_yield_per_100 = Decimal("100") * apy / Decimal("365")
    days_to_amortize = (
        fee_cost / daily_yield_per_100 if daily_yield_per_100 > 0 else Decimal("1e9")
    )
    fee_ok = days_to_amortize <= priority_fee_amortization_days

    # Are we already in a LEND-KAMINO-001 position?
    in_position = any(
        p.template_id == "LEND-KAMINO-001"
        for p in portfolio_state.positions.values()
    )

    # --- Exit: open position but APY dropped below threshold ---
    if in_position and apy < apy_threshold:
        return Decision(
            action="exit",
            target_size=Decimal("0"),
            confidence=Decimal("0.9"),
            reasoning=f"apy {apy} below threshold {apy_threshold}; exit {asset}",
        )

    # --- Entry: not yet in, APY high enough, reserve liquid enough, fee ok ---
    if (
        not in_position
        and apy >= apy_threshold
        and tvl >= min_liquidity_usd
        and fee_ok
    ):
        # Size = full cash (allocator will cap to allocation_max from manifest).
        return Decision(
            action="enter",
            target_size=portfolio_state.cash_usd,
            confidence=Decimal("0.8"),
            reasoning=(
                f"apy {apy} >= threshold {apy_threshold}, "
                f"tvl {tvl} >= min {min_liquidity_usd}, "
                f"priority fee amortizes in {days_to_amortize:.1f} days"
            ),
        )

    # --- Hold: everything else ---
    if in_position:
        reason = f"apy {apy} >= threshold {apy_threshold}; hold {asset}"
    elif not fee_ok:
        reason = (
            f"priority fee amortization {days_to_amortize:.1f}d > "
            f"limit {priority_fee_amortization_days}d"
        )
    elif tvl < min_liquidity_usd:
        reason = f"tvl {tvl} < min {min_liquidity_usd}"
    else:
        reason = f"apy {apy} < threshold {apy_threshold}"

    return Decision(
        action="hold",
        target_size=Decimal("0"),
        confidence=Decimal("0.5"),
        reasoning=reason,
    )
