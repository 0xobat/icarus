# Managed Portfolio P1.5c Task 4B — DB-Backed Trade Log

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development or superpowers:executing-plans. Checkbox steps.

**Goal:** Persist an append-only trade log to the existing `Trade` table using the **pending-on-publish + update-on-result** pattern: when a rebalance publishes, write a `Trade(status="pending")` with the order-side fields; when its `ExecutionResult` arrives, update the row (status, tx_hash, amount_out, fill price). Reuses `icarus.db.models.Trade` — no new table. The trade log is **history/audit, not correctness** (balance-truth-from-chain owns NAV), so every DB write is best-effort and never blocks trading.

**Architecture:** (1) extend `ManagedCycleResult` with the published order's details; (2) new `trade_log.py` with `record_pending_trade(db, ...)` + `make_db_trade_sink(db)`; (3) wire into `ManagedEngine` (pending write on publish) and `__main__` (the DB sink on the consumer).

**Tech Stack:** Python 3.13, `uv`, `pytest`, SQLAlchemy (sync `DatabaseManager`), `Decimal`.

---

## Contracts (verified)
- `DatabaseManager(DatabaseConfig(url="sqlite:///..."))`; `.create_tables()`; `.get_session() -> Session` used in a `with` block; caller commits. `Trade` (`icarus.db.models`): non-nullable `trade_id, correlation_id, strategy, protocol, chain, action, asset_in, amount_in, status`; nullable `asset_out, amount_out, price_at_execution, tx_hash, error_message, ...`. `status` default `"pending"`.
- `ResultsConsumer` projection dict keys (P1.5c-2): `order_id, correlation_id, chain, status, tx_hash, amount_out (str|None), fill_price (str|None), timestamp`.
- `ManagedCycleResult` (P1.4): frozen `(action, reason, published, correlation_id)`. `ManagedPortfolioCycle.run_one` builds the order via `_build_order(plan, ...)`; `plan` has `from_symbol/to_symbol/usd_amount`; the order has `order_id`.
- `ManagedEngine` (4C): `_tick` calls `result = await self._cycle.run_one()`.

---

## Task 1: Extend `ManagedCycleResult` with the published order's details

**Files:** Modify `managed_cycle.py`; Modify `test_managed_cycle_unit.py`.

- [ ] **Step 1: Failing test** — append to `test_managed_cycle_unit.py`:

```python
@pytest.mark.asyncio
async def test_result_carries_order_details_on_rebalance() -> None:
    publisher = _CapturePublisher()
    cycle = _cycle(_StubHoldings(Decimal("8000"), Decimal("2000")), publisher)
    result = await cycle.run_one()
    assert result.published is True
    assert result.order_id is not None and len(result.order_id) >= 8
    assert result.from_symbol == "WETH"
    assert result.to_symbol == "USDC"
    assert result.usd_amount == Decimal("2000")


@pytest.mark.asyncio
async def test_hold_result_has_no_order_details() -> None:
    publisher = _CapturePublisher()
    cycle = _cycle(_StubHoldings(Decimal("6500"), Decimal("3500")), publisher)
    result = await cycle.run_one()
    assert result.action == "hold"
    assert result.order_id is None
    assert result.from_symbol is None
    assert result.usd_amount is None
```

- [ ] **Step 2: Run → fails** (AttributeError on `result.order_id`).

- [ ] **Step 3: Implement** — in `managed_cycle.py`:
  - Add optional fields to `ManagedCycleResult` (after `correlation_id`):
    ```python
    order_id: str | None = None
    from_symbol: str | None = None
    to_symbol: str | None = None
    usd_amount: Decimal | None = None
    ```
  - In `run_one`, the **rebalance/published** return (and the gate-dropped return) must populate these from `plan` + `order`. The successful-publish path becomes:
    ```python
    return ManagedCycleResult(
        action="rebalance", reason=plan.reason, published=True,
        correlation_id=correlation_id, order_id=order.order_id,
        from_symbol=plan.from_symbol, to_symbol=plan.to_symbol,
        usd_amount=plan.usd_amount,
    )
    ```
    (The hold return and the gate-dropped return leave the new fields at their `None` defaults — the dropped path may optionally set them, but published=False so the recorder skips it.)

- [ ] **Step 4: Run → pass.** **Step 5: Commit** `feat(daedalus): P1.5c-4B ManagedCycleResult carries published order details`.

---

## Task 2: `trade_log.py` — pending writer + update sink

**Files:** Create `decision-engine/src/decision_engine/trade_log.py`; Test `decision-engine/tests/test_trade_log_unit.py`.

- [ ] **Step 1: Failing test** — `decision-engine/tests/test_trade_log_unit.py`:

```python
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
```

- [ ] **Step 2: Run → fails** (ModuleNotFoundError).

- [ ] **Step 3: Implement** — `decision-engine/src/decision_engine/trade_log.py`:

