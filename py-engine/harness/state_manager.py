"""Agent state persistence with atomic writes and schema versioning."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from monitoring.logger import get_logger

SCHEMA_VERSION = 1

_logger = get_logger("state-manager", enable_file=False)


def _default_state() -> dict[str, Any]:
    """Return a fresh default agent state."""
    now = datetime.now(UTC).isoformat()
    return {
        "schema_version": SCHEMA_VERSION,
        "positions": {},
        "strategy_statuses": {},
        "last_reconciliation": None,
        "operational_flags": {
            "diagnostic_mode": False,
            "trading_paused": False,
        },
        "metadata": {
            "created_at": now,
            "updated_at": now,
        },
    }


class StateManager:
    """Manages persistent agent state with atomic writes.

    State is stored as human-readable JSON.  Every write goes through a
    tempfile-then-rename pattern so the file is never partially written.
    """

    def __init__(self, state_path: Path | str = "agent-state.json") -> None:
        self._path = Path(state_path)
        self._state: dict[str, Any] = self._load_or_create()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load_or_create(self) -> dict[str, Any]:
        """Load existing state or create a fresh default."""
        if self._path.exists():
            raw = self._path.read_text(encoding="utf-8")
            state = json.loads(raw)
            state = self._migrate(state)
            _logger.info(
                "State loaded",
                extra={"data": {
                    "path": str(self._path),
                    "schema_version": state["schema_version"],
                }},
            )
            return state

        state = _default_state()
        self._atomic_write(state)
        _logger.info(
            "Default state created",
            extra={"data": {"path": str(self._path)}},
        )
        return state

    def _migrate(self, state: dict[str, Any]) -> dict[str, Any]:
        """Upgrade state from older schema versions."""
        version = state.get("schema_version", 0)
        if version < 1:
            # v0 → v1: add operational_flags and metadata if missing
            state.setdefault("operational_flags", {
                "diagnostic_mode": False,
                "trading_paused": False,
            })
            state.setdefault("metadata", {
                "created_at": datetime.now(UTC).isoformat(),
                "updated_at": datetime.now(UTC).isoformat(),
            })
            state["schema_version"] = SCHEMA_VERSION
            _logger.info(
                "State migrated",
                extra={"data": {"from_version": version, "to_version": SCHEMA_VERSION}},
            )
        return state

    def _atomic_write(self, state: dict[str, Any]) -> None:
        """Write state to disk atomically via tempfile + rename."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            dir=self._path.parent,
            prefix=".agent-state-",
            suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2, default=str)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, self._path)
        except BaseException:
            # Clean up temp file on any error
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def save(self) -> None:
        """Persist the current state to disk atomically."""
        self._state["metadata"]["updated_at"] = datetime.now(UTC).isoformat()
        self._atomic_write(self._state)
        _logger.debug("State saved", extra={"data": {"path": str(self._path)}})

    def reload(self) -> None:
        """Reload state from disk, discarding in-memory changes."""
        self._state = self._load_or_create()

    # ------------------------------------------------------------------
    # State accessors
    # ------------------------------------------------------------------

    @property
    def state(self) -> dict[str, Any]:
        """Return the full state dict (read-only reference)."""
        return self._state

    @property
    def schema_version(self) -> int:
        return self._state["schema_version"]

    # -- Positions ------------------------------------------------------

    def get_positions(self) -> dict[str, Any]:
        return self._state["positions"]

    def set_position(self, position_id: str, position: dict[str, Any]) -> None:
        """Add or update a position, then persist."""
        self._state["positions"][position_id] = position
        self.save()
        _logger.info(
            "Position updated",
            extra={"data": {"position_id": position_id}},
        )

    def remove_position(self, position_id: str) -> None:
        """Remove a position, then persist."""
        self._state["positions"].pop(position_id, None)
        self.save()
        _logger.info(
            "Position removed",
            extra={"data": {"position_id": position_id}},
        )

    # -- Strategy statuses -----------------------------------------------

    def get_strategy_statuses(self) -> dict[str, Any]:
        return self._state["strategy_statuses"]

    def set_strategy_status(self, strategy_id: str, status: str) -> None:
        """Update a strategy's status, then persist."""
        self._state["strategy_statuses"][strategy_id] = status
        self.save()
        _logger.info(
            "Strategy status changed",
            extra={"data": {"strategy_id": strategy_id, "status": status}},
        )

    # -- Reconciliation --------------------------------------------------

    def get_last_reconciliation(self) -> str | None:
        return self._state["last_reconciliation"]

    def mark_reconciled(self) -> None:
        """Record the current time as last reconciliation, then persist."""
        self._state["last_reconciliation"] = datetime.now(UTC).isoformat()
        self.save()

    # -- Operational flags -----------------------------------------------

    def get_operational_flags(self) -> dict[str, Any]:
        return self._state["operational_flags"]

    def set_operational_flag(self, flag: str, value: bool) -> None:
        self._state["operational_flags"][flag] = value
        self.save()
        _logger.info(
            "Operational flag changed",
            extra={"data": {"flag": flag, "value": value}},
        )

    # -- PostgreSQL backup stub ------------------------------------------

    def backup_to_postgres(self) -> None:
        """Stub: log that a PostgreSQL backup would happen.

        Real implementation will be added with INFRA-006 (PostgreSQL).
        """
        _logger.info(
            "PostgreSQL backup stub",
            extra={"data": {
                "action": "backup_to_postgres",
                "note": "stub — real implementation with INFRA-006",
                "state_version": self.schema_version,
            }},
        )
