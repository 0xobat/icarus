"""SQLAlchemy ORM models for the Icarus trading database.

Schema is designed to be portable between SQLite (development) and PostgreSQL
(production on Railway). All financial amounts use ``Numeric`` which maps to
``NUMERIC`` on Postgres and ``REAL`` on SQLite. Indices are chosen for the
most common dashboard queries (by strategy, by timestamp, by chain).

NOTE: In development we use SQLite via aiosqlite. For production deployment,
swap the connection string to ``postgresql+asyncpg://...`` (Railway provides
managed PostgreSQL). The ORM layer and all queries remain the same.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Index, Integer, Numeric, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base for all Icarus ORM models."""


class Trade(Base):
    """Record of an executed trade with full context.

    Every trade executed by the system is recorded here for audit trail,
    performance analysis, and regulatory compliance.
    """

    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trade_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    strategy: Mapped[str] = mapped_column(String(64), nullable=False)
    protocol: Mapped[str] = mapped_column(String(64), nullable=False)
    chain: Mapped[str] = mapped_column(String(32), nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    asset_in: Mapped[str] = mapped_column(String(32), nullable=False)
    asset_out: Mapped[str | None] = mapped_column(String(32), nullable=True)
    amount_in: Mapped[float] = mapped_column(Numeric(precision=36, scale=18), nullable=False)
    amount_out: Mapped[float | None] = mapped_column(
        Numeric(precision=36, scale=18), nullable=True
    )
    price_at_execution: Mapped[float | None] = mapped_column(
        Numeric(precision=36, scale=18), nullable=True
    )
    gas_used: Mapped[int | None] = mapped_column(Integer, nullable=True)
    gas_price_wei: Mapped[int | None] = mapped_column(Integer, nullable=True)
    slippage_bps: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tx_hash: Mapped[str | None] = mapped_column(String(66), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_trades_strategy", "strategy"),
        Index("ix_trades_timestamp", "timestamp"),
        Index("ix_trades_chain", "chain"),
        Index("ix_trades_status", "status"),
        Index("ix_trades_strategy_timestamp", "strategy", "timestamp"),
    )


class PortfolioSnapshot(Base):
    """Point-in-time snapshot of portfolio state.

    Snapshots are taken at configurable intervals (minimum hourly) for
    tracking portfolio value, drawdown, and allocation over time.
    """

    __tablename__ = "portfolio_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    total_value_usd: Mapped[float] = mapped_column(
        Numeric(precision=36, scale=18), nullable=False
    )
    stablecoin_value_usd: Mapped[float] = mapped_column(
        Numeric(precision=36, scale=18), nullable=False
    )
    deployed_value_usd: Mapped[float] = mapped_column(
        Numeric(precision=36, scale=18), nullable=False
    )
    positions_json: Mapped[str] = mapped_column(Text, nullable=False)
    drawdown_from_peak: Mapped[float] = mapped_column(
        Numeric(precision=10, scale=6), nullable=False
    )
    peak_value_usd: Mapped[float] = mapped_column(
        Numeric(precision=36, scale=18), nullable=False
    )

    __table_args__ = (
        Index("ix_portfolio_snapshots_timestamp", "timestamp"),
    )


class StrategyPerformance(Base):
    """Aggregated strategy performance for a given period.

    Records PnL, gas costs, trade counts, and win rates per strategy
    for daily, weekly, and monthly reporting periods.
    """

    __tablename__ = "strategy_performance"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    strategy: Mapped[str] = mapped_column(String(64), nullable=False)
    period: Mapped[str] = mapped_column(String(16), nullable=False)
    pnl_usd: Mapped[float] = mapped_column(Numeric(precision=36, scale=18), nullable=False)
    return_pct: Mapped[float] = mapped_column(Numeric(precision=10, scale=6), nullable=False)
    gas_cost_usd: Mapped[float] = mapped_column(Numeric(precision=36, scale=18), nullable=False)
    trade_count: Mapped[int] = mapped_column(Integer, nullable=False)
    win_rate: Mapped[float | None] = mapped_column(Numeric(precision=10, scale=6), nullable=True)

    __table_args__ = (
        Index("ix_strategy_performance_strategy", "strategy"),
        Index("ix_strategy_performance_timestamp", "timestamp"),
        Index("ix_strategy_performance_strategy_period", "strategy", "period"),
    )


