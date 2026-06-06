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

__all__ = [
    "DEFAULT_CHAIN_ID",
    "DEFAULT_LENDING_VENUE",
    "DEFAULT_LP_VENUE",
    "TokenInfo",
    "lookup_token",
    "register_token",
    "resolve_burn_lp_params",
    "resolve_mint_lp_params",
    "resolve_supply_params",
    "resolve_swap_params",
    "resolve_withdraw_params",
    "slippage_bounded_min_out",
    "usd_to_smallest_unit",
]

DEFAULT_CHAIN_ID = 8453  # Base mainnet
DEFAULT_LENDING_VENUE = "aave_v3"  # the executor's adapter holds the Pool address
DEFAULT_LP_VENUE = "aerodrome"  # blue-chip DEX on Base for the LP overlay


@dataclass(frozen=True)
class TokenInfo:
    """A token's on-chain address and ERC-20 decimals."""

    address: str
    decimals: int


# Per-network token addresses, keyed by EVM chain_id. The logical `Chain`
# ("base") does not distinguish mainnet from testnet — chain_id does. WETH is
# the OP-stack predeploy (same address on every OP chain); only USDC differs.
# Operators can override any entry at boot via `register_token` (env-driven).
_TOKENS_BY_CHAIN_ID: dict[int, dict[str, TokenInfo]] = {
    8453: {  # Base mainnet
        "USDC": TokenInfo("0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913", 6),
        "WETH": TokenInfo("0x4200000000000000000000000000000000000006", 18),
        # cbBTC (Coinbase Wrapped BTC) — the prevalent BTC on Base; 8 decimals.
        "cbBTC": TokenInfo("0xcbB7C0000aB88B473b1f5aFd9ef808440eed33Bf", 8),
        # wstETH (Lido wrapped staked ETH) on Base — the ETH sleeve (P2.4).
        "wstETH": TokenInfo("0xc1CBa3fCea344f92D9239c08C0568f6F2F0ee452", 18),
    },
    84532: {  # Base Sepolia — VERIFY before live use; override via env if stale.
        "USDC": TokenInfo("0x036CbD53842c5426634e7929541eC2318f3dCF7e", 6),
        "WETH": TokenInfo("0x4200000000000000000000000000000000000006", 18),
    },
}


def register_token(*, chain_id: int, symbol: str, address: str, decimals: int) -> None:
    """Add or override a token entry at boot (e.g. from an env-supplied address).

    Lets an operator point a network's symbol at a specific contract without a
    code change — `__main__` calls this for any address overrides in env.
    """
    _TOKENS_BY_CHAIN_ID.setdefault(chain_id, {})[symbol] = TokenInfo(address, decimals)


def lookup_token(chain: Chain, symbol: str, *, chain_id: int = DEFAULT_CHAIN_ID) -> TokenInfo:
    """Resolve (chain, symbol) → TokenInfo for the given EVM network (chain_id).

    `chain` gates the logical chain (only "base" has a registry today); `chain_id`
    selects the network's address set. Raises KeyError if unregistered.
    """
    if chain != "base":
        raise KeyError(f"no token registry for chain {chain!r}")
    try:
        network = _TOKENS_BY_CHAIN_ID[chain_id]
    except KeyError as exc:
        raise KeyError(f"unsupported chain_id {chain_id} for chain {chain!r}") from exc
    try:
        return network[symbol]
    except KeyError as exc:
        raise KeyError(f"token {symbol!r} not registered on chain_id {chain_id}") from exc


def usd_to_smallest_unit(
    usd_amount: Decimal, price_usd: Decimal, decimals: int
) -> Decimal:
    """Convert a USD value into a token's smallest unit (wei/atomic).

    token_qty = usd_amount / price_usd; smallest_unit = token_qty * 10**decimals,
    floored to an integer (never emit a fractional base unit, which would be an
    invalid on-chain amount).
    """
    if usd_amount < 0:
        raise ValueError(f"usd_amount must be non-negative, got {usd_amount}")
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
    chain_id: int = DEFAULT_CHAIN_ID,
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

    token_in = lookup_token(chain, token_in_symbol, chain_id=chain_id)
    token_out = lookup_token(chain, token_out_symbol, chain_id=chain_id)

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


def _resolve_lending_params(
    *,
    chain: Chain,
    asset_symbol: str,
    usd_amount: Decimal,
    price_usd: Decimal,
    recipient: str,
    venue: str,
    chain_id: int,
) -> OrderParams:
    """Shared shape for Aave-style supply/withdraw — one asset, one amount.

    `token_in` is the underlying asset; `amount` its smallest unit; `venue` names
    the lending market (the executor's adapter holds the Pool address). The
    action ("supply"/"withdraw") is set by the caller on the ExecutionOrder.
    """
    info = lookup_token(chain, asset_symbol, chain_id=chain_id)
    amount = usd_to_smallest_unit(usd_amount, price_usd, info.decimals)
    return OrderParams(
        token_in=info.address,
        amount=amount,
        recipient=recipient,
        venue=venue,
    )


