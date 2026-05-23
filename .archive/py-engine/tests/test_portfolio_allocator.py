"""Tests for portfolio allocator — PORT-001.

Tests per-strategy allocation limits, PostgreSQL persistence,
available capital tracking, and allocation summary for Claude context.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

from portfolio.allocator import (
    DEFAULT_STRATEGY_LIMITS,
    AllocatorConfig,
    PortfolioAllocator,
)

# ---------------------------------------------------------------------------
# Mock repository for database tests
# ---------------------------------------------------------------------------

class MockPosition:
    """Minimal mock for PortfolioPosition ORM model."""

    def __init__(self, strategy: str, current_value: float) -> None:
        self.strategy = strategy
        self.current_value = current_value


def _mock_repository(positions: list[MockPosition] | None = None) -> MagicMock:
    repo = MagicMock()
    repo.get_positions.return_value = positions or []
    return repo


# ---------------------------------------------------------------------------
# Default strategy limits from STRATEGY.md
# ---------------------------------------------------------------------------

class TestDefaultLimits:
    """Strategy limits match STRATEGY.md: LEND-001=70%, LP-001=30%."""

    def test_default_lend_001_limit(self) -> None:
        assert DEFAULT_STRATEGY_LIMITS["LEND-001"] == Decimal("0.70")

    def test_default_lp_001_limit(self) -> None:
        assert DEFAULT_STRATEGY_LIMITS["LP-001"] == Decimal("0.30")

    def test_config_defaults(self) -> None:
        cfg = AllocatorConfig()
        assert cfg.strategy_limits["LEND-001"] == Decimal("0.70")
        assert cfg.strategy_limits["LP-001"] == Decimal("0.30")
        assert cfg.min_liquid_reserve == Decimal("0.10")


# ---------------------------------------------------------------------------
# Per-strategy allocation enforcement
# ---------------------------------------------------------------------------

class TestStrategyAllocation:
    """Capital must be allocated within per-strategy percentage bounds."""

    def test_lend_001_within_limit_allowed(self) -> None:
        alloc = PortfolioAllocator(Decimal("10000"))
        assert alloc.can_allocate("LEND-001", Decimal("6000"))  # 60% < 70%

    def test_lend_001_at_limit_allowed(self) -> None:
        alloc = PortfolioAllocator(Decimal("10000"))
        assert alloc.can_allocate("LEND-001", Decimal("7000"))  # 70% == 70%

    def test_lend_001_exceeds_limit_rejected(self) -> None:
        alloc = PortfolioAllocator(Decimal("10000"))
        assert not alloc.can_allocate("LEND-001", Decimal("7100"))  # 71% > 70%

    def test_lp_001_within_limit_allowed(self) -> None:
        alloc = PortfolioAllocator(Decimal("10000"))
        assert alloc.can_allocate("LP-001", Decimal("2000"))  # 20% < 30%

    def test_lp_001_exceeds_limit_rejected(self) -> None:
        alloc = PortfolioAllocator(Decimal("10000"))
        assert not alloc.can_allocate("LP-001", Decimal("3100"))  # 31% > 30%

    def test_unknown_strategy_rejected(self) -> None:
        alloc = PortfolioAllocator(Decimal("10000"))
        result = alloc.check_allocation_for_strategy("UNKNOWN-001", Decimal("100"))
        assert not result.allowed
        assert "unknown strategy" in result.reason

    def test_cumulative_enforcement(self) -> None:
        """Adding to existing allocation must count cumulatively."""
        alloc = PortfolioAllocator(Decimal("10000"))
        alloc.update_allocation("LEND-001", Decimal("6000"))  # 60%
        # 60% + 11% = 71% > 70%
        assert not alloc.can_allocate("LEND-001", Decimal("1100"))

    def test_cumulative_within_limit(self) -> None:
        alloc = PortfolioAllocator(Decimal("10000"))
        alloc.update_allocation("LEND-001", Decimal("6000"))
        # 60% + 10% = 70% == max
        assert alloc.can_allocate("LEND-001", Decimal("1000"))


# ---------------------------------------------------------------------------
# Liquid reserve enforcement
# ---------------------------------------------------------------------------

class TestLiquidReserve:
    """At least min_liquid_reserve (default 10%) must remain unallocated."""

    def test_sufficient_reserve_allowed(self) -> None:
        alloc = PortfolioAllocator(Decimal("10000"))
        # 70% LEND + 20% LP = 90% total, 10% reserve == min
        alloc.update_allocation("LEND-001", Decimal("7000"))
        assert alloc.can_allocate("LP-001", Decimal("2000"))

    def test_insufficient_reserve_rejected(self) -> None:
        alloc = PortfolioAllocator(Decimal("10000"))
        alloc.update_allocation("LEND-001", Decimal("7000"))
        # 70% + 21% = 91% → reserve = 9% < 10%
        result = alloc.check_allocation_for_strategy("LP-001", Decimal("2100"))
        assert not result.allowed
        assert "liquid reserve" in result.reason

    def test_custom_reserve(self) -> None:
        config = AllocatorConfig(min_liquid_reserve=Decimal("0.20"))
        alloc = PortfolioAllocator(Decimal("10000"), config=config)
        # 70% + 11% = 81% → reserve = 19% < 20%
        alloc.update_allocation("LEND-001", Decimal("7000"))
        assert not alloc.can_allocate("LP-001", Decimal("1100"))


# ---------------------------------------------------------------------------
# Available capital
# ---------------------------------------------------------------------------

class TestAvailableCapital:
    """get_available_capital returns deployable amount per strategy."""

    def test_empty_portfolio_lend(self) -> None:
        alloc = PortfolioAllocator(Decimal("10000"))
        # LEND-001 max = 70% of 10000 = 7000
        assert alloc.get_available_capital("LEND-001") == Decimal("7000")

    def test_empty_portfolio_lp(self) -> None:
        alloc = PortfolioAllocator(Decimal("10000"))
        # LP-001 max = 30% of 10000 = 3000
        assert alloc.get_available_capital("LP-001") == Decimal("3000")

    def test_partially_allocated(self) -> None:
        alloc = PortfolioAllocator(Decimal("10000"))
        alloc.update_allocation("LEND-001", Decimal("5000"))
        # Strategy room: 7000 - 5000 = 2000
        # Reserve room: 9000 - 5000 = 4000
        # min(2000, 4000) = 2000
        assert alloc.get_available_capital("LEND-001") == Decimal("2000")

    def test_reserve_constrains(self) -> None:
        alloc = PortfolioAllocator(Decimal("10000"))
        alloc.update_allocation("LEND-001", Decimal("7000"))
        # LP-001 strategy room: 3000 - 0 = 3000
        # Reserve room: 9000 - 7000 = 2000
        # min(3000, 2000) = 2000
        assert alloc.get_available_capital("LP-001") == Decimal("2000")

    def test_fully_allocated_returns_zero(self) -> None:
        alloc = PortfolioAllocator(Decimal("10000"))
        alloc.update_allocation("LEND-001", Decimal("7000"))
        alloc.update_allocation("LP-001", Decimal("2000"))
        # Reserve room: 9000 - 9000 = 0
        assert alloc.get_available_capital("LP-001") == Decimal(0)

    def test_unknown_strategy_returns_zero(self) -> None:
        alloc = PortfolioAllocator(Decimal("10000"))
        assert alloc.get_available_capital("UNKNOWN") == Decimal(0)


# ---------------------------------------------------------------------------
# check_allocation dict interface
# ---------------------------------------------------------------------------

class TestCheckAllocation:
    """check_allocation accepts dict with 'strategy' and 'value_usd'."""

    def test_allowed(self) -> None:
        alloc = PortfolioAllocator(Decimal("10000"))
        result = alloc.check_allocation({
            "strategy": "LEND-001",
            "value_usd": 5000,
        })
        assert result.allowed

    def test_rejected(self) -> None:
        alloc = PortfolioAllocator(Decimal("10000"))
        result = alloc.check_allocation({
            "strategy": "LEND-001",
            "value_usd": 8000,
        })
        assert not result.allowed

    def test_missing_strategy_rejected(self) -> None:
        alloc = PortfolioAllocator(Decimal("10000"))
        result = alloc.check_allocation({"value_usd": 1000})
        assert not result.allowed
        assert "missing" in result.reason


# ---------------------------------------------------------------------------
# PostgreSQL persistence via repository
# ---------------------------------------------------------------------------

class TestDatabasePersistence:
    """Allocation state loaded from PostgreSQL via repository."""

    def test_loads_positions_from_db(self) -> None:
        repo = _mock_repository([
            MockPosition("LEND-001", 5000.0),
            MockPosition("LEND-001", 1000.0),
            MockPosition("LP-001", 2000.0),
        ])
        alloc = PortfolioAllocator(Decimal("10000"), repository=repo)
        repo.get_positions.assert_called_once_with(status="open")
        # LEND-001 = 5000 + 1000 = 6000
        assert not alloc.can_allocate("LEND-001", Decimal("1100"))  # 71%
        assert alloc.can_allocate("LEND-001", Decimal("1000"))  # 70%

    def test_reload_refreshes_state(self) -> None:
        repo = _mock_repository([MockPosition("LEND-001", 3000.0)])
        alloc = PortfolioAllocator(Decimal("10000"), repository=repo)
        assert alloc.get_available_capital("LEND-001") == Decimal("4000")

        # Simulate DB update
        repo.get_positions.return_value = [MockPosition("LEND-001", 6000.0)]
        alloc.reload()
        assert alloc.get_available_capital("LEND-001") == Decimal("1000")

    def test_empty_db(self) -> None:
        repo = _mock_repository([])
        alloc = PortfolioAllocator(Decimal("10000"), repository=repo)
        assert alloc.get_available_capital("LEND-001") == Decimal("7000")

    def test_no_repository_works(self) -> None:
        """In-memory mode (no repo) should work without DB."""
        alloc = PortfolioAllocator(Decimal("10000"))
        assert alloc.get_available_capital("LEND-001") == Decimal("7000")
        alloc.reload()  # no-op when no repo


# ---------------------------------------------------------------------------
# Allocation summary for Claude's prompt context
# ---------------------------------------------------------------------------

class TestAllocationSummary:
    """get_allocation_summary provides data for Claude's prompt."""

    def test_empty_portfolio(self) -> None:
        alloc = PortfolioAllocator(Decimal("10000"))
        summary = alloc.get_allocation_summary()
        assert summary["total_capital_usd"] == "10000"
        assert summary["allocated_usd"] == "0"
        assert summary["available_usd"] == "10000"
        assert "LEND-001" in summary["strategies"]
        assert "LP-001" in summary["strategies"]
        lend = summary["strategies"]["LEND-001"]
        assert lend["allocated_usd"] == "0"
        assert lend["max_pct"] == "0.70"

    def test_with_allocations(self) -> None:
        alloc = PortfolioAllocator(Decimal("10000"))
        alloc.update_allocation("LEND-001", Decimal("5000"))
        alloc.update_allocation("LP-001", Decimal("2000"))
        summary = alloc.get_allocation_summary()
        assert summary["allocated_usd"] == "7000"
        assert summary["available_usd"] == "3000"
        lend = summary["strategies"]["LEND-001"]
        assert lend["allocated_usd"] == "5000"
        assert lend["allocated_pct"] == "0.5"

    def test_zero_capital(self) -> None:
        alloc = PortfolioAllocator(Decimal("0"))
        summary = alloc.get_allocation_summary()
        assert summary["total_capital_usd"] == "0"
        assert summary["strategies"] == {}

    def test_exposure_summary_alias(self) -> None:
        """get_exposure_summary is a legacy alias."""
        alloc = PortfolioAllocator(Decimal("10000"))
        assert alloc.get_exposure_summary() == alloc.get_allocation_summary()


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    """Edge cases: zero capital, negative."""

    def test_zero_capital_rejects(self) -> None:
        alloc = PortfolioAllocator(Decimal("0"))
        assert not alloc.can_allocate("LEND-001", Decimal("100"))

    def test_update_allocation(self) -> None:
        alloc = PortfolioAllocator(Decimal("10000"))
        alloc.update_allocation("LEND-001", Decimal("5000"))
        assert not alloc.can_allocate("LEND-001", Decimal("2100"))

    def test_can_allocate_with_float(self) -> None:
        alloc = PortfolioAllocator(Decimal("10000"))
        assert alloc.can_allocate("LEND-001", 5000.0)

    def test_custom_strategy_limits(self) -> None:
        config = AllocatorConfig(
            strategy_limits={"CUSTOM-001": Decimal("0.50")},
        )
        alloc = PortfolioAllocator(Decimal("10000"), config=config)
        assert alloc.can_allocate("CUSTOM-001", Decimal("5000"))
        assert not alloc.can_allocate("CUSTOM-001", Decimal("5100"))