class Alert(Base):
    """System alert for circuit breakers, risk events, and operational issues.

    Alerts persist in the database for audit trail and can be acknowledged
    by operators via the dashboard or Discord bot.
    """

    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    data_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    acknowledged: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (
        Index("ix_alerts_timestamp", "timestamp"),
        Index("ix_alerts_severity", "severity"),
        Index("ix_alerts_category", "category"),
        Index("ix_alerts_acknowledged", "acknowledged"),
    )


class PortfolioPosition(Base):
    """Individual portfolio position tracked over its lifecycle.

    Maps to the Position dataclass in portfolio/position_tracker.py but
    provides persistent storage in PostgreSQL for startup recovery.
    """

    __tablename__ = "portfolio_positions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    position_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    strategy: Mapped[str] = mapped_column(String(64), nullable=False)
    protocol: Mapped[str] = mapped_column(String(64), nullable=False)
    chain: Mapped[str] = mapped_column(String(32), nullable=False)
    asset: Mapped[str] = mapped_column(String(32), nullable=False)
    entry_price: Mapped[float] = mapped_column(Numeric(precision=36, scale=18), nullable=False)
    entry_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    amount: Mapped[float] = mapped_column(Numeric(precision=36, scale=18), nullable=False)
    current_value: Mapped[float] = mapped_column(Numeric(precision=36, scale=18), nullable=False)
    unrealized_pnl: Mapped[float] = mapped_column(
        Numeric(precision=36, scale=18), nullable=False, default=0
    )
    realized_pnl: Mapped[float | None] = mapped_column(
        Numeric(precision=36, scale=18), nullable=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    close_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    protocol_data_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_positions_strategy", "strategy"),
        Index("ix_positions_status", "status"),
        Index("ix_positions_protocol", "protocol"),
        Index("ix_positions_chain", "chain"),
    )


class StrategyStatus(Base):
    """Active/inactive status for each registered strategy.

    Persists strategy status across restarts. Loaded into memory at startup
    and updated when strategies are toggled.
    """

    __tablename__ = "strategy_statuses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    strategy_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )

    __table_args__ = (
        Index("ix_strategy_statuses_strategy_id", "strategy_id"),
        Index("ix_strategy_statuses_status", "status"),
    )


class DecisionAuditLog(Base):
    """Audit log for Claude API decisions.

    Records every decision cycle including the prompt context, Claude's
    response, orders produced, and whether they passed the verification gate.
    """

    __tablename__ = "decision_audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    decision_action: Mapped[str] = mapped_column(String(32), nullable=False)
    reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
    strategy_reports_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    orders_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    passed_verification: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    risk_flags_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)

    __table_args__ = (
        Index("ix_decision_audit_timestamp", "timestamp"),
        Index("ix_decision_audit_correlation_id", "correlation_id"),
        Index("ix_decision_audit_action", "decision_action"),
    )


class SchemaVersion(Base):
    """Track applied schema migrations for version control.

    Each row represents a migration that has been applied to the database.
    This enables future schema evolution without data loss.
    """

    __tablename__ = "schema_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    version: Mapped[int] = mapped_column(Integer, unique=True, nullable=False)
    description: Mapped[str] = mapped_column(String(256), nullable=False)
    applied_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


# =============================================================================
# v2 (Daedalus) extension — strategy lake architecture
# =============================================================================
# Per blueprint §"Database (db/)":
#   - Research-owned: templates, parameter_search_results,
#     walk_forward_results, candidates
#   - Curation-owned: lake_roster, paper_trade_state
#   - Execution-owned: existing positions/orders/executions remain
#
# These tables are ADDITIONS — v4.2 ignores them per the rollback design.
# The columns on v4.2 tables stay intact for the rollback path.

# State machine values for Candidate.state and LakeRoster.state.
# Single source of truth; lake-governor enforces transitions in code,
# not via DB CHECK constraints (state-machine logic stays in Python).
CANDIDATE_STATES = (
    "backtest",
    "paper_trade",
    "live_capped",
    "live_mature",
    "demoted_paper",
    "archived",
)


