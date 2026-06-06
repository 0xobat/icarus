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
    async def current_usd_by_asset(self) -> dict[str, Decimal]:
        return {"WETH": Decimal("8000"), "USDC": Decimal("2000")}  # nav 10000


class _FakeCycle:
    def __init__(self) -> None:
        self.calls = 0

    async def run_one(self) -> ManagedCycleResult:
        self.calls += 1
        return ManagedCycleResult(action="hold", reason="test", published=False, correlation_id="x")


class _UsdcFeedAdapter:
    """Like _FakeAdapter but the snapshot carries a live USDC price."""

    name = "fake"
    historical_supported = False

    def __init__(self, usdc_price: Decimal) -> None:
        self._usdc_price = usdc_price

    async def fetch_live(self, chain: Chain) -> MarketSnapshot:
        return MarketSnapshot(
            timestamp=datetime(2026, 6, 3, tzinfo=UTC), chain=chain,
            prices={"ETH": Decimal("3000"), "USDC": self._usdc_price},
            apys={}, pool_state={}, gas_gwei=Decimal("2"), metadata={},
        )


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


@pytest.mark.asyncio
async def test_tick_feeds_depeg_breaker_from_usdc_price() -> None:
    from decision_engine.risk.depeg_breaker import DepegBreaker

    depeg = DepegBreaker(threshold_bps=100)
    engine = ManagedEngine(
        cycle=_FakeCycle(), holdings=_StubHoldings(),
        adapter=_UsdcFeedAdapter(usdc_price=Decimal("0.985")),
        drawdown=DrawdownBreaker(), gas_spike=GasSpikeBreaker(),
        gas_tracker=GasAverageTracker(), chain="base", depeg=depeg,
    )
    await engine._tick()
    # The depeg breaker saw the live USDC price and tripped (150 bps > 100).
    assert depeg.current_price == Decimal("0.985")
    assert depeg.is_tripped


@pytest.mark.asyncio
async def test_tick_skips_depeg_update_when_no_usdc_price() -> None:
    from decision_engine.risk.depeg_breaker import DepegBreaker

    depeg = DepegBreaker(threshold_bps=100)
    engine = ManagedEngine(
        cycle=_FakeCycle(), holdings=_StubHoldings(),
        adapter=_FakeAdapter(),  # no USDC in prices
        drawdown=DrawdownBreaker(), gas_spike=GasSpikeBreaker(),
        gas_tracker=GasAverageTracker(), chain="base", depeg=depeg,
    )
    await engine._tick()
    # No USDC price → breaker never updated → stays untripped (backward compat).
    assert depeg.current_price is None
    assert not depeg.is_tripped


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


# ── PnL deposit-tracker isolation (reporting, best-effort) ───────────────────


class _StubTracker:
    """A tracker with a fixed contributed value; records refresh() calls."""

    def __init__(self, contributed: Decimal | None) -> None:
        self._contributed = contributed
        self.refreshes = 0

    @property
    def contributed_usd(self) -> Decimal | None:
        return self._contributed

    async def refresh(self) -> None:
        self.refreshes += 1


class _RaisingTracker:
    """A tracker whose every method raises — the engine must isolate it."""

    @property
    def contributed_usd(self) -> Decimal | None:
        raise RuntimeError("boom on read")

    async def refresh(self) -> None:
        raise RuntimeError("boom on refresh")


@pytest.mark.asyncio
async def test_tick_logs_portfolio_pnl() -> None:
    """With a tracker returning contributed=4000 and nav=10000, the tick logs a
    `portfolio_pnl` event carrying the correct structured values."""
    from structlog.testing import capture_logs

    tracker = _StubTracker(Decimal("4000"))
    engine = ManagedEngine(
        cycle=_FakeCycle(), holdings=_StubHoldings(), adapter=_FakeAdapter(),
        drawdown=DrawdownBreaker(), gas_spike=GasSpikeBreaker(),
        gas_tracker=GasAverageTracker(), chain="base",
        pnl_tracker=tracker, pnl_refresh_every=1,
    )
    with capture_logs() as logs:
        await engine._tick()
    pnl_events = [e for e in logs if e.get("event") == "portfolio_pnl"]
    assert len(pnl_events) == 1
    event = pnl_events[0]
    # nav 10000 (8000 + 2000), contributed 4000 → pnl 6000, pct 150.
    assert event["nav_usd"] == "10000"
    assert event["contributed_usd"] == "4000"
    assert event["pnl_usd"] == "6000"
    # Decimal division keeps a trailing zero (150.0); value is numerically 150.
    assert Decimal(event["pnl_pct"]) == Decimal("150")
    # Tracker was refreshed on the cadence.
    assert tracker.refreshes == 1


