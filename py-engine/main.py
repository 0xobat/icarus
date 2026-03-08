"""Icarus Python engine — decision-making service.

Main decision loop: subscribe market:events → enrich data → synthesize
insights → decide (deterministic fast-path or Claude API) → risk gate →
emit execution:orders → process execution:results → update portfolio.
"""

from __future__ import annotations

import os
import signal
import sys
import time
import uuid
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from ai.decision_engine import Decision, DecisionAction, DecisionEngine
from ai.insight_synthesis import InsightSynthesizer
from data.gas_monitor import GasMonitor
from data.price_feed import PriceFeedManager
from data.redis_client import CHANNELS, RedisManager
from db.database import DatabaseConfig, DatabaseManager
from db.repository import DatabaseRepository
from harness.hold_mode import HoldMode
from harness.startup_recovery import FullRecoveryResult, run_startup_recovery
from harness.state_manager import StateManager
from monitoring.logger import get_logger
from portfolio.allocator import PortfolioAllocator
from portfolio.position_tracker import PositionTracker
from portfolio.rebalancer import PortfolioRebalancer
from risk.drawdown_breaker import DrawdownBreaker
from risk.exposure_limits import ExposureLimiter
from risk.exposure_limits import load_config as load_exposure_config
from risk.gas_spike_breaker import GasSpikeBreaker
from risk.oracle_guard import OracleGuard
from risk.position_loss_limit import PositionLossLimit
from risk.tvl_monitor import TVLMonitor
from risk.tx_failure_monitor import TxFailureMonitor
from strategies.base import (
    GasInfo,
    MarketSnapshot,
    PoolState,
    Strategy,
    StrategyReport,
    TokenPrice,
)
from strategies.lifecycle_manager import LifecycleManager

_logger = get_logger("main", enable_file=False)

_shutdown = False


def _handle_signal(sig: int, _frame: object) -> None:
    global _shutdown  # noqa: PLW0603
    _shutdown = True
    _logger.info("Shutdown signal received", extra={"data": {"signal": sig}})


