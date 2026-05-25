"""Shared fixtures: synthetic ``DataAdapter`` stub + simple template.

Stream A is producing the real adapters (DefiLlama / on-chain RPC / Dune)
in a parallel worktree. This stub lets the W3 search + walk-forward
engine and runner be developed and tested without that dep landing —
it satisfies the ``DataAdapter`` Protocol and emits a deterministic
ramp + sinusoid price series that exercises both entry and exit paths
of a simple template.

Tests targeting persistence use an in-memory SQLite DatabaseManager so
ORM behaviour is exercised end-to-end without a live Postgres.
"""

from __future__ import annotations

import math
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from icarus.db.database import DatabaseConfig, DatabaseManager
from icarus.types import Decision, MarketSnapshot, PortfolioSnapshot
from icarus.types.market import Chain, PoolState


class StubDataAdapter:
    """Synthetic ``DataAdapter`` impl — N daily snapshots of one asset.

    `prices['BASE']` is a ramp + sinusoid so returns have non-zero
    variance (Sharpe is well-defined) and there's enough drawdown for
    ``max_dd > 0``. APYs default to 0 (templates that read APY get a
    deterministic zero, which exercises the no-entry branch).
    """

    name = "stub"
    historical_supported = True

    def __init__(self, n_days: int = 90) -> None:
        self._n_days = n_days

    async def fetch_live(self, chain: Chain) -> MarketSnapshot:
        return _make_snapshot(0, chain)

    async def fetch_historical(
        self, chain: Chain, start: datetime, end: datetime
    ) -> AsyncIterator[MarketSnapshot]:
        for i in range(self._n_days):
            yield _make_snapshot(i, chain, base=start)


def _make_snapshot(
    i: int, chain: Chain, base: datetime | None = None
) -> MarketSnapshot:
    base = base or datetime(2026, 1, 1, tzinfo=UTC)
    ts = base + timedelta(days=i)
    # Ramp + sinusoid → realistic-ish return series.
    price = Decimal(str(100 + i * 0.5 + 20 * math.sin(i / 7)))
    return MarketSnapshot(
        timestamp=ts,
        chain=chain,
        prices={"BASE": price},
        apys={"BASE.apy": Decimal("0.05")},
        pool_state={
            "stub:pool:base": PoolState(
                pool_id="stub:pool:base",
                tvl=Decimal("10000000"),
                depth=Decimal("1000000"),
                fees_24h=Decimal("1000"),
            )
        },
        gas_gwei=Decimal("20"),
        metadata={"i": i},
    )


def _toy_evaluate(
    params: dict, market_data: MarketSnapshot, portfolio_state: PortfolioSnapshot
) -> Decision:
    """Tiny moving-average crossover stand-in.

    Enters when the snapshot index (in metadata) modulo the period is
    less than half; exits otherwise. The discrete params (``period``,
    ``threshold``) carve out a deterministic 3x3 grid for tests without
    needing a real signal.
    """
    period = int(params.get("period", 5))
    threshold = int(params.get("threshold", 0))
    i = int(market_data.metadata.get("i", 0))
    in_position = bool(portfolio_state.positions)
    phase = i % max(period, 1)
    enter = phase < period // 2 + threshold
    if enter and not in_position:
        return Decision(
            action="enter",
            target_size=portfolio_state.cash_usd,
            confidence=Decimal("0.6"),
            reasoning=f"phase {phase} < {period // 2 + threshold}",
        )
    if not enter and in_position:
        return Decision(
            action="exit",
            target_size=Decimal("0"),
            confidence=Decimal("0.6"),
            reasoning=f"phase {phase} >= {period // 2 + threshold}",
        )
    return Decision(
        action="hold",
        target_size=Decimal("0"),
        confidence=Decimal("0.5"),
        reasoning="no change",
    )


@pytest.fixture
def stub_adapter() -> StubDataAdapter:
    return StubDataAdapter(n_days=90)


@pytest.fixture
def toy_evaluate():
    return _toy_evaluate


@pytest.fixture
def in_memory_db(tmp_path):
    """Ephemeral on-disk SQLite (file in tmp_path).

    On-disk rather than ``:memory:`` because SQLAlchemy plus SQLite's
    in-memory mode opens a fresh connection per Session, and each one
    sees an empty DB. A tmp_path file gives every session the same DB
    without pulling in StaticPool wiring just for a test.
    """
    db_path = tmp_path / "test.db"
    config = DatabaseConfig(url=f"sqlite:///{db_path}")
    db = DatabaseManager(config)
    db.create_tables()
    try:
        yield db
    finally:
        db.close()