@pytest.mark.asyncio
async def test_tick_pnl_math() -> None:
    """Directly verify pnl_usd / pnl_pct via the engine's helper."""
    tracker = _StubTracker(Decimal("4000"))
    engine = ManagedEngine(
        cycle=_FakeCycle(), holdings=_StubHoldings(), adapter=_FakeAdapter(),
        drawdown=DrawdownBreaker(), gas_spike=GasSpikeBreaker(),
        gas_tracker=GasAverageTracker(), chain="base", pnl_tracker=tracker,
    )
    pnl_usd, pnl_pct = engine._compute_pnl(Decimal("10000"), Decimal("4000"))
    assert pnl_usd == Decimal("6000")
    assert pnl_pct == Decimal("150")


def test_compute_pnl_zero_nav_positive_contributed() -> None:
    """NAV=0, contributed>0 → total loss: pnl -4000, pct -100 (no div-by-zero)."""
    pnl_usd, pnl_pct = ManagedEngine._compute_pnl(Decimal("0"), Decimal("4000"))
    assert pnl_usd == Decimal("-4000")
    assert pnl_pct == Decimal("-100")


def test_compute_pnl_negative_contributed() -> None:
    """Withdrawals > deposits -> contributed negative; NAV - (negative) adds.

    nav 1000, contributed -200 → pnl 1200; pct = 1200 / -200 * 100 = -600.
    """
    pnl_usd, pnl_pct = ManagedEngine._compute_pnl(Decimal("1000"), Decimal("-200"))
    assert pnl_usd == Decimal("1200")
    assert pnl_pct == Decimal("-600")


@pytest.mark.asyncio
async def test_tick_with_raising_tracker_still_runs_cycle() -> None:
    """A tracker that raises on refresh/read must NOT break the tick or trading."""
    cycle = _FakeCycle()
    drawdown = DrawdownBreaker()
    engine = ManagedEngine(
        cycle=cycle, holdings=_StubHoldings(), adapter=_FakeAdapter(),
        drawdown=drawdown, gas_spike=GasSpikeBreaker(),
        gas_tracker=GasAverageTracker(), chain="base",
        pnl_tracker=_RaisingTracker(), pnl_refresh_every=1,
    )
    await engine._tick()  # must NOT raise
    # The cycle still ran and breakers were still fed — trading unaffected.
    assert cycle.calls == 1
    assert drawdown.current_value == Decimal("10000")
    # A PnL (reporting) failure must NEVER halt trading.
    assert not engine._stop.is_set()


@pytest.mark.asyncio
async def test_tick_no_tracker_runs_normally() -> None:
    """No tracker (PnL disabled) → tick runs the cycle normally, no error."""
    cycle = _FakeCycle()
    engine = ManagedEngine(
        cycle=cycle, holdings=_StubHoldings(), adapter=_FakeAdapter(),
        drawdown=DrawdownBreaker(), gas_spike=GasSpikeBreaker(),
        gas_tracker=GasAverageTracker(), chain="base",
    )
    await engine._tick()
    assert cycle.calls == 1


@pytest.mark.asyncio
async def test_tick_refresh_cadence_only_every_nth() -> None:
    """The tracker refreshes on a slow cadence, not every tick."""
    tracker = _StubTracker(Decimal("4000"))
    engine = ManagedEngine(
        cycle=_FakeCycle(), holdings=_StubHoldings(), adapter=_FakeAdapter(),
        drawdown=DrawdownBreaker(), gas_spike=GasSpikeBreaker(),
        gas_tracker=GasAverageTracker(), chain="base",
        pnl_tracker=tracker, pnl_refresh_every=3,
    )
    for _ in range(7):
        await engine._tick()
    # Refreshed on ticks 1 and 4 and 7 → 3 refreshes (refresh on (n-1)%every==0).
    assert tracker.refreshes == 3


@pytest.mark.asyncio
async def test_tick_surfaces_derisk_signal_on_usdc_depeg() -> None:
    """P3.2: a tripped USDC depeg → the health monitor surfaces a halt_all
    derisk_signal in the tick (reporting; isolated from the cycle)."""
    from decision_engine.health_monitor import PortfolioHealthMonitor
    from decision_engine.risk.depeg_breaker import DepegBreaker
    from structlog.testing import capture_logs

    depeg = DepegBreaker(threshold_bps=100)
    monitor = PortfolioHealthMonitor(usdc_depeg=depeg, lst_breakers={})
    engine = ManagedEngine(
        cycle=_FakeCycle(), holdings=_StubHoldings(),
        adapter=_UsdcFeedAdapter(usdc_price=Decimal("0.95")),  # 500 bps off-peg
        drawdown=DrawdownBreaker(), gas_spike=GasSpikeBreaker(),
        gas_tracker=GasAverageTracker(), chain="base", depeg=depeg,
        health_monitor=monitor,
    )
    with capture_logs() as logs:
        await engine._tick()
    derisk = [e for e in logs if e.get("event") == "derisk_signal"]
    assert len(derisk) == 1
    assert derisk[0]["kind"] == "halt_all"


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
