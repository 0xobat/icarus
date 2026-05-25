"""DecisionCycle — the per-tick orchestrator for the Execution cluster.

One tick (`run_one`) walks the blueprint's cycle in deterministic order:

  a. Pull MarketSnapshot for each live chain via injected `DataAdapter`.
  b. Read PortfolioSnapshot (positions + NAV) from the database.
  c. Run the rules-based regime classifier (synchronous, fast).
  d. Read live candidates from the RosterCache (LISTEN/NOTIFY-backed).
  e. For each candidate, call `template.evaluate(params, market, portfolio)`.
  f. Group decisions by template_id and pass to the allocator.
  g. For each non-zero target_size, build an ExecutionOrder envelope.
  h. Run each ExecutionOrder through the pre-trade risk gate.
  i. Publish surviving orders to `execution:orders:{chain}` via Redis.
  j. Return a CycleResult summarising counts.

Design discipline:

  * No global state — every collaborator is injected at construction so
    tests can drop in stubs that satisfy `lib.protocols.*`.
  * The LLM advisor (allocator commentary) is fire-and-forget per
    blueprint Q2: rules-based regime is primary, allocator computes
    allocations synchronously, the inference advisor enriches
    commentary asynchronously and never blocks the cycle.
  * Late-binding NotImplementedError when a Protocol impl is missing
    (Streams A/B/D ship in parallel) so a stub Allocator/Regime can be
    swapped in without code edits.
  * One strategy adjustment per decision cycle (CLAUDE.md convention):
    we emit at most one order per candidate per tick.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

import structlog
from icarus.db.models import PortfolioPosition
from icarus.envelopes.orders import ExecutionOrder, OrderLimits, OrderParams
from icarus.protocols.allocator import AllocationDecision, Allocator
from icarus.protocols.data import DataAdapter
from icarus.protocols.regime import RegimeClassifier
from icarus.types import (
    Decision,
    MarketSnapshot,
    PortfolioSnapshot,
    Position,
)
from icarus.types.market import Chain
from sqlalchemy import select

from decision_engine.risk_gate import RiskContext, RiskGate
from decision_engine.roster_listener import RosterCache, RosterEntry

if TYPE_CHECKING:  # pragma: no cover
    from icarus.db.database import DatabaseManager
    from icarus.dsl.registry import TemplateRegistry

logger = structlog.get_logger(service="decision-engine.cycle")

# Per-cycle default risk-gate bounds; the allocator/template can tighten
# these by overriding limits before risk_gate.check is called. Kept
# generous because the gate's job is hard-blocks, not micromanagement.
DEFAULT_MAX_SLIPPAGE_BPS = 50
DEFAULT_DEADLINE_SECONDS = 60


@runtime_checkable
class ExecutorPublisher(Protocol):
    """Async publisher of ExecutionOrders onto a chain-keyed channel.

    The concrete impl is a Redis client (`redis.asyncio.Redis.publish`)
    in production; the test fakes implement the same protocol surface
    so the cycle never knows it's not talking to Redis.
    """

    async def publish_order(self, chain: Chain, order: ExecutionOrder) -> None: ...


class RedisExecutorPublisher:
    """Production publisher: JSON-serialises into `execution:orders:{chain}`."""

    def __init__(self, redis_client: Any) -> None:
        self._redis = redis_client

    @staticmethod
    def _channel(chain: Chain) -> str:
        return f"execution:orders:{chain}"

    async def publish_order(self, chain: Chain, order: ExecutionOrder) -> None:
        # `model_dump_json` handles Decimal/datetime per the envelope's
        # pydantic config; consumers (ts-executor / solana-executor)
        # validate against the same JSON Schema.
        payload = order.model_dump_json()
        await self._redis.publish(self._channel(chain), payload)


@dataclass(frozen=True)
class CycleResult:
    """Summary of one cycle's work — fed to the structured tick log."""

    started_at: datetime
    finished_at: datetime
    correlation_id: str
    candidates_evaluated: int
    decisions_by_action: dict[str, int]
    orders_emitted: int
    orders_dropped: int
    drops_by_checker: dict[str, int]
    chains_processed: tuple[Chain, ...]
    allocator_mode: str
    advisor_commentary: str  # may be ADVISOR_ERROR_PREFIX-prefixed


