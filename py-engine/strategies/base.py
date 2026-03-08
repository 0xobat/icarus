"""Strategy protocol and data types for the Icarus strategy framework.

Defines the Strategy protocol that all strategy classes must implement,
along with the data structures used for market snapshots, strategy reports,
and signal types. Strategies are analysts — they produce reports, not
execution orders.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum  # noqa: UP042
from typing import Any, Protocol, runtime_checkable


class SignalType(StrEnum):
    """Signal types that strategies can emit.

    Each type represents a condition evaluation result:
    - entry_met: entry condition satisfied (can be actionable)
    - exit_met: exit condition satisfied (can be actionable)
    - harvest_ready: harvest threshold crossed (can be actionable)
    - rebalance_needed: rebalance condition triggered (can be actionable)
    - threshold_approaching: condition approaching but not met (always actionable=False)
    """

    ENTRY_MET = "entry_met"
    EXIT_MET = "exit_met"
    HARVEST_READY = "harvest_ready"
    REBALANCE_NEEDED = "rebalance_needed"
    THRESHOLD_APPROACHING = "threshold_approaching"


# --- MarketSnapshot component dataclasses ---


@dataclass(frozen=True)
class TokenPrice:
    """Token price with source and timestamp.

    Args:
        token: Token symbol (e.g. "USDC", "AERO").
        price: Price in USD.
        source: Data source (e.g. "alchemy", "defillama").
        timestamp: When this price was observed.
    """

    token: str
    price: float
    source: str
    timestamp: datetime


@dataclass(frozen=True)
class GasInfo:
    """Gas price information.

    Args:
        current_gwei: Current gas price in gwei.
        avg_24h_gwei: 24-hour rolling average gas price in gwei.
    """

    current_gwei: float
    avg_24h_gwei: float


@dataclass(frozen=True)
class PoolState:
    """Protocol pool metrics.

    Args:
        protocol: Protocol name (e.g. "aave_v3", "aerodrome").
        pool_id: Pool identifier.
        tvl: Total value locked in USD.
        apy: Annual percentage yield.
        utilization: Utilization rate (0.0 to 1.0), if applicable.
    """

    protocol: str
    pool_id: str
    tvl: float
    apy: float
    utilization: float | None = None


@dataclass(frozen=True)
class MarketSnapshot:
    """Pre-sliced market data provided to strategy evaluate() calls.

    The engine creates this snapshot by slicing cached market data
    to the strategy's data_window before calling evaluate().

    Args:
        prices: Token prices with source and timestamp.
        gas: Current gas price and 24h average.
        pools: Protocol pool metrics (TVL, APY, utilization).
        timestamp: Snapshot creation time.
    """

    prices: list[TokenPrice]
    gas: GasInfo
    pools: list[PoolState]
    timestamp: datetime


# --- StrategyReport component dataclasses ---


@dataclass(frozen=True)
class Observation:
    """Factual data point observed by a strategy.

    Observations are objective — no opinion, just what the strategy sees.

    Args:
        metric: What is being measured (e.g. "aave_usdc_supply_apy").
        value: The measured value (string for flexibility).
        context: Human-readable context for the observation.
    """

    metric: str
    value: str
    context: str


@dataclass(frozen=True)
class Signal:
    """Condition evaluation result from a strategy.

    The strategy sets actionable based on its own threshold logic.
    The decision gate checks the flag to determine whether to open.

    Args:
        type: The signal type.
        actionable: Whether this signal should open the decision gate.
        details: Human-readable explanation of the signal.
    """

    type: SignalType
    actionable: bool
    details: str

    def __post_init__(self) -> None:
        """Validate signal constraints.

        Raises:
            ValueError: If threshold_approaching signal is marked actionable.
        """
        if self.type == SignalType.THRESHOLD_APPROACHING and self.actionable:
            raise ValueError(
                "threshold_approaching signals must always have actionable=False"
            )


@dataclass(frozen=True)
class Recommendation:
    """Advisory recommendation from a strategy.

    This is advisory — Claude makes the final call on execution.

    Args:
        action: Suggested action (e.g. "supply", "withdraw", "harvest").
        reasoning: Why the strategy recommends this action.
        parameters: Action-specific parameters (amounts, targets, etc.).
    """

    action: str
    reasoning: str
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StrategyReport:
    """Output of a strategy's evaluate() call.

    Contains factual observations, condition signals, and an optional
    recommendation. Reports are collected and fed to Claude for reasoning.

    Args:
        strategy_id: Which strategy produced this report.
        timestamp: ISO 8601 timestamp string.
        observations: Factual data points the strategy observed.
        signals: Condition evaluation results.
        recommendation: Advisory suggestion, if any.
    """

    strategy_id: str
    timestamp: str
    observations: list[Observation]
    signals: list[Signal]
    recommendation: Recommendation | None = None


# --- Strategy Protocol ---


@runtime_checkable
class Strategy(Protocol):
    """Protocol that all strategy classes must satisfy.

    Strategies are analysts — they examine market data and produce
    reports with observations, signals, and recommendations. They do
    not emit execution orders directly.

    Attributes:
        strategy_id: Unique identifier matching STRATEGY.md (e.g. "LEND-001").
        eval_interval: How often evaluate() runs.
        data_window: How far back the strategy needs data.
    """

    @property
    def strategy_id(self) -> str:
        """Unique identifier matching STRATEGY.md."""
        ...

    @property
    def eval_interval(self) -> timedelta:
        """How often evaluate() should be called."""
        ...

    @property
    def data_window(self) -> timedelta:
        """How far back the strategy needs market data."""
        ...

    def evaluate(self, snapshot: MarketSnapshot) -> StrategyReport:
        """Analyze market data and return a structured report.

        Args:
            snapshot: Pre-sliced market data for this strategy's data_window.

        Returns:
            StrategyReport with observations, signals, and optional recommendation.
        """
        ...
