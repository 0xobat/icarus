"""Tests for portfolio allocator — PORT-001."""

from __future__ import annotations

from decimal import Decimal

from portfolio.allocator import (
    AllocatorConfig,
    PortfolioAllocator,
    _is_stablecoin,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_position(
    value_usd: float,
    protocol: str = "aave",
    asset: str = "ETH",
    tier: int = 1,
) -> dict:
    return {
        "value_usd": value_usd,
        "protocol": protocol,
        "asset": asset,
        "tier": tier,
    }


def _default_config() -> AllocatorConfig:
    return AllocatorConfig()


# ---------------------------------------------------------------------------
# Tier allocation bounds
# ---------------------------------------------------------------------------

class TestTierAllocation:
    """Capital must be allocated within tier percentage bounds."""

    def test_tier1_within_bounds_allowed(self) -> None:
        alloc = PortfolioAllocator(Decimal("10000"), config=_default_config())
        # 3900 = 39% → within tier max 60% AND protocol max 40%
        result = alloc.check_allocation(_make_position(3900, tier=1))
        assert result.allowed

    def test_tier1_exceeds_max_rejected(self) -> None:
        alloc = PortfolioAllocator(Decimal("10000"), config=_default_config())
        result = alloc.check_allocation(_make_position(6100, tier=1))
        assert not result.allowed
        assert "tier 1" in result.reason

    def test_tier2_within_bounds_allowed(self) -> None:
        alloc = PortfolioAllocator(Decimal("10000"), config=_default_config())
        result = alloc.check_allocation(_make_position(3000, tier=2))
        assert result.allowed

    def test_tier2_exceeds_max_rejected(self) -> None:
        alloc = PortfolioAllocator(Decimal("10000"), config=_default_config())
        result = alloc.check_allocation(_make_position(3600, tier=2))
        assert not result.allowed
        assert "tier 2" in result.reason

    def test_tier3_within_bounds_allowed(self) -> None:
        alloc = PortfolioAllocator(Decimal("10000"), config=_default_config())
        result = alloc.check_allocation(_make_position(1500, tier=3))
        assert result.allowed

    def test_tier3_exceeds_max_rejected(self) -> None:
        alloc = PortfolioAllocator(Decimal("10000"), config=_default_config())
        result = alloc.check_allocation(_make_position(2100, tier=3))
        assert not result.allowed
        assert "tier 3" in result.reason

    def test_unknown_tier_rejected(self) -> None:
        alloc = PortfolioAllocator(Decimal("10000"), config=_default_config())
        result = alloc.check_allocation(_make_position(1000, tier=99))
        assert not result.allowed
        assert "unknown tier" in result.reason

    def test_cumulative_tier_enforcement(self) -> None:
        """Adding to existing tier positions must count cumulatively."""
        positions = {
            "pos1": _make_position(5000, tier=1),
        }
        alloc = PortfolioAllocator(Decimal("10000"), positions, _default_config())
        # 5000 existing + 1100 new = 6100 = 61% > 60% max
        result = alloc.check_allocation(_make_position(1100, tier=1))
        assert not result.allowed


# ---------------------------------------------------------------------------
# Protocol exposure limit (max 40%)
# ---------------------------------------------------------------------------

class TestProtocolExposure:
    """No single protocol may exceed 40% of total capital."""

    def test_under_limit_allowed(self) -> None:
        alloc = PortfolioAllocator(Decimal("10000"), config=_default_config())
        result = alloc.check_allocation(_make_position(3900, protocol="aave", tier=1))
        assert result.allowed

    def test_over_limit_rejected(self) -> None:
        alloc = PortfolioAllocator(Decimal("10000"), config=_default_config())
        result = alloc.check_allocation(_make_position(4100, protocol="aave", tier=1))
        assert not result.allowed
        assert "protocol" in result.reason

    def test_cumulative_protocol_enforcement(self) -> None:
        positions = {
            "p1": _make_position(3000, protocol="aave", tier=1),
        }
        alloc = PortfolioAllocator(Decimal("10000"), positions, _default_config())
        # 3000 + 1100 = 4100 = 41% > 40%
        result = alloc.check_allocation(_make_position(1100, protocol="aave", tier=1))
        assert not result.allowed

    def test_different_protocols_independent(self) -> None:
        positions = {
            "p1": _make_position(3500, protocol="aave", asset="ETH", tier=1),
        }
        alloc = PortfolioAllocator(Decimal("10000"), positions, _default_config())
        # Different protocol, different asset → no concentration issue
        result = alloc.check_allocation(
            _make_position(3000, protocol="uniswap", asset="WBTC", tier=2),
        )
        assert result.allowed


# ---------------------------------------------------------------------------
# Asset exposure limit (max 60%, stablecoins exempt)
# ---------------------------------------------------------------------------

class TestAssetExposure:
    """No single non-stablecoin asset may exceed 60% of capital."""

    def test_under_limit_allowed(self) -> None:
        # Spread across protocols to stay under 40% protocol limit
        positions = {
            "p1": _make_position(3000, asset="ETH", protocol="aave", tier=1),
        }
        alloc = PortfolioAllocator(Decimal("10000"), positions, _default_config())
        # +2500 ETH via lido → total ETH=5500=55% < 60%, lido=25% < 40%
        result = alloc.check_allocation(
            _make_position(2500, asset="ETH", protocol="lido", tier=1),
        )
        assert result.allowed

    def test_over_limit_rejected(self) -> None:
        positions = {
            "p1": _make_position(3000, asset="ETH", protocol="aave", tier=1),
            "p2": _make_position(3000, asset="ETH", protocol="lido", tier=1),
        }
        alloc = PortfolioAllocator(Decimal("10000"), positions, _default_config())
        # total ETH = 3000+3000+200 = 6200 = 62% > 60%
        result = alloc.check_allocation(
            _make_position(200, asset="ETH", protocol="compound", tier=2),
        )
        assert not result.allowed
        assert "asset" in result.reason

    def test_stablecoins_exempt(self) -> None:
        """Stablecoins should not be subject to asset exposure limit."""
        alloc = PortfolioAllocator(Decimal("10000"), config=_default_config())
        # 3900 USDC via aave = 39% → under protocol limit, stablecoin exempt from asset limit
        result = alloc.check_allocation(
            _make_position(3900, asset="USDC", protocol="aave", tier=1),
        )
        assert result.allowed

    def test_stablecoin_detection_case_insensitive(self) -> None:
        assert _is_stablecoin("USDC")
        assert _is_stablecoin("usdc")
        assert _is_stablecoin("DAI")
        assert not _is_stablecoin("ETH")
        assert not _is_stablecoin("WBTC")


# ---------------------------------------------------------------------------
# Stablecoin reserve (min 15%)
# ---------------------------------------------------------------------------

class TestStablecoinReserve:
    """At least 15% must remain in stablecoins/liquid reserves."""

    def test_sufficient_reserve_allowed(self) -> None:
        # Deploy positions that stay under all limits, totaling 85%
        positions = {
            "p1": _make_position(4000, asset="ETH", protocol="aave", tier=1),
            "p2": _make_position(2000, asset="WBTC", protocol="lido", tier=1),
        }
        alloc = PortfolioAllocator(Decimal("10000"), positions, _default_config())
        # +2500 via uniswap → total deployed=8500 → reserve=1500=15%
        result = alloc.check_allocation(
            _make_position(2500, asset="LINK", protocol="uniswap", tier=2),
        )
        assert result.allowed

    def test_insufficient_reserve_rejected(self) -> None:
        positions = {
            "p1": _make_position(4000, asset="ETH", protocol="aave", tier=1),
            "p2": _make_position(2000, asset="WBTC", protocol="lido", tier=1),
            "p3": _make_position(2000, asset="LINK", protocol="uniswap", tier=2),
        }
        alloc = PortfolioAllocator(Decimal("10000"), positions, _default_config())
        # deployed=8000 + 600 = 8600 → reserve=1400=14% < 15%
        result = alloc.check_allocation(
            _make_position(600, asset="SOL", protocol="compound", tier=3),
        )
        assert not result.allowed
        assert "stablecoin reserve" in result.reason

    def test_stablecoin_positions_count_as_reserve(self) -> None:
        """Deployed stablecoins still count toward the reserve."""
        positions = {
            "p1": _make_position(5000, asset="ETH", tier=1),
            "p2": _make_position(2000, asset="USDC", protocol="aave", tier=1),
        }
        alloc = PortfolioAllocator(Decimal("10000"), positions, _default_config())
        # deployed=7000+1500=8500, undeployed=1500
        # stablecoin value = 2000 (USDC) + 1500 (undeployed) = 3500 = 35% > 15%
        result = alloc.check_allocation(
            _make_position(1500, asset="WBTC", protocol="uniswap", tier=2),
        )
        assert result.allowed


# ---------------------------------------------------------------------------
# Available capital
# ---------------------------------------------------------------------------

class TestAvailableCapital:
    """get_available_capital returns deployable amount per tier."""

    def test_empty_portfolio(self) -> None:
        alloc = PortfolioAllocator(Decimal("10000"), config=_default_config())
        avail = alloc.get_available_capital(1)
        # Tier 1 max = 60% of 10000 = 6000
        assert avail == Decimal("6000")

    def test_partially_deployed(self) -> None:
        positions = {
            "p1": _make_position(3000, tier=1),
        }
        alloc = PortfolioAllocator(Decimal("10000"), positions, _default_config())
        avail = alloc.get_available_capital(1)
        # Tier 1 room = 6000 - 3000 = 3000
        # Reserve room = 8500 - 3000 = 5500
        # min(3000, 5500) = 3000
        assert avail == Decimal("3000")

    def test_reserve_constrains(self) -> None:
        """When reserve limit is tighter than tier limit."""
        positions = {
            "p1": _make_position(5000, tier=1),
            "p2": _make_position(2500, tier=2, protocol="uniswap"),
        }
        alloc = PortfolioAllocator(Decimal("10000"), positions, _default_config())
        avail = alloc.get_available_capital(3)
        # Tier 3 room = 2000 - 0 = 2000
        # Reserve room = 8500 - 7500 = 1000
        # min(2000, 1000) = 1000
        assert avail == Decimal("1000")

    def test_fully_allocated_returns_zero(self) -> None:
        positions = {
            "p1": _make_position(6000, tier=1),
            "p2": _make_position(2500, tier=2, protocol="uniswap"),
        }
        alloc = PortfolioAllocator(Decimal("10000"), positions, _default_config())
        avail = alloc.get_available_capital(3)
        # Reserve room = 8500 - 8500 = 0
        assert avail == Decimal(0)

    def test_unknown_tier_returns_zero(self) -> None:
        alloc = PortfolioAllocator(Decimal("10000"), config=_default_config())
        assert alloc.get_available_capital(99) == Decimal(0)


# ---------------------------------------------------------------------------
# Rebalance trigger
# ---------------------------------------------------------------------------

class TestRebalanceTrigger:
    """Rebalance triggers when tier allocation drifts beyond threshold."""

    def test_balanced_no_rebalance(self) -> None:
        """Portfolio near tier midpoints should not trigger."""
        # Tier targets: T1=55%, T2=30%, T3=15%
        positions = {
            "p1": _make_position(5500, tier=1),
            "p2": _make_position(3000, tier=2, protocol="uniswap"),
            "p3": _make_position(1000, tier=3, protocol="lido", asset="stETH"),
        }
        alloc = PortfolioAllocator(Decimal("10000"), positions, _default_config())
        assert not alloc.needs_rebalance()

    def test_drifted_triggers_rebalance(self) -> None:
        """Large drift in tier 1 should trigger."""
        # Tier 1 target=55%, actual=70% → drift=15% > 5% threshold
        positions = {
            "p1": _make_position(7000, tier=1),
        }
        alloc = PortfolioAllocator(Decimal("10000"), positions, _default_config())
        assert alloc.needs_rebalance()

    def test_empty_portfolio_triggers(self) -> None:
        """Empty portfolio: all tiers at 0% vs targets → drift > threshold."""
        alloc = PortfolioAllocator(Decimal("10000"), config=_default_config())
        # T1 target=55%, actual=0% → drift=55% > 5%
        assert alloc.needs_rebalance()

    def test_zero_capital_no_rebalance(self) -> None:
        alloc = PortfolioAllocator(Decimal("0"), config=_default_config())
        assert not alloc.needs_rebalance()

    def test_custom_threshold(self) -> None:
        """Large threshold should tolerate more drift."""
        config = AllocatorConfig(rebalance_threshold=Decimal("0.50"))
        positions = {
            "p1": _make_position(7000, tier=1),
        }
        alloc = PortfolioAllocator(Decimal("10000"), positions, config)
        # T1 drift = |70% - 55%| = 15% < 50% threshold
        assert not alloc.needs_rebalance()


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    """Edge cases: zero capital, empty portfolio."""

    def test_zero_capital_rejects(self) -> None:
        alloc = PortfolioAllocator(Decimal("0"), config=_default_config())
        result = alloc.check_allocation(_make_position(100, tier=1))
        assert not result.allowed
        assert "zero" in result.reason

    def test_exposure_summary_empty(self) -> None:
        alloc = PortfolioAllocator(Decimal("10000"), config=_default_config())
        summary = alloc.get_exposure_summary()
        assert summary["total_capital"] == "10000"
        assert summary["deployed"] == "0"

    def test_exposure_summary_with_positions(self) -> None:
        positions = {
            "p1": _make_position(5000, protocol="aave", asset="ETH", tier=1),
        }
        alloc = PortfolioAllocator(Decimal("10000"), positions, _default_config())
        summary = alloc.get_exposure_summary()
        assert summary["by_protocol"]["aave"] == "5000"
        assert summary["by_asset"]["ETH"] == "5000"

    def test_zero_capital_summary(self) -> None:
        alloc = PortfolioAllocator(Decimal("0"), config=_default_config())
        summary = alloc.get_exposure_summary()
        assert summary["deployed"] == "0"


# ---------------------------------------------------------------------------
# Config from env
# ---------------------------------------------------------------------------

class TestConfig:
    """AllocatorConfig defaults should match spec."""

    def test_default_tier_bounds(self) -> None:
        cfg = AllocatorConfig()
        assert cfg.tier_bounds[1].min_pct == Decimal("0.50")
        assert cfg.tier_bounds[1].max_pct == Decimal("0.60")
        assert cfg.tier_bounds[2].min_pct == Decimal("0.25")
        assert cfg.tier_bounds[2].max_pct == Decimal("0.35")
        assert cfg.tier_bounds[3].min_pct == Decimal("0.10")
        assert cfg.tier_bounds[3].max_pct == Decimal("0.20")

    def test_default_limits(self) -> None:
        cfg = AllocatorConfig()
        assert cfg.min_stablecoin_reserve == Decimal("0.15")
        assert cfg.max_protocol_exposure == Decimal("0.40")
        assert cfg.max_asset_exposure == Decimal("0.60")
        assert cfg.rebalance_threshold == Decimal("0.05")
