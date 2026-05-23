"""End-to-end registry test against templates/LEND-001/.

Locks the DSL runtime against the first reference template. Any change
to icarus.dsl.* or icarus.types.* that breaks LEND-001 fails CI here
before it reaches the backtest engine.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from icarus.dsl import TemplateRegistry
from icarus.types import MarketSnapshot, PoolState, PortfolioSnapshot

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATES_DIR = REPO_ROOT / "templates"


@pytest.fixture(scope="module")
def registry():
    reg = TemplateRegistry(root=TEMPLATES_DIR, smoke_test_mode="blocking")
    result = reg.load()
    assert result.ok, f"templates/ failed to load: {[(f.stage, f.message) for f in result.failed]}"
    return reg


def test_lend_001_loads(registry):
    assert "LEND-001" in registry
    t = registry.by_id("LEND-001")
    assert t.manifest.chain == "base"
    assert t.manifest.protocol == "aave_v3"
    assert t.manifest.allocation_max == Decimal("0.70")
    assert set(t.manifest.params.keys()) == {
        "apy_threshold",
        "min_liquidity_usd",
        "gas_amortization_days",
        "asset_variant",
    }


def test_lend_001_enters_when_apy_high(registry):
    t = registry.by_id("LEND-001")
    market = MarketSnapshot(
        timestamp=datetime.now(UTC),
        chain="base",
        prices={"USDC": Decimal("1.0"), "ETH": Decimal("3500")},
        apys={"aave_v3.usdc.base": Decimal("0.065")},
        pool_state={
            "base:aave_v3:usdc": PoolState(
                pool_id="base:aave_v3:usdc",
                tvl=Decimal("8000000"),
                depth=Decimal("200000"),
                fees_24h=Decimal("500"),
            )
        },
        gas_gwei=Decimal("0.05"),
        metadata={},
    )
    portfolio = PortfolioSnapshot(
        nav_usd=Decimal("1000"),
        positions={},
        cash_usd=Decimal("1000"),
        drawdown_from_peak=Decimal("0"),
        last_rebalance=datetime.now(UTC),
    )
    decision = t.evaluate(
        {
            "apy_threshold": "0.04",
            "min_liquidity_usd": "1000000",
            "gas_amortization_days": "14",
            "asset_variant": "USDC",
        },
        market,
        portfolio,
    )
    assert decision.action == "enter"
    assert decision.target_size == Decimal("1000")
