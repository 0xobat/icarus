"""Smoke tests for LEND-KAMINO-001 evaluate().

Loaded by `TemplateRegistry._run_smoke()` via the synthetic module
name `icarus.templates.lend_kamino_001`. From W4 onwards (per blueprint),
a failure here blocks registration; in W1-W3 it logs a warning.

Solana fixtures are constructed inline — no shared fixture module needed.
Priority-fee data lives at
`market_data.metadata["priority_fees"]["solana_p50_microlamports"]` per
the v2 metadata-as-extension-hatch convention (Stream A is extending the
MarketSnapshot metadata schema with this key).
"""

import sys
from datetime import UTC, datetime
from decimal import Decimal

from icarus.types import MarketSnapshot, PoolState, PortfolioSnapshot, Position

# Resolve the synthetic module the registry created for our evaluate.py.
_mod = sys.modules["icarus.templates.lend_kamino_001"]


def _mk_market(
    apy: float,
    tvl: float = 5_000_000,
    priority_fee_microlamports: float = 50_000,
) -> MarketSnapshot:
    return MarketSnapshot(
        timestamp=datetime.now(UTC),
        chain="solana",
        prices={"USDC": Decimal("1.0"), "USDT": Decimal("1.0"), "SOL": Decimal("150")},
        apys={"kamino.usdc.solana": Decimal(str(apy))},
        pool_state={
            "solana:kamino:usdc": PoolState(
                pool_id="solana:kamino:usdc",
                tvl=Decimal(str(tvl)),
                depth=Decimal("100000"),
                fees_24h=Decimal("0"),
            )
        },
        gas_gwei=Decimal("0"),  # unused on Solana; kept for shape compat
        metadata={
            "priority_fees": {
                "solana_p50_microlamports": Decimal(str(priority_fee_microlamports)),
            },
        },
    )


def _mk_portfolio(cash: float = 1000.0, with_position: bool = False) -> PortfolioSnapshot:
    positions = {}
    if with_position:
        positions["c-kamino-existing"] = Position(
            candidate_id="c-kamino-existing",
            template_id="LEND-KAMINO-001",
            asset="USDC",
            size_usd=Decimal("500"),
            entry_price=Decimal("1.0"),
            entry_time=datetime.now(UTC),
        )
    return PortfolioSnapshot(
        nav_usd=Decimal(str(cash + 500 if with_position else cash)),
        positions=positions,
        cash_usd=Decimal(str(cash)),
        drawdown_from_peak=Decimal("0"),
        last_rebalance=datetime.now(UTC),
    )


def test_enter_when_apy_above_threshold_and_tvl_ok():
    params = {
        "apy_threshold": "0.04",
        "min_liquidity_usd": "1000000",
        "priority_fee_amortization_days": "7",
        "asset_variant": "USDC",
    }
    d = _mod.evaluate(params, _mk_market(apy=0.06, tvl=5_000_000), _mk_portfolio(cash=1000))
    assert d.action == "enter", f"expected enter, got {d.action}: {d.reasoning}"
    assert d.target_size == Decimal("1000")


def test_hold_when_apy_below_threshold_and_no_position():
    params = {"apy_threshold": "0.05", "asset_variant": "USDC"}
    d = _mod.evaluate(params, _mk_market(apy=0.03), _mk_portfolio(cash=1000))
    assert d.action == "hold", f"expected hold, got {d.action}"


def test_exit_when_apy_drops_below_threshold_with_open_position():
    params = {"apy_threshold": "0.05", "asset_variant": "USDC"}
    d = _mod.evaluate(params, _mk_market(apy=0.02), _mk_portfolio(with_position=True))
    assert d.action == "exit", f"expected exit, got {d.action}"


def test_hold_when_tvl_below_min_liquidity():
    params = {
        "apy_threshold": "0.04",
        "min_liquidity_usd": "10000000",
        "asset_variant": "USDC",
    }
    d = _mod.evaluate(params, _mk_market(apy=0.06, tvl=2_000_000), _mk_portfolio(cash=1000))
    assert d.action == "hold", f"expected hold (tvl-limited), got {d.action}"
    assert "tvl" in d.reasoning


def test_hold_when_priority_fee_amortization_exceeds_limit():
    # Pathologically high priority fee (5e13 microlamports = 0.05 SOL = $7.5
    # per tx at SOL=$150) vs a tiny APY → can't amortize in 1 day.
    # Daily yield on $100 at 1.2% APY = ~$0.0033, so amortization takes
    # ~2300 days. Well above the 1-day limit.
    params = {
        "apy_threshold": "0.011",
        "priority_fee_amortization_days": "1",
        "asset_variant": "USDC",
    }
    d = _mod.evaluate(
        params,
        _mk_market(apy=0.012, priority_fee_microlamports=5e13),
        _mk_portfolio(cash=1000),
    )
    assert d.action == "hold", f"expected hold (fee-limited), got {d.action}"
    assert "priority fee" in d.reasoning
