"""Tests for strategy lifecycle manager — STRAT-007."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from harness.state_manager import StateManager
from strategies.lifecycle_manager import (
    VALID_TRANSITIONS,
    LifecycleManager,
    StrategyPerformance,
)


def _make_manager(tmp_path: Path, **kwargs):
    sm = StateManager(tmp_path / "agent-state.json")
    lm = LifecycleManager(sm, **kwargs)
    return lm, sm


# ---------------------------------------------------------------------------
# Status transitions
# ---------------------------------------------------------------------------
class TestStatusTransitions:

    def test_evaluating_to_active(self, tmp_path: Path) -> None:
        lm, _ = _make_manager(tmp_path)
        assert lm.get_status("STRAT-001") == "evaluating"
        assert lm.transition("STRAT-001", "active")
        assert lm.get_status("STRAT-001") == "active"

    def test_active_to_paused(self, tmp_path: Path) -> None:
        lm, sm = _make_manager(tmp_path)
        sm.set_strategy_status("STRAT-001", "active")
        assert lm.transition("STRAT-001", "paused")
        assert lm.get_status("STRAT-001") == "paused"

    def test_paused_to_active(self, tmp_path: Path) -> None:
        lm, sm = _make_manager(tmp_path)
        sm.set_strategy_status("STRAT-001", "paused")
        assert lm.transition("STRAT-001", "active")
        assert lm.get_status("STRAT-001") == "active"

    def test_active_to_retired(self, tmp_path: Path) -> None:
        lm, sm = _make_manager(tmp_path)
        sm.set_strategy_status("STRAT-001", "active")
        assert lm.transition("STRAT-001", "retired")
        assert lm.get_status("STRAT-001") == "retired"

    def test_retired_cannot_transition(self, tmp_path: Path) -> None:
        lm, sm = _make_manager(tmp_path)
        sm.set_strategy_status("STRAT-001", "retired")
        assert not lm.transition("STRAT-001", "active")
        assert lm.get_status("STRAT-001") == "retired"

    def test_invalid_transition_evaluating_to_paused(
        self, tmp_path: Path,
    ) -> None:
        lm, _ = _make_manager(tmp_path)
        assert not lm.transition("STRAT-001", "paused")
        assert lm.get_status("STRAT-001") == "evaluating"

    def test_invalid_transition_evaluating_to_retired(
        self, tmp_path: Path,
    ) -> None:
        lm, _ = _make_manager(tmp_path)
        assert not lm.transition("STRAT-001", "retired")

    def test_invalid_status_value(self, tmp_path: Path) -> None:
        lm, _ = _make_manager(tmp_path)
        assert not lm.transition("STRAT-001", "unknown")

    def test_all_valid_transitions_covered(self) -> None:
        assert "evaluating" in VALID_TRANSITIONS
        assert "active" in VALID_TRANSITIONS
        assert "paused" in VALID_TRANSITIONS
        assert "retired" in VALID_TRANSITIONS
        assert VALID_TRANSITIONS["evaluating"] == {"active"}
        assert VALID_TRANSITIONS["active"] == {"paused", "retired"}
        assert VALID_TRANSITIONS["paused"] == {"active"}
        assert VALID_TRANSITIONS["retired"] == set()


# ---------------------------------------------------------------------------
# One adjustment per cycle
# ---------------------------------------------------------------------------
class TestCycleEnforcement:

    def test_only_one_adjustment_per_cycle(
        self, tmp_path: Path,
    ) -> None:
        lm, sm = _make_manager(tmp_path)
        assert lm.transition("STRAT-001", "active")
        sm.set_strategy_status("STRAT-002", "active")
        assert not lm.transition("STRAT-002", "paused")

    def test_reset_cycle_allows_new_adjustment(
        self, tmp_path: Path,
    ) -> None:
        lm, sm = _make_manager(tmp_path)
        assert lm.transition("STRAT-001", "active")
        assert lm.adjustment_made_this_cycle
        lm.reset_cycle()
        assert not lm.adjustment_made_this_cycle
        sm.set_strategy_status("STRAT-002", "active")
        assert lm.transition("STRAT-002", "paused")

    def test_failed_transition_does_not_consume_cycle(
        self, tmp_path: Path,
    ) -> None:
        lm, _ = _make_manager(tmp_path)
        # evaluating->paused is invalid, shouldn't consume cycle
        assert not lm.transition("STRAT-001", "paused")
        assert not lm.adjustment_made_this_cycle
        assert lm.transition("STRAT-001", "active")


# ---------------------------------------------------------------------------
# Persistence via StateManager
# ---------------------------------------------------------------------------
class TestStatePersistence:

    def test_status_persisted_to_state(self, tmp_path: Path) -> None:
        lm, _ = _make_manager(tmp_path)
        lm.transition("STRAT-001", "active")
        state = json.loads(
            (tmp_path / "agent-state.json").read_text(),
        )
        assert state["strategy_statuses"]["STRAT-001"] == "active"

    def test_status_survives_reload(self, tmp_path: Path) -> None:
        lm, _ = _make_manager(tmp_path)
        lm.transition("STRAT-001", "active")
        sm2 = StateManager(tmp_path / "agent-state.json")
        lm2 = LifecycleManager(sm2)
        assert lm2.get_status("STRAT-001") == "active"

    def test_default_status_evaluating(self, tmp_path: Path) -> None:
        lm, _ = _make_manager(tmp_path)
        assert lm.get_status("STRAT-NEW") == "evaluating"


# ---------------------------------------------------------------------------
# Performance tracking
# ---------------------------------------------------------------------------
class TestPerformanceTracking:

    def test_initial_performance(self, tmp_path: Path) -> None:
        lm, _ = _make_manager(tmp_path)
        perf = lm.get_performance("STRAT-001")
        assert perf.strategy_id == "STRAT-001"
        assert perf.total_pnl == Decimal(0)
        assert perf.sharpe_ratio == Decimal(0)
        assert perf.max_drawdown == Decimal(0)

    def test_update_tracks_pnl(self, tmp_path: Path) -> None:
        lm, _ = _make_manager(tmp_path)
        lm.update_performance("STRAT-001", Decimal("1000"))
        lm.update_performance("STRAT-001", Decimal("1100"))
        perf = lm.get_performance("STRAT-001")
        assert perf.current_value == Decimal("1100")
        assert perf.peak_value == Decimal("1100")

    def test_drawdown_tracked(self, tmp_path: Path) -> None:
        lm, _ = _make_manager(tmp_path)
        lm.update_performance("STRAT-001", Decimal("1000"))
        lm.update_performance("STRAT-001", Decimal("900"))
        perf = lm.get_performance("STRAT-001")
        assert perf.max_drawdown == Decimal("0.1")

    def test_sharpe_ratio_computed(self, tmp_path: Path) -> None:
        lm, _ = _make_manager(tmp_path)
        lm.update_performance("STRAT-001", Decimal("1000"))
        lm.update_performance("STRAT-001", Decimal("1100"))
        lm.update_performance("STRAT-001", Decimal("1200"))
        perf = lm.get_performance("STRAT-001")
        assert perf.sharpe_ratio > 0

    def test_performance_per_strategy(self, tmp_path: Path) -> None:
        lm, _ = _make_manager(tmp_path)
        lm.update_performance("STRAT-001", Decimal("1000"))
        lm.update_performance("STRAT-002", Decimal("2000"))
        assert lm.get_performance("STRAT-001").current_value == Decimal("1000")
        assert lm.get_performance("STRAT-002").current_value == Decimal("2000")

    def test_get_all_performance(self, tmp_path: Path) -> None:
        lm, _ = _make_manager(tmp_path)
        lm.update_performance("STRAT-001", Decimal("1000"))
        lm.update_performance("STRAT-002", Decimal("2000"))
        all_perf = lm.get_all_performance()
        assert "STRAT-001" in all_perf
        assert "STRAT-002" in all_perf

    def test_to_dict(self) -> None:
        perf = StrategyPerformance(strategy_id="STRAT-001")
        d = perf.to_dict()
        assert d["strategy_id"] == "STRAT-001"
        assert "total_pnl" in d
        assert "sharpe_ratio" in d
        assert "max_drawdown" in d


# ---------------------------------------------------------------------------
# Auto-pause on losses
# ---------------------------------------------------------------------------
class TestAutoPause:

    def test_auto_pause_on_loss_threshold(
        self, tmp_path: Path,
    ) -> None:
        lm, sm = _make_manager(
            tmp_path, loss_threshold=Decimal("0.10"),
        )
        sm.set_strategy_status("STRAT-001", "active")
        lm.update_performance("STRAT-001", Decimal("1000"))
        lm.update_performance("STRAT-001", Decimal("890"))
        assert lm.get_status("STRAT-001") == "paused"

    def test_no_auto_pause_below_threshold(
        self, tmp_path: Path,
    ) -> None:
        lm, sm = _make_manager(
            tmp_path, loss_threshold=Decimal("0.10"),
        )
        sm.set_strategy_status("STRAT-001", "active")
        lm.update_performance("STRAT-001", Decimal("1000"))
        lm.update_performance("STRAT-001", Decimal("950"))
        assert lm.get_status("STRAT-001") == "active"

    def test_auto_pause_only_for_active(
        self, tmp_path: Path,
    ) -> None:
        lm, _ = _make_manager(
            tmp_path, loss_threshold=Decimal("0.10"),
        )
        lm.update_performance("STRAT-001", Decimal("1000"))
        lm.update_performance("STRAT-001", Decimal("800"))
        assert lm.get_status("STRAT-001") == "evaluating"

    def test_auto_pause_respects_cycle_limit(
        self, tmp_path: Path,
    ) -> None:
        lm, sm = _make_manager(
            tmp_path, loss_threshold=Decimal("0.10"),
        )
        sm.set_strategy_status("STRAT-001", "active")
        sm.set_strategy_status("STRAT-002", "active")
        lm.update_performance("STRAT-001", Decimal("1000"))
        lm.update_performance("STRAT-001", Decimal("800"))
        assert lm.get_status("STRAT-001") == "paused"
        lm.update_performance("STRAT-002", Decimal("1000"))
        lm.update_performance("STRAT-002", Decimal("800"))
        assert lm.get_status("STRAT-002") == "active"


# ---------------------------------------------------------------------------
# Tier activation approval
# ---------------------------------------------------------------------------
class TestTierActivation:

    def test_request_sets_flag(self, tmp_path: Path) -> None:
        lm, _ = _make_manager(tmp_path)
        lm.request_tier_activation("STRAT-001", tier=2)
        assert lm.is_tier_activation_pending("STRAT-001", tier=2)

    def test_no_pending_by_default(self, tmp_path: Path) -> None:
        lm, _ = _make_manager(tmp_path)
        assert not lm.is_tier_activation_pending("STRAT-001", tier=2)

    def test_different_tiers_independent(
        self, tmp_path: Path,
    ) -> None:
        lm, _ = _make_manager(tmp_path)
        lm.request_tier_activation("STRAT-001", tier=2)
        assert lm.is_tier_activation_pending("STRAT-001", tier=2)
        assert not lm.is_tier_activation_pending("STRAT-001", tier=3)
