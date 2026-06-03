"""Order resolver — turns a rebalance intent into executor-ready OrderParams.

Managed-portfolio P1.1. Pure functions, no I/O. The decision-engine cycle
calls this to build the `params` block of an ExecutionOrder; the ts-executor
protocol adapters consume the result verbatim (real addresses, smallest-unit
amounts, recipient, slippage-bounded amount_out_min).

Scope (YAGNI): only USDC + WETH on Base, and only the `swap` primitive — the
P1 trivial target ("hold USDC + ETH on Base, 60/40"). Later phases add SOL,
wBTC, Solana, lending, and LP.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal

from icarus.envelopes.orders import OrderParams
from icarus.types.market import Chain


@dataclass(frozen=True)
class TokenInfo:
    """A token's on-chain address and ERC-20 decimals."""

    address: str
    decimals: int


# Static token registry. Real Base mainnet addresses.
_TOKEN_REGISTRY: dict[Chain, dict[str, TokenInfo]] = {
    "base": {
        "USDC": TokenInfo("0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913", 6),
        "WETH": TokenInfo("0x4200000000000000000000000000000000000006", 18),
    },
}


def lookup_token(chain: Chain, symbol: str) -> TokenInfo:
    """Resolve (chain, symbol) → TokenInfo. Raises KeyError if unregistered."""
    try:
        chain_tokens = _TOKEN_REGISTRY[chain]
    except KeyError as exc:
        raise KeyError(f"no token registry for chain {chain!r}") from exc
    try:
        return chain_tokens[symbol]
    except KeyError as exc:
        raise KeyError(f"token {symbol!r} not registered on chain {chain!r}") from exc


def usd_to_smallest_unit(
    usd_amount: Decimal, price_usd: Decimal, decimals: int
) -> Decimal:
    """Convert a USD value into a token's smallest unit (wei/atomic).

    token_qty = usd_amount / price_usd; smallest_unit = token_qty * 10**decimals,
    floored to an integer (never emit a fractional base unit, which would be an
    invalid on-chain amount).
    """
    if price_usd <= 0:
        raise ValueError(f"price_usd must be positive, got {price_usd}")
    token_qty = usd_amount / price_usd
    smallest = token_qty * (Decimal(10) ** decimals)
    return smallest.quantize(Decimal(1), rounding=ROUND_DOWN)


def resolve_swap_params(
    *,
    chain: Chain,
    token_in_symbol: str,
    token_out_symbol: str,
    usd_amount: Decimal,
    price_in_usd: Decimal,
    price_out_usd: Decimal,
    recipient: str,
    slippage_bps: int,
    deadline_unix: int,
    stable: bool = False,
) -> OrderParams:
    """Build executor-ready OrderParams for an Aerodrome-style swap.

    Resolves token symbols to addresses, converts the USD notional to the
    token_in smallest unit, and computes a slippage-bounded `amount_out_min`
    in the token_out smallest unit. `recipient` is always set explicitly — the
    Aerodrome adapter falls back to `token_in` if recipient is absent, which
    would send funds to the token contract.
    """
    if not 0 <= slippage_bps <= 1000:
        raise ValueError(f"slippage_bps must be in [0, 1000], got {slippage_bps}")

    token_in = lookup_token(chain, token_in_symbol)
    token_out = lookup_token(chain, token_out_symbol)

    amount_in = usd_to_smallest_unit(usd_amount, price_in_usd, token_in.decimals)
    expected_out = usd_to_smallest_unit(usd_amount, price_out_usd, token_out.decimals)
    amount_out_min = (
        expected_out * Decimal(10_000 - slippage_bps) / Decimal(10_000)
    ).quantize(Decimal(1), rounding=ROUND_DOWN)

    return OrderParams(
        token_in=token_in.address,
        token_out=token_out.address,
        amount=amount_in,
        recipient=recipient,
        extra={
            "amount_out_min": str(amount_out_min),
            "deadline": str(deadline_unix),
            "stable": "true" if stable else "false",
        },
    )
