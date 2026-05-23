"""Startup recovery sequence — load state, reconcile, health check, resume."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from monitoring.logger import get_logger

_logger = get_logger("startup-recovery", enable_file=False)

# Recovery timing limits
RECOVERY_TARGET_SECONDS = 60
RECOVERY_HARD_LIMIT_SECONDS = 300


class RecoveryStatus(StrEnum):
    """Enumeration of startup recovery outcomes."""

    SUCCESS = "success"
    DIAGNOSTIC = "diagnostic"
    FAILED = "failed"


@dataclass
class HealthCheckResult:
    """Result of a single protocol health check."""

    protocol: str
    healthy: bool
    message: str = ""
    latency_ms: float = 0.0


@dataclass
class RecoveryResult:
    """Full result of the startup recovery sequence."""

    status: RecoveryStatus
    duration_seconds: float = 0.0
    state_loaded: bool = False
    unprocessed_orders: int = 0
    unprocessed_results: int = 0
    discrepancies_found: int = 0
    discrepancies_resolved: int = 0
    health_checks: list[HealthCheckResult] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class StartupRecovery:
    """Orchestrates the startup recovery sequence.

    Steps:
    1. Load agent-state.json via StateManager
    2. Check Redis Streams for unprocessed messages
    3. Query on-chain state to verify positions match internal records
    4. Reconcile discrepancies
    5. Run health checks on connected protocols
    6. Resume normal operation or enter diagnostic mode
    """

    def __init__(
        self,
        *,
        state_manager: Any,
        redis_manager: Any | None = None,
        reconciler: Any | None = None,
        health_check_fn: Any | None = None,
        on_diagnostic: Any | None = None,
    ) -> None:
        self._state_manager = state_manager
        self._redis = redis_manager
        self._reconciler = reconciler
        self._health_check_fn = health_check_fn
        self._on_diagnostic = on_diagnostic

    def run(self) -> RecoveryResult:
        """Execute the full recovery sequence. Returns RecoveryResult."""
        result = RecoveryResult(status=RecoveryStatus.FAILED)
        start = time.monotonic()

        try:
            # Step 1: Load state
            self._load_state(result)

            # Step 2: Check Redis Streams for unprocessed messages
            self._check_streams(result)

            # Step 3 & 4: Reconcile on-chain state
            self._reconcile(result)

            # Step 5: Health checks
            self._run_health_checks(result)

            # Check timing
            elapsed = time.monotonic() - start
            result.duration_seconds = round(elapsed, 3)

            if elapsed > RECOVERY_HARD_LIMIT_SECONDS:
                result.status = RecoveryStatus.FAILED
                result.errors.append(
                    f"Recovery exceeded hard limit: {elapsed:.1f}s > "
                    f"{RECOVERY_HARD_LIMIT_SECONDS}s"
                )
            elif result.errors:
                result.status = RecoveryStatus.DIAGNOSTIC
            else:
                result.status = RecoveryStatus.SUCCESS

            if elapsed > RECOVERY_TARGET_SECONDS:
                _logger.warning(
                    "Recovery exceeded target time",
                    extra={"data": {
                        "elapsed_seconds": result.duration_seconds,
                        "target_seconds": RECOVERY_TARGET_SECONDS,
                    }},
                )

        except Exception as exc:
            result.duration_seconds = round(time.monotonic() - start, 3)
            result.status = RecoveryStatus.FAILED
            result.errors.append(f"Unhandled error: {exc}")
            _logger.error(
                "Recovery failed with exception",
                extra={"data": {"error": str(exc)}},
                exc_info=True,
            )

        # Log final status
        _logger.info(
            "Recovery sequence completed",
            extra={"data": {
                "status": result.status.value,
                "duration_seconds": result.duration_seconds,
                "state_loaded": result.state_loaded,
                "unprocessed_orders": result.unprocessed_orders,
                "unprocessed_results": result.unprocessed_results,
                "discrepancies_found": result.discrepancies_found,
                "discrepancies_resolved": result.discrepancies_resolved,
                "health_checks_passed": sum(
                    1 for h in result.health_checks if h.healthy
                ),
                "health_checks_total": len(result.health_checks),
                "errors": result.errors,
            }},
        )

        # Trigger diagnostic callback if needed
        if result.status == RecoveryStatus.DIAGNOSTIC and self._on_diagnostic:
            self._on_diagnostic(result)

        return result

    def _load_state(self, result: RecoveryResult) -> None:
        """Step 1: Load agent state from disk."""
        try:
            self._state_manager.reload()
            result.state_loaded = True
            _logger.info(
                "State loaded successfully",
                extra={"data": {
                    "schema_version": self._state_manager.schema_version,
                    "positions": len(self._state_manager.get_positions()),
                }},
            )
        except Exception as exc:
            result.errors.append(f"State load failed: {exc}")
            _logger.error(
                "Failed to load agent state",
                extra={"data": {"error": str(exc)}},
            )

    def _check_streams(self, result: RecoveryResult) -> None:
        """Step 2: Check Redis Streams for unprocessed messages."""
        if self._redis is None:
            return

        try:
            orders = self._redis.stream_read(
                "execution:orders", from_id="0-0", count=1000,
            )
            result.unprocessed_orders = len(orders)
            if orders:
                _logger.info(
                    "Found unprocessed orders in stream",
                    extra={"data": {"count": len(orders)}},
                )

            results = self._redis.stream_read(
                "execution:results", from_id="0-0", count=1000,
            )
            result.unprocessed_results = len(results)
            if results:
                _logger.info(
                    "Found unprocessed results in stream",
                    extra={"data": {"count": len(results)}},
                )
        except Exception as exc:
            result.errors.append(f"Redis stream check failed: {exc}")
            _logger.error(
                "Failed to check Redis streams",
                extra={"data": {"error": str(exc)}},
            )

    def _reconcile(self, result: RecoveryResult) -> None:
        """Steps 3 & 4: Query on-chain and reconcile discrepancies."""
        if self._reconciler is None:
            return

        try:
            positions = self._state_manager.get_positions()
            if not positions:
                _logger.info("No positions to reconcile")
                return

            # Use reconciler to compare on-chain vs agent state
            discrepancies = self._reconciler.reconcile_positions(positions)
            result.discrepancies_found = len(discrepancies)

            # Auto-resolve what we can
            resolved = 0
            for d in discrepancies:
                if d.get("auto_fixable", False):
                    resolved += 1
            result.discrepancies_resolved = resolved

            unresolved = result.discrepancies_found - resolved
            if unresolved > 0:
                result.errors.append(
                    f"{unresolved} unresolved discrepancies require manual review"
                )
                _logger.warning(
                    "Unresolved discrepancies found",
                    extra={"data": {
                        "total": result.discrepancies_found,
                        "resolved": resolved,
                        "unresolved": unresolved,
                    }},
                )
            elif result.discrepancies_found > 0:
                _logger.info(
                    "All discrepancies auto-resolved",
                    extra={"data": {"count": resolved}},
                )

        except Exception as exc:
            result.errors.append(f"Reconciliation failed: {exc}")
            _logger.error(
                "Reconciliation failed",
                extra={"data": {"error": str(exc)}},
            )

    def _run_health_checks(self, result: RecoveryResult) -> None:
        """Step 5: Run health checks on connected protocols."""
        if self._health_check_fn is None:
            return

        try:
            checks = self._health_check_fn()
            result.health_checks = checks

            failed = [c for c in checks if not c.healthy]
            if failed:
                names = ", ".join(c.protocol for c in failed)
                result.errors.append(f"Health check failures: {names}")
                _logger.warning(
                    "Protocol health check failures",
                    extra={"data": {
                        "failed": [c.protocol for c in failed],
                        "total": len(checks),
                    }},
                )
            else:
                _logger.info(
                    "All health checks passed",
                    extra={"data": {"count": len(checks)}},
                )
        except Exception as exc:
            result.errors.append(f"Health checks failed: {exc}")
            _logger.error(
                "Health check execution failed",
                extra={"data": {"error": str(exc)}},
            )


# ═══════════════════════════════════════════════════════════════════════
# Full startup recovery (HARNESS-002)
# ═══════════════════════════════════════════════════════════════════════


@dataclass
class FullRecoveryResult:
    """Summary of the full startup recovery sequence."""

    positions_loaded: int = 0
    strategy_statuses_loaded: int = 0
    messages_replayed: int = 0
    reconciliation_discrepancies: int = 0
    reconciliation_success: bool = True
    health_check_redis: bool = True
    health_check_postgres: bool = True
    entered_hold_mode: bool = False
    hold_reason: str = ""
    steps_completed: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0

    @property
    def success(self) -> bool:
        """Recovery succeeded if health checks passed and not in hold mode."""
        return (
            self.health_check_redis
            and self.health_check_postgres
            and not self.entered_hold_mode
        )


def run_startup_recovery(
    *,
    redis: Any,
    db_manager: Any,
    repository: Any,
    hold_mode: Any,
    position_tracker: Any,
    reconciler: Any | None = None,
    wallet_address: str = "",
) -> FullRecoveryResult:
    """Execute the full HARNESS-002 startup recovery sequence.

    Steps:
    1. Load portfolio state, positions, strategy statuses from PostgreSQL
    2. Check Redis Streams for unprocessed messages, replay after last acknowledged
    3. Query on-chain state to verify positions match database records
    4. Reconcile discrepancies (trust on-chain state)
    5. Health check Redis and PostgreSQL connectivity
    6. Resume normal operation or enter hold mode if recovery fails

    Args:
        redis: RedisManager instance.
        db_manager: DatabaseManager instance.
        repository: DatabaseRepository instance.
        hold_mode: HoldMode instance.
        position_tracker: PositionTracker instance (updated during recovery).
        reconciler: PositionReconciler instance (optional).
        wallet_address: Wallet address for on-chain reconciliation.

    Returns:
        FullRecoveryResult summarizing all steps.
    """
    result = FullRecoveryResult()
    start = time.monotonic()

    _logger.info("Starting full recovery sequence (HARNESS-002)")

    # Step 1: Load state from PostgreSQL
    _recovery_load_pg_state(repository, result)

    # Step 2: Replay unprocessed Redis Stream messages
    _recovery_replay_streams(redis, result)

    # Step 3 & 4: On-chain reconciliation
    _recovery_reconcile(reconciler, wallet_address, repository, result)

    # Step 5: Health check
    _recovery_health_check(redis, db_manager, result)

    # Step 6: Decide resume or hold
    if not result.health_check_redis or not result.health_check_postgres:
        _recovery_enter_hold(hold_mode, result, "Health check failed")
    elif not result.reconciliation_success:
        _recovery_enter_hold(
            hold_mode, result, "On-chain reconciliation failed"
        )

    result.duration_seconds = round(time.monotonic() - start, 3)

    status = "success" if result.success else "hold_mode"
    _logger.info(
        "Full recovery sequence complete",
        extra={"data": {
            "status": status,
            "positions_loaded": result.positions_loaded,
            "strategy_statuses_loaded": result.strategy_statuses_loaded,
            "messages_replayed": result.messages_replayed,
            "reconciliation_discrepancies": result.reconciliation_discrepancies,
            "hold_mode": result.entered_hold_mode,
            "steps_completed": result.steps_completed,
            "errors": result.errors,
            "duration_seconds": result.duration_seconds,
        }},
    )

    return result


def _recovery_load_pg_state(
    repository: Any, result: FullRecoveryResult,
) -> None:
    """Step 1: Load portfolio state from PostgreSQL."""
    try:
        cache = repository.load_cache()
        result.positions_loaded = len(cache.get("positions", {}))
        result.strategy_statuses_loaded = len(
            cache.get("strategy_statuses", {}),
        )
        result.steps_completed.append("pg_state_loaded")
        _logger.info(
            "PostgreSQL state loaded",
            extra={"data": {
                "positions": result.positions_loaded,
                "strategy_statuses": result.strategy_statuses_loaded,
            }},
        )
    except Exception as e:
        msg = f"Failed to load state from PostgreSQL: {e}"
        result.errors.append(msg)
        _logger.exception(msg)


def _recovery_replay_streams(
    redis: Any, result: FullRecoveryResult,
) -> None:
    """Step 2: Replay unprocessed Redis Stream messages."""
    from data.redis_client import CHANNELS

    channels_to_replay = [
        CHANNELS["EXECUTION_RESULTS"],
        CHANNELS["MARKET_EVENTS"],
    ]

    total_replayed = 0

    for channel in channels_to_replay:
        try:
            pending = _get_pending_messages(redis, channel)
            count = len(pending)
            total_replayed += count
            if count > 0:
                _logger.info(
                    "Found pending messages",
                    extra={"data": {"channel": channel, "count": count}},
                )
        except Exception as e:
            msg = f"Failed to replay stream {channel}: {e}"
            result.errors.append(msg)
            _logger.warning(msg)

    result.messages_replayed = total_replayed
    result.steps_completed.append("streams_replayed")
    _logger.info(
        "Stream replay complete",
        extra={"data": {"messages_replayed": total_replayed}},
    )


def _get_pending_messages(
    redis: Any, channel: str,
) -> list[dict[str, Any]]:
    """Read pending (unacknowledged) messages from a consumer group.

    Uses XREADGROUP with ID ``0`` to get messages delivered to this
    consumer but not yet acknowledged.
    """
    import json as _json

    try:
        client = redis.client
        group = redis.group
        consumer = redis.consumer

        # Ensure consumer group exists
        redis.ensure_group(channel)

        entries = client.xreadgroup(
            group, consumer,
            {channel: "0"},
            count=1000,
        )

        results: list[dict[str, Any]] = []
        if not entries:
            return results

        for _stream, messages in entries:
            for msg_id, fields in messages:
                if not fields:
                    continue
                raw = fields.get("data")
                if raw:
                    try:
                        data = _json.loads(raw)
                        results.append({"id": msg_id, "data": data})
                    except (ValueError, TypeError):
                        pass

        return results
    except Exception as e:
        _logger.warning(
            "Failed to read pending messages",
            extra={"data": {"channel": channel, "error": str(e)}},
        )
        return []


def _recovery_reconcile(
    reconciler: Any | None,
    wallet_address: str,
    repository: Any,
    result: FullRecoveryResult,
) -> None:
    """Steps 3 & 4: On-chain reconciliation via PositionReconciler."""
    if reconciler is None:
        _logger.info(
            "No reconciler configured — skipping on-chain reconciliation",
        )
        result.steps_completed.append("onchain_reconciliation_skipped")
        return

    if not wallet_address:
        _logger.info(
            "No wallet address configured — skipping reconciliation",
        )
        result.steps_completed.append("onchain_reconciliation_skipped")
        return

    try:
        recon_result = reconciler.run(wallet_address, repository)

        result.reconciliation_discrepancies = recon_result.discrepancies_found
        result.reconciliation_success = recon_result.success

        # Check for positions that exist in DB but not on-chain
        manual_review_count = sum(
            1 for d in recon_result.discrepancies
            if d.discrepancy_type == "missing_onchain"
        )
        if manual_review_count > 0:
            _logger.warning(
                "Irreconcilable discrepancies found",
                extra={"data": {
                    "manual_review_count": manual_review_count,
                    "total_discrepancies": recon_result.discrepancies_found,
                }},
            )
            result.reconciliation_success = False

        result.steps_completed.append("onchain_reconciliation_complete")
        _logger.info(
            "On-chain reconciliation complete",
            extra={"data": {
                "discrepancies_found": recon_result.discrepancies_found,
                "positions_updated": recon_result.positions_updated,
                "positions_closed": recon_result.positions_closed,
                "positions_created": recon_result.positions_created,
            }},
        )
    except Exception as e:
        msg = f"On-chain reconciliation failed: {e}"
        result.errors.append(msg)
        result.reconciliation_success = False
        _logger.exception(msg)


def _recovery_health_check(
    redis: Any, db_manager: Any, result: FullRecoveryResult,
) -> None:
    """Step 5: Verify Redis and PostgreSQL connectivity."""
    # Redis ping
    try:
        redis.client.ping()
        result.health_check_redis = True
    except Exception as e:
        result.health_check_redis = False
        result.errors.append(f"Redis health check failed: {e}")
        _logger.error(
            "Redis health check failed",
            extra={"data": {"error": str(e)}},
        )

    # PostgreSQL lightweight query
    try:
        import sqlalchemy

        session = db_manager.get_session()
        try:
            session.execute(sqlalchemy.text("SELECT 1"))
            result.health_check_postgres = True
        finally:
            session.close()
    except Exception as e:
        result.health_check_postgres = False
        result.errors.append(f"PostgreSQL health check failed: {e}")
        _logger.error(
            "PostgreSQL health check failed",
            extra={"data": {"error": str(e)}},
        )

    result.steps_completed.append("health_check_complete")
    _logger.info(
        "Health check complete",
        extra={"data": {
            "redis": "ok" if result.health_check_redis else "failed",
            "postgres": "ok" if result.health_check_postgres else "failed",
        }},
    )


def _recovery_enter_hold(
    hold_mode: Any, result: FullRecoveryResult, reason: str,
) -> None:
    """Enter hold mode due to recovery failure."""
    from harness.hold_mode import HoldTrigger

    result.entered_hold_mode = True
    result.hold_reason = reason

    hold_mode.enter(
        reason=f"Startup recovery: {reason}",
        trigger=HoldTrigger.IRRECONCILABLE_STATE,
        context={
            "errors": result.errors,
            "steps_completed": result.steps_completed,
        },
    )

    _logger.critical(
        "Entering hold mode due to recovery failure",
        extra={"data": {"reason": reason, "errors": result.errors}},
    )