def resolve_supply_params(
    *,
    chain: Chain,
    asset_symbol: str,
    usd_amount: Decimal,
    price_usd: Decimal,
    recipient: str,
    venue: str = DEFAULT_LENDING_VENUE,
    chain_id: int = DEFAULT_CHAIN_ID,
) -> OrderParams:
    """Build executor-ready params for an Aave-style supply (lend an asset)."""
    return _resolve_lending_params(
        chain=chain, asset_symbol=asset_symbol, usd_amount=usd_amount,
        price_usd=price_usd, recipient=recipient, venue=venue, chain_id=chain_id,
    )


def resolve_withdraw_params(
    *,
    chain: Chain,
    asset_symbol: str,
    usd_amount: Decimal,
    price_usd: Decimal,
    recipient: str,
    venue: str = DEFAULT_LENDING_VENUE,
    chain_id: int = DEFAULT_CHAIN_ID,
) -> OrderParams:
    """Build executor-ready params for an Aave-style withdraw (redeem an asset).

    Withdraws an exact sized `usd_amount` of the underlying; full-balance exit
    (a max sentinel) is out of scope until P3 de-risk."""
    return _resolve_lending_params(
        chain=chain, asset_symbol=asset_symbol, usd_amount=usd_amount,
        price_usd=price_usd, recipient=recipient, venue=venue, chain_id=chain_id,
    )


def slippage_bounded_min_out(expected_out: Decimal, slippage_bps: int) -> Decimal:
    """Floor `expected_out * (1 - slippage)` to an integer smallest unit.

    Guards the design's "no naked minOut=0" rule: a non-positive expected output
    is never a valid trade/LP action, so it raises rather than emitting minOut=0
    (which would accept any fill, MEV-exposed).
    """
    if expected_out <= 0:
        raise ValueError(f"expected_out must be positive (no naked minOut=0), got {expected_out}")
    if not 0 <= slippage_bps <= 1000:
        raise ValueError(f"slippage_bps must be in [0, 1000], got {slippage_bps}")
    return (expected_out * Decimal(10_000 - slippage_bps) / Decimal(10_000)).quantize(
        Decimal(1), rounding=ROUND_DOWN
    )


def resolve_mint_lp_params(
    *,
    chain: Chain,
    token_a_symbol: str,
    token_b_symbol: str,
    usd_amount_a: Decimal,
    usd_amount_b: Decimal,
    price_a: Decimal,
    price_b: Decimal,
    recipient: str,
    slippage_bps: int,
    pool_id: str,
    venue: str = DEFAULT_LP_VENUE,
    chain_id: int = DEFAULT_CHAIN_ID,
) -> OrderParams:
    """Build executor-ready params for adding liquidity to a pair (LP overlay).

    `amount` is token_a's desired smallest unit (amountADesired); `extra`
    carries token_b's desired (`amount_b`) and the slippage-bounded MINIMUM token
    amounts (`amount_a_min`/`amount_b_min`) — the keys the Aerodrome executor
    reads. Aerodrome `addLiquidity` protects on the two token amounts (not LP
    shares), and both mins are strictly positive (no naked minOut=0)."""
    token_a = lookup_token(chain, token_a_symbol, chain_id=chain_id)
    token_b = lookup_token(chain, token_b_symbol, chain_id=chain_id)
    amount_a = usd_to_smallest_unit(usd_amount_a, price_a, token_a.decimals)
    amount_b = usd_to_smallest_unit(usd_amount_b, price_b, token_b.decimals)
    return OrderParams(
        token_in=token_a.address,
        token_out=token_b.address,
        amount=amount_a,
        recipient=recipient,
        pool_id=pool_id,
        venue=venue,
        extra={
            "amount_b": str(amount_b),
            "amount_a_min": str(slippage_bounded_min_out(amount_a, slippage_bps)),
            "amount_b_min": str(slippage_bounded_min_out(amount_b, slippage_bps)),
        },
    )


def resolve_burn_lp_params(
    *,
    chain: Chain,
    token_a_symbol: str,
    token_b_symbol: str,
    lp_amount: Decimal,
    expected_a_out: Decimal,
    expected_b_out: Decimal,
    recipient: str,
    slippage_bps: int,
    pool_id: str,
    venue: str = DEFAULT_LP_VENUE,
    chain_id: int = DEFAULT_CHAIN_ID,
) -> OrderParams:
    """Build executor-ready params for removing liquidity (burn LP shares).

    `amount` is the LP shares to burn; `extra` carries slippage-bounded minimum
    underlying amounts out (both strictly positive — no naked minOut=0)."""
    token_a = lookup_token(chain, token_a_symbol, chain_id=chain_id)
    token_b = lookup_token(chain, token_b_symbol, chain_id=chain_id)
    amount_a_min = slippage_bounded_min_out(expected_a_out, slippage_bps)
    amount_b_min = slippage_bounded_min_out(expected_b_out, slippage_bps)
    return OrderParams(
        token_in=token_a.address,
        token_out=token_b.address,
        amount=lp_amount,
        recipient=recipient,
        pool_id=pool_id,
        venue=venue,
        extra={"amount_a_min": str(amount_a_min), "amount_b_min": str(amount_b_min)},
    )
