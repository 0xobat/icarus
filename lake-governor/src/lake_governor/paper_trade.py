"""Paper-trade harness — shadow execution of promoted candidates.

Cluster: Curation. Service: lake-governor.

A candidate that survives backtest + OOS gates enters the paper-trade
window before becoming live-promotion eligible. The harness:

  - Pulls a live `MarketSnapshot` per cycle via the injected `DataAdapter`
    (the *same* adapter Research workers use — that's what makes paper
    fidelity a static check rather than a runtime hope).
  - Calls each candidate's `evaluate()` with its private shadow portfolio.
  - Simulates a fill at the snapshot's quoted price minus a fixed bps
    cost (no slippage curve in v1; the realistic-fill model is a W7 item
    per the blueprint's executor stream).
  - Updates `shadow_positions_json`, recomputes `observed_sharpe` and
    `observed_max_dd`, and persists the row to `paper_trade_state`.

Persistence cadence: one upsert per cycle per candidate. The first cycle
inserts the initial row (entered_paper_at = now, zero NAV history); each
subsequent cycle updates the same row. After N cycles you see exactly
N + 1 versions of the row's last_updated_at if you snapshot the table on
every write — that's the bookkeeping invariant test_persists_n_plus_1
checks.

Promotion gate: not implemented here. The blueprint's state-machine
component (W5 Stream B) reads `paper_trade_state` and applies the
"observed_sharpe >= 0.8 * backtest_oos_sharpe AND observed_max_dd <=
1.2 * backtest_max_dd AND no risk breaches AND operator approval"
predicate. The harness's job is to *populate* those fields honestly.

Concurrency model: the harness is async-friendly. Each DB write is run
inside `asyncio.to_thread` to match the W2 extractor-worker pattern
(SQLAlchemy sync sessions wrapped at the await boundary). Per-candidate
evaluation is sequential within a cycle — the blueprint's "Top-K from
each template run independently in parallel" phrase refers to *strategy
independence*, not to within-cycle concurrency, which doesn't help when
the bottleneck is the data adapter's single fetch_live call.
"""

from __future__ import annotations

import asyncio
import json
import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import numpy as np
import structlog
from icarus.db.database import DatabaseManager
from icarus.db.models import PaperTradeState
from icarus.dsl import Template
from icarus.protocols.data import DataAdapter
from icarus.types import Decision, MarketSnapshot, PortfolioSnapshot, Position
from icarus.types.market import Chain

__all__ = [
    "DEFAULT_FILL_COST_BPS",
    "OBSERVATION_WINDOW_DAYS",
    "PaperTradeHarness",
    "ShadowPosition",
]

_logger = structlog.get_logger(service="lake_governor.paper_trade")

# Blueprint §"paper-trade harness": "2-4 week observation window before
# live-promotion eligibility." 14 days is the lower-bound default; the
# state-machine component may require longer per template.
OBSERVATION_WINDOW_DAYS: int = 14

# Fixed round-trip fill cost in basis points. Real venues add slippage on
# top of this — the W7 executor stream introduces a chain-aware slippage
# model. Until then a flat haircut is the honest placeholder.
DEFAULT_FILL_COST_BPS: Decimal = Decimal("5")


# ─────────────────────────────────────────────────────────────────────────────
# Shadow position bookkeeping
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class ShadowPosition:
    """One open simulated position for one candidate.

    Mutable on purpose — the harness updates `mark_price` every cycle so
    the JSON-serialised form reflects current mark-to-market. A
    `to_json_obj` round-trip preserves enter/hold/exit semantics, which
    is what the W5 state-machine reads to make promotion decisions.
    """

    asset: str
    size_asset: Decimal  # units of the underlying asset held (positive = long)
    entry_price: Decimal
    entry_time: datetime
    mark_price: Decimal

    @property
    def size_usd(self) -> Decimal:
        return self.size_asset * self.mark_price

    @property
    def unrealised_pnl_usd(self) -> Decimal:
        return self.size_asset * (self.mark_price - self.entry_price)

    def to_json_obj(self) -> dict[str, Any]:
        return {
            "asset": self.asset,
            "size_asset": str(self.size_asset),
            "entry_price": str(self.entry_price),
            "entry_time": self.entry_time.isoformat(),
            "mark_price": str(self.mark_price),
        }

    @classmethod
    def from_json_obj(cls, obj: Mapping[str, Any]) -> ShadowPosition:
        return cls(
            asset=str(obj["asset"]),
            size_asset=Decimal(str(obj["size_asset"])),
            entry_price=Decimal(str(obj["entry_price"])),
            entry_time=datetime.fromisoformat(str(obj["entry_time"])),
            mark_price=Decimal(str(obj["mark_price"])),
        )


