"""Tests for exposure limit enforcement — RISK-008."""

from __future__ import annotations

import os
from decimal import Decimal
from typing import Any
from unittest.mock import patch

from risk.exposure_limits import (
    ExposureLimiter,
    ExposureLimitsConfig,
    load_config,
)


def _limiter(
    capital: float = 10000.0,
    positions: dict[str, dict[str, Any]] | None = None,
    config: ExposureLimitsConfig | None = None,
) -> ExposureLimiter:
    return ExposureLimiter(
        total_capital=capital,
        positions=positions or {},
        config=config,
    )


def _order(value: float, protocol: str = "aave", asset: str = "ETH") -> dict[str, Any]:
    return {"value_usd": value, "protocol": protocol, "asset": asset}


# ---------------------------------------------------------------------------
# Protocol exposure (max 40%)
# ---------------------------------------------------------------------------

class TestProtocolExposure:

    def test_within_limit_allowed(self) -> None:
        lim = _limiter(10000)
        result = lim.check_order(_order(3000, protocol="aave"))
        assert result.allowed is True

    def test_at_limit_allowed(self) -> None:
        lim = _limiter(10000)
        result = lim.check_order(_order(4000, protocol="aave"))
        assert result.allowed is True

    def test_exceeds_limit_rejected(self) -> None:
        lim = _limiter(10000)
        result = lim.check_order(_order(4500, protocol="aave"))
        assert result.allowed is False
        assert "protocol" in result.reason
        assert result.limit_type == "protocol"

    def test_existing_positions_count(self) -> None:
        """Existing protocol positions count toward the limit."""
        positions = {
            "pos1": {"value_usd": 3000, "protocol": "aave", "asset": "ETH"},
        }
        lim = _limiter(10000, positions)
        # 3000 existing + 1500 new = 4500 = 45% > 40%
        result = lim.check_order(_order(1500, protocol="aave"))
        assert result.allowed is False

    def test_different_protocols_independent(self) -> None:
        positions = {
            "pos1": {"value_usd": 3000, "protocol": "aave", "asset": "ETH"},
        }
        lim = _limiter(10000, positions)
        result = lim.check_order(_order(3000, protocol="uniswap"))
        assert result.allowed is True


# ---------------------------------------------------------------------------
# Asset exposure (max 60%, stablecoins exempt)
# ---------------------------------------------------------------------------

class TestAssetExposure:

    def test_within_limit_allowed(self) -> None:
        """ETH across two protocols to stay under 40% protocol limit each."""
        positions = {
            "pos1": {"value_usd": 2000, "protocol": "aave", "asset": "ETH"},
        }
        lim = _limiter(10000, positions)
        result = lim.check_order(_order(2000, protocol="uniswap", asset="ETH"))
        assert result.allowed is True

    def test_at_limit_allowed(self) -> None:
        """ETH at exactly 60% across two protocols."""
        positions = {
            "pos1": {"value_usd": 3000, "protocol": "aave", "asset": "ETH"},
        }
        lim = _limiter(10000, positions)
        result = lim.check_order(_order(3000, protocol="uniswap", asset="ETH"))
        assert result.allowed is True

    def test_exceeds_limit_rejected(self) -> None:
        """ETH above 60% across two protocols should be rejected by asset limit."""
        positions = {
            "pos1": {"value_usd": 4000, "protocol": "aave", "asset": "ETH"},
        }
        lim = _limiter(10000, positions)
        result = lim.check_order(_order(2500, protocol="uniswap", asset="ETH"))
        assert result.allowed is False
        assert "asset" in result.reason
        assert result.limit_type == "asset"

    def test_existing_positions_count(self) -> None:
        positions = {
            "pos1": {"value_usd": 3000, "protocol": "aave", "asset": "ETH"},
            "pos2": {"value_usd": 2000, "protocol": "compound", "asset": "ETH"},
        }
        lim = _limiter(10000, positions)
        # 3000 + 2000 + 1500 = 6500 ETH = 65% > 60%
        result = lim.check_order(_order(1500, protocol="uniswap", asset="ETH"))
        assert result.allowed is False

    def test_stablecoins_exempt(self) -> None:
        """Stablecoin positions should not be limited by asset exposure."""
        # Use high protocol limit to isolate asset check
        config = ExposureLimitsConfig(max_protocol_pct=Decimal("0.90"))
        lim = _limiter(10000, config=config)
        result = lim.check_order(_order(7000, asset="USDC"))
        assert result.allowed is True

    def test_stablecoin_case_insensitive(self) -> None:
        config = ExposureLimitsConfig(max_protocol_pct=Decimal("0.90"))
        lim = _limiter(10000, config=config)
        result = lim.check_order(_order(7000, asset="usdc"))
        assert result.allowed is True

    def test_multiple_stablecoins(self) -> None:
        config = ExposureLimitsConfig(max_protocol_pct=Decimal("0.90"))
        for stable in ["USDC", "USDT", "DAI", "FRAX"]:
            lim = _limiter(10000, config=config)
            result = lim.check_order(_order(7000, asset=stable))
            assert result.allowed is True, f"{stable} should be exempt"


