"""BASIS-PERP-001 — ETH delta-neutral basis trade on Base.

Long ETH spot (Aerodrome) + short ETH perp (Synthetix Perps Base).
The alpha is the funding rate the short leg receives when perp funding
is positive. Net delta is zero by construction; PnL ≈ funding net of
fees/gas.

Convention:
  - `target_size` is the **per-leg** USD notional. target_size=1000
    means $1000 long spot + $1000 short perp.
  - The strategy operates on a single ETH basis position at a time.
  - Funding-rate data lives at
    `market_data.metadata["funding_rates"]["synthetix_perps_base:eth-usd"]`
    (Decimal, 8-hour funding fraction; positive = longs pay shorts).
  - Streak data lives at
    `market_data.metadata["funding_streaks"]["synthetix_perps_base:eth-usd"]`
    (int, consecutive 8h periods with funding ≥ funding_threshold).

The function is pure: same inputs → same Decision. No I/O.
"""

from decimal import Decimal

from icarus.types import Decision, MarketSnapshot, PortfolioSnapshot

_VENUE_KEY = "synthetix_perps_base:eth-usd"


def _read_funding(market_data: MarketSnapshot) -> Decimal:
    """Pull the current 8-hour funding rate. Defaults to 0 (neutral)
    if the adapter hasn't populated it — that's safer than guessing."""
    funding_rates = market_data.metadata.get("funding_rates", {})
    return Decimal(str(funding_rates.get(_VENUE_KEY, "0")))


def _read_streak(market_data: MarketSnapshot) -> int:
    """Count of consecutive 8h periods with funding ≥ funding_threshold.
    Maintained by the data adapter (W3); zero if absent."""
    streaks = market_data.metadata.get("funding_streaks", {})
    return int(streaks.get(_VENUE_KEY, 0))


def _holding_basis_position(portfolio_state: PortfolioSnapshot) -> bool:
    """Already in a BASIS-PERP-001 position? (any leg counts; the
    template is intended to hold both or neither — a single open leg
    is an error state for the executor, not for evaluate)."""
    return any(
        p.template_id == "BASIS-PERP-001"
        for p in portfolio_state.positions.values()
    )


def evaluate(
    params: dict,
    market_data: MarketSnapshot,
    portfolio_state: PortfolioSnapshot,
) -> Decision:
    funding_threshold = Decimal(str(params.get("funding_threshold", "0.0001")))
    exit_funding_threshold = Decimal(str(params.get("exit_funding_threshold", "0.00001")))
    min_funding_streak = int(params.get("min_funding_streak", "3"))
    position_size_pct = Decimal(str(params.get("position_size_pct", "0.5")))

    funding = _read_funding(market_data)
    streak = _read_streak(market_data)
    in_position = _holding_basis_position(portfolio_state)

    # ── Entry condition ─────────────────────────────────────────────
    #
    # OPERATOR INPUT (optional refinement): the baseline below is the
    # textbook basis-trade entry — funding above threshold AND the
    # funding regime has been sustained for `min_funding_streak`
    # periods. You can refine by adding e.g.:
    #
    #   - a price-volatility filter (skip entry during high-vol days
    #     because spot-perp basis can blow out, breaking the delta-
    #     neutrality assumption during the entry transaction)
    #   - a TVL floor on the perp venue (skip entry when Synthetix
    #     Perps Base TVL is low — your short can move the market)
    #   - a max-position-staleness rule (re-enter on a fresh
    #     streak rather than holding through funding inversions)
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
                f"funding {funding} < exit_threshold {exit_funding_threshold}; "
                f"unwind basis trade (close spot, close perp)"
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
                f"funding {funding} >= threshold {funding_threshold} for "
                f"{streak} periods (>= {min_funding_streak}); "
                f"enter basis trade: long ${target} spot ETH, "
                f"short ${target} perp ETH"
            ),
        )

    # Hold path — explain why so the audit log + webapp surface the
    # blocking condition.
    if in_position:
        reason = (
            f"funding {funding} >= exit_threshold {exit_funding_threshold}; "
            f"hold open basis trade"
        )
    elif funding < funding_threshold:
        reason = f"funding {funding} < threshold {funding_threshold}"
    elif streak < min_funding_streak:
        reason = (
            f"funding qualifies but streak {streak} < min {min_funding_streak}; "
            f"wait for sustained funding regime"
        )
    else:
        reason = "no qualifying entry or exit condition"

    return Decision(
        action="hold",
        target_size=Decimal("0"),
        confidence=Decimal("0.5"),
        reasoning=reason,
    )
