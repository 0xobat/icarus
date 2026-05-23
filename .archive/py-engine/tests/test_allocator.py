"""Legacy allocator tests — adapted for per-strategy allocation (PORT-001).

These tests verify backward compatibility of the PortfolioAllocator
with the per-strategy limit model from STRATEGY.md.
"""

from __future__ import annotations

from decimal import Decimal

from portfolio.allocator import (
    AllocatorConfig,
    PortfolioAllocator,
)

# ---------------------------------------------------------------------------
# Strategy allocation bounds
# ---------------------------------------------------------------------------

class TestStrategyAllocation:
    """Capital must be allocated within per-strategy percentage bounds."""

    def test_lend_within_bounds_allowed(self) -> None:
        alloc = PortfolioAllocator(Decimal("10000"))
        result = alloc.check_allocation({
            "strategy": "LEND-001", "value_usd": 6000,
        })
        assert result.allowed

    def test_lend_exceeds_max_rejected(self) -> None:
        alloc = PortfolioAllocator(Decimal("10000"))
        result = alloc.check_allocation({
            "strategy": "LEND-001", "value_usd": 7100,
        })
        assert not result.allowed
        assert "LEND-001" in result.reason

    def test_lp_within_bounds_allowed(self) -> None:
        alloc = PortfolioAllocator(Decimal("10000"))
        result = alloc.check_allocation({
            "strategy": "LP-001", "value_usd": 2000,
        })
        assert result.allowed

    def test_lp_exceeds_max_rejected(self) -> None:
        alloc = PortfolioAllocator(Decimal("10000"))
        result = alloc.check_allocation({
            "strategy": "LP-001", "value_usd": 3100,
        })
        assert not result.allowed
        assert "LP-001" in result.reason

    def test_unknown_strategy_rejected(self) -> None:
        alloc = PortfolioAllocator(Decimal("10000"))
        result = alloc.check_allocation({
            "strategy": "UNKNOWN-99", "value_usd": 100,
        })
        assert not result.allowed
        assert "unknown" in result.reason

    def test_cumulative_strategy_enforcement(self) -> None:
        """Adding to existing strategy allocation must count cumulatively."""
        alloc = PortfolioAllocator(Decimal("10000"))
        alloc.update_allocation("LEND-001", Decimal("6000"))
        result = alloc.check_allocation({
            "strategy": "LEND-001", "value_usd": 1100,
        })
        assert not result.allowed


# ---------------------------------------------------------------------------
# Reserve enforcement
# ---------------------------------------------------------------------------

class TestReserve:
    """At least min_liquid_reserve must remain unallocated."""

    def test_sufficient_reserve_allowed(self) -> None:
        alloc = PortfolioAllocator(Decimal("10000"))
        alloc.update_allocation("LEND-001", Decimal("7000"))
        result = alloc.check_allocation({
            "strategy": "LP-001", "value_usd": 2000,
        })
        assert result.allowed

    def test_insufficient_reserve_rejected(self) -> None:
        alloc = PortfolioAllocator(Decimal("10000"))
        alloc.update_allocation("LEND-001", Decimal("7000"))
        result = alloc.check_allocation({
            "strategy": "LP-001", "value_usd": 2100,
        })
        assert not result.allowed
        assert "liquid reserve" in result.reason


# ---------------------------------------------------------------------------
# Available capital
# ---------------------------------------------------------------------------

class TestAvailableCapital:
    """get_available_capital returns deployable amount per strategy."""

    def test_empty_portfolio(self) -> None:
        alloc = PortfolioAllocator(Decimal("10000"))
        avail = alloc.get_available_capital("LEND-001")
        assert avail == Decimal("7000")

    def test_partially_allocated(self) -> None:
        alloc = PortfolioAllocator(Decimal("10000"))
        alloc.update_allocation("LEND-001", Decimal("3000"))
        avail = alloc.get_available_capital("LEND-001")
        assert avail == Decimal("4000")

    def test_reserve_constrains(self) -> None:
        alloc = PortfolioAllocator(Decimal("10000"))
        alloc.update_allocation("LEND-001", Decimal("7000"))
        avail = alloc.get_available_capital("LP-001")
        # LP room = 3000, reserve room = 9000 - 7000 = 2000
        assert avail == Decimal("2000")

    def test_fully_allocated_returns_zero(self) -> None:
        alloc = PortfolioAllocator(Decimal("10000"))
        alloc.update_allocation("LEND-001", Decimal("7000"))
        alloc.update_allocation("LP-001", Decimal("2000"))
        avail = alloc.get_available_capital("LP-001")
        assert avail == Decimal(0)

    def test_unknown_strategy_returns_zero(self) -> None:
        alloc = PortfolioAllocator(Decimal("10000"))
        assert alloc.get_available_capital("UNKNOWN") == Decimal(0)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    """Edge cases: zero capital, empty portfolio."""

    def test_zero_capital_rejects(self) -> None:
        alloc = PortfolioAllocator(Decimal("0"))
        result = alloc.check_allocation({
            "strategy": "LEND-001", "value_usd": 100,
        })
        assert not result.allowed
        assert "zero" in result.reason

    def test_exposure_summary_empty(self) -> None:
        alloc = PortfolioAllocator(Decimal("10000"))
        summary = alloc.get_exposure_summary()
        assert summary["total_capital_usd"] == "10000"
        assert summary["allocated_usd"] == "0"

    def test_exposure_summary_with_allocations(self) -> None:
        alloc = PortfolioAllocator(Decimal("10000"))
        alloc.update_allocation("LEND-001", Decimal("5000"))
        summary = alloc.get_exposure_summary()
        assert summary["strategies"]["LEND-001"]["allocated_usd"] == "5000"

    def test_zero_capital_summary(self) -> None:
        alloc = PortfolioAllocator(Decimal("0"))
        summary = alloc.get_exposure_summary()
        assert summary["allocated_usd"] == "0"


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

class TestConfig:
    """AllocatorConfig defaults should match STRATEGY.md."""

    def test_default_strategy_limits(self) -> None:
        cfg = AllocatorConfig()
        assert cfg.strategy_limits["LEND-001"] == Decimal("0.70")
        assert cfg.strategy_limits["LP-001"] == Decimal("0.30")

    def test_default_reserve(self) -> None:
        cfg = AllocatorConfig()
        assert cfg.min_liquid_reserve == Decimal("0.10")