# ---------------------------------------------------------------------------
# Stablecoin reserve (min 15%)
# ---------------------------------------------------------------------------

class TestStablecoinReserve:

    def test_reserve_maintained(self) -> None:
        """Should allow orders that keep 15%+ in stables/liquid."""
        # Use relaxed protocol/asset limits to isolate stablecoin reserve check
        config = ExposureLimitsConfig(
            max_protocol_pct=Decimal("0.90"),
            max_asset_pct=Decimal("0.90"),
        )
        lim = _limiter(10000, config=config)
        # Deploy 8500 = 85%, leaving 15% undeployed
        result = lim.check_order(_order(8500, asset="ETH"))
        assert result.allowed is True

    def test_reserve_breached_rejected(self) -> None:
        config = ExposureLimitsConfig(
            max_protocol_pct=Decimal("0.90"),
            max_asset_pct=Decimal("0.90"),
        )
        lim = _limiter(10000, config=config)
        # Deploy 8600 = 86%, leaving only 14% < 15%
        result = lim.check_order(_order(8600, asset="ETH"))
        assert result.allowed is False
        assert "stablecoin reserve" in result.reason
        assert result.limit_type == "stablecoin_reserve"

    def test_stablecoin_position_counts_as_reserve(self) -> None:
        """USDC positions count toward the reserve."""
        config = ExposureLimitsConfig(
            max_protocol_pct=Decimal("0.90"),
            max_asset_pct=Decimal("0.90"),
        )
        positions = {
            "stable": {"value_usd": 2000, "protocol": "aave", "asset": "USDC"},
        }
        lim = _limiter(10000, positions, config=config)
        # 2000 USDC deployed + (10000 - 2000 - 7000) = 1000 undeployed = 3000 reserve = 30%
        result = lim.check_order(_order(7000, protocol="uniswap", asset="ETH"))
        assert result.allowed is True

    def test_existing_deployment_reduces_room(self) -> None:
        positions = {
            "pos1": {"value_usd": 3500, "protocol": "aave", "asset": "ETH"},
            "pos2": {"value_usd": 3500, "protocol": "compound", "asset": "WBTC"},
        }
        lim = _limiter(10000, positions)
        # 7000 deployed, 3000 undeployed. Adding 2000 more: 1000 left = 10% < 15%
        result = lim.check_order(_order(2000, protocol="uniswap", asset="LINK"))
        assert result.allowed is False


# ---------------------------------------------------------------------------
# Zero capital
# ---------------------------------------------------------------------------

class TestZeroCapital:

    def test_zero_capital_rejects_all(self) -> None:
        lim = _limiter(0)
        result = lim.check_order(_order(100))
        assert result.allowed is False
        assert "zero" in result.reason


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

class TestConfiguration:

    def test_default_limits(self) -> None:
        config = ExposureLimitsConfig()
        assert config.max_protocol_pct == Decimal("0.40")
        assert config.max_asset_pct == Decimal("0.60")
        assert config.min_stablecoin_pct == Decimal("0.15")

    def test_custom_limits(self) -> None:
        config = ExposureLimitsConfig(
            max_protocol_pct=Decimal("0.50"),
            max_asset_pct=Decimal("0.70"),
            min_stablecoin_pct=Decimal("0.10"),
        )
        lim = _limiter(10000, config=config)
        # 50% protocol limit → 5000 allowed
        result = lim.check_order(_order(5000, protocol="aave"))
        assert result.allowed is True

    def test_update_config_changes_limits(self) -> None:
        lim = _limiter(10000)
        # Initially 40% protocol limit
        result = lim.check_order(_order(4500, protocol="aave"))
        assert result.allowed is False
        # Raise to 50%
        lim.update_config(max_protocol_pct=Decimal("0.50"))
        result = lim.check_order(_order(4500, protocol="aave"))
        assert result.allowed is True

    def test_update_config_logs_audit_trail(self) -> None:
        lim = _limiter(10000)
        # Should not raise; logging happens internally
        lim.update_config(max_protocol_pct=Decimal("0.50"))
        assert lim.config.max_protocol_pct == Decimal("0.50")


# ---------------------------------------------------------------------------
# Exposure summary (queryable)
# ---------------------------------------------------------------------------

