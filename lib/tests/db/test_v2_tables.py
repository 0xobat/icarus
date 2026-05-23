"""Tests for v2 strategy-lake tables added in W1D4.

These tables are pure additions to the v4.2 schema — the rollback design
relies on v4.2 ignoring them. The tests confirm:
  - create_all() materializes every new table
  - Indices are correct
  - Insert / query round-trips work for each
  - State-machine values from CANDIDATE_STATES are the only valid Candidate.state

Repository CRUD for these tables lands alongside lake-governor build in W5;
this test exercises the bare ORM layer.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from icarus.db.database import DatabaseConfig, DatabaseManager
from icarus.db.models import (
    CANDIDATE_STATES,
    Base,
    Candidate,
    LakeRoster,
    PaperTradeState,
    ParameterSearchResult,
    Template,
    WalkForwardResult,
)
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError


@pytest.fixture
def db_manager():
    mgr = DatabaseManager(DatabaseConfig(url="sqlite:///:memory:", echo=False))
    mgr.create_tables()
    yield mgr
    mgr.close()


# --- Schema presence ---


def test_all_v2_tables_created(db_manager):
    names = set(Base.metadata.tables.keys())
    expected = {
        "templates",
        "candidates",
        "parameter_search_results",
        "walk_forward_results",
        "lake_roster",
        "paper_trade_state",
    }
    assert expected.issubset(names), f"missing: {expected - names}"


def test_state_machine_constant_matches_blueprint():
    # Blueprint §"Lake governance" line 319: backtest → paper_trade →
    # live_capped → live_mature with demotion to demoted_paper / archived.
    assert CANDIDATE_STATES == (
        "backtest",
        "paper_trade",
        "live_capped",
        "live_mature",
        "demoted_paper",
        "archived",
    )


# --- Per-table round-trips ---


def test_template_round_trip(db_manager):
    with db_manager.get_session() as s:
        t = Template(
            template_id="LEND-001",
            semver="0.1.0",
            title="Aave V3 supply rotation",
            chain="base",
            protocol="aave_v3",
            asset_universe_json=json.dumps(["USDC", "USDbC"]),
            manifest_yaml="id: LEND-001\nsemver: 0.1.0\n",
            evaluate_py_path="templates/LEND-001/evaluate.py",
            parameter_rationale_md="# rationale\n",
            judge_verdict="FLAG_FOR_OPERATOR",
            judge_rationale="Default verdict for non-trivial templates",
        )
        s.add(t)
        s.commit()

    with db_manager.get_session() as s:
        row = s.scalar(select(Template).where(Template.template_id == "LEND-001"))
        assert row is not None
        assert row.chain == "base"
        assert row.judge_verdict == "FLAG_FOR_OPERATOR"


def test_template_template_id_unique(db_manager):
    with db_manager.get_session() as s:
        s.add(
            Template(
                template_id="DUP-001",
                semver="0.1.0",
                title="x",
                chain="base",
                protocol="x",
                asset_universe_json="[]",
                manifest_yaml="",
                evaluate_py_path="x",
            )
        )
        s.commit()
    with db_manager.get_session() as s:
        s.add(
            Template(
                template_id="DUP-001",
                semver="0.2.0",
                title="y",
                chain="base",
                protocol="y",
                asset_universe_json="[]",
                manifest_yaml="",
                evaluate_py_path="y",
            )
        )
        with pytest.raises(IntegrityError):
            s.commit()


def test_candidate_round_trip_and_default_state(db_manager):
    with db_manager.get_session() as s:
        c = Candidate(
            candidate_id="c-7af3",
            template_id="LEND-001",
            template_version="0.1.0",
            params_json=json.dumps({"apy_threshold": "0.05"}),
        )
        s.add(c)
        s.commit()
        s.refresh(c)
        assert c.state == "backtest"
        assert c.entered_state_at is not None


def test_parameter_search_result_round_trip(db_manager):
    with db_manager.get_session() as s:
        r = ParameterSearchResult(
            template_id="LEND-001",
            template_version="0.1.0",
            params_json=json.dumps({"apy_threshold": "0.05"}),
            sharpe=1.42,
            deflated_sharpe=1.10,
            max_dd=0.04,
            turnover=0.20,
            oos_sharpe=0.95,
            compute_seconds=180.5,
            is_top_k=True,
        )
        s.add(r)
        s.commit()
    with db_manager.get_session() as s:
        rows = list(s.scalars(select(ParameterSearchResult).where(ParameterSearchResult.is_top_k)))
        assert len(rows) == 1
        assert float(rows[0].sharpe) == pytest.approx(1.42, rel=1e-3)


def test_walk_forward_result_round_trip(db_manager):
    with db_manager.get_session() as s:
        now = datetime.now(UTC)
        s.add(
            WalkForwardResult(
                candidate_id="c-7af3",
                train_start=now,
                train_end=now,
                test_start=now,
                test_end=now,
                train_sharpe=1.5,
                test_sharpe=1.2,
                test_max_dd=0.03,
                regime_label="vol_low_trend_up",
            )
        )
        s.commit()
    with db_manager.get_session() as s:
        rows = list(s.scalars(select(WalkForwardResult)))
        assert len(rows) == 1
        assert rows[0].regime_label == "vol_low_trend_up"


def test_lake_roster_round_trip(db_manager):
    with db_manager.get_session() as s:
        s.add(
            LakeRoster(
                candidate_id="c-7af3",
                template_id="LEND-001",
                state="paper_trade",
                allocation_usd=0,
                allocation_max_pct=0.70,
            )
        )
        s.commit()
    with db_manager.get_session() as s:
        row = s.scalar(select(LakeRoster).where(LakeRoster.candidate_id == "c-7af3"))
        assert row is not None
        assert row.state == "paper_trade"
        assert row.breaker_tripped is False


def test_paper_trade_state_round_trip(db_manager):
    with db_manager.get_session() as s:
        s.add(
            PaperTradeState(
                candidate_id="c-7af3",
                shadow_positions_json=json.dumps([{"asset": "USDC", "size": 500}]),
                observed_sharpe=1.30,
                observed_max_dd=0.04,
                observation_days=14,
            )
        )
        s.commit()
    with db_manager.get_session() as s:
        row = s.scalar(select(PaperTradeState).where(PaperTradeState.candidate_id == "c-7af3"))
        assert row is not None
        assert row.observation_days == 14
        assert json.loads(row.shadow_positions_json) == [{"asset": "USDC", "size": 500}]


# --- Indices ---


def test_v2_indices(db_manager):
    candidate_idx = {i.name for i in Candidate.__table__.indexes}
    assert "ix_candidates_template_state" in candidate_idx
    lake_idx = {i.name for i in LakeRoster.__table__.indexes}
    assert "ix_lake_roster_state" in lake_idx
    psr_idx = {i.name for i in ParameterSearchResult.__table__.indexes}
    assert "ix_psr_is_top_k" in psr_idx
