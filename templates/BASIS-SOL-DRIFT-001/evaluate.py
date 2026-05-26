"""BASIS-SOL-DRIFT-001 — Delta-neutral basis trade on Solana.

Long spot (SOL or ETH via Jupiter aggregator) + short Drift perpetual
on the same asset. The alpha is the funding rate the short leg receives
when perp funding is positive. Net delta is zero by construction;
PnL ≈ funding net of fees / priority fees.

Solana analog of BASIS-PERP-001. Key structural difference: Drift
funds HOURLY (24x/day), not every 8h (3x/day as on Synthetix Perps
Base). Per-period thresholds are roughly 1/8 of a comparable
8h-cadence venue for the same annualized hurdle.

Convention:
  - `target_size` is the **per-leg** USD notional. target_size=1000
    means $1000 long spot + $1000 short Drift perp.
  - The strategy operates on a single basis position at a time, for
    the chosen `asset_variant` (SOL or ETH).
  - Funding-rate data lives at
    `market_data.metadata["funding_rates"]["drift:sol-perp"]` (and
    `"drift:eth-perp"`). Decimal, hourly funding fraction; positive =
    longs pay shorts.
  - Streak data lives at
    `market_data.metadata["funding_streaks"]["drift:sol-perp"]` (int,
    consecutive hourly periods with funding ≥ funding_threshold).

The function is pure: same inputs → same Decision. No I/O.
"""

from decimal import Decimal

from icarus.types import Decision, MarketSnapshot, PortfolioSnapshot


def _venue_key(asset: str) -> str:
    """Drift market key for the chosen asset. SOL→sol-perp, ETH→eth-perp."""
    return f"drift:{asset.lower()}-perp"


def _read_funding(market_data: MarketSnapshot, venue_key: str) -> Decimal:
    """Pull the current hourly funding rate. Defaults to 0 (neutral)
    if the adapter hasn't populated it — that's safer than guessing."""
    funding_rates = market_data.metadata.get("funding_rates", {})
    return Decimal(str(funding_rates.get(venue_key, "0")))


def _read_streak(market_data: MarketSnapshot, venue_key: str) -> int:
    """Count of consecutive hourly periods with funding ≥ funding_threshold.
    Maintained by the data adapter (W3); zero if absent."""
    streaks = market_data.metadata.get("funding_streaks", {})
    return int(streaks.get(venue_key, 0))


def _holding_basis_position(portfolio_state: PortfolioSnapshot) -> bool:
    """Already in a BASIS-SOL-DRIFT-001 position? (any leg counts; the
    template is intended to hold both or neither — a single open leg
    is an error state for the executor, not for evaluate)."""
    return any(
        p.template_id == "BASIS-SOL-DRIFT-001"
        for p in portfolio_state.positions.values()
    )


def evaluate(
    params: dict,
    market_data: MarketSnapshot,
    portfolio_state: PortfolioSnapshot,
) -> Decision:
    funding_threshold = Decimal(str(params.get("funding_threshold", "0.00005")))
    exit_funding_threshold = Decimal(str(params.get("exit_funding_threshold", "0.000005")))
    min_funding_streak = int(params.get("min_funding_streak", "6"))
    position_size_pct = Decimal(str(params.get("position_size_pct", "0.5")))
    asset = str(params.get("asset_variant", "SOL"))

    venue_key = _venue_key(asset)
    funding = _read_funding(market_data, venue_key)
    streak = _read_streak(market_data, venue_key)
    in_position = _holding_basis_position(portfolio_state)

    # ── Entry condition ─────────────────────────────────────────────
    #
    # OPERATOR INPUT (optional refinement): the baseline below is the
    # textbook basis-trade entry — funding above threshold AND the
    # funding regime has been sustained for `min_funding_streak`
    # hourly periods. You can refine by adding e.g.:
    #
    #   - a price-volatility filter (skip entry during high-vol days
    #     because spot-perp basis can blow out on Drift when oracle
    #     and DEX prices diverge during volatile minutes)
    #   - an open-interest floor on the Drift market (skip when the
    #     book is thin — your short can move the funding rate against
    #     itself)
    #   - a max-position-staleness rule (re-enter on a fresh streak
    #     rather than holding through funding inversions)
    #
    # The default below is the no-refinement baseline. Edit this
    # block if you want a tighter entry policy.
    entry_qualified = funding >= funding_threshold and streak >= min_funding_streak

    # ── Exit condition ──────────────────────────────────────────────
    # Symmetric to entry: exit when funding drops below the (lower)
    # exit threshold. Strict `<` so we hold across one-tick noise at
    # the boundary.
    exit_qualified = funding < exit_funding_threshold

    # ── Decision tree ───────────────────────────────────────────────
    if in_position and exit_qualified:
        return Decision(
            action="exit",
            target_size=Decimal("0"),
            confidence=Decimal("0.9"),
            reasoning=(
                f"funding {funding} < exit_threshold {exit_funding_threshold} "
                f"on {venue_key}; unwind basis trade (close spot, close perp)"
            ),
        )

    if not in_position and entry_qualified:
        # Per-leg size = position_size_pct of available cash. The
        # executor doubles this for gross exposure (long spot + short
        # perp = 2x notional, 0 net).
        target = portfolio_state.cash_usd * position_size_pct
        return Decision(
            action="enter",
            target_size=target,
            confidence=Decimal("0.75"),
            reasoning=(
                f"funding {funding} >= threshold {funding_threshold} on "
                f"{venue_key} for {streak} hours (>= {min_funding_streak}); "
                f"enter basis: long ${target} spot {asset}, "
                f"short ${target} Drift {asset}-perp"
            ),
        )

    # Hold path — explain why so the audit log + webapp surface the
    # blocking condition.
    if in_position:
        reason = (
            f"funding {funding} >= exit_threshold {exit_funding_threshold} "
            f"on {venue_key}; hold open basis trade"
        )
    elif funding < funding_threshold:
        reason = f"funding {funding} < threshold {funding_threshold} on {venue_key}"
    elif streak < min_funding_streak:
        reason = (
            f"funding qualifies on {venue_key} but streak {streak} < "
            f"min {min_funding_streak}; wait for sustained funding regime"
        )
    else:
        reason = f"no qualifying entry or exit condition on {venue_key}"

    return Decision(
        action="hold",
        target_size=Decimal("0"),
        confidence=Decimal("0.5"),
        reasoning=reason,
    )
