"""Position tracker — lifecycle management for all open and closed positions.

Positions are kept in-memory for fast access and synced to PostgreSQL
via ``DatabaseRepository`` for persistence across restarts.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from monitoring.logger import get_logger

if TYPE_CHECKING:
    from db.repository import DatabaseRepository

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
        """Return dictionary representation."""
        d = asdict(self)
        # Convert Decimals to str for JSON serialization
        for k, v in d.items():
            if isinstance(v, Decimal):
                d[k] = str(v)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Position:
        """Construct a Position from a dictionary."""
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

    Positions are stored in-memory for fast access and synced to PostgreSQL
    via ``DatabaseRepository`` for persistence across restarts.

    Args:
        repository: Optional database repository for PostgreSQL persistence.
            When provided, all position changes are synced to the database.
    """

    def __init__(self, repository: DatabaseRepository | None = None) -> None:
        self._open: dict[str, Position] = {}
        self._closed: list[Position] = []
        self._repository = repository

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
        """Create and track a new position.

        Args:
            strategy: Strategy identifier (e.g. ``LEND-001``).
            protocol: Protocol name (e.g. ``aave_v3``).
            chain: Blockchain (e.g. ``base``).
            asset: Asset symbol (e.g. ``USDC``).
            entry_price: Price at entry.
            amount: Position size.
            protocol_data: Protocol-specific metadata.
            position_id: Optional custom ID; auto-generated if omitted.

        Returns:
            The newly created Position.
        """
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
        self._sync_position_to_db(pos)
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

        Args:
            position_id: ID of the position to close.
            exit_price: Price at exit. Uses current_value if omitted.

        Returns:
            The closed Position, or None if not found.
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
        self._sync_position_to_db(pos)
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

        Args:
            price_map: Maps asset names to current prices.
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

        Updates position state based on TX success or failure. On confirmed
        close, closes the position. On confirmed open, updates the fill price.

        Args:
            result: Execution result dict with ``position_id``, ``status``,
                ``action``, and optionally ``fill_price``.
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
                self._sync_position_to_db(pos)

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
        status: str | None = None,
        strategy: str | None = None,
        protocol: str | None = None,
        chain: str | None = None,
        asset: str | None = None,
        include_closed: bool = False,
    ) -> list[Position]:
        """Filter positions by any combination of fields.

        Args:
            status: Filter by position status (``open`` or ``closed``).
            strategy: Filter by strategy name.
            protocol: Filter by protocol.
            chain: Filter by blockchain.
            asset: Filter by asset symbol.
            include_closed: If True, include closed positions in results.
                Ignored when ``status`` is explicitly set.

        Returns:
            List of matching Position objects.
        """
        if status == "open":
            positions: list[Position] = list(self._open.values())
        elif status == "closed":
            positions = list(self._closed)
        elif include_closed:
            positions = list(self._open.values()) + list(self._closed)
        else:
            positions = list(self._open.values())

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
    # Summary / prompt context
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

    def get_position_summary(self) -> dict[str, Any]:
        """Assemble position data for Claude's prompt context.

        Returns a structured dict with open positions grouped by protocol,
        aggregate P&L, and allocation breakdown — everything Claude needs
        to reason about portfolio state.

        Returns:
            Dictionary with ``positions``, ``by_protocol``, ``by_strategy``,
            and ``totals`` keys.
        """
        positions_list = []
        by_protocol: dict[str, list[dict[str, Any]]] = {}
        by_strategy: dict[str, list[dict[str, Any]]] = {}

        for pos in self._open.values():
            pos_dict = {
                "id": pos.id,
                "strategy": pos.strategy,
                "protocol": pos.protocol,
                "chain": pos.chain,
                "asset": pos.asset,
                "amount": str(pos.amount),
                "entry_price": str(pos.entry_price),
                "current_value": str(pos.current_value),
                "unrealized_pnl": str(pos.unrealized_pnl),
                "entry_time": pos.entry_time,
            }
            positions_list.append(pos_dict)
            by_protocol.setdefault(pos.protocol, []).append(pos_dict)
            by_strategy.setdefault(pos.strategy, []).append(pos_dict)

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
            "positions": positions_list,
            "by_protocol": {
                proto: {
                    "count": len(positions),
                    "total_value": str(sum(
                        Decimal(p["current_value"]) for p in positions
                    )),
                    "positions": positions,
                }
                for proto, positions in by_protocol.items()
            },
            "by_strategy": {
                strat: {
                    "count": len(positions),
                    "total_value": str(sum(
                        Decimal(p["current_value"]) for p in positions
                    )),
                }
                for strat, positions in by_strategy.items()
            },
            "totals": {
                "open_positions": len(self._open),
                "closed_positions": len(self._closed),
                "total_value": str(total_value),
                "total_unrealized_pnl": str(total_unrealized),
                "total_realized_pnl": str(total_realized),
            },
        }

    # ------------------------------------------------------------------
    # PostgreSQL persistence
    # ------------------------------------------------------------------

    def _sync_position_to_db(self, pos: Position) -> None:
        """Sync a single position to PostgreSQL via the repository.

        Args:
            pos: The position to persist.
        """
        if self._repository is None:
            return

        try:
            close_time = None
            if pos.close_time is not None:
                close_time = datetime.fromisoformat(pos.close_time)

            self._repository.save_position({
                "position_id": pos.id,
                "strategy": pos.strategy,
                "protocol": pos.protocol,
                "chain": pos.chain,
                "asset": pos.asset,
                "entry_price": pos.entry_price,
                "entry_time": datetime.fromisoformat(pos.entry_time),
                "amount": pos.amount,
                "current_value": pos.current_value,
                "unrealized_pnl": pos.unrealized_pnl,
                "realized_pnl": pos.realized_pnl,
                "status": pos.status,
                "close_time": close_time,
                "protocol_data": pos.protocol_data or None,
            })
        except Exception:
            _logger.exception(
                "Failed to sync position to database",
                extra={"data": {"position_id": pos.id}},
            )

    def sync_all_to_db(self) -> None:
        """Sync all in-memory positions to PostgreSQL.

        Persists every open and recently closed position. Called during
        periodic checkpoints or before shutdown.
        """
        if self._repository is None:
            _logger.debug("No repository configured — skipping sync")
            return

        count = 0
        for pos in self._open.values():
            self._sync_position_to_db(pos)
            count += 1
        for pos in self._closed:
            self._sync_position_to_db(pos)
            count += 1

        _logger.info(
            "All positions synced to database",
            extra={"data": {"synced_count": count}},
        )

    @classmethod
    def from_database(cls, repository: DatabaseRepository) -> PositionTracker:
        """Load positions from PostgreSQL into a new tracker.

        Queries all positions from the database and populates the in-memory
        cache. Used during startup recovery.

        Args:
            repository: Database repository to load from.

        Returns:
            A new PositionTracker with positions loaded from the database.
        """
        tracker = cls(repository=repository)

        open_rows = repository.get_positions(status="open")
        for row in open_rows:
            pos = Position(
                id=row.position_id,
                strategy=row.strategy,
                protocol=row.protocol,
                chain=row.chain,
                asset=row.asset,
                entry_price=Decimal(str(row.entry_price)),
                entry_time=row.entry_time.isoformat() if row.entry_time else "",
                amount=Decimal(str(row.amount)),
                current_value=Decimal(str(row.current_value)),
                unrealized_pnl=Decimal(str(row.unrealized_pnl)),
                status="open",
                protocol_data={},
            )
            tracker._open[pos.id] = pos

        closed_rows = repository.get_positions(status="closed")
        for row in closed_rows:
            pos = Position(
                id=row.position_id,
                strategy=row.strategy,
                protocol=row.protocol,
                chain=row.chain,
                asset=row.asset,
                entry_price=Decimal(str(row.entry_price)),
                entry_time=row.entry_time.isoformat() if row.entry_time else "",
                amount=Decimal(str(row.amount)),
                current_value=Decimal(str(row.current_value)),
                unrealized_pnl=Decimal(str(row.unrealized_pnl)),
                realized_pnl=(
                    Decimal(str(row.realized_pnl))
                    if row.realized_pnl is not None else None
                ),
                status="closed",
                close_time=(
                    row.close_time.isoformat() if row.close_time else None
                ),
                protocol_data={},
            )
            tracker._closed.append(pos)

        _logger.info(
            "Positions loaded from database",
            extra={"data": {
                "open": len(tracker._open),
                "closed": len(tracker._closed),
            }},
        )
        return tracker

    # ------------------------------------------------------------------
    # Legacy persistence helpers (for StateManager compatibility)
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
