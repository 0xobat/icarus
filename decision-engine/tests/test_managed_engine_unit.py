"""Smoke test for the managed worker loop (P1.5c Task 4C)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from icarus.types import MarketSnapshot
from icarus.types.market import Chain

from decision_engine.__main__ import ManagedEngine
from decision_engine.managed_cycle import ManagedCycleResult
from decision_engine.risk.drawdown_breaker import DrawdownBreaker
from decision_engine.risk.gas_spike_breaker import GasSpikeBreaker
from decision_engine.gas_tracker import GasAverageTracker


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