```python
"""DB-backed trade log — pending-on-publish + update-on-result.

Managed-portfolio P1.5c-4B. Append-only audit/history of rebalance swaps in the
existing `Trade` table. Balance-truth-from-chain owns NAV; this log is history,
so writes are best-effort and never block trading (the results consumer already
swallows sink exceptions).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Callable

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


__all__ = ["record_pending_trade", "make_db_trade_sink"]
```

- [ ] **Step 4: Run → 3 pass.** **Step 5: Commit** `feat(daedalus): P1.5c-4B trade_log — pending writer + update sink`.

---

## Task 3: Wire into `ManagedEngine` + `__main__`

**Files:** Modify `__main__.py`; Modify `test_managed_engine_unit.py`.

- [ ] **Step 1: Failing test** — append to `test_managed_engine_unit.py` (uses a real SQLite DB to assert a pending row is written when a rebalance publishes):

```python
import pytest
from decimal import Decimal
from pathlib import Path

from icarus.db.database import DatabaseConfig, DatabaseManager
from icarus.db.models import Trade
from sqlalchemy import select

from decision_engine.managed_cycle import ManagedCycleResult


class _PublishingCycle:
    async def run_one(self) -> ManagedCycleResult:
        return ManagedCycleResult(
            action="rebalance", reason="t", published=True, correlation_id="c",
            order_id="ord9", from_symbol="WETH", to_symbol="USDC",
            usd_amount=Decimal("2000"),
        )


@pytest.mark.asyncio
async def test_tick_records_pending_trade_on_publish(tmp_path: Path) -> None:
    db = DatabaseManager(DatabaseConfig(url=f"sqlite:///{tmp_path}/e.db"))
    db.create_tables()
    from decision_engine.__main__ import _make_pending_recorder

    engine = ManagedEngine(
        cycle=_PublishingCycle(), holdings=_StubHoldings(), adapter=_FakeAdapter(),
        drawdown=DrawdownBreaker(), gas_spike=GasSpikeBreaker(),
        gas_tracker=GasAverageTracker(), chain="base",
        pending_trade_recorder=_make_pending_recorder(db, chain="base", protocol="aerodrome", slippage_bps=50),
    )
    await engine._tick()
    with db.get_session() as s:
        rows = s.execute(select(Trade)).scalars().all()
    assert len(rows) == 1 and rows[0].trade_id == "ord9" and rows[0].status == "pending"
```

- [ ] **Step 2: Run → fails** (no `pending_trade_recorder` param / no `_make_pending_recorder`).

- [ ] **Step 3: Implement** —
  - `ManagedEngine.__init__`: add `pending_trade_recorder: Callable[[Any], None] | None = None`; store it.
  - In `_tick`, after `result = await self._cycle.run_one()`:
    ```python
    if result.published and self._pending_trade_recorder is not None:
        try:
            self._pending_trade_recorder(result)
        except Exception:
            logger.warning("pending_trade_record_failed", order_id=result.order_id, exc_info=True)
    ```
  - Add a module-level helper in `__main__.py`:
    ```python
    def _make_pending_recorder(db, *, chain: str, protocol: str, slippage_bps: int):
        from decision_engine.trade_log import record_pending_trade
        def _record(result) -> None:
            record_pending_trade(
                db, order_id=result.order_id, correlation_id=result.correlation_id,
                chain=chain, protocol=protocol, from_symbol=result.from_symbol,
                to_symbol=result.to_symbol, usd_amount=result.usd_amount,
                slippage_bps=slippage_bps,
            )
        return _record
    ```
  - In `_amain`: build `trade_sink = make_db_trade_sink(db)` and pass it to `ResultsConsumer(tx_failure=tx_failure, trade_sink=trade_sink)`; build `pending_recorder = _make_pending_recorder(db, chain=config.chain, protocol="aerodrome", slippage_bps=config.slippage_bps)` and pass `pending_trade_recorder=pending_recorder` to `ManagedEngine`. (Imports: `from decision_engine.trade_log import make_db_trade_sink`.)

- [ ] **Step 4: Run → pass.** **Step 5: Full suite + ruff:** `uv run pytest decision-engine/tests -q` (no regressions) and `uv run ruff check decision-engine/`. **Step 6: Commit** `feat(daedalus): P1.5c-4B wire trade log into ManagedEngine + __main__`.

---

## Self-Review
- Pending-on-publish (ManagedEngine) + update-on-result (consumer sink) fully populate `Trade`; reuses the model.
- All DB writes best-effort: pending write wrapped in try/except in `_tick`; the sink already swallowed by the consumer (P1.5c-2).
- Missing-pending-row update is a logged no-op (can't insert — non-null order-side columns).
- `trade_sink=None` no longer the case in `__main__`; the consumer now persists. Tests cover insert, update, missing-row, result-detail propagation, and the tick→pending write.

## Handoff
After 4B: P1's code is fully complete (trade log included). Only the operator live run remains. Lake-module cleanup is the next non-P1 phase.
