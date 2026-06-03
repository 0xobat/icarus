"""Unit tests for the managed-portfolio cycle (P1.4)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from decision_engine.cycle import ExecutorPublisher
from decision_engine.managed_cycle import (
    HoldingsProvider,
    ManagedCycleConfig,
    ManagedPortfolioCycle,
)
from decision_engine.rebalance import RebalanceTarget
from decision_engine.risk_gate import RiskGate
from icarus.envelopes.orders import ExecutionOrder
from icarus.types import MarketSnapshot
from icarus.types.market import Chain

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


@pytest.mark.asyncio
async def test_overweight_publishes_weth_to_usdc_swap() -> None:
    # crypto 0.80 of $10k → sell $2000 WETH→USDC. cost: gas≈$0.60 + slip $10 = ~$10.60;
    # margin 4 → ~$42 threshold; correction $2000 >> threshold → proceeds.
    publisher = _CapturePublisher()
    cycle = _cycle(_StubHoldings(Decimal("8000"), Decimal("2000")), publisher)
    result = await cycle.run_one()
    assert result.action == "rebalance"
    assert result.published is True
    assert len(publisher.published) == 1
    chain, order = publisher.published[0]
    assert chain == "base"
    assert order.chain == "base"
    assert order.action == "swap"
    assert order.strategy == "REBAL:base"
    assert order.template_id is None and order.candidate_id is None
    assert order.solana_specific is None
    # WETH→USDC: token_in is WETH address, token_out is USDC address.
    assert order.params.token_in == "0x4200000000000000000000000000000000000006"
    assert order.params.token_out == "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
    assert order.params.recipient == _SAFE
    # $2000 of WETH at $3000 = 0.6666... WETH → 666666666666666666 wei (floored).
    assert order.params.amount == Decimal("666666666666666666")


@pytest.mark.asyncio
async def test_underweight_publishes_usdc_to_weth_swap() -> None:
    # crypto 0.40 of $10k → buy $2000 USDC→WETH.
    publisher = _CapturePublisher()
    cycle = _cycle(_StubHoldings(Decimal("4000"), Decimal("6000")), publisher)
    result = await cycle.run_one()
    assert result.action == "rebalance"
    assert result.published is True
    _, order = publisher.published[0]
    # USDC→WETH: token_in is USDC, token_out is WETH.
    assert order.params.token_in == "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
    assert order.params.token_out == "0x4200000000000000000000000000000000000006"
    # $2000 of USDC at $1 = 2000e6 (6 decimals).
    assert order.params.amount == Decimal("2000000000")


@pytest.mark.asyncio
async def test_sepolia_cycle_uses_sepolia_token_addresses() -> None:
    """ManagedCycleConfig(chain_id=84532) resolves Sepolia addresses in published order."""
    sepolia_usdc = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"
    weth_addr = "0x4200000000000000000000000000000000000006"
    sepolia_config = ManagedCycleConfig(
        recipient=_SAFE, protocol="aerodrome", slippage_bps=50,
        cost_gate_margin=Decimal("4"), gas_units=200_000, deadline_seconds=60,
        chain_id=84532,
    )
    publisher = _CapturePublisher()
    cycle = ManagedPortfolioCycle(
        adapter=_FakeAdapter(Decimal("3000"), Decimal("1")),
        holdings=_StubHoldings(Decimal("4000"), Decimal("6000")),  # underweight: USDC→WETH
        target=_TARGET,
        risk_gate=RiskGate(checkers=[]),
        publisher=publisher,
        config=sepolia_config,
    )
    result = await cycle.run_one()
    assert result.action == "rebalance"
    assert result.published is True
    _, order = publisher.published[0]
    # Sepolia USDC→WETH: token_in is Sepolia USDC address.
    assert order.params.token_in == sepolia_usdc
    assert order.params.token_out == weth_addr


@pytest.mark.asyncio
async def test_risk_gate_rejection_blocks_publish() -> None:
    from decision_engine.risk_gate import RiskContext, RiskDecision

    class _RejectAll:
        name = "reject_all"

        def check(self, order: ExecutionOrder, ctx: RiskContext) -> RiskDecision:
            return RiskDecision(passed=False, checker=self.name, reason="test reject")

    publisher = _CapturePublisher()
    cycle = ManagedPortfolioCycle(
        adapter=_FakeAdapter(Decimal("3000"), Decimal("1")),
        holdings=_StubHoldings(Decimal("8000"), Decimal("2000")),
        target=_TARGET,
        risk_gate=RiskGate(checkers=[_RejectAll()]),
        publisher=publisher,
        config=_CONFIG,
    )
    result = await cycle.run_one()
    assert result.action == "rebalance"
    assert result.published is False
    assert publisher.published == []
