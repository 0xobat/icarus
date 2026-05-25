"""Paper-trade harness unit tests.

Uses the in-process `db` fixture from conftest.py (ephemeral SQLite on
tmp_path). Adapters and templates are constructed inline — no live calls,
no frontier API, no real `templates/` directory.
"""

from __future__ import annotations

import json
import math
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from icarus.db.models import PaperTradeState
from icarus.dsl import Template
from icarus.dsl.linter import LintReport
from icarus.dsl.manifest import (
    ContinuousParam,
    ExpectedMetrics,
    Source,
    TemplateManifest,
)
from icarus.types import Decision, MarketSnapshot, PortfolioSnapshot
from icarus.types.market import Chain, PoolState
from lake_governor.paper_trade import (
    OBSERVATION_WINDOW_DAYS,
    PaperTradeHarness,
    ShadowPosition,
    _max_drawdown,
    _sharpe,
)

# ─────────────────────────────────────────────────────────────────────────────
# Test fixtures: stub adapter + stub template factory
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class _ScriptedAdapter:
    """DataAdapter Protocol impl: yields snapshots from a pre-built list.

    `fetch_live` advances through the script one snapshot per call,
    repeating the last entry once exhausted (so a harness driven for
    more cycles than scripted ticks never crashes).
    """

    snapshots: Sequence[MarketSnapshot]
    name: str = "scripted"
    historical_supported: bool = True
    _idx: int = field(default=0, init=False)

    async def fetch_live(self, chain: Chain) -> MarketSnapshot:
        snap = self.snapshots[min(self._idx, len(self.snapshots) - 1)]
        self._idx += 1
        return snap

    def fetch_historical(
        self, chain: Chain, start: datetime, end: datetime
    ) -> AsyncIterator[MarketSnapshot]:
        async def _gen() -> AsyncIterator[MarketSnapshot]:
            for s in self.snapshots:
                yield s

        return _gen()


def _snapshot(t: datetime, price: float, *, chain: Chain = "base") -> MarketSnapshot:
    return MarketSnapshot(
        timestamp=t,
        chain=chain,
        prices={"ASSET": Decimal(str(price))},
        apys={"ASSET": Decimal("0.05")},
        pool_state={
            "ASSET": PoolState(
                pool_id="base:test:asset",
                tvl=Decimal("1000000"),
                depth=Decimal("100000"),
                fees_24h=Decimal("1000"),
            )
        },
        gas_gwei=Decimal("0.1"),
        metadata={},
    )


def _manifest(template_id: str = "TEST-001") -> TemplateManifest:
    return TemplateManifest(
        id=template_id,
        semver="0.1.0",
        title="paper-trade test template",
        chain="base",
        protocol="test",
        asset_universe=["ASSET"],
        sources=[Source(type="paper_pdf", ref="test://paper")],
        allocation_max=Decimal("0.10"),
        risk_profile="low",
        params={
            "threshold": ContinuousParam(
                kind="continuous",
                low=Decimal("0"),
                high=Decimal("1"),
            )
        },
        expected_metrics=ExpectedMetrics(
            sharpe_min=Decimal("0.5"),
            max_dd_max=Decimal("0.20"),
        ),
    )


