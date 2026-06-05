"""Unit tests for the DB-backed trade log (managed-portfolio P1.5c-4B)."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from icarus.db.database import DatabaseConfig, DatabaseManager
from icarus.db.models import Trade
from sqlalchemy import select

from decision_engine.trade_log import make_db_trade_sink, record_pending_trade


@pytest.fixture
def db(tmp_path: Path) -> DatabaseManager:
    mgr = DatabaseManager(DatabaseConfig(url=f"sqlite:///{tmp_path}/t.db"))
    mgr.create_tables()
    return mgr


def _pending(db: DatabaseManager, order_id: str = "ord1") -> None:
    record_pending_trade(
        db, order_id=order_id, correlation_id="corr1", chain="base",
        protocol="aerodrome", from_symbol="WETH", to_symbol="USDC",
        usd_amount=Decimal("2000"), slippage_bps=50,
    )


def _row(db: DatabaseManager, order_id: str = "ord1") -> Trade:
    with db.get_session() as s:
        return s.execute(select(Trade).where(Trade.trade_id == order_id)).scalar_one()


def test_record_pending_inserts_a_pending_row(db: DatabaseManager) -> None:
    _pending(db)
    row = _row(db)
    assert row.status == "pending"
    assert row.strategy == "REBAL:base"
    assert row.protocol == "aerodrome"
    assert row.action == "swap"
    assert row.asset_in == "WETH"
    assert row.asset_out == "USDC"
    assert Decimal(str(row.amount_in)) == Decimal("2000")
    assert row.slippage_bps == 50


def test_sink_updates_pending_to_confirmed(db: DatabaseManager) -> None:
    _pending(db)
    sink = make_db_trade_sink(db)
    sink({
        "order_id": "ord1", "correlation_id": "corr1", "chain": "base",
        "status": "confirmed", "tx_hash": "0xabc",
        "amount_out": "1990000000000000000", "fill_price": "3000",
        "timestamp": "2026-06-03T00:00:00+00:00",
    })
    row = _row(db)
    assert row.status == "confirmed"
    assert row.tx_hash == "0xabc"
    assert Decimal(str(row.amount_out)) == Decimal("1990000000000000000")
    assert Decimal(str(row.price_at_execution)) == Decimal("3000")


def test_sink_missing_pending_row_is_noop(db: DatabaseManager) -> None:
    sink = make_db_trade_sink(db)
    # No pending row for ord_missing → must not raise, must not insert.
    sink({
        "order_id": "ord_missing", "correlation_id": "c", "chain": "base",
        "status": "confirmed", "tx_hash": "0x", "amount_out": None,
        "fill_price": None, "timestamp": "2026-06-03T00:00:00+00:00",
    })
    with db.get_session() as s:
        assert s.execute(select(Trade)).scalars().all() == []
