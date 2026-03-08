"""Strategy lifecycle manager with PostgreSQL persistence.

Manages active/inactive status per strategy, respects per-strategy
eval_interval scheduling, and runs concurrent async evaluations of
active strategies whose interval has elapsed.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from monitoring.logger import get_logger

if TYPE_CHECKING:
    from db.repository import DatabaseRepository
    from strategies.base import MarketSnapshot, StrategyReport

_logger = get_logger("strategies.manager", enable_file=False)


class StrategyManager:
    """Manages strategy lifecycle: activation, scheduling, and evaluation.

    Loads strategy statuses from PostgreSQL at init, syncs with discovered
    strategy classes, and tracks evaluation timestamps for interval-based
    scheduling.

    Args:
        repository: DatabaseRepository for persistence.
        strategies: Mapping of strategy_id to strategy class from discover_strategies().
    """

    def __init__(
        self,
        repository: DatabaseRepository,
        strategies: dict[str, type],
    ) -> None:
        self._repo = repository
        self._strategy_classes = dict(strategies)
        self._statuses: dict[str, str] = {}
        self._last_evaluated: dict[str, datetime] = {}
        self._instances: dict[str, object] = {}

        self._load_from_db()
        self.sync_with_discovered(strategies)

    def _load_from_db(self) -> None:
        """Load strategy statuses from PostgreSQL."""
        rows = self._repo.get_strategy_statuses()
        for row in rows:
            self._statuses[row.strategy_id] = row.status

        _logger.info(
            "Strategy statuses loaded from database",
            extra={"data": {"count": len(self._statuses)}},
        )

    def _get_instance(self, strategy_id: str) -> object:
        """Get or create a strategy instance.

        Args:
            strategy_id: The strategy identifier.

        Returns:
            An instance of the strategy class.
        """
        if strategy_id not in self._instances:
            cls = self._strategy_classes[strategy_id]
            self._instances[strategy_id] = cls()
        return self._instances[strategy_id]

    def get_active_strategies(self) -> list[str]:
        """Return list of active strategy IDs.

        Returns:
            List of strategy_id strings with active status.
        """
        return [
            sid for sid, status in self._statuses.items()
            if status == "active" and sid in self._strategy_classes
        ]

    def activate(self, strategy_id: str) -> None:
        """Set a strategy to active status.

        Args:
            strategy_id: The strategy to activate.

        Raises:
            KeyError: If the strategy_id is not in discovered strategies.
        """
        if strategy_id not in self._strategy_classes:
            msg = f"Unknown strategy: {strategy_id}"
            raise KeyError(msg)

        self._statuses[strategy_id] = "active"
        self._repo.save_strategy_status(strategy_id, "active")

        _logger.info(
            "Strategy activated",
            extra={"data": {"strategy_id": strategy_id}},
        )

    def deactivate(self, strategy_id: str) -> None:
        """Set a strategy to inactive status.

        Args:
            strategy_id: The strategy to deactivate.

        Raises:
            KeyError: If the strategy_id is not in discovered strategies.
        """
        if strategy_id not in self._strategy_classes:
            msg = f"Unknown strategy: {strategy_id}"
            raise KeyError(msg)

        self._statuses[strategy_id] = "inactive"
        self._repo.save_strategy_status(strategy_id, "inactive")

        _logger.info(
            "Strategy deactivated",
            extra={"data": {"strategy_id": strategy_id}},
        )

    def should_evaluate(self, strategy_id: str) -> bool:
        """Check if eval_interval has elapsed since last evaluation.

        Args:
            strategy_id: The strategy to check.

        Returns:
            True if the strategy should be evaluated now.
        """
        if strategy_id not in self._strategy_classes:
            return False

        if self._statuses.get(strategy_id) != "active":
            return False

        last = self._last_evaluated.get(strategy_id)
        if last is None:
            return True

        instance = self._get_instance(strategy_id)
        interval: timedelta = instance.eval_interval  # type: ignore[attr-defined]
        return datetime.now(UTC) - last >= interval

    def record_evaluation(self, strategy_id: str) -> None:
        """Record that a strategy was just evaluated.

        Args:
            strategy_id: The strategy that was evaluated.
        """
        self._last_evaluated[strategy_id] = datetime.now(UTC)

    async def evaluate_all(self, snapshot: MarketSnapshot) -> list[StrategyReport]:
        """Concurrently evaluate all active strategies whose interval has elapsed.

        Args:
            snapshot: Market data snapshot for strategy evaluation.

        Returns:
            List of StrategyReport from all evaluated strategies.
        """
        to_evaluate = [
            sid for sid in self.get_active_strategies()
            if self.should_evaluate(sid)
        ]

        if not to_evaluate:
            return []

        async def _run(sid: str) -> StrategyReport | None:
            try:
                instance = self._get_instance(sid)
                report = await asyncio.to_thread(
                    instance.evaluate, snapshot  # type: ignore[attr-defined]
                )
                self.record_evaluation(sid)
                _logger.info(
                    "Strategy evaluated",
                    extra={"data": {"strategy_id": sid}},
                )
                return report
            except Exception:
                _logger.exception(
                    "Strategy evaluation failed",
                    extra={"data": {"strategy_id": sid}},
                )
                return None

        results = await asyncio.gather(*[_run(sid) for sid in to_evaluate])
        return [r for r in results if r is not None]

    def sync_with_discovered(self, discovered: dict[str, type]) -> None:
        """Sync in-memory state with discovered strategy classes.

        Strategies no longer discovered (class file removed) are marked inactive.
        Newly discovered strategies default to active.

        Args:
            discovered: Current mapping of strategy_id to class.
        """
        self._strategy_classes = dict(discovered)

        # Invalidate cached instances for removed strategies
        for sid in list(self._instances.keys()):
            if sid not in discovered:
                del self._instances[sid]

        # Mark removed strategies as inactive
        for sid in list(self._statuses.keys()):
            if sid not in discovered and self._statuses[sid] == "active":
                self._statuses[sid] = "inactive"
                self._repo.save_strategy_status(sid, "inactive")
                _logger.info(
                    "Strategy marked inactive (class not found)",
                    extra={"data": {"strategy_id": sid}},
                )

        # Register new strategies as active
        for sid in discovered:
            if sid not in self._statuses:
                self._statuses[sid] = "active"
                self._repo.save_strategy_status(sid, "active")
                _logger.info(
                    "New strategy registered as active",
                    extra={"data": {"strategy_id": sid}},
                )
