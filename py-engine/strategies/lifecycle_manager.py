"""Strategy lifecycle manager — status transitions, performance, cycle enforcement (STRAT-007).

Each strategy has a status persisted in agent-state.json via StateManager.
Only one strategy adjustment per decision cycle is enforced.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from harness.state_manager import StateManager
from monitoring.logger import get_logger

_logger = get_logger("lifecycle-manager", enable_file=False)

# Valid status transitions
VALID_TRANSITIONS: dict[str, set[str]] = {
    "evaluating": {"active"},
    "active": {"paused", "retired"},
    "paused": {"active"},
    "retired": set(),
}

ALL_STATUSES = set(VALID_TRANSITIONS.keys())


@dataclass
class StrategyPerformance:
    """Per-strategy performance metrics."""

    strategy_id: str
    total_pnl: Decimal = Decimal(0)
    sharpe_ratio: Decimal = Decimal(0)
    max_drawdown: Decimal = Decimal(0)
    peak_value: Decimal = Decimal(0)
    current_value: Decimal = Decimal(0)
    returns: list[Decimal] = field(default_factory=list)

    def update(self, new_value: Decimal) -> None:
        """Update performance with a new portfolio value snapshot."""
        if self.peak_value == 0:
            self.peak_value = new_value
            self.current_value = new_value
            return

        old_value = self.current_value
        self.current_value = new_value

        if new_value > self.peak_value:
            self.peak_value = new_value

        # Track return
        if old_value > 0:
            ret = (new_value - old_value) / old_value
            self.returns.append(ret)

        # P&L from initial
        if self.peak_value > 0:
            self.total_pnl = (
                new_value - self.peak_value
                + sum(r * self.peak_value for r in self.returns)
                if self.returns
                else new_value - self.peak_value
            )

        # Drawdown
        if self.peak_value > 0:
            drawdown = (self.peak_value - new_value) / self.peak_value
            if drawdown > self.max_drawdown:
                self.max_drawdown = drawdown

        # Sharpe ratio (simplified: mean return / std dev of returns)
        if len(self.returns) >= 2:
            mean = sum(self.returns) / len(self.returns)
            variance = sum(
                (r - mean) ** 2 for r in self.returns
            ) / (len(self.returns) - 1)
            exp = Decimal("0.5")
            std = Decimal(str(variance ** exp))
            self.sharpe_ratio = mean / std if std > 0 else Decimal(0)

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "total_pnl": str(self.total_pnl),
            "sharpe_ratio": str(self.sharpe_ratio),
            "max_drawdown": str(self.max_drawdown),
            "peak_value": str(self.peak_value),
            "current_value": str(self.current_value),
        }


class LifecycleManager:
    """Manages strategy lifecycle: status transitions, performance, cycle enforcement.

    - Valid transitions: evaluating->active, active->paused, paused->active,
      active->retired
    - Only one strategy adjustment per decision cycle
    - Underperforming strategies auto-paused if losses exceed threshold
    - New strategy tier activation requires human approval (flagged in state)
    """

    def __init__(
        self,
        state_manager: StateManager,
        *,
        loss_threshold: Decimal = Decimal("0.10"),
    ) -> None:
        self._state = state_manager
        self._loss_threshold = loss_threshold
        self._performance: dict[str, StrategyPerformance] = {}
        self._adjustment_made_this_cycle = False

    @property
    def adjustment_made_this_cycle(self) -> bool:
        return self._adjustment_made_this_cycle

    def reset_cycle(self) -> None:
        """Reset cycle tracking — call at the start of each decision cycle."""
        self._adjustment_made_this_cycle = False

    def get_status(self, strategy_id: str) -> str:
        """Get current status for a strategy from persisted state."""
        statuses = self._state.get_strategy_statuses()
        return statuses.get(strategy_id, "evaluating")

    def transition(self, strategy_id: str, new_status: str) -> bool:
        """Attempt a status transition for a strategy.

        Returns True if the transition was valid and applied, False otherwise.
        Enforces one-adjustment-per-cycle rule.
        """
        if new_status not in ALL_STATUSES:
            _logger.warning(
                "Invalid status",
                extra={"data": {
                    "strategy_id": strategy_id, "status": new_status,
                }},
            )
            return False

        current = self.get_status(strategy_id)
        allowed = VALID_TRANSITIONS.get(current, set())

        if new_status not in allowed:
            _logger.warning(
                "Invalid transition",
                extra={"data": {
                    "strategy_id": strategy_id,
                    "from": current,
                    "to": new_status,
                    "allowed": sorted(allowed),
                }},
            )
            return False

        if self._adjustment_made_this_cycle:
            _logger.warning(
                "Cycle adjustment limit reached",
                extra={"data": {
                    "strategy_id": strategy_id,
                    "attempted": new_status,
                }},
            )
            return False

        self._state.set_strategy_status(strategy_id, new_status)
        self._adjustment_made_this_cycle = True

        _logger.info(
            "Strategy transitioned",
            extra={"data": {
                "strategy_id": strategy_id,
                "from": current,
                "to": new_status,
            }},
        )
        return True

    def get_performance(self, strategy_id: str) -> StrategyPerformance:
        """Get or create performance tracker for a strategy."""
        if strategy_id not in self._performance:
            self._performance[strategy_id] = StrategyPerformance(
                strategy_id=strategy_id,
            )
        return self._performance[strategy_id]

    def update_performance(
        self, strategy_id: str, current_value: Decimal,
    ) -> StrategyPerformance:
        """Update performance metrics and check for auto-pause."""
        perf = self.get_performance(strategy_id)
        perf.update(current_value)

        # Auto-pause check
        if (
            perf.max_drawdown >= self._loss_threshold
            and self.get_status(strategy_id) == "active"
            and not self._adjustment_made_this_cycle
        ):
            self.transition(strategy_id, "paused")
            _logger.warning(
                "Strategy auto-paused due to losses",
                extra={"data": {
                    "strategy_id": strategy_id,
                    "max_drawdown": str(perf.max_drawdown),
                    "threshold": str(self._loss_threshold),
                }},
            )

        return perf

    def request_tier_activation(
        self, strategy_id: str, tier: int,
    ) -> None:
        """Flag that a new strategy tier requires human approval."""
        self._state.set_operational_flag(
            f"tier_activation_pending:{strategy_id}:{tier}", True,
        )
        _logger.info(
            "Tier activation requested — requires human approval",
            extra={"data": {
                "strategy_id": strategy_id,
                "tier": tier,
                "timestamp": datetime.now(UTC).isoformat(),
            }},
        )

    def is_tier_activation_pending(
        self, strategy_id: str, tier: int,
    ) -> bool:
        """Check if a tier activation is pending human approval."""
        flags = self._state.get_operational_flags()
        return flags.get(
            f"tier_activation_pending:{strategy_id}:{tier}", False,
        )

    def get_all_performance(self) -> dict[str, dict[str, Any]]:
        """Return performance summary for all tracked strategies."""
        return {
            sid: perf.to_dict()
            for sid, perf in self._performance.items()
        }
