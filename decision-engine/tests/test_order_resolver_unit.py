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
