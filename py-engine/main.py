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
from decimal import Decimal
from typing import Any

from ai.decision_engine import Decision, DecisionAction, DecisionEngine
from ai.insight_synthesis import InsightSynthesizer
from data.gas_monitor import GasMonitor
from data.price_feed import PriceFeedManager
from data.redis_client import CHANNELS, RedisManager
from db.database import DatabaseConfig, DatabaseManager
from db.repository import DatabaseRepository
from harness.state_manager import StateManager
from monitoring.logger import get_logger
from portfolio.allocator import AllocatorConfig, PortfolioAllocator
from portfolio.position_tracker import PositionTracker
from risk.drawdown_breaker import DrawdownBreaker
from risk.gas_spike_breaker import GasSpikeBreaker
from risk.tx_failure_monitor import TxFailureMonitor
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
            total_capital, {}, AllocatorConfig(),
        )
        self.tracker = PositionTracker()

        # Risk circuit breakers
        self.drawdown = DrawdownBreaker(initial_value=total_capital)
        self.gas_spike = GasSpikeBreaker()
        self.tx_failures = TxFailureMonitor()

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
        )
        self.decision_engine = DecisionEngine()

        self._adjustment_made = False
        self._cycle_count = 0
        self._trim_interval = int(os.environ.get("STREAM_TRIM_INTERVAL_CYCLES", "100"))

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

        # 4. Decide — deterministic fast-path or Claude API
        decision = self._decide(snapshot_dict)

        if decision.action == DecisionAction.HOLD:
            _logger.debug("Decision: HOLD", extra={"data": {
                "reason": decision.reasoning,
            }})
            return []

        # 5. Risk gate — every non-hold decision must pass
        orders = self._apply_risk_gate(decision, correlation_id)

        # 6. Record decision for future insight synthesis
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
        """Choose between deterministic fast-path and Claude API.

        Simple threshold crossings (clear signals, single strategy) bypass
        the Claude API. Ambiguous situations invoke the full AI engine.

        Args:
            snapshot: The insight snapshot with market context.

        Returns:
            A Decision object with action and reasoning.
        """
        signals = snapshot.get("active_signals", [])
        signal_count = len(signals) if isinstance(signals, list) else 0

        # Fast-path: no signals → hold
        if signal_count == 0:
            return Decision(
                action=DecisionAction.HOLD,
                strategy="system",
                reasoning="No active signals",
                confidence=1.0,
            )

        # Fast-path: single clear signal with high urgency
        if signal_count == 1 and isinstance(signals[0], dict):
            sig = signals[0]
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
            "useFlashbotsProtect": params.get("useFlashbotsProtect", False),
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

        _logger.info(
            "Execution result processed",
            extra={"data": {
                "orderId": tx_id,
                "status": status,
            }},
        )

    def persist_state(self) -> None:
        """Save all operational state for recovery."""
        self.state.save()
        _logger.debug("State persisted")


def _create_components() -> (
    tuple[RedisManager, DatabaseManager, DatabaseRepository, StateManager]
):
    """Initialize all infrastructure components.

    Returns:
        Tuple of (redis, db_manager, repository, state_manager).
    """
    redis = RedisManager(url=os.environ.get("REDIS_URL"))
    redis.connect()

    db_config = DatabaseConfig(url=os.environ.get("DATABASE_URL"))
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
