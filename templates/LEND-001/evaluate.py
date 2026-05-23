"""LEND-001 — Aave V3 stablecoin supply rotation on Base.

Hand-conversion of v4.2's aave_lending.py to the v2 Decision contract.

Logic:
  - For each whitelisted asset, look up the current supply APY from
    market_data.apys (key format: `aave_v3.{asset_lower}.base`).
  - Choose the best APY across the asset universe.
  - Compute estimated gas cost per rotation in USD (gas_gwei × 21_000 ×
    eth_price); reject if gas can not be recovered within
    gas_amortization_days at the current APY delta.
  - Enter if best APY > apy_threshold AND TVL >= min_liquidity_usd AND
    we are not already holding (cash > 0).
  - Exit if our open position's pool APY drops below apy_threshold.
  - Otherwise hold.

The function is pure: same inputs → same Decision. No I/O.
"""

from decimal import Decimal

from icarus.types import Decision, MarketSnapshot, PortfolioSnapshot

# Constants — gas cost model. Not tunable per search; these are physics.
_AAVE_SUPPLY_GAS_UNITS = Decimal("21000")
_ETH_PRICE_FALLBACK_USD = Decimal("3500")  # used if ETH/USD not in prices
_SECONDS_PER_DAY = Decimal("86400")
_NS_PER_GWEI = Decimal("1e9")


def _gas_cost_usd(gas_gwei: Decimal, eth_price: Decimal) -> Decimal:
    """Convert one supply tx's gas cost to USD using simple model."""
    eth_per_gwei = Decimal("1") / _NS_PER_GWEI
    return _AAVE_SUPPLY_GAS_UNITS * gas_gwei * eth_per_gwei * eth_price


def evaluate(
    params: dict,
    market_data: MarketSnapshot,
    portfolio_state: PortfolioSnapshot,
) -> Decision:
    apy_threshold = Decimal(str(params.get("apy_threshold", "0.05")))
    min_liquidity_usd = Decimal(str(params.get("min_liquidity_usd", "1000000")))
    gas_amortization_days = Decimal(str(params.get("gas_amortization_days", "14")))
    asset = str(params.get("asset_variant", "USDC"))

    # Resolve current best APY for the chosen asset.
    apy_key = f"aave_v3.{asset.lower()}.base"
    apy = market_data.apys.get(apy_key, Decimal("0"))

    # Pool TVL — required for eligibility.
    pool_key = f"base:aave_v3:{asset.lower()}"
    pool = market_data.pool_state.get(pool_key)
    tvl = pool.tvl if pool is not None else Decimal("0")

    # Gas amortization check: at apy delta, how many days to recover one supply tx?
    eth_price = market_data.prices.get("ETH", _ETH_PRICE_FALLBACK_USD)
    gas_cost = _gas_cost_usd(market_data.gas_gwei, eth_price)
    # Yield per day on a $100 position at this APY:
    daily_yield_per_100 = Decimal("100") * apy / Decimal("365")
    days_to_amortize = (
        gas_cost / daily_yield_per_100 if daily_yield_per_100 > 0 else Decimal("1e9")
    )
    gas_ok = days_to_amortize <= gas_amortization_days

    # Are we already in a LEND-001 position?
    in_position = any(
        p.template_id == "LEND-001" for p in portfolio_state.positions.values()
    )

    # --- Exit: open position but APY dropped below threshold ---
    if in_position and apy < apy_threshold:
        return Decision(
            action="exit",
            target_size=Decimal("0"),
            confidence=Decimal("0.9"),
            reasoning=f"apy {apy} below threshold {apy_threshold}; exit {asset}",
        )

    # --- Entry: not yet in, APY high enough, pool liquid enough, gas ok ---
    if not in_position and apy >= apy_threshold and tvl >= min_liquidity_usd and gas_ok:
        # Size = full cash (allocator will cap to allocation_max from manifest).
        return Decision(
            action="enter",
            target_size=portfolio_state.cash_usd,
            confidence=Decimal("0.8"),
            reasoning=(
                f"apy {apy} >= threshold {apy_threshold}, "
                f"tvl {tvl} >= min {min_liquidity_usd}, "
                f"gas amortizes in {days_to_amortize:.1f} days"
            ),
        )

    # --- Hold: everything else ---
    if in_position:
        reason = f"apy {apy} >= threshold {apy_threshold}; hold {asset}"
    elif not gas_ok:
        reason = f"gas amortization {days_to_amortize:.1f}d > limit {gas_amortization_days}d"
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
