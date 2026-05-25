"""Grid-search unit tests.

Exercises ``run_grid_search`` over a 3x3 param space against a stub
DataAdapter — asserts the engine yields 9 rows in stable
Cartesian-product order with the expected metric shape. No live
network, no real templates.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from backtest_worker.search import (
    _iter_param_combinations,
    run_grid_search,
    select_top_k,
)
from icarus.protocols.backtest import GridSearchConfig


def test_iter_param_combinations_3x3_stable_order():
    ranges = {"period": [3, 5, 7], "threshold": [-1, 0, 1]}
    combos = _iter_param_combinations(ranges)
    assert len(combos) == 9
    # Insertion order on keys; sequence order on values.
    assert combos[0] == {"period": 3, "threshold": -1}
    assert combos[1] == {"period": 3, "threshold": 0}
    assert combos[2] == {"period": 3, "threshold": 1}
    assert combos[3] == {"period": 5, "threshold": -1}
    assert combos[-1] == {"period": 7, "threshold": 1}


def test_iter_param_combinations_empty_yields_one_row():
    """Empty param space => one default row (the template's defaults)."""
    assert _iter_param_combinations({}) == [{}]


@pytest.mark.asyncio
async def test_run_grid_search_produces_nine_rows(stub_adapter, toy_evaluate):
    config = GridSearchConfig(
        template_id="TEST-001",
        template_version="0.1.0",
        asset_universe=("BASE",),
        chain="base",
        backtest_start=datetime(2026, 1, 1, tzinfo=UTC),
        backtest_end=datetime(2026, 4, 1, tzinfo=UTC),
        walk_forward=(60, 15, 3),
        turnover_lambda=Decimal("0.01"),
        top_k=3,
        param_ranges={"period": [3, 5, 7], "threshold": [-1, 0, 1]},
    )
    rows = await run_grid_search(
        config, evaluate_fn=toy_evaluate, adapter=stub_adapter
    )
    assert len(rows) == 9
    # Every row has the right shape and finite metrics.
    for r in rows:
        assert r.template_id == "TEST-001"
        assert r.template_version == "0.1.0"
        assert set(r.params.keys()) == {"period", "threshold"}
        # Metrics may be 0 for inactive combinations, but must be finite.
        assert r.sharpe == r.sharpe  # not NaN
        assert r.max_dd >= 0.0
        assert r.compute_seconds >= 0.0


def test_select_top_k_picks_highest_deflated_sharpe(stub_adapter, toy_evaluate):
    """Sanity: select_top_k is order-stable and respects k."""
    from backtest_worker.search import ParameterSearchResult

    rows = [
        ParameterSearchResult(
            template_id="T",
            template_version="1",
            params={"i": i},
            sharpe=float(i),
            deflated_sharpe=float(i),
            max_dd=0.0,
            turnover=0.0,
            oos_sharpe=None,
            compute_seconds=0.01,
        )
        for i in range(5)
    ]
    top = select_top_k(rows, 2)
    assert len(top) == 2
    assert {r.params["i"] for r in top} == {4, 3}
    assert all(r.is_top_k for r in top)


def test_select_top_k_zero_returns_empty():
    assert select_top_k([], 0) == []
