"""Walk-forward unit tests.

90 days of synthetic snapshots → expect ~``(90 - 60 - 15) / 3 + 1 = 6``
windows under the blueprint's default ``(60, 15, 3)``. We assert at
least one window emerges and that the train/test slicing is
non-overlapping and within-bounds.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from backtest_worker.walkforward import _split_windows, run_walk_forward
from icarus.types import MarketSnapshot
from icarus.types.market import PoolState


def _stub_snapshots(n: int) -> list[MarketSnapshot]:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    out: list[MarketSnapshot] = []
    for i in range(n):
        out.append(
            MarketSnapshot(
                timestamp=base + timedelta(days=i),
                chain="base",
                prices={"BASE": Decimal(str(100 + i))},
                apys={},
                pool_state={
                    "stub:pool:base": PoolState(
                        pool_id="stub:pool:base",
                        tvl=Decimal("1000000"),
                        depth=Decimal("100000"),
                        fees_24h=Decimal("100"),
                    )
                },
                gas_gwei=Decimal("20"),
                metadata={"i": i},
            )
        )
    return out


def test_split_windows_60_15_3_on_90_days():
    snapshots = _stub_snapshots(90)
    windows = _split_windows(snapshots, train_days=60, test_days=15, step_days=3)
    # (90 - 60 - 15) / 3 + 1 = 6 windows.
    assert len(windows) == 6
    for tr_s, tr_e, te_s, te_e in windows:
        assert tr_e == tr_s + 60
        assert te_s == tr_e
        assert te_e == te_s + 15
        assert te_e <= 90


def test_split_windows_too_few_snapshots():
    snapshots = _stub_snapshots(40)
    windows = _split_windows(snapshots, train_days=60, test_days=15, step_days=3)
    assert windows == []


@pytest.mark.asyncio
async def test_run_walk_forward_emits_windows(stub_adapter, toy_evaluate):
    """Drive walk-forward end-to-end on the stub adapter's 90 days."""
    snapshots = []
    async for snap in stub_adapter.fetch_historical(
        "base", datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 4, 1, tzinfo=UTC)
    ):
        snapshots.append(snap)
    assert len(snapshots) == 90

    rows = run_walk_forward(
        candidate_id="TEST-001-abc",
        template_id="TEST-001",
        params={"period": 5, "threshold": 0},
        snapshots=snapshots,
        walk_forward=(60, 15, 3),
        evaluate_fn=toy_evaluate,
    )
    # The contract guaranteed by ``_split_windows``: 6 windows on 90 days.
    assert len(rows) == 6
    # Each row has the right shape and timestamps respect ordering.
    for r in rows:
        assert r.candidate_id == "TEST-001-abc"
        assert r.train_start < r.train_end <= r.test_start < r.test_end
        # Sharpe is finite (may be zero on flat segments).
        assert r.train_sharpe == r.train_sharpe
        assert r.test_sharpe == r.test_sharpe
        assert r.test_max_dd >= 0.0
