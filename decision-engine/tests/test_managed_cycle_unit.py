"""Unit tests for the managed-portfolio cycle (P1.4)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from icarus.envelopes.orders import ExecutionOrder
from icarus.types import MarketSnapshot
from icarus.types.market import Chain

from decision_engine.cycle import ExecutorPublisher
from decision_engine.managed_cycle import (
    HoldingsProvider,
    ManagedCycleConfig,
    ManagedPortfolioCycle,
)
from decision_engine.rebalance import RebalanceTarget
from decision_engine.risk_gate import RiskGate

_SAFE = "0x1111111111111111111111111111111111111111"
_TARGET = RebalanceTarget(
    crypto_symbol="WETH", stable_symbol="USDC",
    crypto_weight=Decimal("0.6"), band=Decimal("0.10"),
)
_CONFIG = ManagedCycleConfig(
    recipient=_SAFE, protocol="aerodrome", slippage_bps=50,
    cost_gate_margin=Decimal("4"), gas_units=200_000, deadline_seconds=60,
)


class _FakeAdapter:
    name = "fake"
    historical_supported = False

    def __init__(self, eth_usd: Decimal, gas_gwei: Decimal) -> None:
        self._eth, self._gas = eth_usd, gas_gwei

    async def fetch_live(self, chain: Chain) -> MarketSnapshot:
        return MarketSnapshot(
            timestamp=datetime(2026, 6, 3, tzinfo=UTC), chain=chain,
            prices={"ETH": self._eth}, apys={}, pool_state={},
            gas_gwei=self._gas, metadata={},
        )


class _StubHoldings:
    def __init__(self, crypto_usd: Decimal, stable_usd: Decimal) -> None:
        self._c, self._s = crypto_usd, stable_usd

    async def current_usd_holdings(self) -> tuple[Decimal, Decimal]:
        return self._c, self._s


class _CapturePublisher:
    def __init__(self) -> None:
        self.published: list[tuple[str, ExecutionOrder]] = []

    async def publish_order(self, chain: Chain, order: ExecutionOrder) -> None:
        self.published.append((chain, order))


def _cycle(holdings: _StubHoldings, publisher: _CapturePublisher) -> ManagedPortfolioCycle:
    return ManagedPortfolioCycle(
        adapter=_FakeAdapter(Decimal("3000"), Decimal("1")),
        holdings=holdings,
        target=_TARGET,
        risk_gate=RiskGate(checkers=[]),  # permissive: empty gate passes everything
        publisher=publisher,
        config=_CONFIG,
    )


def test_protocols_satisfied() -> None:
    assert isinstance(_StubHoldings(Decimal("1"), Decimal("1")), HoldingsProvider)
    assert isinstance(_CapturePublisher(), ExecutorPublisher)


@pytest.mark.asyncio
async def test_within_band_publishes_nothing() -> None:
    publisher = _CapturePublisher()
    cycle = _cycle(_StubHoldings(Decimal("6500"), Decimal("3500")), publisher)
    result = await cycle.run_one()
    assert result.action == "hold"
    assert result.published is False
    assert publisher.published == []
