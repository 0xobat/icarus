"""DB-backed trade log — pending-on-publish + update-on-result.

Managed-portfolio P1.5c-4B. Append-only audit/history of rebalance swaps in the
existing `Trade` table. Balance-truth-from-chain owns NAV; this log is history,
so writes are best-effort and never block trading (the results consumer already
swallows sink exceptions).
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from typing import Any

import structlog
from icarus.db.database import DatabaseManager
from icarus.db.models import Trade
from sqlalchemy import select

logger = structlog.get_logger(service="decision-engine.trade_log")


def record_pending_trade(
    db: DatabaseManager,
    *,
    order_id: str,
    correlation_id: str,
    chain: str,
    protocol: str,
    from_symbol: str,
    to_symbol: str,
    usd_amount: Decimal,
    slippage_bps: int,
) -> None:
    """Insert a Trade(status="pending") for a just-published rebalance swap."""
    with db.get_session() as session:
        session.add(
            Trade(
                trade_id=order_id,
                correlation_id=correlation_id,
                strategy=f"REBAL:{chain}",
                protocol=protocol,
                chain=chain,
                action="swap",
                asset_in=from_symbol,
                asset_out=to_symbol,
                amount_in=usd_amount,  # USD notional of the swap (audit)
                slippage_bps=slippage_bps,
                status="pending",
            )
        )
        session.commit()


def make_db_trade_sink(db: DatabaseManager) -> Callable[[dict[str, Any]], None]:
    """Return a trade_sink that updates the pending Trade row on a result.

    Matched by trade_id == order_id. If no pending row exists (the pending write
    was lost, or the order wasn't recorded), it logs and no-ops — a result-only
    insert can't satisfy the Trade table's non-null order-side columns.
    """

    def _sink(rec: dict[str, Any]) -> None:
        with db.get_session() as session:
            row = session.execute(
                select(Trade).where(Trade.trade_id == rec["order_id"])
            ).scalar_one_or_none()
            if row is None:
                logger.warning("trade_update_no_pending_row", order_id=rec["order_id"])
                return
            row.status = rec["status"]
            row.tx_hash = rec.get("tx_hash")
            if rec.get("amount_out") is not None:
                row.amount_out = Decimal(rec["amount_out"])
            if rec.get("fill_price") is not None:
                row.price_at_execution = Decimal(rec["fill_price"])
            session.commit()

    return _sink


__all__ = ["make_db_trade_sink", "record_pending_trade"]