@dataclass
class DecisionCycle:
    """One-tick orchestrator. Stateless across ticks — keep it that way.

    All collaborators are injected; nothing here owns I/O lifecycle
    (Redis/Postgres are owned by `__main__`). Tests pass stubs that
    satisfy the same Protocol surfaces.

    `chains` controls which chains the cycle drives per tick; defaults
    to ("base", "solana") matching the blueprint's live execution
    target set.
    """

    adapter: DataAdapter
    registry: TemplateRegistry
    allocator: Allocator
    regime_classifier: RegimeClassifier
    risk_gate: RiskGate
    executor_publisher: ExecutorPublisher
    db: DatabaseManager
    roster_cache: RosterCache
    chains: tuple[Chain, ...] = ("base", "solana")

    async def run_one(self) -> CycleResult:
        """Execute one decision tick end-to-end.

        Returns a `CycleResult` with the counters; the caller (the
        worker loop in `__main__`) emits one structured log line per
        tick using these counters.
        """
        started_at = datetime.now(UTC)
        correlation_id = f"cycle-{uuid.uuid4().hex[:12]}"
        log = logger.bind(correlation_id=correlation_id)

        # --- a. Market snapshots, one per chain --------------------------
        market_by_chain = await self._fetch_markets()
        if not market_by_chain:
            # Adapter rejected every chain. Surface and skip — risk gates
            # for absent data live with the adapter, not the cycle.
            log.warning("cycle_skipped_no_market_data")
            return CycleResult(
                started_at=started_at,
                finished_at=datetime.now(UTC),
                correlation_id=correlation_id,
                candidates_evaluated=0,
                decisions_by_action={},
                orders_emitted=0,
                orders_dropped=0,
                drops_by_checker={},
                chains_processed=(),
                allocator_mode="",
                advisor_commentary="",
            )

        # --- b. Portfolio snapshot ---------------------------------------
        portfolio = self._read_portfolio()

        # --- c. Regime classification (rules-based, per chain) -----------
        # Per blueprint Q2 the rules-based classifier is the primary
        # signal. We classify on the first available market snapshot
        # (Base is the default reference chain); cross-chain regime
        # is intentionally a Stream A refinement.
        primary_market = next(iter(market_by_chain.values()))
        try:
            regime = self.regime_classifier.classify(primary_market)
        except NotImplementedError as exc:
            raise NotImplementedError(
                f"RegimeClassifier impl '{getattr(self.regime_classifier, 'name', '?')}' "
                f"is a stub — Stream A must ship a real classify(): {exc}"
            ) from exc

        # --- d. Live candidates from the roster cache --------------------
        live_entries = self.roster_cache.live()
        log.debug("cycle_live_candidates", count=len(live_entries))

        # --- e. Per-candidate evaluate() ---------------------------------
        decisions: dict[str, Decision] = {}
        decisions_by_action: dict[str, int] = defaultdict(int)
        for entry in live_entries:
            decision = self._evaluate_candidate(
                entry=entry,
                market_by_chain=market_by_chain,
                portfolio=portfolio,
                log=log,
            )
            if decision is None:
                continue
            decisions[entry.candidate_id] = decision
            decisions_by_action[decision.action] += 1

        # --- f. Allocator over the decision set --------------------------
        try:
            allocation = self.allocator.allocate(
                candidate_decisions=decisions,
                portfolio=portfolio,
                regime=regime,
            )
        except NotImplementedError as exc:
            raise NotImplementedError(
                f"Allocator impl '{getattr(self.allocator, 'name', '?')}' is a stub "
                f"— Stream B must ship a real allocate(): {exc}"
            ) from exc

        # --- g/h/i. Build orders, run gate, publish ----------------------
        orders_emitted, orders_dropped, drops_by_checker = await self._publish_orders(
            allocation=allocation,
            decisions=decisions,
            live_entries=live_entries,
            market_by_chain=market_by_chain,
            portfolio=portfolio,
            correlation_id=correlation_id,
            log=log,
        )

        finished_at = datetime.now(UTC)
        result = CycleResult(
            started_at=started_at,
            finished_at=finished_at,
            correlation_id=correlation_id,
            candidates_evaluated=len(decisions),
            decisions_by_action=dict(decisions_by_action),
            orders_emitted=orders_emitted,
            orders_dropped=orders_dropped,
            drops_by_checker=drops_by_checker,
            chains_processed=tuple(market_by_chain.keys()),
            allocator_mode=allocation.mode,
            advisor_commentary=allocation.commentary,
        )
        log.info(
            "cycle_complete",
            duration_ms=int((finished_at - started_at).total_seconds() * 1000),
            evaluated=result.candidates_evaluated,
            emitted=result.orders_emitted,
            dropped=result.orders_dropped,
            allocator_mode=allocation.mode,
        )
        return result

    # ------------------------------------------------------------------
    # Step helpers — kept private; the public surface is `run_one`.
    # ------------------------------------------------------------------

    async def _fetch_markets(self) -> dict[Chain, MarketSnapshot]:
        """Pull a snapshot per chain; skip chains whose adapter fails.

        Adapter errors here are NOT fatal — a single chain outage must
        not stop trading on the healthy chain. The corresponding chain
        is excluded from this tick.
        """
        out: dict[Chain, MarketSnapshot] = {}
        for chain in self.chains:
            try:
                snapshot = await self.adapter.fetch_live(chain)
            except NotImplementedError as exc:
                raise NotImplementedError(
                    f"DataAdapter impl '{getattr(self.adapter, 'name', '?')}' "
                    f"missing fetch_live for chain {chain}: {exc}"
                ) from exc
            except Exception:
                logger.warning("market_fetch_failed", chain=chain, exc_info=True)
                continue
            out[chain] = snapshot
        return out

    def _read_portfolio(self) -> PortfolioSnapshot:
        """Read open positions from Postgres and assemble a PortfolioSnapshot.

        Cash + NAV + drawdown reconstruction belongs to a portfolio
        accountant (lib/icarus/portfolio in W7); for now the cycle reads
        what's available and the allocator treats missing fields as
        empty-portfolio defaults. The cycle never derives strategy
        decisions from these defaults — it just hands them through.
        """
        with self.db.get_session() as session:
            rows = (
                session.execute(
                    select(PortfolioPosition).where(PortfolioPosition.status == "open")
                )
                .scalars()
                .all()
            )

        positions: dict[str, Position] = {}
        nav = Decimal("0")
        for row in rows:
            cid = row.position_id  # the portfolio writer uses position_id == candidate_id
            tid = row.strategy
            current_value = Decimal(str(row.current_value))
            nav += current_value
            positions[cid] = Position(
                candidate_id=cid,
                template_id=tid,
                asset=row.asset,
                size_usd=current_value,
                entry_price=Decimal(str(row.entry_price)),
                entry_time=row.entry_time,
            )

        return PortfolioSnapshot(
            nav_usd=nav,
            positions=positions,
            cash_usd=Decimal("0"),  # W7 portfolio accountant fills this in
            drawdown_from_peak=Decimal("0"),
            last_rebalance=datetime.now(UTC),
        )

    def _evaluate_candidate(
        self,
        *,
        entry: RosterEntry,
        market_by_chain: dict[Chain, MarketSnapshot],
        portfolio: PortfolioSnapshot,
        log: Any,
    ) -> Decision | None:
        """Look up the template, route the right market snapshot, evaluate."""
        try:
            template = self.registry.by_id(entry.template_id)
        except KeyError:
            log.warning(
                "candidate_template_missing",
                candidate_id=entry.candidate_id,
                template_id=entry.template_id,
            )
            return None

        # The chain a template targets is on its manifest. We fall back
        # to base if the manifest type isn't accessible here; the
        # template's own evaluate() will surface mismatches.
        template_chain: Chain = getattr(template.manifest, "chain", "base")
        market = market_by_chain.get(template_chain)
        if market is None:
            log.debug(
                "candidate_chain_unavailable_this_tick",
                candidate_id=entry.candidate_id,
                chain=template_chain,
            )
            return None

        # Per-candidate params live in lake-governor's `candidates.params_json`.
        # The cycle does not own that read; it passes through an empty dict
        # if the caller hasn't pre-resolved params. (A small follow-up
        # refactor will pre-join params into RosterEntry, but that touches
        # the lake-governor seam — out of scope for this PR.)
        params: dict[str, Any] = {}
        try:
            decision = template.evaluate(params, market, portfolio)
        except Exception:
            log.exception(
                "candidate_evaluate_failed",
                candidate_id=entry.candidate_id,
                template_id=entry.template_id,
            )
            return None
        return decision

    async def _publish_orders(
        self,
        *,
        allocation: AllocationDecision,
        decisions: dict[str, Decision],
        live_entries: list[RosterEntry] | tuple[RosterEntry, ...],
        market_by_chain: dict[Chain, MarketSnapshot],
        portfolio: PortfolioSnapshot,
        correlation_id: str,
        log: Any,
    ) -> tuple[int, int, dict[str, int]]:
        """Materialise orders, run the gate, publish survivors."""

        entries_by_cid = {e.candidate_id: e for e in live_entries}
        orders_emitted = 0
        orders_dropped = 0
        drops_by_checker: dict[str, int] = defaultdict(int)
        now = datetime.now(UTC)
        deadline_unix = int(now.timestamp()) + DEFAULT_DEADLINE_SECONDS

        for candidate_id, target_usd in allocation.target_usd_by_candidate.items():
            if target_usd == 0:
                continue
            entry = entries_by_cid.get(candidate_id)
            decision = decisions.get(candidate_id)
            if entry is None or decision is None:
                # Allocator returned a candidate we don't have an entry/decision
                # for — defensive skip; the allocator contract says
                # `target_usd_by_candidate` keys are a subset of input keys.
                log.warning(
                    "allocation_for_unknown_candidate",
                    candidate_id=candidate_id,
                )
                continue

            template = self.registry.by_id(entry.template_id)
            chain: Chain = getattr(template.manifest, "chain", "base")
            market = market_by_chain.get(chain)
            if market is None:
                # Chain dropped out between evaluate and publish — skip.
                continue

            order = self._build_order(
                entry=entry,
                decision=decision,
                target_usd=Decimal(str(target_usd)),
                chain=chain,
                protocol=getattr(template.manifest, "protocol", "unknown"),
                correlation_id=correlation_id,
                timestamp=now,
                deadline_unix=deadline_unix,
            )

            ctx = RiskContext(portfolio=portfolio, market=market)
            verdict = self.risk_gate.check(order, ctx)
            if not verdict.passed:
                orders_dropped += 1
                drops_by_checker[verdict.checker] += 1
                continue

            await self.executor_publisher.publish_order(chain, order)
            orders_emitted += 1
            log.info(
                "order_published",
                order_id=order.order_id,
                chain=chain,
                template_id=entry.template_id,
                candidate_id=candidate_id,
                action=order.action,
                target_usd=str(target_usd),
            )

        return orders_emitted, orders_dropped, dict(drops_by_checker)

    @staticmethod
    def _build_order(
        *,
        entry: RosterEntry,
        decision: Decision,
        target_usd: Decimal,
        chain: Chain,
        protocol: str,
        correlation_id: str,
        timestamp: datetime,
        deadline_unix: int,
    ) -> ExecutionOrder:
        """Translate a (decision, allocation) pair into an `ExecutionOrder`.

        Order action is derived from the decision action:
          enter / rebalance → `swap` (broad protocol-agnostic default;
              executor-side adapters specialise per protocol).
          exit              → `withdraw`.
          hold              → never reaches here (target_usd would be 0).
        """
        action_map: dict[str, Any] = {
            "enter": "swap",
            "rebalance": "swap",
            "exit": "withdraw",
        }
        action = action_map.get(decision.action, "swap")
        strategy = f"{entry.template_id}:{entry.candidate_id}"
        order_id = uuid.uuid4().hex

        params = OrderParams(amount=target_usd.copy_abs())
        limits = OrderLimits(
            max_slippage_bps=DEFAULT_MAX_SLIPPAGE_BPS,
            deadline_unix=deadline_unix,
        )
        return ExecutionOrder(
            order_id=order_id,
            correlation_id=correlation_id,
            timestamp=timestamp,
            chain=chain,
            protocol=protocol,
            action=action,
            strategy=strategy,
            template_id=entry.template_id,
            candidate_id=entry.candidate_id,
            params=params,
            limits=limits,
        )


__all__ = [
    "CycleResult",
    "DecisionCycle",
    "ExecutorPublisher",
    "RedisExecutorPublisher",
]
