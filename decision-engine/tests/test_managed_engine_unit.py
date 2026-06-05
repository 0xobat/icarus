"""Smoke test for the managed worker loop (P1.5c Task 4C)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from decision_engine.__main__ import ManagedEngine, _make_pending_recorder
from decision_engine.gas_tracker import GasAverageTracker
from decision_engine.managed_cycle import ManagedCycleResult
from decision_engine.risk.drawdown_breaker import DrawdownBreaker
from decision_engine.risk.gas_spike_breaker import GasSpikeBreaker
from icarus.db.database import DatabaseConfig, DatabaseManager
from icarus.db.models import Trade
from icarus.types import MarketSnapshot
from icarus.types.market import Chain
from sqlalchemy import select


class _FakeAdapter:
    name = "fake"
    historical_supported = False

    async def fetch_live(self, chain: Chain) -> MarketSnapshot:
        return MarketSnapshot(
            timestamp=datetime(2026, 6, 3, tzinfo=UTC), chain=chain,
            prices={"ETH": Decimal("3000")}, apys={}, pool_state={},
            gas_gwei=Decimal("2"), metadata={},
        )


class _StubHoldings:
    async def current_usd_holdings(self) -> tuple[Decimal, Decimal]:
        return Decimal("8000"), Decimal("2000")  # nav 10000


class _FakeCycle:
    def __init__(self) -> None:
        self.calls = 0

    async def run_one(self) -> ManagedCycleResult:
        self.calls += 1
        return ManagedCycleResult(action="hold", reason="test", published=False, correlation_id="x")


@pytest.mark.asyncio
async def test_tick_feeds_breakers_and_runs_cycle() -> None:
    drawdown = DrawdownBreaker()
    gas_spike = GasSpikeBreaker()
    cycle = _FakeCycle()
    engine = ManagedEngine(
        cycle=cycle, holdings=_StubHoldings(), adapter=_FakeAdapter(),
        drawdown=drawdown, gas_spike=gas_spike, gas_tracker=GasAverageTracker(),
        chain="base",
    )
    await engine._tick()
    # Drawdown breaker saw the live NAV (8000 + 2000).
    assert drawdown.current_value == Decimal("10000")
    # Gas spike breaker saw the live gas.
    assert gas_spike.current_gas == Decimal("2")
    # The cycle ran exactly once.
    assert cycle.calls == 1


class _DeadTask:
    def __init__(self, *, cancelled: bool, exc: Exception | None) -> None:
        self._cancelled, self._exc = cancelled, exc

    def cancelled(self) -> bool:
        return self._cancelled

    def exception(self) -> Exception | None:
        return self._exc


def test_consumer_death_halts_engine() -> None:
    engine = ManagedEngine(
        cycle=_FakeCycle(), holdings=_StubHoldings(), adapter=_FakeAdapter(),
        drawdown=DrawdownBreaker(), gas_spike=GasSpikeBreaker(),
        gas_tracker=GasAverageTracker(), chain="base",
    )
    engine._on_consumer_done(_DeadTask(cancelled=False, exc=RuntimeError("redis down")))
    assert engine._stop.is_set()  # fail-closed: trading halted


def test_clean_cancel_does_not_halt() -> None:
    engine = ManagedEngine(
        cycle=_FakeCycle(), holdings=_StubHoldings(), adapter=_FakeAdapter(),
        drawdown=DrawdownBreaker(), gas_spike=GasSpikeBreaker(),
        gas_tracker=GasAverageTracker(), chain="base",
    )
    engine._on_consumer_done(_DeadTask(cancelled=True, exc=None))
    assert not engine._stop.is_set()  # normal shutdown, no alarm


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
    engine = ManagedEngine(
        cycle=_PublishingCycle(), holdings=_StubHoldings(), adapter=_FakeAdapter(),
        drawdown=DrawdownBreaker(), gas_spike=GasSpikeBreaker(),
        gas_tracker=GasAverageTracker(), chain="base",
        pending_trade_recorder=_make_pending_recorder(
            db, chain="base", protocol="aerodrome", slippage_bps=50
        ),
    )
    await engine._tick()
    with db.get_session() as s:
        rows = s.execute(select(Trade)).scalars().all()
    assert len(rows) == 1 and rows[0].trade_id == "ord9" and rows[0].status == "pending"
