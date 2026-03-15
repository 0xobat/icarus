"""Dashboard state publisher — serializes in-memory state to Redis KV keys (CONN-002).

Publishes the full dashboard state at the end of each decision cycle.
All keys are written with a 120s TTL so that the frontend's stale-indicator
component can detect py-engine downtime automatically.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from monitoring.logger import get_logger

_logger = get_logger("state-publisher", enable_file=False)

# TTL for all dashboard keys (seconds)
_DASHBOARD_TTL = 120


def _decimal_default(obj: object) -> Any:
    """JSON serializer for Decimal values."""
    if isinstance(obj, Decimal):
        return float(obj)
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


def _to_json(data: Any) -> str:
    """Serialize data to JSON, converting Decimals to floats."""
    return json.dumps(data, default=_decimal_default)


def _safe_float(value: Any, default: float = 0.0) -> float:
    """Convert a value to float safely."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def publish_dashboard_state(
    redis_client: Any,
    tracker: Any,
    drawdown_breaker: Any,
    circuit_breakers: dict[str, Any],
    exposure_limiter: Any,
    strategy_manager: Any,
    position_tracker: Any,
    db_repo: Any,
) -> None:
    """Serialize in-memory state to Redis KV keys with 120s TTL.

    Collects state from all passed-in modules, serializes to JSON
    (converting Decimal to float), and writes Redis KV keys with SET + TTL.
    All errors are caught and logged — never crashes the decision loop.

    Args:
        redis_client: RedisManager instance with a .client property.
        tracker: PositionTracker for portfolio summary data.
        drawdown_breaker: DrawdownBreaker for drawdown state.
        circuit_breakers: Dict mapping breaker names to breaker instances
            (gas_spike, tx_failures, position_loss, tvl_monitor).
        exposure_limiter: ExposureLimiter for exposure data.
        strategy_manager: LifecycleManager for strategy statuses.
        position_tracker: Same as tracker (kept for interface clarity).
        db_repo: DatabaseRepository for snapshots and alerts.
    """
    try:
        client = redis_client.client
        _publish_metrics(client, tracker, drawdown_breaker, db_repo)
        _publish_strategies(client, tracker, strategy_manager, exposure_limiter, db_repo)
        _publish_breakers(client, drawdown_breaker, circuit_breakers)
        _publish_drawdown(client, drawdown_breaker)
        _publish_exposure(client, exposure_limiter)
        _publish_reserve(client, exposure_limiter)
        _publish_hold_mode(client, circuit_breakers)
        _publish_health(client, redis_client, db_repo)
    except Exception:
        _logger.exception("Failed to publish dashboard state")


# ---------------------------------------------------------------------------
# Individual key publishers
# ---------------------------------------------------------------------------


def _publish_metrics(
    client: Any,
    tracker: Any,
    drawdown_breaker: Any,
    db_repo: Any,
) -> None:
    """Publish dashboard:metrics — full MetricsData shape."""
    summary = tracker.get_summary()
    portfolio_value = _safe_float(summary.get("total_value", 0))

    # TX success stats from tx_failures if available
    tx_total = 0
    tx_success = 0

    # Derive 24h change from snapshots
    portfolio_change_24h_pct = 0.0
    portfolio_change_24h_abs = 0.0
    portfolio_sparkline: list[float] = []
    pnl_sparkline: list[float] = []

    try:
        since_24h = datetime.now(UTC) - timedelta(hours=24)
        snapshots = db_repo.get_snapshots(since=since_24h, limit=100)

        if snapshots:
            # Snapshots are desc-ordered — oldest is last
            oldest = snapshots[-1]
            old_value = _safe_float(oldest.total_value_usd)
            if old_value > 0:
                portfolio_change_24h_abs = portfolio_value - old_value
                portfolio_change_24h_pct = (portfolio_change_24h_abs / old_value) * 100

            # Sparkline: chronological hourly values
            chronological = list(reversed(snapshots))
            portfolio_sparkline = [
                _safe_float(s.total_value_usd) for s in chronological
            ]

            # P&L sparkline: deltas between sequential snapshots (last 16)
            if len(chronological) >= 2:
                deltas = []
                for i in range(1, len(chronological)):
                    prev = _safe_float(chronological[i - 1].total_value_usd)
                    curr = _safe_float(chronological[i].total_value_usd)
                    deltas.append(curr - prev)
                pnl_sparkline = deltas[-16:]
    except Exception:
        _logger.debug("Failed to derive 24h metrics from snapshots")

    # Drawdown
    drawdown_current = _safe_float(drawdown_breaker.drawdown_pct) * 100
    drawdown_limit = float(os.environ.get("DRAWDOWN_LIMIT_PCT", "20"))

    # P&L today
    pnl_today = portfolio_change_24h_abs
    pnl_today_pct = portfolio_change_24h_pct

    metrics = {
        "portfolio_value": portfolio_value,
        "portfolio_change_24h_pct": portfolio_change_24h_pct,
        "portfolio_change_24h_abs": portfolio_change_24h_abs,
        "portfolio_sparkline": portfolio_sparkline,
        "drawdown_current": drawdown_current,
        "drawdown_limit": drawdown_limit,
        "pnl_today": pnl_today,
        "pnl_today_pct": pnl_today_pct,
        "pnl_sparkline": pnl_sparkline,
        "tx_success_rate": (tx_success / tx_total * 100) if tx_total > 0 else 100.0,
        "tx_success_count": tx_success,
        "tx_total_count": tx_total,
    }

    client.set("dashboard:metrics", _to_json(metrics), ex=_DASHBOARD_TTL)


