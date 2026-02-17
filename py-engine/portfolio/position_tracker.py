"""Position tracker — lifecycle management for all open and closed positions."""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from monitoring.logger import get_logger

_logger = get_logger("position-tracker", enable_file=False)


# ---------------------------------------------------------------------------
# Position dataclass
# ---------------------------------------------------------------------------
@dataclass
class Position:
    """A single tracked position across any protocol."""

    id: str
    strategy: str
    protocol: str
    chain: str
    asset: str
    entry_price: Decimal
    entry_time: str
    amount: Decimal
    current_value: Decimal
    unrealized_pnl: Decimal = Decimal(0)
    status: str = "open"  # open | closed
    realized_pnl: Decimal | None = None
    close_time: str | None = None
    protocol_data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # Convert Decimals to str for JSON serialization
        for k, v in d.items():
            if isinstance(v, Decimal):
                d[k] = str(v)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Position:
        decimal_fields = {
            "entry_price", "amount", "current_value",
            "unrealized_pnl", "realized_pnl",
        }
        converted = {}
        for k, v in d.items():
            if k in decimal_fields and v is not None:
                converted[k] = Decimal(str(v))
            else:
                converted[k] = v
        return cls(**converted)


# ---------------------------------------------------------------------------
# Position tracker
# ---------------------------------------------------------------------------
class PositionTracker:
    """Tracks all open and closed positions with P&L calculation.

    Positions are stored in-memory and can be persisted via
    ``to_state_dict()`` / ``from_state_dict()`` for integration with
    StateManager (HARNESS-001).
    """

    def __init__(self) -> None:
        self._open: dict[str, Position] = {}
        self._closed: list[Position] = []

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def open_position(
        self,
        *,
        strategy: str,
        protocol: str,
        chain: str,
        asset: str,
        entry_price: Decimal | float | str,
        amount: Decimal | float | str,
        protocol_data: dict[str, Any] | None = None,
        position_id: str | None = None,
    ) -> Position:
        """Create and track a new position."""
        ep = Decimal(str(entry_price))
        amt = Decimal(str(amount))
        current_value = ep * amt

        pos = Position(
            id=position_id or uuid.uuid4().hex[:12],
            strategy=strategy,
            protocol=protocol,
            chain=chain,
            asset=asset,
            entry_price=ep,
            entry_time=datetime.now(UTC).isoformat(),
            amount=amt,
            current_value=current_value,
            unrealized_pnl=Decimal(0),
            protocol_data=protocol_data or {},
        )
        self._open[pos.id] = pos
        _logger.info(
            "Position opened",
            extra={"data": {
                "position_id": pos.id, "strategy": strategy,
                "protocol": protocol, "asset": asset,
                "entry_price": str(ep), "amount": str(amt),
            }},
        )
        return pos

    def close_position(
        self,
        position_id: str,
        exit_price: Decimal | float | str | None = None,
    ) -> Position | None:
        """Close a position and calculate realized P&L.

        If *exit_price* is provided, realized P&L is calculated from it.
        Otherwise, the current_value is used as the exit value.
        """
        pos = self._open.pop(position_id, None)
        if pos is None:
            _logger.warning(
                "Close requested for unknown position",
                extra={"data": {"position_id": position_id}},
            )
            return None

        pos.status = "closed"
        pos.close_time = datetime.now(UTC).isoformat()

        if exit_price is not None:
            ep = Decimal(str(exit_price))
            exit_value = ep * pos.amount
        else:
            exit_value = pos.current_value

        entry_value = pos.entry_price * pos.amount
        pos.realized_pnl = exit_value - entry_value
        pos.unrealized_pnl = Decimal(0)
        pos.current_value = exit_value

        self._closed.append(pos)
        _logger.info(
            "Position closed",
            extra={"data": {
                "position_id": pos.id, "realized_pnl": str(pos.realized_pnl),
            }},
        )
        return pos

    # ------------------------------------------------------------------
    # Price updates
    # ------------------------------------------------------------------

    def update_prices(self, price_map: dict[str, Decimal | float | str]) -> None:
        """Update current_value and unrealized_pnl for all open positions.

        *price_map* maps asset names to current prices.
        """
        for pos in self._open.values():
            if pos.asset in price_map:
                current_price = Decimal(str(price_map[pos.asset]))
                pos.current_value = current_price * pos.amount
                entry_value = pos.entry_price * pos.amount
                pos.unrealized_pnl = pos.current_value - entry_value

    # ------------------------------------------------------------------
    # Execution result handler
    # ------------------------------------------------------------------

    def on_execution_result(self, result: dict[str, Any]) -> None:
        """Handle an execution:results message from Redis.

        Expected fields: ``position_id``, ``status`` (confirmed|failed),
        optionally ``fill_price``, ``action`` (open|close).
        """
        pos_id = result.get("position_id", "")
        status = result.get("status", "")
        action = result.get("action", "")

        if status == "failed":
            _logger.warning(
                "Execution failed",
                extra={"data": {"position_id": pos_id, "reason": result.get("reason")}},
            )
            return

        if action == "close" and pos_id in self._open:
            self.close_position(pos_id, exit_price=result.get("fill_price"))
        elif action == "open":
            # Update entry_price with actual fill price if available
            fill_price = result.get("fill_price")
            if fill_price is not None and pos_id in self._open:
                pos = self._open[pos_id]
                pos.entry_price = Decimal(str(fill_price))
                pos.current_value = pos.entry_price * pos.amount
                pos.unrealized_pnl = Decimal(0)

        _logger.debug(
            "Execution result processed",
            extra={"data": {"position_id": pos_id, "action": action, "status": status}},
        )

    # ------------------------------------------------------------------
    # Querying
    # ------------------------------------------------------------------

    def query(
        self,
        *,
        strategy: str | None = None,
        protocol: str | None = None,
        chain: str | None = None,
        asset: str | None = None,
        include_closed: bool = False,
    ) -> list[Position]:
        """Filter positions by any combination of fields."""
        positions: list[Position] = list(self._open.values())
        if include_closed:
            positions.extend(self._closed)

        if strategy is not None:
            positions = [p for p in positions if p.strategy == strategy]
        if protocol is not None:
            positions = [p for p in positions if p.protocol == protocol]
        if chain is not None:
            positions = [p for p in positions if p.chain == chain]
        if asset is not None:
            positions = [p for p in positions if p.asset == asset]
        return positions

    def get_position(self, position_id: str) -> Position | None:
        """Get a single open position by ID."""
        return self._open.get(position_id)

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    def get_summary(self) -> dict[str, Any]:
        """Total value and unrealized P&L across all open positions."""
        total_value = sum(
            (p.current_value for p in self._open.values()), Decimal(0),
        )
        total_unrealized = sum(
            (p.unrealized_pnl for p in self._open.values()), Decimal(0),
        )
        total_realized = sum(
            (p.realized_pnl for p in self._closed if p.realized_pnl is not None),
            Decimal(0),
        )
        return {
            "open_count": len(self._open),
            "closed_count": len(self._closed),
            "total_value": str(total_value),
            "total_unrealized_pnl": str(total_unrealized),
            "total_realized_pnl": str(total_realized),
        }

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------

    def to_state_dict(self) -> dict[str, Any]:
        """Serialize for StateManager."""
        return {
            "open": {pid: p.to_dict() for pid, p in self._open.items()},
            "closed": [p.to_dict() for p in self._closed],
        }

    @classmethod
    def from_state_dict(cls, data: dict[str, Any]) -> PositionTracker:
        """Restore from StateManager data."""
        tracker = cls()
        for pid, pdata in data.get("open", {}).items():
            tracker._open[pid] = Position.from_dict(pdata)
        for pdata in data.get("closed", []):
            tracker._closed.append(Position.from_dict(pdata))
        return tracker

    def backup_to_postgres(self) -> None:
        """Stub: log that a PostgreSQL backup would happen.

        Real implementation will be added with INFRA-006.
        """
        _logger.info(
            "PostgreSQL position backup stub",
            extra={"data": {
                "action": "backup_positions",
                "open_count": len(self._open),
                "closed_count": len(self._closed),
                "note": "stub — real implementation with INFRA-006",
            }},
        )
