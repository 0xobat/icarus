"""Portfolio allocator — per-strategy capital allocation with PostgreSQL persistence.

Enforces per-strategy allocation limits defined in STRATEGY.md (e.g. LEND-001:
max 70%, LP-001: max 30%). Tracks available capital, allocated amounts per
strategy, and provides allocation summaries for Claude's prompt context.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from monitoring.logger import get_logger

_logger = get_logger("portfolio-allocator", enable_file=False)

# Default per-strategy allocation limits from STRATEGY.md
DEFAULT_STRATEGY_LIMITS: dict[str, Decimal] = {
    "LEND-001": Decimal("0.70"),
    "LP-001": Decimal("0.30"),
}


def _parse_strategy_limits_from_env() -> dict[str, Decimal]:
    """Parse per-strategy limits from environment variables.

    Format: STRATEGY_LIMIT_LEND_001=0.70, STRATEGY_LIMIT_LP_001=0.30
    Falls back to DEFAULT_STRATEGY_LIMITS for any strategy not in env.
    """
    limits = dict(DEFAULT_STRATEGY_LIMITS)
    prefix = "STRATEGY_LIMIT_"
    for key, value in os.environ.items():
        if key.startswith(prefix):
            # STRATEGY_LIMIT_LEND_001 -> LEND-001
            strategy_id = key[len(prefix):].replace("_", "-")
            limits[strategy_id] = Decimal(value)
    return limits


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class AllocatorConfig:
    """Per-strategy allocation limits and reserve requirements."""

    strategy_limits: dict[str, Decimal] = field(
        default_factory=lambda: dict(DEFAULT_STRATEGY_LIMITS)
    )
    min_liquid_reserve: Decimal = Decimal("0.10")


def _load_config() -> AllocatorConfig:
    """Load config from environment variables, falling back to defaults."""
    return AllocatorConfig(
        strategy_limits=_parse_strategy_limits_from_env(),
        min_liquid_reserve=Decimal(
            os.environ.get("MIN_LIQUID_RESERVE", "0.10"),
        ),
    )


# ---------------------------------------------------------------------------
# Allocator
# ---------------------------------------------------------------------------
@dataclass
class AllocationCheck:
    """Result of a pre-trade allocation check."""

    allowed: bool
    reason: str


class PortfolioAllocator:
    """Manages per-strategy capital allocation with PostgreSQL-backed state.

    Each strategy has a maximum allocation percentage from STRATEGY.md.
    The allocator loads open positions from the database repository to
    determine current allocations and available capital per strategy.

    Args:
        total_capital: Total portfolio value in USD.
        repository: Database repository for loading positions. Optional for
            in-memory-only usage (e.g. tests).
        config: Allocation configuration. Loaded from env if not provided.
    """

    def __init__(
        self,
        total_capital: Decimal,
        repository: Any | None = None,
        config: AllocatorConfig | None = None,
    ) -> None:
        self.total_capital = total_capital
        self.config = config or _load_config()
        self._repository = repository
        # In-memory allocation state: strategy_id -> allocated USD value
        self._allocations: dict[str, Decimal] = {}
        if repository is not None:
            self._load_from_db()

    def _load_from_db(self) -> None:
        """Load current allocations from PostgreSQL open positions."""
        positions = self._repository.get_positions(status="open")
        self._allocations.clear()
        for pos in positions:
            strategy = pos.strategy
            value = Decimal(str(pos.current_value))
            self._allocations[strategy] = (
                self._allocations.get(strategy, Decimal(0)) + value
            )
        _logger.info(
            "Allocations loaded from database",
            extra={"data": {
                "strategies": {k: str(v) for k, v in self._allocations.items()},
                "total_allocated": str(self._total_allocated()),
            }},
        )

    def reload(self) -> None:
        """Reload allocation state from the database."""
        if self._repository is not None:
            self._load_from_db()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _total_allocated(self) -> Decimal:
        """Sum of all strategy allocations."""
        return sum(self._allocations.values(), Decimal(0))

    def _strategy_allocated(self, strategy_id: str) -> Decimal:
        """Amount currently allocated to a specific strategy."""
        return self._allocations.get(strategy_id, Decimal(0))

    def _max_for_strategy(self, strategy_id: str) -> Decimal | None:
        """Return the max allocation percentage for a strategy, or None."""
        return self.config.strategy_limits.get(strategy_id)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def can_allocate(self, strategy_id: str, amount: Decimal | float) -> bool:
        """Check if an allocation of the given amount is allowed for a strategy.

        Args:
            strategy_id: The strategy to allocate for (e.g. 'LEND-001').
            amount: Amount in USD to allocate.

        Returns:
            True if the allocation is within all limits, False otherwise.
        """
        return self.check_allocation_for_strategy(strategy_id, amount).allowed

    def check_allocation_for_strategy(
        self, strategy_id: str, amount: Decimal | float,
    ) -> AllocationCheck:
        """Validate a proposed allocation against per-strategy limits.

        Args:
            strategy_id: The strategy to allocate for.
            amount: Amount in USD to allocate.

        Returns:
            AllocationCheck with allowed=True if within limits, or reason.
        """
        amount = Decimal(str(amount))

        if self.total_capital <= 0:
            return AllocationCheck(False, "total capital is zero or negative")

        # 1. Strategy limit check
        max_pct = self._max_for_strategy(strategy_id)
        if max_pct is None:
            return AllocationCheck(False, f"unknown strategy '{strategy_id}'")

        current = self._strategy_allocated(strategy_id)
        after = current + amount
        after_pct = after / self.total_capital
        if after_pct > max_pct:
            return AllocationCheck(
                False,
                f"strategy '{strategy_id}' would be {after_pct:.1%} "
                f"(max {max_pct:.0%})",
            )

        # 2. Liquid reserve check
        total_after = self._total_allocated() + amount
        reserve_after = self.total_capital - total_after
        reserve_pct = reserve_after / self.total_capital
        if reserve_pct < self.config.min_liquid_reserve:
            return AllocationCheck(
                False,
                f"liquid reserve would be {reserve_pct:.1%} "
                f"(min {self.config.min_liquid_reserve:.0%})",
            )

        _logger.debug(
            "Allocation check passed",
            extra={"data": {
                "strategy": strategy_id,
                "amount": str(amount),
                "after_pct": str(after_pct),
            }},
        )
        return AllocationCheck(True, "ok")

    def check_allocation(self, proposed: dict[str, Any]) -> AllocationCheck:
        """Validate a proposed position against allocation limits.

        Backward-compatible interface. Accepts a dict with ``strategy`` and
        ``value_usd`` keys.

        Args:
            proposed: Dict with ``strategy`` and ``value_usd``.

        Returns:
            AllocationCheck with allowed/reason.
        """
        value = Decimal(str(proposed["value_usd"]))
        strategy_id = proposed.get("strategy")
        if strategy_id is None:
            return AllocationCheck(False, "missing 'strategy' in proposed allocation")
        return self.check_allocation_for_strategy(strategy_id, value)

    def get_available_capital(self, strategy_id: str) -> Decimal:
        """How much capital can still be deployed into a strategy.

        Returns the lesser of:
        - remaining room under the strategy's max allocation
        - remaining capital after liquid reserve

        Args:
            strategy_id: The strategy to check (e.g. 'LEND-001').

        Returns:
            Available capital in USD. Zero if strategy is unknown.
        """
        max_pct = self._max_for_strategy(strategy_id)
        if max_pct is None:
            return Decimal(0)

        strategy_room = (max_pct * self.total_capital) - self._strategy_allocated(
            strategy_id
        )

        max_deployable = self.total_capital * (1 - self.config.min_liquid_reserve)
        reserve_room = max_deployable - self._total_allocated()

        available = min(strategy_room, reserve_room)
        return max(available, Decimal(0))

    def get_allocation_summary(self) -> dict[str, Any]:
        """Return allocation summary for Claude's prompt context.

        Returns:
            Dict with total_capital, available_capital, per-strategy
            allocations with current/max/available, and reserve info.
        """
        total = self.total_capital
        allocated = self._total_allocated()
        available = total - allocated

        if total <= 0:
            return {
                "total_capital_usd": "0",
                "allocated_usd": "0",
                "available_usd": "0",
                "strategies": {},
            }

        strategies: dict[str, Any] = {}
        for strategy_id, max_pct in self.config.strategy_limits.items():
            current = self._strategy_allocated(strategy_id)
            max_usd = max_pct * total
            strategy_available = self.get_available_capital(strategy_id)
            current_pct = current / total if total > 0 else Decimal(0)

            strategies[strategy_id] = {
                "allocated_usd": str(current),
                "allocated_pct": str(current_pct),
                "max_pct": str(max_pct),
                "max_usd": str(max_usd),
                "available_usd": str(strategy_available),
            }

        reserve = available
        reserve_pct = reserve / total if total > 0 else Decimal(0)

        return {
            "total_capital_usd": str(total),
            "allocated_usd": str(allocated),
            "available_usd": str(available),
            "allocated_pct": str(allocated / total if total > 0 else Decimal(0)),
            "reserve_pct": str(reserve_pct),
            "strategies": strategies,
        }

    def update_allocation(self, strategy_id: str, amount: Decimal | float) -> None:
        """Manually update the in-memory allocation for a strategy.

        Used when a position is opened/closed and the DB hasn't been
        refreshed yet.

        Args:
            strategy_id: The strategy to update.
            amount: New total allocated amount for the strategy.
        """
        self._allocations[strategy_id] = Decimal(str(amount))

    def get_current_allocations(self) -> dict[str, Decimal]:
        """Return current allocation fractions per strategy.

        Each value is the fraction of total capital currently allocated
        to that strategy (e.g. ``Decimal('0.35')`` for 35%).

        Returns:
            Mapping of strategy_id to current allocation fraction.
        """
        if self.total_capital <= 0:
            return {sid: Decimal(0) for sid in self.config.strategy_limits}
        return {
            sid: self._strategy_allocated(sid) / self.total_capital
            for sid in self.config.strategy_limits
        }

    def get_target_allocations(self) -> dict[str, Decimal]:
        """Return target allocation fractions per strategy from STRATEGY.md limits.

        Returns:
            Mapping of strategy_id to maximum allocation fraction.
        """
        return dict(self.config.strategy_limits)

    def get_exposure_summary(self) -> dict[str, Any]:
        """Return allocation summary (legacy alias for get_allocation_summary)."""
        return self.get_allocation_summary()
