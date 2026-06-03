"""Unit tests for the order resolver (managed-portfolio P1.1)."""

from __future__ import annotations

import pytest

from decision_engine.order_resolver import TokenInfo, lookup_token


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


from decimal import Decimal

from decision_engine.order_resolver import usd_to_smallest_unit


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


from icarus.envelopes.orders import OrderParams

from decision_engine.order_resolver import resolve_swap_params

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