class Template(Base):
    """A loaded strategy template — extractor wrote it; registry validated it.

    `template_id` is the natural key (e.g. "LEND-001"). The `manifest_yaml`
    is preserved raw alongside the parsed metadata so audits can replay the
    exact text the LLM produced.

    `judge_verdict` carries the LLM-as-judge plausibility result from Q8:
    PASS, FLAG_FOR_OPERATOR (default for non-trivial templates), or REJECT.
    Templates with FLAG_FOR_OPERATOR sit in webapp until the operator acts.
    """

    __tablename__ = "templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    template_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    semver: Mapped[str] = mapped_column(String(16), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    chain: Mapped[str] = mapped_column(String(32), nullable=False)
    protocol: Mapped[str] = mapped_column(String(64), nullable=False)
    asset_universe_json: Mapped[str] = mapped_column(Text, nullable=False)
    manifest_yaml: Mapped[str] = mapped_column(Text, nullable=False)
    evaluate_py_path: Mapped[str] = mapped_column(String(256), nullable=False)
    parameter_rationale_md: Mapped[str | None] = mapped_column(Text, nullable=True)
    judge_verdict: Mapped[str] = mapped_column(
        String(32), nullable=False, default="FLAG_FOR_OPERATOR"
    )
    judge_rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )

    __table_args__ = (
        Index("ix_templates_template_id", "template_id"),
        Index("ix_templates_chain", "chain"),
        Index("ix_templates_judge_verdict", "judge_verdict"),
    )


class Candidate(Base):
    """One instantiated parameter combination of a Template.

    Born in `backtest` state when search picks the row as top-K; moves
    through paper_trade → live_capped → live_mature, with demotion paths
    to demoted_paper and archived. The state machine logic lives in
    lake-governor; this row records the current resting state and audit
    timestamps.

    `template_id` is a soft FK (string, no enforced FK constraint) to
    match v4.2's existing convention.
    """

    __tablename__ = "candidates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    candidate_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    template_id: Mapped[str] = mapped_column(String(64), nullable=False)
    template_version: Mapped[str] = mapped_column(String(16), nullable=False)
    params_json: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="backtest")
    entered_state_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )

    __table_args__ = (
        Index("ix_candidates_candidate_id", "candidate_id"),
        Index("ix_candidates_template_id", "template_id"),
        Index("ix_candidates_state", "state"),
        Index("ix_candidates_template_state", "template_id", "state"),
    )


class ParameterSearchResult(Base):
    """One row of a backtest search surface.

    Written per-param-combination by backtest-worker. `is_top_k` flips to
    True on the rows that get promoted to paper_trade as Candidate rows.
    """

    __tablename__ = "parameter_search_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    template_id: Mapped[str] = mapped_column(String(64), nullable=False)
    template_version: Mapped[str] = mapped_column(String(16), nullable=False)
    params_json: Mapped[str] = mapped_column(Text, nullable=False)
    sharpe: Mapped[float] = mapped_column(Numeric(precision=10, scale=6), nullable=False)
    deflated_sharpe: Mapped[float] = mapped_column(Numeric(precision=10, scale=6), nullable=False)
    max_dd: Mapped[float] = mapped_column(Numeric(precision=10, scale=6), nullable=False)
    turnover: Mapped[float] = mapped_column(Numeric(precision=10, scale=6), nullable=False)
    oos_sharpe: Mapped[float | None] = mapped_column(
        Numeric(precision=10, scale=6), nullable=True
    )
    compute_seconds: Mapped[float] = mapped_column(Numeric(precision=12, scale=3), nullable=False)
    is_top_k: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Multi-test correction outcome (W10). Nullable for backward compatibility
    # with rows written before cohort correction was wired in.
    multi_test_correction_method: Mapped[str | None] = mapped_column(
        String(32), nullable=True
    )
    multi_test_survives: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )

    __table_args__ = (
        Index("ix_psr_template_id", "template_id"),
        Index("ix_psr_template_version", "template_id", "template_version"),
        Index("ix_psr_is_top_k", "is_top_k"),
    )


class WalkForwardResult(Base):
    """One walk-forward window for one candidate.

    Multiple rows per candidate — one per (train_start, test_start) pair.
    Lets the OOS gate compute regime-segmented stats and lets the webapp
    plot the rolling test_sharpe trajectory.
    """

    __tablename__ = "walk_forward_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    candidate_id: Mapped[str] = mapped_column(String(64), nullable=False)
    train_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    train_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    test_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    test_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    train_sharpe: Mapped[float] = mapped_column(Numeric(precision=10, scale=6), nullable=False)
    test_sharpe: Mapped[float] = mapped_column(Numeric(precision=10, scale=6), nullable=False)
    test_max_dd: Mapped[float] = mapped_column(Numeric(precision=10, scale=6), nullable=False)
    regime_label: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )

    __table_args__ = (
        Index("ix_wfr_candidate_id", "candidate_id"),
        Index("ix_wfr_test_start", "test_start"),
    )