class TestExposureSummary:

    def test_empty_portfolio(self) -> None:
        lim = _limiter(10000)
        summary = lim.get_exposure()
        assert summary.total_capital == "10000"
        assert summary.total_deployed == "0"
        assert summary.by_protocol == {}

    def test_with_positions(self) -> None:
        positions = {
            "pos1": {"value_usd": 3000, "protocol": "aave", "asset": "ETH"},
            "pos2": {"value_usd": 2000, "protocol": "uniswap", "asset": "WBTC"},
        }
        lim = _limiter(10000, positions)
        summary = lim.get_exposure()
        assert summary.total_deployed == "5000"
        assert "aave" in summary.by_protocol
        assert "uniswap" in summary.by_protocol
        assert "ETH" in summary.by_asset
        assert "WBTC" in summary.by_asset

    def test_protocol_pcts_calculated(self) -> None:
        positions = {
            "pos1": {"value_usd": 4000, "protocol": "aave", "asset": "ETH"},
        }
        lim = _limiter(10000, positions)
        summary = lim.get_exposure()
        assert summary.protocol_pcts["aave"] == "0.4"

    def test_stablecoin_reserve_pct(self) -> None:
        positions = {
            "pos1": {"value_usd": 5000, "protocol": "aave", "asset": "ETH"},
        }
        lim = _limiter(10000, positions)
        summary = lim.get_exposure()
        # 5000 undeployed + 0 stable deployed = 5000 / 10000 = 0.5
        assert summary.stablecoin_reserve_pct == "0.5"

    def test_zero_capital_summary(self) -> None:
        lim = _limiter(0)
        summary = lim.get_exposure()
        assert summary.total_capital == "0"


# ---------------------------------------------------------------------------
# Position and capital updates
# ---------------------------------------------------------------------------

class TestDynamicUpdates:

    def test_update_positions(self) -> None:
        lim = _limiter(10000)
        lim.update_positions({
            "pos1": {"value_usd": 3000, "protocol": "aave", "asset": "ETH"},
        })
        result = lim.check_order(_order(1500, protocol="aave"))
        assert result.allowed is False  # 3000 + 1500 = 4500 > 4000

    def test_update_capital(self) -> None:
        lim = _limiter(10000)
        lim.update_capital(20000)
        assert lim.total_capital == Decimal("20000")
        result = lim.check_order(_order(8000, protocol="aave"))
        assert result.allowed is True  # 8000/20000 = 40%


# ---------------------------------------------------------------------------
# Combined limit checks
# ---------------------------------------------------------------------------

class TestCombinedLimits:

    def test_first_failing_limit_wins(self) -> None:
        """Protocol limit is checked before asset limit."""
        positions = {
            "pos1": {"value_usd": 3500, "protocol": "aave", "asset": "ETH"},
        }
        lim = _limiter(10000, positions)
        # Both protocol (aave: 3500+1000=45%) and asset (ETH: 3500+1000=45%) could fail
        # but protocol is checked first
        result = lim.check_order(_order(1000, protocol="aave", asset="ETH"))
        assert result.allowed is False
        assert result.limit_type == "protocol"

    def test_passes_all_checks(self) -> None:
        lim = _limiter(10000)
        result = lim.check_order(_order(2000, protocol="aave", asset="ETH"))
        assert result.allowed is True
        assert result.reason == "ok"


# ---------------------------------------------------------------------------
# Environment variable loading (RISK-008 step 4)
# ---------------------------------------------------------------------------

class TestEnvVarLoading:

    def test_load_config_defaults(self) -> None:
        """Without env vars, defaults are used."""
        with patch.dict(os.environ, {}, clear=True):
            config = load_config()
        assert config.max_protocol_pct == Decimal("0.40")
        assert config.max_asset_pct == Decimal("0.60")
        assert config.min_stablecoin_pct == Decimal("0.15")

    def test_load_config_from_env(self) -> None:
        """Env vars are percentages (40 = 40%), converted to decimals."""
        env = {
            "MAX_SINGLE_PROTOCOL_PERCENT": "50",
            "MAX_SINGLE_ASSET_PERCENT": "70",
            "MIN_STABLECOIN_RESERVE_PERCENT": "10",
        }
        with patch.dict(os.environ, env, clear=True):
            config = load_config()
        assert config.max_protocol_pct == Decimal("0.50")
        assert config.max_asset_pct == Decimal("0.70")
        assert config.min_stablecoin_pct == Decimal("0.10")

    def test_load_config_partial_env(self) -> None:
        """Only set env vars override, others keep defaults."""
        env = {"MAX_SINGLE_PROTOCOL_PERCENT": "30"}
        with patch.dict(os.environ, env, clear=True):
            config = load_config()
        assert config.max_protocol_pct == Decimal("0.30")
        assert config.max_asset_pct == Decimal("0.60")  # default
        assert config.min_stablecoin_pct == Decimal("0.15")  # default

    def test_load_config_env_applied_to_limiter(self) -> None:
        """ExposureLimiter uses env-loaded config by default."""
        env = {"MAX_SINGLE_PROTOCOL_PERCENT": "25"}
        with patch.dict(os.environ, env, clear=True):
            lim = ExposureLimiter(total_capital=10000)
        # 25% of 10000 = 2500
        result = lim.check_order(_order(2600, protocol="aave"))
        assert result.allowed is False
        assert result.limit_type == "protocol"
