"""Per-position loss limit — individual position stop-loss with cooldown (RISK-002).

Every position has tracked entry value. At >10% loss: close position,
strategy enters 24h cooldown. Cooldown enforced — strategy cannot open
new positions until expiry.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from monitoring.logger import get_logger

_logger = get_logger("position-loss-limit", enable_file=False)

DEFAULT_LOSS_THRESHOLD = Decimal("0.10")  # 10%
DEFAULT_COOLDOWN_HOURS = 24


@dataclass
class LossEvent:
    """Record of a position being closed due to loss limit."""

    position_id: str
    strategy_id: str
    asset: str
    entry_price: Decimal
    exit_price: Decimal
    loss_pct: Decimal
    duration_seconds: float
    timestamp: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "position_id": self.position_id,
            "strategy_id": self.strategy_id,
            "asset": self.asset,
            "entry_price": str(self.entry_price),
            "exit_price": str(self.exit_price),
            "loss_pct": str(self.loss_pct),
            "duration_seconds": self.duration_seconds,
            "timestamp": self.timestamp,
        }


@dataclass
class PositionCheck:
    """Result of checking a position against loss limit."""

    position_id: str
    should_close: bool
    loss_pct: Decimal
    reason: str


class PositionLossLimit:
    """Per-position loss limit with strategy cooldown.

    - Every position has tracked entry value
    - Loss calculated relative to entry on each price update
    - At >10% loss: close position, strategy enters 24h cooldown
    - Cooldown enforced — strategy cannot open new positions until expiry
    - Loss events logged with full context
    """

    def __init__(
        self,
        *,
        loss_threshold: Decimal = DEFAULT_LOSS_THRESHOLD,
        cooldown_hours: int = DEFAULT_COOLDOWN_HOURS,
    ) -> None:
        self._loss_threshold = loss_threshold
        self._cooldown_hours = cooldown_hours
        self._cooldowns: dict[str, datetime] = {}
        self._loss_events: list[LossEvent] = []

    @property
    def loss_threshold(self) -> Decimal:
        return self._loss_threshold

    @property
    def cooldown_hours(self) -> int:
        return self._cooldown_hours

    @property
    def loss_events(self) -> list[LossEvent]:
        return list(self._loss_events)

    def check_position(
        self,
        *,
        position_id: str,
        entry_price: Decimal,
        current_price: Decimal,
    ) -> PositionCheck:
        """Check if a position has breached the loss limit.

        Returns a PositionCheck indicating whether the position
        should be closed.
        """
        if entry_price <= 0:
            return PositionCheck(
                position_id=position_id,
                should_close=False,
                loss_pct=Decimal(0),
                reason="invalid entry price",
            )

        loss_pct = (entry_price - current_price) / entry_price

        if loss_pct > self._loss_threshold:
            return PositionCheck(
                position_id=position_id,
                should_close=True,
                loss_pct=loss_pct,
                reason=f"loss {loss_pct:.1%} exceeds {self._loss_threshold:.0%}",
            )

        return PositionCheck(
            position_id=position_id,
            should_close=False,
            loss_pct=loss_pct,
            reason="within limit",
        )

    def record_loss_event(
        self,
        *,
        position_id: str,
        strategy_id: str,
        asset: str,
        entry_price: Decimal,
        exit_price: Decimal,
        entry_time: str,
    ) -> LossEvent:
        """Record a loss event and start strategy cooldown."""
        now = datetime.now(UTC)
        loss_pct = (
            (entry_price - exit_price) / entry_price
            if entry_price > 0
            else Decimal(0)
        )

        # Calculate duration
        try:
            entry_dt = datetime.fromisoformat(entry_time)
            duration = (now - entry_dt).total_seconds()
        except (ValueError, TypeError):
            duration = 0.0

        event = LossEvent(
            position_id=position_id,
            strategy_id=strategy_id,
            asset=asset,
            entry_price=entry_price,
            exit_price=exit_price,
            loss_pct=loss_pct,
            duration_seconds=duration,
            timestamp=now.isoformat(),
        )
        self._loss_events.append(event)

        # Start cooldown for this strategy
        cooldown_until = now + timedelta(hours=self._cooldown_hours)
        self._cooldowns[strategy_id] = cooldown_until

        _logger.warning(
            "Position closed — loss limit breached",
            extra={"data": event.to_dict()},
        )
        _logger.info(
            "Strategy cooldown started",
            extra={"data": {
                "strategy_id": strategy_id,
                "cooldown_until": cooldown_until.isoformat(),
                "cooldown_hours": self._cooldown_hours,
            }},
        )

        return event

    def is_strategy_in_cooldown(
        self, strategy_id: str, now: datetime | None = None,
    ) -> bool:
        """Check if a strategy is in cooldown period."""
        cooldown_until = self._cooldowns.get(strategy_id)
        if cooldown_until is None:
            return False
        current = now or datetime.now(UTC)
        return current < cooldown_until

    def get_cooldown_remaining(
        self, strategy_id: str, now: datetime | None = None,
    ) -> timedelta | None:
        """Get remaining cooldown time for a strategy.

        Returns None if not in cooldown.
        """
        cooldown_until = self._cooldowns.get(strategy_id)
        if cooldown_until is None:
            return None
        current = now or datetime.now(UTC)
        remaining = cooldown_until - current
        if remaining.total_seconds() <= 0:
            return None
        return remaining

    def can_open_position(
        self, strategy_id: str, now: datetime | None = None,
    ) -> bool:
        """Check if a strategy can open new positions (not in cooldown)."""
        return not self.is_strategy_in_cooldown(strategy_id, now)

    def check_all_positions(
        self,
        positions: list[dict[str, Any]],
        price_map: dict[str, Decimal],
    ) -> list[PositionCheck]:
        """Check all positions against loss limit.

        *positions*: list of dicts with id, asset, entry_price fields.
        *price_map*: maps asset names to current prices.

        Returns list of PositionCheck results for positions that
        should be closed.
        """
        results: list[PositionCheck] = []
        for pos in positions:
            asset = pos.get("asset", "")
            current_price = price_map.get(asset)
            if current_price is None:
                continue

            check = self.check_position(
                position_id=pos.get("id", "unknown"),
                entry_price=Decimal(str(pos.get("entry_price", 0))),
                current_price=current_price,
            )
            if check.should_close:
                results.append(check)

        return results