class LakeRoster(Base):
    """Per-candidate live state — the lake's working set.

    One row per candidate (state-machine view). decision-engine reads
    this each cycle to decide who is live; lake-governor writes
    transitions in response to decay / breaker events.

    `decay_state_json` carries the Page-Hinkley CUSUM internal state so
    detection survives lake-governor restarts.
    """

    __tablename__ = "lake_roster"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    candidate_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    template_id: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    allocation_usd: Mapped[float] = mapped_column(
        Numeric(precision=36, scale=18), nullable=False, default=0
    )
    allocation_max_pct: Mapped[float] = mapped_column(
        Numeric(precision=10, scale=6), nullable=False
    )
    last_transition_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    decay_state_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    breaker_tripped: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (
        Index("ix_lake_roster_candidate_id", "candidate_id"),
        Index("ix_lake_roster_template_id", "template_id"),
        Index("ix_lake_roster_state", "state"),
    )


class PaperTradeState(Base):
    """Shadow-position bookkeeping during a candidate's paper-trade window.

    Updated tick-by-tick by lake-governor's paper-trade harness.
    `shadow_positions_json` carries the simulated open positions; observed
    sharpe + max_dd drive the live-promotion gate.
    """

    __tablename__ = "paper_trade_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    candidate_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    entered_paper_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    shadow_positions_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    observed_sharpe: Mapped[float | None] = mapped_column(
        Numeric(precision=10, scale=6), nullable=True
    )
    observed_max_dd: Mapped[float | None] = mapped_column(
        Numeric(precision=10, scale=6), nullable=True
    )
    observation_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )

    __table_args__ = (
        Index("ix_pts_candidate_id", "candidate_id"),
        Index("ix_pts_entered_paper_at", "entered_paper_at"),
    )


# =============================================================================
# Discord reply-token state (W8)
# =============================================================================
# Per blueprint §"Operator alerts": the lake-governor's promotion gate posts
# a structured PROMOTION REQUEST to the operator's Discord webhook, then
# waits for an "APPROVE tok-<id>" / "REJECT tok-<id> <reason>" reply where
# <id> is this table's primary key (printed back in the broadcast message).
# The reply-token row threads the request ↔ reply correlation; pending
# tokens older than DISCORD_REPLY_TOKEN_TIMEOUT_HOURS are swept to "expired"
# by the governor's tick loop so a stale gate never blocks the cycle.
#
# Status state machine (enforced in Python, not via CHECK constraint to
# stay portable between SQLite/Postgres):
#   pending → {approved, rejected, expired}  — terminal once set.

DISCORD_REPLY_TOKEN_STATUSES = (
    "pending",
    "approved",
    "rejected",
    "expired",
)

DISCORD_REPLY_TOKEN_KINDS = (
    "promotion_request",
    "demotion_explain",
)


class DiscordReplyToken(Base):
    """One operator-attention round-trip via the Discord webhook.

    Created in ``pending`` when ``WebhookPoster.post_promotion_request``
    fires (or any future kind). Transitions to ``approved``/``rejected``
    when ``ReplyTokenStore.match_reply`` parses a matching operator
    message, or to ``expired`` when ``ReplyTokenStore.expire_stale``
    sweeps tokens older than ``expires_at``.

    ``candidate_id`` is a soft FK (string) matching v4.2 + v2 convention.
    The composite ``(candidate_id, status, created_at)`` index serves the
    "most recent unanswered token per candidate" lookup hot path.
    """

    __tablename__ = "discord_reply_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    candidate_id: Mapped[str] = mapped_column(String(64), nullable=False)
    template_id: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    replied_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    reply_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    reply_verdict: Mapped[str | None] = mapped_column(String(16), nullable=True)

    __table_args__ = (
        Index("ix_discord_reply_tokens_candidate_id", "candidate_id"),
        Index("ix_discord_reply_tokens_status", "status"),
        Index("ix_discord_reply_tokens_created_at", "created_at"),
        Index(
            "ix_discord_reply_tokens_candidate_status_created",
            "candidate_id",
            "status",
            "created_at",
        ),
    )