def _publish_strategies(
    client: Any,
    tracker: Any,
    strategy_manager: Any,
    exposure_limiter: Any,
    db_repo: Any,
) -> None:
    """Publish dashboard:strategies — StrategiesPanelData envelope."""
    summary = tracker.get_summary()
    total_value = _safe_float(summary.get("total_value", 0))

    # Get strategy statuses — try file-based state first, fall back to PostgreSQL
    strategies: list[dict[str, Any]] = []
    try:
        statuses = strategy_manager._state.get_strategy_statuses()
        for sid, status in statuses.items():
            strategies.append({
                "strategy_id": sid,
                "status": status,
            })
    except Exception:
        _logger.debug("Failed to get strategy statuses from state manager")

    # Fall back to PostgreSQL if file-based state is empty
    if not strategies:
        try:
            db_statuses = db_repo.get_strategy_statuses()
            for row in db_statuses:
                strategies.append({
                    "strategy_id": row.strategy_id,
                    "status": row.status,
                })
        except Exception:
            _logger.debug("Failed to get strategy statuses from PostgreSQL")

    # Reserve info
    reserve_amount = 0.0
    reserve_pct = 0.0
    try:
        exposure = exposure_limiter.get_exposure()
        total_capital = _safe_float(exposure.total_capital)
        total_deployed = _safe_float(exposure.total_deployed)
        reserve_amount = total_capital - total_deployed
        if total_capital > 0:
            reserve_pct = (reserve_amount / total_capital) * 100
    except Exception:
        _logger.debug("Failed to compute reserve from exposure")

    data = {
        "strategies": strategies,
        "reserve": {"amount": reserve_amount, "pct": reserve_pct},
        "total_value": total_value,
    }

    client.set("dashboard:strategies", _to_json(data), ex=_DASHBOARD_TTL)


def _publish_breakers(
    client: Any,
    drawdown_breaker: Any,
    circuit_breakers: dict[str, Any],
) -> None:
    """Publish dashboard:breakers — array of breaker states."""
    breakers: list[dict[str, Any]] = []

    # Drawdown breaker
    dd_state = drawdown_breaker.get_state()
    breakers.append({
        "name": "drawdown",
        "current": _safe_float(dd_state.drawdown_pct) * 100,
        "limit": _safe_float(drawdown_breaker._critical_threshold) * 100,
        "unit": "%",
        "status": dd_state.level,
        "last_triggered": dd_state.triggered_at,
    })

    # Gas spike breaker
    gas_spike = circuit_breakers.get("gas_spike")
    if gas_spike is not None:
        gs_state = gas_spike.get_state()
        breakers.append({
            "name": "gas_spike",
            "current": _safe_float(gs_state.current_gas),
            "limit": _safe_float(gs_state.threshold),
            "unit": "gwei",
            "status": "critical" if gs_state.is_active else "normal",
            "last_triggered": gs_state.activated_at,
        })

    # TX failure monitor
    tx_failures = circuit_breakers.get("tx_failures")
    if tx_failures is not None:
        tx_state = tx_failures.get_state()
        breakers.append({
            "name": "tx_failure_rate",
            "current": tx_state.failures_in_window,
            "limit": tx_state.threshold,
            "unit": "failures/hr",
            "status": "critical" if tx_state.is_paused else "normal",
            "last_triggered": tx_state.last_failure,
        })

    # Position loss limit
    pos_loss = circuit_breakers.get("position_loss")
    if pos_loss is not None:
        breakers.append({
            "name": "position_loss",
            "current": 0,
            "limit": 10,
            "unit": "%",
            "status": "normal",
            "last_triggered": None,
        })

    # TVL monitor
    tvl_mon = circuit_breakers.get("tvl_monitor")
    if tvl_mon is not None:
        breakers.append({
            "name": "tvl_drop",
            "current": 0,
            "limit": 30,
            "unit": "%",
            "status": "normal",
            "last_triggered": None,
        })

    client.set("dashboard:breakers", _to_json(breakers), ex=_DASHBOARD_TTL)


def _publish_drawdown(client: Any, drawdown_breaker: Any) -> None:
    """Publish dashboard:drawdown — drawdown state."""
    dd_state = drawdown_breaker.get_state()
    drawdown_limit = float(os.environ.get("DRAWDOWN_LIMIT_PCT", "20"))

    data = {
        "current_pct": _safe_float(dd_state.drawdown_pct) * 100,
        "peak_value": _safe_float(dd_state.peak_value),
        "current_value": _safe_float(dd_state.current_value),
        "level": dd_state.level,
        "limit": drawdown_limit,
    }

    client.set("dashboard:drawdown", _to_json(data), ex=_DASHBOARD_TTL)


