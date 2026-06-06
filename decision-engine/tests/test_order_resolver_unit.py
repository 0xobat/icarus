"""Unit tests for the order resolver (managed-portfolio P1.1)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from decision_engine.order_resolver import (
    TokenInfo,
    lookup_token,
    resolve_burn_lp_params,
    resolve_mint_lp_params,
    resolve_supply_params,
    resolve_swap_params,
    resolve_withdraw_params,
    slippage_bounded_min_out,
    usd_to_smallest_unit,
)
from icarus.envelopes.orders import OrderParams


def test_lookup_usdc_base() -> None:
    info = lookup_token("base", "USDC")
    assert info == TokenInfo(
        address="0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913", decimals=6
    )


def test_lookup_weth_base() -> None:
    info = lookup_token("base", "WETH")
    assert info.address == "0x4200000000000000000000000000000000000006"
    assert info.decimals == 18


def test_lookup_unknown_symbol_raises() -> None:
    with pytest.raises(KeyError, match="UNKNOWN"):
        lookup_token("base", "UNKNOWN")


def test_lookup_unsupported_chain_raises() -> None:
    with pytest.raises(KeyError, match="solana"):
        lookup_token("solana", "USDC")


def test_usd_to_smallest_unit_weth() -> None:
    assert usd_to_smallest_unit(
        Decimal("6000"), Decimal("3000"), 18
    ) == Decimal("2000000000000000000")


def test_usd_to_smallest_unit_usdc() -> None:
    assert usd_to_smallest_unit(
        Decimal("6000"), Decimal("1"), 6
    ) == Decimal("6000000000")


def test_usd_to_smallest_unit_floors_fractional_base_units() -> None:
    result = usd_to_smallest_unit(Decimal("100"), Decimal("3000"), 18)
    assert result == Decimal("33333333333333333")
    assert result == result.to_integral_value()


def test_usd_to_smallest_unit_rejects_nonpositive_price() -> None:
    with pytest.raises(ValueError, match="price"):
        usd_to_smallest_unit(Decimal("100"), Decimal("0"), 18)


def test_usd_to_smallest_unit_rejects_negative_amount() -> None:
    with pytest.raises(ValueError, match="usd_amount"):
        usd_to_smallest_unit(Decimal("-100"), Decimal("3000"), 18)


_SAFE = "0x1111111111111111111111111111111111111111"


def test_resolve_swap_params_usdc_to_weth() -> None:
    params = resolve_swap_params(
        chain="base",
        token_in_symbol="USDC",
        token_out_symbol="WETH",
        usd_amount=Decimal("6000"),
        price_in_usd=Decimal("1"),
        price_out_usd=Decimal("3000"),
        recipient=_SAFE,
        slippage_bps=50,
        deadline_unix=1_900_000_000,
    )
    assert isinstance(params, OrderParams)
    assert params.token_in == "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
    assert params.token_out == "0x4200000000000000000000000000000000000006"
    assert params.amount == Decimal("6000000000")
    assert params.recipient == _SAFE
    assert params.extra["deadline"] == "1900000000"
    assert params.extra["stable"] == "false"
    assert params.extra["amount_out_min"] == "1990000000000000000"


def test_resolve_swap_params_amount_out_min_floors() -> None:
    params = resolve_swap_params(
        chain="base",
        token_in_symbol="WETH",
        token_out_symbol="USDC",
        usd_amount=Decimal("100"),
        price_in_usd=Decimal("3000"),
        price_out_usd=Decimal("1"),
        recipient=_SAFE,
        slippage_bps=100,
        deadline_unix=1_900_000_000,
    )
    assert params.extra["amount_out_min"] == "99000000"
    assert params.amount == Decimal("33333333333333333")


def test_resolve_swap_params_rejects_bad_slippage() -> None:
    with pytest.raises(ValueError, match="slippage_bps"):
        resolve_swap_params(
            chain="base",
            token_in_symbol="USDC",
            token_out_symbol="WETH",
            usd_amount=Decimal("6000"),
            price_in_usd=Decimal("1"),
            price_out_usd=Decimal("3000"),
            recipient=_SAFE,
            slippage_bps=1001,
            deadline_unix=1_900_000_000,
        )


from decision_engine.order_resolver import DEFAULT_CHAIN_ID, register_token  # noqa: E402

BASE_SEPOLIA = 84532


def test_default_chain_id_is_base_mainnet() -> None:
    assert DEFAULT_CHAIN_ID == 8453


def test_lookup_token_sepolia_usdc_differs_from_mainnet() -> None:
    mainnet = lookup_token("base", "USDC")  # default 8453
    sepolia = lookup_token("base", "USDC", chain_id=BASE_SEPOLIA)
    assert sepolia.address != mainnet.address
    assert sepolia.decimals == 6


def test_lookup_token_weth_same_on_both_networks() -> None:
    # WETH is the OP-stack predeploy — identical address on Base mainnet + Sepolia.
    assert (
        lookup_token("base", "WETH").address
        == lookup_token("base", "WETH", chain_id=BASE_SEPOLIA).address
        == "0x4200000000000000000000000000000000000006"
    )


def test_lookup_token_unknown_chain_id_raises() -> None:
    with pytest.raises(KeyError, match="999999"):
        lookup_token("base", "USDC", chain_id=999999)


def test_register_token_overrides_address() -> None:
    custom = "0xabc0000000000000000000000000000000000001"
    register_token(chain_id=BASE_SEPOLIA, symbol="USDC", address=custom, decimals=6)
    assert lookup_token("base", "USDC", chain_id=BASE_SEPOLIA).address == custom
    # restore so test order independence holds
    register_token(
        chain_id=BASE_SEPOLIA, symbol="USDC",
        address="0x036CbD53842c5426634e7929541eC2318f3dCF7e", decimals=6,
    )


def test_resolve_swap_params_threads_chain_id() -> None:
    params = resolve_swap_params(
        chain="base", token_in_symbol="USDC", token_out_symbol="WETH",
        usd_amount=Decimal("6000"), price_in_usd=Decimal("1"),
        price_out_usd=Decimal("3000"), recipient=_SAFE, slippage_bps=50,
        deadline_unix=1_900_000_000, chain_id=BASE_SEPOLIA,
    )
    # token_in is Sepolia USDC, not mainnet USDC.
    assert params.token_in == "0x036CbD53842c5426634e7929541eC2318f3dCF7e"


# ── P2.3: cbBTC registry + Aave supply/withdraw ──────────────────────────────


def test_lookup_cbbtc_base_mainnet() -> None:
    info = lookup_token("base", "cbBTC")
    assert info.address == "0xcbB7C0000aB88B473b1f5aFd9ef808440eed33Bf"
    assert info.decimals == 8


def test_lookup_wsteth_base_mainnet() -> None:
    info = lookup_token("base", "wstETH")
    assert info.address == "0xc1CBa3fCea344f92D9239c08C0568f6F2F0ee452"
    assert info.decimals == 18


def test_resolve_supply_params_usdc_aave() -> None:
    params = resolve_supply_params(
        chain="base", asset_symbol="USDC", usd_amount=Decimal("4000"),
        price_usd=Decimal("1"), recipient=_SAFE,
    )
    assert isinstance(params, OrderParams)
    assert params.token_in == "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
    assert params.amount == Decimal("4000000000")  # 4000e6
    assert params.recipient == _SAFE
    assert params.venue == "aave_v3"


def test_resolve_withdraw_params_cbbtc_aave() -> None:
    # $3000 of cbBTC at $60000 = 0.05 cbBTC → 0.05e8 = 5_000_000 (8 decimals).
    params = resolve_withdraw_params(
        chain="base", asset_symbol="cbBTC", usd_amount=Decimal("3000"),
        price_usd=Decimal("60000"), recipient=_SAFE,
    )
    assert params.token_in == "0xcbB7C0000aB88B473b1f5aFd9ef808440eed33Bf"
    assert params.amount == Decimal("5000000")
    assert params.venue == "aave_v3"
    assert params.recipient == _SAFE


def test_resolve_supply_params_custom_venue() -> None:
    params = resolve_supply_params(
        chain="base", asset_symbol="USDC", usd_amount=Decimal("1000"),
        price_usd=Decimal("1"), recipient=_SAFE, venue="moonwell",
    )
    assert params.venue == "moonwell"


# ── P3.3: LP overlay params + no-naked-minOut guard ──────────────────────────


def test_slippage_bounded_min_out_floors_with_slippage() -> None:
    # 1000 expected at 50 bps → 1000 * 0.995 = 995.
    assert slippage_bounded_min_out(Decimal("1000"), 50) == Decimal("995")


def test_slippage_bounded_min_out_rejects_zero_expected() -> None:
    # No valid LP action has a zero quote — guard against a naked minOut=0.
    with pytest.raises(ValueError, match="expected_out"):
        slippage_bounded_min_out(Decimal("0"), 50)


def test_resolve_mint_lp_params_usdc_weth() -> None:
    params = resolve_mint_lp_params(
        chain="base", token_a_symbol="USDC", token_b_symbol="WETH",
        usd_amount_a=Decimal("1000"), usd_amount_b=Decimal("1000"),
        price_a=Decimal("1"), price_b=Decimal("3000"),
        expected_lp_out=Decimal("1000000000000000000"),  # 1 LP token (18 dec)
        recipient=_SAFE, slippage_bps=50, pool_id="base:aerodrome:usdc-weth",
    )
    assert isinstance(params, OrderParams)
    assert params.token_in == "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"  # USDC
    assert params.token_out == "0x4200000000000000000000000000000000000006"  # WETH
    assert params.amount == Decimal("1000000000")  # 1000 USDC at 6 dec
    assert params.extra["amount_b"] == "333333333333333333"  # ~0.333 WETH at $3000
    # minOut floored with slippage, strictly positive (no naked zero).
    assert params.extra["amount_lp_min"] == "995000000000000000"
    assert params.pool_id == "base:aerodrome:usdc-weth"
    assert params.venue == "aerodrome"
    assert params.recipient == _SAFE


def test_resolve_burn_lp_params_min_amounts_out() -> None:
    params = resolve_burn_lp_params(
        chain="base", token_a_symbol="USDC", token_b_symbol="WETH",
        lp_amount=Decimal("1000000000000000000"),
        expected_a_out=Decimal("1000000000"),  # 1000 USDC
        expected_b_out=Decimal("333333333333333333"),
        recipient=_SAFE, slippage_bps=100, pool_id="base:aerodrome:usdc-weth",
    )
    assert params.amount == Decimal("1000000000000000000")  # LP burned
    assert params.extra["amount_a_min"] == "990000000"  # 1000e6 * 0.99
    assert params.extra["amount_b_min"] == "329999999999999999"  # floored
    assert params.pool_id == "base:aerodrome:usdc-weth"
    assert params.venue == "aerodrome"
