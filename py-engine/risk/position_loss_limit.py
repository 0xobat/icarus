"""Per-position loss limit — individual position stop-loss with cooldown (RISK-002).

Every position has tracked entry value. At >10% loss: close position,
strategy enters 24h cooldown. Cooldown enforced — strategy cannot open
new positions until expiry.

Direct emission: generates CB:position_loss orders to close affected
positions, bypassing the decision gate and Claude API.

Cooldowns stored as Redis TTL keys when a Redis client is provided,
falling back to in-memory dict for testing.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from monitoring.logger import get_logger

if TYPE_CHECKING:
    from data.redis_client import RedisManager

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
        """Return dictionary representation."""
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
    - Direct emission of CB:position_loss orders (bypasses decision gate)
    - Redis TTL-based cooldowns when redis client provided (survives restarts)
    """

    def __init__(
        self,
        *,
        loss_threshold: Decimal = DEFAULT_LOSS_THRESHOLD,
        cooldown_hours: int = DEFAULT_COOLDOWN_HOURS,
        redis: RedisManager | None = None,
    ) -> None:
        self._loss_threshold = loss_threshold
        self._cooldown_hours = cooldown_hours
        self._redis = redis
        self._cooldowns: dict[str, datetime] = {}
        self._loss_events: list[LossEvent] = []

    @property
    def loss_threshold(self) -> Decimal:
        """Return the loss percentage threshold that triggers a close."""
        return self._loss_threshold

    @property
    def cooldown_hours(self) -> int:
        """Return the cooldown duration in hours after a loss event."""
        return self._cooldown_hours

    @property
    def loss_events(self) -> list[LossEvent]:
        """Return a copy of all recorded loss events."""
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
        """Record a loss event and start strategy cooldown.

        When a Redis client is configured, sets a TTL key
        ``cooldown:{strategy_id}`` with EX = cooldown_hours * 3600
        so cooldowns survive restarts.
        """
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

        # Start in-memory cooldown for this strategy
        cooldown_until = now + timedelta(hours=self._cooldown_hours)
        self._cooldowns[strategy_id] = cooldown_until

        # Set Redis TTL key if redis client available
        if self._redis is not None:
            try:
                client = self._redis.client
                ttl_seconds = self._cooldown_hours * 3600
                client.set(
                    f"cooldown:{strategy_id}",
                    now.isoformat(),
                    ex=ttl_seconds,
                )
                _logger.info(
                    "Redis cooldown key set",
                    extra={"data": {
                        "key": f"cooldown:{strategy_id}",
                        "ttl_seconds": ttl_seconds,
                    }},
                )
            except Exception:
                _logger.warning(
                    "Failed to set Redis cooldown key — using in-memory only",
                    extra={"data": {"strategy_id": strategy_id}},
                )

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
        """Check if a strategy is in cooldown period.

        When Redis is configured, checks the TTL key first. Falls
        back to the in-memory dict.
        """
        # Check Redis TTL key first if available
        if self._redis is not None:
            try:
                client = self._redis.client
                if client.exists(f"cooldown:{strategy_id}"):
                    return True
            except Exception:
                pass  # Fall through to in-memory check

        # In-memory fallback
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

    def generate_close_orders(
        self,
        *,
        positions: list[dict[str, Any]],
        price_map: dict[str, Decimal],
        correlation_id: str,
    ) -> list[dict[str, Any]]:
        """Generate CB:position_loss orders for positions exceeding loss limit.

        Checks all positions, records loss events for breached ones, and
        returns schema-compliant execution orders for direct emission to
        Redis (bypassing the decision gate and Claude API).

        Args:
            positions: List of position dicts with id, asset, entry_price,
                protocol, strategy_id, entry_time, current_value fields.
            price_map: Maps asset names to current prices.
            correlation_id: Correlation ID for tracing.

        Returns:
            List of execution-orders-schema-compliant order dicts.
        """
        checks = self.check_all_positions(positions, price_map)
        if not checks:
            return []

        # Build lookup by position_id for enrichment
        pos_lookup: dict[str, dict[str, Any]] = {}
        for pos in positions:
            pos_lookup[pos.get("id", "unknown")] = pos

        orders: list[dict[str, Any]] = []
        for check in checks:
            pos = pos_lookup.get(check.position_id, {})
            asset = pos.get("asset", "unknown")
            strategy_id = pos.get("strategy_id", "unknown")
            entry_price = Decimal(str(pos.get("entry_price", 0)))
            current_price = price_map.get(asset, Decimal(0))
            protocol = pos.get("protocol", "aave_v3")
            entry_time = pos.get("entry_time", datetime.now(UTC).isoformat())
            position_value = str(pos.get("current_value", pos.get("amount", "0")))

            # Record the loss event and start cooldown
            self.record_loss_event(
                position_id=check.position_id,
                strategy_id=strategy_id,
                asset=asset,
                entry_price=entry_price,
                exit_price=current_price,
                entry_time=entry_time,
            )

            # Generate schema-compliant order
            order = {
                "version": "1.0.0",
                "orderId": uuid.uuid4().hex,
                "correlationId": correlation_id,
                "timestamp": datetime.now(UTC).isoformat(),
                "chain": "base",
                "protocol": protocol,
                "action": "withdraw",
                "strategy": "CB:position_loss",
                "priority": "urgent",
                "params": {
                    "tokenIn": asset,
                    "amount": position_value,
                },
                "limits": {
                    "maxGasWei": "500000000000000",
                    "maxSlippageBps": 50,
                    "deadlineUnix": int(time.time()) + 300,
                },
            }
            orders.append(order)

            _logger.warning(
                "CB:position_loss order generated",
                extra={"data": {
                    "position_id": check.position_id,
                    "strategy_id": strategy_id,
                    "loss_pct": str(check.loss_pct),
                    "orderId": order["orderId"],
                }},
            )

        return orders