def _publish_exposure(client: Any, exposure_limiter: Any) -> None:
    """Publish dashboard:exposure — array of exposure entries."""
    entries: list[dict[str, Any]] = []

    try:
        exposure = exposure_limiter.get_exposure()
        config = exposure_limiter.config
        # Per-protocol exposure
        for proto, value_str in exposure.by_protocol.items():
            value = _safe_float(value_str)
            pct = _safe_float(exposure.protocol_pcts.get(proto, 0)) * 100
            limit_pct = _safe_float(config.max_protocol_pct) * 100
            entries.append({
                "scope": "protocol",
                "name": proto,
                "current_allocation": value,
                "current_pct": pct,
                "limit_pct": limit_pct,
                "headroom": limit_pct - pct,
            })

        # Per-asset exposure
        for asset, value_str in exposure.by_asset.items():
            value = _safe_float(value_str)
            pct = _safe_float(exposure.asset_pcts.get(asset, 0)) * 100
            limit_pct = _safe_float(config.max_asset_pct) * 100
            entries.append({
                "scope": "asset",
                "name": asset,
                "current_allocation": value,
                "current_pct": pct,
                "limit_pct": limit_pct,
                "headroom": limit_pct - pct,
            })
    except Exception:
        _logger.debug("Failed to build exposure data")

    client.set("dashboard:exposure", _to_json(entries), ex=_DASHBOARD_TTL)


def _publish_reserve(client: Any, exposure_limiter: Any) -> None:
    """Publish dashboard:reserve — liquid reserve data."""
    try:
        exposure = exposure_limiter.get_exposure()
        config = exposure_limiter.config
        total_capital = _safe_float(exposure.total_capital)
        total_deployed = _safe_float(exposure.total_deployed)
        liquid_reserve = total_capital - total_deployed
        min_req = _safe_float(config.min_stablecoin_pct) * 100
        reserve_pct = (_safe_float(exposure.stablecoin_reserve_pct)) * 100
    except Exception:
        liquid_reserve = 0.0
        min_req = 15.0
        reserve_pct = 0.0

    data = {
        "liquid_reserve": liquid_reserve,
        "min_reserve_requirement": min_req,
        "reserve_pct": reserve_pct,
    }

    client.set("dashboard:reserve", _to_json(data), ex=_DASHBOARD_TTL)


def _publish_hold_mode(client: Any, circuit_breakers: dict[str, Any]) -> None:
    """Publish dashboard:hold_mode — hold mode state."""
    hold_mode = circuit_breakers.get("hold_mode")

    if hold_mode is not None and hold_mode.is_active():
        data = {
            "active": True,
            "reason": hold_mode.reason or "",
            "since": hold_mode.entry_time or "",
        }
    else:
        data = {
            "active": False,
            "reason": "",
            "since": "",
        }

    client.set("dashboard:hold_mode", _to_json(data), ex=_DASHBOARD_TTL)


def _publish_health(
    client: Any,
    redis_client: Any,
    db_repo: Any,
) -> None:
    """Publish dashboard:health — service health array."""
    now = datetime.now(UTC)
    services: list[dict[str, Any]] = []

    # Redis health
    redis_latency = 0.0
    redis_status = "connected"
    try:
        start = datetime.now(UTC)
        redis_client.client.ping()
        elapsed = (datetime.now(UTC) - start).total_seconds() * 1000
        redis_latency = round(elapsed, 1)
    except Exception:
        redis_status = "disconnected"

    redis_error_count = _count_errors_24h(db_repo, "redis")

    services.append({
        "name": "Redis",
        "status": redis_status,
        "latency_ms": redis_latency,
        "last_heartbeat": now.isoformat(),
        "error_count_24h": redis_error_count,
    })

    # PostgreSQL health
    pg_status = "connected"
    pg_latency = 0.0
    try:
        start = datetime.now(UTC)
        db_repo.get_latest_snapshot()
        elapsed = (datetime.now(UTC) - start).total_seconds() * 1000
        pg_latency = round(elapsed, 1)
    except Exception:
        pg_status = "disconnected"

    pg_error_count = _count_errors_24h(db_repo, "postgres")

    services.append({
        "name": "PostgreSQL",
        "status": pg_status,
        "latency_ms": pg_latency,
        "last_heartbeat": now.isoformat(),
        "error_count_24h": pg_error_count,
    })

    client.set("dashboard:health", _to_json(services), ex=_DASHBOARD_TTL)


def _count_errors_24h(db_repo: Any, category: str) -> int:
    """Count alerts for a service category in the last 24h."""
    try:
        since = datetime.now(UTC) - timedelta(hours=24)
        alerts = db_repo.get_alerts(category=category, since=since, limit=10000)
        return len(alerts)
    except Exception:
        return 0
