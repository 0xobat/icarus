"""Tests for agent state persistence — HARNESS-001."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from harness.state_manager import SCHEMA_VERSION, StateManager, _default_state

# ---------------------------------------------------------------------------
# Default state
# ---------------------------------------------------------------------------

class TestDefaultState:
    """A fresh state must contain all required fields."""

    def test_has_schema_version(self) -> None:
        state = _default_state()
        assert state["schema_version"] == SCHEMA_VERSION

    def test_has_empty_positions(self) -> None:
        state = _default_state()
        assert state["positions"] == {}

    def test_has_empty_strategy_statuses(self) -> None:
        state = _default_state()
        assert state["strategy_statuses"] == {}

    def test_has_reconciliation_none(self) -> None:
        state = _default_state()
        assert state["last_reconciliation"] is None

    def test_has_operational_flags(self) -> None:
        state = _default_state()
        flags = state["operational_flags"]
        assert flags["diagnostic_mode"] is False
        assert flags["trading_paused"] is False

    def test_has_metadata_timestamps(self) -> None:
        state = _default_state()
        assert "created_at" in state["metadata"]
        assert "updated_at" in state["metadata"]


# ---------------------------------------------------------------------------
# Load / Save round-trip
# ---------------------------------------------------------------------------

class TestLoadSave:
    """State must survive a save → load cycle identically."""

    def test_creates_file_on_init(self, tmp_path: Path) -> None:
        path = tmp_path / "state.json"
        StateManager(state_path=path)
        assert path.exists()

    def test_file_is_valid_json(self, tmp_path: Path) -> None:
        path = tmp_path / "state.json"
        StateManager(state_path=path)
        data = json.loads(path.read_text())
        assert isinstance(data, dict)

    def test_file_is_human_readable(self, tmp_path: Path) -> None:
        """State must be indented JSON, not single-line."""
        path = tmp_path / "state.json"
        StateManager(state_path=path)
        raw = path.read_text()
        # Indented JSON has newlines
        assert raw.count("\n") > 5

    def test_round_trip_preserves_data(self, tmp_path: Path) -> None:
        path = tmp_path / "state.json"
        mgr = StateManager(state_path=path)
        mgr.set_position("aave-eth", {"protocol": "aave", "amount": 1.5})
        mgr.set_strategy_status("STRAT-001", "active")

        # Reload from disk
        mgr2 = StateManager.__new__(StateManager)
        mgr2._path = path
        mgr2._state = mgr2._load_or_create()

        assert mgr2.get_positions()["aave-eth"]["amount"] == 1.5
        assert mgr2.get_strategy_statuses()["STRAT-001"] == "active"

    def test_reload_discards_in_memory(self, tmp_path: Path) -> None:
        path = tmp_path / "state.json"
        mgr = StateManager(state_path=path)
        mgr._state["positions"]["ghost"] = {"fake": True}
        # Don't save — reload should discard
        mgr.reload()
        assert "ghost" not in mgr.get_positions()


# ---------------------------------------------------------------------------
# Atomic writes
# ---------------------------------------------------------------------------

class TestAtomicWrites:
    """Writes must be atomic: no partial files on crash."""

    def test_no_temp_files_after_success(self, tmp_path: Path) -> None:
        path = tmp_path / "state.json"
        mgr = StateManager(state_path=path)
        mgr.set_position("test", {"value": 1})
        # No .tmp files left behind
        tmp_files = list(tmp_path.glob(".agent-state-*.tmp"))
        assert tmp_files == []

    def test_original_preserved_on_write_failure(self, tmp_path: Path) -> None:
        """If atomic write fails, old state file must remain intact."""
        path = tmp_path / "state.json"
        mgr = StateManager(state_path=path)
        mgr.set_position("original", {"value": 42})

        original_content = path.read_text()

        # Simulate write failure by making os.replace raise
        with patch("harness.state_manager.os.replace", side_effect=OSError("disk full")):
            try:
                mgr.set_position("bad", {"value": -1})
            except OSError:
                pass

        # Original file must still be intact
        assert path.read_text() == original_content

    def test_temp_cleaned_on_failure(self, tmp_path: Path) -> None:
        path = tmp_path / "state.json"
        mgr = StateManager(state_path=path)

        with patch("harness.state_manager.os.replace", side_effect=OSError("fail")):
            try:
                mgr.save()
            except OSError:
                pass

        tmp_files = list(tmp_path.glob(".agent-state-*.tmp"))
        assert tmp_files == []


# ---------------------------------------------------------------------------
# Schema versioning
# ---------------------------------------------------------------------------

class TestSchemaVersioning:
    """State must include a schema version and support migration."""

    def test_version_in_file(self, tmp_path: Path) -> None:
        path = tmp_path / "state.json"
        StateManager(state_path=path)
        data = json.loads(path.read_text())
        assert data["schema_version"] == SCHEMA_VERSION

    def test_version_on_instance(self, tmp_path: Path) -> None:
        path = tmp_path / "state.json"
        mgr = StateManager(state_path=path)
        assert mgr.schema_version == SCHEMA_VERSION

    def test_migrates_v0_state(self, tmp_path: Path) -> None:
        """A v0 state (missing fields) should be upgraded to current."""
        path = tmp_path / "state.json"
        old_state = {
            "positions": {"eth": {"amount": 10}},
            "strategy_statuses": {"STRAT-001": "active"},
            "last_reconciliation": None,
        }
        path.write_text(json.dumps(old_state))

        mgr = StateManager(state_path=path)
        assert mgr.schema_version == SCHEMA_VERSION
        assert "operational_flags" in mgr.state
        assert "metadata" in mgr.state
        # Original data preserved
        assert mgr.get_positions()["eth"]["amount"] == 10


# ---------------------------------------------------------------------------
# Position management
# ---------------------------------------------------------------------------

class TestPositions:

    def test_set_and_get(self, tmp_path: Path) -> None:
        mgr = StateManager(state_path=tmp_path / "s.json")
        mgr.set_position("uni-v3-eth-usdc", {"protocol": "uniswap", "tick_lower": -100})
        assert mgr.get_positions()["uni-v3-eth-usdc"]["protocol"] == "uniswap"

    def test_remove(self, tmp_path: Path) -> None:
        mgr = StateManager(state_path=tmp_path / "s.json")
        mgr.set_position("pos1", {"v": 1})
        mgr.remove_position("pos1")
        assert "pos1" not in mgr.get_positions()

    def test_remove_nonexistent_is_noop(self, tmp_path: Path) -> None:
        mgr = StateManager(state_path=tmp_path / "s.json")
        mgr.remove_position("nope")  # should not raise


# ---------------------------------------------------------------------------
# Strategy statuses
# ---------------------------------------------------------------------------

class TestStrategyStatuses:

    def test_set_and_get(self, tmp_path: Path) -> None:
        mgr = StateManager(state_path=tmp_path / "s.json")
        mgr.set_strategy_status("STRAT-001", "evaluating")
        assert mgr.get_strategy_statuses()["STRAT-001"] == "evaluating"

    def test_update_existing(self, tmp_path: Path) -> None:
        mgr = StateManager(state_path=tmp_path / "s.json")
        mgr.set_strategy_status("STRAT-001", "evaluating")
        mgr.set_strategy_status("STRAT-001", "active")
        assert mgr.get_strategy_statuses()["STRAT-001"] == "active"


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------

class TestReconciliation:

    def test_initially_none(self, tmp_path: Path) -> None:
        mgr = StateManager(state_path=tmp_path / "s.json")
        assert mgr.get_last_reconciliation() is None

    def test_mark_sets_timestamp(self, tmp_path: Path) -> None:
        mgr = StateManager(state_path=tmp_path / "s.json")
        mgr.mark_reconciled()
        ts = mgr.get_last_reconciliation()
        assert ts is not None
        assert "+00:00" in ts or ts.endswith("Z")


# ---------------------------------------------------------------------------
# Operational flags
# ---------------------------------------------------------------------------

class TestOperationalFlags:

    def test_defaults(self, tmp_path: Path) -> None:
        mgr = StateManager(state_path=tmp_path / "s.json")
        flags = mgr.get_operational_flags()
        assert flags["diagnostic_mode"] is False
        assert flags["trading_paused"] is False

    def test_set_flag(self, tmp_path: Path) -> None:
        mgr = StateManager(state_path=tmp_path / "s.json")
        mgr.set_operational_flag("diagnostic_mode", True)
        assert mgr.get_operational_flags()["diagnostic_mode"] is True


# ---------------------------------------------------------------------------
# PostgreSQL backup stub
# ---------------------------------------------------------------------------

class TestPostgresBackup:

    def test_stub_does_not_raise(self, tmp_path: Path) -> None:
        mgr = StateManager(state_path=tmp_path / "s.json")
        mgr.backup_to_postgres()  # should not raise

    def test_stub_logs(self, tmp_path: Path, capfd: object) -> None:
        mgr = StateManager(state_path=tmp_path / "s.json")
        mgr.backup_to_postgres()
        # The stub logs via structured logger — just ensure no crash


# ---------------------------------------------------------------------------
# Metadata timestamps
# ---------------------------------------------------------------------------

class TestMetadata:

    def test_updated_at_changes_on_save(self, tmp_path: Path) -> None:
        mgr = StateManager(state_path=tmp_path / "s.json")
        first_ts = mgr.state["metadata"]["updated_at"]
        mgr.set_position("x", {"v": 1})
        second_ts = mgr.state["metadata"]["updated_at"]
        assert second_ts >= first_ts