def _template(evaluate_fn: Any, template_id: str = "TEST-001") -> Template:
    return Template(
        manifest=_manifest(template_id),
        evaluate=evaluate_fn,
        source_dir=Path("/dev/null"),
        lint_report=LintReport(path=Path("/dev/null"), issues=()),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Test 1: N cycles → N+1 persistence ticks
# ─────────────────────────────────────────────────────────────────────────────


async def test_harness_persists_paper_trade_state_per_cycle(db):
    """First cycle inserts the row; each subsequent cycle updates it.

    After running N+1 cycles we should see exactly 1 paper_trade_state
    row whose `observation_days` advanced and whose `last_updated_at`
    matches the most recent `now_fn` tick.

    Per the task spec: "harness with a stub adapter + a 1-candidate
    template runs N cycles and persists exactly N+1 paper_trade_state
    updates" — interpreted as N+1 writes (not rows) over N+1 cycles.
    """
    base_t = datetime(2026, 1, 1, tzinfo=UTC)
    snapshots = [_snapshot(base_t + timedelta(days=i), 100.0 + i) for i in range(5)]

    def always_hold(
        params: dict[str, Any], market: MarketSnapshot, portfolio: PortfolioSnapshot
    ) -> Decision:
        return Decision(
            action="hold",
            target_size=Decimal("0"),
            confidence=Decimal("0.5"),
            reasoning="test hold",
        )

    cursor = {"t": base_t}

    def now_fn() -> datetime:
        return cursor["t"]

    n_plus_1 = 4  # i.e. N=3 follow-up cycles after the inaugural one
    harness = PaperTradeHarness(
        adapter=_ScriptedAdapter(snapshots),
        chain="base",
        candidates=[("cand-1", _template(always_hold), {"threshold": Decimal("0.5")})],
        db=db,
        now_fn=now_fn,
    )

    for i in range(n_plus_1):
        cursor["t"] = base_t + timedelta(days=i)
        await harness.run_cycle()

    with db.get_session() as session:
        rows = session.query(PaperTradeState).all()

    assert len(rows) == 1, "one candidate ⇒ one upserted row, not N+1 distinct rows"
    row = rows[0]
    assert row.candidate_id == "cand-1"
    # observation_days reflects the last cursor advancement (3 days).
    assert row.observation_days == n_plus_1 - 1
    # last_updated_at matches the final cursor (UTC-aware compare).
    last = row.last_updated_at
    if last.tzinfo is None:
        last = last.replace(tzinfo=UTC)
    assert last == cursor["t"]


# ─────────────────────────────────────────────────────────────────────────────
# Test 2: sharpe + max_dd on a known synthetic series
# ─────────────────────────────────────────────────────────────────────────────


def test_sharpe_and_max_dd_match_known_series():
    """Hand-pick a price series, drive the metric helpers directly, and
    check the numbers match a closed-form expectation. We exercise the
    helpers (not the full harness) because the harness's per-cycle
    machinery is exercised in test 1 — this test pins the math."""

    # 6 NAV points → 5 returns: +10%, -5%, +5%, -10%, +20%.
    nav = [100.0, 110.0, 104.5, 109.725, 98.7525, 118.503]

    sharpe = _sharpe(nav, periods_per_year=365)
    assert sharpe is not None

    # Reference value: mean / std * sqrt(365)
    returns = [(nav[i + 1] - nav[i]) / nav[i] for i in range(len(nav) - 1)]
    mean = sum(returns) / len(returns)
    var = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    std = math.sqrt(var)
    expected = mean / std * math.sqrt(365)
    assert sharpe == pytest.approx(expected, rel=1e-9)

    # Max drawdown: peak is 110 at index 1; trough after that = 98.7525.
    # dd = (98.7525 - 110) / 110 = -0.1022...; reported magnitude = 0.1022...
    max_dd = _max_drawdown(nav)
    assert max_dd is not None
    assert max_dd == pytest.approx((110.0 - 98.7525) / 110.0, rel=1e-9)

    # Edge case: single point ⇒ None (matches paper_trade_state NULL).
    assert _sharpe([100.0]) is None
    assert _max_drawdown([100.0]) is None

    # Zero-variance series ⇒ sharpe is 0.0, not None or NaN.
    flat = [100.0, 100.0, 100.0, 100.0]
    assert _sharpe(flat) == 0.0
    assert _max_drawdown(flat) == 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Test 3: shadow_positions_json round-trips enter → hold → exit
# ─────────────────────────────────────────────────────────────────────────────


async def test_shadow_positions_json_roundtrips_enter_hold_exit(db):
    """Drive an enter on cycle 0, hold on cycle 1, exit on cycle 2 and
    assert the persisted JSON reflects the position state at each step,
    and that ShadowPosition.from_json_obj reconstructs faithfully."""

    base_t = datetime(2026, 2, 1, tzinfo=UTC)
    snapshots = [
        _snapshot(base_t, 100.0),
        _snapshot(base_t + timedelta(days=1), 110.0),
        _snapshot(base_t + timedelta(days=2), 105.0),
    ]

    script = iter(["enter", "hold", "exit"])

    def scripted(
        params: dict[str, Any], market: MarketSnapshot, portfolio: PortfolioSnapshot
    ) -> Decision:
        action = next(script)
        target = Decimal("1000") if action == "enter" else Decimal("0")
        return Decision(
            action=action,  # type: ignore[arg-type]
            target_size=target,
            confidence=Decimal("0.5"),
            reasoning=f"scripted {action}",
        )

    harness = PaperTradeHarness(
        adapter=_ScriptedAdapter(snapshots),
        chain="base",
        candidates=[("cand-rt", _template(scripted), {"threshold": Decimal("0.5")})],
        db=db,
        starting_cash_usd=Decimal("10000"),
    )

    # Cycle 0: enter ASSET at 100.
    await harness.run_cycle()
    with db.get_session() as session:
        row = (
            session.query(PaperTradeState).filter_by(candidate_id="cand-rt").one()
        )
        positions = json.loads(row.shadow_positions_json)
    assert len(positions) == 1
    rebuilt = ShadowPosition.from_json_obj(positions[0])
    assert rebuilt.asset == "ASSET"
    assert rebuilt.entry_price == Decimal("100")
    # 5 bps round-trip cost on entry ⇒ effective notional 999.5, size ~9.995.
    assert rebuilt.size_asset == pytest.approx(Decimal("9.995"), rel=Decimal("1e-9"))
    book_after_enter = harness.book("cand-rt")
    assert "ASSET" in book_after_enter.positions
    assert book_after_enter.cash_usd == Decimal("9000")

    # Cycle 1: hold — position should remain, mark price now 110.
    await harness.run_cycle()
    with db.get_session() as session:
        row = (
            session.query(PaperTradeState).filter_by(candidate_id="cand-rt").one()
        )
        positions = json.loads(row.shadow_positions_json)
    assert len(positions) == 1
    held = ShadowPosition.from_json_obj(positions[0])
    assert held.mark_price == Decimal("110")
    assert held.entry_price == Decimal("100")  # unchanged on hold

    # Cycle 2: exit — positions JSON should empty, cash should reflect
    # proceeds at 105 minus 5 bps round-trip cost.
    await harness.run_cycle()
    with db.get_session() as session:
        row = (
            session.query(PaperTradeState).filter_by(candidate_id="cand-rt").one()
        )
        positions = json.loads(row.shadow_positions_json)
    assert positions == []
    book_after_exit = harness.book("cand-rt")
    assert book_after_exit.positions == {}
    # cash ≈ 9000 + 9.995 * 105 * (1 - 5/10000) = 9000 + 1049.475 * 0.9995
    expected_cash = Decimal("9000") + Decimal("9.995") * Decimal("105") * (
        Decimal("1") - Decimal("5") / Decimal("10000")
    )
    assert book_after_exit.cash_usd == pytest.approx(
        expected_cash, rel=Decimal("1e-9")
    )


# ─────────────────────────────────────────────────────────────────────────────
# Test 4: evaluate() raising must not break the cycle
# ─────────────────────────────────────────────────────────────────────────────


async def test_harness_isolates_evaluate_exceptions(db, caplog):
    """Two candidates: one raises, one returns hold. The raising one
    must surface an error on its CycleOutcome and the healthy one must
    still produce a Decision and a persisted row. Subsequent cycles
    keep running for both."""

    base_t = datetime(2026, 3, 1, tzinfo=UTC)
    snapshots = [_snapshot(base_t + timedelta(days=i), 100.0) for i in range(3)]

    def boom(
        params: dict[str, Any], market: MarketSnapshot, portfolio: PortfolioSnapshot
    ) -> Decision:
        raise RuntimeError("evaluate exploded")

    def healthy(
        params: dict[str, Any], market: MarketSnapshot, portfolio: PortfolioSnapshot
    ) -> Decision:
        return Decision(
            action="hold",
            target_size=Decimal("0"),
            confidence=Decimal("0.5"),
            reasoning="hold",
        )

    harness = PaperTradeHarness(
        adapter=_ScriptedAdapter(snapshots),
        chain="base",
        candidates=[
            ("cand-boom", _template(boom, "TEST-002"), {"threshold": Decimal("0.5")}),
            ("cand-ok", _template(healthy, "TEST-003"), {"threshold": Decimal("0.5")}),
        ],
        db=db,
    )

    outcomes = await harness.run_cycle()
    assert {o.candidate_id for o in outcomes} == {"cand-boom", "cand-ok"}
    boom_outcome = next(o for o in outcomes if o.candidate_id == "cand-boom")
    ok_outcome = next(o for o in outcomes if o.candidate_id == "cand-ok")

    assert boom_outcome.error is not None
    assert "evaluate exploded" in boom_outcome.error
    assert boom_outcome.decision is None
    assert ok_outcome.error is None
    assert ok_outcome.decision is not None
    assert ok_outcome.decision.action == "hold"

    # Both rows persisted despite the raise.
    with db.get_session() as session:
        ids = {r.candidate_id for r in session.query(PaperTradeState).all()}
    assert ids == {"cand-boom", "cand-ok"}

    # Cycle survives a second tick (i.e. one raise does not poison the harness).
    outcomes2 = await harness.run_cycle()
    assert len(outcomes2) == 2
    assert next(o for o in outcomes2 if o.candidate_id == "cand-boom").error is not None
    assert next(o for o in outcomes2 if o.candidate_id == "cand-ok").decision is not None


# ─────────────────────────────────────────────────────────────────────────────
# Misc: blueprint default
# ─────────────────────────────────────────────────────────────────────────────


def test_observation_window_default_matches_blueprint():
    """Lower-bound of the blueprint's 2-4 week window."""
    assert OBSERVATION_WINDOW_DAYS == 14
