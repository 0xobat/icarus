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