@dataclass
class _CandidateBook:
    """Per-candidate accounting kept in memory across cycles.

    Persisted to Postgres each cycle as the canonical view; this is the
    in-process cache so we don't re-derive NAV history from the DB on
    every tick (and so the harness can be exercised in tests without a
    DB writer at all).
    """

    candidate_id: str
    template_id: str
    starting_cash_usd: Decimal
    cash_usd: Decimal
    positions: dict[str, ShadowPosition] = field(default_factory=dict)
    nav_history: list[float] = field(default_factory=list)
    entered_paper_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def nav_at(self, price_lookup: Mapping[str, Decimal]) -> Decimal:
        nav = self.cash_usd
        for pos in self.positions.values():
            mark = price_lookup.get(pos.asset, pos.mark_price)
            nav += pos.size_asset * mark
        return nav

    def positions_json(self) -> str:
        return json.dumps([p.to_json_obj() for p in self.positions.values()])

    def observation_days(self, now: datetime) -> int:
        delta = now - self.entered_paper_at
        return max(0, int(delta.total_seconds() // 86_400))


# ─────────────────────────────────────────────────────────────────────────────
# Metric helpers (mirror backtest-worker's reduction so paper ↔ backtest
# numbers are comparable for the 0.8x promotion gate).
# ─────────────────────────────────────────────────────────────────────────────


def _sharpe(nav_history: list[float], periods_per_year: int = 365) -> float | None:
    """Annualised Sharpe over the per-cycle return series.

    Returns None until we have ≥ 2 NAV points (one return), matching the
    Numeric-NULL convention the `paper_trade_state` column uses for
    "not enough data yet."
    """
    if len(nav_history) < 2:
        return None
    arr = np.asarray(nav_history, dtype=float)
    bases = np.where(arr[:-1] == 0, 1.0, arr[:-1])
    returns = np.diff(arr) / bases
    if returns.size < 2:
        return None
    std = float(np.std(returns, ddof=1))
    if std <= 0.0:
        return 0.0
    mean = float(np.mean(returns))
    return mean / std * math.sqrt(periods_per_year)


def _max_drawdown(nav_history: list[float]) -> float | None:
    """Max peak-to-trough drawdown as a non-negative fraction.

    `0.12` means a 12 percent drawdown from peak. Matches search.py's
    sign convention so the W5 state-machine's `observed_max_dd <= 1.2 *
    backtest_max_dd` predicate compares apples to apples.
    """
    if len(nav_history) < 2:
        return None
    arr = np.asarray(nav_history, dtype=float)
    peaks = np.maximum.accumulate(arr)
    safe_peaks = np.where(peaks == 0, 1.0, peaks)
    drawdowns = (arr - peaks) / safe_peaks
    return float(-np.min(drawdowns)) if drawdowns.size else 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Harness
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CycleOutcome:
    """Per-candidate result for one harness cycle — returned for tests
    and operator dashboards (the state-machine consumes the persisted
    row, not this object)."""

    candidate_id: str
    template_id: str
    decision: Decision | None
    nav_usd: Decimal
    observed_sharpe: float | None
    observed_max_dd: float | None
    error: str | None = None


class PaperTradeHarness:
    """Drives shadow execution of one or more candidates against live data.

    Wiring contract: the caller injects (a) a DataAdapter, (b) one
    Template per candidate, (c) per-candidate parameter dicts (parameters
    are extractor-emitted and live on the Candidate row, but the harness
    is parameter-agnostic so tests can pass arbitrary dicts).

    Constructor stays cheap. The first `run_cycle` populates the initial
    paper_trade_state row; subsequent cycles upsert.

    Why not a thread pool over candidates: a candidate's evaluate() is
    pure (AST-linted, no I/O) and cheap. The DB write is the only
    blocking call and we run it on the asyncio thread pool already. A
    per-candidate pool buys nothing and risks SQLite write contention in
    tests.
    """

    def __init__(
        self,
        *,
        adapter: DataAdapter,
        chain: Chain,
        candidates: Iterable[tuple[str, Template, Mapping[str, Any]]],
        db: DatabaseManager | None = None,
        starting_cash_usd: Decimal = Decimal("10_000"),
        fill_cost_bps: Decimal = DEFAULT_FILL_COST_BPS,
        now_fn: Any = None,
    ) -> None:
        self._adapter = adapter
        self._chain = chain
        self._db = db
        self._fill_cost_bps = Decimal(fill_cost_bps)
        self._now_fn = now_fn or (lambda: datetime.now(UTC))

        self._templates: dict[str, Template] = {}
        self._params: dict[str, Mapping[str, Any]] = {}
        self._books: dict[str, _CandidateBook] = {}

        now = self._now_fn()
        for candidate_id, template, params in candidates:
            self._templates[candidate_id] = template
            self._params[candidate_id] = dict(params)
            self._books[candidate_id] = _CandidateBook(
                candidate_id=candidate_id,
                template_id=template.id,
                starting_cash_usd=Decimal(starting_cash_usd),
                cash_usd=Decimal(starting_cash_usd),
                entered_paper_at=now,
            )

    # ── public API ─────────────────────────────────────────────────────

    @property
    def candidate_ids(self) -> tuple[str, ...]:
        return tuple(self._books.keys())

    def book(self, candidate_id: str) -> _CandidateBook:
        """Read-only handle for tests + state-machine queries."""
        return self._books[candidate_id]

    async def run_cycle(self) -> list[CycleOutcome]:
        """One end-to-end tick: fetch snapshot, evaluate, fill, persist.

        Returns one CycleOutcome per candidate, in registration order.
        Persists one `paper_trade_state` row per candidate (insert on
        first cycle, update on subsequent cycles).

        An `evaluate()` that raises is logged and does NOT break the
        cycle for other candidates — the harness records `error=str(exc)`
        on that candidate's CycleOutcome and treats the cycle as a HOLD
        (no position change, no NAV change beyond mark-to-market).
        """
        snapshot = await self._adapter.fetch_live(self._chain)
        outcomes: list[CycleOutcome] = []
        for candidate_id in self._books:
            outcomes.append(await self._step_one(candidate_id, snapshot))
        return outcomes

    async def run_n_cycles(self, n: int) -> list[list[CycleOutcome]]:
        """Convenience for tests + the lake-governor scheduler shim."""
        if n < 0:
            raise ValueError(f"n must be >= 0, got {n}")
        return [await self.run_cycle() for _ in range(n)]

    # ── per-candidate step ─────────────────────────────────────────────

    async def _step_one(
        self, candidate_id: str, snapshot: MarketSnapshot
    ) -> CycleOutcome:
        book = self._books[candidate_id]
        template = self._templates[candidate_id]
        params = self._params[candidate_id]

        # Mark every open position to the snapshot's quoted price (or
        # leave at last-known mark if the snapshot lacks that asset).
        for pos in book.positions.values():
            quoted = snapshot.prices.get(pos.asset)
            if quoted is not None:
                pos.mark_price = Decimal(quoted)

        portfolio = self._build_portfolio_snapshot(book, snapshot)
        nav_before = portfolio.nav_usd
        book.nav_history.append(float(nav_before))

        decision: Decision | None = None
        error: str | None = None
        try:
            decision = template.evaluate(dict(params), snapshot, portfolio)
        except Exception as exc:  # candidate isolation: must not break the cycle
            error = f"{type(exc).__name__}: {exc}"
            _logger.error(
                "paper_trade_evaluate_raised",
                candidate_id=candidate_id,
                template_id=template.id,
                error=error,
            )

        if decision is not None and error is None:
            try:
                self._apply_decision(book, decision, snapshot)
            except Exception as exc:
                # Fill simulation should be total; if it isn't, surface
                # the bug without killing the cycle.
                error = f"fill_error:{type(exc).__name__}: {exc}"
                _logger.error(
                    "paper_trade_fill_raised",
                    candidate_id=candidate_id,
                    template_id=template.id,
                    error=error,
                )

        # Recompute NAV after any fills (cash + new mark-to-market).
        nav_after = book.nav_at(snapshot.prices)
        sharpe = _sharpe(book.nav_history)
        max_dd = _max_drawdown(book.nav_history)

        await self._persist(book, sharpe, max_dd)

        _logger.info(
            "paper_trade_cycle",
            candidate_id=candidate_id,
            template_id=template.id,
            decision=decision.action if decision else None,
            nav_usd=str(nav_after),
            observed_sharpe=sharpe,
            observed_max_dd=max_dd,
            open_positions=len(book.positions),
            error=error,
        )

        return CycleOutcome(
            candidate_id=candidate_id,
            template_id=template.id,
            decision=decision,
            nav_usd=nav_after,
            observed_sharpe=sharpe,
            observed_max_dd=max_dd,
            error=error,
        )

    # ── helpers ────────────────────────────────────────────────────────

    def _build_portfolio_snapshot(
        self, book: _CandidateBook, snapshot: MarketSnapshot
    ) -> PortfolioSnapshot:
        """Build the immutable PortfolioSnapshot the evaluate() contract
        expects. We re-derive on every call so the candidate sees the
        same frozen shape the backtest engine handed it during search.
        """
        positions: dict[str, Position] = {}
        for asset, pos in book.positions.items():
            positions[asset] = Position(
                candidate_id=book.candidate_id,
                template_id=book.template_id,
                asset=pos.asset,
                size_usd=pos.size_usd,
                entry_price=pos.entry_price,
                entry_time=pos.entry_time,
            )
        nav = book.nav_at(snapshot.prices)
        # Drawdown-from-peak is a portfolio-level signal the candidate
        # may consult; compute it on the fly from nav_history.
        peak = max((*book.nav_history, float(nav)), default=float(nav))
        dd = Decimal("0") if peak <= 0 else (Decimal(str(peak)) - nav) / Decimal(str(peak))
        return PortfolioSnapshot(
            nav_usd=nav,
            positions=positions,
            cash_usd=book.cash_usd,
            drawdown_from_peak=dd,
            last_rebalance=snapshot.timestamp,
        )

    def _apply_decision(
        self,
        book: _CandidateBook,
        decision: Decision,
        snapshot: MarketSnapshot,
    ) -> None:
        """Translate a Decision into a fill against the snapshot.

        v1 semantics:
          - 'enter': open a position on the first asset in `snapshot.prices`.
            target_size is interpreted as USD notional and capped at cash.
          - 'exit':  liquidate every open position to cash.
          - 'rebalance': treated as exit-then-enter at target_size.
          - 'hold':  no-op.

        The "first asset" rule mirrors backtest-worker.search._simulate_one
        and is honest for single-asset templates (the majority for W5).
        Multi-asset templates need a richer Decision contract — that's a
        W7 follow-up.
        """
        if decision.action == "hold":
            return

        cost_factor = Decimal("1") - (self._fill_cost_bps / Decimal("10000"))

        asset = next(iter(snapshot.prices.keys()), None)
        if asset is None:
            return
        price = Decimal(snapshot.prices[asset])
        if price <= 0:
            return

        if decision.action == "exit":
            self._close_all(book, snapshot.prices, cost_factor)
            return

        if decision.action == "rebalance":
            self._close_all(book, snapshot.prices, cost_factor)
            # fall through to enter

        # 'enter' or 'rebalance' tail
        target_usd = Decimal(str(decision.target_size))
        if target_usd <= 0:
            return
        spend = min(target_usd, book.cash_usd)
        if spend <= 0:
            return
        # Pay the fill cost on entry: you spend `spend` cash, but the
        # position is sized as if you got `spend * cost_factor` of asset.
        effective_usd = spend * cost_factor
        size_asset = effective_usd / price
        book.cash_usd -= spend
        # If we already had a position in this asset, average in.
        existing = book.positions.get(asset)
        if existing is not None:
            total_size = existing.size_asset + size_asset
            if total_size > 0:
                blended_entry = (
                    existing.size_asset * existing.entry_price + size_asset * price
                ) / total_size
            else:
                blended_entry = price
            existing.size_asset = total_size
            existing.entry_price = blended_entry
            existing.mark_price = price
        else:
            book.positions[asset] = ShadowPosition(
                asset=asset,
                size_asset=size_asset,
                entry_price=price,
                entry_time=snapshot.timestamp,
                mark_price=price,
            )

    @staticmethod
    def _close_all(
        book: _CandidateBook,
        prices: Mapping[str, Decimal],
        cost_factor: Decimal,
    ) -> None:
        for asset, pos in list(book.positions.items()):
            mark = Decimal(prices.get(asset, pos.mark_price))
            proceeds = pos.size_asset * mark * cost_factor
            book.cash_usd += proceeds
            del book.positions[asset]

    # ── persistence (sync inside asyncio.to_thread per W2 pattern) ────

    async def _persist(
        self,
        book: _CandidateBook,
        observed_sharpe: float | None,
        observed_max_dd: float | None,
    ) -> None:
        if self._db is None:
            return
        await asyncio.to_thread(
            self._persist_sync, book, observed_sharpe, observed_max_dd
        )

    def _persist_sync(
        self,
        book: _CandidateBook,
        observed_sharpe: float | None,
        observed_max_dd: float | None,
    ) -> None:
        """Upsert one paper_trade_state row.

        SQLAlchemy 1.x-style sync session, matching extractor-worker's
        writer.py. We use a get-or-insert pattern instead of dialect-
        specific UPSERT to stay portable between SQLite (tests) and
        Postgres (prod).
        """
        assert self._db is not None
        now = self._now_fn()
        with self._db.get_session() as session:
            row = (
                session.query(PaperTradeState)
                .filter_by(candidate_id=book.candidate_id)
                .one_or_none()
            )
            payload = {
                "shadow_positions_json": book.positions_json(),
                "observed_sharpe": observed_sharpe,
                "observed_max_dd": observed_max_dd,
                "observation_days": book.observation_days(now),
                "last_updated_at": now,
            }
            if row is None:
                row = PaperTradeState(
                    candidate_id=book.candidate_id,
                    entered_paper_at=book.entered_paper_at,
                    **payload,
                )
                session.add(row)
            else:
                for k, v in payload.items():
                    setattr(row, k, v)
            try:
                session.commit()
            except Exception:
                session.rollback()
                raise
