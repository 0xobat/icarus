"""Pricing & cost slice — interpret a MarketSnapshot for the resolver + cost gate.

Managed-portfolio P1.2. Pure functions, no I/O. The RpcAdapter already
sources real ETH/USD (Chainlink, Base) and gas into the MarketSnapshot; this
module pins stablecoins to $1, aliases WETH→ETH, and estimates swap cost in USD.

Scope (YAGNI): the symbols the P1 trivial target needs — USDC and WETH/ETH on
Base. SOL/wBTC/Solana pricing arrives with those assets in later phases.
"""

from __future__ import annotations

from decimal import Decimal

from icarus.types import MarketSnapshot

__all__ = ["DEFAULT_SWAP_GAS_UNITS", "estimate_swap_cost_usd", "price_usd"]

# Stablecoins pin to $1 *unless* the snapshot carries a live price for them
# (e.g. a Chainlink USDC/USD feed). The depeg breaker — not this pricing slice —
# decides whether an off-peg price should halt trading.
_STABLE_SYMBOLS = frozenset({"USDC", "USDT", "DAI"})

# Wrapped assets price off their underlying's snapshot key. cbBTC pegs ~1:1 to
# BTC (P2.3); wstETH is priced via its ETH exchange rate, not an alias (P2.4).
_PRICE_KEY_ALIASES = {"WETH": "ETH", "cbBTC": "BTC"}

# Liquid-staking tokens are NOT 1:1 with their underlying — they appreciate as
# rewards accrue. Price = exchange_rate (from the snapshot) * underlying USD
# price. The adapter sources the rate (a wstETH/ETH feed or the contract's
# stEthPerToken); pricing stays pure (P2.4).
_LST_RATE_KEY = {"wstETH": "wstETH/ETH"}
_LST_UNDERLYING = {"wstETH": "ETH"}


def price_usd(symbol: str, market: MarketSnapshot) -> Decimal:
    """USD price for an asset symbol from a market snapshot.

    Stablecoins pin to $1 unless the snapshot carries a live price for them
    (a configured peg feed), in which case that price wins; wrapped assets alias
    to their underlying's price key; everything else reads `market.prices`.
    Raises KeyError if unpriced.
    """
    if symbol in _STABLE_SYMBOLS:
        live = market.prices.get(symbol)
        return live if live is not None else Decimal("1")
    if symbol in _LST_RATE_KEY:
        rate_key = _LST_RATE_KEY[symbol]
        try:
            rate = market.prices[rate_key]
        except KeyError as exc:
            raise KeyError(
                f"no exchange rate for {symbol!r} (key {rate_key!r}) in snapshot"
            ) from exc
        return rate * price_usd(_LST_UNDERLYING[symbol], market)
    price_key = _PRICE_KEY_ALIASES.get(symbol, symbol)
    try:
        return market.prices[price_key]
    except KeyError as exc:
        raise KeyError(f"no USD price for {symbol!r} (key {price_key!r}) in snapshot") from exc


# A Base Aerodrome swap is ~150-250k gas; 200k is a safe central estimate for
# the cost gate. Tunable later from real receipts (ExecutionResult.gas_used_wei).
DEFAULT_SWAP_GAS_UNITS = 200_000


def estimate_swap_cost_usd(
    *,
    trade_usd: Decimal,
    slippage_bps: int,
    market: MarketSnapshot,
    eth_price_usd: Decimal,
    gas_units: int = DEFAULT_SWAP_GAS_UNITS,
) -> Decimal:
    """Estimate the USD cost of a swap = gas cost + slippage allowance.

    gas_usd  = gas_gwei * gas_units / 1e9 (→ ETH) * eth_price_usd
    slip_usd = trade_usd * slippage_bps / 10_000
    The cost gate compares the rebalance's corrective value against this.
    """
    gas_eth = market.gas_gwei * Decimal(gas_units) / Decimal(10**9)
    gas_usd = gas_eth * eth_price_usd
    slippage_usd = trade_usd * Decimal(slippage_bps) / Decimal(10_000)
    return gas_usd + slippage_usd