class DecisionLoop:
    """Orchestrates the full Icarus decision cycle.

    Wires together data pipeline, insight synthesis, AI decision engine,
    risk circuit breakers, and portfolio management into a single loop
    that processes market events and emits execution orders.

    Args:
        redis: Redis connection manager.
        db_manager: Database connection manager.
        repository: Database repository for trade/state persistence.
        state: State manager for operational persistence.
    """

    def __init__(
        self,
        redis: RedisManager,
        db_manager: DatabaseManager,
        repository: DatabaseRepository,
        state: StateManager,
    ) -> None:
        self.redis = redis
        self.db = db_manager
        self.repository = repository
        self.state = state

        # Data pipeline
        self.price_feed = PriceFeedManager(redis=redis)
        self.gas_monitor = GasMonitor(redis=redis)

        # Portfolio
        total_capital = Decimal(os.environ.get("TOTAL_CAPITAL", "10000"))
        self.allocator = PortfolioAllocator(
            total_capital,
        )
        self.tracker = self._load_positions()
        self.rebalancer = PortfolioRebalancer()

        # Hold mode
        self.hold_mode = HoldMode()

        # Risk circuit breakers
        self.drawdown = DrawdownBreaker(initial_value=total_capital)
        self.position_loss = PositionLossLimit(redis=redis)
        self.gas_spike = GasSpikeBreaker()
        self.tx_failures = TxFailureMonitor(hold_mode=self.hold_mode)

        # Exposure limits (env var configured)
        self.exposure = ExposureLimiter(
            total_capital=total_capital,
            config=load_exposure_config(),
        )

        # TVL monitor circuit breaker
        self.tvl_monitor = TVLMonitor()

        # Oracle manipulation guard
        self.oracle_guard = OracleGuard(self.price_feed)

        # Data pipeline
        from data.defi_metrics import DeFiMetricsCollector
        self.defi_metrics = DeFiMetricsCollector(redis)

        # Strategy lifecycle
        self.lifecycle = LifecycleManager(state)

        # AI
        self.synthesizer = InsightSynthesizer(
            price_feed=self.price_feed,
            gas_monitor=self.gas_monitor,
            defi_metrics=self.defi_metrics,
            position_tracker=self.tracker,
            lifecycle_manager=self.lifecycle,
            drawdown=self.drawdown,
            gas_spike=self.gas_spike,
            tx_failures=self.tx_failures,
            position_loss=self.position_loss,
            tvl_monitor=self.tvl_monitor,
            hold_mode=self.hold_mode,
        )
        self.decision_engine = DecisionEngine()

        self._adjustment_made = False
        self._cycle_count = 0
        self._trim_interval = int(os.environ.get("STREAM_TRIM_INTERVAL_CYCLES", "100"))

        # Strategy evaluation state (INFRA-006)
        self._strategies: dict[str, Strategy] = {}
        self._latest_reports: dict[str, StrategyReport] = {}
        self._last_evaluated: dict[str, datetime] = {}

    def _load_positions(self) -> PositionTracker:
        """Load positions from PostgreSQL into a new tracker.

        Falls back to an empty tracker if the database load fails.

        Returns:
            A PositionTracker populated from PostgreSQL.
        """
        try:
            tracker = PositionTracker.from_database(self.repository)
            _logger.info("Positions loaded from PostgreSQL on startup")
            return tracker
        except Exception:
            _logger.exception("Failed to load positions from PostgreSQL — starting empty")
            return PositionTracker(repository=self.repository)

    def startup_recovery(self) -> FullRecoveryResult:
        """Run the startup recovery sequence before entering the main loop.

        Loads state from PostgreSQL, replays unprocessed Redis Stream
        messages, reconciles on-chain positions, and performs health checks.
        Enters hold mode if any critical step fails.

        Returns:
            FullRecoveryResult summarizing all recovery steps.
        """
        return run_startup_recovery(
            redis=self.redis,
            db_manager=self.db,
            repository=self.repository,
            hold_mode=self.hold_mode,
            position_tracker=self.tracker,
        )

    def register_strategy(self, strategy: Strategy) -> None:
        """Register a strategy instance for evaluation in the decision loop.

        Args:
            strategy: A Strategy-conforming instance.
        """
        self._strategies[strategy.strategy_id] = strategy

    def _evaluate_strategies(
        self, prices: dict[str, Any], gas: Any,
    ) -> list[StrategyReport]:
        """Evaluate strategies whose eval_interval has elapsed.

        Builds a MarketSnapshot from current data and calls evaluate()
        on each due strategy.

        Args:
            prices: Raw price data from PriceFeedManager.
            gas: Gas data from GasMonitor.

        Returns:
            List of new StrategyReports produced this cycle.
        """
        if not self._strategies:
            return []

        now = datetime.now(UTC)

        # Build MarketSnapshot for strategy consumption
        token_prices = [
            TokenPrice(
                token=token,
                price=float(data.get("price_usd", 0)) if isinstance(data, dict) else 0.0,
                source="aggregated",
                timestamp=now,
            )
            for token, data in prices.items()
        ]

        gas_info = GasInfo(
            current_gwei=float(getattr(gas, "standard", 30)) if gas else 30.0,
            avg_24h_gwei=float(self.gas_monitor.get_rolling_average() or 30),
        )

        pools: list[PoolState] = []
        for protocol in ("aave", "aerodrome"):
            try:
                metrics = self.defi_metrics.get_metrics(protocol)
                if metrics and isinstance(metrics, dict):
                    for market in metrics.get("markets", []):
                        pools.append(PoolState(
                            protocol=protocol,
                            pool_id=market.get("symbol", "unknown"),
                            tvl=float(market.get("tvl", 0)),
                            apy=float(market.get("supply_apy", 0)),
                            utilization=(
                                float(market["utilization_rate"])
                                if "utilization_rate" in market else None
                            ),
                        ))
            except Exception:
                pass

        snapshot = MarketSnapshot(
            prices=token_prices,
            gas=gas_info,
            pools=pools,
            timestamp=now,
        )

        reports: list[StrategyReport] = []
        for sid, strategy in self._strategies.items():
            last = self._last_evaluated.get(sid)
            if last is not None and (now - last) < strategy.eval_interval:
                continue
            try:
                report = strategy.evaluate(snapshot)
                self._last_evaluated[sid] = now
                reports.append(report)
                _logger.info(
                    "Strategy evaluated",
                    extra={"data": {"strategy_id": sid}},
                )
            except Exception as e:
                _logger.warning(
                    "Strategy evaluation failed",
                    extra={"data": {"strategy_id": sid, "error": str(e)}},
                )

        return reports

    def run_cycle(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        """Execute one decision cycle for a market event.

        Pipeline: enrich → synthesize → decide → risk gate → emit orders.
        Enforces one strategy adjustment per cycle.

        Args:
            event: A market:events message from Redis.

        Returns:
            List of execution:orders to emit (may be empty).
        """
        correlation_id = event.get("correlationId", uuid.uuid4().hex)
        self._cycle_count += 1
        self._adjustment_made = False

        _logger.info(
            "Decision cycle started",
            extra={"data": {
                "cycle": self._cycle_count,
                "event_type": event.get("eventType"),
                "correlationId": correlation_id,
            }},
        )

        # 1. Enrich — update price and gas caches
        prices = self.price_feed.fetch_prices()
        gas = self.gas_monitor.update()

        # Update position values from latest prices
        price_map = {
            token: Decimal(str(data.get("price_usd", 0)))
            for token, data in prices.items()
        }
        self.tracker.update_prices(price_map)

        # Check per-position loss limits (circuit breaker — direct emission)
        loss_orders = self.position_loss.generate_close_orders(
            positions=self.tracker.query(),
            price_map=price_map,
            correlation_id=correlation_id,
        )
        if loss_orders:
            _logger.warning(
                "Position loss limit triggered",
                extra={"data": {"order_count": len(loss_orders)}},
            )
            return loss_orders

        # 2. Check circuit breakers before any decision
        portfolio_value = Decimal(
            self.tracker.get_summary().get("total_value", "0"),
        )
        if portfolio_value > 0:
            dd_state = self.drawdown.update(portfolio_value)
            if self.drawdown.should_unwind_all():
                _logger.warning(
                    "Drawdown breaker triggered — unwinding all",
                    extra={"data": {"drawdown_pct": str(dd_state.drawdown_pct)}},
                )
                return self._emit_unwind_orders(correlation_id)

        # TVL monitor — feed data and check for critical drops
        for protocol_key in ("aave", "aerodrome"):
            tvl_result = self.defi_metrics.fetch_tvl(protocol_key)
            if tvl_result is not None:
                self.tvl_monitor.record_tvl(
                    protocol=protocol_key,
                    chain="base",
                    tvl_usd=Decimal(str(tvl_result.tvl_usd)),
                    source="defi_metrics",
                )

        tvl_orders = self.tvl_monitor.generate_withdrawal_orders(
            positions=self.tracker.query(),
            correlation_id=correlation_id,
        )
        if tvl_orders:
            _logger.warning(
                "TVL drop breaker triggered",
                extra={"data": {"order_count": len(tvl_orders)}},
            )
            return tvl_orders

        if gas is not None:
            self.gas_spike.update(
                current_gas=Decimal(str(gas.standard)),
                average_gas=self.gas_monitor.get_rolling_average() or Decimal("30"),
            )

        if not self.tx_failures.can_execute():
            _logger.warning("TX failure breaker active — skipping cycle")
            return []

        # 3. Synthesize insights
        snapshot = self.synthesizer.synthesize()
        snapshot_dict = snapshot.to_dict()
        snapshot_dict["correlationId"] = correlation_id
        snapshot_dict["market_event"] = event

        # 3b. Evaluate portfolio drift and include rebalance signals
        current_allocs = self.allocator.get_current_allocations()
        target_allocs = self.allocator.get_target_allocations()
        rebalance_report = self.rebalancer.evaluate(
            current_allocations=current_allocs,
            target_allocations=target_allocs,
            total_value_usd=portfolio_value,
        )
        snapshot_dict["rebalance_report"] = rebalance_report

        # Surface actionable rebalance signals for the decision gate
        for sig in rebalance_report.get("signals", []):
            if sig.get("actionable"):
                active_signals = snapshot_dict.get("active_signals")
                if not isinstance(active_signals, list):
                    active_signals = []
                    snapshot_dict["active_signals"] = active_signals
                active_signals.append({
                    "type": sig["type"],
                    "strategy_id": "rebalancer",
                    "details": sig.get("details", ""),
                    "parameters": (
                        rebalance_report.get("recommendation", {})
                        .get("parameters", {})
                    ),
                })

        # 3c. Evaluate strategies and accumulate reports
        strategy_reports = self._evaluate_strategies(prices, gas)
        for report in strategy_reports:
            self._latest_reports[report.strategy_id] = report

        # Include all accumulated reports in snapshot for Claude
        if self._latest_reports:
            snapshot_dict["strategy_reports"] = [
                asdict(r) for r in self._latest_reports.values()
            ]

        # 4. Decide — decision gate based on strategy reports
        decision = self._decide(snapshot_dict)

        if decision.action == DecisionAction.HOLD:
            _logger.debug("Decision: HOLD", extra={"data": {
                "reason": decision.reasoning,
            }})
            return []

        # 5. Risk gate — every non-hold decision must pass
        orders = self._apply_risk_gate(decision, correlation_id)

        # 6. Record decision in audit log
        self._record_decision(correlation_id, decision, orders)

        # 7. Record decision for future insight synthesis
        self.synthesizer.record_decision(decision.to_dict())

        # Periodic stream maintenance
        if self._cycle_count % self._trim_interval == 0:
            self._trim_streams()

        return orders

    def _trim_streams(self) -> None:
        """Trim all Redis streams to configured max length."""
        max_len = self.redis._stream_max_len
        channels = (
            CHANNELS["MARKET_EVENTS"],
            CHANNELS["EXECUTION_ORDERS"],
            CHANNELS["EXECUTION_RESULTS"],
        )
        for channel in channels:
            try:
                self.redis.stream_trim(channel, max_len)
            except Exception:
                _logger.debug("Stream trim failed for %s", channel)

    def _decide(self, snapshot: dict[str, Any]) -> Decision:
        """Decision gate — opens only when actionable signals exist.

        Hold mode keeps the gate closed regardless. When open, uses
        deterministic fast-path for simple cases, Claude API for complex.

        Args:
            snapshot: The insight snapshot with market context.

        Returns:
            A Decision object with action and reasoning.
        """
        # Hold mode: gate stays closed regardless of signals
        if self.hold_mode.is_active():
            return Decision(
                action=DecisionAction.HOLD,
                strategy="system",
                reasoning="Hold mode active — gate closed",
                confidence=1.0,
            )

        # Decision gate: check strategy reports for actionable signals
        has_actionable_report = any(
            any(sig.actionable for sig in report.signals)
            for report in self._latest_reports.values()
        )

        # Also check rebalancer active_signals (non-strategy signals)
        active_signals = snapshot.get("active_signals", [])
        signal_count = len(active_signals) if isinstance(active_signals, list) else 0

        # Gate closed: no actionable signals from any source
        if not has_actionable_report and signal_count == 0:
            return Decision(
                action=DecisionAction.HOLD,
                strategy="system",
                reasoning="No actionable signals",
                confidence=1.0,
            )

        # Fast-path: single clear signal with high urgency
        if signal_count == 1 and isinstance(active_signals[0], dict):
            sig = active_signals[0]
            if sig.get("urgency", "low") == "critical":
                return Decision(
                    action=DecisionAction.ADJUST,
                    strategy=sig.get("strategy_id", "unknown"),
                    reasoning=f"Critical signal: {sig.get('type', 'unknown')}",
                    confidence=0.9,
                    params=sig.get("parameters", {}),
                )

        # Complex situation → Claude API reasoning
        if self.decision_engine.cost_tracker.is_budget_exhausted():
            return Decision(
                action=DecisionAction.HOLD,
                strategy="system",
                reasoning="AI budget exhausted — defaulting to hold",
                confidence=0.5,
            )

        return self.decision_engine.decide(snapshot)

    def _apply_risk_gate(
        self, decision: Decision, correlation_id: str,
    ) -> list[dict[str, Any]]:
        """Validate a decision against all circuit breakers.

        Non-negotiable: every non-hold decision passes through all risk
        checks. One adjustment per cycle enforced.

        Args:
            decision: The decision to validate.
            correlation_id: Correlation ID for tracing.

        Returns:
            List of approved orders (may be empty if risk blocked).
        """
        # One adjustment per cycle
        if self._adjustment_made:
            _logger.info("Adjustment already made this cycle — skipping")
            return []

        # Gas spike check
        if not self.gas_spike.is_operation_allowed("trade"):
            _logger.warning("Gas spike breaker — blocking trade")
            return []

        # Drawdown check
        if not self.drawdown.can_open_position():
            _logger.warning("Drawdown breaker — blocking new positions")
            return []

        # TX failure check
        if not self.tx_failures.can_execute():
            _logger.warning("TX failure monitor — blocking execution")
            return []

        # Oracle manipulation guard
        oracle_result = self.oracle_guard.check()
        if not oracle_result.safe:
            _logger.warning(
                "Oracle guard — blocking order",
                extra={"data": {
                    "reason": oracle_result.reason,
                    "deviations": self.oracle_guard.get_deviations(),
                    "stale": oracle_result.stale,
                }},
            )
            return []

        # Exposure limit check
        exposure_result = self._check_exposure(decision)
        if exposure_result is not None:
            return exposure_result

        self._adjustment_made = True

        _logger.info(
            "Decision approved by risk gate",
            extra={"data": {
                "action": decision.action,
                "strategy": decision.strategy,
                "confidence": str(decision.confidence),
            }},
        )

        # Convert decision to execution orders
        return self._decision_to_orders(decision, correlation_id)

    def _check_exposure(
        self, decision: Decision,
    ) -> list[dict[str, Any]] | None:
        """Check exposure limits for a decision.

        Updates the limiter with current positions and capital, then validates
        the proposed order. Returns empty list if blocked, None if OK.

        Args:
            decision: The decision to validate.

        Returns:
            Empty list if exposure blocked, None if check passed.
        """
        params = decision.params or {}
        protocol = params.get("protocol", decision.strategy or "unknown")
        asset = params.get("asset", "")
        value_usd = params.get("value_usd", 0)

        if not asset or not value_usd:
            return None  # Cannot check without order details; allow through

        # Sync limiter state with current portfolio
        positions = self.tracker.query()
        pos_dict = {
            p.get("id", f"pos_{i}"): {
                "value_usd": p.get("current_value", p.get("value_usd", 0)),
                "protocol": p.get("protocol", "unknown"),
                "asset": p.get("asset", "unknown"),
            }
            for i, p in enumerate(positions)
        }
        self.exposure.update_positions(pos_dict)
        portfolio_value = Decimal(
            self.tracker.get_summary().get("total_value", "0"),
        )
        if portfolio_value > 0:
            self.exposure.update_capital(portfolio_value)

        order = {"value_usd": value_usd, "protocol": protocol, "asset": asset}
        result = self.exposure.check_order(order)
        if not result.allowed:
            _logger.warning(
                "Exposure limit breaker — blocking order",
                extra={"data": {
                    "reason": result.reason,
                    "limit_type": result.limit_type,
                }},
            )
            return []

        return None

    def _decision_to_orders(
        self, decision: Decision, correlation_id: str,
    ) -> list[dict[str, Any]]:
        """Convert a Decision into schema-compliant execution:orders.

        Args:
            decision: The approved decision.
            correlation_id: Correlation ID for tracing.

        Returns:
            List of order dicts ready for Redis publication.
        """
        if not decision.params:
            return []

        params = decision.params
        return [{
            "version": "1.0.0",
            "orderId": uuid.uuid4().hex,
            "correlationId": correlation_id,
            "timestamp": __import__("datetime").datetime.now(
                __import__("datetime").UTC,
            ).isoformat(),
            "chain": params.get("chain", "ethereum"),
            "protocol": params.get("protocol", "unknown"),
            "action": params.get("action", decision.action),
            "strategy": decision.strategy or "unknown",
            "priority": params.get("priority", "normal"),
            "params": {
                k: v for k, v in params.items()
                if k not in {"chain", "protocol", "action", "priority"}
            },
            "limits": params.get("limits", {
                "maxGasWei": "500000000000000",
                "maxSlippageBps": 50,
                "deadlineUnix": int(time.time()) + 300,
            }),
        }]

    def _emit_unwind_orders(
        self, correlation_id: str,
    ) -> list[dict[str, Any]]:
        """Generate withdrawal orders for all open positions.

        Args:
            correlation_id: Correlation ID for tracing.

        Returns:
            List of withdrawal orders for every open position.
        """
        return self.drawdown.get_unwind_orders(
            positions=self.tracker.query(),
            correlation_id=correlation_id,
        )

    def process_result(self, result: dict[str, Any]) -> None:
        """Process an execution:results message.

        Updates position tracker and records TX success/failure for
        the failure monitor breaker.

        Args:
            result: An execution:results message from Redis.
        """
        self.tracker.on_execution_result(result)

        status = result.get("status", "")
        tx_id = result.get("orderId", "unknown")

        if status == "confirmed":
            self.tx_failures.record_success(tx_id)
        elif status == "failed":
            self.tx_failures.record_failure(
                tx_id=tx_id,
                reason=result.get("reason", "revert"),
                details=result.get("error", ""),
            )

        # Persist trade to PostgreSQL
        self._record_trade(result, status)

        _logger.info(
            "Execution result processed",
            extra={"data": {
                "orderId": tx_id,
                "status": status,
            }},
        )

    def persist_state(self) -> None:
        """Save all operational state for recovery."""
        try:
            self.tracker.sync_all_to_db()
        except Exception:
            _logger.exception("Failed to sync positions to PostgreSQL on shutdown")
        self.state.save()
        _logger.debug("State persisted")

    def _record_trade(self, result: dict[str, Any], status: str) -> None:
        """Persist an execution result as a trade record in PostgreSQL.

        Args:
            result: The execution result message.
            status: The transaction status (confirmed/failed).
        """
        try:
            params = result.get("params", {})
            self.repository.record_trade({
                "trade_id": result.get("orderId", uuid.uuid4().hex),
                "correlation_id": result.get("correlationId", ""),
                "strategy": result.get("strategy", "unknown"),
                "protocol": result.get("protocol", "unknown"),
                "chain": result.get("chain", "base"),
                "action": result.get("action", "unknown"),
                "asset_in": params.get("tokenIn", "unknown"),
                "amount_in": params.get("amount", "0"),
                "tx_hash": result.get("txHash"),
                "gas_used": result.get("gasUsed"),
                "status": status,
                "error_message": result.get("error"),
            })
        except Exception:
            _logger.exception("Failed to record trade to PostgreSQL")

    def _record_decision(
        self,
        correlation_id: str,
        decision: Decision,
        orders: list[dict[str, Any]],
    ) -> None:
        """Persist a decision cycle to the audit log in PostgreSQL.

        Args:
            correlation_id: Correlation ID for tracing.
            decision: The decision that was made.
            orders: The orders produced (may be empty if risk-blocked).
        """
        try:
            self.repository.record_decision({
                "correlation_id": correlation_id,
                "decision_action": decision.action.value
                if hasattr(decision.action, "value") else str(decision.action),
                "reasoning": decision.reasoning,
                "orders": orders,
                "passed_verification": len(orders) > 0,
            })
        except Exception:
            _logger.exception("Failed to record decision to PostgreSQL")


def _create_components() -> (
    tuple[RedisManager, DatabaseManager, DatabaseRepository, StateManager]
):
    """Initialize all infrastructure components.

    Returns:
        Tuple of (redis, db_manager, repository, state_manager).
    """
    redis = RedisManager(url=os.environ.get("REDIS_URL"))
    redis.connect()

    db_url = os.environ.get("DATABASE_URL")
    db_config = DatabaseConfig(url=db_url) if db_url else DatabaseConfig()
    db_manager = DatabaseManager(config=db_config)
    db_manager.create_tables()

    repository = DatabaseRepository(db_manager)
    state = StateManager()

    return redis, db_manager, repository, state


def main() -> None:
    """Run the Python engine main loop."""
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    _logger.info("Python engine starting")

    redis, db_manager, repository, state = _create_components()
    loop = DecisionLoop(redis, db_manager, repository, state)

    # Run startup recovery before entering main loop
    recovery_result = loop.startup_recovery()
    _logger.info(
        "Startup recovery complete",
        extra={"data": {
            "success": recovery_result.success,
            "hold_mode": recovery_result.entered_hold_mode,
        }},
    )

    # Subscribe to market events and execution results
    event_queue: list[dict[str, Any]] = []
    result_queue: list[dict[str, Any]] = []

    redis.subscribe("market:events", lambda msg: event_queue.append(msg))
    redis.subscribe("execution:results", lambda msg: result_queue.append(msg))

    _logger.info("Python engine ready — entering decision loop")

    try:
        while not _shutdown:
            # Process pending execution results
            while result_queue:
                loop.process_result(result_queue.pop(0))

            # Process pending market events
            while event_queue:
                event = event_queue.pop(0)
                orders = loop.run_cycle(event)
                for order in orders:
                    redis.publish("execution:orders", order)

            time.sleep(0.1)

    finally:
        _logger.info("Python engine shutting down")
        loop.persist_state()
        redis.disconnect()
        db_manager.close()
        _logger.info("Python engine stopped")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        _logger.error(
            "Fatal error",
            extra={"data": {"error": str(e)}},
        )
        sys.exit(1)
