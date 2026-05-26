"""End-to-end registry tests against the reference templates.

Locks the DSL runtime against the templates that live in templates/.
Any change to icarus.dsl.* or icarus.types.* that breaks a reference
template fails CI here before it reaches the backtest engine.

The fixture loads with smoke_test_mode="blocking", so every template
in templates/ must pass its own smoke_test.py for the fixture itself
to construct. That makes structural failures explicit even without a
template-specific test below.
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


def test_default_smoke_test_mode_is_blocking():
    """W4 milestone: registry loader enforces smoke tests by default.

    Locks the default so a future refactor cannot silently drop production
    back to warn-only mode. Per blueprint W4: "Smoke test enforcement turned
    on in registry loader."
    """
    reg = TemplateRegistry(TEMPLATES_DIR)
    assert reg._smoke_test_mode == "blocking"


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


def test_lend_kamino_001_loads(registry):
    assert "LEND-KAMINO-001" in registry
    t = registry.by_id("LEND-KAMINO-001")
    assert t.manifest.chain == "solana"
    assert t.manifest.protocol == "kamino"
    assert t.manifest.allocation_max == Decimal("0.70")
    assert set(t.manifest.asset_universe) == {"USDC", "USDT", "SOL"}
    assert set(t.manifest.params.keys()) == {
        "apy_threshold",
        "min_liquidity_usd",
        "priority_fee_amortization_days",
        "asset_variant",
    }


def test_lend_kamino_001_enters_when_apy_high(registry):
    t = registry.by_id("LEND-KAMINO-001")
    market = MarketSnapshot(
        timestamp=datetime.now(UTC),
        chain="solana",
        prices={"USDC": Decimal("1.0"), "SOL": Decimal("150")},
        apys={"kamino.usdc.solana": Decimal("0.065")},
        pool_state={
            "solana:kamino:usdc": PoolState(
                pool_id="solana:kamino:usdc",
                tvl=Decimal("8000000"),
                depth=Decimal("200000"),
                fees_24h=Decimal("500"),
            )
        },
        gas_gwei=Decimal("0"),
        metadata={
            "priority_fees": {"solana_p50_microlamports": Decimal("50000")},
        },
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
            "priority_fee_amortization_days": "7",
            "asset_variant": "USDC",
        },
        market,
        portfolio,
    )
    assert decision.action == "enter"
    assert decision.target_size == Decimal("1000")


def test_basis_perp_001_loads(registry):
    assert "BASIS-PERP-001" in registry
    t = registry.by_id("BASIS-PERP-001")
    assert t.manifest.chain == "base"
    assert t.manifest.protocol == "synthetix_perps_base"
    assert t.manifest.allocation_max == Decimal("0.30")
    assert set(t.manifest.params.keys()) == {
        "funding_threshold",
        "exit_funding_threshold",
        "min_funding_streak",
        "position_size_pct",
    }


def test_basis_perp_001_enters_when_funding_qualifies(registry):
    t = registry.by_id("BASIS-PERP-001")
    market = MarketSnapshot(
        timestamp=datetime.now(UTC),
        chain="base",
        prices={"ETH": Decimal("3500"), "USDC": Decimal("1.0")},
        apys={},
        pool_state={},
        gas_gwei=Decimal("0.05"),
        metadata={
            "funding_rates": {"synthetix_perps_base:eth-usd": Decimal("0.0002")},
            "funding_streaks": {"synthetix_perps_base:eth-usd": 6},
        },
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
            "funding_threshold": "0.0001",
            "exit_funding_threshold": "0.00001",
            "min_funding_streak": "3",
            "position_size_pct": "0.5",
        },
        market,
        portfolio,
    )
    assert decision.action == "enter"
    # Per-leg sizing: 0.5 of $1000 cash = $500 per leg.
    assert decision.target_size == Decimal("500.0")


def test_basis_sol_drift_001_loads(registry):
    assert "BASIS-SOL-DRIFT-001" in registry
    t = registry.by_id("BASIS-SOL-DRIFT-001")
    assert t.manifest.chain == "solana"
    assert t.manifest.protocol == "drift"
    assert t.manifest.allocation_max == Decimal("0.30")
    assert set(t.manifest.asset_universe) == {"SOL", "ETH"}
    assert set(t.manifest.params.keys()) == {
        "funding_threshold",
        "exit_funding_threshold",
        "min_funding_streak",
        "position_size_pct",
        "asset_variant",
    }


def test_basis_sol_drift_001_enters_when_funding_qualifies(registry):
    t = registry.by_id("BASIS-SOL-DRIFT-001")
    market = MarketSnapshot(
        timestamp=datetime.now(UTC),
        chain="solana",
        prices={"SOL": Decimal("150"), "ETH": Decimal("3500"), "USDC": Decimal("1.0")},
        apys={},
        pool_state={},
        gas_gwei=Decimal("0"),
        metadata={
            # 0.0001/hr ≈ 88% annualized — well above 0.00005/hr threshold.
            "funding_rates": {"drift:sol-perp": Decimal("0.0001")},
            "funding_streaks": {"drift:sol-perp": 12},
        },
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
            "funding_threshold": "0.00005",
            "exit_funding_threshold": "0.000005",
            "min_funding_streak": "6",
            "position_size_pct": "0.5",
            "asset_variant": "SOL",
        },
        market,
        portfolio,
    )
    assert decision.action == "enter"
    # Per-leg sizing: 0.5 of $1000 cash = $500 per leg.
    assert decision.target_size == Decimal("500.0")


def test_lp_aero_001_loads(registry):
    assert "LP-AERO-001" in registry
    t = registry.by_id("LP-AERO-001")
    assert t.manifest.chain == "base"
    assert t.manifest.protocol == "aerodrome"
    assert t.manifest.allocation_max == Decimal("0.40")
    assert set(t.manifest.asset_universe) == {"USDC"}
    assert set(t.manifest.params.keys()) == {
        "fee_yield_threshold",
        "min_pool_tvl_usd",
        "gas_amortization_days",
    }


def test_lp_aero_001_enters_when_fee_yield_qualifies(registry):
    t = registry.by_id("LP-AERO-001")
    # 18% APR implied: fees_24h * 365 / tvl = 18% -> fees_24h = 0.18 * 8M / 365 ~ 3945.
    tvl = Decimal("8000000")
    fees_24h = Decimal("0.18") * tvl / Decimal("365")
    market = MarketSnapshot(
        timestamp=datetime.now(UTC),
        chain="base",
        prices={"USDC": Decimal("1.0"), "ETH": Decimal("3500")},
        apys={},
        pool_state={
            "base:aerodrome:usdc-usdbc": PoolState(
                pool_id="base:aerodrome:usdc-usdbc",
                tvl=tvl,
                depth=Decimal("250000"),
                fees_24h=fees_24h,
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
            "fee_yield_threshold": "0.10",
            "min_pool_tvl_usd": "2000000",
            "gas_amortization_days": "7",
        },
        market,
        portfolio,
    )
    assert decision.action == "enter"
    assert decision.target_size == Decimal("1000")
