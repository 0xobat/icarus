"""Insight synthesis pipeline -- packages market data into Claude-ready snapshots (AI-003).

Collects latest data from all data sources (price feed, gas monitor, DeFi metrics,
position tracker), enriches with derived signals, compresses for token efficiency,
and validates the resulting snapshot schema before passing to the decision engine.
"""

from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from monitoring.logger import get_logger

_logger = get_logger("insight-synthesis", enable_file=False)

# ---------------------------------------------------------------------------
# Snapshot schema
# ---------------------------------------------------------------------------

SNAPSHOT_REQUIRED_FIELDS = frozenset({
    "market_data",
    "positions",
    "risk_status",
    "strategies",
    "recent_decisions",
})

_DEFAULT_DECISION_HISTORY_SIZE = 10


@dataclass
class InsightSnapshot:
    """Structured insight snapshot for Claude API consumption.

    Contains compressed market data, position summaries, risk status,
    active strategy specs, and recent decision history for context continuity.
    """

    market_data: dict[str, Any]
    positions: dict[str, Any]
    risk_status: dict[str, Any]
    strategies: list[dict[str, Any]]
    recent_decisions: list[dict[str, Any]]
    timestamp: str = ""
    snapshot_version: str = "1.0.0"

    def __post_init__(self) -> None:
        """Set timestamp if not provided."""
        if not self.timestamp:
            self.timestamp = datetime.now(UTC).isoformat()

    def to_dict(self) -> dict[str, Any]:
        """Return dictionary representation."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> InsightSnapshot:
        """Construct from a dictionary."""
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


def validate_snapshot(snapshot: dict[str, Any]) -> tuple[bool, list[str]]:
    """Validate a snapshot dict against the expected schema.

    Args:
        snapshot: Dictionary to validate.

    Returns:
        Tuple of (valid, list_of_errors).
    """
    errors: list[str] = []
    for req in SNAPSHOT_REQUIRED_FIELDS:
        if req not in snapshot:
            errors.append(f"Missing required field: {req}")
    if "market_data" in snapshot and not isinstance(snapshot["market_data"], dict):
        errors.append("market_data must be a dict")
    if "positions" in snapshot and not isinstance(snapshot["positions"], dict):
        errors.append("positions must be a dict")
    if "risk_status" in snapshot and not isinstance(snapshot["risk_status"], dict):
        errors.append("risk_status must be a dict")
    if "strategies" in snapshot and not isinstance(snapshot["strategies"], list):
        errors.append("strategies must be a list")
    if "recent_decisions" in snapshot and not isinstance(snapshot["recent_decisions"], list):
        errors.append("recent_decisions must be a list")
    return (len(errors) == 0, errors)


# ---------------------------------------------------------------------------
# Data compression helpers
# ---------------------------------------------------------------------------

def _compress_prices(prices: dict[str, Any]) -> dict[str, str]:
    """Compress price data to token-efficient summaries.

    Args:
        prices: Raw price data from PriceFeed.

    Returns:
        Compressed price summaries like "ETH: $3,200.00 (2 sources)".
    """
    compressed: dict[str, str] = {}
    for token, data in prices.items():
        if isinstance(data, dict):
            price = data.get("price_usd", 0)
            sources = data.get("sources", [])
            src_count = len(sources)
            src_label = "source" if src_count == 1 else "sources"
            compressed[token] = f"${price:,.2f} ({src_count} {src_label})"
        else:
            compressed[token] = str(data)
    return compressed


def _compress_gas(gas_data: dict[str, Any]) -> dict[str, str]:
    """Compress gas data to a concise summary.

    Args:
        gas_data: Gas price information.

    Returns:
        Compressed gas summary.
    """
    if not gas_data:
        return {"status": "unavailable"}
    return {
        "fast_gwei": str(gas_data.get("fast", "?")),
        "standard_gwei": str(gas_data.get("standard", "?")),
        "slow_gwei": str(gas_data.get("slow", "?")),
        "is_spike": str(gas_data.get("is_spike", "unknown")),
    }


def _compress_positions(positions_summary: dict[str, Any]) -> dict[str, Any]:
    """Compress position data for token efficiency.

    Args:
        positions_summary: Position tracker summary.

    Returns:
        Compressed position overview.
    """
    return {
        "open_count": positions_summary.get("open_count", 0),
        "total_value": positions_summary.get("total_value", "0"),
        "unrealized_pnl": positions_summary.get("total_unrealized_pnl", "0"),
        "realized_pnl": positions_summary.get("total_realized_pnl", "0"),
    }


def _compress_defi_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    """Compress DeFi protocol metrics.

    Args:
        metrics: Raw DeFi metrics from collector.

    Returns:
        Compressed protocol summaries.
    """
    compressed: dict[str, Any] = {}
    for protocol, data in metrics.items():
        if isinstance(data, dict):
            if "markets" in data:
                # Aave-style: summarize top markets
                markets = data["markets"]
                if markets:
                    top = sorted(markets, key=lambda m: m.get("supply_apy", 0), reverse=True)[:3]
                    compressed[protocol] = {
                        "market_count": len(markets),
                        "top_markets": [
                            {
                                "symbol": m.get("symbol", "?"),
                                "supply_apy": f"{m.get('supply_apy', 0):.2f}%",
                                "utilization": f"{m.get('utilization_rate', 0):.1f}%",
                            }
                            for m in top
                        ],
                    }
                else:
                    compressed[protocol] = {"market_count": 0}
            elif "pools" in data:
                # Uniswap-style
                pools = data["pools"]
                compressed[protocol] = {
                    "pool_count": len(pools),
                    "top_volume": [
                        {
                            "pair": p.get("pair", "?"),
                            "volume_24h": f"${p.get('volume_24h', 0):,.0f}",
                        }
                        for p in sorted(
                            pools, key=lambda p: p.get("volume_24h", 0), reverse=True,
                        )[:3]
                    ],
                }
            else:
                compressed[protocol] = data
        else:
            compressed[protocol] = str(data)
    return compressed


def _compute_rate_trends(
    current_metrics: dict[str, Any],
    previous_metrics: dict[str, Any] | None,
) -> dict[str, Any]:
    """Compute rate trend signals by comparing current vs previous metrics.

    Args:
        current_metrics: Current DeFi metrics.
        previous_metrics: Previous cycle's metrics (or None if first cycle).

    Returns:
        Dict of derived trend signals.
    """
    trends: dict[str, Any] = {}
    if previous_metrics is None:
        return {"note": "First cycle, no trend data available"}

    for protocol in current_metrics:
        current = current_metrics.get(protocol, {})
        previous = previous_metrics.get(protocol, {})

        if isinstance(current, dict) and isinstance(previous, dict):
            curr_markets = current.get("markets", [])
            prev_markets = previous.get("markets", [])

            if curr_markets and prev_markets:
                prev_by_symbol = {m.get("symbol"): m for m in prev_markets}
                changes: list[dict[str, Any]] = []
                for m in curr_markets:
                    sym = m.get("symbol", "")
                    prev_m = prev_by_symbol.get(sym)
                    if prev_m:
                        apy_curr = float(m.get("supply_apy", 0))
                        apy_prev = float(prev_m.get("supply_apy", 0))
                        if apy_prev > 0:
                            change_pct = ((apy_curr - apy_prev) / apy_prev) * 100
                            if abs(change_pct) > 1.0:
                                changes.append({
                                    "symbol": sym,
                                    "apy_change_pct": round(change_pct, 1),
                                    "direction": "up" if change_pct > 0 else "down",
                                })
                if changes:
                    trends[protocol] = {"rate_changes": changes}

    return trends


# ---------------------------------------------------------------------------
# Insight synthesizer
# ---------------------------------------------------------------------------


class InsightSynthesizer:
    """Collects, enriches, compresses, and validates insight snapshots.

    Integrates with PriceFeed, GasMonitor, DeFiMetricsCollector,
    PositionTracker, and LifecycleManager to build comprehensive
    snapshots for the Claude decision engine.

    Args:
        price_feed: PriceFeedManager instance for current prices.
        gas_monitor: GasMonitor instance for gas prices.
        defi_metrics: DeFiMetricsCollector instance for protocol data.
        position_tracker: PositionTracker instance for open positions.
        lifecycle_manager: LifecycleManager instance for strategy statuses.
        decision_history_size: Max number of recent decisions to retain.
    """

    def __init__(
        self,
        *,
        price_feed: Any,
        gas_monitor: Any,
        defi_metrics: Any,
        position_tracker: Any,
        lifecycle_manager: Any,
        decision_history_size: int = _DEFAULT_DECISION_HISTORY_SIZE,
    ) -> None:
        self._price_feed = price_feed
        self._gas_monitor = gas_monitor
        self._defi_metrics = defi_metrics
        self._position_tracker = position_tracker
        self._lifecycle_manager = lifecycle_manager
        self._recent_decisions: deque[dict[str, Any]] = deque(
            maxlen=decision_history_size,
        )
        self._previous_metrics: dict[str, Any] | None = None

    def record_decision(self, decision: dict[str, Any]) -> None:
        """Record a decision for inclusion in future snapshots.

        Args:
            decision: Decision dict to record.
        """
        self._recent_decisions.append({
            **decision,
            "recorded_at": datetime.now(UTC).isoformat(),
        })

    def _collect_prices(self) -> dict[str, Any]:
        """Collect current price data from the price feed."""
        try:
            return self._price_feed.fetch_prices()
        except Exception as e:
            _logger.warning(
                "Price feed collection failed",
                extra={"data": {"error": str(e)}},
            )
            return {}

    def _collect_gas(self) -> dict[str, Any]:
        """Collect current gas data from the gas monitor."""
        try:
            cached = self._gas_monitor.get_cached_prices()
            if cached is not None:
                gas_data = cached.to_dict()
            else:
                updated = self._gas_monitor.update()
                gas_data = updated.to_dict() if updated else {}
            # Add spike info
            spike = self._gas_monitor.is_spike()
            gas_data["is_spike"] = spike
            return gas_data
        except Exception as e:
            _logger.warning(
                "Gas data collection failed",
                extra={"data": {"error": str(e)}},
            )
            return {}

    def _collect_defi_metrics(self) -> dict[str, Any]:
        """Collect DeFi protocol metrics."""
        metrics: dict[str, Any] = {}
        for protocol in ("aave", "aerodrome"):
            try:
                data = self._defi_metrics.get_metrics(protocol)
                if data is not None:
                    metrics[protocol] = data
            except Exception as e:
                _logger.warning(
                    "DeFi metrics collection failed",
                    extra={"data": {"protocol": protocol, "error": str(e)}},
                )
        return metrics

    def _collect_positions(self) -> dict[str, Any]:
        """Collect position summary and individual positions."""
        try:
            summary = self._position_tracker.get_summary()
            open_positions = self._position_tracker.query()
            position_details = [p.to_dict() for p in open_positions]
            return {
                **summary,
                "details": position_details,
            }
        except Exception as e:
            _logger.warning(
                "Position collection failed",
                extra={"data": {"error": str(e)}},
            )
            return {"open_count": 0, "details": []}

    def _collect_strategies(self) -> list[dict[str, Any]]:
        """Collect active strategy specs and their current statuses."""
        strategies: list[dict[str, Any]] = []
        try:
            statuses = self._lifecycle_manager._state.get_strategy_statuses()
            for strategy_id, status in statuses.items():
                perf = self._lifecycle_manager.get_performance(strategy_id)
                strategies.append({
                    "id": strategy_id,
                    "status": status,
                    "performance": perf.to_dict(),
                })
        except Exception as e:
            _logger.warning(
                "Strategy collection failed",
                extra={"data": {"error": str(e)}},
            )
        return strategies

    def _collect_risk_status(self) -> dict[str, Any]:
        """Collect risk status as a simple summary.

        In production this would query all circuit breakers. Here we provide
        a basic structure that the decision engine expects.
        """
        return {
            "circuit_breakers_active": False,
            "trading_paused": False,
            "entries_paused": False,
            "timestamp": datetime.now(UTC).isoformat(),
        }

    def synthesize(self) -> InsightSnapshot:
        """Collect, enrich, compress, and validate a full insight snapshot.

        Returns:
            A validated InsightSnapshot ready for the decision engine.

        Raises:
            ValueError: If the resulting snapshot fails schema validation.
        """
        # Step 1: Collect raw data from all sources
        raw_prices = self._collect_prices()
        raw_gas = self._collect_gas()
        raw_defi = self._collect_defi_metrics()
        raw_positions = self._collect_positions()
        raw_strategies = self._collect_strategies()
        raw_risk = self._collect_risk_status()

        # Step 2: Enrich with derived signals
        rate_trends = _compute_rate_trends(raw_defi, self._previous_metrics)
        self._previous_metrics = raw_defi

        # Step 3: Compress for token efficiency
        compressed_prices = _compress_prices(raw_prices)
        compressed_gas = _compress_gas(raw_gas)
        compressed_positions = _compress_positions(raw_positions)
        compressed_defi = _compress_defi_metrics(raw_defi)

        # Step 4: Assemble market data
        market_data = {
            "prices": compressed_prices,
            "gas": compressed_gas,
            "defi_protocols": compressed_defi,
            "derived_signals": {
                "rate_trends": rate_trends,
            },
            "timestamp": datetime.now(UTC).isoformat(),
        }

        # Step 5: Build snapshot
        snapshot = InsightSnapshot(
            market_data=market_data,
            positions=compressed_positions,
            risk_status=raw_risk,
            strategies=raw_strategies,
            recent_decisions=list(self._recent_decisions),
        )

        # Step 6: Validate before returning
        snapshot_dict = snapshot.to_dict()
        valid, errors = validate_snapshot(snapshot_dict)
        if not valid:
            _logger.error(
                "Snapshot validation failed",
                extra={"data": {"errors": errors}},
            )
            raise ValueError(f"Snapshot validation failed: {'; '.join(errors)}")

        _logger.info(
            "Insight snapshot synthesized",
            extra={"data": {
                "price_count": len(compressed_prices),
                "position_count": compressed_positions.get("open_count", 0),
                "strategy_count": len(raw_strategies),
                "decision_history_count": len(self._recent_decisions),
            }},
        )

        return snapshot
